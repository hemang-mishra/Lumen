"""
Database tables for the operational store.

These are SQLAlchemy models, which means the same definitions work on SQLite
today and PostgreSQL later — only the connection URL changes.

Two conventions hold throughout:

  * Times are stored as timezone-aware UTC. SQLite does not remember time
    zones on its own, so utcnow() is used everywhere and values are made
    aware again on the way out.
  * Enums are stored as their string values rather than as database enum
    types. Adding a new value then costs nothing on SQLite, which has no way
    to alter an enum in place.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from lumen.operational.enums import (
    BufferSource,
    BufferStatus,
    ErasureStatus,
    HitlItemStatus,
    ImportStatus,
    JobStatus,
    StageStatus,
)


def utcnow() -> datetime:
    """Current time, always timezone-aware and in UTC."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Shared base for every operational table."""


class SessionBuffer(Base):
    """
    A conversation being collected, waiting to be processed.

    Buffers are identified by user, calendar date, and label. A single day can
    hold several separate conversations, and they stay separate on purpose —
    the user split them by topic, so merging them would destroy that intent.

    A buffer sits OPEN while messages arrive. Once it has been quiet long
    enough it becomes eligible for processing, which is what find_decayed
    looks for.
    """

    __tablename__ = "session_buffers"

    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    session_label: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=BufferStatus.OPEN.value
    )
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default=BufferSource.NATIVE_CHAT.value
    )

    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    decayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # The last message of the thread the person is actually in.
    #
    # A conversation is a tree once messages can be edited, and this names
    # the branch that counts: walking parents back from here gives what the
    # person kept, while the branches they moved away from stay stored and
    # unreachable from this pointer. Empty on an imported conversation and on
    # anything written before editing existed, which is read as "no branching
    # here, take every message in order".
    active_message_id: Mapped[str | None] = mapped_column(String(128))

    # What this conversation has been about, in a few sentences, refreshed
    # every so often. Stored rather than held in memory so a long chat still
    # makes sense to the assistant after a restart — and so the cost of
    # writing it is paid once rather than on every turn.
    rolling_summary: Mapped[str | None] = mapped_column(Text)
    summary_through_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    messages: Mapped[list["BufferMessage"]] = relationship(
        back_populates="buffer",
        cascade="all, delete-orphan",
        order_by="BufferMessage.seq",
    )

    __table_args__ = (
        # One buffer per conversation. Two messages arriving at once for the
        # same day and label must land in the same buffer, not create two.
        UniqueConstraint(
            "user_id", "event_date", "session_label", name="uq_buffer_user_date_label"
        ),
        # Supports the decay scan, which filters on status and idle time.
        Index("ix_buffer_status_activity", "status", "last_activity_at"),
    )


class BufferMessage(Base):
    """
    One message inside a buffer.

    Two different orderings live here and they answer different questions.
    `seq` is arrival order — it never repeats within a conversation and it is
    what an imported transcript is read by, since those sometimes carry
    identical or missing timestamps. `parent_message_id` is conversational
    order: which message this one is a reply to.

    They differ the moment somebody edits something. An edit is written as a
    new message sharing the old one's parent, so the two are siblings; they
    have different arrival numbers and the same place in the conversation.
    Reading a thread follows parents, not numbers.

    Nothing is ever deleted. A branch the person moved away from stays
    exactly where it was and stays readable — the same instinct as the
    graph's append-only rule, applied to what was said.
    """

    __tablename__ = "buffer_messages"

    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("session_buffers.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)

    # What this message is a reply to. Empty for the first message of a
    # conversation. No foreign key on purpose: it points inside its own
    # table, and a self-reference would make a whole conversation impossible
    # to delete in one statement.
    parent_message_id: Mapped[str | None] = mapped_column(String(128), index=True)

    role: Mapped[str] = mapped_column(String(8), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Whether this turn was spoken or typed. Kept per message because a day
    # where somebody did both is normal, and the extraction pipeline cleans
    # the two differently.
    modality: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="TEXT", default="TEXT"
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)

    dialogue_act: Mapped[str | None] = mapped_column(String(48))
    co_created_marker: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    buffer: Mapped[SessionBuffer] = relationship(back_populates="messages")

    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_message_session_seq"),
    )


