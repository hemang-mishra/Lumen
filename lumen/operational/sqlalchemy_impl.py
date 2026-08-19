"""
The SQLAlchemy implementation of the operational store.

This is the only module that knows how operational data is actually stored.
Everything else works through the protocols, so replacing SQLite with
PostgreSQL — or with something else entirely — touches nothing outside here.

Each repository is small and covers one area. They share a session manager
that lets several of them take part in a single transaction when a caller asks
for one, and otherwise gives each operation its own.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, delete, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from lumen.config import AppConfig, OperationalConfig
from lumen.observability.trace import get_trace_id, new_trace_id
from lumen.operational import models
from lumen.operational.engine import create_ops_engine, create_session_factory
from lumen.operational.enums import (
    ALLOWED_JOB_TRANSITIONS,
    HITL_ENTRY_TYPE_RANK,
    OPEN_HITL_STATUSES,
    TERMINAL_IMPORT_STATUSES,
    BufferSource,
    BufferStatus,
    ErasureStatus,
    HitlEntryType,
    HitlItemStatus,
    ImportStatus,
    JobStatus,
    PipelineStage,
    StageStatus,
    WriteTarget,
)
from lumen.operational.repositories import (
    IllegalStateTransitionError,
    RecordNotFoundError,
    UnknownSettingKeyError,
)
from lumen.operational.schemas import (
    BufferMessageRecord,
    CoreferenceRecord,
    ErasureAuditRecord,
    HitlQueueItemRecord,
    ImportBatch,
    ImportRecord,
    PipelineJobRecord,
    PipelineTrace,
    SessionBufferRecord,
    StageMetrics,
    StageRunRecord,
    StoredErasureAudit,
    UserSettingRecord,
    WriteLogEntry,
)
from lumen.schemas.enums import (
    HitlResolutionChoice,
    ReconciliationAction,
    SignalStrength,
    SourceModality,
)
from lumen.schemas.pipeline import BufferMessage, SessionDecayEvent

logger = logging.getLogger(__name__)


# The shape saved proposals are written in. Stored beside each one so a
# later change to that shape can be migrated rather than guessed at.
_PROPOSAL_SCHEMA_VERSION = 1


# How strongly a signal counts when ordering the review queue. Higher wins.
_SIGNAL_RANK: dict[SignalStrength, int] = {
    SignalStrength.CRITICAL: 3,
    SignalStrength.HIGH: 2,
    SignalStrength.STANDARD: 1,
}


def _known_setting_keys() -> frozenset[str]:
    """
    Every setting the application actually reads.

    Deliberately absent: anything naming a model provider. Which model backs a
    role is a deployment property the maintainer sets in the environment, read
    once at startup. It is not a user preference, so it does not belong in a
    table the user can write to.
    """
    return frozenset({
        "pipeline.session_decay_minutes",
        "hitl.queue_cap",
        "logging.level",
    })


KNOWN_SETTING_KEYS: frozenset[str] = _known_setting_keys()


def _utcnow() -> datetime:
    """Current time, timezone-aware and in UTC."""
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    """
    Make sure a time read from the database carries its time zone.

    SQLite forgets time zones, so values come back naive. They were written as
    UTC, so that is what gets attached again.
    """
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _hash_user_id(user_id: str) -> str:
    """Turn a user id into a one-way hash for the erasure log."""
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()


class _SessionManager:
    """
    Hands out database sessions.

    When a caller has opened a transaction, every repository joins that one
    session so their writes commit or roll back together. Otherwise each
    operation gets its own short-lived session that commits on its own.

    The open transaction is tracked per thread, so two background workers
    running side by side never end up sharing a session.
    """

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory
        self._local = threading.local()

    @property
    def _ambient(self) -> Session | None:
        return getattr(self._local, "session", None)

    @contextmanager
    def session(self) -> Generator[Session]:
        """Get a session for one piece of work."""
        existing = self._ambient
        if existing is not None:
            # Inside a transaction: use it and let the owner decide the outcome.
            yield existing
            return

        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def transaction(self) -> Generator[None]:
        """Group everything inside the block into one all-or-nothing write."""
        if self._ambient is not None:
            # Already in a transaction; joining it keeps the outermost block
            # in charge of committing.
            yield
            return

        session = self._factory()
        self._local.session = session
        try:
            yield
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            self._local.session = None
            session.close()


class SqlAlchemySessionBufferRepository:
    """Stores conversations while they wait to be processed."""

    def __init__(self, sessions: _SessionManager) -> None:
        self._sessions = sessions

    def create_buffer(self, record: SessionBufferRecord) -> str:
        with self._sessions.session() as db:
            now = _utcnow()
            db.add(
                models.SessionBuffer(
                    session_id=record.session_id,
                    user_id=record.user_id,
                    event_date=record.event_date,
                    session_label=record.session_label,
                    status=record.status.value,
                    source=record.source.value,
                    message_count=record.message_count,
                    created_at=record.created_at or now,
                    last_activity_at=record.last_activity_at or now,
                    decayed_at=record.decayed_at,
                    ingested_at=record.ingested_at,
                )
            )
            db.flush()
        logger.debug("created session buffer", extra={"session_id": record.session_id})
        return record.session_id

    def find_or_create(
        self,
        user_id: str,
        event_date: date,
        session_label: str = "",
        source: BufferSource = BufferSource.NATIVE_CHAT,
    ) -> SessionBufferRecord:
        with self._sessions.session() as db:
            row = db.scalar(
                select(models.SessionBuffer).where(
                    models.SessionBuffer.user_id == user_id,
                    models.SessionBuffer.event_date == event_date,
                    models.SessionBuffer.session_label == session_label,
                )
            )
            if row is None:
                now = _utcnow()
                row = models.SessionBuffer(
                    session_id=_new_session_id(event_date, session_label),
                    user_id=user_id,
                    event_date=event_date,
                    session_label=session_label,
                    status=BufferStatus.OPEN.value,
                    source=source.value,
                    message_count=0,
                    created_at=now,
                    last_activity_at=now,
                    ingested_at=now,
                )
                db.add(row)
                db.flush()
                logger.info(
                    "opened session buffer",
                    extra={"session_id": row.session_id, "event_date": str(event_date)},
                )
            return _to_buffer_record(row)

    def append_message(self, session_id: str, message: BufferMessageRecord) -> None:
        """
        Add a message to the end of the conversation as it currently reads.

        The reply link is filled in from wherever the conversation currently
        ends, unless the caller named one. That makes the ordinary case —
        somebody saying the next thing — correct without any caller having to
        think about branches, and leaves the explicit form for editing.
        """
        with self._sessions.session() as db:
            self._write_message(db, session_id, message, parent=message.parent_message_id)

    def branch_from(
        self,
        session_id: str,
        parent_message_id: str | None,
        message: BufferMessageRecord,
    ) -> BufferMessageRecord:
        """Add a message beside an existing one and read from it instead."""
        with self._sessions.session() as db:
            written = self._write_message(
                db, session_id, message, parent=parent_message_id, explicit_parent=True
            )
            return _to_message_record(written)

    def _write_message(
        self,
        db: Session,
        session_id: str,
        message: BufferMessageRecord,
        *,
        parent: str | None,
        explicit_parent: bool = False,
    ) -> models.BufferMessage:
        """
        Store one message and move the live end of the conversation to it.

        The buffer's counters are kept current here rather than recomputed
        later, because the decay check reads them and counting messages on
        every check would make it cost more the longer somebody talks.
        """
        buffer = self._require_buffer(db, session_id)
        row = models.BufferMessage(
            message_id=message.message_id,
            session_id=session_id,
            seq=message.seq,
            parent_message_id=parent if (parent or explicit_parent) else buffer.active_message_id,
            role=message.role,
            content=message.content,
            timestamp=message.timestamp,
            event_date=message.event_date,
            dialogue_act=message.dialogue_act.value if message.dialogue_act else None,
            co_created_marker=message.co_created_marker,
            modality=message.modality,
        )
        db.add(row)
        buffer.message_count += 1
        buffer.last_activity_at = _utcnow()
        buffer.active_message_id = message.message_id
        db.flush()
        return row

    def set_active(self, session_id: str, message_id: str) -> None:
        with self._sessions.session() as db:
            buffer = self._require_buffer(db, session_id)
            known = {row.message_id for row in self._all_messages(db, session_id)}
            if message_id not in known:
                raise RecordNotFoundError(
                    f"no message {message_id!r} in conversation {session_id!r}"
                )
            buffer.active_message_id = message_id
            db.flush()

    def recent_buffers(
        self,
        user_id: str,
        *,
        before: date,
        limit: int,
        session_label: str = "",
        lookback_days: int = 14,
    ) -> list[SessionBufferRecord]:
        wanted = max(int(limit), 0)
        if wanted == 0:
            return []

        earliest = before - timedelta(days=max(int(lookback_days), 0))
        with self._sessions.session() as db:
            rows = db.scalars(
                select(models.SessionBuffer)
                .where(
                    models.SessionBuffer.user_id == user_id,
                    models.SessionBuffer.session_label == session_label,
                    models.SessionBuffer.event_date < before,
                    models.SessionBuffer.event_date >= earliest,
                    models.SessionBuffer.message_count > 0,
                )
                .order_by(models.SessionBuffer.event_date.desc())
                .limit(wanted)
            )
            return [_to_buffer_record(row) for row in rows]

    def save_summary(self, session_id: str, summary: str, through_seq: int) -> None:
        with self._sessions.session() as db:
            buffer = self._require_buffer(db, session_id)
            buffer.rolling_summary = summary
            buffer.summary_through_seq = max(int(through_seq), 0)
            db.flush()

    def active_thread(self, session_id: str) -> list[BufferMessageRecord]:
        with self._sessions.session() as db:
            buffer = self._require_buffer(db, session_id)
            rows = self._all_messages(db, session_id)
            return [
                _to_message_record(row)
                for row in _walk_back(rows, buffer.active_message_id)
            ]

    def _all_messages(self, db: Session, session_id: str) -> list[models.BufferMessage]:
        """Every message of a conversation, in the order they arrived."""
        return list(
            db.scalars(
                select(models.BufferMessage)
                .where(models.BufferMessage.session_id == session_id)
                .order_by(models.BufferMessage.seq)
            ).all()
        )

    def get_buffer(self, session_id: str) -> SessionBufferRecord | None:
        with self._sessions.session() as db:
            row = db.get(models.SessionBuffer, session_id)
            return _to_buffer_record(row) if row else None

    def get_messages(self, session_id: str) -> list[BufferMessageRecord]:
        with self._sessions.session() as db:
            rows = db.scalars(
                select(models.BufferMessage)
                .where(models.BufferMessage.session_id == session_id)
                .order_by(models.BufferMessage.seq)
            ).all()
            return [_to_message_record(row) for row in rows]

    def find_decayed(self, cutoff: datetime, limit: int = 50) -> list[SessionBufferRecord]:
        with self._sessions.session() as db:
            rows = db.scalars(
                select(models.SessionBuffer)
                .where(
                    models.SessionBuffer.status == BufferStatus.OPEN.value,
                    models.SessionBuffer.last_activity_at < cutoff,
                    models.SessionBuffer.message_count > 0,
                )
                .order_by(models.SessionBuffer.last_activity_at)
                .limit(limit)
            ).all()
            return [_to_buffer_record(row) for row in rows]

    def build_decay_event(self, session_id: str) -> SessionDecayEvent:
        """
        Turn a conversation into the object the pipeline consumes.

        The **active thread**, not every message. A message somebody edited
        away was said, but it is not what they settled on, and letting
        abandoned branches become permanent history would record arguments
        they took back as things they believe.
        """
        with self._sessions.session() as db:
            buffer = self._require_buffer(db, session_id)
            messages = _walk_back(
                self._all_messages(db, session_id), buffer.active_message_id
            )

            return SessionDecayEvent(
                session_id=buffer.session_id,
                user_id=buffer.user_id,
                event_date=buffer.event_date,
                session_label=buffer.session_label,
                source_modality=_source_modality(buffer.source),
                message_count=len(messages),
                raw_buffer=[_to_buffer_message(row) for row in messages],
                triggered_at=_aware(buffer.decayed_at) or _utcnow(),
            )

    def mark_status(self, session_id: str, status: BufferStatus) -> SessionBufferRecord:
        with self._sessions.session() as db:
            buffer = self._require_buffer(db, session_id)
            buffer.status = status.value
            if status == BufferStatus.DECAYED and buffer.decayed_at is None:
                buffer.decayed_at = _utcnow()
            db.flush()
            return _to_buffer_record(buffer)

    def _require_buffer(self, db: Session, session_id: str) -> models.SessionBuffer:
        row = db.get(models.SessionBuffer, session_id)
        if row is None:
            raise RecordNotFoundError(f"no session buffer with id {session_id!r}")
        return row


class SqlAlchemyPipelineJobRepository:
    """Tracks pipeline runs, their stages, and everything they wrote."""

    def __init__(self, sessions: _SessionManager) -> None:
        self._sessions = sessions

    def create_job(
        self,
        session_id: str,
        user_id: str,
        config_snapshot: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> PipelineJobRecord:
        resolved_trace = trace_id or get_trace_id() or new_trace_id()
        job_id = f"job_{uuid.uuid4().hex[:16]}"

        with self._sessions.session() as db:
            row = models.PipelineJob(
                job_id=job_id,
                trace_id=resolved_trace,
                session_id=session_id,
                user_id=user_id,
                status=JobStatus.PENDING.value,
                created_at=_utcnow(),
                config_snapshot=config_snapshot,
            )
            db.add(row)
            db.flush()
            record = _to_job_record(row)

        logger.info("pipeline job created", extra={"job_id": job_id, "session_id": session_id})
        return record

    def get_job(self, job_id: str) -> PipelineJobRecord | None:
        with self._sessions.session() as db:
            row = db.get(models.PipelineJob, job_id)
            return _to_job_record(row) if row else None

    def transition(
        self,
        job_id: str,
        to_status: JobStatus,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> PipelineJobRecord:
        with self._sessions.session() as db:
            row = self._require_job(db, job_id)
            current = JobStatus(row.status)

            if to_status not in ALLOWED_JOB_TRANSITIONS[current]:
                raise IllegalStateTransitionError(
                    f"job {job_id} cannot move from {current.value} to {to_status.value}"
                )

            # A failed job going back to running is a re-run, and that is worth
            # counting — three quiet retries look very different from one.
            if current == JobStatus.FAILED and to_status == JobStatus.RUNNING:
                row.retry_count += 1
                row.error_type = None
                row.error_message = None

            row.status = to_status.value

            if to_status == JobStatus.RUNNING and row.started_at is None:
                row.started_at = _utcnow()
            if to_status in (JobStatus.COMPLETE, JobStatus.FAILED, JobStatus.CANCELLED):
                row.finished_at = _utcnow()
            if to_status == JobStatus.FAILED:
                row.error_type = error_type
                row.error_message = error_message

            db.flush()
            record = _to_job_record(row)

        logger.info(
            "pipeline job transitioned",
            extra={"job_id": job_id, "to_status": to_status.value},
        )
        return record

    def start_stage(
        self,
        job_id: str,
        stage: PipelineStage,
        input_payload: dict[str, Any] | None = None,
        attempt: int | None = None,
        episode_id: str = "",
    ) -> StageRunRecord:
        with self._sessions.session() as db:
            job = self._require_job(db, job_id)
            resolved_attempt = (
                attempt
                if attempt is not None
                else self._next_attempt(db, job_id, stage, episode_id)
            )

            row = models.PipelineStageRun(
                job_id=job_id,
                trace_id=job.trace_id,
                episode_id=episode_id,
                stage=stage.value,
                attempt=resolved_attempt,
                status=StageStatus.RUNNING.value,
                started_at=_utcnow(),
                input_payload=input_payload,
            )
            db.add(row)

            # Recording the stage on the job keeps "where is this run right
            # now" answerable without reading through every stage row.
            job.current_stage = stage.value
            db.flush()
            return _to_stage_record(row)

    def finish_stage(
        self,
        run_id: int,
        status: StageStatus,
        metrics: StageMetrics | None = None,
        output_payload: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> StageRunRecord:
        with self._sessions.session() as db:
            row = db.get(models.PipelineStageRun, run_id)
            if row is None:
                raise RecordNotFoundError(f"no stage run with id {run_id!r}")

            finished = _utcnow()
            row.status = status.value
            row.finished_at = finished
            row.output_payload = output_payload
            row.error_message = error_message

            if metrics is not None:
                row.model_used = metrics.model_used
                row.validation_passed = metrics.validation_passed
                row.retry_count = metrics.retry_count
                row.duration_ms = metrics.duration_ms

            if row.duration_ms is None and row.started_at is not None:
                started = _aware(row.started_at)
                row.duration_ms = int((finished - started).total_seconds() * 1000)

            db.flush()
            return _to_stage_record(row)

    def get_stage_runs(self, job_id: str) -> list[StageRunRecord]:
        with self._sessions.session() as db:
            rows = db.scalars(
                select(models.PipelineStageRun)
                .where(models.PipelineStageRun.job_id == job_id)
                .order_by(models.PipelineStageRun.id)
            ).all()
            return [_to_stage_record(row) for row in rows]

    def record_write(
        self,
        job_id: str,
        stage: PipelineStage,
        target: WriteTarget,
        node_id: str | None = None,
        edge_type: str | None = None,
        from_id: str | None = None,
        to_id: str | None = None,
        episode_id: str = "",
    ) -> None:
        with self._sessions.session() as db:
            job = self._require_job(db, job_id)

            # The write belongs to the job's trace, not to whatever happens to
            # be current — a stage replayed later still belongs to its original
            # run. Building the entry also validates it, so a malformed record
            # is refused before it becomes permanent.
            entry = WriteLogEntry(
                job_id=job_id,
                trace_id=job.trace_id,
                episode_id=episode_id,
                stage=stage,
                target=target,
                node_id=node_id,
                edge_type=edge_type,
                from_id=from_id,
                to_id=to_id,
            )

            db.add(
                models.PipelineWriteLog(
                    job_id=job_id,
                    trace_id=job.trace_id,
                    episode_id=entry.episode_id,
                    stage=entry.stage.value,
                    target=entry.target.value,
                    node_id=entry.node_id,
                    edge_type=entry.edge_type,
                    from_id=entry.from_id,
                    to_id=entry.to_id,
                    written_at=_utcnow(),
                )
            )
            db.flush()

    def get_trace(self, trace_id: str) -> PipelineTrace | None:
        with self._sessions.session() as db:
            job = db.scalar(
                select(models.PipelineJob).where(models.PipelineJob.trace_id == trace_id)
            )
            if job is None:
                return None

            stage_rows = db.scalars(
                select(models.PipelineStageRun)
                .where(models.PipelineStageRun.job_id == job.job_id)
                .order_by(models.PipelineStageRun.id)
            ).all()
            write_rows = db.scalars(
                select(models.PipelineWriteLog)
                .where(models.PipelineWriteLog.job_id == job.job_id)
                .order_by(models.PipelineWriteLog.id)
            ).all()

            return PipelineTrace(
                job=_to_job_record(job),
                stage_runs=[_to_stage_record(row) for row in stage_rows],
                writes=[_to_write_entry(row) for row in write_rows],
            )

    def find_job_for_node(self, node_id: str) -> PipelineJobRecord | None:
        with self._sessions.session() as db:
            write = db.scalar(
                select(models.PipelineWriteLog)
                .where(models.PipelineWriteLog.node_id == node_id)
                .order_by(models.PipelineWriteLog.id)
            )
            if write is None:
                return None
            job = db.get(models.PipelineJob, write.job_id)
            return _to_job_record(job) if job else None

    def list_recent(self, user_id: str, limit: int = 50) -> list[PipelineJobRecord]:
        with self._sessions.session() as db:
            rows = db.scalars(
                select(models.PipelineJob)
                .where(models.PipelineJob.user_id == user_id)
                .order_by(models.PipelineJob.created_at.desc())
                .limit(limit)
            ).all()
            return [_to_job_record(row) for row in rows]

    def _require_job(self, db: Session, job_id: str) -> models.PipelineJob:
        row = db.get(models.PipelineJob, job_id)
        if row is None:
            raise RecordNotFoundError(f"no pipeline job with id {job_id!r}")
        return row

    def _next_attempt(
        self, db: Session, job_id: str, stage: PipelineStage, episode_id: str
    ) -> int:
        """
        The next attempt number for one stage of one episode.

        Counted per episode, because each episode runs the middle stages
        independently. Counting per job would make the second episode's
        first try look like the first episode's second.
        """
        highest = db.scalar(
            select(func.max(models.PipelineStageRun.attempt)).where(
                models.PipelineStageRun.job_id == job_id,
                models.PipelineStageRun.stage == stage.value,
                models.PipelineStageRun.episode_id == episode_id,
            )
        )
        return (highest or 0) + 1


class SqlAlchemyCoreferenceMapRepository:
    """Stores who the pronouns in an entry referred to."""

    def __init__(self, sessions: _SessionManager) -> None:
        self._sessions = sessions

    def save(self, record: CoreferenceRecord) -> str:
        """
        Store one entry's resolutions, overwriting any earlier version.

        Overwriting matters for re-runs: reading the same entry again
        produces the same map, and a run should not fail because it already
        succeeded once.
        """
        with self._sessions.session() as db:
            row = db.get(models.CoreferenceMapRecord, record.id)
            if row is None:
                row = models.CoreferenceMapRecord(
                    id=record.id, created_at=_utcnow()
                )
                db.add(row)

            row.job_id = record.job_id
            row.trace_id = record.trace_id
            row.session_id = record.session_id
            row.entry_id = record.entry_id
            row.resolved_entities = record.resolved_entities
            row.ambiguous_refs = record.ambiguous_refs
            db.flush()
            return record.id

    def get(self, map_id: str) -> CoreferenceRecord | None:
        with self._sessions.session() as db:
            row = db.get(models.CoreferenceMapRecord, map_id)
            return _to_coref_record(row) if row else None


class SqlAlchemyHitlQueueRepository:
    """Holds items waiting for the user to decide something."""

    def __init__(self, sessions: _SessionManager) -> None:
        self._sessions = sessions

    def enqueue(self, item: HitlQueueItemRecord) -> str:
        priority_rank = HITL_ENTRY_TYPE_RANK[item.entry_type]
        signal_rank = _SIGNAL_RANK[item.signal_strength]

        with self._sessions.session() as db:
            db.add(
                models.HitlQueueItem(
                    id=item.id,
                    user_id=item.user_id,
                    trace_id=item.trace_id or get_trace_id(),
                    job_id=item.job_id,
                    audit_node_id=item.audit_node_id,
                    observation_id=item.observation_id,
                    episode_id=item.episode_id,
                    entry_type=item.entry_type.value,
                    status=item.status.value,
                    priority_rank=priority_rank,
                    signal_rank=signal_rank,
                    recommended_action=(
                        item.recommended_action.value if item.recommended_action else None
                    ),
                    candidate_a_node_id=item.candidate_a_node_id,
                    candidate_b_node_id=item.candidate_b_node_id,
                    confidence_a=item.confidence_a,
                    confidence_b=item.confidence_b,
                    context_summary=item.context_summary,
                    created_at=item.created_at or _utcnow(),
                    snooze_count=item.snooze_count,
                    last_snoozed_at=item.last_snoozed_at,
                )
            )
            db.flush()

        logger.info(
            "review item queued",
            extra={"item_id": item.id, "entry_type": item.entry_type.value},
        )
        return item.id

    def get(self, item_id: str) -> HitlQueueItemRecord | None:
        with self._sessions.session() as db:
            row = db.get(models.HitlQueueItem, item_id)
            return _to_hitl_record(row) if row else None

    def get_by_audit_node(self, audit_node_id: str) -> HitlQueueItemRecord | None:
        with self._sessions.session() as db:
            row = db.scalar(
                select(models.HitlQueueItem).where(
                    models.HitlQueueItem.audit_node_id == audit_node_id
                )
            )
            return _to_hitl_record(row) if row else None

    def list_pending(self, user_id: str, limit: int = 20) -> list[HitlQueueItemRecord]:
        open_statuses = [status.value for status in OPEN_HITL_STATUSES]
        with self._sessions.session() as db:
            rows = db.scalars(
                select(models.HitlQueueItem)
                .where(
                    models.HitlQueueItem.user_id == user_id,
                    models.HitlQueueItem.status.in_(open_statuses),
                )
                # Ties first, then stronger signals, then oldest first.
                .order_by(
                    models.HitlQueueItem.priority_rank.asc(),
                    models.HitlQueueItem.signal_rank.desc(),
                    models.HitlQueueItem.created_at.asc(),
                )
                .limit(limit)
            ).all()
            return [_to_hitl_record(row) for row in rows]

    def list_visible(
        self, user_id: str, *, now: datetime, limit: int = 20
    ) -> list[HitlQueueItemRecord]:
        with self._sessions.session() as db:
            rows = db.scalars(
                select(models.HitlQueueItem)
                .where(
                    models.HitlQueueItem.user_id == user_id,
                    models.HitlQueueItem.status
                    == HitlItemStatus.PENDING_HITL.value,
                    or_(
                        models.HitlQueueItem.snoozed_until.is_(None),
                        models.HitlQueueItem.snoozed_until <= now,
                    ),
                )
                # Ties first, then stronger signals, then oldest first.
                .order_by(
                    models.HitlQueueItem.priority_rank.asc(),
                    models.HitlQueueItem.signal_rank.desc(),
                    models.HitlQueueItem.created_at.asc(),
                )
                .limit(limit)
            ).all()
            return [_to_hitl_record(row) for row in rows]

    def list_parked(self, user_id: str) -> list[HitlQueueItemRecord]:
        with self._sessions.session() as db:
            rows = db.scalars(
                select(models.HitlQueueItem)
                .where(
                    models.HitlQueueItem.user_id == user_id,
                    models.HitlQueueItem.status
                    == HitlItemStatus.SUSPENDED_QUEUE_FULL.value,
                )
                .order_by(
                    models.HitlQueueItem.priority_rank.asc(),
                    models.HitlQueueItem.signal_rank.desc(),
                    models.HitlQueueItem.created_at.asc(),
                )
            ).all()
            return [_to_hitl_record(row) for row in rows]

    def find_auto_resolvable(
        self, user_id: str, *, cutoff: datetime
    ) -> list[HitlQueueItemRecord]:
        with self._sessions.session() as db:
            rows = db.scalars(
                select(models.HitlQueueItem)
                .where(
                    models.HitlQueueItem.user_id == user_id,
                    models.HitlQueueItem.status
                    == HitlItemStatus.PENDING_HITL.value,
                    # Never something nobody has touched. A count of at least
                    # one is the only evidence that the question was seen.
                    models.HitlQueueItem.snooze_count >= 1,
                    models.HitlQueueItem.last_snoozed_at.is_not(None),
                    models.HitlQueueItem.last_snoozed_at < cutoff,
                )
                .order_by(models.HitlQueueItem.last_snoozed_at.asc())
            ).all()
            return [_to_hitl_record(row) for row in rows]

    def count_pending(self, user_id: str) -> int:
        open_statuses = [status.value for status in OPEN_HITL_STATUSES]
        with self._sessions.session() as db:
            return int(
                db.scalar(
                    select(func.count())
                    .select_from(models.HitlQueueItem)
                    .where(
                        models.HitlQueueItem.user_id == user_id,
                        models.HitlQueueItem.status.in_(open_statuses),
                    )
                )
                or 0
            )

    def count_asked(self, user_id: str) -> int:
        with self._sessions.session() as db:
            return int(
                db.scalar(
                    select(func.count())
                    .select_from(models.HitlQueueItem)
                    .where(
                        models.HitlQueueItem.user_id == user_id,
                        models.HitlQueueItem.status
                        == HitlItemStatus.PENDING_HITL.value,
                    )
                )
                or 0
            )

    def oldest_pending_at(self, user_id: str) -> datetime | None:
        open_statuses = [status.value for status in OPEN_HITL_STATUSES]
        with self._sessions.session() as db:
            return db.scalar(
                select(func.min(models.HitlQueueItem.created_at)).where(
                    models.HitlQueueItem.user_id == user_id,
                    models.HitlQueueItem.status.in_(open_statuses),
                )
            )

    def update_status(
        self,
        item_id: str,
        status: HitlItemStatus,
        resolution_choice: HitlResolutionChoice | None = None,
        resolved_action: ReconciliationAction | None = None,
    ) -> HitlQueueItemRecord:
        with self._sessions.session() as db:
            row = db.get(models.HitlQueueItem, item_id)
            if row is None:
                raise RecordNotFoundError(f"no review item with id {item_id!r}")

            settling = status in (
                HitlItemStatus.RESOLVED,
                HitlItemStatus.AUTO_RESOLVED,
            )
            already_settled = HitlItemStatus(row.status) not in OPEN_HITL_STATUSES
            if settling and already_settled:
                # Two taps on one card, or a sweep racing somebody answering.
                # Letting the second one through would write the same change
                # to the graph a second time.
                raise IllegalStateTransitionError(
                    f"review item {item_id!r} was already settled as {row.status}"
                )

            row.status = status.value
            if resolution_choice is not None:
                row.resolution_choice = resolution_choice.value
            if resolved_action is not None:
                row.resolved_action = resolved_action.value
            if settling:
                row.resolved_at = _utcnow()
                # Nothing is hidden once it is answered. Leaving the date
                # behind would keep a settled item out of any view that reads
                # it, which is confusing rather than harmful, and free to fix.
                row.snoozed_until = None

            db.flush()
            return _to_hitl_record(row)

    def snooze(
        self, item_id: str, *, until: datetime, at: datetime
    ) -> HitlQueueItemRecord:
        with self._sessions.session() as db:
            row = db.get(models.HitlQueueItem, item_id)
            if row is None:
                raise RecordNotFoundError(f"no review item with id {item_id!r}")
            if HitlItemStatus(row.status) not in OPEN_HITL_STATUSES:
                raise IllegalStateTransitionError(
                    f"review item {item_id!r} is already settled"
                )

            row.snooze_count += 1
            row.last_snoozed_at = at
            row.snoozed_until = until
            db.flush()
            return _to_hitl_record(row)

    def save_proposal(self, audit_node_id: str, payload: str) -> None:
        with self._sessions.session() as db:
            row = db.get(models.HitlProposal, audit_node_id)
            if row is None:
                db.add(
                    models.HitlProposal(
                        audit_node_id=audit_node_id,
                        payload=payload,
                        schema_version=_PROPOSAL_SCHEMA_VERSION,
                    )
                )
            else:
                row.payload = payload
                row.schema_version = _PROPOSAL_SCHEMA_VERSION
            db.flush()

    def get_proposal(self, audit_node_id: str) -> str | None:
        with self._sessions.session() as db:
            row = db.get(models.HitlProposal, audit_node_id)
            return row.payload if row else None


class SqlAlchemyUserSettingsRepository:
    """Stores the settings a user has changed from their defaults."""

    def __init__(self, sessions: _SessionManager) -> None:
        self._sessions = sessions

    def get(self, user_id: str, key: str) -> Any | None:
        with self._sessions.session() as db:
            row = db.get(models.UserSetting, (user_id, key))
            return row.value_json if row else None

    def get_all(self, user_id: str) -> dict[str, Any]:
        with self._sessions.session() as db:
            rows = db.scalars(
                select(models.UserSetting).where(models.UserSetting.user_id == user_id)
            ).all()
            return {row.key: row.value_json for row in rows}

    def set(self, user_id: str, key: str, value: Any) -> None:
        # Refusing unknown keys stops a typo from becoming a setting the user
        # believes they changed but which nothing will ever read.
        if key not in KNOWN_SETTING_KEYS:
            raise UnknownSettingKeyError(
                f"{key!r} is not a recognised setting; known keys are "
                f"{sorted(KNOWN_SETTING_KEYS)}"
            )

        with self._sessions.session() as db:
            row = db.get(models.UserSetting, (user_id, key))
            if row is None:
                db.add(models.UserSetting(user_id=user_id, key=key, value_json=value))
            else:
                row.value_json = value
                row.updated_at = _utcnow()
            db.flush()

    def delete(self, user_id: str, key: str) -> bool:
        with self._sessions.session() as db:
            result = db.execute(
                delete(models.UserSetting).where(
                    models.UserSetting.user_id == user_id,
                    models.UserSetting.key == key,
                )
            )
            return bool(result.rowcount)

    def get_records(self, user_id: str) -> list[UserSettingRecord]:
        """Read a user's settings with their timestamps, newest change last."""
        with self._sessions.session() as db:
            rows = db.scalars(
                select(models.UserSetting)
                .where(models.UserSetting.user_id == user_id)
                .order_by(models.UserSetting.key)
            ).all()
            return [
                UserSettingRecord(
                    user_id=row.user_id,
                    key=row.key,
                    value=row.value_json,
                    updated_at=_aware(row.updated_at),
                )
                for row in rows
            ]


