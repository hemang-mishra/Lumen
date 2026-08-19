"""
The shapes the web layer hands back.

Nothing raw from the store ever crosses this boundary. A record read out of
the graph arrives as the union of every column across every kind of record —
well over a hundred of them, almost all empty — with any list it held
written out as a run of text. Passing that straight through would make every
reader deal with the storage layer's shape, and would quietly tie the web
surface to the database in use.

What leaves here is what the record actually holds, plus one thing the store
cannot say on its own: whether the answer was cut short.
"""

from __future__ import annotations

import json

from typing import Any, Literal

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from lumen.graph.provider import EdgeRow, GraphSlice
from lumen.graph.queries import node_type_of, tidy_edge, tidy_row
from lumen.review.contracts import QueueCounts, ResolutionChoice
from lumen.schemas.query import RetrievalSignal


class NodeView(BaseModel):
    """
    One record, as a reader sees it.

    Attributes:
        node_id: What it is called. The same identifier names it in the
            search index and in the run log.
        node_type: Which kind of record it is.
        properties: What it actually holds — empty columns dropped, lists
            read back as lists.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    properties: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def of(cls, row: dict[str, Any]) -> "NodeView":
        """Build one from a row as the store returned it."""
        tidied = tidy_row(row)
        return cls(
            node_id=str(tidied.get("node_id", "")),
            node_type=node_type_of(row),
            properties=tidied,
        )


class EdgeView(BaseModel):
    """
    One link between two records.

    Named by its two ends and its kind rather than by an identifier,
    because links do not have one. That triple is the only way to point at a
    particular link, and it is what reversing a decision has to be told.

    Attributes:
        edge_type: What the link means.
        from_node_id: Where it starts.
        to_node_id: Where it ends.
        valid_from: When it was made.
        invalidated_at: When it stopped applying, if it has.
        decision_id: The decision that made it, for links a decision made.
        confidence: How sure that decision was.
    """

    model_config = ConfigDict(extra="forbid")

    edge_type: str = Field(min_length=1)
    from_node_id: str = Field(min_length=1)
    to_node_id: str = Field(min_length=1)
    valid_from: str | None = None
    invalidated_at: str | None = None
    decision_id: str | None = None
    confidence: float | None = None

    @classmethod
    def of(cls, edge: EdgeRow) -> "EdgeView":
        """Build one from a link as the store returned it."""
        tidied = tidy_edge(
            edge.edge_type, edge.from_node_id, edge.to_node_id, edge.properties
        )
        return cls(
            edge_type=tidied["edge_type"],
            from_node_id=tidied["from_node_id"],
            to_node_id=tidied["to_node_id"],
            valid_from=_text(tidied.get("valid_from")),
            invalidated_at=_text(tidied.get("invalidated_at")),
            decision_id=_text(tidied.get("decision_id")),
            confidence=tidied.get("confidence"),
        )


class GraphSliceView(BaseModel):
    """
    A piece of the graph: records and the links among them.

    Attributes:
        nodes: The records in this piece.
        edges: The links between them.
        truncated: True when a limit cut the answer short. Without this, a
            piece that was cut and a piece that was genuinely that size look
            identical — and a partial graph drawn as a whole one is a wrong
            answer that looks right.
    """

    model_config = ConfigDict(extra="forbid")

    nodes: list[NodeView] = Field(default_factory=list)
    edges: list[EdgeView] = Field(default_factory=list)
    truncated: bool = False

    @classmethod
    def of(cls, slice_: GraphSlice) -> "GraphSliceView":
        """Build one from a piece of the graph as the store returned it."""
        return cls(
            nodes=[NodeView.of(row) for row in slice_.nodes],
            edges=[EdgeView.of(edge) for edge in slice_.edges],
            truncated=slice_.truncated,
        )


class NodeListView(BaseModel):
    """
    A page of records.

    Attributes:
        nodes: The records on this page.
        count: How many are on it.
        limit: How many were asked for.
        offset: How many were skipped to reach it.
    """

    model_config = ConfigDict(extra="forbid")

    nodes: list[NodeView] = Field(default_factory=list)
    count: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)

    @classmethod
    def of(
        cls, rows: list[dict[str, Any]], *, limit: int, offset: int
    ) -> "NodeListView":
        views = [NodeView.of(row) for row in rows]
        return cls(nodes=views, count=len(views), limit=limit, offset=offset)


class VersionChainView(BaseModel):
    """
    Every version of one belief or pattern, oldest first.

    Attributes:
        versions: The whole history, in order.
        current_version_id: The version that still applies.
        length: How many versions there have been.
    """

    model_config = ConfigDict(extra="forbid")

    versions: list[NodeView] = Field(default_factory=list)
    current_version_id: str | None = None
    length: int = Field(default=0, ge=0)

    @classmethod
    def of(cls, rows: list[dict[str, Any]]) -> "VersionChainView":
        views = [NodeView.of(row) for row in rows]
        return cls(
            versions=views,
            current_version_id=views[-1].node_id if views else None,
            length=len(views),
        )


class DecisionHistoryView(BaseModel):
    """
    Every decision recorded about one record, newest first.

    Attributes:
        node_id: The record the decisions were about.
        decisions: The notes of those decisions, each carrying what was
            compared, what was chosen, and what it would take to undo it.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    decisions: list[NodeView] = Field(default_factory=list)


