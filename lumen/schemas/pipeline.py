"""
Pipeline stage data transfer objects — the typed hand-offs between Stage 0
through Stage 3 of the extraction pipeline.

Each stage accepts one of these models as input and returns another as
output — stages are pure functions, and the orchestrator is the only
component that chains them together.

Five of these are the stage boundaries themselves: SessionDecayEvent,
PreprocessingResult, ExtractionResult, RetrievalResult, and
ReconciliationResult. The rest are the smaller pieces those five are built
from.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny, model_validator

from lumen.observability.trace import get_trace_id
from lumen.schemas.base import GraphNode
from lumen.schemas.edges import LogicalEdgeType, LumenEdge
from lumen.schemas.enums import (
    BookkeepingOperation,
    CandidateRetrievalSource,
    DialogueAct,
    EntryClass,
    HitlEntryType,
    QualityGateDecision,
    ReconciliationAction,
    ReconciliationStatus,
    SignalStrength,
    SourceModality,
    StructuralAnchorType,
)
from lumen.schemas.nodes import (
    CausalChainNode,
    CausalStepNode,
    DecisionAuditNode,
    EventNode,
    ObservationNode,
    SessionNode,
)

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class PipelineDTO(BaseModel):
    """
    Base class shared by every top-level pipeline stage input and output.
    Each stage takes one of these in and hands another one back out.

    Attributes:
        trace_id: Ties together everything produced by a single run. It fills
            itself in from the current run context, so stages never have to
            pass it along by hand. Built outside a run it stays empty, which
            is correct — there is no run for it to belong to.
    """

    model_config = ConfigDict(extra="forbid")

    trace_id: str | None = Field(default_factory=get_trace_id)


# ---------------------------------------------------------------------------
# Supporting sub-models
# ---------------------------------------------------------------------------


class BufferMessage(BaseModel):
    """
    A single message stored in a user's session buffer, waiting to be
    grouped together and processed once the session goes idle.

    Attributes:
        message_id: Unique identifier for this message.
        role: Who sent the message — "USER" or "AI".
        content: The raw text of the message.
        timestamp: The exact time the message was sent.
        event_date: The calendar date this message logically belongs to,
            which may differ from the timestamp's date for late-night
            entries.
        dialogue_act: Optional classification of the message's intent —
            e.g. distinguishing a factual question from an emotionally
            expressive reflection.
        co_created_marker: True if this message shows the user explicitly
            agreeing with or adopting something the AI said just before
            it.
    """

    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1)
    role: str = Field(pattern="^(USER|AI)$")
    content: str
    timestamp: datetime
    event_date: date
    dialogue_act: DialogueAct | None = None
    co_created_marker: bool = False


class ResolvedEntity(BaseModel):
    """
    A single pronoun or alias that was successfully matched to a specific
    named person within a journal entry.

    Attributes:
        span: The exact text span that was resolved, e.g. "he" or
            "my mentor".
        resolved_to: The canonical name of the person this span refers to.
        confidence: How confident the resolution is, from 0.0 to 1.0.
        resolution_basis: A short explanation of why this resolution was
            made, e.g. "most recent named person mentioned".
    """

    model_config = ConfigDict(extra="forbid")

    span: str = Field(min_length=1)
    resolved_to: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    resolution_basis: str = Field(min_length=1)


class AmbiguousRef(BaseModel):
    """
    A pronoun or alias that could not be confidently matched to a single
    person, because more than one plausible match exists in the entry.

    Attributes:
        span: The exact text span that could not be resolved, e.g. "she".
        candidates: The people this span might refer to (at least two,
            since a single candidate would just be resolved instead).
        reason: A short explanation of why the reference is ambiguous.
    """

    model_config = ConfigDict(extra="forbid")

    span: str = Field(min_length=1)
    candidates: list[str] = Field(min_length=2)
    reason: str = Field(min_length=1)


class CoreferenceMap(BaseModel):
    """
    The full set of pronoun/alias resolutions produced for one journal
    entry, combining everything that was successfully resolved with
    everything left ambiguous.

    Attributes:
        entry_id: The journal entry this coreference map was built for.
        resolved_entities: All pronouns/aliases confidently matched to a
            named person.
        ambiguous_refs: All pronouns/aliases that could not be confidently
            resolved.
    """

    model_config = ConfigDict(extra="forbid")

    entry_id: str = Field(min_length=1)
    resolved_entities: list[ResolvedEntity] = Field(default_factory=list)
    ambiguous_refs: list[AmbiguousRef] = Field(default_factory=list)


class PreprocessedEpisode(BaseModel):
    """
    One self-contained topical segment of a journal entry, cleaned up and
    ready to be analyzed. A single entry can be split into multiple
    episodes if it covers more than one distinct topic.

    Attributes:
        episode_id: Stable identifier for this episode, assigned as soon as
            the episode is created. Everything downstream refers back to
            the episode by this id.
        episode_summary: A one-line description of what this episode is
            about, useful for scanning a day's episodes without reading
            them in full.
        episode_index: This episode's position within its entry
            (1-indexed).
        total_episodes_in_entry: How many episodes the entry was split
            into in total. episode_index can never exceed this value.
        cleaned_text: The episode's text after cleanup — filler words
            removed, self-corrections resolved, translated to English if
            needed.
        entry_class: Whether this episode is a full "REFLECTION" worth
            deep analysis, or a lighter "RAW_CAPTURE".
        coherence_score: How clear and complete the reflection is, from
            0.0 (incoherent) to 1.0 (a fully formed thought).
        historical_era: Optional label for a specific past period of the
            user's life this episode is anchored to, if one was
            mentioned.
        overarching_themes: High-level topic tags describing what this
            episode is about.
        raw_text_hash: A hash of the cleaned text, used to detect
            duplicates.
    """

    model_config = ConfigDict(extra="forbid")

    episode_id: str = Field(min_length=1)
    episode_summary: str = Field(min_length=1)
    episode_index: int = Field(ge=1)
    total_episodes_in_entry: int = Field(ge=1)
    cleaned_text: str = Field(min_length=1)
    entry_class: EntryClass
    coherence_score: float = Field(ge=0.0, le=1.0)
    historical_era: str | None = None
    overarching_themes: list[str] = Field(default_factory=list)
    raw_text_hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_episode_index_bounds(self) -> "PreprocessedEpisode":
        if self.episode_index > self.total_episodes_in_entry:
            raise ValueError(
                f"episode_index ({self.episode_index}) exceeds "
                f"total_episodes_in_entry ({self.total_episodes_in_entry})"
            )
        return self


class CandidateNode(BaseModel):
    """
    One existing graph node retrieved as a possible match for something
    newly extracted from a journal entry, before a final decision is made
    about how the two relate.

    Attributes:
        node_id: The identifier of the candidate node in the graph.
        node_type: The type of node this is, e.g. "PatternNode" or
            "BeliefNode".
        content_preview: A short preview of the candidate's content,
            useful for display or debugging.
        similarity_score: How semantically similar the candidate is to
            the new content, from 0.0 to 1.0. Only set for candidates
            found via semantic search — candidates found by direct
            structural lookup (e.g. matching a named person) leave this
            unset.
        retrieval_source: How this candidate was found — "SEMANTIC"
            (found by meaning) or "STRUCTURAL" (found via a direct link,
            such as a shared person or historical era).
        structural_anchor_type: If found structurally, what kind of
            anchor led to it, e.g. a named person or a historical era.
        structural_anchor_value: If found structurally, the specific
            value of that anchor, e.g. the person's name.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    content_preview: str = Field(min_length=1)
    similarity_score: float | None = Field(default=None, ge=0.0, le=1.0)
    retrieval_source: CandidateRetrievalSource
    structural_anchor_type: StructuralAnchorType | None = None
    structural_anchor_value: str | None = None

    @model_validator(mode="after")
    def _validate_similarity_score_presence(self) -> "CandidateNode":
        """Semantic candidates must carry a similarity score; structural candidates don't have one."""
        if self.retrieval_source == CandidateRetrievalSource.SEMANTIC and self.similarity_score is None:
            raise ValueError("SEMANTIC candidates require a similarity_score")
        return self


