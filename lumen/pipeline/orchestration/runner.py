"""
Running one whole session through the pipeline and saving what it produced.

Everything before this goal was reversible. Cleaning, reading, searching and
deciding can all be run again; a mistake costs a re-run. This is where it
stops being free — once an episode is saved, it is in the person's history.

Three rules shape the whole file.

Episodes are independent. One entry usually holds several unrelated topics,
each read and decided on its own, and losing three good ones because a
fourth got a bad reply from a model is the worse outcome. A failure is
caught, recorded against that episode, and does not spread.

An episode saves whole or not at all. Every record and link it produces goes
in one transaction, so there is no state where an entry looks complete but
is missing half of what the person said.

An episode already in the graph is left completely alone — not just its
saving, but its reading and deciding too. Deciding it again would compare
the entry against the graph's copy of its own previous conclusions and
cheerfully record it as a repeat of itself.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from lumen.config import AppConfig
from lumen.graph.provider import GraphProvider
from lumen.observability.trace import bind_trace
from lumen.operational.enums import BufferStatus
from lumen.operational.repositories import OperationalStore
from lumen.operational.schemas import PipelineJobRecord
from lumen.pipeline.orchestration import bookkeeping, commit, compose, embed, episode
from lumen.pipeline.orchestration.contracts import (
    CommitReport,
    IndexEntry,
    IndexWriteFailed,
)
from lumen.pipeline.preprocessing import preprocess
from lumen.providers.protocols import EmbeddingProvider, LLMProvider
from lumen.schemas.enums import (
    EpisodeRunStatus,
    PipelineStage,
    QualityGateDecision,
    ReconciliationStatus,
)
from lumen.schemas.pipeline import (
    EpisodeOutcome,
    GraphWritePlan,
    MicroextractionInput,
    PreprocessedEpisode,
    PreprocessingResult,
    RunReport,
    SessionDecayEvent,
)
from lumen.vector.provider import VectorProvider

logger = logging.getLogger(__name__)


def run_pipeline(
    event: SessionDecayEvent,
    *,
    graph: GraphProvider,
    vectors: VectorProvider,
    embedder: EmbeddingProvider,
    lightweight: LLMProvider,
    thinking: LLMProvider,
    ops: OperationalStore,
    config: AppConfig | None = None,
) -> RunReport:
    """
    Take one finished session all the way from raw messages to saved history.

    Every store and every model is handed in rather than reached for, so the
    same function runs against real databases in production and against
    temporary ones in a test, and reading the signature tells you everything
    it can touch.

    Returns a report of what happened to each episode. The run is only
    called a success when every episode was saved and every saved record can
    be found again.
    """
    settings = config or AppConfig()
    started = time.perf_counter()

    with bind_trace():
        job = bookkeeping.open_job(event, ops=ops, config=settings)
        preprocessing = _clean(
            event,
            ops=ops,
            job=job,
            lightweight=lightweight,
            thinking=thinking,
            config=settings,
        )
        bookkeeping.save_coreference_map(
            preprocessing.coreference_map,
            ops=ops,
            job=job,
            entry_id=event.session_id,
        )

        outcomes = _process_episodes(
            event,
            preprocessing,
            job=job,
            graph=graph,
            vectors=vectors,
            embedder=embedder,
            lightweight=lightweight,
            thinking=thinking,
            ops=ops,
            config=settings,
        )

        _settle_buffer(event, preprocessing, ops=ops)
        status = bookkeeping.close_job(
            ops=ops,
            job_id=job.job_id,
            failed_episodes=sum(
                1 for o in outcomes if o.status is EpisodeRunStatus.FAILED
            ),
            unindexed=sum(len(o.unindexed_node_ids) for o in outcomes),
        )

        report = RunReport(
            job_id=job.job_id,
            session_id=event.session_id,
            quality_gate_decision=preprocessing.quality_gate_decision,
            episodes=outcomes,
            job_status=status.value,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        _log_run(report)
        return report


# ---------------------------------------------------------------------------
# The whole entry
# ---------------------------------------------------------------------------


def _clean(
    event: SessionDecayEvent,
    *,
    ops: OperationalStore,
    job: PipelineJobRecord,
    lightweight: LLMProvider,
    thinking: LLMProvider,
    config: AppConfig,
) -> PreprocessingResult:
    """Turn the raw session into clean, separated episodes."""
    with bookkeeping.stage_span(
        ops,
        job.job_id,
        PipelineStage.STAGE_0_PREPROCESSING,
        input_payload={
            "message_count": event.message_count,
            "source_modality": event.source_modality.value,
        },
    ) as span:
        result = preprocess(
            event,
            lightweight=lightweight,
            thinking=thinking,
            config=config,
        )
        span.output_payload = {
            "episodes": len(result.episodes),
            "decision": result.quality_gate_decision.value,
            "detected_languages": list(result.detected_languages),
        }
        return result


def _process_episodes(
    event: SessionDecayEvent,
    preprocessing: PreprocessingResult,
    *,
    job: PipelineJobRecord,
    graph: GraphProvider,
    vectors: VectorProvider,
    embedder: EmbeddingProvider,
    lightweight: LLMProvider,
    thinking: LLMProvider,
    ops: OperationalStore,
    config: AppConfig,
) -> list[EpisodeOutcome]:
    """
    Run every episode of the entry, one at a time, keeping failures apart.

    The identifier carried forward is the last episode that actually saved,
    not the last one attempted. Episodes are chained together in the order
    they were written, and a chain pointing at an episode whose save was
    undone would be a link to a record that does not exist.
    """
    if preprocessing.quality_gate_decision is QualityGateDecision.DISCARD:
        logger.info(
            "entry held nothing worth extracting",
            extra={"session_id": event.session_id},
        )
        return []

    outcomes: list[EpisodeOutcome] = []
    last_saved: str | None = None

    for piece in preprocessing.episodes:
        outcome = _run_one_episode(
            piece,
            event,
            preprocessing,
            job=job,
            previous_episode_id=last_saved,
            graph=graph,
            vectors=vectors,
            embedder=embedder,
            lightweight=lightweight,
            thinking=thinking,
            ops=ops,
            config=config,
        )
        outcomes.append(outcome)
        if outcome.status in _SAVED_STATUSES:
            last_saved = piece.episode_id

    return outcomes


# Statuses that mean the episode's record is in the graph and can safely be
# pointed at by the next episode in the entry.
_SAVED_STATUSES = frozenset(
    {
        EpisodeRunStatus.COMPLETE,
        EpisodeRunStatus.SUSPENDED,
        EpisodeRunStatus.SKIPPED,
    }
)


# ---------------------------------------------------------------------------
# One episode
# ---------------------------------------------------------------------------


def _run_one_episode(
    piece: PreprocessedEpisode,
    event: SessionDecayEvent,
    preprocessing: PreprocessingResult,
    *,
    job: PipelineJobRecord,
    previous_episode_id: str | None,
    graph: GraphProvider,
    vectors: VectorProvider,
    embedder: EmbeddingProvider,
    lightweight: LLMProvider,
    thinking: LLMProvider,
    ops: OperationalStore,
    config: AppConfig,
) -> EpisodeOutcome:
    """
    Read, decide and save one episode, and never let it take down the rest.

    The broad catch here is deliberate and is the only one in the codebase.
    Anything at all can go wrong inside a single episode — a model times
    out, a reply is nonsense, a disk fills — and the right answer is always
    the same: record what happened to this episode and carry on with the
    others. Letting it propagate would throw away every topic in the entry
    over one of them.
    """
    saved = graph.get_node(piece.episode_id)
    if saved is not None:
        _warn_if_the_words_changed(saved, piece)
        logger.info(
            "episode is already saved; leaving it untouched",
            extra={"episode_id": piece.episode_id},
        )
        return EpisodeOutcome(
            episode_id=piece.episode_id,
            status=EpisodeRunStatus.SKIPPED,
            entry_class=piece.entry_class,
        )

    payload = _payload_for(piece, event, preprocessing)

    try:
        return _think_and_save(
            payload,
            preprocessing,
            job=job,
            previous_episode_id=previous_episode_id,
            graph=graph,
            vectors=vectors,
            embedder=embedder,
            lightweight=lightweight,
            thinking=thinking,
            ops=ops,
            config=config,
        )
    except Exception as exc:
        logger.exception(
            "episode failed; the rest of the entry continues",
            extra={"episode_id": piece.episode_id},
        )
        return EpisodeOutcome(
            episode_id=piece.episode_id,
            status=EpisodeRunStatus.FAILED,
            entry_class=piece.entry_class,
            error=f"{type(exc).__name__}: {exc}",
        )


def _think_and_save(
    payload: MicroextractionInput,
    preprocessing: PreprocessingResult,
    *,
    job: PipelineJobRecord,
    previous_episode_id: str | None,
    graph: GraphProvider,
    vectors: VectorProvider,
    embedder: EmbeddingProvider,
    lightweight: LLMProvider,
    thinking: LLMProvider,
    ops: OperationalStore,
    config: AppConfig,
) -> EpisodeOutcome:
    """
    The ordinary path: work out what the episode means, then save it.

    The order of the last three steps is the important part. The searchable
    form of every record is worked out first, while nothing has been
    written — so a failure there costs nothing and the episode can simply be
    tried again. Only then is anything saved.
    """
    episode_id = payload.episode.episode_id
    result = episode.think(
        payload,
        ops=ops,
        job_id=job.job_id,
        graph=graph,
        vectors=vectors,
        embedder=embedder,
        lightweight=lightweight,
        thinking=thinking,
        config=config,
    )

    status = compose.status_for(result.extraction, result.outcome)
    plan = compose.compose(
        payload,
        result.extraction,
        result.outcome,
        preprocessing=preprocessing,
        reconciliation_status=status,
        previous_episode_id=previous_episode_id,
        at=payload.occurred_at,
    )

    entries = embed.prepare_index(plan, embedder=embedder)

    with bookkeeping.stage_span(
        ops,
        job.job_id,
        PipelineStage.STAGE_4_GRAPH_WRITE,
        episode_id=episode_id,
        input_payload={"records": len(plan.nodes), "links": len(plan.edges)},
    ) as span:
        report = _save(plan, entries, graph=graph, vectors=vectors)
        span.output_payload = {
            "records_written": len(report.nodes_written),
            "links_written": len(report.edges_written),
            "records_indexed": len(report.vectors_written),
            "records_not_indexed": len(report.unindexed_node_ids),
        }
        if report.unindexed_node_ids:
            span.validation_passed = False

    bookkeeping.record_commit(
        report, ops=ops, job_id=job.job_id, episode_id=episode_id
    )

    escalated = 0
    if result.outcome is not None:
        escalated = bookkeeping.queue_escalations(
            result.outcome, ops=ops, job=job, config=config
        )

    return EpisodeOutcome(
        episode_id=episode_id,
        status=_outcome_status(status),
        entry_class=payload.episode.entry_class,
        nodes_written=len(report.nodes_written),
        edges_written=len(report.edges_written),
        vectors_written=len(report.vectors_written),
        escalations=escalated,
        unindexed_node_ids=list(report.unindexed_node_ids),
        stages_run=[*result.stages_run, PipelineStage.STAGE_4_GRAPH_WRITE],
    )


def _save(
    plan: GraphWritePlan,
    entries: list[IndexEntry],
    *,
    graph: GraphProvider,
    vectors: VectorProvider,
) -> CommitReport:
    """
    Save the plan, treating a failed search entry as damage rather than loss.

    A record that reached the graph but not the search index is still a real
    and correct record — it simply cannot be found by meaning yet. Reporting
    that as the episode failing would be wrong, and re-running the episode
    would be the wrong repair. The report is recovered from the error so
    everything that did land is still written to the run log, which is what
    a later repair reads.
    """
    try:
        return commit.commit(plan, entries, graph=graph, vectors=vectors)
    except IndexWriteFailed as failure:
        logger.error(
            "records were saved but cannot be found by meaning",
            extra={"node_ids": failure.missing},
        )
        return failure.report


def _outcome_status(status: ReconciliationStatus) -> EpisodeRunStatus:
    """Read a saved episode's own status as a run outcome."""
    if status is ReconciliationStatus.COMPLETE:
        return EpisodeRunStatus.COMPLETE
    return EpisodeRunStatus.SUSPENDED


