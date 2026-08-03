"""
Closed vocabularies for the Lumen knowledge graph.

Every enum here corresponds to a fixed dictionary named in the docs. An LLM
(or any caller) cannot introduce a category that isn't listed here — that is
the entire point of the Enum Dictionary pattern (see Microextraction.md
"Structural Anchor").

See: docs/Graph/Schema.md, docs/Extraction/Microextraction.md,
     docs/Extraction/Reconciliation.md, docs/Extraction/Architecture.md,
     docs/Extraction/Preprocessing.md, docs/hld/Technical_HLD.md Section 5
"""

from __future__ import annotations

from enum import StrEnum

# ---------------------------------------------------------------------------
# Cross-cutting enums — shared by many node types
# ---------------------------------------------------------------------------


class SignalStrength(StrEnum):
    """Retrieval priority multiplier. See Architecture.md Signal Weight Multipliers."""

    STANDARD = "STANDARD"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Provenance(StrEnum):
    """Who originated this content. See Microextraction.md observations array."""

    USER_GENERATED = "USER_GENERATED"
    AI_GENERATED = "AI_GENERATED"
    CO_CREATED = "CO_CREATED"


class VerificationStatus(StrEnum):
    """Gates retrieval trust_weight. See Architecture.md Trust Weight."""

    IMPLICIT = "IMPLICIT"
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"


class ExtractionConfidence(StrEnum):
    """See Schema.md ObservationNode.extraction_confidence."""

    STANDARD = "STANDARD"
    RECONSTRUCTIVE = "RECONSTRUCTIVE"


class Domain(StrEnum):
    """
    Pattern/Belief/Lesson domain classification.

    Base list is Schema.md's original PatternNode domain enum
    (COGNITIVE_STYLE..HEALTH) plus SELF_CONCEPT, which Schema.md's own
    BeliefNode example used but which was absent from the documented list.

    FINANCIAL, SPIRITUALITY, RECREATIONAL, and ENVIRONMENTAL were added on
    top of that per explicit user decision (implementation/Goal_2_Plan.md
    Section A5) to close life-domain gaps: money beliefs had no home;
    SPIRITUALITY gives EXISTENTIAL_REFLECTION / CORE_WOUND-derived beliefs
    a non-SELF_CONCEPT home; ENVIRONMENTAL matches the existing
    ENVIRONMENTAL_CONTEXT / ENVIRONMENTAL_DEPENDENCY observation types.
    Schema.md's PatternNode domain comment was updated to match.
    """

    COGNITIVE_STYLE = "COGNITIVE_STYLE"
    EMOTIONAL = "EMOTIONAL"
    BEHAVIORAL = "BEHAVIORAL"
    RELATIONAL = "RELATIONAL"
    CAREER = "CAREER"
    HEALTH = "HEALTH"
    SELF_CONCEPT = "SELF_CONCEPT"
    FINANCIAL = "FINANCIAL"
    SPIRITUALITY = "SPIRITUALITY"
    RECREATIONAL = "RECREATIONAL"
    ENVIRONMENTAL = "ENVIRONMENTAL"


class ModelRole(StrEnum):
    """
    Model-capability role used by the single-point-of-configuration LLM/AI
    provider abstraction (lumen.config.ProviderConfig).

    Replaces the earlier RoutingTier (STANDARD/HIGH_SECURITY) concept, which
    conflated two independent things: what kind of model a task needs
    (fast vs. deep-reasoning), and where that model runs (cloud vs. local).
    Per explicit user decision, the abstraction only knows about the former.
    An operator wanting guaranteed-local processing configures every role
    to a local provider in ProviderConfig — the abstraction itself never
    inspects content sensitivity or forces a deployment locality.

    See: implementation/Goal_2_Plan.md, docs/hld/LLM_Abstraction_Architecture.md
    """

    LIGHTWEIGHT = "LIGHTWEIGHT"
    THINKING = "THINKING"
    EMBEDDING = "EMBEDDING"
    TRANSCRIPTION = "TRANSCRIPTION"
    TTS = "TTS"


