"""
The one object that is allowed to produce a report.

Everything else in this package is a function taking a graph as a parameter,
which is right for the pipeline and wrong for the web layer. A route handed a
writable graph can do anything to somebody's history; a route handed one of
these can ask for a period to be summarised and nothing else.

It also owns the models, and builds them only when something actually needs
one. A deployment with no credentials configured still starts, still serves
every read, and refuses only the request that would have spent money — rather
than failing at startup over a feature nobody has used yet.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from lumen.config import AppConfig
from lumen.stores import StoreRegistry
from lumen.operational.repositories import OperationalStore
from lumen.pipeline.macroextraction import runner
from lumen.pipeline.macroextraction.contracts import MacroWindow, ReportOutcome
from lumen.providers.errors import ProviderError
from lumen.providers.factory import get_llm_provider
from lumen.providers.protocols import LLMProvider
from lumen.schemas.enums import ModelRole

logger = logging.getLogger(__name__)


class MacroextractionService:
    """
    Builds periodic reports on request, holding everything they need.

    The narrow surface is the point. What a caller can do with one of these
    is name a period, ask what is overdue, or ask for the two-day scan. There
    is no way to reach the graph through it, so no route can grow a way to
    change somebody's history by accident.

    Safe to call from more than one place at once: report building is
    serialised on a lock, because two runs of the same period at the same
    moment would each see no existing report and both write one.
    """

    def __init__(
        self,
        *,
        config: AppConfig,
        stores: StoreRegistry,
        ops: OperationalStore | None = None,
    ) -> None:
        self._config = config
        self._stores = stores
        self._ops = ops
        self._lock = threading.Lock()
        self._models: dict[ModelRole, LLMProvider | None] = {}

    def due(self, user_id: str, now: datetime) -> list[MacroWindow]:
        """
        Which of this person's periods should have a report by now and do not.

        Takes the person because a report is about one person's history, and
        there is a graph each. What used to be an object holding "the graph"
        is now one that knows how to borrow the right one.
        """
        with self._stores.lease(user_id) as stores:
            return runner.due_now(now, graph=stores.graph, config=self._config)

    def run(
        self, user_id: str, window: MacroWindow, *, force: bool = False
    ) -> ReportOutcome:
        """Build one period's report, or say why it was not worth building."""
        with self._lock, self._stores.lease(user_id) as stores:
            return runner.run_report(
                window,
                graph=stores.graph,
                thinking=self._model(ModelRole.THINKING),
                ops=self._ops,
                config=self._config,
                force=force,
            )

    def run_shadow(self, user_id: str, now: datetime) -> ReportOutcome:
        """Look at the last couple of days and raise an alert if something moved."""
        with self._lock, self._stores.lease(user_id) as stores:
            return runner.run_shadow(
                now,
                graph=stores.graph,
                lightweight=self._model(ModelRole.LIGHTWEIGHT),
                config=self._config,
            )

    def run_due(self, user_id: str, now: datetime) -> list[ReportOutcome]:
        """Catch up on everything owed, including the two-day scan."""
        with self._lock, self._stores.lease(user_id) as stores:
            return runner.run_due(
                now,
                graph=stores.graph,
                thinking=self._model(ModelRole.THINKING),
                lightweight=self._model(ModelRole.LIGHTWEIGHT),
                ops=self._ops,
                config=self._config,
            )

    def _model(self, role: ModelRole) -> LLMProvider | None:
        """
        The model for one job, built on first use and kept afterwards.

        A model that cannot be built is remembered as absent rather than
        retried on every report. Reports carry their counts without one, so
        the sensible response to a missing credential is a report without
        prose, not a run of failures.
        """
        if role in self._models:
            return self._models[role]

        try:
            provider = get_llm_provider(role, self._config)
        except (ProviderError, ValueError) as exc:
            logger.warning(
                "no model available for reports, so they will carry only their counts",
                extra={"role": role.value, "reason": type(exc).__name__},
            )
            provider = None

        self._models[role] = provider
        return provider


__all__ = ["MacroextractionService"]
