"""
Pydantic node models for the Lumen knowledge graph.

One model per node type in docs/Graph/Schema.md. Field sets are verified
column-by-column against lumen/graph/kuzu_impl.py NODE_TABLES (the actual
persistence contract), not just the docs' YAML examples, since
to_graph_dict() output must match Kuzu's physical schema exactly.

Each `_validate_*` method enforces one rule from
implementation/Goal_2_Plan.md Section A3 — the method name and docstring
name the rule so a validation failure points straight at its source.

See: docs/Graph/Schema.md
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lumen.schemas.base import (
    GraphNode,
    LumenNode,
    PersonRefsMixin,
    SignalProvenanceMixin,
    TemporalNode,
    VersionedNode,
)
from lumen.schemas.enums import (
    HIGH_SIGNAL_REQUIRED_TYPES,
    CandidateRetrievalSource,
    ContradictionResolutionStatus,
    DecisionStatus,
    Domain,
    EntryClass,
    ExtractionConfidence,
    HitlResolutionChoice,
    LifecycleNodeStatus,
    LifecycleState,
    LoopCategory,
    LoopResolutionStatus,
    ModelRole,
    NodeStatus,
    ObservationStatus,
    ObservationType,
    PrincipleDomain,
    Provenance,
    ReconciliationAction,
    ReconciliationStatus,
    RelationshipToUser,
    ReportStatus,
    ReportType,
    SentimentTrend,
    SignalStrength,
    SourceModality,
    StructuralAnchorType,
    CausalStepType,
)

# ---------------------------------------------------------------------------
# Small nested value objects (not graph nodes themselves — embedded as JSON
# columns via to_graph_dict()'s list/dict -> json.dumps() coercion)
# ---------------------------------------------------------------------------


class LifecycleHistoryEntry(BaseModel):
    """One entry in AdoptedPrincipleNode.lifecycle_history. See Schema.md §7."""

    model_config = ConfigDict(extra="forbid")

    state: LifecycleState
    at: datetime
    reason: str = Field(min_length=1)


class RollbackPointer(BaseModel):
    """DecisionAuditNode.rollback_pointer. See Schema.md §9."""

    model_config = ConfigDict(extra="forbid")

    edge_to_invalidate: str = Field(min_length=1)
    nodes_to_requeue: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. EpisodeNode
# ---------------------------------------------------------------------------


class EpisodeNode(TemporalNode):
    """See Schema.md §1 EpisodeNode."""

    entry_id: str = Field(min_length=1)
    occurred_at: datetime
    event_date: date
    session_label: str = Field(min_length=1)
    source_modality: SourceModality
    entry_class: EntryClass
    episode_summary: str = Field(min_length=1)
    historical_era: str | None = None
    overarching_themes: list[str] = Field(default_factory=list)
    episode_index: int = Field(ge=1)
    total_episodes_in_entry: int = Field(ge=1)
    coreference_map_id: str = Field(min_length=1)
    reconciliation_status: ReconciliationStatus
    raw_text_hash: str = Field(min_length=1)
    language_tags: list[str] = Field(default_factory=lambda: ["en"])

    @model_validator(mode="after")
    def _validate_episode_index_bounds(self) -> "EpisodeNode":
        """Rule A3-11: episode_index must fall within total_episodes_in_entry."""
        if self.episode_index > self.total_episodes_in_entry:
            raise ValueError(
                f"episode_index ({self.episode_index}) exceeds "
                f"total_episodes_in_entry ({self.total_episodes_in_entry})"
            )
        return self


# ---------------------------------------------------------------------------
# 2. ObservationNode
# ---------------------------------------------------------------------------


class ObservationNode(TemporalNode, SignalProvenanceMixin, PersonRefsMixin):
    """See Schema.md §2 ObservationNode."""

    episode_id: str = Field(min_length=1)
    occurred_at: datetime
    type: ObservationType
    content: str = Field(min_length=1)
    raw_evidence: list[str] = Field(default_factory=list)
    open_loop_ref: str | None = None
    extraction_confidence: ExtractionConfidence = ExtractionConfidence.STANDARD
    status: ObservationStatus
    extraction_model: str = Field(min_length=1)
    extraction_attempt: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _validate_signal_floor(self) -> "ObservationNode":
        """Rule A3-1: certain observation types have a mandatory HIGH/CRITICAL floor."""
        if self.type in HIGH_SIGNAL_REQUIRED_TYPES and self.signal_strength not in (
            SignalStrength.HIGH,
            SignalStrength.CRITICAL,
        ):
            raise ValueError(
                f"observation type {self.type} requires signal_strength HIGH "
                f"or CRITICAL, got {self.signal_strength}"
            )
        return self


# ---------------------------------------------------------------------------
# 3. EventNode
# ---------------------------------------------------------------------------


class EventNode(TemporalNode, PersonRefsMixin):
    """See Schema.md §3 EventNode."""

    episode_id: str = Field(min_length=1)
    occurred_at: datetime
    event_summary: str = Field(min_length=1)
    signal_strength: SignalStrength
    status: NodeStatus = NodeStatus.ACTIVE
    raw_evidence: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 3.1 SessionNode
# ---------------------------------------------------------------------------


class SessionNode(TemporalNode):
    """See Schema.md §3.1 SessionNode. No person_refs column (unlike Event/Observation)."""

    episode_id: str = Field(min_length=1)
    occurred_at: datetime
    event_date: date
    session_label: str = Field(min_length=1)
    session_summary: str = Field(min_length=1)
    signal_strength: SignalStrength
    status: NodeStatus = NodeStatus.ACTIVE
    participant_entities: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 3.2 CausalChainNode
# ---------------------------------------------------------------------------


class CausalChainNode(TemporalNode):
    """See Schema.md §3.2 CausalChainNode."""

    episode_id: str = Field(min_length=1)
    chain_summary: str = Field(min_length=1)
    is_anticipatory: bool = False
    step_count: int = Field(ge=1)
    status: NodeStatus = NodeStatus.ACTIVE


# ---------------------------------------------------------------------------
# 3.3 CausalStepNode
# ---------------------------------------------------------------------------


class CausalStepNode(LumenNode):
    """See Schema.md §3.3 CausalStepNode. No valid_from — steps aren't versioned."""

    chain_id: str = Field(min_length=1)
    step_index: int = Field(ge=1)
    step_type: CausalStepType
    content: str = Field(min_length=1)
    branch_id: str | None = None