class PipelineJob(Base):
    """
    One run of the pipeline over one session buffer.

    The config snapshot records which models were configured at the time. A
    re-run can then either reproduce the original conditions or deliberately
    differ from them, and either way it is clear which happened.
    """

    __tablename__ = "pipeline_jobs"

    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("session_buffers.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=JobStatus.PENDING.value
    )
    current_stage: Mapped[str | None] = mapped_column(String(48))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_type: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    config_snapshot: Mapped[dict | None] = mapped_column(JSON)

    stage_runs: Mapped[list["PipelineStageRun"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    writes: Mapped[list["PipelineWriteLog"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class PipelineStageRun(Base):
    """
    One attempt at one stage of a job.

    Both the input and the output are kept. That is what allows a stage to be
    replayed later without re-running everything before it, and it is what the
    debug view reads to show exactly what went in and came back out.

    A stage can be attempted more than once, so each attempt gets its own row
    rather than overwriting the last.
    """

    __tablename__ = "pipeline_stage_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("pipeline_jobs.job_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Which episode this stage was working on. One entry usually splits into
    # several, and each runs the middle stages on its own, so without this a
    # four-episode entry would look like one episode retried three times.
    # Empty for stages that run once for the whole entry, such as cleanup.
    #
    # Empty rather than null on purpose: databases treat two nulls as
    # different values, so a nullable column here would switch the
    # uniqueness rule below off for exactly the rows it should still cover.
    episode_id: Mapped[str] = mapped_column(
        String(256), nullable=False, default="", index=True
    )

    stage: Mapped[str] = mapped_column(String(48), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=StageStatus.PENDING.value
    )

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    # How the stage behaved. Kept as real columns, not buried in JSON, so they
    # can be filtered and averaged directly.
    model_used: Mapped[str | None] = mapped_column(String(128))
    validation_passed: Mapped[bool | None] = mapped_column(Boolean)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    input_payload: Mapped[dict | None] = mapped_column(JSON)
    output_payload: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)

    job: Mapped[PipelineJob] = relationship(back_populates="stage_runs")

    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "stage",
            "episode_id",
            "attempt",
            name="uq_stage_run_job_stage_episode_attempt",
        ),
    )


class PipelineWriteLog(Base):
    """
    A record of everything a pipeline run wrote to the graph and vector stores.

    This is what connects a trace back to its results. Given a node, it answers
    "which run created this and what else did that run touch" — without having
    to store a trace id on every node and edge in the graph itself.
    """

    __tablename__ = "pipeline_write_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("pipeline_jobs.job_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # The episode whose processing produced this write. Makes "show me
    # everything this episode put in the graph" a single query, which is
    # what any attempt to explain a wrong graph starts with.
    episode_id: Mapped[str] = mapped_column(
        String(256), nullable=False, default="", index=True
    )

    stage: Mapped[str] = mapped_column(String(48), nullable=False)
    target: Mapped[str] = mapped_column(String(32), nullable=False)

    # Set for node and vector writes.
    node_id: Mapped[str | None] = mapped_column(String(256), index=True)
    # Set for edge writes.
    edge_type: Mapped[str | None] = mapped_column(String(128))
    from_id: Mapped[str | None] = mapped_column(String(256))
    to_id: Mapped[str | None] = mapped_column(String(256))

    written_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[PipelineJob] = relationship(back_populates="writes")


class CoreferenceMapRecord(Base):
    """
    Who the pronouns in one journal entry referred to.

    Cleanup works out that "she" meant Priya and "my boss" meant Alex, once
    for a whole entry. Every episode record in the graph points back here by
    id, which is how anyone can later check why a person was matched the way
    they were.

    It lives here rather than in the graph because it is a working note
    about how the text was read, not something the person believes or
    experienced. Putting it in the graph would mix the two.
    """

    __tablename__ = "coreference_maps"

    id: Mapped[str] = mapped_column(String(256), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("pipeline_jobs.job_id", ondelete="SET NULL"), index=True
    )
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    entry_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    resolved_entities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    ambiguous_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HitlQueueItem(Base):
    """
    An item waiting for the user to decide something.

    This table holds only the queue mechanics — position, status, how many
    times it has been deferred. The decision itself lives in the graph as an
    audit node, and audit_node_id is the link between the two. Keeping one
    owner per fact means the two stores can never disagree.

    The two rank columns exist because the queue is sorted by meaning, not by
    alphabet: a tie beats a low-confidence call, and a critical signal beats a
    routine one. Databases cannot sort text that way, so the ranks are worked
    out once when the item is added and stored as numbers.
    """

    __tablename__ = "hitl_queue"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    job_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("pipeline_jobs.job_id", ondelete="SET NULL")
    )

    # The matching audit node in the graph. Unique, so one decision can never
    # produce two queue items.
    audit_node_id: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    observation_id: Mapped[str | None] = mapped_column(String(256))
    episode_id: Mapped[str | None] = mapped_column(String(256))

    entry_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=HitlItemStatus.PENDING_HITL.value
    )
    priority_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    signal_rank: Mapped[int] = mapped_column(Integer, nullable=False)

    recommended_action: Mapped[str | None] = mapped_column(String(32))
    candidate_a_node_id: Mapped[str | None] = mapped_column(String(256))
    candidate_b_node_id: Mapped[str | None] = mapped_column(String(256))
    confidence_a: Mapped[float | None] = mapped_column(Float)
    confidence_b: Mapped[float | None] = mapped_column(Float)
    context_summary: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    snooze_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_snoozed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # While this is in the future the item is not shown. Deferring something
    # that comes straight back is not deferring it.
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_choice: Mapped[str | None] = mapped_column(String(48))

    # What was actually done about it, so the queue can be read back without
    # going to the graph for every row.
    resolved_action: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        # The exact ordering the queue is read in.
        Index(
            "ix_hitl_priority",
            "user_id", "status", "priority_rank", "signal_rank", "created_at",
        ),
    )