class DecisionOutcomeView(BaseModel):
    """
    What became of one finding when it met the rest of the history.

    The step that matters most and shows least. Everything up to here is
    reading; this is the point where a thing somebody noticed on a Tuesday
    either becomes a lasting belief, joins a pattern they have had for years,
    or is held back for them to look at. Without it, the graph reports what
    was found and never what was made of it.

    Attributes:
        source_node_id: The finding this happened to.
        action: What was decided — MERGE, BRANCH, REINFORCE, EVOLVE and the
            rest.
        target_node_id: The record it was *compared against*, if there was
            one. Not the same as what it became: a finding branches when
            nothing already said it, and the comparison that reached that
            answer is still worth showing.
        target_type: What kind of record that is, so "the same as a belief
            you already held" can be said rather than an identifier shown.
        target_preview: What that record actually says.
        became_node_id: The lasting record this finding turned into, when it
            turned into one. This is the question people actually arrive
            with — "did this become a belief?" — and it is reached by the
            link the decision drew, not by the comparison it made.
        became_type: What kind of record that is.
        became_preview: What it says.
        edge_type_created: The link the decision drew.
        confidence: How sure the model was.
        runner_up_action: The answer it nearly gave instead.
        runner_up_confidence: How sure it was of that one.
        status: Whether the decision still stands or was rolled back.
        waiting_for_a_person: True when it was held back rather than acted
            on. A decision recorded but not applied looks exactly like one
            that was applied unless this is said out loud.
        model_used: Which model decided.
        decided_at: When.
        decision_id: The note itself, for anything that wants to undo it.
    """

    model_config = ConfigDict(extra="forbid")

    source_node_id: str = Field(min_length=1)
    action: str = ""
    target_node_id: str | None = None
    target_type: str | None = None
    target_preview: str | None = None
    became_node_id: str | None = None
    became_type: str | None = None
    became_preview: str | None = None
    edge_type_created: str | None = None
    confidence: float | None = None
    runner_up_action: str | None = None
    runner_up_confidence: float | None = None
    status: str = ""
    waiting_for_a_person: bool = False
    model_used: str | None = None
    decided_at: str | None = None
    decision_id: str = ""


class EpisodeDetailView(BaseModel):
    """
    One piece of writing and everything it produced.

    Attributes:
        episode: The piece of writing itself.
        contents: What came out of it, and the links between.
        outcomes: What was decided about each of those findings — newest
            first, and empty for an episode that has not been reconciled.
    """

    model_config = ConfigDict(extra="forbid")

    episode: NodeView
    contents: GraphSliceView
    outcomes: list[DecisionOutcomeView] = Field(default_factory=list)