# ---------------------------------------------------------------------------
# Top-level stage DTOs (Technical_HLD.md Section 5)
# ---------------------------------------------------------------------------


class SessionDecayEvent(PipelineDTO):
    """
    Signals that a user's session has gone idle for long enough that its
    buffered messages should now be processed as a complete unit.

    Attributes:
        session_id: Identifier for the session that decayed.
        user_id: Identifier for the user this session belongs to.
        event_date: The calendar date this session's content belongs to.
        session_label: Distinguishes conversations held on the same day. A
            day can hold several, kept separate because the user split them
            by topic. Carried here so later stages can record which
            conversation their results came from.
        source_modality: Whether these messages started life as a voice
            recording or as typed text. Cleanup steps that only make sense
            for speech — removing "um", undoing spoken false starts — are
            skipped for typed input, where those words were meant.
        message_count: How many messages were buffered in this session.
        raw_buffer: The actual buffered messages to be processed.
        triggered_at: The exact time this decay event fired.
    """

    session_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    event_date: date
    session_label: str = ""
    source_modality: SourceModality = SourceModality.TEXT_ENTRY
    message_count: int = Field(ge=0)
    raw_buffer: list[BufferMessage] = Field(default_factory=list)
    triggered_at: datetime


class PreprocessingResult(PipelineDTO):
    """
    The result of cleaning up and segmenting one session's worth of raw
    input before it goes on to deeper analysis.

    Attributes:
        session_id: The session this result was produced from.
        episodes: The cleaned, topic-segmented pieces the session was
            split into.
        coreference_map: The pronoun/alias resolutions found across the
            session, shared by all of its episodes.
        quality_gate_decision: The overall routing decision for this
            session — whether it's substantial enough for full analysis
            ("REFLECTION"), only worth a light pass ("RAW_CAPTURE"), or
            not usable at all ("DISCARD").
        processing_time_ms: How long this preprocessing step took, in
            milliseconds.
        pending_reflections: Follow-up questions generated for the user
            when the input was too thin for full analysis, inviting them
            to expand on it later.
        co_created_spans: Phrasings the assistant offered that the user
            explicitly took up as their own. Only a conversation produces
            these, and only the step that still sees the conversation turn
            by turn can find them — by the time the dialogue has been
            summarized, there is no way to tell whose words were whose.
            Later stages use them to credit an idea to the conversation
            rather than to the person alone.
    """

    session_id: str = Field(min_length=1)
    episodes: list[PreprocessedEpisode] = Field(default_factory=list)
    coreference_map: CoreferenceMap
    quality_gate_decision: QualityGateDecision
    processing_time_ms: int = Field(ge=0)
    pending_reflections: list[str] = Field(default_factory=list)
    co_created_spans: list[str] = Field(default_factory=list)


