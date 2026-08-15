"""
The objects repositories accept and hand back.

Database rows never leave this package. Callers work with these validated
models instead, which keeps the rest of the codebase free of any knowledge of
how the data is stored, and means a fake repository can stand in for a real one
in tests without anything noticing.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lumen.operational.enums import (
    BufferSource,
    BufferStatus,
    ErasureInitiator,
    ErasureStatus,
    HitlEntryType,
    HitlItemStatus,
    JobStatus,
    PipelineStage,
    StageStatus,
    WriteTarget,
)
from lumen.schemas.enums import (
    DialogueAct,
    HitlResolutionChoice,
    ReconciliationAction,
    SignalStrength,
)


class OperationalRecord(BaseModel):
    """
    Shared settings for every record in this module.

    Unknown fields are rejected rather than ignored, so a typo in a field name
    fails loudly instead of quietly dropping data.
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class BufferMessageRecord(OperationalRecord):
    """One message in a buffer."""

    message_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    seq: int = Field(ge=0)
    role: str = Field(pattern="^(USER|AI)$")
    content: str
    timestamp: datetime
    event_date: date
    dialogue_act: DialogueAct | None = None
    co_created_marker: bool = False


class SessionBufferRecord(OperationalRecord):
    """A conversation being collected, without its messages."""

    session_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    event_date: date
    session_label: str = ""
    status: BufferStatus = BufferStatus.OPEN
    source: BufferSource = BufferSource.NATIVE_CHAT
    message_count: int = Field(default=0, ge=0)
    created_at: datetime | None = None
    last_activity_at: datetime | None = None
    decayed_at: datetime | None = None
    ingested_at: datetime | None = None


class StageMetrics(OperationalRecord):
    """
    How one stage behaved: how long it took, which model ran it, whether the
    output validated, and how many retries it needed.
    """

    duration_ms: int | None = Field(default=None, ge=0)
    model_used: str | None = None
    validation_passed: bool | None = None
    retry_count: int = Field(default=0, ge=0)


class StageRunRecord(OperationalRecord):
    """One attempt at one stage, including what went in and what came out."""

    id: int | None = None
    job_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    # Empty for the stages that run once for a whole entry rather than once
    # per episode.
    episode_id: str = ""
    stage: PipelineStage
    attempt: int = Field(default=1, ge=1)
    status: StageStatus = StageStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    model_used: str | None = None
    validation_passed: bool | None = None
    retry_count: int = Field(default=0, ge=0)
    input_payload: dict | None = None
    output_payload: dict | None = None
    error_message: str | None = None


class PipelineJobRecord(OperationalRecord):
    """One run of the pipeline over one buffer."""

    job_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    status: JobStatus = JobStatus.PENDING
    current_stage: PipelineStage | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    retry_count: int = Field(default=0, ge=0)
    error_type: str | None = None
    error_message: str | None = None
    config_snapshot: dict | None = None


class WriteLogEntry(OperationalRecord):
    """
    Something a pipeline run wrote to the graph or vector store.

    Node and vector writes name a node; edge writes name the two endpoints and
    the edge type. One of those two shapes must be present, otherwise the entry
    records nothing useful.
    """

    id: int | None = None
    job_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    episode_id: str = ""
    stage: PipelineStage
    target: WriteTarget
    node_id: str | None = None
    edge_type: str | None = None
    from_id: str | None = None
    to_id: str | None = None
    written_at: datetime | None = None

    @model_validator(mode="after")
    def _check_identifiers_present(self) -> "WriteLogEntry":
        if self.target == WriteTarget.GRAPH_EDGE:
            if not (self.edge_type and self.from_id and self.to_id):
                raise ValueError(
                    "an edge write must record edge_type, from_id and to_id"
                )
        elif not self.node_id:
            raise ValueError(f"a {self.target.value} write must record a node_id")
        return self


