"""
Carrying out an erasure.

The only writer in this package, and the order it works in is deliberate.

The record of the erasure is opened *first*, before anything is touched. An
erasure that dies halfway leaves a history partly forgotten, and the one
thing worse than that is a history partly forgotten with nothing saying so.

Then the graph, a batch at a time. Not one batch because a whole history in
one statement would hold the store's write lock for as long as it took, and
somebody could be mid-conversation while this runs. Small batches give that
conversation gaps to run in.

Then the search index, because a stored position is a reconstruction of the
words it was made from — a record whose text is gone but whose position is
not is still findable by everything it used to say.

Then the working database, which holds the person's own sentences rather
than what was read out of them. An erasure that cleaned only the graph would
leave the originals on disk.

Nothing is rolled back if a step fails. Less content than before is the
direction that was asked for, and putting the words back would undo exactly
what somebody requested.
"""

from __future__ import annotations

import logging
from datetime import datetime

from lumen.config import MaintenanceConfig
from lumen.erasure.contracts import ErasureReport, ErasureRequest
from lumen.erasure.targets import MAX_RECORDED_ENTRY_IDS, GraphTargets
from lumen.graph.provider import GraphProvider
from lumen.operational.enums import ErasureScope, ErasureStatus
from lumen.operational.repositories import OperationalStore
from lumen.operational.schemas import ErasureAuditRecord
from lumen.vector.provider import VectorProvider

logger = logging.getLogger(__name__)

# What an erasure record is called. Dated, and numbered within its day.
AUDIT_ID_PREFIX = "era"


