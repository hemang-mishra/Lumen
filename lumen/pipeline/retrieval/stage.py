"""
The stage that brings the person's history back.

Stage 1 reads today's entry blind, on purpose — a model shown someone's
existing patterns stops reading and starts matching. This is where that
history is allowed back in, one small handful at a time: for each thing
just noticed, the few existing nodes that might be the same thing, or the
thing it grew out of.

It is the hinge of the whole design. Hand the next stage too little and it
creates a fresh node for something the person has said twenty times; hand
it the wrong things and it merges two ideas that were never the same.

The failure to watch for is not an error. **A search that quietly returns
nothing looks exactly like a person having a genuinely new thought**, and
the next stage answers both the same way — by writing a new node. So this
stage distinguishes the two out loud: finding nothing is reported as
finding nothing, and being unable to look is reported as being unable to
look.

Nothing here writes. The two stores are handed in, read from, and left
exactly as they were.
"""

from __future__ import annotations

import logging
import time

from lumen.config import AppConfig, PipelineConfig
from lumen.graph.provider import GraphProvider
from lumen.pipeline.retrieval import hyde, merge, semantic, structural
from lumen.pipeline.retrieval.contracts import SearchTarget
from lumen.providers.protocols import EmbeddingProvider, LLMProvider
from lumen.schemas.enums import ObservationStatus
from lumen.schemas.pipeline import (
    CoreferenceMap,
    ExtractionResult,
    MicroextractionInput,
    RetrievalResult,
)
from lumen.vector.provider import VectorProvider

logger = logging.getLogger(__name__)


def retrieve(
    extraction: ExtractionResult,
    *,
    graph: GraphProvider,
    vectors: VectorProvider,
    embedder: EmbeddingProvider,
    lightweight: LLMProvider,
    episode: MicroextractionInput | None = None,
    config: AppConfig | None = None,
) -> list[RetrievalResult]:
    """
    Find, for everything just extracted, what the person has said before.

    One result per searchable node. The stores are parameters rather than
    something this function reaches for, so it can be pointed at a seeded
    database in a test and at the real one in a run, and so it can be read
    to see exactly what it touches.

    The episode is optional and only adds reach: it carries the people
    named and the period referred to, which are what the anchor lookups
    follow. Without it the anchors that depend on the entry are skipped and
    the search still runs.
    """
    started = time.perf_counter()
    limits = (config or AppConfig()).pipeline
    targets = _searchable(extraction)

    if not targets:
        _log_outcome(extraction, targets=(), results=[], anchors=0, duration_ms=0)
        return []

    search_text = hyde.write_search_text(targets, provider=lightweight)
    search_vectors, embedding_failed = hyde.to_vectors(search_text, embedder=embedder)

    anchored = structural.find_by_anchors(
        people=_people_named(extraction, episode),
        era=episode.episode.historical_era if episode else None,
        graph=graph,
        config=limits,
    )

    results = [
        _for_target(
            target,
            vector=search_vectors[position] if not embedding_failed else None,
            anchored=anchored,
            graph=graph,
            vectors=vectors,
            limits=limits,
            embedding_failed=embedding_failed,
        )
        for position, target in enumerate(targets)
    ]

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    _log_outcome(
        extraction,
        targets=targets,
        results=results,
        anchors=len(anchored),
        duration_ms=elapsed_ms,
        hyde_fallback=search_text.used_fallback,
        embedding_failed=embedding_failed,
    )
    return results