class MicroextractionInput(PipelineDTO):
    """
    One episode, packaged with everything needed to turn it into graph
    nodes — and nothing about the user's history.

    The episode alone is not enough. It carries no date, and every node
    built from it has to record when the described experience happened.
    It also carries no coreference map, because that is resolved once for
    a whole entry rather than per episode. Both are gathered here so the
    extraction step receives one complete object.

    What is deliberately absent matters as much as what is present. There
    is no place in this model for existing beliefs, patterns, or past
    entries. Extraction reads today's writing on its own terms; comparing
    it to what came before is a later step's job, and a model shown the
    old answers stops reading and starts matching.

    Attributes:
        episode: The cleaned, topic-segmented piece of writing to read.
        coreference_map: Who the entry's pronouns and nicknames refer to,
            so the same person is not extracted as two different people.
        entry_id: The entry the episode came from.
        event_date: The calendar date the writing belongs to.
        occurred_at: When the described experience happened, used as the
            timestamp on every node built from this episode.
        source_modality: Whether this started as speech or typing.
        session_label: Distinguishes entries made on the same day.
        co_created_spans: Assistant phrasings the person adopted as their
            own, used to credit those ideas correctly.
    """

    episode: PreprocessedEpisode
    coreference_map: CoreferenceMap
    entry_id: str = Field(min_length=1)
    event_date: date
    occurred_at: datetime
    source_modality: SourceModality = SourceModality.TEXT_ENTRY
    session_label: str = ""
    co_created_spans: list[str] = Field(default_factory=list)


