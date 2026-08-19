"""
Keeping the record of what a run did.

Everything in this module writes to the operational database and nothing
else. Gathering it here means the runner reads as a sequence of stages
rather than a sequence of database calls, and it means the answer to "what
did this run touch" comes from one place.

The most valuable thing written here is the write log. A graph that has gone
wrong is only fixable if it is explainable, and the write log is what turns
any node in the graph back into the conversation that produced it and the
decision that placed it there. It is also what a later repair reads to find
records that were saved but never made searchable.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime

from lumen.config import AppConfig
from lumen.operational.enums import (
    HitlItemStatus,
    JobStatus,
    StageStatus,
    WriteTarget,
)
from lumen.operational.repositories import OperationalStore
from lumen.operational.schemas import (
    CoreferenceRecord,
    HitlQueueItemRecord,
    PipelineJobRecord,
    StageMetrics,
)
from lumen.pipeline.orchestration.compose import coreference_map_id
from lumen.pipeline.orchestration.contracts import CommitReport
from lumen.review import capacity
from lumen.schemas.enums import PipelineStage
from lumen.schemas.pipeline import (
    CoreferenceMap,
    HitlEscalation,
    ReconciliationOutcome,
    SessionDecayEvent,
)

logger = logging.getLogger(__name__)


def open_job(
    event: SessionDecayEvent, *, ops: OperationalStore, config: AppConfig
) -> PipelineJobRecord:
    """
    Start tracking a run and mark it as under way.

    The configuration is snapshotted so a later re-run can tell whether it
    reproduced the original conditions or deliberately differed from them.
    Credentials cannot reach the snapshot: they are exposed as properties
    rather than stored fields, so converting the settings to plain data
    simply does not see them.
    """
    job = ops.jobs.create_job(
        session_id=event.session_id,
        user_id=config.user_id,
        config_snapshot=_config_snapshot(config),
    )
    return ops.jobs.transition(job.job_id, JobStatus.RUNNING)


def _config_snapshot(config: AppConfig) -> dict:
    """The settings a run used, as plain data."""
    return {
        "providers": asdict(config.providers),
        "pipeline": asdict(config.pipeline),
    }


@contextmanager
def stage_span(
    ops: OperationalStore,
    job_id: str,
    stage: PipelineStage,
    *,
    episode_id: str = "",
    input_payload: dict | None = None,
) -> Iterator["StageOutcome"]:
    """
    Record one stage running, however it turns out.

    The caller gets a small object to hang the result on. Whether it fills
    that in or raises, the stage is closed out either way — a stage that
    blew up still gets its row, with the error on it, because a missing row
    and a failed one mean very different things when reading a run back.
    """
    run = ops.jobs.start_stage(
        job_id, stage, input_payload=input_payload, episode_id=episode_id
    )
    outcome = StageOutcome()
    started = datetime.now(UTC)

    try:
        yield outcome
    except Exception as exc:
        ops.jobs.finish_stage(
            run.id,
            StageStatus.FAILED,
            metrics=_metrics(outcome, started),
            error_message=f"{type(exc).__name__}: {exc}",
        )
        raise

    ops.jobs.finish_stage(
        run.id,
        outcome.status,
        metrics=_metrics(outcome, started),
        output_payload=outcome.output_payload,
    )


class StageOutcome:
    """
    Somewhere for a stage to leave its result while it runs.

    Plain and mutable on purpose. The stage span has to close the row out
    whether the stage succeeded or raised, so it needs a handle it can read
    afterwards either way.
    """

    def __init__(self) -> None:
        self.status: StageStatus = StageStatus.COMPLETE
        self.model_used: str | None = None
        self.validation_passed: bool | None = None
        self.retry_count: int = 0
        self.output_payload: dict | None = None

    def skip(self) -> None:
        """Mark this stage as deliberately not run."""
        self.status = StageStatus.SKIPPED


def _metrics(outcome: StageOutcome, started: datetime) -> StageMetrics:
    """How long a stage took and how it behaved."""
    elapsed = datetime.now(UTC) - started
    return StageMetrics(
        duration_ms=max(int(elapsed.total_seconds() * 1000), 0),
        model_used=outcome.model_used,
        validation_passed=outcome.validation_passed,
        retry_count=outcome.retry_count,
    )


def save_coreference_map(
    coreference_map: CoreferenceMap,
    *,
    ops: OperationalStore,
    job: PipelineJobRecord,
    entry_id: str,
) -> str:
    """
    Store who the pronouns in this entry referred to.

    Every episode record in the graph points here by name. Without this the
    pointer would lead nowhere, and the question "why was this filed under
    Alex?" would have no answer at all.
    """
    record = CoreferenceRecord(
        id=coreference_map_id(entry_id),
        session_id=job.session_id,
        entry_id=entry_id,
        job_id=job.job_id,
        trace_id=job.trace_id,
        resolved_entities=[e.model_dump() for e in coreference_map.resolved_entities],
        ambiguous_refs=[r.model_dump() for r in coreference_map.ambiguous_refs],
    )
    return ops.coref.save(record)


def record_commit(
    report: CommitReport,
    *,
    ops: OperationalStore,
    job_id: str,
    episode_id: str,
) -> None:
    """
    Log every record, link and search entry one save produced.

    Written as one group so a run can never claim it created a record while
    losing the link that gives the record its meaning.
    """
    with ops.transaction():
        for node_id in report.nodes_written:
            ops.jobs.record_write(
                job_id=job_id,
                stage=PipelineStage.STAGE_4_GRAPH_WRITE,
                target=WriteTarget.GRAPH_NODE,
                node_id=node_id,
                episode_id=episode_id,
            )
        for table, from_id, to_id in report.edges_written:
            ops.jobs.record_write(
                job_id=job_id,
                stage=PipelineStage.STAGE_4_GRAPH_WRITE,
                target=WriteTarget.GRAPH_EDGE,
                edge_type=table,
                from_id=from_id,
                to_id=to_id,
                episode_id=episode_id,
            )
        for node_id in report.vectors_written:
            ops.jobs.record_write(
                job_id=job_id,
                stage=PipelineStage.STAGE_4_GRAPH_WRITE,
                target=WriteTarget.VECTOR,
                node_id=node_id,
                episode_id=episode_id,
            )


def queue_escalations(
    outcome: ReconciliationOutcome,
    *,
    ops: OperationalStore,
    job: PipelineJobRecord,
    config: AppConfig | None = None,
) -> int:
    """
    Put everything the system could not settle in front of the person.

    Runs after the records are saved, never before, because each queued item
    points at the note of the decision it is waiting on and that note has to
    exist first.

    An item already in the queue is left alone. Re-running an entry produces
    the same undecided items, and asking the person the same question twice
    is worse than not asking at all.

    Past the queue's ceiling, items are parked rather than asked — and parked
    is not decided. They wait outside until answering something else makes
    room. Nothing is ever guessed to keep the queue short.

    What each item was about to write is saved alongside it, because that
    working is the only thing that makes the question answerable later.

    Returns how many were newly added.
    """
    decisions = {result.audit_node_id: result for result in outcome.results}
    cap = (config or AppConfig()).operational.hitl_queue_cap
    asked = ops.hitl.count_asked(job.user_id)
    added = 0

    for escalation in outcome.escalations:
        if ops.hitl.get_by_audit_node(escalation.audit_node_id) is not None:
            continue

        decision = decisions.get(escalation.audit_node_id)
        status = capacity.entry_status(pending=asked, cap=cap)
        runner_up = _runner_up_of(escalation)

        ops.hitl.enqueue(
            HitlQueueItemRecord(
                id=f"hitl_{escalation.audit_node_id}",
                user_id=job.user_id,
                audit_node_id=escalation.audit_node_id,
                entry_type=escalation.entry_type,
                signal_strength=escalation.signal_strength,
                status=status,
                trace_id=job.trace_id,
                job_id=job.job_id,
                observation_id=escalation.source_node_id,
                episode_id=escalation.episode_id,
                recommended_action=decision.action if decision else None,
                candidate_a_node_id=decision.target_node_id if decision else None,
                confidence_a=decision.confidence if decision else None,
                candidate_b_node_id=runner_up.target_node_id if runner_up else None,
                confidence_b=runner_up.confidence if runner_up else None,
                context_summary=escalation.summary,
            )
        )
        if escalation.proposal is not None:
            ops.hitl.save_proposal(
                escalation.audit_node_id,
                escalation.proposal.model_dump_json(),
            )
        else:
            logger.warning(
                "an undecided item was queued with nothing recorded to carry out",
                extra={"audit_node_id": escalation.audit_node_id},
            )

        if status is HitlItemStatus.PENDING_HITL:
            asked += 1
        added += 1

    if added:
        logger.info(
            "undecided items queued",
            extra={"added": added, "asked": asked, "cap": cap},
        )
    return added


def _runner_up_of(escalation: HitlEscalation):
    """The second reading a tie offered, where one was kept."""
    proposal = escalation.proposal
    return proposal.runner_up if proposal is not None else None


def close_job(
    *,
    ops: OperationalStore,
    job_id: str,
    failed_episodes: int,
    unindexed: int,
) -> JobStatus:
    """
    Finish a run, reporting success only when it earned it.

    A run counts as failed if any episode was lost, or if anything reached
    the graph without becoming searchable. The second one matters as much as
    the first: those records are real and correct and invisible, and a run
    that reports success would leave nobody with a reason to look.
    """
    if failed_episodes or unindexed:
        ops.jobs.transition(
            job_id,
            JobStatus.FAILED,
            error_type="INCOMPLETE_RUN",
            error_message=(
                f"{failed_episodes} episode(s) failed; "
                f"{unindexed} saved record(s) are not searchable"
            ),
        )
        return JobStatus.FAILED

    ops.jobs.transition(job_id, JobStatus.COMPLETE)
    return JobStatus.COMPLETE


__all__ = [
    "open_job",
    "stage_span",
    "StageOutcome",
    "save_coreference_map",
    "record_commit",
    "queue_escalations",
    "close_job",
]
