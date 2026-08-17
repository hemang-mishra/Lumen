"""
The web application, and the stores it holds open.

Built by a function rather than created when this module is imported. A
module-level application would open a database the moment anything named it,
which makes it impossible to point a test at a temporary one and awkward to
run two configurations in one process.

**The routes are read-only, and one of them is not.** The graph is opened as
a full provider — somebody has to be able to create the tables — and handed
to the rest of the application as a reader, so no route can reach a write
even by accident. Uploading breaks that symmetry, and it is worth being
exact about how. The upload route still cannot write: what it holds is the
importer, and all it can do with it is put an identifier on a queue. The
graph, the vector store and the models live inside that importer, on its
own thread. So the guarantee is unchanged in substance — nothing serving a
request can reach the graph's write methods — while the process as a whole
has gained a writer.

That writer shares the one graph provider rather than opening a second. It
has to: Kuzu is embedded and takes a file lock, so there is only ever one.
The provider serialises access internally, which is what keeps a read
arriving mid-import from landing inside an uncommitted transaction.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from lumen.api.deps import get_graph, get_ops
from lumen.api.errors import register_error_handlers
from lumen.api.resources import LazySearchStack
from lumen.api.routes import debug, graph, ingest, query
from lumen.api.schemas import HealthView
from lumen.config import AppConfig
from lumen.env import load_env
from lumen.graph.kuzu_impl import KuzuGraphProvider
from lumen.graph.provider import ReadOnlyGraph
from lumen.ingest import IngestWorker
from lumen.observability.logging import configure_logging
from lumen.operational.repositories import OperationalStore
from lumen.operational.sqlalchemy_impl import build_operational_store
from lumen.providers.errors import ProviderError
from lumen.providers.factory import get_llm_provider
from lumen.query import (
    ConversationMemory,
    ConversationStore,
    PromptComposer,
    QueryFormulator,
    SessionRegistry,
)
from lumen.schemas.enums import ModelRole

logger = logging.getLogger(__name__)

TITLE = "Lumen"
DESCRIPTION = (
    "Read access to the knowledge graph and the history of the runs that "
    "built it, and one way in: uploading an exported conversation."
)


def create_app(config: AppConfig | None = None) -> FastAPI:
    """
    Build the application, with its stores opened when it starts.

    The configuration is taken as an argument so a test can point the whole
    thing at temporary databases without changing anything about how it is
    wired.
    """
    settings = config or AppConfig()

    app = FastAPI(
        title=TITLE,
        description=DESCRIPTION,
        version="0.1.0",
        lifespan=_lifespan_for(settings),
    )
    app.state.config = settings

    register_error_handlers(app)
    app.include_router(graph.router)
    app.include_router(debug.router)
    app.include_router(query.router)

    # Not mounted at all when uploads are switched off, rather than mounted
    # and refusing. A deployment that says it only reads should not have a
    # write endpoint in its documentation answering 503.
    if settings.ingest.enabled:
        app.include_router(ingest.router)

    _mount_ui(app)

    @app.get("/health", response_model=HealthView, tags=["health"])
    def health(
        store: ReadOnlyGraph = Depends(get_graph),
        ops: OperationalStore = Depends(get_ops),
    ) -> HealthView:
        """
        Whether the service is up, and whether each store answers.

        Reported separately because a service that is running but cannot
        reach its databases is a different problem from one that is down,
        and the two are fixed differently.
        """
        graph_ok = _answers(lambda: store.count_by_type())
        ops_ok = _answers(lambda: ops.jobs.get_job("health-probe"))
        return HealthView(
            status="ok" if graph_ok and ops_ok else "degraded",
            graph=graph_ok,
            operational=ops_ok,
        )

    return app


def create_configured_app() -> FastAPI:
    """
    The application, with a .env file read first.

    Two entry points rather than one, and the difference is the whole point.
    `create_app` has no side effects: it reads the environment as it finds
    it, which is what lets a test build an application without a file on
    somebody's machine quietly redirecting it at a real database.

    This one is for actually running the thing:

        uvicorn lumen.api.main:create_configured_app --factory

    Reading the file has to happen before the settings are built, because
    every setting is read at construction and a .env loaded afterwards
    changes nothing.
    """
    load_env()
    return create_app()


def _lifespan_for(settings: AppConfig):
    """
    Open both stores when the application starts and close them when it stops.

    Held open for the life of the process rather than opened per request:
    the graph is an embedded database that takes a file lock, and taking and
    releasing that on every request would be both slow and a way to collide
    with a pipeline run happening at the same time.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.observability)

        _warn_about_vectors_that_will_not_survive(settings)

        provider = KuzuGraphProvider(settings.graph.db_path)
        provider.init_schema()
        store = build_operational_store(settings)
        _bring_the_schema_up_to_date(store)
        formulator = _build_formulator(settings, provider)
        worker = _build_worker(settings, store, provider)
        search = LazySearchStack(
            config=settings, graph=provider, reader=provider, worker=worker
        )

        app.state.graph = provider
        app.state.ops = store
        app.state.formulator = formulator
        app.state.ingest = worker
        app.state.search = search
        app.state.sessions = SessionRegistry(settings.query)
        app.state.composer = PromptComposer(config=settings.chat)
        app.state.memory = ConversationMemory(
            store=ConversationStore(store.buffers),
            llm=_summariser(settings),
            config=settings.chat,
        )
        logger.info("api ready", extra={"graph_path": settings.graph.db_path})

        try:
            yield
        finally:
            # The importer goes first, and is given time to finish what it
            # was in the middle of. It is the only thing here holding a
            # half-written episode, and closing the graph out from under it
            # would be the one way to leave the store in a state no
            # transaction protected.
            # The search stack goes before the importer, since it may be
            # borrowing the importer's index and closing that first would
            # pull it out from under a request still being served.
            search.close()
            if worker is not None:
                worker.stop()
            if formulator is not None:
                formulator.close()
            provider.close()
            store.close()
            logger.info("api stopped")

    return lifespan