# ---------------------------------------------------------------------------
# 4. PatternNode
# ---------------------------------------------------------------------------


class PatternNode(VersionedNode, SignalProvenanceMixin):
    """See Schema.md §4 PatternNode."""

    pattern_name: str = Field(min_length=1)
    pattern_description: str = Field(min_length=1)
    domain: Domain
    is_canonical: bool = True
    status: LifecycleNodeStatus = LifecycleNodeStatus.ACTIVE
    era_tag: str | None = None
    archetype_tags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 5. BeliefNode
# ---------------------------------------------------------------------------


class BeliefNode(VersionedNode, SignalProvenanceMixin):
    """See Schema.md §5 BeliefNode."""

    belief_statement: str = Field(min_length=1)
    belief_source_summary: str = Field(min_length=1)
    domain: Domain
    is_contradicted: bool = False
    contradiction_node_id: str | None = None
    version_delta: str | None = None
    status: LifecycleNodeStatus = LifecycleNodeStatus.ACTIVE
    era_tag: str | None = None

    @model_validator(mode="after")
    def _validate_contradiction_consistency(self) -> "BeliefNode":
        """Rule A3-9: is_contradicted and contradiction_node_id must agree."""
        if self.is_contradicted and not self.contradiction_node_id:
            raise ValueError(
                "is_contradicted=True requires a non-null contradiction_node_id"
            )
        if self.contradiction_node_id and not self.is_contradicted:
            raise ValueError(
                "contradiction_node_id set requires is_contradicted=True"
            )
        return self


# ---------------------------------------------------------------------------
# 6. LessonNode
# ---------------------------------------------------------------------------


class LessonNode(TemporalNode):
    """See Schema.md §6 LessonNode."""

    lesson_statement: str = Field(min_length=1)
    domain: Domain
    signal_strength: SignalStrength
    lesson_confidence: float = Field(ge=0.0, le=1.0)
    status: NodeStatus = NodeStatus.ACTIVE
    evidence_episodes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 7. AdoptedPrincipleNode
# ---------------------------------------------------------------------------