# ---------------------------------------------------------------------------
# Per-node status enums — deliberately NOT unified into one Status enum,
# since valid values differ per node type (e.g. IMMUTABLE only applies to
# MacroextractionReportNode).
# ---------------------------------------------------------------------------


class NodeStatus(StrEnum):
    """Generic active/suspended status for nodes with no richer lifecycle."""

    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class ObservationStatus(StrEnum):
    """See Schema.md ObservationNode.status."""

    ACTIVE = "ACTIVE"
    RAW_CAPTURE = "RAW_CAPTURE"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    SUSPENDED = "SUSPENDED"


class LifecycleNodeStatus(StrEnum):
    """See Schema.md PatternNode.status / BeliefNode.status."""

    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    SUPPRESSED = "SUPPRESSED"


class DecisionStatus(StrEnum):
    """See Schema.md DecisionAuditNode status lifecycle."""

    ACTIVE = "ACTIVE"
    ROLLED_BACK = "ROLLED_BACK"
    PENDING_HITL = "PENDING_HITL"
    BELOW_THRESHOLD = "BELOW_THRESHOLD"
    SUSPENDED_QUEUE_FULL = "SUSPENDED_QUEUE_FULL"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"


class ReportStatus(StrEnum):
    """See Schema.md MacroextractionReportNode.status."""

    IMMUTABLE = "IMMUTABLE"


# ---------------------------------------------------------------------------
# Domain-specific enums
# ---------------------------------------------------------------------------


class ObservationType(StrEnum):
    """
    The Enum Dictionary (Structural Anchor). See Microextraction.md.

    """

    # Core Experience Types
    CONTEXT = "CONTEXT"
    CONTEXT_SEVERANCE = "CONTEXT_SEVERANCE"
    EMOTION = "EMOTION"
    SOMATIC_STATE = "SOMATIC_STATE"
    SOMATIC_CATHARSIS = "SOMATIC_CATHARSIS"
    ANTICIPATORY_ANXIETY = "ANTICIPATORY_ANXIETY"
    COGNITIVE_FRICTION = "COGNITIVE_FRICTION"
    TRIGGER_CATALYST = "TRIGGER_CATALYST"
    PROSODY_SIGNAL = "PROSODY_SIGNAL"

    # Coping & Response Types
    ENVIRONMENTAL_REANCHORING = "ENVIRONMENTAL_REANCHORING"
    COGNITIVE_DEFENSE_MECHANISM = "COGNITIVE_DEFENSE_MECHANISM"
    INTERVENTION_APPLIED = "INTERVENTION_APPLIED"
    ENERGY_SPIKE_EVENT = "ENERGY_SPIKE_EVENT"
    SUPPRESSED_EMOTION_SURFACING = "SUPPRESSED_EMOTION_SURFACING"

    # Self-Model Types
    SUBPERSONALITY_ACTION = "SUBPERSONALITY_ACTION"
    ERA_INTEGRATION_STATE = "ERA_INTEGRATION_STATE"
    RUMINATION_LOOP = "RUMINATION_LOOP"
    PHYSIOLOGICAL_CAPACITY_STATE = "PHYSIOLOGICAL_CAPACITY_STATE"
    IDENTITY_AFFINITY = "IDENTITY_AFFINITY"
    IDENTITY_FUSION_STATE = "IDENTITY_FUSION_STATE"
    EXISTENTIAL_REFLECTION = "EXISTENTIAL_REFLECTION"
    ACCEPTANCE_ACKNOWLEDGEMENT = "ACCEPTANCE_ACKNOWLEDGEMENT"
    CORE_WOUND = "CORE_WOUND"
    SYSTEM_DESIGN_ITERATION = "SYSTEM_DESIGN_ITERATION"
    SELF_NARRATION_PATTERN = "SELF_NARRATION_PATTERN"
    SOCIAL_PERFORMANCE_STATE = "SOCIAL_PERFORMANCE_STATE"
    BIOGRAPHICAL_GAP = "BIOGRAPHICAL_GAP"
    INAUTHENTICITY_STATE = "INAUTHENTICITY_STATE"

    # Cognitive & Belief Types
    EPISTEMIC_SHIFT = "EPISTEMIC_SHIFT"
    CONCEPTUAL_REFRAME = "CONCEPTUAL_REFRAME"
    LEXICON_UPDATE = "LEXICON_UPDATE"
    META_BELIEF = "META_BELIEF"
    COGNITIVE_DISTORTION = "COGNITIVE_DISTORTION"
    COGNITIVE_DISTORTION_STATE = "COGNITIVE_DISTORTION_STATE"
    METACOGNITIVE_INTERRUPT = "METACOGNITIVE_INTERRUPT"
    METACOGNITIVE_BREAKTHROUGH = "METACOGNITIVE_BREAKTHROUGH"
    PERSPECTIVE_SHIFT = "PERSPECTIVE_SHIFT"
    CORE_CONFLICT = "CORE_CONFLICT"
    BELIEF = "BELIEF"
    LESSON = "LESSON"

    # Pattern & Environment Types
    PATTERN = "PATTERN"
    OPEN_LOOP = "OPEN_LOOP"
    ENVIRONMENTAL_CONTEXT = "ENVIRONMENTAL_CONTEXT"
    ENVIRONMENTAL_DEPENDENCY = "ENVIRONMENTAL_DEPENDENCY"

    # Relational Types
    OTHER_PERSON_MODEL = "OTHER_PERSON_MODEL"
    RELATIONAL_DYNAMIC = "RELATIONAL_DYNAMIC"
    GRATITUDE_APPRECIATION = "GRATITUDE_APPRECIATION"


