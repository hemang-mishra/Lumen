"""
Taking one episode through reading, searching and deciding.

Split out from the runner because the runner's job is the whole entry — the
job record, the loop over episodes, keeping one failure from spreading — and
this is the part that concerns a single piece of writing.

Nothing here writes to the graph or the search index. It runs the three
thinking stages, records how each one went, and hands back what they
produced. Saving is somebody else's job, which is what makes it possible to
see everything an episode decided before any of it becomes permanent.

Two shortcuts live here, and both save real money. An episode too thin to be
worth comparing against the past skips searching and deciding entirely. An
episode that could not be read has nothing to search for, so it skips them
too.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from lumen.config import AppConfig
from lumen.graph.provider import GraphProvider
from lumen.operational.repositories import OperationalStore
from lumen.pipeline.extraction import extract
from lumen.pipeline.orchestration import bookkeeping, compose
from lumen.pipeline.reconciliation import reconcile
from lumen.pipeline.retrieval import retrieve
from lumen.providers.protocols import EmbeddingProvider, LLMProvider
from lumen.schemas.enums import PipelineStage
from lumen.schemas.pipeline import (
    ExtractionResult,
    MicroextractionInput,
    ReconciliationOutcome,
    RetrievalResult,
)
from lumen.vector.provider import VectorProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ThinkingResult:
    """
    What the three thinking stages produced for one episode.

    Attributes:
        extraction: Everything read out of the episode.
        retrievals: What the search brought back, one per searched finding.
        outcome: The decisions and the records they imply. None when the
            episode never reached that stage.
        stages_run: Which stages actually ran, so a deliberate skip is
            visible rather than looking like a stage that found nothing.
    """

    extraction: ExtractionResult
    retrievals: list[RetrievalResult] = field(default_factory=list)
    outcome: ReconciliationOutcome | None = None
    stages_run: tuple[PipelineStage, ...] = ()


def think(
    payload: MicroextractionInput,
    *,
    ops: OperationalStore,
    job_id: str,
    graph: GraphProvider,
    vectors: VectorProvider,
    embedder: EmbeddingProvider,
    lightweight: LLMProvider,
    thinking: LLMProvider,
    config: AppConfig,
) -> ThinkingResult:
    """
    Read one episode, search the past for it, and decide what it means.

    The graph is read here but never written to. Reading is what searching
    and deciding are for; writing waits until every decision has been made,
    so a run can be stopped and inspected at the point where nothing is
    permanent yet.
    """
    episode_id = payload.episode.episode_id
    extraction = _read(
        payload,
        ops=ops,
        job_id=job_id,
        lightweight=lightweight,
        thinking=thinking,
        config=config,
    )
    stages: list[PipelineStage] = [PipelineStage.STAGE_1_MICROEXTRACTION]

    if _has_nothing_to_decide(extraction):
        _skip(ops, job_id, episode_id, PipelineStage.STAGE_2_RETRIEVAL)
        _skip(ops, job_id, episode_id, PipelineStage.STAGE_3_RECONCILIATION)
        return ThinkingResult(extraction=extraction, stages_run=tuple(stages))

    retrievals = _search(
        payload,
        extraction,
        ops=ops,
        job_id=job_id,
        graph=graph,
        vectors=vectors,
        embedder=embedder,
        lightweight=lightweight,
        config=config,
    )
    stages.append(PipelineStage.STAGE_2_RETRIEVAL)

    outcome = _decide(
        payload,
        extraction,
        retrievals,
        ops=ops,
        job_id=job_id,
        graph=graph,
        lightweight=lightweight,
        thinking=thinking,
        config=config,
    )
    stages.append(PipelineStage.STAGE_3_RECONCILIATION)

    return ThinkingResult(
        extraction=extraction,
        retrievals=retrievals,
        outcome=outcome,
        stages_run=tuple(stages),
    )


def _has_nothing_to_decide(extraction: ExtractionResult) -> bool:
    """
    True when comparing this episode against the past would be wasted work.

    Either the reading failed, so there is nothing to compare, or everything
    found is the kind of light note that is stored as written and never
    turned into a standing claim.
    """
    return extraction.read_failed or compose.is_thin(extraction)


def _read(
    payload: MicroextractionInput,
    *,
    ops: OperationalStore,
    job_id: str,
    lightweight: LLMProvider,
    thinking: LLMProvider,
    config: AppConfig,
) -> ExtractionResult:
    """Pull the findings, events and reflections out of one episode."""
    with bookkeeping.stage_span(
        ops,
        job_id,
        PipelineStage.STAGE_1_MICROEXTRACTION,
        episode_id=payload.episode.episode_id,
        input_payload={"entry_class": payload.episode.entry_class.value},
    ) as span:
        result = extract(
            payload, lightweight=lightweight, thinking=thinking, config=config
        )
        span.model_used = result.extraction_model
        span.validation_passed = result.validation_passed
        span.retry_count = result.retry_count
        span.output_payload = {
            "observations": len(result.observations),
            "events": len(result.events),
            "sessions": len(result.sessions),
            "failed_observations": len(result.failed_observations),
            "read_failed": result.read_failed,
        }
        return result


def _search(
    payload: MicroextractionInput,
    extraction: ExtractionResult,
    *,
    ops: OperationalStore,
    job_id: str,
    graph: GraphProvider,
    vectors: VectorProvider,
    embedder: EmbeddingProvider,
    lightweight: LLMProvider,
    config: AppConfig,
) -> list[RetrievalResult]:
    """Find what the person has said before that relates to this episode."""
    with bookkeeping.stage_span(
        ops,
        job_id,
        PipelineStage.STAGE_2_RETRIEVAL,
        episode_id=payload.episode.episode_id,
    ) as span:
        results = retrieve(
            extraction,
            graph=graph,
            vectors=vectors,
            embedder=embedder,
            lightweight=lightweight,
            episode=payload,
            config=config,
        )
        span.output_payload = {
            "searched": len(results),
            "candidates": sum(
                len(r.pass_a_candidates) + len(r.pass_b_candidates) for r in results
            ),
            "search_failed": sum(1 for r in results if r.search_failed),
        }
        return results


def _decide(
    payload: MicroextractionInput,
    extraction: ExtractionResult,
    retrievals: list[RetrievalResult],
    *,
    ops: OperationalStore,
    job_id: str,
    graph: GraphProvider,
    lightweight: LLMProvider,
    thinking: LLMProvider,
    config: AppConfig,
) -> ReconciliationOutcome:
    """Work out what this episode means for what came before it."""
    with bookkeeping.stage_span(
        ops,
        job_id,
        PipelineStage.STAGE_3_RECONCILIATION,
        episode_id=payload.episode.episode_id,
    ) as span:
        outcome = reconcile(
            extraction,
            retrievals,
            graph=graph,
            lightweight=lightweight,
            thinking=thinking,
            episode=payload,
            config=config,
        )
        span.model_used = outcome.decision_model
        span.validation_passed = not outcome.decision_failed
        span.output_payload = {
            "decisions": len(outcome.results),
            "records_planned": len(outcome.write_plan.nodes),
            "links_planned": len(outcome.write_plan.edges),
            "waiting_for_a_person": len(outcome.escalations),
            "episode_status": outcome.episode_status.value,
        }
        return outcome


def _skip(
    ops: OperationalStore, job_id: str, episode_id: str, stage: PipelineStage
) -> None:
    """
    Note that a stage was deliberately not run.

    Recorded rather than left out, because a stage that never ran and a
    stage that ran and found nothing look the same afterwards, and only one
    of them is worth investigating.
    """
    with bookkeeping.stage_span(ops, job_id, stage, episode_id=episode_id) as span:
        span.skip()


__all__ = ["think", "ThinkingResult"]
