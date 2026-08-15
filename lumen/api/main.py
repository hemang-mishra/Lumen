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

from fastapi import Depends, FastAPI

from lumen.api.deps import get_graph, get_ops
from lumen.api.errors import register_error_handlers
from lumen.api.routes import debug, graph
from lumen.api.schemas import HealthView
from lumen.config import AppConfig
from lumen.graph.kuzu_impl import KuzuGraphProvider
from lumen.graph.provider import ReadOnlyGraph
from lumen.observability.logging import configure_logging
from lumen.operational.repositories import OperationalStore
from lumen.operational.sqlalchemy_impl import build_operational_store

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

        app.state.graph = provider
        app.state.ops = store
        logger.info("api ready", extra={"graph_path": settings.graph.db_path})

        try:
            yield
        finally:
            provider.close()
            store.close()
            logger.info("api stopped")

    return lifespan


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