class SqlAlchemyDataErasureAuditRepository:
    """Records that erasures happened, without recording what was erased."""

    def __init__(self, sessions: _SessionManager) -> None:
        self._sessions = sessions

    def record(self, entry: ErasureAuditRecord) -> str:
        # Hashing here rather than at the call site means no caller can store
        # a readable identifier by forgetting to.
        user_id_hash = _hash_user_id(entry.user_id)

        with self._sessions.session() as db:
            db.add(
                models.DataErasureAudit(
                    id=entry.id,
                    user_id_hash=user_id_hash,
                    erased_at=entry.erased_at or _utcnow(),
                    nodes_anonymized=entry.nodes_anonymized,
                    embeddings_deleted=entry.embeddings_deleted,
                    entry_ids_affected=list(entry.entry_ids_affected),
                    initiated_by=entry.initiated_by.value,
                    status=entry.status.value,
                )
            )
            db.flush()

        logger.info(
            "erasure recorded",
            extra={"record_id": entry.id, "nodes_anonymized": entry.nodes_anonymized},
        )
        return entry.id

    def get(self, record_id: str) -> StoredErasureAudit | None:
        with self._sessions.session() as db:
            row = db.get(models.DataErasureAudit, record_id)
            return _to_erasure_record(row) if row else None

    def list_for_user(self, user_id: str) -> list[StoredErasureAudit]:
        user_id_hash = _hash_user_id(user_id)
        with self._sessions.session() as db:
            rows = db.scalars(
                select(models.DataErasureAudit)
                .where(models.DataErasureAudit.user_id_hash == user_id_hash)
                .order_by(models.DataErasureAudit.erased_at.desc())
            ).all()
            return [_to_erasure_record(row) for row in rows]


