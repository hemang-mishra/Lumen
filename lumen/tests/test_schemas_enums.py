"""
Test suite for lumen/schemas/enums.py.

Verifies every closed vocabulary matches its source doc exactly, and that
the StrEnum round-trip (parsing raw LLM JSON strings, serializing back) works.
"""

from __future__ import annotations

from enum import StrEnum

from lumen.schemas import enums as E


class TestObservationTypeParity:
    """The Enum Dictionary must exactly match Microextraction.md's updated list."""

    DOC_TYPES = {
        "CONTEXT", "CONTEXT_SEVERANCE", "EMOTION", "SOMATIC_STATE",
        "SOMATIC_CATHARSIS", "ANTICIPATORY_ANXIETY", "COGNITIVE_FRICTION",
        "TRIGGER_CATALYST", "PROSODY_SIGNAL", "ENVIRONMENTAL_REANCHORING",
        "COGNITIVE_DEFENSE_MECHANISM", "INTERVENTION_APPLIED",
        "ENERGY_SPIKE_EVENT", "SUPPRESSED_EMOTION_SURFACING",
        "SUBPERSONALITY_ACTION", "ERA_INTEGRATION_STATE", "RUMINATION_LOOP",
        "PHYSIOLOGICAL_CAPACITY_STATE", "IDENTITY_AFFINITY",
        "IDENTITY_FUSION_STATE", "EXISTENTIAL_REFLECTION",
        "ACCEPTANCE_ACKNOWLEDGEMENT", "CORE_WOUND", "SYSTEM_DESIGN_ITERATION",
        "SELF_NARRATION_PATTERN", "SOCIAL_PERFORMANCE_STATE",
        "BIOGRAPHICAL_GAP", "INAUTHENTICITY_STATE", "EPISTEMIC_SHIFT",
        "CONCEPTUAL_REFRAME", "LEXICON_UPDATE", "META_BELIEF",
        "COGNITIVE_DISTORTION", "COGNITIVE_DISTORTION_STATE",
        "METACOGNITIVE_INTERRUPT", "METACOGNITIVE_BREAKTHROUGH",
        "PERSPECTIVE_SHIFT", "CORE_CONFLICT", "BELIEF", "LESSON", "PATTERN",
        "OPEN_LOOP", "ENVIRONMENTAL_CONTEXT", "ENVIRONMENTAL_DEPENDENCY",
        "OTHER_PERSON_MODEL", "RELATIONAL_DYNAMIC", "GRATITUDE_APPRECIATION",
    }

    def test_matches_doc_exactly(self):
        implemented = {m.value for m in E.ObservationType}
        assert implemented == self.DOC_TYPES

    def test_includes_the_three_added_types(self):
        assert E.ObservationType.COGNITIVE_DISTORTION_STATE == "COGNITIVE_DISTORTION_STATE"
        assert E.ObservationType.EXISTENTIAL_REFLECTION == "EXISTENTIAL_REFLECTION"
        assert E.ObservationType.IDENTITY_FUSION_STATE == "IDENTITY_FUSION_STATE"


class TestHighSignalRequiredTypes:
    def test_contains_documented_mandatory_floor_types(self):
        assert E.ObservationType.SUPPRESSED_EMOTION_SURFACING in E.HIGH_SIGNAL_REQUIRED_TYPES
        assert E.ObservationType.METACOGNITIVE_INTERRUPT in E.HIGH_SIGNAL_REQUIRED_TYPES
        assert E.ObservationType.METACOGNITIVE_BREAKTHROUGH in E.HIGH_SIGNAL_REQUIRED_TYPES
        assert E.ObservationType.PROSODY_SIGNAL in E.HIGH_SIGNAL_REQUIRED_TYPES

    def test_contains_the_two_new_high_sensitivity_types(self):
        assert E.ObservationType.IDENTITY_FUSION_STATE in E.HIGH_SIGNAL_REQUIRED_TYPES
        assert E.ObservationType.EXISTENTIAL_REFLECTION in E.HIGH_SIGNAL_REQUIRED_TYPES

    def test_does_not_contain_a_standard_type(self):
        assert E.ObservationType.CONTEXT not in E.HIGH_SIGNAL_REQUIRED_TYPES