class AdoptedPrincipleNode(TemporalNode):
    """See Schema.md §7 AdoptedPrincipleNode."""

    adopted_at: datetime
    principle_statement: str = Field(min_length=1)
    principle_name: str = Field(min_length=1)
    domain: PrincipleDomain
    lifecycle_state: LifecycleState
    lifecycle_updated_at: datetime
    source_session_id: str = Field(min_length=1)
    provenance: Provenance
    supersedes_id: str | None = None
    last_referenced_at: datetime
    evidence_count: int = Field(default=0, ge=0)
    status: NodeStatus = NodeStatus.ACTIVE
    lifecycle_history: list[LifecycleHistoryEntry] = Field(default_factory=list)
    parent_belief_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_provenance_restricted(self) -> "AdoptedPrincipleNode":
        """Schema.md restricts this field to USER_GENERATED | CO_CREATED (no AI_GENERATED)."""
        if self.provenance == Provenance.AI_GENERATED:
            raise ValueError(
                "AdoptedPrincipleNode.provenance must be USER_GENERATED or "
                "CO_CREATED, not AI_GENERATED"
            )
        return self

    @model_validator(mode="after")
    def _validate_lifecycle_history(self) -> "AdoptedPrincipleNode":
        """Rule A3-13: lifecycle_history is non-empty and its tail matches lifecycle_state."""
        if not self.lifecycle_history:
            raise ValueError("lifecycle_history must be non-empty")
        if self.lifecycle_history[-1].state != self.lifecycle_state:
            raise ValueError(
                "the last lifecycle_history entry "
                f"({self.lifecycle_history[-1].state}) must match the current "
                f"lifecycle_state ({self.lifecycle_state})"
            )
        return self


# ---------------------------------------------------------------------------
# 8. PersonEntityNode
# ---------------------------------------------------------------------------


class PersonEntityNode(GraphNode):
    """
    See Schema.md §8 PersonEntityNode.

    Extends GraphNode directly — neither created_at nor valid_from appear in
    Schema.md's example or the Kuzu DDL for this table.
    """

    canonical_name: str = Field(min_length=1)
    first_mentioned_at: datetime
    last_mentioned_at: datetime
    mention_count: int = Field(default=0, ge=0)
    relationship_to_user: RelationshipToUser
    relationship_sentiment_trend: SentimentTrend
    is_canonical: bool = True
    status: NodeStatus = NodeStatus.ACTIVE
    aliases: list[str] = Field(default_factory=list)
    linked_observation_types: list[ObservationType] = Field(default_factory=list)
    merged_from: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 9. DecisionAuditNode
# ---------------------------------------------------------------------------


class DecisionAuditNode(LumenNode):
    """
    See Schema.md §9 DecisionAuditNode.

    Extends LumenNode (not TemporalNode) — no valid_from in the Kuzu DDL.

    model_role (formerly routing_tier) records which model-capability role
    (see lumen.schemas.enums.ModelRole) handled this decision — e.g.
    LIGHTWEIGHT for low-risk actions like MERGE/REINFORCE, THINKING for
    high-consequence actions like EVOLVE/CONTRADICT/DIALECTIC. It carries no
    information about where that model ran (cloud vs. local) — deployment
    locality is purely an operator/config concern (lumen.config.ProviderConfig),
    never a routing decision the pipeline makes based on content sensitivity.

    source_node_id (formerly source_observation_id) is the node a
    Reconciliation decision was made about. Renamed because that node isn't
    always an ObservationNode — reinforces/branches_to/regulates edges can
    also originate from an EventNode or SessionNode (see EDGE_REGISTRY),
    and evolved_from/dialectic edges originate from a Pattern/BeliefNode
    itself, not from the triggering extracted content at all.
    """

    action: ReconciliationAction
    source_node_id: str = Field(min_length=1)
    target_node_id: str | None = None
    edge_type_created: str | None = None
    edge_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_runner_up: float | None = Field(default=None, ge=0.0, le=1.0)
    runner_up_action: ReconciliationAction | None = None
    delta_description: str | None = None
    model_used: str = Field(min_length=1)
    model_role: ModelRole
    hitl_resolved: bool = False
    hitl_resolution_timestamp: datetime | None = None
    hitl_resolution_user_choice: HitlResolutionChoice | None = None
    snooze_count: int = Field(default=0, ge=0)
    last_snoozed_at: datetime | None = None
    candidate_retrieval_source: CandidateRetrievalSource
    structural_anchor_type: StructuralAnchorType | None = None
    structural_anchor_value: str | None = None
    co_created_origin: bool = False
    status: DecisionStatus
    rollback_pointer: RollbackPointer

    @model_validator(mode="after")
    def _validate_evolve_requires_delta(self) -> "DecisionAuditNode":
        """Rule A3-5: EVOLVE requires a non-null delta_description."""
        if self.action == ReconciliationAction.EVOLVE and not self.delta_description:
            raise ValueError("action EVOLVE requires a non-null delta_description")
        return self

    @model_validator(mode="after")
    def _validate_structural_anchor_present(self) -> "DecisionAuditNode":
        """Rule A3-6: STRUCTURAL retrieval source requires an anchor type + value."""
        if self.candidate_retrieval_source == CandidateRetrievalSource.STRUCTURAL and (
            self.structural_anchor_type is None or self.structural_anchor_value is None
        ):
            raise ValueError(
                "candidate_retrieval_source=STRUCTURAL requires both "
                "structural_anchor_type and structural_anchor_value"
            )
        return self

    @model_validator(mode="after")
    def _validate_ambiguous_never_active(self) -> "DecisionAuditNode":
        """Rule A3-7: AMBIGUOUS never auto-executes; it must be PENDING_HITL."""
        if self.action == ReconciliationAction.AMBIGUOUS and self.status != DecisionStatus.PENDING_HITL:
            raise ValueError(
                "action AMBIGUOUS must have status PENDING_HITL — it never "
                f"auto-executes (got status={self.status})"
            )
        return self