class SqlAlchemyImportRepository:
    """Records what has been uploaded and what became of it."""

    def __init__(self, sessions: _SessionManager) -> None:
        self._sessions = sessions

    def find_by_conversation(
        self, user_id: str, source_conversation_id: str
    ) -> ImportRecord | None:
        with self._sessions.session() as db:
            row = db.scalar(
                select(models.ImportedConversation).where(
                    models.ImportedConversation.user_id == user_id,
                    models.ImportedConversation.source_conversation_id
                    == source_conversation_id,
                )
            )
            return _to_import_record(row) if row else None

    def record(self, entry: ImportRecord) -> str:
        with self._sessions.session() as db:
            db.add(
                models.ImportedConversation(
                    import_id=entry.import_id,
                    batch_id=entry.batch_id,
                    user_id=entry.user_id,
                    source_conversation_id=entry.source_conversation_id,
                    title=entry.title,
                    filename=entry.filename,
                    event_date=entry.event_date,
                    message_count=entry.message_count,
                    session_id=entry.session_id,
                    job_id=entry.job_id,
                    trace_id=entry.trace_id,
                    status=entry.status.value,
                    error=entry.error,
                    created_at=entry.created_at or _utcnow(),
                    finished_at=entry.finished_at,
                )
            )
            db.flush()

        logger.info(
            "import recorded",
            extra={
                "import_id": entry.import_id,
                "batch_id": entry.batch_id,
                "event_date": str(entry.event_date),
                "status": entry.status.value,
            },
        )
        return entry.import_id

    def get(self, import_id: str) -> ImportRecord | None:
        with self._sessions.session() as db:
            row = db.get(models.ImportedConversation, import_id)
            return _to_import_record(row) if row else None

    def update_status(
        self,
        import_id: str,
        status: ImportStatus,
        *,
        job_id: str | None = None,
        trace_id: str | None = None,
        error: str | None = None,
    ) -> ImportRecord:
        with self._sessions.session() as db:
            row = db.get(models.ImportedConversation, import_id)
            if row is None:
                raise RecordNotFoundError(f"no import with id {import_id!r}")

            row.status = status.value
            # Only ever filled in, never cleared. A retry that could not
            # reach a model should not erase the trace of the attempt that
            # did.
            if job_id is not None:
                row.job_id = job_id
            if trace_id is not None:
                row.trace_id = trace_id
            if error is not None:
                row.error = error
            if status in TERMINAL_IMPORT_STATUSES and row.finished_at is None:
                row.finished_at = _utcnow()

            db.flush()
            return _to_import_record(row)

    def get_batch(self, batch_id: str) -> ImportBatch | None:
        with self._sessions.session() as db:
            rows = db.scalars(
                select(models.ImportedConversation)
                .where(models.ImportedConversation.batch_id == batch_id)
                .order_by(models.ImportedConversation.created_at)
            ).all()
            if not rows:
                return None
            return ImportBatch(
                batch_id=batch_id,
                filename=rows[0].filename,
                imports=[_to_import_record(row) for row in rows],
            )

    def list_recent(self, user_id: str, limit: int = 50) -> list[ImportRecord]:
        with self._sessions.session() as db:
            rows = db.scalars(
                select(models.ImportedConversation)
                .where(models.ImportedConversation.user_id == user_id)
                .order_by(models.ImportedConversation.created_at.desc())
                .limit(limit)
            ).all()
            return [_to_import_record(row) for row in rows]


