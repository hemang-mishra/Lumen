"""
Finding history that reads like the thing just written.

The vector store answers with the closest matches to a piece of text. That
is most of what is needed, but not all of it — three things happen to the
answer before it is usable.

It is read back from the graph, because the vector store knows only ids and
scores and reconciliation needs to see what the nodes actually say.

It is filtered, because a node that has been superseded or suspended should
not be offered as something to reconcile against, and a node from the very
episode being processed must never be offered at all.

It is reweighted, because raw closeness is not the same as importance. A
life-defining realisation described in slightly different words should
outrank a routine note phrased almost identically, and it will not unless
something says so.
"""

from __future__ import annotations

import logging

from lumen.config import PipelineConfig
from lumen.graph.provider import GraphProvider
from lumen.pipeline.retrieval.hydrate import preview_of, signal_of
from lumen.schemas.enums import CandidateRetrievalSource, SignalStrength
from lumen.schemas.pipeline import CandidateNode
from lumen.vector.provider import ScoredHit, VectorProvider

logger = logging.getLogger(__name__)

# How much more a weighty node is worth when ranking. A realisation that
# changed how someone sees themselves earns its place in a short candidate
# list ahead of a routine note that happens to be worded alike.
SIGNAL_WEIGHT: dict[SignalStrength, float] = {
    SignalStrength.STANDARD: 1.0,
    SignalStrength.HIGH: 1.5,
    SignalStrength.CRITICAL: 2.0,
}

# Kinds of node worth reconciling against. Everything else in the graph —
# audit records, decisions, reports — is machinery, not history.
CONTENT_TABLES: frozenset[str] = frozenset(
    {
        "ObservationNode",
        "EventNode",
        "SessionNode",
        "PatternNode",
        "BeliefNode",
        "LessonNode",
        "AdoptedPrincipleNode",
        "OpenLoopNode",
    }
)

# Statuses that mean a node is no longer part of the live picture.
RETIRED_STATUSES: frozenset[str] = frozenset(
    {"SUPERSEDED", "SUSPENDED", "EXTRACTION_FAILED"}
)


def find_by_resemblance(
    vector: list[float],
    *,
    exclude_episode: str,
    graph: GraphProvider,
    vectors: VectorProvider,
    config: PipelineConfig,
) -> list[CandidateNode]:
    """
    Search for what reads like this, then keep the few that matter.

    More matches are fetched than are kept. Ranking happens after the
    search, and a weighty node can sit just below the cut on raw closeness
    while belonging above it once its weight counts — fetching only what is
    kept would throw it away before anything could notice.
    """
    hits = _search(vector, vectors=vectors, limit=config.pass_a_overfetch)
    if not hits:
        return []

    rows = {
        row["node_id"]: row
        for row in graph.get_nodes_by_ids([hit.node_id for hit in hits])
        if row.get("node_id")
    }

    ranked = sorted(
        (
            (hit, rows[hit.node_id])
            for hit in hits
            if hit.node_id in rows and _is_usable(rows[hit.node_id], exclude_episode)
        ),
        key=lambda pair: _weighted(pair[0], pair[1]),
        reverse=True,
    )

    return [
        CandidateNode(
            node_id=hit.node_id,
            node_type=row.get("_label", "unknown"),
            content_preview=preview_of(row),
            # The honest closeness, not the weighted number used to rank.
            # Weighting can reach twice the maximum this field allows, and
            # a caller reading it should get the measurement rather than a
            # ranking decision baked into it.
            similarity_score=_clamped(hit.score),
            retrieval_source=CandidateRetrievalSource.SEMANTIC,
        )
        for hit, row in ranked[: config.pass_a_keep]
    ]


def _search(
    vector: list[float], *, vectors: VectorProvider, limit: int
) -> list[ScoredHit]:
    """
    Ask the vector store for its closest matches.

    A failure here is contained rather than raised, because the anchors are
    still worth asking. Whether anything was searched at all is reported
    separately by the caller.
    """
    try:
        return vectors.hybrid_search(vector, limit=limit)
    except Exception as exc:  # noqa: BLE001 — a broken search must not lose the anchors
        logger.warning(
            "the similarity search failed and was skipped",
            extra={"reason": type(exc).__name__},
        )
        return []


def _is_usable(row: dict, exclude_episode: str) -> bool:
    """
    Decide whether a match is worth offering.

    The episode check is the one that would do real damage if it were
    missing. A run that is replayed finds its own nodes already stored, and
    a node offered as a candidate for itself reconciles as a perfect match
    with total confidence — quietly merging something with itself.
    """
    if row.get("_label") not in CONTENT_TABLES:
        return False
    if (row.get("status") or "") in RETIRED_STATUSES:
        return False
    return row.get("episode_id") != exclude_episode


def _weighted(hit: ScoredHit, row: dict) -> float:
    """How this match ranks once the node's own weight is counted."""
    return hit.score * SIGNAL_WEIGHT[signal_of(row)]


def _clamped(score: float) -> float:
    """
    Hold a similarity inside the range the contract allows.

    Cosine similarity is mathematically bounded, but floating point sums
    overshoot slightly and a stored score of 1.0000000009 would be refused
    by the model it is going into.
    """
    return max(0.0, min(1.0, score))


__all__ = ["find_by_resemblance", "SIGNAL_WEIGHT", "CONTENT_TABLES", "RETIRED_STATUSES"]
