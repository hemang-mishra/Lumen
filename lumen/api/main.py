"""
The web application, and the stores it holds open.

Built by a function rather than created when this module is imported. A
module-level application would open a database the moment anything named it,
which makes it impossible to point a test at a temporary one and awkward to
run two configurations in one process.

Everything here is read-only. The graph is opened as a full provider —
somebody has to be able to create the tables — and immediately handed to the
rest of the application as a reader, so no route can reach a write even by
accident.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace

from fastapi import Depends, FastAPI

from lumen.api.deps import get_graph, get_ops
from lumen.api.errors import register_error_handlers
from lumen.api.routes import debug, graph, query
from lumen.api.schemas import HealthView
from lumen.config import AppConfig
from lumen.graph.kuzu_impl import KuzuGraphProvider
from lumen.graph.provider import ReadOnlyGraph
from lumen.observability.logging import configure_logging
from lumen.operational.repositories import OperationalStore
from lumen.operational.sqlalchemy_impl import build_operational_store
from lumen.providers.errors import ProviderError
from lumen.providers.factory import get_llm_provider
from lumen.query import QueryFormulator
from lumen.schemas.enums import ModelRole

logger = logging.getLogger(__name__)

TITLE = "Lumen"
DESCRIPTION = (
    "Read-only access to the knowledge graph and the history of the runs "
    "that built it."
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

        provider = KuzuGraphProvider(settings.graph.db_path)
        provider.init_schema()
        store = build_operational_store(settings)
        store.init_schema()
        formulator = _build_formulator(settings, provider)

        app.state.graph = provider
        app.state.ops = store
        app.state.formulator = formulator
        logger.info("api ready", extra={"graph_path": settings.graph.db_path})

        try:
            yield
        finally:
            if formulator is not None:
                formulator.close()
            provider.close()
            store.close()
            logger.info("api stopped")

    return lifespan


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
