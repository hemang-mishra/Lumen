"""
The interfaces the rest of the application uses to reach stored data.

Each repository covers one area and nothing else, so a caller that only needs
the review queue does not gain the ability to rewrite pipeline history. They
are protocols rather than base classes, which means a test can supply a plain
in-memory stand-in with no database anywhere in sight.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from lumen.operational.enums import (
    BufferSource,
    BufferStatus,
    HitlItemStatus,
    ImportStatus,
    JobStatus,
    PipelineStage,
    StageStatus,
    WriteTarget,
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
)
from lumen.schemas.enums import HitlResolutionChoice
from lumen.schemas.pipeline import SessionDecayEvent


class OperationalError(Exception):
    """Something went wrong in the operational store."""


class RecordNotFoundError(OperationalError):
    """The requested record does not exist."""


class IllegalStateTransitionError(OperationalError):
    """
    A caller tried to move a job into a state it cannot reach from where it is.

    Raised rather than ignored: a job that silently skips from pending to
    complete hides a bug that would otherwise surface much later, as data that
    was never actually produced.
    """


class UnknownSettingKeyError(OperationalError):
    """
    A caller tried to save a setting nobody reads.

    Accepting it would leave the user believing they had changed something
    when nothing would ever act on it.
    """


@runtime_checkable
class SessionBufferRepository(Protocol):
    """Stores conversations while they wait to be processed."""

    def create_buffer(self, record: SessionBufferRecord) -> str:
        """Save a new buffer and return its id."""
        ...

    def find_or_create(
        self,
        user_id: str,
        event_date: date,
        session_label: str = "",
        source: BufferSource = BufferSource.NATIVE_CHAT,
    ) -> SessionBufferRecord:
        """
        Return the buffer for this conversation, creating it if it is the
        first message. Two messages arriving together land in the same buffer.
        """
        ...

    def append_message(self, session_id: str, message: BufferMessageRecord) -> None:
        """
        Add a message to a buffer, keeping its message count and last activity
        time current. The activity time is what the decay check reads.
        """
        ...

    def get_buffer(self, session_id: str) -> SessionBufferRecord | None:
        """Fetch one buffer, or None if there is no such buffer."""
        ...

    def get_messages(self, session_id: str) -> list[BufferMessageRecord]:
        """
        Fetch every message a buffer holds, in the order they arrived.

        Everything, including branches the person moved away from. Anything
        that wants what the conversation actually says wants `active_thread`
        instead.
        """
        ...

    def active_thread(self, session_id: str) -> list[BufferMessageRecord]:
        """
        Fetch the conversation as the person would read it, oldest first.

        Follows the reply links back from whichever message the buffer names
        as current, so a message that was edited away is left out along with
        everything that followed from it. A conversation that has never
        branched — every import, and every chat nobody edited — comes back
        exactly as `get_messages` would return it.
        """
        ...

    def branch_from(
        self, session_id: str, parent_message_id: str | None, message: BufferMessageRecord
    ) -> BufferMessageRecord:
        """
        Add a message as a reply to a particular earlier one, and make that
        the live thread.

        This is what editing is. Passing the parent of the message being
        rewritten puts the new one beside it rather than after it; the
        original and everything that followed stay stored and are simply no
        longer the thread being read.
        """
        ...

    def set_active(self, session_id: str, message_id: str) -> None:
        """
        Point the conversation at a different branch.

        How somebody moves back to a version they had left — nothing is
        rewritten, only which end is being read from.
        """
        ...

    def recent_buffers(
        self,
        user_id: str,
        *,
        before: date,
        limit: int,
        session_label: str = "",
        lookback_days: int = 14,
    ) -> list[SessionBufferRecord]:
        """
        This person's last few conversations before a given day, newest first.

        "The last three days" means the last three days that hold a
        conversation, not the last three squares on the calendar — somebody
        who writes twice a week would otherwise get nothing. The lookback is
        how far back it is willing to reach to find them.

        Days with nothing in them are left out, so an empty one does not take
        a slot from a day that has something to say.
        """
        ...

    def save_summary(self, session_id: str, summary: str, through_seq: int) -> None:
        """
        Record what this conversation has been about so far.

        `through_seq` is how much of it the summary covers, so the next
        refresh reads only what has been said since rather than the whole
        conversation again.
        """
        ...

    def find_decayed(self, cutoff: datetime, limit: int = 50) -> list[SessionBufferRecord]:
        """
        Find open buffers with no activity since the cutoff time.

        These are ready to process. Finding them is this repository's job;
        deciding when to look is the orchestrator's.
        """
        ...

    def build_decay_event(self, session_id: str) -> SessionDecayEvent:
        """
        Turn a buffer and its messages into the object the pipeline consumes.

        This is the single handover point between stored data and the pipeline.
        """
        ...

    def mark_status(self, session_id: str, status: BufferStatus) -> SessionBufferRecord:
        """Move a buffer to a new state, recording the time if it decayed."""
        ...


@runtime_checkable
class PipelineJobRepository(Protocol):
    """Tracks pipeline runs, their stages, and everything they wrote."""

    def create_job(
        self,
        session_id: str,
        user_id: str,
        config_snapshot: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> PipelineJobRecord:
        """
        Start tracking a new run. The trace id is taken from the current run
        context when not supplied.
        """
        ...

    def get_job(self, job_id: str) -> PipelineJobRecord | None:
        """Fetch one job, or None if there is no such job."""
        ...

    def transition(
        self,
        job_id: str,
        to_status: JobStatus,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> PipelineJobRecord:
        """
        Move a job to a new state, rejecting moves that make no sense.

        Restarting a failed job counts its retry, which is how repeated
        failures become visible instead of looking like first attempts.
        """
        ...

    def start_stage(
        self,
        job_id: str,
        stage: PipelineStage,
        input_payload: dict[str, Any] | None = None,
        attempt: int | None = None,
        episode_id: str = "",
    ) -> StageRunRecord:
        """
        Record that a stage has begun, saving what went into it so the stage
        can be replayed later. The attempt number is worked out automatically
        when not given.

        The episode is named for stages that run once per episode. Attempts
        are then counted per episode, so running four episodes reads as four
        first attempts rather than one stage retried three times.
        """
        ...

    def finish_stage(
        self,
        run_id: int,
        status: StageStatus,
        metrics: StageMetrics | None = None,
        output_payload: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> StageRunRecord:
        """Close out a stage attempt with its result and timings."""
        ...

    def get_stage_runs(self, job_id: str) -> list[StageRunRecord]:
        """Fetch every stage attempt for a job, oldest first."""
        ...

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
        """Note that a run created something in the graph or vector store."""
        ...

    def get_trace(self, trace_id: str) -> PipelineTrace | None:
        """Gather a whole run — job, stages, and writes — by its trace id."""
        ...

    def find_job_for_node(self, node_id: str) -> PipelineJobRecord | None:
        """
        Find the run that created a given node.

        This is the reverse lookup that makes any node in the graph traceable
        back to the conversation it came from.
        """
        ...

    def list_recent(self, user_id: str, limit: int = 50) -> list[PipelineJobRecord]:
        """
        Recent runs, newest first.

        Fetching a run by its trace id is only useful to somebody who
        already has one. This is how anybody else finds one to look at.
        """
        ...


@runtime_checkable
class CoreferenceMapRepository(Protocol):
    """
    Stores who the pronouns in an entry referred to.

    Every episode in the graph carries the id of one of these. Without
    somewhere to keep them, that id would point at nothing.
    """

    def save(self, record: CoreferenceRecord) -> str:
        """
        Store one entry's resolutions, replacing any earlier version.

        Replacing rather than refusing, because re-running an entry produces
        the same map again and a second run should not fail over it.
        """
        ...

    def get(self, map_id: str) -> CoreferenceRecord | None:
        """Fetch one map, or None if there is no such map."""
        ...


@runtime_checkable
class HitlQueueRepository(Protocol):
    """Holds items waiting for the user to decide something."""

    def enqueue(self, item: HitlQueueItemRecord) -> str:
        """
        Add an item to the queue, working out its priority from its type and
        signal strength.
        """
        ...

    def get(self, item_id: str) -> HitlQueueItemRecord | None:
        """Fetch one item, or None if there is no such item."""
        ...

    def get_by_audit_node(self, audit_node_id: str) -> HitlQueueItemRecord | None:
        """Find the queue item belonging to a decision in the graph."""
        ...

    def list_pending(self, user_id: str, limit: int = 20) -> list[HitlQueueItemRecord]:
        """
        List unresolved items in the order they should be shown: ties first,
        then stronger signals, then oldest first.
        """
        ...

    def count_pending(self, user_id: str) -> int:
        """Count unresolved items, which is what the queue cap is measured against."""
        ...

    def update_status(
        self,
        item_id: str,
        status: HitlItemStatus,
        resolution_choice: HitlResolutionChoice | None = None,
    ) -> HitlQueueItemRecord:
        """Move an item to a new state, stamping the time when it is settled."""
        ...


@runtime_checkable
class UserSettingsRepository(Protocol):
    """Stores the settings a user has changed from their defaults."""

    def get(self, user_id: str, key: str) -> Any | None:
        """Read one setting, or None if it has never been overridden."""
        ...

    def get_all(self, user_id: str) -> dict[str, Any]:
        """Read every setting this user has overridden."""
        ...

    def set(self, user_id: str, key: str, value: Any) -> None:
        """
        Save a setting. Unrecognised keys are refused rather than stored,
        since nothing would ever read them.
        """
        ...

    def delete(self, user_id: str, key: str) -> bool:
        """Remove an override so the setting falls back to its default."""
        ...


@runtime_checkable
class DataErasureAuditRepository(Protocol):
    """Records that erasures happened, without recording what was erased."""

    def record(self, entry: ErasureAuditRecord) -> str:
        """Save an erasure record, hashing the user id before storing it."""
        ...

    def get(self, record_id: str) -> StoredErasureAudit | None:
        """Fetch one erasure record, or None if there is no such record."""
        ...

    def list_for_user(self, user_id: str) -> list[StoredErasureAudit]:
        """
        List a user's erasure records, newest first. The plain user id given
        here is hashed to do the lookup and is never stored.
        """
        ...


@runtime_checkable
class ImportRepository(Protocol):
    """
    Records what has been uploaded and what became of it.

    Every method here is about one of three questions: has this conversation
    been seen before, what is this upload doing right now, and what has been
    uploaded in the past.
    """

    def find_by_conversation(
        self, user_id: str, source_conversation_id: str
    ) -> ImportRecord | None:
        """
        Find an earlier import of the same conversation, if there is one.

        This is the dedupe check. It runs before anything is staged, so a
        second upload of the same export never reaches a model.
        """
        ...

    def record(self, entry: ImportRecord) -> str:
        """Store a new import row and return its id."""
        ...

    def get(self, import_id: str) -> ImportRecord | None:
        """Fetch one import, or None if there is no such row."""
        ...

    def update_status(
        self,
        import_id: str,
        status: ImportStatus,
        *,
        job_id: str | None = None,
        trace_id: str | None = None,
        error: str | None = None,
    ) -> ImportRecord:
        """
        Move an import to a new state, filling in what is now known.

        The job and trace arrive later than the row does — the row is written
        when the messages are stored, and the run that processes them starts
        afterwards — so they are set here rather than at creation.
        """
        ...

    def get_batch(self, batch_id: str) -> ImportBatch | None:
        """
        Everything one uploaded file turned into.

        Assembled from its rows rather than stored, so it cannot disagree
        with them.
        """
        ...

    def list_recent(self, user_id: str, limit: int = 50) -> list[ImportRecord]:
        """Past imports, newest first. This is the history view."""
        ...


@runtime_checkable
class OperationalStore(Protocol):
    """
    One way in to all operational data.

    Callers take this and reach the area they need through it, which keeps
    connection handling in one place instead of spread across the codebase.
    """

    buffers: SessionBufferRepository
    jobs: PipelineJobRepository
    coref: CoreferenceMapRepository
    hitl: HitlQueueRepository
    settings: UserSettingsRepository
    erasure: DataErasureAuditRepository
    imports: ImportRepository

    def init_schema(self) -> None:
        """Make sure the tables exist. Safe to call more than once."""
        ...

    def transaction(self) -> AbstractContextManager[None]:
        """
        Group several writes so they all succeed or all fail together.

        Without this, a run could record that it wrote a node while failing to
        record the edge that gives the node meaning.
        """
        ...

    def close(self) -> None:
        """Release the database connection."""
        ...


__all__ = [
    "OperationalError",
    "RecordNotFoundError",
    "IllegalStateTransitionError",
    "UnknownSettingKeyError",
    "SessionBufferRepository",
    "PipelineJobRepository",
    "CoreferenceMapRepository",
    "HitlQueueRepository",
    "UserSettingsRepository",
    "DataErasureAuditRepository",
    "ImportRepository",
    "OperationalStore",
]