class TestDomainEnum:
    def test_matches_full_expanded_list_exactly(self):
        """
        Section A5: SELF_CONCEPT added to close the BeliefNode-example gap.
        FINANCIAL, SPIRITUALITY, RECREATIONAL, ENVIRONMENTAL added per
        explicit user decision to close life-domain coverage gaps.
        Schema.md's PatternNode domain comment was updated to match.
        """
        assert {m.value for m in E.Domain} == {
            "COGNITIVE_STYLE", "EMOTIONAL", "BEHAVIORAL", "RELATIONAL",
            "CAREER", "HEALTH", "SELF_CONCEPT", "FINANCIAL", "SPIRITUALITY",
            "RECREATIONAL", "ENVIRONMENTAL",
        }

    def test_includes_documented_pattern_domains(self):
        for value in ("COGNITIVE_STYLE", "EMOTIONAL", "BEHAVIORAL", "RELATIONAL", "CAREER", "HEALTH"):
            assert E.Domain(value).value == value


class TestModelRole:
    def test_names_every_job_a_model_can_be_hired_for(self):
        """
        Each member is a job, not a place a model runs.

        CONVERSATION is separate from THINKING because writing a warm reply
        in under a second and doing the overnight extraction reasoning need
        different models, and tying them together means every improvement to
        one is a regression to the other.
        """
        assert {m.value for m in E.ModelRole} == {
            "LIGHTWEIGHT",
            "THINKING",
            "CONVERSATION",
            "EMBEDDING",
            "TRANSCRIPTION",
            "TTS",
        }

    def test_carries_no_privacy_or_locality_members(self):
        role_values = {m.value for m in E.ModelRole}
        assert "STANDARD" not in role_values
        assert "HIGH_SECURITY" not in role_values


class TestCausalStepType:
    def test_matches_doc(self):
        assert {m.value for m in E.CausalStepType} == {
            "TRIGGER", "INTERNAL_STATE", "ACTION", "OUTCOME", "LESSON"
        }


class TestReconciliationAction:
    def test_has_all_eight_actions(self):
        assert {m.value for m in E.ReconciliationAction} == {
            "MERGE", "REINFORCE", "EVOLVE", "BRANCH", "CONTRADICT",
            "DIALECTIC", "REGULATE", "AMBIGUOUS",
        }


class TestDecisionStatus:
    def test_has_all_seven_statuses(self):
        # DISMISSED is neither active nor waiting: no change was made, and
        # the question has been withdrawn rather than settled.
        assert {m.value for m in E.DecisionStatus} == {
            "ACTIVE", "ROLLED_BACK", "PENDING_HITL", "BELOW_THRESHOLD",
            "SUSPENDED_QUEUE_FULL", "EXTRACTION_FAILED", "DISMISSED",
        }


class TestAdoptedPrincipleLifecycleState:
    def test_has_all_four_states(self):
        assert {m.value for m in E.LifecycleState} == {
            "TRYING", "INTERNALIZED", "SUSPENDED", "ABANDONED"
        }


class TestStrEnumRoundTrip:
    """Every enum must parse raw LLM JSON strings and serialize back identically."""

    ALL_ENUMS = [
        E.SignalStrength, E.Provenance, E.VerificationStatus,
        E.ExtractionConfidence, E.Domain, E.ModelRole, E.NodeStatus,
        E.ObservationStatus, E.LifecycleNodeStatus, E.DecisionStatus,
        E.ReportStatus, E.ObservationType, E.CausalStepType,
        E.SourceModality, E.EntryClass, E.QualityGateDecision,
        E.ReconciliationStatus, E.ReconciliationAction, E.PrincipleDomain,
        E.LifecycleState, E.RelationshipToUser, E.SentimentTrend,
        E.ContradictionResolutionStatus, E.LoopCategory,
        E.LoopResolutionStatus, E.ReportType, E.CandidateRetrievalSource,
        E.StructuralAnchorType, E.HitlResolutionChoice, E.DialogueAct,
    ]

    def test_all_are_strenum(self):
        for enum_cls in self.ALL_ENUMS:
            assert issubclass(enum_cls, StrEnum)

    def test_parse_and_serialize_round_trip(self):
        for enum_cls in self.ALL_ENUMS:
            for member in enum_cls:
                assert enum_cls(member.value) is member
                assert str(member.value) == member.value
