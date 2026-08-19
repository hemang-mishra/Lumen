"""
Saving one episode, all of it or none of it.

This is the only place in the system that writes to the graph, and it makes
no decisions while doing so. It is handed a finished plan and carries it out
in the order the plan gives. Everything worth arguing about was settled
before this module was called, which is what makes it short enough to read
in one sitting and check by eye.

The two stores cannot share a transaction. The graph gets a real one, so a
failure partway through leaves it exactly as it was. The search index cannot
join that transaction, so it is written afterwards and its failures are
reported by name rather than swallowed — a record that exists but cannot be
found is the one kind of damage that looks identical to success.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from lumen.graph.provider import GraphProvider
from lumen.pipeline.orchestration.contracts import (
    CommitReport,
    GraphWriteFailed,
    IndexEntry,
    IndexWriteFailed,
)
from lumen.schemas.enums import BookkeepingOperation
from lumen.schemas.pipeline import GraphWritePlan, PlannedBookkeeping
from lumen.vector.provider import VectorProvider

logger = logging.getLogger(__name__)

def _mark_hitl_resolved(graph: GraphProvider, update: PlannedBookkeeping) -> None:
    """
    Stamp a waiting decision with the answer somebody gave it.

    An instruction that does not say what was decided is refused rather than
    applied. Marking a decision answered without recording the answer would
    leave a note claiming somebody settled it and no way to find out how.
    """
    if update.choice is None or update.resolved_action is None:
        raise GraphWriteFailed(
            f"answer for {update.node_id} does not say what was decided"
        )
    graph.resolve_decision(
        update.node_id,
        choice=update.choice,
        action=update.resolved_action,
        at=update.at,
    )


# The small changes allowed to a record that already exists, and the one
# method each is allowed to call.
#
# A lookup rather than a chain of if-statements so that the complete list of
# ways an existing record can change is one readable block. Adding a fifth
# means adding a line here, which is a visible act; growing a branch in the
# middle of a save is not.
BookkeepingCall = Callable[[GraphProvider, PlannedBookkeeping], None]

BOOKKEEPING_OPERATIONS: dict[BookkeepingOperation, BookkeepingCall] = {
    BookkeepingOperation.MARK_SUPERSEDED: (
        lambda graph, update: graph.mark_superseded(update.node_id, at=update.at)
    ),
    BookkeepingOperation.RECORD_REINFORCEMENT: (
        lambda graph, update: graph.record_reinforcement(update.node_id, at=update.at)
    ),
    BookkeepingOperation.TOUCH_PERSON: (
        lambda graph, update: graph.touch_person(update.node_id, at=update.at)
    ),
    BookkeepingOperation.MARK_HITL_RESOLVED: _mark_hitl_resolved,
}


def commit(
    plan: GraphWritePlan,
    entries: list[IndexEntry],
    *,
    graph: GraphProvider,
    vectors: VectorProvider,
) -> CommitReport:
    """
    Carry out one episode's plan.

    The graph half either lands whole or not at all. Records go first, then
    the links between them, then the small updates to records that already
    existed — an order the plan itself guarantees is workable, having
    checked when it was built that nothing points at something created
    later.

    Only after the graph has committed is the search index written. If that
    part fails the graph is still right, so the failure names the records
    that cannot be found rather than pretending the episode did not happen.
    """
    report = _write_graph(plan, graph=graph)
    indexed, missing = _write_index(entries, vectors=vectors)

    report = report.model_copy(
        update={"vectors_written": indexed, "unindexed_node_ids": missing}
    )
    if missing:
        raise IndexWriteFailed(report)
    return report


def _write_graph(plan: GraphWritePlan, *, graph: GraphProvider) -> CommitReport:
    """
    Write every record, link and update in one transaction.

    Anything that goes wrong undoes the lot. The alternative — stopping
    where it broke — leaves an entry that reads as complete to anything
    looking at the graph while missing half of what the person said, and
    nothing downstream could ever tell.
    """
    nodes: list[str] = []
    edges: list[tuple[str, str, str]] = []

    try:
        with graph.transaction():
            for planned in plan.nodes:
                graph.write_node(planned.node_type, planned.node)
                nodes.append(planned.node.node_id)

            for edge in plan.edges:
                graph.write_edge(
                    edge.table,
                    edge.from_node_id,
                    edge.to_node_id,
                    edge.properties(),
                )
                edges.append((edge.table, edge.from_node_id, edge.to_node_id))

            for operation in plan.bookkeeping:
                _apply_bookkeeping(operation, graph=graph)
    except Exception as exc:
        logger.warning(
            "episode could not be saved; every write was undone",
            extra={
                "records_attempted": len(nodes),
                "links_attempted": len(edges),
                "error": str(exc),
            },
        )
        raise GraphWriteFailed(str(exc)) from exc

    return CommitReport(nodes_written=nodes, edges_written=edges)


def _apply_bookkeeping(
    operation: PlannedBookkeeping, *, graph: GraphProvider
) -> None:
    """
    Make one small change to a record that already exists.

    The operation is looked up rather than branched on, so there is no way
    to reach anything but the named changes, and no way for a field name to
    be passed in. Nothing the person wrote can be touched from here even by
    mistake.
    """
    call = BOOKKEEPING_OPERATIONS.get(operation.operation)
    if call is None:
        raise GraphWriteFailed(
            f"no such bookkeeping operation: {operation.operation}"
        )
    call(graph, operation)


def _write_index(
    entries: list[IndexEntry], *, vectors: VectorProvider
) -> tuple[list[str], list[str]]:
    """
    Add every record to the search index, and report which ones would not go.

    Every entry is attempted even after one fails. Stopping at the first
    would hide nine healthy records behind one broken one, and the point of
    this list is to be complete enough to repair from.
    """
    written: list[str] = []
    missing: list[str] = []

    for entry in entries:
        try:
            vectors.upsert(entry.node_id, entry.vector, entry.payload)
            written.append(entry.node_id)
        except Exception as exc:
            logger.error(
                "a saved record could not be made searchable",
                extra={"node_id": entry.node_id, "error": str(exc)},
            )
            missing.append(entry.node_id)

    return written, missing


__all__ = ["commit", "BOOKKEEPING_OPERATIONS"]