class GraphStatsView(BaseModel):
    """
    How much is in the graph.

    Counts everything, retired records included: "how much is in here" is a
    different question from "how much of it still applies", and this is the
    first one.

    Attributes:
        counts: How many of each kind of record.
        total: How many records in all.
    """

    model_config = ConfigDict(extra="forbid")

    counts: dict[str, int] = Field(default_factory=dict)
    total: int = Field(default=0, ge=0)

    @classmethod
    def of(cls, counts: dict[str, int]) -> "GraphStatsView":
        return cls(counts=counts, total=sum(counts.values()))


class ProvenanceView(BaseModel):
    """
    Where one record came from.

    The answer to the question every complaint about the graph starts with:
    the conversation it was written from, the run that wrote it, and the
    piece of writing within that conversation.

    Attributes:
        node_id: The record being explained.
        job_id: The run that wrote it.
        trace_id: That run's identifier in the logs.
        session_id: The conversation it came from.
        episode_id: The piece of writing within that conversation.
        written_at: When it was saved.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    episode_id: str = ""
    written_at: str | None = None


class HealthView(BaseModel):
    """
    Whether the service is up and whether its stores answer.

    Both are reported separately, because a running service that cannot
    reach its databases is a different problem from one that is down, and
    the two need different fixing.
    """

    model_config = ConfigDict(extra="forbid")

    status: str
    graph: bool
    operational: bool


class FormulationTurn(BaseModel):
    """One earlier message, as a caller describes it."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"] = "user"
    content: str = Field(min_length=1, max_length=8000)


class FormulationRequest(BaseModel):
    """
    A turn to read, and enough of the conversation to read it against.

    The history is supplied by the caller rather than held between requests.
    This surface exists to look at what a sentence is made of, not to hold a
    conversation, and remembering one between calls would make every answer
    depend on whatever happened to be asked before it.

    Attributes:
        text: The message to read.
        history: What was said before it, oldest first.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=8000)
    history: list[FormulationTurn] = Field(default_factory=list, max_length=20)


class RetrievalRequest(FormulationRequest):
    """
    A turn to read, and then to actually go and fetch history for.

    Everything `FormulationRequest` carries, plus one thing it deliberately
    does not: a way to stay in the same conversation across calls.

    Attributes:
        session_key: Names a conversation to continue. Without it every
            request is its own day and starts with nothing remembered, which
            makes the continuity search impossible to see — it exists
            precisely to notice that this turn and an earlier one are about
            the same thing, and a conversation of length one has no earlier
            one. With it, successive calls share a day's memory the way a
            real conversation would.
    """

    model_config = ConfigDict(extra="forbid")

    session_key: str | None = Field(default=None, min_length=1, max_length=120)


class BriefingLineView(BaseModel):
    """
    One line of the briefing, and where it came from.

    The line is what the assistant reads; the record it came from is what
    somebody judging the briefing needs, because "is this sentence a fair
    summary of that record?" is the only question worth asking about it.

    Attributes:
        node_id: The record behind this line.
        node_type: What kind of record.
        text: The sentence the assistant reads.
        tokens: Roughly what it costs.
        found_by: Which search surfaced it.
        boosted: True when today's conversation had already been round it.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str
    node_type: str
    text: str
    tokens: int
    found_by: str
    boosted: bool = False


