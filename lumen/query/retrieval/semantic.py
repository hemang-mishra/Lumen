"""
Pass A — finding history that reads like what was just said.

The search index answers with the records closest to a piece of text. Four
things happen to that answer before it is usable.

It is read back from the graph, because the index knows only identifiers and
distances, and a conversation needs to know what the records actually say.

It is filtered down to history that is still in play — machinery and
superseded records are not worth putting in front of anyone.

It is narrowed to the kinds of record the reason could be answered by.
Somebody describing a tight chest should not be answered with career
beliefs, and the reason they gave already says as much.

And it is reweighted, because raw closeness is not importance. A realisation
that changed how somebody sees themselves should outrank a routine note
worded almost identically, and it will not unless something says so.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from lumen.config import QueryConfig
from lumen.graph.provider import ReadOnlyGraph
from lumen.graph.rows import is_live_content
from lumen.providers.protocols import EmbeddingProvider, LLMProvider
from lumen.query.retrieval import hyde
from lumen.query.retrieval.contracts import (
    PassAResult,
    RetrievedNode,
    SearchUnavailable,
    Tally,
)
from lumen.query.retrieval.hydrate import has_id, to_node
from lumen.schemas.enums import ObservationType, RetrievalPass, TriggerType
from lumen.schemas.query import RetrievalTrigger
from lumen.vector.provider import ScoredHit, VectorProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NodeFilter:
    """
    Which records a particular reason can be answered by.

    Empty means anything that is live history. Where a reason names kinds,
    only those kinds survive — and for observations the filter goes one
    level deeper, because "observation" covers about fifty different things
    and only a few of them are about the body.

    Attributes:
        tables: Kinds of record allowed through, or none for all of them.
        observation_types: When an observation is allowed, which sorts.
    """

    tables: frozenset[str] = frozenset()
    observation_types: frozenset[str] = field(default_factory=frozenset)

    def allows(self, row: dict[str, Any]) -> bool:
        """Whether this record is the sort the reason was asking about."""
        if self.tables and row.get("_label") not in self.tables:
            return False
        if (
            self.observation_types
            and row.get("_label") == "ObservationNode"
            and str(row.get("type") or "") not in self.observation_types
        ):
            return False
        return True


# What each reason narrows the search to.
#
# Only two reasons narrow it at all, and both for the same cause: they name
# an experience the graph records under a specific type, so an unrestricted
# search would bury the four records that answer the question under fifty
# that merely mention the same words.
WANTED: dict[TriggerType, NodeFilter] = {
    TriggerType.SOMATIC_MARKER: NodeFilter(
        tables=frozenset({"ObservationNode"}),
        observation_types=frozenset(
            {
                ObservationType.PHYSIOLOGICAL_CAPACITY_STATE.value,
                ObservationType.SUPPRESSED_EMOTION_SURFACING.value,
            }
        ),
    ),
    TriggerType.IDENTITY_STATEMENT: NodeFilter(
        tables=frozenset({"BeliefNode", "ObservationNode"}),
        observation_types=frozenset(
            {
                ObservationType.BELIEF.value,
                ObservationType.META_BELIEF.value,
                ObservationType.IDENTITY_FUSION_STATE.value,
            }
        ),
    ),
}

UNRESTRICTED = NodeFilter()


def find_by_resemblance(
    turn_text: str,
    triggers: tuple[RetrievalTrigger, ...],
    *,
    graph: ReadOnlyGraph,
    vectors: VectorProvider,
    embedder: EmbeddingProvider,
    llm: LLMProvider,
    config: QueryConfig,
) -> PassAResult:
    """
    Invent a record per reason, search for each, and keep the best few.

    The vector for the first reason comes back with the result. It is what
    the continuity check measures today's earlier records against, and
    handing it over costs nothing next to asking a model to measure the same
    sentence twice.

    More matches are fetched than kept, because ranking happens after the
    search: a weighty record can sit just below the cut on raw distance and
    belong above it once its weight counts.
    """
    if not triggers:
        return PassAResult()

    text = hyde.write_search_text(turn_text, triggers, provider=llm)
    embedded, failed = hyde.to_vectors(text, embedder=embedder)
    if failed or not embedded:
        # Nothing was searched at all. Said out loud by raising, so the caller
        # reports "could not look" rather than "found nothing".
        raise SearchUnavailable("the search text could not be turned into vectors")


    tally = Tally()
    found: dict[str, RetrievedNode] = {}
    seen = 0
    for position, trigger in enumerate(triggers):
        hits = _search(
            embedded[position],
            vectors=vectors,
            limit=max(config.conversational_pass_a_overfetch, 1),
            tally=tally,
        )
        seen += len(hits)
        for node in _read_back(hits, trigger, graph=graph, tally=tally):
            held = found.get(node.node_id)
            # One record can answer two reasons. It is offered once, under
            # whichever reason matched it more closely.
            if held is None or node.rank_score > held.rank_score:
                found[node.node_id] = node

    if tally.came_up_short(found=len(found)):
        # Nothing to show, and something refused along the way. An empty
        # answer here is indistinguishable from a person with no history, and
        # the layer above answers those two identically unless it is told.
        raise SearchUnavailable(
            f"{tally.failed} of {tally.attempted} searches failed and nothing "
            "was found, so nothing could be looked up"
        )

    ranked = sorted(found.values(), key=lambda node: node.rank_score, reverse=True)
    kept = tuple(ranked[: max(config.conversational_pass_a_keep, 0)])

    return PassAResult(
        candidates=kept,
        query_vector=tuple(embedded[0]),
        found=seen,
        used_fallback=text.used_fallback,
    )


def _read_back(
    hits: list[ScoredHit],
    trigger: RetrievalTrigger,
    *,
    graph: ReadOnlyGraph,
    tally: Tally,
) -> list[RetrievedNode]:
    """
    Read the matches out of the graph and keep the ones worth offering.

    A graph that refuses this read costs one reason its results rather than
    the whole pass — but it is counted, because a graph refusing every read
    is a turn that could not look rather than a turn that found nothing.
    """
    if not hits:
        return []

    tally.ran()
    try:
        fetched = graph.get_nodes_by_ids([hit.node_id for hit in hits])
    except Exception as exc:  # noqa: BLE001 — one failed read must not lose the rest
        tally.broke()
        logger.warning(
            "could not read a set of matches out of the graph",
            extra={"reason": type(exc).__name__, "wanted": len(hits)},
        )
        return []

    rows = {row["node_id"]: row for row in fetched if has_id(row)}
    wanted = WANTED.get(trigger.trigger_type, UNRESTRICTED)

    kept: list[RetrievedNode] = []
    for hit in hits:
        row = rows.get(hit.node_id)
        if row is None or not is_live_content(row) or not wanted.allows(row):
            continue
        kept.append(
            to_node(
                row,
                found_by=RetrievalPass.SEMANTIC,
                trigger_type=trigger.trigger_type,
                # The honest closeness, not the weighted number used for
                # ordering. Weighting can reach twice what this field
                # allows, and somebody reading it should get the
                # measurement rather than a ranking decision baked into it.
                similarity=_clamped(hit.score),
            )
        )
    return kept


def _search(
    vector: Sequence[float], *, vectors: VectorProvider, limit: int, tally: Tally
) -> list[ScoredHit]:
    """
    Ask the index for its closest matches.

    A failure here is contained rather than raised, because one reason
    failing should not cost the others — and counted, because a turn where
    every search failed is a different matter and nothing else can see that.
    """
    tally.ran()
    try:
        return vectors.hybrid_search(list(vector), limit=limit)
    except Exception as exc:  # noqa: BLE001 — one failed search must not lose the rest
        tally.broke()
        logger.warning(
            "a similarity search failed and was skipped",
            extra={"reason": type(exc).__name__},
        )
        return []


def _clamped(score: float) -> float:
    """
    Hold a similarity inside the range the contract allows.

    Cosine similarity is bounded by definition, but floating point sums
    overshoot slightly and a score of 1.0000000009 would be refused by the
    model it is going into.
    """
    return max(0.0, min(1.0, score))


__all__ = ["find_by_resemblance", "SearchUnavailable", "NodeFilter", "WANTED"]