class ExtractionResult(PipelineDTO):
    """
    Everything extracted from a single episode: the standalone
    observations made, any concrete events or sessions identified, and any
    cause-and-effect chains traced through them.

    Attributes:
        episode_id: The episode this result was extracted from.
        observations: Standalone observations pulled from the episode,
            such as emotions, beliefs, or patterns noticed.
        events: Concrete real-world events identified in the episode.
        sessions: Reflective or conversational sessions identified in the
            episode, as opposed to a physical event.
        causal_chains: Cause-and-effect sequences traced across the
            episode's observations and events.
        causal_steps: The individual steps making up each causal chain
            above.
        failed_observations: Observations that broke a rule and could not
            be corrected within the allowed attempts. Kept apart from the
            good ones rather than marked among them, so nothing can
            accidentally treat a failed reading as a real finding. They
            are held onto so a person can be shown what could not be read
            and put it right themselves.
        extraction_model: The name of the model used to perform the
            extraction.
        validation_passed: Whether the extracted data passed schema
            validation.
        retry_count: How many times extraction had to be retried before
            it validated successfully.
        read_failed: True when the episode could not be read at all, as
            opposed to being read and found to hold little. The two look
            identical from the outside — both leave an empty result — but
            only one of them is worth trying again.
    """

    episode_id: str = Field(min_length=1)
    observations: list[ObservationNode] = Field(default_factory=list)
    events: list[EventNode] = Field(default_factory=list)
    sessions: list[SessionNode] = Field(default_factory=list)
    causal_chains: list[CausalChainNode] = Field(default_factory=list)
    causal_steps: list[CausalStepNode] = Field(default_factory=list)
    failed_observations: list[ObservationNode] = Field(default_factory=list)
    extraction_model: str = Field(min_length=1)
    validation_passed: bool
    retry_count: int = Field(default=0, ge=0)
    read_failed: bool = False


class RetrievalResult(PipelineDTO):
    """
    The candidate matches found for a single piece of extracted content
    (an observation, event, or session), gathered from two independent
    search passes so they can be compared before a final decision is made.
    Together the two passes may surface at most 8 unique candidates.

    Attributes:
        source_node_id: The observation, event, or session these
            candidates were retrieved for.
        pass_a_candidates: Candidates found via semantic (meaning-based)
            search.
        pass_b_candidates: Candidates found via structural (direct-link)
            search, e.g. by shared named person or historical era.
        retrieval_time_ms: How long retrieval took, in milliseconds.
        search_failed: True when the search could not be carried out, as
            opposed to being carried out and finding nothing. The two look
            identical from outside — both leave an empty list — but they
            mean opposite things. Finding nothing means the thought is
            genuinely new and should become a new node. Failing to look
            means nobody knows, and treating that as novelty would record
            a long-standing pattern as a fresh discovery.
    """

    source_node_id: str = Field(min_length=1)
    pass_a_candidates: list[CandidateNode] = Field(default_factory=list)
    pass_b_candidates: list[CandidateNode] = Field(default_factory=list)
    retrieval_time_ms: int = Field(ge=0)
    search_failed: bool = False

    @model_validator(mode="after")
    def _validate_candidate_cap(self) -> "RetrievalResult":
        """Caps the combined, deduplicated candidate set at 8 unique nodes."""
        merged_ids = {c.node_id for c in self.pass_a_candidates} | {
            c.node_id for c in self.pass_b_candidates
        }
        if len(merged_ids) > 8:
            raise ValueError(
                f"merged candidate set has {len(merged_ids)} unique nodes; "
                "the Pass A + Pass B merge is capped at 8"
            )
        return self


class PlannedNode(BaseModel):
    """
    A record reconciliation decided to create, built and checked but not yet
    saved.

    Attributes:
        node_type: Which kind of record this is.
        node: The record itself, already valid.
        searchable_text: The text to index for this record, when it should
            be findable by meaning later. Left unset for records nobody will
            ever search for, such as the note of a decision.
    """

    model_config = ConfigDict(extra="forbid")

    node_type: str = Field(min_length=1)
    node: SerializeAsAny[GraphNode]
    searchable_text: str | None = None


class PlannedEdge(BaseModel):
    """
    A link reconciliation decided to create.

    Attributes:
        logical_type: What the link means.
        table: The specific storage table the link goes in, worked out when
            the plan was built. Resolving it here means an unsupported
            combination of record types fails while the plan is being made,
            rather than halfway through saving it.
        from_node_id: Where the link starts.
        to_node_id: Where it ends.
        edge: The link's own fields.
    """

    model_config = ConfigDict(extra="forbid")

    logical_type: LogicalEdgeType
    table: str = Field(min_length=1)
    from_node_id: str = Field(min_length=1)
    to_node_id: str = Field(min_length=1)
    edge: SerializeAsAny[LumenEdge]

    def properties(self) -> dict[str, Any]:
        """
        The link's own columns, ready to be saved.

        The two ends are left out. A link is stored between two records that
        are found by their identifiers, so the identifiers are how it is
        attached rather than something it carries — a stored copy of them
        would be a second version of the same fact, able to disagree with
        the first.
        """
        return {
            key: value
            for key, value in self.edge.to_graph_dict().items()
            if key not in ("source_node_id", "target_node_id")
        }