class DroppedLineView(BaseModel):
    """
    One record that was fetched and did not make the briefing.

    Attributes:
        node_id: The record left out.
        reason: Which rule left it out.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str
    reason: str


class PromptView(BaseModel):
    """
    Exactly what the assistant would be sent for one sentence.

    The whole point of the inspection surface: everything about this layer
    happens between somebody speaking and the assistant answering, and none
    of it reaches a screen. Without a way to print it, the only evidence that
    the briefing is any good is that the replies feel about right.

    Attributes:
        system: The instructions, in full, exactly as they would be sent.
        messages: The conversation as the assistant would see it.
        briefing: The lines drawn from their history, with their sources.
        dropped: What was fetched and cut, and by which rule.
        summary: What the earlier part of the conversation was about.
        emotional_register: How the turn was read, which set the allowance.
        token_budget: What the briefing was allowed.
        briefing_tokens: What it used.
        total_tokens: Roughly what the whole prompt costs.
        suppressed: True when history was deliberately withheld.
    """

    model_config = ConfigDict(extra="forbid")

    system: str
    messages: list[FormulationTurn] = Field(default_factory=list)
    briefing: list[BriefingLineView] = Field(default_factory=list)
    dropped: list[DroppedLineView] = Field(default_factory=list)
    summary: str | None = None
    emotional_register: str
    token_budget: int
    briefing_tokens: int
    total_tokens: int
    suppressed: bool = False


class RetrievedNodeView(BaseModel):
    """
    One record fetched for a turn, as the outside sees it.

    A trimmed view rather than the internal shape. What the search hands
    around internally carries the whole record so the next stage can
    compress it without reading the graph again; that is machinery, and
    machinery does not belong on a web response.

    Attributes:
        node_id: Which record.
        node_type: What kind.
        preview: Its readable text, shortened.
        found_by: Which of the three searches surfaced it.
        trigger_type: Which reason led there.
        similarity: How closely it matched, where that was measured.
        signal_strength: How much it weighs.
        domain: The area of life, where the record names one.
        era_tag: The period of life, likewise.
        anchor_type: Which kind of anchor led here, for structural finds.
        anchor_value: The anchor itself.
        boosted: True when today's conversation had already been round this
            record once.
        rank_score: Its provisional place in the order.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str
    node_type: str
    preview: str
    found_by: str
    trigger_type: str | None = None
    similarity: float | None = None
    signal_strength: str
    domain: str | None = None
    era_tag: str | None = None
    anchor_type: str | None = None
    anchor_value: str | None = None
    boosted: bool = False
    rank_score: float


class PassReportView(BaseModel):
    """
    What one of the three searches did.

    Attributes:
        which: Which search.
        ran: False when it never got to start.
        found: How many records it turned up.
        kept: How many survived.
        duration_ms: How long it took.
        failure: A short word for what went wrong, or nothing.
    """

    model_config = ConfigDict(extra="forbid")

    which: str
    ran: bool
    found: int
    kept: int
    duration_ms: int
    failure: str | None = None


class TurnContextView(BaseModel):
    """
    Everything decided and everything found for one sentence.

    Both halves together, because they are only meaningful as a pair: the
    records make no sense without the reasons that fetched them, and the
    reasons are hard to judge without seeing what they actually returned.

    Attributes:
        signal: What reading the turn decided.
        outcome: The short version of how the search went.
        candidates: The records, best first.
        passes: What each search did.
        latency_ms: How long the search took.
        within_budget: False when the deadline passed with work outstanding.
        gated: Records held back as too sensitive to arrive uninvited.
        buffered: What today's conversation is currently holding on to.
    """

    model_config = ConfigDict(extra="forbid")

    signal: RetrievalSignal
    outcome: str
    candidates: list[RetrievedNodeView] = Field(default_factory=list)
    passes: list[PassReportView] = Field(default_factory=list)
    latency_ms: int
    within_budget: bool
    gated: list[str] = Field(default_factory=list)
    buffered: list[str] = Field(default_factory=list)