# ---------------------------------------------------------------------------
# Odds and ends
# ---------------------------------------------------------------------------


def _warn_if_the_words_changed(
    saved: dict, piece: PreprocessedEpisode
) -> None:
    """
    Say something when a saved episode no longer matches the words it came from.

    An episode is stored under the day it happened on, not under what it
    says, so a day that gets run again is recognised and skipped whatever its
    text now holds. That is right for an ordinary repeat and quietly wrong
    when the words really did change — the stored records were drawn from
    text that no longer exists anywhere, and nothing would ever say so.

    Live chat is not allowed to reach this state: a day is frozen once it has
    been processed. A re-upload or a corrected import can still get here by
    another road, and a silent disagreement between what somebody wrote and
    what the graph believes is the worst failure this system has.
    """
    stored_hash = str(saved.get("raw_text_hash") or "")
    if not stored_hash or stored_hash == piece.raw_text_hash:
        return

    logger.warning(
        "this episode is already saved but its words have changed since; the "
        "stored records were drawn from text that is no longer here, and they "
        "are being left exactly as they are",
        extra={
            "episode_id": piece.episode_id,
            "stored_hash": stored_hash,
            "incoming_hash": piece.raw_text_hash,
        },
    )


def _payload_for(
    piece: PreprocessedEpisode,
    event: SessionDecayEvent,
    preprocessing: PreprocessingResult,
) -> MicroextractionInput:
    """
    Package one episode with everything reading it needs.

    The entry is identified by the session it came from: a session is one
    person writing on one day under one label, which is exactly what an
    entry is.
    """
    return MicroextractionInput(
        episode=piece,
        coreference_map=preprocessing.coreference_map,
        entry_id=event.session_id,
        event_date=event.event_date,
        occurred_at=_moment_of(event),
        source_modality=event.source_modality,
        session_label=event.session_label,
        co_created_spans=list(preprocessing.co_created_spans),
    )


