"""
Pipeline stage data transfer objects — the typed hand-offs between Stage 0
through Stage 3 of the extraction pipeline.

Each stage accepts one of these models as input and returns another as
output (HLD Rule 2: pipeline stages are pure functions). The orchestrator
(Goal 10) is the only component that chains them together.

Five top-level models are named explicitly in Technical_HLD.md Section 5:
SessionDecayEvent, PreprocessingResult, ExtractionResult, RetrievalResult,
ReconciliationResult. Two more are referenced there but never defined
(BufferMessage, CandidateNode) — reconstructed here from their usage.
Four supporting sub-models (CoreferenceMap, ResolvedEntity, AmbiguousRef,
PreprocessedEpisode) are reconstructed from Preprocessing.md, whose JSON
examples this module mirrors directly.

See: docs/hld/Technical_HLD.md Section 5, docs/Extraction/Preprocessing.md
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lumen.schemas.enums import (
    CandidateRetrievalSource,
    DialogueAct,
    EntryClass,
    QualityGateDecision,
    ReconciliationAction,
    StructuralAnchorType,
)
from lumen.schemas.nodes import (
    CausalChainNode,
    CausalStepNode,
    EventNode,
    ObservationNode,
    SessionNode,
)

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class PipelineDTO(BaseModel):
    """
    Base for the top-level stage contracts. trace_id carries the per-session
    UUID from Technical_HLD.md Section 10 through every stage — populated by
    Goal 3b; left optional here so this goal's DTOs are constructible without
    that infrastructure existing yet.
    """

    model_config = ConfigDict(extra="forbid")

    trace_id: str | None = None


# ---------------------------------------------------------------------------
# Supporting sub-models
# ---------------------------------------------------------------------------


class BufferMessage(BaseModel):
    """
    One message in the Session Buffer. Referenced as SessionDecayEvent's
    raw_buffer in Technical_HLD.md Section 5 but not itself defined there;
    reconstructed from Preprocessing.md's Dialogue Act Classification and
    CO_CREATED Marker Detection description.
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
    """One resolved coreference. See Preprocessing.md §4 Coreference Pre-Pass."""

    model_config = ConfigDict(extra="forbid")

    span: str = Field(min_length=1)
    resolved_to: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    resolution_basis: str = Field(min_length=1)


class AmbiguousRef(BaseModel):
    """An unresolved coreference span. See Preprocessing.md §4."""

    model_config = ConfigDict(extra="forbid")

    span: str = Field(min_length=1)
    candidates: list[str] = Field(min_length=2)
    reason: str = Field(min_length=1)


class CoreferenceMap(BaseModel):
    """
    The coreference_map JSON object from Preprocessing.md §4 — produced once
    in Stage 0, consumed directly by Stage 1 (no re-derivation).
    """

    model_config = ConfigDict(extra="forbid")

    entry_id: str = Field(min_length=1)
    resolved_entities: list[ResolvedEntity] = Field(default_factory=list)
    ambiguous_refs: list[AmbiguousRef] = Field(default_factory=list)


class PreprocessedEpisode(BaseModel):
    """
    One conceptual episode after Stage 0 cleaning, ready for Stage 1
    Microextraction. Reconstructed from Preprocessing.md's completeness
    scoring and quality-gate routing, and Schema.md's EpisodeNode fields
    that Stage 0 is responsible for producing.
    """

    model_config = ConfigDict(extra="forbid")

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
    One retrieval candidate surfaced by Stage 2. Referenced as
    RetrievalResult's pass_a_candidates/pass_b_candidates in
    Technical_HLD.md Section 5 but not itself defined there; reconstructed
    from Architecture.md's Stage 2 description (semantic vs. structural
    retrieval, structural anchors).
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
        """Semantic candidates carry a similarity score; structural ones don't (Architecture.md)."""
        if self.retrieval_source == CandidateRetrievalSource.SEMANTIC and self.similarity_score is None:
            raise ValueError("SEMANTIC candidates require a similarity_score")
        return self


# ---------------------------------------------------------------------------
# Top-level stage DTOs (Technical_HLD.md Section 5)
# ---------------------------------------------------------------------------


class SessionDecayEvent(PipelineDTO):
    """Fires when a session decays (1hr inactivity) and enters the pipeline."""

    session_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    event_date: date
    message_count: int = Field(ge=0)
    raw_buffer: list[BufferMessage] = Field(default_factory=list)
    triggered_at: datetime


class PreprocessingResult(PipelineDTO):
    """Output of Stage 0. See Preprocessing.md."""

    session_id: str = Field(min_length=1)
    episodes: list[PreprocessedEpisode] = Field(default_factory=list)
    coreference_map: CoreferenceMap
    quality_gate_decision: QualityGateDecision
    processing_time_ms: int = Field(ge=0)
    pending_reflections: list[str] = Field(default_factory=list)


class ExtractionResult(PipelineDTO):
    """
    Output of Stage 1. Extended beyond Technical_HLD.md's `observations`-only
    sketch with events/sessions/causal_chains/causal_steps, since
    Microextraction.md's schema also produces EventNode, SessionNode, and
    CausalChainNode/CausalStepNode instances per episode, not just
    observations.
    """

    episode_id: str = Field(min_length=1)
    observations: list[ObservationNode] = Field(default_factory=list)
    events: list[EventNode] = Field(default_factory=list)
    sessions: list[SessionNode] = Field(default_factory=list)
    causal_chains: list[CausalChainNode] = Field(default_factory=list)
    causal_steps: list[CausalStepNode] = Field(default_factory=list)
    extraction_model: str = Field(min_length=1)
    validation_passed: bool
    retry_count: int = Field(default=0, ge=0)


class RetrievalResult(PipelineDTO):
    """
    Output of Stage 2. See Architecture.md Stage 2 merge rule: the combined
    Pass A + Pass B candidate set is capped at 8 nodes after deduplication
    by node_id.
    """

    observation_id: str = Field(min_length=1)
    pass_a_candidates: list[CandidateNode] = Field(default_factory=list)
    pass_b_candidates: list[CandidateNode] = Field(default_factory=list)
    retrieval_time_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_candidate_cap(self) -> "RetrievalResult":
        """Architecture.md merge rule: max 8 deduplicated candidates."""
        merged_ids = {c.node_id for c in self.pass_a_candidates} | {
            c.node_id for c in self.pass_b_candidates
        }
        if len(merged_ids) > 8:
            raise ValueError(
                f"merged candidate set has {len(merged_ids)} unique nodes; "
                "Architecture.md caps the Pass A + Pass B merge at 8"
            )
        return self


class ReconciliationResult(PipelineDTO):
    """Output of Stage 3. See Reconciliation.md."""

    observation_id: str = Field(min_length=1)
    action: ReconciliationAction
    target_node_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    delta_description: str | None = None
    decision_model: str = Field(min_length=1)
    escalated_to_hitl: bool
    audit_node_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_evolve_requires_delta(self) -> "ReconciliationResult":
        """Same rule as DecisionAuditNode: EVOLVE requires delta_description."""
        if self.action == ReconciliationAction.EVOLVE and not self.delta_description:
            raise ValueError("action EVOLVE requires a non-null delta_description")
        return self


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
    "ExtractionResult",
    "RetrievalResult",
    "ReconciliationResult",
]