class ConversationReceipt(BaseModel):
    """
    What happened to one conversation from an uploaded file.

    Handed back the moment the messages are stored, before the run that
    processes them has started. That is deliberate: the identifiers are
    settled by then, so the caller is given something to follow rather than
    being made to wait several minutes for the same answer.

    Attributes:
        import_id: Follow this to see how it gets on.
        session_id: The conversation as Lumen now holds it.
        title: What the export called it.
        event_date: The day it was filed under — taken from its first
            message, and applied to the whole conversation.
        message_count: How many messages were stored.
        already_imported: True when this conversation arrived in an earlier
            upload and nothing was queued. It is not an error and not a
            silent skip: the earlier import is named, so the caller can go
            and look at what it produced.
    """

    model_config = ConfigDict(extra="forbid")

    import_id: str
    session_id: str
    title: str = ""
    event_date: str
    message_count: int = 0
    already_imported: bool = False


class RejectionView(BaseModel):
    """
    A conversation in the file that could not be read, and why.

    Reported rather than raised. A file holding thirty conversations where
    two are unreadable should still import twenty-eight, and whoever
    uploaded it should be told exactly which two were dropped.
    """

    model_config = ConfigDict(extra="forbid")

    source_conversation_id: str = ""
    title: str = ""
    reason: str


class UploadReceipt(BaseModel):
    """
    What one uploaded file turned into.

    Attributes:
        batch_id: The upload. Poll this to watch every conversation in the
            file finish.
        filename: What the file was called.
        queued: How many conversations were sent to be processed.
        conversations: One receipt per conversation that could be read.
        rejected: The ones that could not, each with its reason.
    """

    model_config = ConfigDict(extra="forbid")

    batch_id: str
    filename: str = ""
    queued: int = 0
    conversations: list[ConversationReceipt] = Field(default_factory=list)
    rejected: list[RejectionView] = Field(default_factory=list)


class ImportView(BaseModel):
    """
    One past import, as the history shows it.

    Attributes:
        import_id: The row.
        batch_id: The upload it came in.
        title: What the export called the conversation.
        filename: The file it arrived in.
        event_date: The day it was filed under.
        message_count: How many messages it held.
        status: Where it got to.
        error: Why it failed, when it did.
        session_id: The conversation as Lumen holds it.
        trace_id: What to follow to see what the run did. Absent until the
            run has started.
        created_at: When it was uploaded.
        finished_at: When it stopped changing.
    """

    model_config = ConfigDict(extra="forbid")

    import_id: str
    batch_id: str
    title: str = ""
    filename: str = ""
    event_date: str
    message_count: int = 0
    status: str
    error: str | None = None
    session_id: str | None = None
    trace_id: str | None = None
    created_at: str | None = None
    finished_at: str | None = None

    @classmethod
    def of(cls, record: Any) -> "ImportView":
        """Shape one stored import for a reader."""
        return cls(
            import_id=record.import_id,
            batch_id=record.batch_id,
            title=record.title,
            filename=record.filename,
            event_date=record.event_date.isoformat(),
            message_count=record.message_count,
            status=record.status.value,
            error=record.error,
            session_id=record.session_id,
            trace_id=record.trace_id,
            created_at=record.created_at.isoformat() if record.created_at else None,
            finished_at=record.finished_at.isoformat() if record.finished_at else None,
        )


class BatchStatusView(BaseModel):
    """
    How one upload is getting on.

    What the page polls while a file is being processed.

    Attributes:
        batch_id: The upload.
        filename: What the file was called.
        finished: True once nothing in it will change again on its own —
            the signal to stop polling.
        imports: Every conversation in it, with where each has got to.
    """

    model_config = ConfigDict(extra="forbid")

    batch_id: str
    filename: str = ""
    finished: bool = False
    imports: list[ImportView] = Field(default_factory=list)