class ErasureRunner:
    """
    Rewrites a history so that it says nothing, and proves it did.

    Everything it works on is handed in, so the same object erases a real
    deployment and a set of temporary stores in a test without knowing the
    difference.
    """

    def __init__(
        self,
        *,
        graph: GraphProvider,
        vectors: VectorProvider,
        ops: OperationalStore,
        config: MaintenanceConfig | None = None,
    ) -> None:
        self._graph = graph
        self._vectors = vectors
        self._ops = ops
        self._config = config or MaintenanceConfig()
        self._targets = GraphTargets(graph, batch_size=self._config.erasure_batch_size)

    def run(
        self, request: ErasureRequest, *, entry_ids: list[str], at: datetime
    ) -> ErasureReport:
        """
        Erase what the request asks for and record what happened.

        Never raises for a failure partway through. A caller has to be able
        to tell "erased" from "partly erased" and act on the difference, and
        an exception carries neither the counts nor the record it left
        behind.
        """
        audit_id = self._open_record(request, at=at)
        failures: list[str] = []

        records, vectors = self._erase_the_graph(request, failures, at=at)
        rows = self._erase_the_working_store(request, entry_ids, failures, at=at)

        status = ErasureStatus.FAILED if failures else ErasureStatus.COMPLETE
        self._close_record(
            audit_id,
            status=status,
            records=records,
            vectors=vectors,
            entry_ids=entry_ids,
        )

        report = ErasureReport(
            audit_id=audit_id,
            scope=request.scope,
            entry_id=request.entry_id,
            status=status,
            records_anonymized=records,
            vectors_deleted=vectors,
            operational_rows_cleared=rows,
            entry_ids_affected=tuple(entry_ids[:MAX_RECORDED_ENTRY_IDS]),
            failures=tuple(failures),
        )
        _log(report)
        return report

    # ------------------------------------------------------------------
    # The graph and the index, which move together
    # ------------------------------------------------------------------

    def _erase_the_graph(
        self, request: ErasureRequest, failures: list[str], *, at: datetime
    ) -> tuple[int, int]:
        """
        Rewrite every record in scope and drop its position in the index.

        The two happen together, batch by batch, rather than as two sweeps.
        Between them is the only moment when a record's words are gone and
        its position is not, and keeping that moment as short as one batch is
        free.
        """
        records = 0
        vectors = 0

        for batch in self._batches(request):
            records += self._attempt(
                "graph", failures, lambda ids=batch: self._anonymize(ids, at=at)
            )
            vectors += self._attempt(
                "index", failures, lambda ids=batch: self._vectors.delete(ids)
            )

        return records, vectors

    def _batches(self, request: ErasureRequest):
        """The records to erase, in pages small enough not to block anybody."""
        if request.scope is ErasureScope.ALL:
            for _, page in self._targets.everything():
                yield page
            return

        found = self._targets.for_entry(str(request.entry_id))
        size = self._config.erasure_batch_size
        for start in range(0, len(found), size):
            yield found[start : start + size]

    def _anonymize(self, node_ids: list[str], *, at: datetime) -> int:
        """
        Rewrite one batch inside a transaction of its own.

        Every batch is stamped with the moment the erasure was asked for
        rather than the moment it reached that batch, so a sweep that takes
        an hour does not leave a history dated across an hour.
        """
        with self._graph.transaction():
            return self._graph.anonymize_nodes(node_ids, at=at)

    # ------------------------------------------------------------------
    # The working database, which holds the sentences themselves
    # ------------------------------------------------------------------

    def _erase_the_working_store(
        self,
        request: ErasureRequest,
        entry_ids: list[str],
        failures: list[str],
        *,
        at: datetime,
    ) -> int:
        """
        Clear the person's own words out of the operational database.

        Each store clears its own tables. Erasure asking the database
        directly would mean it knowing the shape of five tables it does not
        own, and a column added to any of them would quietly stop being
        erased.
        """
        whole = request.scope is ErasureScope.ALL
        sessions = None if whole else entry_ids
        user_id = request.user_id

        cleared = 0
        cleared += self._attempt(
            "conversations",
            failures,
            lambda: self._ops.buffers.purge_content(
                user_id, at=at, session_ids=sessions
            ),
        )
        cleared += self._attempt(
            "coreference notes",
            failures,
            lambda: self._ops.coref.purge(session_ids=sessions),
        )
        cleared += self._attempt(
            "uploads",
            failures,
            lambda: self._ops.imports.purge_content(
                user_id, at=at, session_ids=sessions
            ),
        )

        # Only a whole erasure touches these two. A queued question can be
        # about a standing record built from many entries, and a person's
        # settings are not part of any entry at all.
        if whole:
            cleared += self._attempt(
                "review queue",
                failures,
                lambda: self._ops.hitl.purge_content(user_id, at=at),
            )
            cleared += self._attempt(
                "settings", failures, lambda: self._ops.settings.purge(user_id)
            )

        return cleared

    # ------------------------------------------------------------------
    # The proof
    # ------------------------------------------------------------------

    def _open_record(self, request: ErasureRequest, *, at: datetime) -> str:
        """Write the record before any of the work, so a crash still leaves one."""
        audit_id = self._next_audit_id(request.user_id, at=at)
        self._ops.erasure.record(
            ErasureAuditRecord(
                id=audit_id,
                user_id=request.user_id,
                erased_at=at,
                initiated_by=request.initiated_by,
                status=ErasureStatus.IN_PROGRESS,
            )
        )
        return audit_id

    def _close_record(
        self,
        audit_id: str,
        *,
        status: ErasureStatus,
        records: int,
        vectors: int,
        entry_ids: list[str],
    ) -> None:
        """
        Say what the erasure did, or log that even that could not be said.

        A record left saying "in progress" is not a lie — it is exactly what
        is known when the closing write fails — so nothing is invented here.
        """
        try:
            self._ops.erasure.finish(
                audit_id,
                status=status,
                nodes_anonymized=records,
                embeddings_deleted=vectors,
                entry_ids_affected=entry_ids[:MAX_RECORDED_ENTRY_IDS],
            )
        except Exception:
            logger.error(
                "an erasure ran and its record could not be closed, so it "
                "still reads as unfinished",
                exc_info=True,
                extra={"audit_id": audit_id},
            )

    def _next_audit_id(self, user_id: str, *, at: datetime) -> str:
        """
        The next unused name for today's erasure record.

        Numbered within the day, so two erasures on one day are two records
        rather than one overwriting the other.
        """
        taken = {
            record.id
            for record in self._ops.erasure.list_for_user(user_id)
        }
        day = at.date().strftime("%Y_%m_%d")
        for number in range(1, 1000):
            candidate = f"{AUDIT_ID_PREFIX}_{day}_{number:03d}"
            if candidate not in taken:
                return candidate
        raise RuntimeError("a thousand erasures in one day is not a real request")

    def _attempt(self, what: str, failures: list[str], step) -> int:
        """
        Run one step, and note rather than raise if it will not run.

        An erasure that stops at the first refusal leaves more words behind
        than one that carries on and reports what it could not reach. The
        failures travel back with the report, and the record says FAILED.
        """
        try:
            return int(step() or 0)
        except Exception as exc:  # noqa: BLE001 — every step is worth attempting
            failures.append(f"{what}: {type(exc).__name__}")
            logger.error(
                "part of an erasure could not be carried out",
                exc_info=True,
                extra={"step": what},
            )
            return 0


def _log(report: ErasureReport) -> None:
    """One line about an erasure, with nothing in it about what was erased."""
    logger.warning(
        "an erasure was carried out",
        extra={
            "audit_id": report.audit_id,
            "scope": report.scope.value,
            "status": report.status.value,
            "records": report.records_anonymized,
            "vectors": report.vectors_deleted,
            "rows": report.operational_rows_cleared,
            "failures": len(report.failures),
        },
    )


__all__ = ["ErasureRunner", "AUDIT_ID_PREFIX"]
