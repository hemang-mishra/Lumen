"""
Deciding what becomes findable, and turning it into numbers.

A record in the graph can be reached by following links to it. Being found
by *meaning* — "what have I said before that sounds like this?" — needs a
separate entry in the search index, and a record without one is invisible to
every future search no matter how relevant it is.

Two rules run this module, and both are deliberately mechanical.

Which records get an entry is a fixed list, and that list is checked against
the one the search stage actually reads. Indexing something nothing ever
looks for is waste; failing to index something that is looked for is a hole
that never announces itself.

When the numbers are worked out is the other rule. It happens before
anything is written, so a failure here costs nothing at all — the graph has
not been touched and the whole episode can simply be run again.
"""

from __future__ import annotations

import logging
from typing import Any

from lumen.graph.provider import GraphProvider
from lumen.operational.enums import WriteTarget
from lumen.operational.repositories import OperationalStore
from lumen.pipeline.orchestration.contracts import EmbeddingFailed, IndexEntry
from lumen.pipeline.retrieval.semantic import CONTENT_TABLES, RETIRED_STATUSES
from lumen.providers.protocols import EmbeddingProvider, EmbeddingTaskType
from lumen.schemas.base import GraphNode
from lumen.schemas.pipeline import GraphWritePlan, PlannedNode
from lumen.vector.provider import VectorProvider

logger = logging.getLogger(__name__)

# The kinds of record worth making findable by meaning.
#
# Taken from the set the search stage reads rather than written out again. A
# test asserts the two are identical, so neither can quietly drift away from
# the other: searching for a kind of record nobody indexed, or indexing a
# kind of record nobody searches for, are both silent failures.
INDEXED_NODE_TYPES: frozenset[str] = CONTENT_TABLES

# Where each kind of record keeps the words worth searching, in the order to
# try them. Deliberately not the shortened version used to preview a search
# result — a record indexed from a truncated preview is only findable by its
# first few lines.
_TEXT_FIELDS: dict[str, tuple[str, ...]] = {
    "ObservationNode": ("content",),
    "EventNode": ("event_summary",),
    "SessionNode": ("session_summary",),
    "PatternNode": ("pattern_name", "pattern_description"),
    "BeliefNode": ("belief_statement",),
    "LessonNode": ("lesson_statement",),
    "AdoptedPrincipleNode": ("principle_statement",),
    "OpenLoopNode": ("loop_description",),
}


def prepare_index(
    plan: GraphWritePlan, *, embedder: EmbeddingProvider
) -> list[IndexEntry]:
    """
    Work out the searchable form of everything this plan will create.

    Called before the plan is saved, never after. One request covers the
    whole episode rather than one per record, which for a rich entry is the
    difference between one call and forty.

    A failure here raises, and because nothing has been written yet the
    episode ends with the graph untouched. That ordering is the whole reason
    this runs first.
    """
    candidates = [
        (planned, text)
        for planned in plan.nodes
        if (text := text_for_index(planned)) is not None
    ]
    if not candidates:
        return []

    try:
        vectors = embedder.embed_batch(
            [text for _, text in candidates],
            task_type=EmbeddingTaskType.DOCUMENT,
        )
    except Exception as exc:
        raise EmbeddingFailed(
            f"could not turn {len(candidates)} record(s) into searchable form: {exc}"
        ) from exc

    if len(vectors) != len(candidates):
        raise EmbeddingFailed(
            f"asked for {len(candidates)} vectors and got back {len(vectors)}"
        )

    return [
        IndexEntry(
            node_id=planned.node.node_id,
            node_type=planned.node_type,
            text=text,
            vector=vector,
            payload=_payload_for(planned.node_type, planned.node),
        )
        for (planned, text), vector in zip(candidates, vectors, strict=True)
    ]


def text_for_index(planned: PlannedNode) -> str | None:
    """
    The words to search this record by, or None if it should not be indexed.

    A record may name its own searchable wording, and that always wins — it
    lets a decision say "index this new belief by what it means" rather than
    by whatever field happens to hold its text. Nothing sets it today, so in
    practice the wording comes from the record's own content.
    """
    if not _is_indexable(planned):
        return None

    if planned.searchable_text and planned.searchable_text.strip():
        return planned.searchable_text.strip()

    parts = [
        value.strip()
        for field in _TEXT_FIELDS.get(planned.node_type, ())
        if isinstance(value := getattr(planned.node, field, None), str) and value.strip()
    ]
    if not parts:
        logger.debug(
            "record has no text to search it by",
            extra={"node_id": planned.node.node_id, "node_type": planned.node_type},
        )
        return None
    return " ".join(parts)