class HitlProposal(Base):
    """
    What the system was going to write, kept until somebody answers.

    Stored whole and as it was built, so answering a question days later
    replays a finished piece of writing rather than working one out again.
    Without this the only thing surviving an escalation is a note of what the
    system was leaning towards, which describes the question and cannot
    answer it.

    Keyed on the decision note rather than the queue row, because the note is
    already the one thing linking this store to the graph, and one decision
    can only ever be waiting on one answer.

    The version number is here so that a later change to the saved shape can
    be migrated deliberately instead of guessed at when an old row is read
    back.
    """

    __tablename__ = "hitl_proposals"

    audit_node_id: Mapped[str] = mapped_column(
        String(256),
        ForeignKey("hitl_queue.audit_node_id", ondelete="CASCADE"),
        primary_key=True,
    )
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserSetting(Base):
    """
    A single setting the user has changed from its default.

    Stored as key and value rather than one column per setting, so adding a new
    setting never needs a database migration. Only settings that have actually
    been overridden get a row; everything else falls back to the environment
    or the built-in default.
    """

    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_json: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class DataErasureAudit(Base):
    """
    Proof that an erasure happened.

    Deliberately holds no personal content and no readable user identifier —
    the user id is hashed before it ever reaches this table. A record of a
    deletion that itself preserved the deleted information would defeat the
    point.
    """

    __tablename__ = "data_erasure_audit"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    erased_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    nodes_anonymized: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embeddings_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    entry_ids_affected: Mapped[list | None] = mapped_column(JSON)

    initiated_by: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ErasureStatus.IN_PROGRESS.value
    )


class ImportedConversation(Base):
    """
    One conversation from one uploaded file.

    Three jobs, which is why it exists as a table rather than being derived
    from the buffers it produced.

    It is the history of what has been uploaded — a buffer alone cannot say
    which file it came from, when it arrived, or that it arrived alongside
    thirty others.

    It is how a re-upload is recognised. The unique rule on
    (user_id, source_conversation_id) is what makes uploading the same
    export twice land on the same row instead of running someone's history
    through the pipeline a second time.

    And it is the join from an upload to the run it caused. A buffer knows
    nothing about trace ids; without this row, watching an upload finish
    would mean guessing which of the recent runs was yours.
    """

    __tablename__ = "imports"

    import_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    # Every conversation from one uploaded file shares a batch, so the file
    # can be followed as a whole while each conversation still succeeds or
    # fails on its own.
    batch_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    source_conversation_id: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    filename: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Nullable because a duplicate never gets a buffer of its own, and a run
    # that fails before it starts never gets a job.
    session_id: Mapped[str | None] = mapped_column(String(128))
    job_id: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ImportStatus.QUEUED.value
    )
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # The dedupe rule. Scoped to the user rather than global, because two
        # people importing from the same application can legitimately hold
        # the same conversation identifier.
        UniqueConstraint(
            "user_id", "source_conversation_id", name="uq_import_user_conversation"
        ),
        # Supports the history view, which is always "this user's imports,
        # newest first".
        Index("ix_import_user_created", "user_id", "created_at"),
    )


__all__ = [
    "Base",
    "utcnow",
    "SessionBuffer",
    "BufferMessage",
    "PipelineJob",
    "PipelineStageRun",
    "PipelineWriteLog",
    "HitlQueueItem",
    "UserSetting",
    "DataErasureAudit",
    "ImportedConversation",
]