class SQLAlchemyOperationalStore:
    """
    One way in to all operational data.

    Owns the connection and every repository, so callers hold a single
    object rather than assembling pieces themselves.
    """

    def __init__(
        self,
        config: OperationalConfig | None = None,
        engine: Engine | None = None,
    ) -> None:
        self._config = config or OperationalConfig()
        self._engine = engine or create_ops_engine(self._config)
        self._owns_engine = engine is None
        self._factory = create_session_factory(self._engine)
        self._sessions = _SessionManager(self._factory)

        self.buffers = SqlAlchemySessionBufferRepository(self._sessions)
        self.jobs = SqlAlchemyPipelineJobRepository(self._sessions)
        self.coref = SqlAlchemyCoreferenceMapRepository(self._sessions)
        self.hitl = SqlAlchemyHitlQueueRepository(self._sessions)
        self.settings = SqlAlchemyUserSettingsRepository(self._sessions)
        self.erasure = SqlAlchemyDataErasureAuditRepository(self._sessions)
        self.imports = SqlAlchemyImportRepository(self._sessions)

    @property
    def engine(self) -> Engine:
        """The underlying engine, for migrations and tests."""
        return self._engine

    def init_schema(self) -> None:
        """
        Create any missing tables.

        Migrations are the normal way the schema is built and changed. This is
        here for tests and throwaway databases where running a migration would
        be more ceremony than the situation deserves.
        """
        models.Base.metadata.create_all(self._engine)

    def transaction(self):
        """Group several writes so they all succeed or all fail together."""
        return self._sessions.transaction()

    def close(self) -> None:
        """Release the database connection, if this store opened it."""
        if self._owns_engine:
            self._engine.dispose()

    def __enter__(self) -> SQLAlchemyOperationalStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def build_operational_store(config: AppConfig | None = None) -> SQLAlchemyOperationalStore:
    """Create the store the application uses, wired from configuration."""
    settings = config or AppConfig()
    return SQLAlchemyOperationalStore(settings.operational)