def _for_target(
    target: SearchTarget,
    *,
    vector: list[float] | None,
    anchored: list,
    graph: GraphProvider,
    vectors: VectorProvider,
    limits: PipelineConfig,
    embedding_failed: bool,
) -> RetrievalResult:
    """Gather and settle the candidates for one extracted node."""
    started = time.perf_counter()

    resembling = (
        []
        if vector is None
        else semantic.find_by_resemblance(
            vector,
            exclude_episode=target.episode_id,
            graph=graph,
            vectors=vectors,
            config=limits,
        )
    )
    kept_semantic, kept_structural = merge.merge(
        resembling, list(anchored), cap=limits.merged_candidate_cap
    )

    return RetrievalResult(
        source_node_id=target.node_id,
        pass_a_candidates=kept_semantic,
        pass_b_candidates=kept_structural,
        retrieval_time_ms=int((time.perf_counter() - started) * 1000),
        search_failed=embedding_failed,
    )


def _searchable(extraction: ExtractionResult) -> tuple[SearchTarget, ...]:
    """
    Pick out the nodes worth looking for history on.

    Findings, things that happened, and the session the thinking happened
    in — the three kinds the next stage can act on, and the three the graph
    has edges for. Cause-and-effect sequences are left out: they belong to
    their episode and there is no way to relate one to another, so
    candidates for them would have nowhere to go.

    Findings from a thin entry are left out too. They are written straight
    to the graph without ever being compared against the person's history,
    so searching for candidates would be work nobody reads.
    """
    targets: list[SearchTarget] = []

    for node in extraction.observations:
        if node.status is ObservationStatus.RAW_CAPTURE:
            continue
        targets.append(
            SearchTarget(
                node_id=node.node_id,
                node_type="ObservationNode",
                text=node.content,
                episode_id=node.episode_id,
            )
        )

    targets.extend(
        SearchTarget(
            node_id=node.node_id,
            node_type="EventNode",
            text=node.event_summary,
            episode_id=node.episode_id,
        )
        for node in extraction.events
    )
    targets.extend(
        SearchTarget(
            node_id=node.node_id,
            node_type="SessionNode",
            text=node.session_summary,
            episode_id=node.episode_id,
        )
        for node in extraction.sessions
    )
    return tuple(targets)


def _people_named(
    extraction: ExtractionResult, episode: MicroextractionInput | None
) -> tuple[str, ...]:
    """
    Collect everyone this entry refers to, without repeats.

    Taken from two places, because they know different things. The
    extracted nodes name whoever a particular finding was about; the
    entry's reference map also holds people who were only ever referred to
    by a pronoun.
    """
    names: list[str] = []
    for node in (*extraction.observations, *extraction.events):
        names.extend(node.person_refs)
    if episode is not None:
        names.extend(_resolved_names(episode.coreference_map))

    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        cleaned = name.strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            ordered.append(cleaned)
    return tuple(ordered)


def _resolved_names(coreference_map: CoreferenceMap) -> list[str]:
    """
    The people the entry settled on.

    Unsettled references are left out on purpose. A reference the earlier
    stage refused to resolve should not be quietly resolved here by looking
    up both candidates and pulling in one of their histories.
    """
    return [entity.resolved_to for entity in coreference_map.resolved_entities]


def _log_outcome(
    extraction: ExtractionResult,
    *,
    targets: tuple[SearchTarget, ...],
    results: list[RetrievalResult],
    anchors: int,
    duration_ms: int,
    hyde_fallback: bool = False,
    embedding_failed: bool = False,
) -> None:
    """
    Record one line about what came back.

    The counts are the only warning there is for this stage's quiet
    failure. A search that has stopped returning anything breaks nothing
    and fails no test — it just makes every entry look like a new
    beginning, and the graph fills with duplicates nobody can trace back to
    the day the search stopped working.
    """
    logger.info(
        "retrieval complete",
        extra={
            "episode_id": extraction.episode_id,
            "searched": len(targets),
            "resembling": sum(len(r.pass_a_candidates) for r in results),
            "anchored": anchors,
            "results_with_nothing": sum(
                1
                for r in results
                if not r.pass_a_candidates and not r.pass_b_candidates
            ),
            "search_text_fallback": hyde_fallback,
            "search_failed": embedding_failed,
            "duration_ms": duration_ms,
        },
    )


__all__ = ["retrieve"]