# ---------------------------------------------------------------------------
# 10. ContradictionNode
# ---------------------------------------------------------------------------


class ContradictionNode(TemporalNode):
    """See Schema.md §10 ContradictionNode."""

    belief_a_id: str = Field(min_length=1)
    belief_b_id: str = Field(min_length=1)
    contradiction_summary: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    resolution_status: ContradictionResolutionStatus = ContradictionResolutionStatus.UNRESOLVED
    resolved_at: datetime | None = None
    resolution_decision_id: str | None = None

    @model_validator(mode="after")
    def _validate_distinct_beliefs(self) -> "ContradictionNode":
        """Rule A3-10: a contradiction cannot link a belief to itself."""
        if self.belief_a_id == self.belief_b_id:
            raise ValueError("belief_a_id and belief_b_id must refer to different beliefs")
        return self

    @model_validator(mode="after")
    def _validate_resolution_timestamp(self) -> "ContradictionNode":
        """Rule A3-10: a resolved contradiction must record when it resolved."""
        if self.resolution_status != ContradictionResolutionStatus.UNRESOLVED and self.resolved_at is None:
            raise ValueError(
                f"resolution_status={self.resolution_status} requires resolved_at"
            )
        return self


# ---------------------------------------------------------------------------
# 11. MacroextractionReportNode
# ---------------------------------------------------------------------------


class MacroextractionReportNode(LumenNode):
    """
    See Schema.md §11 MacroextractionReportNode.

    Extends LumenNode (not TemporalNode) — no valid_from in the Kuzu DDL.
    report_content stays an untyped dict; its schema is defined in
    Extraction/Macroextraction.md and typed in Goal 17.
    """

    report_type: ReportType
    period_start: datetime
    period_end: datetime
    episodes_analyzed: int = Field(ge=0)
    archetype_shift_detected: bool = False
    model_used: str = Field(min_length=1)
    status: ReportStatus = ReportStatus.IMMUTABLE
    report_content: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_period_ordering(self) -> "MacroextractionReportNode":
        if self.period_end < self.period_start:
            raise ValueError("period_end must not precede period_start")
        return self


# ---------------------------------------------------------------------------
# 12. OpenLoopNode
# ---------------------------------------------------------------------------


class OpenLoopNode(TemporalNode):
    """See Schema.md §12 OpenLoopNode."""

    loop_description: str = Field(min_length=1)
    loop_category: LoopCategory
    provenance: Provenance
    source_episode_id: str = Field(min_length=1)
    resolution_status: LoopResolutionStatus = LoopResolutionStatus.OPEN
    resolved_at: datetime | None = None
    resolution_summary: str | None = None
    last_referenced_at: datetime
    linked_patterns: list[str] = Field(default_factory=list)
    linked_beliefs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_resolution_timestamp(self) -> "OpenLoopNode":
        if self.resolution_status != LoopResolutionStatus.OPEN and self.resolved_at is None:
            raise ValueError(
                f"resolution_status={self.resolution_status} requires resolved_at"
            )
        return self


__all__ = [
    "LifecycleHistoryEntry",
    "RollbackPointer",
    "EpisodeNode",
    "ObservationNode",
    "EventNode",
    "SessionNode",
    "CausalChainNode",
    "CausalStepNode",
    "PatternNode",
    "BeliefNode",
    "LessonNode",
    "AdoptedPrincipleNode",
    "PersonEntityNode",
    "DecisionAuditNode",
    "ContradictionNode",
    "MacroextractionReportNode",
    "OpenLoopNode",
]