class HitlQueueItemRecord(OperationalRecord):
    """
    An item waiting for the user's decision.

    The two rank fields are worked out by the repository when the item is
    added, so callers pass meaning and never have to know the numbers.
    """

    id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    audit_node_id: str = Field(min_length=1)
    entry_type: HitlEntryType
    signal_strength: SignalStrength = SignalStrength.STANDARD
    status: HitlItemStatus = HitlItemStatus.PENDING_HITL
    trace_id: str | None = None
    job_id: str | None = None
    observation_id: str | None = None
    episode_id: str | None = None
    recommended_action: ReconciliationAction | None = None
    candidate_a_node_id: str | None = None
    candidate_b_node_id: str | None = None
    confidence_a: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_b: float | None = Field(default=None, ge=0.0, le=1.0)
    context_summary: str | None = None
    created_at: datetime | None = None
    snooze_count: int = Field(default=0, ge=0)
    last_snoozed_at: datetime | None = None
    resolved_at: datetime | None = None
    resolution_choice: HitlResolutionChoice | None = None
    priority_rank: int | None = Field(default=None, ge=1)
    signal_rank: int | None = Field(default=None, ge=1)


class UserSettingRecord(OperationalRecord):
    """One setting the user has overridden."""

    user_id: str = Field(min_length=1)
    key: str = Field(min_length=1)
    value: object = None
    updated_at: datetime | None = None


class ErasureAuditRecord(OperationalRecord):
    """
    Proof that an erasure ran.

    Takes a plain user id, which the repository hashes before storing. The
    stored row never contains a readable identifier or any user content.
    """

    id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    erased_at: datetime | None = None
    nodes_anonymized: int = Field(default=0, ge=0)
    embeddings_deleted: int = Field(default=0, ge=0)
    entry_ids_affected: list[str] = Field(default_factory=list)
    initiated_by: ErasureInitiator = ErasureInitiator.USER_REQUEST
    status: ErasureStatus = ErasureStatus.IN_PROGRESS


class StoredErasureAudit(OperationalRecord):
    """
    An erasure record as it exists in the database, with the identifier hashed.

    A separate type from ErasureAuditRecord on purpose: that one takes a real
    user id going in, this one can only ever carry a hash coming out. Making
    them the same type would make it possible to read back something that looks
    like a plain identifier.
    """

    id: str = Field(min_length=1)
    user_id_hash: str = Field(min_length=1)
    erased_at: datetime
    nodes_anonymized: int = Field(ge=0)
    embeddings_deleted: int = Field(ge=0)
    entry_ids_affected: list[str] = Field(default_factory=list)
    initiated_by: ErasureInitiator
    status: ErasureStatus


class CoreferenceRecord(OperationalRecord):
    """
    Who the pronouns in one entry referred to, as stored.

    Every episode in the graph points here by id. Keeping the resolutions
    means a later question like "why was this filed under Alex?" has an
    answer instead of a guess.
    """

    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    entry_id: str = Field(min_length=1)
    job_id: str | None = None
    trace_id: str | None = None
    resolved_entities: list[dict] = Field(default_factory=list)
    ambiguous_refs: list[dict] = Field(default_factory=list)
    created_at: datetime | None = None


class PipelineTrace(OperationalRecord):
    """
    Everything that happened during one run, assembled in one place.

    This is what turns a trace id into a readable story: the job, every stage
    attempt in order, and every node and edge the run produced.
    """

    job: PipelineJobRecord
    stage_runs: list[StageRunRecord] = Field(default_factory=list)
    writes: list[WriteLogEntry] = Field(default_factory=list)


__all__ = [
    "OperationalRecord",
    "BufferMessageRecord",
    "SessionBufferRecord",
    "StageMetrics",
    "StageRunRecord",
    "PipelineJobRecord",
    "WriteLogEntry",
    "HitlQueueItemRecord",
    "UserSettingRecord",
    "ErasureAuditRecord",
    "StoredErasureAudit",
    "CoreferenceRecord",
    "PipelineTrace",
]