# Rule A3-1: these observation types carry a mandatory HIGH/CRITICAL signal
# floor per Microextraction.md and Architecture.md's Validation Layer.
HIGH_SIGNAL_REQUIRED_TYPES: frozenset[ObservationType] = frozenset(
    {
        ObservationType.SUPPRESSED_EMOTION_SURFACING,
        ObservationType.METACOGNITIVE_INTERRUPT,
        ObservationType.METACOGNITIVE_BREAKTHROUGH,
        ObservationType.PROSODY_SIGNAL,
        ObservationType.IDENTITY_FUSION_STATE,
        ObservationType.EXISTENTIAL_REFLECTION,
    }
)


class CausalStepType(StrEnum):
    """See Microextraction.md causal_mechanisms array."""

    TRIGGER = "TRIGGER"
    INTERNAL_STATE = "INTERNAL_STATE"
    ACTION = "ACTION"
    OUTCOME = "OUTCOME"
    LESSON = "LESSON"


class SourceModality(StrEnum):
    """See Schema.md EpisodeNode.source_modality."""

    VOICE_NOTE = "VOICE_NOTE"
    TEXT_ENTRY = "TEXT_ENTRY"


class EntryClass(StrEnum):
    """See Preprocessing.md Quality Gate Routing."""

    REFLECTION = "REFLECTION"
    RAW_CAPTURE = "RAW_CAPTURE"


class QualityGateDecision(StrEnum):
    """See Technical_HLD.md Section 5 PreprocessingResult.quality_gate_decision."""

    REFLECTION = "REFLECTION"
    RAW_CAPTURE = "RAW_CAPTURE"
    DISCARD = "DISCARD"


class ReconciliationStatus(StrEnum):
    """See Schema.md EpisodeNode.reconciliation_status."""

    COMPLETE = "COMPLETE"
    SUSPENDED = "SUSPENDED"
    PENDING_RERECONCILIATION = "PENDING_RERECONCILIATION"


class ReconciliationAction(StrEnum):
    """The 8 Reconciliation actions. See Reconciliation.md."""

    MERGE = "MERGE"
    REINFORCE = "REINFORCE"
    EVOLVE = "EVOLVE"
    BRANCH = "BRANCH"
    CONTRADICT = "CONTRADICT"
    DIALECTIC = "DIALECTIC"
    REGULATE = "REGULATE"
    AMBIGUOUS = "AMBIGUOUS"


class PrincipleDomain(StrEnum):
    """See Schema.md AdoptedPrincipleNode.domain — distinct vocabulary from Domain."""

    PRODUCTIVITY = "PRODUCTIVITY"
    HEALTH = "HEALTH"
    RELATIONAL = "RELATIONAL"
    COGNITIVE = "COGNITIVE"
    IDENTITY = "IDENTITY"