def _bring_the_schema_up_to_date(store) -> None:
    """
    Run the migrations the operational database has not had yet.

    This used to create any missing tables instead, which is a different
    thing and quietly the wrong one. Creating tables leaves a database with
    no record of which migrations it represents, so the next change to a
    model adds a column that never reaches the table — and the failure
    arrives much later as `no such column` from whichever query happens to
    touch it first, with nothing to connect it back to a schema that was
    never migrated.

    A database that holds tables but no migration history is refused rather
    than guessed at. It can only have come from the old path, and the two
    ways out are a one-line command each — but choosing between them means
    knowing which migration its tables actually represent, which is a
    question for a person and not for a service starting up.
    """
    from sqlalchemy import inspect

    from lumen.operational.migrator import upgrade_to_head

    inspector = inspect(store.engine)
    tables = set(inspector.get_table_names())

    if tables and "alembic_version" not in tables:
        raise RuntimeError(
            "the operational database has tables but no migration history, so "
            "the migrations cannot be applied to it. It was built by an older "
            "startup path that created tables directly. Stamp it with the "
            "revision its tables match and then upgrade:\n"
            "    uv run alembic stamp <revision>\n"
            "    uv run alembic upgrade head\n"
            "or, if nothing in it is worth keeping, delete it and start again."
        )

    upgrade_to_head(store.engine)
    logger.info("operational schema is up to date")


