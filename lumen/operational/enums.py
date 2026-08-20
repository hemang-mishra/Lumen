"""
Closed vocabularies used only by the operational database.

These describe process state — where a session is in its lifecycle, whether a
job finished, why an item is waiting for review. Vocabularies that describe
knowledge instead live in lumen/schemas/enums.py and are imported from there
rather than duplicated.
"""

from __future__ import annotations

from enum import StrEnum

from lumen.schemas.enums import HitlEntryType, PipelineStage


class BufferStatus(StrEnum):
    """
    Where a session buffer is in its journey to the pipeline.

    OPEN       — still collecting messages; the user may add more.
    DECAYED    — gone quiet long enough that it is ready to process.
    DISPATCHED — handed to the pipeline; a job exists for it.
    PROCESSED  — the pipeline finished with it.
    DISCARDED  — rejected, usually because it held nothing worth extracting.
    """

    OPEN = "OPEN"
    DECAYED = "DECAYED"
    DISPATCHED = "DISPATCHED"
    PROCESSED = "PROCESSED"
    DISCARDED = "DISCARDED"


class BufferSource(StrEnum):
    """Where the messages in a buffer came from."""

    NATIVE_CHAT = "NATIVE_CHAT"
    IMPORT_MARKDOWN = "IMPORT_MARKDOWN"
    IMPORT_JSON = "IMPORT_JSON"
    VOICE_NOTE = "VOICE_NOTE"


class JobStatus(StrEnum):
    """
    Lifecycle of one pipeline run.

    COMPLETE and CANCELLED are final. FAILED is not — a failed job can be
    started again, which is what makes a re-run possible.
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StageStatus(StrEnum):
    """Outcome of a single attempt at one stage."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class WriteTarget(StrEnum):
    """Which store a pipeline run wrote to."""

    GRAPH_NODE = "GRAPH_NODE"
    GRAPH_EDGE = "GRAPH_EDGE"
    VECTOR = "VECTOR"


# Two vocabularies here belong to the pipeline rather than to the database,
# so they are defined with the pipeline schemas and imported at the top of
# this module: why an item is waiting for a person, which reconciliation
# decides, and the list of pipeline stages. Both are re-exported below for
# the code that stores them.


class HitlItemStatus(StrEnum):
    """
    State of a review queue item.

    SUSPENDED_QUEUE_FULL means the queue was at capacity when the item arrived.
    It waits its turn rather than being decided automatically — the queue cap
    exists to protect the user's attention, not to license guessing.
    """

    PENDING_HITL = "PENDING_HITL"
    SUSPENDED_QUEUE_FULL = "SUSPENDED_QUEUE_FULL"
    RESOLVED = "RESOLVED"
    AUTO_RESOLVED = "AUTO_RESOLVED"


class ErasureInitiator(StrEnum):
    """Who asked for data to be erased."""

    USER_REQUEST = "USER_REQUEST"
    ADMIN_REQUEST = "ADMIN_REQUEST"
    AUTOMATED_RETENTION_POLICY = "AUTOMATED_RETENTION_POLICY"


class UserStatus(StrEnum):
    """
    Whether somebody may use the system.

    ACTIVE          — ordinary.
    SUSPENDED       — kept, and refused. A state somebody can come back from.
    ERASURE_PENDING — they have asked to be forgotten. Sessions end first and
                      the data goes second, in that order, because erasing
                      while a session is live means requests arriving for
                      history that is disappearing underneath them.
    """

    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ERASURE_PENDING = "ERASURE_PENDING"


class AuthProvider(StrEnum):
    """Where a sign-in came from. One today, and the table shape expects more."""

    GOOGLE = "GOOGLE"


class ErasureScope(StrEnum):
    """
    How much of somebody's history an erasure covers.

    ALL   — everything, for a person leaving.
    ENTRY — one piece of writing and what was read out of it, for a person
            who regrets one evening rather than the whole record.
    """

    ALL = "ALL"
    ENTRY = "ENTRY"


class ErasureStatus(StrEnum):
    """How far an erasure run got."""

    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


# Priority numbers used to sort the review queue. Lower sorts first.
HITL_ENTRY_TYPE_RANK: dict[HitlEntryType, int] = {
    HitlEntryType.AMBIGUOUS_TIE: 1,
    HitlEntryType.BELOW_THRESHOLD: 2,
    HitlEntryType.EXTRACTION_FAILED: 3,
}

# Statuses that mean an item is still waiting on the user.
OPEN_HITL_STATUSES: frozenset[HitlItemStatus] = frozenset(
    {HitlItemStatus.PENDING_HITL, HitlItemStatus.SUSPENDED_QUEUE_FULL}
)

# Job states that cannot be left once entered.
class ImportStatus(StrEnum):
    """
    What became of one conversation from one uploaded file.

    QUEUED    — its messages are stored and it is waiting for the worker.
    RUNNING   — the pipeline is working through it now.
    COMPLETE  — the pipeline finished with it.
    FAILED    — the pipeline could not finish; `error` says why.
    DUPLICATE — this conversation arrived in an earlier upload and nothing
                was run. Kept as an outcome rather than passed over in
                silence, because "we have seen this before" is a different
                answer from "nothing happened" and the person uploading the
                file deserves the first one.
    """

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    DUPLICATE = "DUPLICATE"


# An import that will not change again on its own.
TERMINAL_IMPORT_STATUSES: frozenset[ImportStatus] = frozenset(
    {ImportStatus.COMPLETE, ImportStatus.FAILED, ImportStatus.DUPLICATE}
)


TERMINAL_JOB_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.COMPLETE, JobStatus.CANCELLED}
)

# The only job state changes allowed. Anything else is a bug in the caller.
ALLOWED_JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.PENDING: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset({JobStatus.COMPLETE, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.FAILED: frozenset({JobStatus.RUNNING}),
    JobStatus.COMPLETE: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


__all__ = [
    "BufferStatus",
    "BufferSource",
    "JobStatus",
    "PipelineStage",
    "StageStatus",
    "WriteTarget",
    "HitlEntryType",
    "HitlItemStatus",
    "ErasureInitiator",
    "ErasureStatus",
    "ImportStatus",
    "HITL_ENTRY_TYPE_RANK",
    "OPEN_HITL_STATUSES",
    "TERMINAL_IMPORT_STATUSES",
    "TERMINAL_JOB_STATUSES",
    "ALLOWED_JOB_TRANSITIONS",
]