class RunSummaryView(BaseModel):
    """
    One past run, enough to pick it out of a list.

    Exists because fetching a run by its trace id is only useful to somebody
    who already has one, and nothing else in the system hands them out.

    Attributes:
        trace_id: What to follow for the whole story of the run.
        job_id: The run's own identifier.
        session_id: The conversation it processed.
        status: How it ended.
        created_at: When it started.
        finished_at: When it stopped.
    """

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    job_id: str
    session_id: str
    status: str
    created_at: str | None = None
    finished_at: str | None = None

    @classmethod
    def of(cls, record: Any) -> "RunSummaryView":
        """Shape one stored run for a reader."""
        return cls(
            trace_id=record.trace_id,
            job_id=record.job_id,
            session_id=record.session_id,
            status=record.status.value,
            created_at=record.created_at.isoformat() if record.created_at else None,
            finished_at=record.finished_at.isoformat() if record.finished_at else None,
        )


class WrittenMessageView(BaseModel):
    """
    One message exactly as it was written.

    Attributes:
        seq: Where it came in the conversation.
        role: Who said it — the person, or whatever they were talking to.
        content: The text itself, unaltered.
        timestamp: When it was said.
    """

    model_config = ConfigDict(extra="forbid")

    seq: int = Field(ge=0)
    role: str
    content: str
    timestamp: str | None = None

    @classmethod
    def of(cls, record: Any) -> "WrittenMessageView":
        return cls(
            seq=record.seq,
            role=record.role,
            content=record.content,
            timestamp=record.timestamp.isoformat() if record.timestamp else None,
        )


class EpisodeSourceView(BaseModel):
    """
    The writing an episode was read from.

    The graph keeps what was *concluded* — an episode carries a summary and a
    hash of its text, never the text. That is the right thing for a store of
    conclusions and the wrong thing for a person checking one: a claim about
    somebody's history is only reviewable next to the words it came from.

    So this reaches past the graph to the conversation the run processed, and
    hands back what was actually written.

    Attributes:
        episode_id: The piece of writing this belongs to.
        session_id: The conversation it was part of.
        trace_id: The run that read it.
        event_date: The day it was filed under.
        session_label: What that day's conversation was called.
        messages: Everything written, in order.
    """

    model_config = ConfigDict(extra="forbid")

    episode_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    trace_id: str = ""
    event_date: str | None = None
    session_label: str = ""
    messages: list[WrittenMessageView] = Field(default_factory=list)


class RunListView(BaseModel):
    """Recent runs, newest first."""

    model_config = ConfigDict(extra="forbid")

    runs: list[RunSummaryView] = Field(default_factory=list)


def _text(value: Any) -> str | None:
    """A stored value as text, or nothing when it was never set."""
    return None if value is None else str(value)


__all__ = [
    "NodeView",
    "EdgeView",
    "GraphSliceView",
    "NodeListView",
    "VersionChainView",
    "DecisionHistoryView",
    "DecisionOutcomeView",
    "EpisodeDetailView",
    "GraphStatsView",
    "ProvenanceView",
    "HealthView",
    "FormulationTurn",
    "FormulationRequest",
    "RetrievalRequest",
    "BriefingLineView",
    "DroppedLineView",
    "PromptView",
    "RetrievedNodeView",
    "PassReportView",
    "TurnContextView",
    "ConversationReceipt",
    "RejectionView",
    "UploadReceipt",
    "ImportView",
    "BatchStatusView",
    "RunSummaryView",
    "RunListView",
    "WrittenMessageView",
    "EpisodeSourceView",
]


class TranscriptView(BaseModel):
    """What a recording turned out to say."""

    model_config = ConfigDict(extra="forbid")

    text: str
    language: str | None = None


class ChatMessageView(BaseModel):
    """One turn of a conversation, as the outside may read it."""

    model_config = ConfigDict(extra="forbid")

    message_id: str
    role: str
    content: str
    timestamp: datetime


class ChatDayView(BaseModel):
    """
    One day that holds a conversation.

    Says whether it can still be changed, because that is the next question
    somebody will ask and the date alone does not answer it — a day becomes
    unchangeable when the pipeline has drawn conclusions from it, not at
    midnight.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str
    event_date: date
    message_count: int = Field(ge=0)
    status: str
    editable: bool
    summary: str | None = None


class ChatThreadView(BaseModel):
    """One day's conversation, as the person would read it back."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    event_date: date
    editable: bool
    summary: str | None = None
    messages: list[ChatMessageView] = Field(default_factory=list)


