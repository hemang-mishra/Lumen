"""
The narrow way in to erasing somebody's history.

Everything above this — a web request, a command line, an account being
closed — talks to this and nothing else. What it gets is two operations that
answer the only two questions there are: what would this do, and do it.

Three things live here rather than in the runner, because all three are about
whether an erasure should start at all rather than about carrying one out.

The confirmation phrase is checked here. Erasure cannot be undone, so it must
not be reachable by a request that merely arrived at the right address.

An entry nobody wrote is refused rather than erased. Silently succeeding on a
mistyped identifier would tell somebody their evening had been forgotten when
nothing had been touched.

And only one erasure runs at a time. Two sweeps over one history would
produce two records each claiming to have done what the other did.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from lumen.config import AppConfig
from lumen.erasure.contracts import (
    ErasurePlan,
    ErasureRefused,
    ErasureReport,
    ErasureRequest,
)
from lumen.erasure.runner import ErasureRunner
from lumen.erasure.targets import ENTRY_SCOPE_LIMITS, GraphTargets
from lumen.graph.provider import GraphProvider
from lumen.operational.enums import ErasureScope
from lumen.operational.repositories import OperationalStore
from lumen.operational.schemas import StoredErasureAudit
from lumen.vector.provider import VectorProvider

logger = logging.getLogger(__name__)


class ErasureService:
    """
    Previews and carries out erasures, one at a time.

    Holds the stores it needs and nothing else. The rule about which requests
    are allowed to start lives here; the rule about how a record is rewritten
    lives with the records.
    """

    def __init__(
        self,
        *,
        config: AppConfig,
        graph: GraphProvider,
        vectors: VectorProvider,
        ops: OperationalStore,
    ) -> None:
        self._config = config
        self._graph = graph
        self._ops = ops
        self._targets = GraphTargets(
            graph, batch_size=config.maintenance.erasure_batch_size
        )
        self._runner = ErasureRunner(
            graph=graph, vectors=vectors, ops=ops, config=config.maintenance
        )
        self._lock = threading.Lock()

    def preview(self, request: ErasureRequest) -> ErasurePlan:
        """
        What this erasure would cover, having changed nothing.

        The confirmation phrase is not required to look. Somebody deciding
        whether to go ahead should not have to type the word that means yes
        in order to find out what yes would mean.
        """
        if request.scope is ErasureScope.ENTRY:
            found = self._entry_records(str(request.entry_id))
            return ErasurePlan(
                scope=request.scope,
                entry_id=request.entry_id,
                records_by_kind=self._targets.count_by_kind(found),
                total_records=len(found),
                vectors=len(found),
                conversations=1,
                not_reached=ENTRY_SCOPE_LIMITS,
            )

        by_kind: dict[str, int] = {}
        for table, page in self._targets.everything():
            by_kind[table] = by_kind.get(table, 0) + len(page)
        total = sum(by_kind.values())

        return ErasurePlan(
            scope=request.scope,
            records_by_kind=by_kind,
            total_records=total,
            vectors=total,
            conversations=len(self._conversations(request.user_id)),
        )

    def erase(
        self, request: ErasureRequest, *, at: datetime | None = None
    ) -> ErasureReport:
        """
        Carry out the erasure. There is no way back from this.

        Raises:
            ErasureRefused: The request was not carried out and nothing was
                touched — a missing confirmation, or an entry nobody wrote.
        """
        self._require_confirmation(request)
        entry_ids = self._entry_ids_for(request)

        if not self._lock.acquire(blocking=False):
            raise ErasureRefused(
                "an erasure is already running; wait for it to finish rather "
                "than starting a second one over the same history"
            )
        try:
            logger.warning(
                "an erasure is starting and cannot be undone",
                extra={
                    "scope": request.scope.value,
                    "entry_id": request.entry_id,
                    "initiated_by": request.initiated_by.value,
                },
            )
            return self._runner.run(
                request, entry_ids=entry_ids, at=at or _now()
            )
        finally:
            self._lock.release()

    def audits(self, user_id: str) -> list[StoredErasureAudit]:
        """Every erasure recorded for this person, newest first."""
        return self._ops.erasure.list_for_user(user_id)

    # ------------------------------------------------------------------
    # Deciding whether to start
    # ------------------------------------------------------------------

    def _require_confirmation(self, request: ErasureRequest) -> None:
        """Refuse anything that did not say the word this deployment asks for."""
        wanted = self._config.maintenance.erasure_confirm_phrase
        if request.confirmation.strip() != wanted:
            raise ErasureRefused(
                f"erasure cannot be undone, so it needs {wanted!r} as "
                "confirmation before it will run"
            )

    def _entry_ids_for(self, request: ErasureRequest) -> list[str]:
        """
        Which pieces of writing this erasure covers.

        For a whole erasure that is every conversation there has been. For one
        entry it is that entry, and only once it has been shown to exist.
        """
        if request.scope is ErasureScope.ALL:
            return self._conversations(request.user_id)

        entry_id = str(request.entry_id)
        if not self._entry_records(entry_id):
            raise ErasureRefused(
                f"nothing was ever written from an entry called {entry_id!r}, "
                "so there is nothing to erase"
            )
        return [entry_id]

    def _entry_records(self, entry_id: str) -> list[str]:
        """The graph records one entry produced."""
        return self._targets.for_entry(entry_id)

    def _conversations(self, user_id: str) -> list[str]:
        """
        Every conversation this person has had, by identifier.

        Read from the working database rather than from the graph, because a
        conversation that never made it through the pipeline still holds
        every word they typed.
        """
        return self._ops.buffers.list_session_ids(user_id)


def _now() -> datetime:
    """The moment an erasure is being asked for."""
    return datetime.now(timezone.utc)


__all__ = ["ErasureService"]