# ---------------------------------------------------------------------------
# Turning database rows into the records callers see.
# ---------------------------------------------------------------------------


def _new_session_id(event_date: date, session_label: str) -> str:
    """
    Build a readable id for a new buffer.

    Readable rather than a bare identifier so a log line naming a session says
    something useful on its own.
    """
    label = session_label.strip().replace(" ", "_").lower() or "main"
    return f"sb_{event_date:%Y_%m_%d}_{label}_{uuid.uuid4().hex[:8]}"


def _to_buffer_record(row: models.SessionBuffer) -> SessionBufferRecord:
    return SessionBufferRecord(
        session_id=row.session_id,
        user_id=row.user_id,
        event_date=row.event_date,
        session_label=row.session_label,
        status=BufferStatus(row.status),
        source=BufferSource(row.source),
        message_count=row.message_count,
        created_at=_aware(row.created_at),
        last_activity_at=_aware(row.last_activity_at),
        decayed_at=_aware(row.decayed_at),
        ingested_at=_aware(row.ingested_at),
        active_message_id=row.active_message_id,
        rolling_summary=row.rolling_summary,
        summary_through_seq=row.summary_through_seq,
    )


def _walk_back(
    rows: list[models.BufferMessage], leaf_id: str | None
) -> list[models.BufferMessage]:
    """
    The thread ending at one message, oldest first.

    Follows reply links back from the end rather than reading arrival order,
    which is what leaves an edited-away message and everything after it out.

    Two cases come back as the whole conversation, and both are correct. A
    buffer with no end named has never branched — every import, and every
    chat nobody edited. And a chain that runs into a message that is not
    there falls back rather than returning half a conversation, because a
    conversation silently missing its first half is worse than one carrying
    a branch nobody wanted.
    """
    if leaf_id is None:
        return rows

    by_id = {row.message_id: row for row in rows}
    thread: list[models.BufferMessage] = []
    seen: set[str] = set()
    at: str | None = leaf_id

    while at is not None and at in by_id and at not in seen:
        seen.add(at)
        row = by_id[at]
        thread.append(row)
        at = row.parent_message_id

    if at is not None and at not in by_id:
        logger.warning(
            "a conversation's thread ran into a message that is not there, so "
            "the whole conversation was read instead",
            extra={"missing": at, "found": len(thread), "held": len(rows)},
        )
        return rows

    return list(reversed(thread))