class LifecycleState(StrEnum):
    """See Schema.md AdoptedPrincipleNode Lifecycle States table."""

    TRYING = "TRYING"
    INTERNALIZED = "INTERNALIZED"
    SUSPENDED = "SUSPENDED"
    ABANDONED = "ABANDONED"


class RelationshipToUser(StrEnum):
    """See Schema.md PersonEntityNode.relationship_to_user."""

    COLLEAGUE = "COLLEAGUE"
    FRIEND = "FRIEND"
    FAMILY = "FAMILY"
    MANAGER = "MANAGER"
    PARTNER = "PARTNER"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class SentimentTrend(StrEnum):
    """See Schema.md PersonEntityNode.relationship_sentiment_trend."""

    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEUTRAL_TO_NEGATIVE = "NEUTRAL_TO_NEGATIVE"
    NEGATIVE = "NEGATIVE"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class ContradictionResolutionStatus(StrEnum):
    """See Schema.md ContradictionNode.resolution_status."""

    UNRESOLVED = "UNRESOLVED"
    RESOLVED_EVOLVE = "RESOLVED_EVOLVE"
    RESOLVED_USER = "RESOLVED_USER"
    RESOLVED_MACRO = "RESOLVED_MACRO"


class LoopCategory(StrEnum):
    """See Schema.md OpenLoopNode.loop_category."""

    CAREER_IDENTITY = "CAREER_IDENTITY"
    RELATIONSHIP = "RELATIONSHIP"
    SELF_CONCEPT = "SELF_CONCEPT"
    VALUES = "VALUES"
    HEALTH = "HEALTH"
    OTHER = "OTHER"


class LoopResolutionStatus(StrEnum):
    """See Schema.md OpenLoopNode.resolution_status."""

    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DISSOLVED = "DISSOLVED"


class ReportType(StrEnum):
    """See Schema.md MacroextractionReportNode.report_type."""

    SHADOW = "SHADOW"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"


class CandidateRetrievalSource(StrEnum):
    """See Schema.md DecisionAuditNode.candidate_retrieval_source."""

    SEMANTIC = "SEMANTIC"
    STRUCTURAL = "STRUCTURAL"


class StructuralAnchorType(StrEnum):
    """See Schema.md DecisionAuditNode.structural_anchor_type."""

    NAMED_PERSON = "NAMED_PERSON"
    HISTORICAL_ERA = "HISTORICAL_ERA"


class HitlResolutionChoice(StrEnum):
    """See Schema.md DecisionAuditNode.hitl_resolution_user_choice."""

    ACTION_A = "ACTION_A"
    ACTION_B = "ACTION_B"
    CREATE_NEW = "CREATE_NEW"
    AUTO_BRANCH_AFTER_SNOOZE = "AUTO_BRANCH_AFTER_SNOOZE"


class DialogueAct(StrEnum):
    """See Preprocessing.md Dialogue Act Classification."""

    OPERATIONAL_REQUEST = "OPERATIONAL_REQUEST"
    EXPRESSIVE = "EXPRESSIVE"


__all__ = [
    "SignalStrength",
    "Provenance",
    "VerificationStatus",
    "ExtractionConfidence",
    "Domain",
    "ModelRole",
    "NodeStatus",
    "ObservationStatus",
    "LifecycleNodeStatus",
    "DecisionStatus",
    "ReportStatus",
    "ObservationType",
    "HIGH_SIGNAL_REQUIRED_TYPES",
    "CausalStepType",
    "SourceModality",
    "EntryClass",
    "QualityGateDecision",
    "ReconciliationStatus",
    "ReconciliationAction",
    "PrincipleDomain",
    "LifecycleState",
    "RelationshipToUser",
    "SentimentTrend",
    "ContradictionResolutionStatus",
    "LoopCategory",
    "LoopResolutionStatus",
    "ReportType",
    "CandidateRetrievalSource",
    "StructuralAnchorType",
    "HitlResolutionChoice",
    "DialogueAct",
]