def _build_worker(settings: AppConfig, ops, graph) -> IngestWorker | None:
    """
    The importer, sharing the graph the rest of the application reads.

    Sharing is not an optimisation. Kuzu is embedded and takes a file lock,
    so a second provider on the same path inside this process would simply
    be refused; the provider serialises access internally so that the read
    routes and this thread cannot collide.

    Its models are deliberately *not* built here. A missing credential would
    otherwise stop the service from starting, when every other thing it does
    reads two local databases and needs no model at all. They are built on
    the first upload instead, and an upload that finds none is refused with
    a 503 saying exactly that.
    """
    if not settings.ingest.enabled:
        logger.info("uploads are switched off for this deployment")
        return None

    worker = IngestWorker(config=settings, ops=ops, graph=graph)
    worker.start()
    return worker


def _warn_about_vectors_that_will_not_survive(settings: AppConfig) -> None:
    """
    Say something when the graph is permanent and its search index is not.

    The default vector location is in memory, which is right for tests and
    quietly wrong everywhere else. Paired with a graph on disk it produces a
    store that slowly fills with records semantic search can never find:
    every restart empties the index while the graph keeps everything, and
    nothing anywhere reports an error. It is the most expensive silent
    misconfiguration in the system, so it gets said out loud.
    """
    if settings.vector.location == ":memory:":
        logger.warning(
            "the search index is in memory while the graph is on disk, so "
            "anything imported will become unfindable by meaning after a "
            "restart; set LUMEN_VECTOR_LOCATION to a path to keep it",
            extra={"graph_path": settings.graph.db_path},
        )


def _mount_ui(app: FastAPI) -> None:
    """
    Serve the test pages, if they are there.

    A small, deliberately plain surface for looking at what the pipeline
    did — uploading a file, reading a run, checking how a sentence is
    interpreted. It has no build step and no dependencies, which is the
    whole point: it exists to make the machinery visible while it is being
    built, and it is meant to be deleted rather than grown into a product.

    Missing files are not an error. The service is an API first and works
    perfectly without a single page.
    """
    directory = Path(__file__).resolve().parent / "static"
    if not directory.is_dir():
        logger.info("no test pages to serve at %s", directory)
        return
    app.mount("/ui", StaticFiles(directory=str(directory), html=True), name="ui")


def _build_formulator(
    settings: AppConfig, graph: ReadOnlyGraph
) -> QueryFormulator | None:
    """
    The turn reader, with retries switched off for its model.

    Every other model call in the system retries a few times with a growing
    pause, which is right for work nobody is waiting on. This one has a
    deadline measured in fractions of a second: a call that failed has
    already missed it, and retrying only guarantees the wait is spent twice
    over before the same answer arrives.

    A model that cannot be reached is not a reason to refuse to start. Every
    other thing this service does is a read of two local databases and works
    perfectly without one, so the failure is recorded and confined to the one
    surface that needs it.

    The graph is handed over as a reader, so this whole side of the
    application is incapable of changing anything.
    """
    no_retries = replace(settings.providers, max_attempts=1)
    try:
        llm = get_llm_provider(
            ModelRole.LIGHTWEIGHT, replace(settings, providers=no_retries)
        )
    except ProviderError:
        logger.warning(
            "no model is configured, so reading conversational turns is unavailable",
            exc_info=True,
        )
        return None
    return QueryFormulator(llm=llm, graph=graph, config=settings.query)


def _summariser(settings: AppConfig):
    """
    The model that writes up a conversation, if there is one.

    Nobody waits on this call — it happens after a reply has gone out — so it
    keeps the ordinary retries rather than the turn reader's single attempt.
    A deployment with no model configured simply stops compressing the older
    part of a conversation; everything else about holding one still works.
    """
    try:
        return get_llm_provider(ModelRole.LIGHTWEIGHT, settings)
    except ProviderError:
        logger.warning(
            "no model is configured, so conversations will not be summarised",
            exc_info=True,
        )
        return None


def _answers(probe) -> bool:
    """
    Whether a store responds at all.

    What the probe returns does not matter — only that asking it did not
    fail. A missing record is a healthy answer.
    """
    try:
        probe()
    except Exception:
        logger.warning("a store did not answer its health probe", exc_info=True)
        return False
    return True


__all__ = ["create_app"]