def _moment_of(event: SessionDecayEvent) -> datetime:
    """
    The moment everything from this entry is stamped with.

    The day the person wrote, not the day the pipeline happened to get to
    it. An entry processed three days late belongs to the day it describes,
    and a graph whose dates match somebody's memory is a graph they can
    actually read.
    """
    return datetime.combine(
        event.event_date, event.triggered_at.timetz()
    )


def _settle_buffer(
    event: SessionDecayEvent,
    preprocessing: PreprocessingResult,
    *,
    ops: OperationalStore,
) -> None:
    """Mark the conversation as dealt with, one way or the other."""
    status = (
        BufferStatus.DISCARDED
        if preprocessing.quality_gate_decision is QualityGateDecision.DISCARD
        else BufferStatus.PROCESSED
    )
    try:
        ops.buffers.mark_status(event.session_id, status)
    except Exception:
        # The work is done and saved either way. A buffer left marked as
        # dispatched costs a warning; failing the whole run over it would
        # throw away an entry that is already in the graph.
        logger.warning(
            "could not update the conversation's status",
            extra={"session_id": event.session_id, "status": status.value},
        )


def _log_run(report: RunReport) -> None:
    """
    One line saying what the run came to.

    The counts are the only warning a run gives. Nothing here fails loudly:
    an entry that quietly saved two of five topics, or saved everything and
    indexed none of it, looks like a working system from every angle except
    this line.
    """
    logger.info(
        "pipeline run complete",
        extra={
            "job_id": report.job_id,
            "session_id": report.session_id,
            "gate": report.quality_gate_decision.value,
            "episodes": len(report.episodes),
            "saved": sum(
                1
                for e in report.episodes
                if e.status
                in (EpisodeRunStatus.COMPLETE, EpisodeRunStatus.SUSPENDED)
            ),
            "skipped": sum(
                1 for e in report.episodes if e.status is EpisodeRunStatus.SKIPPED
            ),
            "failed": sum(
                1 for e in report.episodes if e.status is EpisodeRunStatus.FAILED
            ),
            "records": report.nodes_written,
            "searchable": report.vectors_written,
            "not_searchable": len(report.unindexed_node_ids),
            "waiting_for_a_person": sum(e.escalations for e in report.episodes),
            "job_status": report.job_status,
            "duration_ms": report.duration_ms,
        },
    )


__all__ = ["run_pipeline"]