def _to_message_record(row: models.BufferMessage) -> BufferMessageRecord:
    return BufferMessageRecord(
        message_id=row.message_id,
        session_id=row.session_id,
        seq=row.seq,
        role=row.role,
        content=row.content,
        timestamp=_aware(row.timestamp),
        event_date=row.event_date,
        dialogue_act=row.dialogue_act,
        co_created_marker=row.co_created_marker,
        parent_message_id=row.parent_message_id,
        modality=row.modality or "TEXT",
    )


def _source_modality(source: str) -> SourceModality:
    """
    Say whether a buffer's messages came from speech or from typing.

    A buffer records where its messages came from in more detail than the
    pipeline needs — chat, a markdown import, a JSON import, a voice note.
    Preprocessing only cares about one distinction: was this spoken? Only
    spoken input gets the speech cleanup, because "um" in typed text was
    typed on purpose.
    """
    if source == BufferSource.VOICE_NOTE.value:
        return SourceModality.VOICE_NOTE
    return SourceModality.TEXT_ENTRY


def _to_buffer_message(row: models.BufferMessage) -> BufferMessage:
    """Convert a stored message into the form the pipeline expects."""
    return BufferMessage(
        message_id=row.message_id,
        role=row.role,
        content=row.content,
        timestamp=_aware(row.timestamp),
        event_date=row.event_date,
        dialogue_act=row.dialogue_act,
        co_created_marker=row.co_created_marker,
    )
    # No reply link here on purpose: what reaches the pipeline is one
    # already-chosen thread, in order. Branches are a fact about how the
    # conversation was written, not about what it says.