class PlannedBookkeeping(BaseModel):
    """
    A small change to a record that already exists.

    These are the only writes in the system that touch something already
    saved, and they never touch anything the person wrote — only counts,
    dates and whether a version is still the current one.

    Attributes:
        operation: Which of the three changes to make.
        node_id: The record to change.
        at: The moment to record against it.
    """

    model_config = ConfigDict(extra="forbid")

    operation: BookkeepingOperation
    node_id: str = Field(min_length=1)
    at: datetime


class GraphWritePlan(BaseModel):
    """
    Everything one entry's decisions add up to, ready to be saved by
    whoever owns the writing.

    Reconciliation builds this and saves none of it. Keeping the two apart
    means every judgement about a person's history lives in one place, and
    the code that writes to the databases makes no judgements at all.

    Attributes:
        nodes: New records, in the order they must be created — anything
            that refers to another new record comes after it.
        edges: New links. Saved after all the records, so both ends always
            exist by the time a link needs them.
        bookkeeping: The small changes to existing records.
        existing_node_ids: Records a link may point at even though this plan
            does not create them. Two kinds go in here: things already in
            the graph, checked when the plan was built, and things this same
            run extracted, which are saved just before this plan runs.
    """

    model_config = ConfigDict(extra="forbid")

    nodes: list[PlannedNode] = Field(default_factory=list)
    edges: list[PlannedEdge] = Field(default_factory=list)
    bookkeeping: list[PlannedBookkeeping] = Field(default_factory=list)
    existing_node_ids: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def _validate_no_duplicate_nodes(self) -> "GraphWritePlan":
        """Refuses a plan that would create the same record twice."""
        seen: set[str] = set()
        for planned in self.nodes:
            if planned.node.node_id in seen:
                raise ValueError(
                    f"the plan creates {planned.node.node_id} more than once"
                )
            seen.add(planned.node.node_id)
        return self

    @model_validator(mode="after")
    def _validate_links_have_both_ends(self) -> "GraphWritePlan":
        """
        Refuses a link pointing at a record nobody will have created.

        A plan is executed straight through with no interpretation, so a
        dangling link would stop halfway and leave an entry half-saved.
        """
        known = {planned.node.node_id for planned in self.nodes} | self.existing_node_ids
        for edge in self.edges:
            for end in (edge.from_node_id, edge.to_node_id):
                if end not in known:
                    raise ValueError(
                        f"link {edge.table} points at unknown record '{end}'"
                    )
        return self

    @model_validator(mode="after")
    def _validate_new_records_come_before_their_references(self) -> "GraphWritePlan":
        """
        Refuses a plan whose records are out of order.

        A record that names another new record — a contradiction naming the
        two beliefs it joins, a new version naming the old one — has to be
        created after the thing it names.
        """
        created_so_far: set[str] = set()
        planned_ids = {planned.node.node_id for planned in self.nodes}
        for planned in self.nodes:
            for field_name in _REFERENCE_FIELDS:
                referenced = getattr(planned.node, field_name, None)
                if not isinstance(referenced, str):
                    continue
                if referenced in planned_ids and referenced not in created_so_far:
                    raise ValueError(
                        f"{planned.node.node_id} names {referenced}, which the "
                        "plan creates later"
                    )
            created_so_far.add(planned.node.node_id)
        return self


# Fields on a record that name another record and must be created after it.
#
# A contradiction points at the two beliefs it joins, and the newer of those
# beliefs points back at the contradiction. That pair refers to each other
# by design, so only one direction can be checked — the one where reading
# the graph forwards depends on it. The back-pointer is left out rather than
# making a legitimate plan impossible to order.
_REFERENCE_FIELDS: tuple[str, ...] = (
    "previous_version_id",
    "belief_a_id",
    "belief_b_id",
)