def _is_indexable(planned: PlannedNode) -> bool:
    """
    Whether this record should be findable by meaning at all.

    Two reasons to say no. It is machinery rather than something the person
    said — the note of a decision, a person's own record — or it is a record
    that has been retired, which the search stage filters out on the way
    back anyway.
    """
    if planned.node_type not in INDEXED_NODE_TYPES:
        return False
    status = getattr(planned.node, "status", None)
    return getattr(status, "value", status) not in RETIRED_STATUSES


def _payload_for(node_type: str, node: GraphNode) -> dict[str, Any]:
    """
    The few plain facts stored beside a record in the search index.

    The search stage reads the real record from the graph and never relies
    on these. They are here so the index can be opened and understood on its
    own, which is what the first disagreement between the two stores will
    need.
    """
    payload: dict[str, Any] = {"node_id": node.node_id, "node_type": node_type}
    for field in ("episode_id", "occurred_at", "signal_strength", "status"):
        value = getattr(node, field, None)
        if value is None:
            continue
        payload[field] = getattr(value, "value", None) or _as_text(value)
    return payload


def _as_text(value: Any) -> str:
    """A stored form for a payload value, with dates written out in full."""
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def repair_index(
    trace_id: str,
    *,
    ops: OperationalStore,
    graph: GraphProvider,
    vectors: VectorProvider,
    embedder: EmbeddingProvider,
) -> list[str]:
    """
    Give a search entry to records that were saved without one.

    Saving happens in two stores that cannot share a single transaction, so
    there is one gap nothing else can close: the graph commits, and then the
    search index write fails. Those records are real and correct and cannot
    be found.

    Nothing has to be remembered for this to work. The run log already
    records every record written and every record indexed, separately, so
    the difference between the two lists *is* the repair list. Running it
    twice is harmless: the second run finds nothing left to do.

    Returns the identifiers it repaired.
    """
    trace = ops.jobs.get_trace(trace_id)
    if trace is None:
        logger.warning("no run found to repair", extra={"repairing_run": trace_id})
        return []

    written = [
        w for w in trace.writes if w.target is WriteTarget.GRAPH_NODE and w.node_id
    ]
    indexed = {w.node_id for w in trace.writes if w.target is WriteTarget.VECTOR}
    missing = [w for w in written if w.node_id not in indexed]
    if not missing:
        return []

    rows = {
        str(row["node_id"]): row
        for row in graph.get_nodes_by_ids([w.node_id for w in missing])
        if row.get("node_id")
    }

    repaired: list[str] = []
    for write in missing:
        row = rows.get(write.node_id)
        if row is None:
            logger.warning(
                "a record in the run log is no longer in the graph",
                extra={"node_id": write.node_id},
            )
            continue

        node_type = str(row.get("_label", ""))
        text = _text_from_row(node_type, row)
        if text is None:
            continue

        vector = embedder.embed_batch([text], task_type=EmbeddingTaskType.DOCUMENT)[0]
        vectors.upsert(
            write.node_id, vector, {"node_id": write.node_id, "node_type": node_type}
        )
        ops.jobs.record_write(
            job_id=trace.job.job_id,
            stage=write.stage,
            target=WriteTarget.VECTOR,
            node_id=write.node_id,
            episode_id=write.episode_id,
        )
        repaired.append(write.node_id)

    logger.info(
        "repaired missing search entries",
        extra={"repairing_run": trace_id, "repaired": len(repaired)},
    )
    return repaired


def _text_from_row(node_type: str, row: dict[str, Any]) -> str | None:
    """The searchable wording of a record read back out of the graph."""
    if node_type not in INDEXED_NODE_TYPES:
        return None
    parts = [
        str(row[field]).strip()
        for field in _TEXT_FIELDS.get(node_type, ())
        if row.get(field) and str(row[field]).strip()
    ]
    return " ".join(parts) if parts else None


__all__ = [
    "INDEXED_NODE_TYPES",
    "prepare_index",
    "text_for_index",
    "repair_index",
]