def _to_job_record(row: models.PipelineJob) -> PipelineJobRecord:
    return PipelineJobRecord(
        job_id=row.job_id,
        trace_id=row.trace_id,
        session_id=row.session_id,
        user_id=row.user_id,
        status=JobStatus(row.status),
        current_stage=PipelineStage(row.current_stage) if row.current_stage else None,
        created_at=_aware(row.created_at),
        started_at=_aware(row.started_at),
        finished_at=_aware(row.finished_at),
        retry_count=row.retry_count,
        error_type=row.error_type,
        error_message=row.error_message,
        config_snapshot=row.config_snapshot,
    )


def _to_stage_record(row: models.PipelineStageRun) -> StageRunRecord:
    return StageRunRecord(
        id=row.id,
        job_id=row.job_id,
        trace_id=row.trace_id,
        episode_id=row.episode_id or "",
        stage=PipelineStage(row.stage),
        attempt=row.attempt,
        status=StageStatus(row.status),
        started_at=_aware(row.started_at),
        finished_at=_aware(row.finished_at),
        duration_ms=row.duration_ms,
        model_used=row.model_used,
        validation_passed=row.validation_passed,
        retry_count=row.retry_count,
        input_payload=row.input_payload,
        output_payload=row.output_payload,
        error_message=row.error_message,
    )