class HitlEscalation(BaseModel):
    """
    One item reconciliation could not settle on its own, described well
    enough for someone to be asked about it later.

    Attributes:
        audit_node_id: The decision note this item belongs to.
        source_node_id: What the undecided item is about.
        episode_id: The piece of writing it came from.
        entry_type: Why it is waiting.
        signal_strength: How much weight the item carries, which is what
            decides where it sits in the queue.
        summary: A short, plain description of what needs deciding.
    """

    model_config = ConfigDict(extra="forbid")

    audit_node_id: str = Field(min_length=1)
    source_node_id: str = Field(min_length=1)
    episode_id: str = Field(min_length=1)
    entry_type: HitlEntryType
    signal_strength: SignalStrength = SignalStrength.STANDARD
    summary: str = Field(min_length=1)


class ReconciliationResult(PipelineDTO):
    """
    The final decision made about how a newly extracted observation,
    event, or session relates to the rest of the graph — whether it
    confirms something existing, represents a change, conflicts with it,
    or stands on its own as something new.

    Attributes:
        source_node_id: The observation, event, or session this decision
            was made about.
        action: The action taken — e.g. merging into an existing node,
            reinforcing it, evolving it into a new version, branching off
            as something new, or flagging a conflict.
        target_node_id: The existing node this decision relates to, if
            any.
        confidence: How confident the model was in this decision, from
            0.0 to 1.0.
        delta_description: A description of what changed. Required
            whenever the action evolves an existing node into a new
            version.
        decision_model: The name of the model that made this decision.
        escalated_to_hitl: Whether this decision was too uncertain to
            make automatically and was sent to the user for manual review
            instead.
        audit_node_id: The identifier of the audit record created for
            this decision, so it can be traced or reversed later.
    """

    source_node_id: str = Field(min_length=1)
    action: ReconciliationAction
    target_node_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    delta_description: str | None = None
    decision_model: str = Field(min_length=1)
    escalated_to_hitl: bool
    audit_node_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_evolve_requires_delta(self) -> "ReconciliationResult":
        """Requires a delta_description whenever the action is EVOLVE."""
        if self.action == ReconciliationAction.EVOLVE and not self.delta_description:
            raise ValueError("action EVOLVE requires a non-null delta_description")
        return self


class ReconciliationOutcome(PipelineDTO):
    """
    Everything one episode's reconciliation produced: the decisions, the
    notes recording them, what those decisions add up to in writing, and
    whatever could not be settled without asking the person.

    These arrive together because they only make sense together. Saving the
    records without the decision notes would leave changes nobody can trace
    or undo, and saving the notes without the records would claim decisions
    that never happened.

    Attributes:
        episode_id: The piece of writing these decisions were made about.
        results: One decision per thing that was extracted and searched for.
        audit_nodes: The permanent note of each decision, including the ones
            that decided to do nothing. Every note carries what it would
            take to reverse it.
        write_plan: The records and links the decisions imply. Nothing here
            has been saved.
        escalations: Items waiting for the person, with the reason each is
            waiting.
        episode_status: Whether the episode is fully settled, or still has
            something outstanding.
        decision_model: The model that made the decisions. When some were
            escalated, this names the one that had the final say.
        decision_time_ms: How long the whole stage took, in milliseconds.
        decision_failed: True when the model's reply could not be read at
            all, as opposed to being read and producing few decisions. On
            this path nothing is written and everything waits for a person —
            because an entry nobody could decide about looks exactly like an
            entry with nothing in it, and treating the first as the second
            files a lifelong pattern as a brand-new thought.
    """

    episode_id: str = Field(min_length=1)
    results: list[ReconciliationResult] = Field(default_factory=list)
    audit_nodes: list[DecisionAuditNode] = Field(default_factory=list)
    write_plan: GraphWritePlan = Field(default_factory=GraphWritePlan)
    escalations: list[HitlEscalation] = Field(default_factory=list)
    episode_status: ReconciliationStatus = ReconciliationStatus.COMPLETE
    decision_model: str = Field(min_length=1)
    decision_time_ms: int = Field(default=0, ge=0)
    decision_failed: bool = False


__all__ = [
    "PipelineDTO",
    "BufferMessage",
    "ResolvedEntity",
    "AmbiguousRef",
    "CoreferenceMap",
    "PreprocessedEpisode",
    "CandidateNode",
    "SessionDecayEvent",
    "PreprocessingResult",
    "MicroextractionInput",
    "ExtractionResult",
    "RetrievalResult",
    "PlannedNode",
    "PlannedEdge",
    "PlannedBookkeeping",
    "GraphWritePlan",
    "HitlEscalation",
    "ReconciliationResult",
    "ReconciliationOutcome",
]