class ReviseRequest(BaseModel):
    """A request to say one of the earlier turns differently."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    content: str = Field(min_length=1)


class ReportEnvelopeView(BaseModel):
    """
    One periodic report, as it appears in a list.

    The envelope only. A quarter's report holds a great deal of content, and
    listing twenty of them in full would send several megabytes to answer the
    question "which reports exist".
    """

    model_config = ConfigDict(extra="forbid")

    report_id: str
    report_type: str
    period_start: datetime
    period_end: datetime
    episodes_analyzed: int = Field(ge=0)
    archetype_shift_detected: bool = False
    narrative_status: str | None = None
    headline: str | None = None
    model_used: str = ""
    created_at: datetime | None = None

    @classmethod
    def of(cls, row: dict[str, Any]) -> "ReportEnvelopeView":
        """Build one from a report as the store returned it."""
        tidied = tidy_row(row)
        content = _report_content(tidied)
        meta = content.get("meta") or {}
        return cls(
            report_id=str(tidied.get("node_id", "")),
            report_type=str(tidied.get("report_type", "")),
            period_start=_as_moment(tidied.get("period_start")),
            period_end=_as_moment(tidied.get("period_end")),
            episodes_analyzed=int(tidied.get("episodes_analyzed") or 0),
            archetype_shift_detected=bool(tidied.get("archetype_shift_detected")),
            narrative_status=meta.get("narrative_status"),
            headline=content.get("headline") or None,
            model_used=str(tidied.get("model_used", "")),
            created_at=_as_moment(tidied.get("created_at"), default=None),
        )


class ReportDetailView(BaseModel):
    """
    One report in full, envelope and content together.

    The content is handed over as it was stored rather than reshaped. It is a
    document written at a point in time, and a reader years from now needs
    what was actually written, not this month's idea of how to present it.
    """

    model_config = ConfigDict(extra="forbid")

    report: ReportEnvelopeView
    content: dict[str, Any] = Field(default_factory=dict)
    episode_ids: list[str] = Field(default_factory=list)

    @classmethod
    def of(
        cls, row: dict[str, Any], *, episode_ids: list[str]
    ) -> "ReportDetailView":
        """Build one from a report and the writing it was joined to."""
        return cls(
            report=ReportEnvelopeView.of(row),
            content=_report_content(tidy_row(row)),
            episode_ids=episode_ids,
        )


class ReportListView(BaseModel):
    """A page of reports, newest first."""

    model_config = ConfigDict(extra="forbid")

    reports: list[ReportEnvelopeView] = Field(default_factory=list)
    count: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class ReportDueView(BaseModel):
    """
    One period that should have been reported on and has not been.

    What a schedule would run if it woke up now. Exposed so that the decision
    is inspectable before anything is spent acting on it.
    """

    model_config = ConfigDict(extra="forbid")

    report_type: str
    period_start: datetime
    period_end: datetime


class ReportRunRequest(BaseModel):
    """
    A request to build one report by hand.

    The period is named by the day it starts. `force` writes a second report
    for a period that already has one, which is the only way to replace a
    report whose wording came out badly — nothing here overwrites, so both
    are kept and the newer one is what readers are shown.
    """

    model_config = ConfigDict(extra="forbid")

    report_type: Literal["WEEKLY", "MONTHLY", "QUARTERLY", "SHADOW"]
    period_start: date | None = None
    force: bool = False


class ReportRunView(BaseModel):
    """What one hand-run report attempt came to."""

    model_config = ConfigDict(extra="forbid")

    status: str
    report_id: str | None = None
    report_type: str
    period_start: datetime
    period_end: datetime
    episodes_analyzed: int = Field(ge=0)
    narrative_status: str
    duration_ms: int = Field(ge=0)
    error: str | None = None


def _report_content(tidied: dict[str, Any]) -> dict[str, Any]:
    """
    A report's body, read back from how it was stored.

    Kept as text in the graph because there is no column type for a document,
    so it arrives as a run of JSON and is read back here. A body that cannot
    be read is reported as empty rather than failing the request: the
    envelope still says which period it covered, which is worth more than an
    error.
    """
    raw = tidied.get("report_content")
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_moment(value: Any, *, default: datetime | None = None) -> Any:
    """Read a stored timestamp back, keeping a missing one missing."""
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# The review queue
# ---------------------------------------------------------------------------


class ReviewResolveRequest(BaseModel):
    """
    One answer to one question.

    Only the answer is accepted. Everything else about what happens next —
    which records are written, which links, what the note says — comes from
    what was saved when the question was raised, so a caller cannot use this
    to write something nobody proposed.
    """

    model_config = ConfigDict(extra="forbid")

    choice: ResolutionChoice


class ReviewCountView(BaseModel):
    """
    How much is waiting, for a badge.

    Cheap on purpose. This is polled from every screen, so it answers with
    counts and one date rather than assembling any cards.
    """

    model_config = ConfigDict(extra="forbid")

    pending: int = Field(ge=0)
    visible: int = Field(ge=0)
    parked: int = Field(ge=0)
    cap: int = Field(ge=1)
    at_capacity: bool = False
    oldest_asked_at: datetime | None = None

    @classmethod
    def of(cls, counts: QueueCounts) -> "ReviewCountView":
        """Build one from what the queue reported."""
        return cls(**counts.model_dump())


class PersonaSectionView(BaseModel):
    """
    One section of the instruction, as a settings screen needs to show it.

    Carries the person's wording and the default side by side, and says
    which of the two is actually in use. A screen with only the effective
    text cannot draw the difference between "they wrote this" and "this is
    what Lumen ships with", which is the one thing somebody editing it needs
    to know before they change anything.

    Attributes:
        name: Which section this is.
        text: What is actually sent to the model.
        default: What Lumen ships with, always, whether or not it is in use.
        overridden: True when the person wrote this themselves.
        max_length: The longest this section may be, so a form can say so
            before a save is refused rather than after.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    text: str
    default: str
    overridden: bool
    max_length: int