def _to_write_entry(row: models.PipelineWriteLog) -> WriteLogEntry:
    return WriteLogEntry(
        id=row.id,
        job_id=row.job_id,
        trace_id=row.trace_id,
        episode_id=row.episode_id or "",
        stage=PipelineStage(row.stage),
        target=WriteTarget(row.target),
        node_id=row.node_id,
        edge_type=row.edge_type,
        from_id=row.from_id,
        to_id=row.to_id,
        written_at=_aware(row.written_at),
    )


def _to_coref_record(row: models.CoreferenceMapRecord) -> CoreferenceRecord:
    return CoreferenceRecord(
        id=row.id,
        job_id=row.job_id,
        trace_id=row.trace_id,
        session_id=row.session_id,
        entry_id=row.entry_id,
        resolved_entities=list(row.resolved_entities or []),
        ambiguous_refs=list(row.ambiguous_refs or []),
        created_at=_aware(row.created_at),
    )


def _to_hitl_record(row: models.HitlQueueItem) -> HitlQueueItemRecord:
    signal_strength = next(
        (strength for strength, rank in _SIGNAL_RANK.items() if rank == row.signal_rank),
        SignalStrength.STANDARD,
    )
    return HitlQueueItemRecord(
        id=row.id,
        user_id=row.user_id,
        audit_node_id=row.audit_node_id,
        entry_type=HitlEntryType(row.entry_type),
        signal_strength=signal_strength,
        status=HitlItemStatus(row.status),
        trace_id=row.trace_id,
        job_id=row.job_id,
        observation_id=row.observation_id,
        episode_id=row.episode_id,
        recommended_action=row.recommended_action,
        candidate_a_node_id=row.candidate_a_node_id,
        candidate_b_node_id=row.candidate_b_node_id,
        confidence_a=row.confidence_a,
        confidence_b=row.confidence_b,
        context_summary=row.context_summary,
        created_at=_aware(row.created_at),
        snooze_count=row.snooze_count,
        last_snoozed_at=_aware(row.last_snoozed_at),
        snoozed_until=_aware(row.snoozed_until),
        resolved_at=_aware(row.resolved_at),
        resolution_choice=row.resolution_choice,
        resolved_action=row.resolved_action,
        priority_rank=row.priority_rank,
        signal_rank=row.signal_rank,
    )


def _to_import_record(row: models.ImportedConversation) -> ImportRecord:
    return ImportRecord(
        import_id=row.import_id,
        batch_id=row.batch_id,
        user_id=row.user_id,
        source_conversation_id=row.source_conversation_id,
        title=row.title,
        filename=row.filename,
        event_date=row.event_date,
        message_count=row.message_count,
        session_id=row.session_id,
        job_id=row.job_id,
        trace_id=row.trace_id,
        status=ImportStatus(row.status),
        error=row.error,
        created_at=_aware(row.created_at),
        finished_at=_aware(row.finished_at),
    )


def _to_erasure_record(row: models.DataErasureAudit) -> StoredErasureAudit:
    return StoredErasureAudit(
        id=row.id,
        user_id_hash=row.user_id_hash,
        erased_at=_aware(row.erased_at),
        nodes_anonymized=row.nodes_anonymized,
        embeddings_deleted=row.embeddings_deleted,
        entry_ids_affected=list(row.entry_ids_affected or []),
        initiated_by=row.initiated_by,
        status=ErasureStatus(row.status),
    )


__all__ = [
    "KNOWN_SETTING_KEYS",
    "SQLAlchemyOperationalStore",
    "SqlAlchemySessionBufferRepository",
    "SqlAlchemyPipelineJobRepository",
    "SqlAlchemyCoreferenceMapRepository",
    "SqlAlchemyHitlQueueRepository",
    "SqlAlchemyUserSettingsRepository",
    "SqlAlchemyDataErasureAuditRepository",
    "SqlAlchemyImportRepository",
    "build_operational_store",
]
