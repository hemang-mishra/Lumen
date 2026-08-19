"""
Test suite for lumen/schemas/nodes.py.

One happy-path construction test per node type (via the conftest fixtures),
plus one rejection test per rule in implementation/Goal_2_Plan.md Section A3.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from lumen.schemas.enums import (
    CandidateRetrievalSource,
    ContradictionResolutionStatus,
    DecisionStatus,
    Domain,
    LifecycleState,
    LoopResolutionStatus,
    ObservationStatus,
    ObservationType,
    Provenance,
    ReconciliationAction,
    RelationshipToUser,
    SentimentTrend,
    SignalStrength,
)
from lumen.schemas.nodes import (
    AdoptedPrincipleNode,
    BeliefNode,
    CausalStepNode,
    ContradictionNode,
    DecisionAuditNode,
    EpisodeNode,
    EventNode,
    LessonNode,
    LifecycleHistoryEntry,
    MacroextractionReportNode,
    ObservationNode,
    OpenLoopNode,
    PatternNode,
    PersonEntityNode,
    RollbackPointer,
    SessionNode,
)

NOW = datetime(2026, 6, 11, 10, 30, 0)


# ---------------------------------------------------------------------------
# Happy path: every node type constructs from its fixture and round-trips
# through to_graph_dict()
# ---------------------------------------------------------------------------


class TestHappyPathConstruction:
    FIXTURE_NAMES = [
        "sample_episode", "sample_observation", "sample_event", "sample_session",
        "sample_causal_chain", "sample_causal_step", "sample_pattern",
        "sample_belief", "sample_lesson", "sample_adopted_principle",
        "sample_person", "sample_decision_audit", "sample_contradiction",
        "sample_macro_report", "sample_open_loop",
    ]

    @pytest.mark.parametrize("fixture_name", FIXTURE_NAMES)
    def test_constructs_and_serializes(self, fixture_name, request):
        node = request.getfixturevalue(fixture_name)
        graph_dict = node.to_graph_dict()
        assert graph_dict["node_id"] == node.node_id
        # every value must be a Kuzu-STRING-safe primitive: str, int, float, bool
        for value in graph_dict.values():
            assert isinstance(value, (str, int, float, bool))


# ---------------------------------------------------------------------------
# extra="forbid" — Section A2 decision 4
# ---------------------------------------------------------------------------


class TestExtraFieldsForbidden:
    def test_unknown_field_on_observation_rejected(self, sample_observation):
        data = sample_observation.model_dump()
        data["emotional_intensity"] = 0.8
        with pytest.raises(ValidationError):
            ObservationNode(**data)

    def test_unknown_field_on_pattern_rejected(self, sample_pattern):
        data = sample_pattern.model_dump()
        data["made_up_field"] = "x"
        with pytest.raises(ValidationError):
            PatternNode(**data)


# ---------------------------------------------------------------------------
# Alias acceptance — Section A2/decision on LLM field names
# ---------------------------------------------------------------------------


class TestFieldAliases:
    def test_observation_accepts_extraction_signal_strength_alias(self):
        obs = ObservationNode(
            node_id="obs_1", episode_id="ep_1", occurred_at=NOW, created_at=NOW,
            valid_from=NOW, type=ObservationType.EMOTION, content="x",
            extraction_signal_strength=SignalStrength.STANDARD,
            provenance=Provenance.USER_GENERATED, status=ObservationStatus.ACTIVE,
            extraction_model="gemini",
        )
        assert obs.signal_strength == SignalStrength.STANDARD

    def test_observation_accepts_singular_person_ref_string(self):
        obs = ObservationNode(
            node_id="obs_1", episode_id="ep_1", occurred_at=NOW, created_at=NOW,
            valid_from=NOW, type=ObservationType.EMOTION, content="x",
            signal_strength=SignalStrength.STANDARD, provenance=Provenance.USER_GENERATED,
            status=ObservationStatus.ACTIVE, extraction_model="gemini", person_ref="Jordan",
        )
        assert obs.person_refs == ["Jordan"]

    def test_observation_accepts_null_person_ref(self):
        obs = ObservationNode(
            node_id="obs_1", episode_id="ep_1", occurred_at=NOW, created_at=NOW,
            valid_from=NOW, type=ObservationType.EMOTION, content="x",
            signal_strength=SignalStrength.STANDARD, provenance=Provenance.USER_GENERATED,
            status=ObservationStatus.ACTIVE, extraction_model="gemini", person_ref=None,
        )
        assert obs.person_refs == []

    def test_event_accepts_plural_person_refs_list(self):
        evt = EventNode(
            node_id="evt_1", episode_id="ep_1", occurred_at=NOW, created_at=NOW,
            valid_from=NOW, event_summary="x", signal_strength=SignalStrength.STANDARD,
            person_refs=["Jordan", "Alex"],
        )
        assert evt.person_refs == ["Jordan", "Alex"]


# ---------------------------------------------------------------------------
# Rule A3-1: HIGH-signal-required observation types
# ---------------------------------------------------------------------------


class TestRuleSignalFloor:
    def test_suppressed_emotion_requires_high_signal(self):
        with pytest.raises(ValidationError, match="signal_strength HIGH or CRITICAL"):
            ObservationNode(
                node_id="obs_1", episode_id="ep_1", occurred_at=NOW, created_at=NOW,
                valid_from=NOW, type=ObservationType.SUPPRESSED_EMOTION_SURFACING,
                content="x", signal_strength=SignalStrength.STANDARD,
                provenance=Provenance.USER_GENERATED, status=ObservationStatus.ACTIVE,
                extraction_model="gemini",
            )

    def test_suppressed_emotion_accepts_high_signal(self):
        obs = ObservationNode(
            node_id="obs_1", episode_id="ep_1", occurred_at=NOW, created_at=NOW,
            valid_from=NOW, type=ObservationType.SUPPRESSED_EMOTION_SURFACING,
            content="x", signal_strength=SignalStrength.CRITICAL,
            provenance=Provenance.USER_GENERATED, status=ObservationStatus.ACTIVE,
            extraction_model="gemini",
        )
        assert obs.signal_strength == SignalStrength.CRITICAL

    def test_standard_type_permits_standard_signal(self):
        obs = ObservationNode(
            node_id="obs_1", episode_id="ep_1", occurred_at=NOW, created_at=NOW,
            valid_from=NOW, type=ObservationType.CONTEXT, content="x",
            signal_strength=SignalStrength.STANDARD, provenance=Provenance.USER_GENERATED,
            status=ObservationStatus.ACTIVE, extraction_model="gemini",
        )
        assert obs.signal_strength == SignalStrength.STANDARD


# ---------------------------------------------------------------------------
# Rule A3-2: closed dictionary + non-null signal_strength (structural)
# ---------------------------------------------------------------------------


class TestRuleObservationRequiredFields:
    def test_unknown_observation_type_rejected(self):
        with pytest.raises(ValidationError):
            ObservationNode(
                node_id="obs_1", episode_id="ep_1", occurred_at=NOW, created_at=NOW,
                valid_from=NOW, type="NOT_A_REAL_TYPE", content="x",
                signal_strength=SignalStrength.STANDARD, provenance=Provenance.USER_GENERATED,
                status=ObservationStatus.ACTIVE, extraction_model="gemini",
            )

    def test_missing_signal_strength_rejected(self):
        with pytest.raises(ValidationError):
            ObservationNode(
                node_id="obs_1", episode_id="ep_1", occurred_at=NOW, created_at=NOW,
                valid_from=NOW, type=ObservationType.CONTEXT, content="x",
                provenance=Provenance.USER_GENERATED, status=ObservationStatus.ACTIVE,
                extraction_model="gemini",
            )


# ---------------------------------------------------------------------------
# Rule A3-3: causal step type enum (structural)
# ---------------------------------------------------------------------------


class TestRuleCausalStepType:
    def test_unknown_step_type_rejected(self):
        with pytest.raises(ValidationError):
            CausalStepNode(
                node_id="step_1", chain_id="chain_1", step_index=1,
                step_type="NOT_A_STEP_TYPE", content="x", created_at=NOW,
            )


# ---------------------------------------------------------------------------
# Rule A3-4: version chain (VersionedNode)
# ---------------------------------------------------------------------------


class TestRuleVersionChain:
    def test_version_one_with_previous_version_id_rejected(self, sample_pattern):
        data = sample_pattern.model_dump()
        data["version"] = 1
        data["previous_version_id"] = "pat_something_else"
        with pytest.raises(ValidationError, match="previous_version_id"):
            PatternNode(**data)

    def test_version_two_without_previous_version_id_rejected(self, sample_pattern):
        data = sample_pattern.model_dump()
        data["version"] = 2
        data["previous_version_id"] = None
        with pytest.raises(ValidationError, match="requires a previous_version_id"):
            PatternNode(**data)

    def test_version_zero_rejected(self, sample_pattern):
        data = sample_pattern.model_dump()
        data["version"] = 0
        with pytest.raises(ValidationError):
            PatternNode(**data)

    def test_valid_version_two_with_previous_id(self, sample_pattern):
        data = sample_pattern.model_dump()
        data["version"] = 2
        data["previous_version_id"] = "pat_decision_saturation_v1"
        pat = PatternNode(**data)
        assert pat.version == 2


# ---------------------------------------------------------------------------
# Rule A3-5: EVOLVE requires delta_description (DecisionAuditNode)
# ---------------------------------------------------------------------------


class TestRuleEvolveRequiresDelta:
    def test_evolve_without_delta_rejected(self, sample_decision_audit):
        data = sample_decision_audit.model_dump()
        data["action"] = "EVOLVE"
        data["delta_description"] = None
        with pytest.raises(ValidationError, match="delta_description"):
            DecisionAuditNode(**data)

    def test_evolve_with_delta_accepted(self, sample_decision_audit):
        data = sample_decision_audit.model_dump()
        data["action"] = "EVOLVE"
        data["delta_description"] = "Belief shifted to include group contexts."
        audit = DecisionAuditNode(**data)
        assert audit.action == ReconciliationAction.EVOLVE

    def test_merge_without_delta_is_fine(self, sample_decision_audit):
        assert sample_decision_audit.action == ReconciliationAction.MERGE
        assert sample_decision_audit.delta_description is None


# ---------------------------------------------------------------------------
# Rule A3-6: structural retrieval source requires anchor
# ---------------------------------------------------------------------------


class TestRuleStructuralAnchorRequired:
    def test_structural_without_anchor_rejected(self, sample_decision_audit):
        data = sample_decision_audit.model_dump()
        data["candidate_retrieval_source"] = "STRUCTURAL"
        with pytest.raises(ValidationError, match="structural_anchor"):
            DecisionAuditNode(**data)

    def test_structural_with_anchor_accepted(self, sample_decision_audit):
        data = sample_decision_audit.model_dump()
        data["candidate_retrieval_source"] = "STRUCTURAL"
        data["structural_anchor_type"] = "NAMED_PERSON"
        data["structural_anchor_value"] = "person_jordan_001"
        audit = DecisionAuditNode(**data)
        assert audit.candidate_retrieval_source == CandidateRetrievalSource.STRUCTURAL


# ---------------------------------------------------------------------------
# Rule A3-7: AMBIGUOUS never auto-executes
# ---------------------------------------------------------------------------


class TestRuleAmbiguousNeverActive:
    def test_ambiguous_with_active_status_rejected(self, sample_decision_audit):
        data = sample_decision_audit.model_dump()
        data["action"] = "AMBIGUOUS"
        data["status"] = "ACTIVE"
        with pytest.raises(ValidationError, match="never acts on its own"):
            DecisionAuditNode(**data)

    def test_ambiguous_with_pending_hitl_accepted(self, sample_decision_audit):
        data = sample_decision_audit.model_dump()
        data["action"] = "AMBIGUOUS"
        data["status"] = "PENDING_HITL"
        audit = DecisionAuditNode(**data)
        assert audit.status == DecisionStatus.PENDING_HITL


# ---------------------------------------------------------------------------
# Rule A3-8: trust-weight default derivation
# ---------------------------------------------------------------------------


class TestRuleVerificationStatusDefault:
    def test_user_generated_defaults_to_implicit(self):
        obs = ObservationNode(
            node_id="obs_1", episode_id="ep_1", occurred_at=NOW, created_at=NOW,
            valid_from=NOW, type=ObservationType.CONTEXT, content="x",
            signal_strength=SignalStrength.STANDARD, provenance=Provenance.USER_GENERATED,
            status=ObservationStatus.ACTIVE, extraction_model="gemini",
        )
        assert obs.verification_status == "IMPLICIT"

    def test_co_created_defaults_to_unverified(self):
        obs = ObservationNode(
            node_id="obs_1", episode_id="ep_1", occurred_at=NOW, created_at=NOW,
            valid_from=NOW, type=ObservationType.CONTEXT, content="x",
            signal_strength=SignalStrength.STANDARD, provenance=Provenance.CO_CREATED,
            status=ObservationStatus.ACTIVE, extraction_model="gemini",
        )
        assert obs.verification_status == "UNVERIFIED"

    def test_ai_generated_defaults_to_unverified(self):
        """
        Architecture.md's Trust Weight table never states an AI_GENERATED
        default. Per explicit user decision, AI_GENERATED gets the same
        UNVERIFIED trust floor as CO_CREATED — only USER_GENERATED is
        trusted as IMPLICIT by default.
        """
        obs = ObservationNode(
            node_id="obs_1", episode_id="ep_1", occurred_at=NOW, created_at=NOW,
            valid_from=NOW, type=ObservationType.CONTEXT, content="x",
            signal_strength=SignalStrength.STANDARD, provenance=Provenance.AI_GENERATED,
            status=ObservationStatus.ACTIVE, extraction_model="gemini",
        )
        assert obs.verification_status == "UNVERIFIED"

    def test_explicit_verification_status_not_overridden(self):
        obs = ObservationNode(
            node_id="obs_1", episode_id="ep_1", occurred_at=NOW, created_at=NOW,
            valid_from=NOW, type=ObservationType.CONTEXT, content="x",
            signal_strength=SignalStrength.STANDARD, provenance=Provenance.CO_CREATED,
            verification_status="VERIFIED", status=ObservationStatus.ACTIVE,
            extraction_model="gemini",
        )
        assert obs.verification_status == "VERIFIED"


# ---------------------------------------------------------------------------
# Rule A3-9: BeliefNode contradiction consistency
# ---------------------------------------------------------------------------


class TestRuleBeliefContradictionConsistency:
    def test_is_contradicted_without_node_id_rejected(self, sample_belief):
        data = sample_belief.model_dump()
        data["is_contradicted"] = True
        data["contradiction_node_id"] = None
        with pytest.raises(ValidationError, match="contradiction_node_id"):
            BeliefNode(**data)

    def test_contradiction_node_id_without_flag_rejected(self, sample_belief):
        data = sample_belief.model_dump()
        data["is_contradicted"] = False
        data["contradiction_node_id"] = "con_example_001"
        with pytest.raises(ValidationError, match="is_contradicted"):
            BeliefNode(**data)

    def test_consistent_pair_accepted(self, sample_belief):
        data = sample_belief.model_dump()
        data["is_contradicted"] = True
        data["contradiction_node_id"] = "con_example_001"
        belief = BeliefNode(**data)
        assert belief.is_contradicted is True


# ---------------------------------------------------------------------------
# Rule A3-10: ContradictionNode self-reference + resolution timestamp
# ---------------------------------------------------------------------------


class TestRuleContradictionNode:
    def test_self_referencing_contradiction_rejected(self, sample_contradiction):
        data = sample_contradiction.model_dump()
        data["belief_b_id"] = data["belief_a_id"]
        with pytest.raises(ValidationError, match="must refer to different"):
            ContradictionNode(**data)

    def test_resolved_without_timestamp_rejected(self, sample_contradiction):
        data = sample_contradiction.model_dump()
        data["resolution_status"] = "RESOLVED_USER"
        data["resolved_at"] = None
        with pytest.raises(ValidationError, match="resolved_at"):
            ContradictionNode(**data)

    def test_resolved_with_timestamp_accepted(self, sample_contradiction):
        data = sample_contradiction.model_dump()
        data["resolution_status"] = "RESOLVED_USER"
        data["resolved_at"] = NOW.isoformat()
        node = ContradictionNode(**data)
        assert node.resolution_status == ContradictionResolutionStatus.RESOLVED_USER


# ---------------------------------------------------------------------------
# Rule A3-11: episode_index bounds
# ---------------------------------------------------------------------------


class TestRuleEpisodeIndexBounds:
    def test_index_exceeding_total_rejected(self, sample_episode):
        data = sample_episode.model_dump()
        data["episode_index"] = 5
        data["total_episodes_in_entry"] = 2
        with pytest.raises(ValidationError, match="exceeds"):
            EpisodeNode(**data)

    def test_index_zero_rejected(self, sample_episode):
        data = sample_episode.model_dump()
        data["episode_index"] = 0
        with pytest.raises(ValidationError):
            EpisodeNode(**data)


# ---------------------------------------------------------------------------
# Rule A3-12: bounded confidence values / non-negative counters
# ---------------------------------------------------------------------------


class TestRuleBoundedNumerics:
    def test_lesson_confidence_above_one_rejected(self, sample_lesson):
        data = sample_lesson.model_dump()
        data["lesson_confidence"] = 1.5
        with pytest.raises(ValidationError):
            LessonNode(**data)

    def test_lesson_confidence_below_zero_rejected(self, sample_lesson):
        data = sample_lesson.model_dump()
        data["lesson_confidence"] = -0.1
        with pytest.raises(ValidationError):
            LessonNode(**data)

    def test_decision_confidence_out_of_range_rejected(self, sample_decision_audit):
        data = sample_decision_audit.model_dump()
        data["confidence"] = 1.2
        with pytest.raises(ValidationError):
            DecisionAuditNode(**data)

    def test_negative_evidence_count_rejected(self, sample_pattern):
        data = sample_pattern.model_dump()
        data["evidence_count"] = -1
        with pytest.raises(ValidationError):
            PatternNode(**data)

    def test_negative_mention_count_rejected(self, sample_person):
        data = sample_person.model_dump()
        data["mention_count"] = -3
        with pytest.raises(ValidationError):
            PersonEntityNode(**data)

    def test_negative_snooze_count_rejected(self, sample_decision_audit):
        data = sample_decision_audit.model_dump()
        data["snooze_count"] = -1
        with pytest.raises(ValidationError):
            DecisionAuditNode(**data)


# ---------------------------------------------------------------------------
# Rule A3-13: AdoptedPrincipleNode lifecycle_history
# ---------------------------------------------------------------------------


class TestRuleLifecycleHistory:
    def test_empty_lifecycle_history_rejected(self, sample_adopted_principle):
        data = sample_adopted_principle.model_dump()
        data["lifecycle_history"] = []
        with pytest.raises(ValidationError, match="non-empty"):
            AdoptedPrincipleNode(**data)

    def test_mismatched_tail_state_rejected(self, sample_adopted_principle):
        data = sample_adopted_principle.model_dump()
        data["lifecycle_state"] = "INTERNALIZED"
        # lifecycle_history tail is still TRYING from the fixture
        with pytest.raises(ValidationError, match="must match"):
            AdoptedPrincipleNode(**data)

    def test_matching_tail_state_accepted(self, sample_adopted_principle):
        data = sample_adopted_principle.model_dump()
        data["lifecycle_state"] = "INTERNALIZED"
        data["lifecycle_history"].append(
            {"state": "INTERNALIZED", "at": NOW.isoformat(), "reason": "Now automatic."}
        )
        node = AdoptedPrincipleNode(**data)
        assert node.lifecycle_state == LifecycleState.INTERNALIZED

    def test_ai_generated_provenance_rejected(self, sample_adopted_principle):
        """AdoptedPrincipleNode restricts provenance to USER_GENERATED | CO_CREATED."""
        data = sample_adopted_principle.model_dump()
        data["provenance"] = "AI_GENERATED"
        with pytest.raises(ValidationError, match="AI_GENERATED"):
            AdoptedPrincipleNode(**data)


# ---------------------------------------------------------------------------
# OpenLoopNode resolution timestamp (same shape as ContradictionNode rule)
# ---------------------------------------------------------------------------


class TestOpenLoopResolution:
    def test_resolved_without_timestamp_rejected(self, sample_open_loop):
        data = sample_open_loop.model_dump()
        data["resolution_status"] = "RESOLVED"
        data["resolved_at"] = None
        with pytest.raises(ValidationError, match="resolved_at"):
            OpenLoopNode(**data)

    def test_open_status_default(self, sample_open_loop):
        assert sample_open_loop.resolution_status == LoopResolutionStatus.OPEN


# ---------------------------------------------------------------------------
# MacroextractionReportNode period ordering
# ---------------------------------------------------------------------------


class TestMacroReportPeriodOrdering:
    def test_period_end_before_start_rejected(self, sample_macro_report):
        data = sample_macro_report.model_dump()
        data["period_start"], data["period_end"] = data["period_end"], data["period_start"]
        with pytest.raises(ValidationError, match="period_end"):
            MacroextractionReportNode(**data)


# ---------------------------------------------------------------------------
# PersonEntityNode structural shape (no created_at / valid_from)
# ---------------------------------------------------------------------------


class TestPersonEntityNodeShape:
    def test_has_no_created_at_field(self, sample_person):
        assert "created_at" not in type(sample_person).model_fields

    def test_has_no_valid_from_field(self, sample_person):
        assert "valid_from" not in type(sample_person).model_fields


# ---------------------------------------------------------------------------
# to_graph_dict() type coercion
# ---------------------------------------------------------------------------


class TestToGraphDictCoercion:
    def test_enum_becomes_plain_string(self, sample_pattern):
        d = sample_pattern.to_graph_dict()
        assert d["domain"] == "COGNITIVE_STYLE"
        assert isinstance(d["domain"], str)

    def test_datetime_becomes_iso_string(self, sample_pattern):
        d = sample_pattern.to_graph_dict()
        assert d["created_at"] == NOW.isoformat()

    def test_list_becomes_json_string(self, sample_pattern):
        import json
        d = sample_pattern.to_graph_dict()
        assert isinstance(d["archetype_tags"], str)
        assert json.loads(d["archetype_tags"]) == ["high_conscientiousness"]

    def test_none_fields_omitted(self, sample_belief):
        d = sample_belief.to_graph_dict()
        assert "contradiction_node_id" not in d

    def test_nested_model_becomes_json_string(self, sample_decision_audit):
        import json
        d = sample_decision_audit.to_graph_dict()
        assert isinstance(d["rollback_pointer"], str)
        parsed = json.loads(d["rollback_pointer"])
        assert parsed["edge_to_invalidate"] == "edge_2026_06_11_009"

    def test_bool_survives_as_bool(self, sample_pattern):
        d = sample_pattern.to_graph_dict()
        assert d["is_canonical"] is True