class PersonaView(BaseModel):
    """
    How the assistant is instructed to talk to one person.

    The fixed sections are handed back as text with no way to set them. They
    are shown rather than hidden on purpose: somebody deciding whether to
    trust this with the worst week of their life is entitled to read what it
    has been told to do when they are in real distress. Being unable to edit
    it is the point; being unable to see it would be a different thing.

    Attributes:
        sections: The parts this person may change.
        safety: The distress instruction, appended to every ordinary turn.
        crisis: What replaces the whole instruction in acute distress.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    sections: list[PersonaSectionView]
    safety: str
    crisis: str


class PersonaUpdateRequest(BaseModel):
    """
    A change to one or more sections.

    Every field is optional and every one distinguishes three states, which
    is why they are typed the way they are. Left out entirely, a section is
    untouched. Sent as text, it is stored. Sent empty or as null, the
    override is removed and the default comes back — which is what clearing
    a box on a form means.

    Attributes:
        identity: Who the assistant is to them.
        how_to_be: How it behaves — length, questions, directness.
        how_to_use_the_notes: How visible their own history may be.
    """

    model_config = ConfigDict(extra="forbid")

    identity: str | None = None
    how_to_be: str | None = None
    how_to_use_the_notes: str | None = None

    def changes(self) -> dict[str, str | None]:
        """
        Only the sections this request actually mentioned.

        Built from what was set rather than from every field, so a request
        naming one section cannot silently clear the other two.
        """
        return {
            name: getattr(self, name)
            for name in self.model_fields_set
        }
