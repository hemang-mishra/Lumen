"""
Test suite for lumen/schemas/pipeline.py.

Covers construction and nesting of every stage DTO, the candidate-cap rule
from Architecture.md's Stage 2 merge, and the shared EVOLVE-requires-delta
rule reused from DecisionAuditNode.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from lumen.schemas.enums import (
    CandidateRetrievalSource,
    EntryClass,
    QualityGateDecision,
    ReconciliationAction,
    SourceModality,
)
from lumen.schemas.pipeline import (
    AmbiguousRef,
    BufferMessage,
    CandidateNode,
    CoreferenceMap,
    ExtractionResult,
    MicroextractionInput,
    PreprocessedEpisode,
    PreprocessingResult,
    ReconciliationResult,
    ResolvedEntity,
    RetrievalResult,
    SessionDecayEvent,
)

NOW = datetime(2026, 6, 11, 10, 30, 0)
TODAY = date(2026, 6, 11)


class TestTraceIdDefault:
    def test_defaults_to_none(self):
        event = SessionDecayEvent(
            session_id="s1", user_id="u1", event_date=TODAY,
            message_count=3, triggered_at=NOW,
        )
        assert event.trace_id is None

    def test_can_be_set_explicitly(self):
        event = SessionDecayEvent(
            session_id="s1", user_id="u1", event_date=TODAY,
            message_count=3, triggered_at=NOW, trace_id="trace-123",
        )
        assert event.trace_id == "trace-123"


class TestSessionDecayEvent:
    def test_constructs_with_nested_buffer_messages(self):
        event = SessionDecayEvent(
            session_id="s1", user_id="u1", event_date=TODAY,
            message_count=2, triggered_at=NOW,
            raw_buffer=[
                BufferMessage(
                    message_id="m1", role="USER", content="hi",
                    timestamp=NOW, event_date=TODAY,
                ),
                BufferMessage(
                    message_id="m2", role="AI", content="hello",
                    timestamp=NOW, event_date=TODAY, co_created_marker=True,
                ),
            ],
        )
        assert len(event.raw_buffer) == 2
        assert event.raw_buffer[1].co_created_marker is True

    def test_invalid_role_rejected(self):
        with pytest.raises(ValidationError):
            BufferMessage(
                message_id="m1", role="SYSTEM", content="hi",
                timestamp=NOW, event_date=TODAY,
            )

    def test_input_is_assumed_typed_unless_stated(self):
        # The safe default. Speech cleanup removes words, so applying it to
        # typed text by accident would delete things the person meant.
        event = SessionDecayEvent(
            session_id="s1", user_id="u1", event_date=TODAY,
            message_count=0, triggered_at=NOW,
        )
        assert event.source_modality == SourceModality.TEXT_ENTRY

    def test_a_spoken_session_can_say_so(self):
        event = SessionDecayEvent(
            session_id="s1", user_id="u1", event_date=TODAY,
            source_modality=SourceModality.VOICE_NOTE,
            message_count=0, triggered_at=NOW,
        )
        assert event.source_modality == SourceModality.VOICE_NOTE


class TestCoreferenceMap:
    def test_constructs_from_doc_example(self):
        cm = CoreferenceMap(
            entry_id="e_2026_06_11_002",
            resolved_entities=[
                ResolvedEntity(
                    span="he", resolved_to="Jordan", confidence=0.94,
                    resolution_basis="most_recent_named_antecedent",
                )
            ],
            ambiguous_refs=[
                AmbiguousRef(span="she", candidates=["Neha", "Priya"], reason="two referents")
            ],
        )
        assert cm.resolved_entities[0].resolved_to == "Jordan"
        assert cm.ambiguous_refs[0].candidates == ["Neha", "Priya"]

    def test_ambiguous_ref_requires_at_least_two_candidates(self):
        with pytest.raises(ValidationError):
            AmbiguousRef(span="she", candidates=["Neha"], reason="only one candidate")


class TestPreprocessedEpisode:
    def test_index_within_bounds_accepted(self):
        ep = PreprocessedEpisode(
            episode_id="ep_2026_06_11_001", episode_summary="A workout struggle",
            episode_index=1, total_episodes_in_entry=2, cleaned_text="text",
            entry_class=EntryClass.REFLECTION, coherence_score=0.8,
            raw_text_hash="sha256:abc",
        )
        assert ep.episode_index == 1

    def test_index_exceeding_total_rejected(self):
        with pytest.raises(ValidationError, match="exceeds"):
            PreprocessedEpisode(
                episode_id="ep_2026_06_11_003", episode_summary="A workout struggle",
                episode_index=3, total_episodes_in_entry=2, cleaned_text="text",
                entry_class=EntryClass.REFLECTION, coherence_score=0.8,
                raw_text_hash="sha256:abc",
            )

    def test_an_episode_must_be_identifiable_and_labelled(self):
        # Both are required downstream and have no other producer: the id is
        # what extraction refers back to, and the summary is what a person
        # sees when scanning a day.
        for missing in ("episode_id", "episode_summary"):
            fields = {
                "episode_id": "ep_2026_06_11_001",
                "episode_summary": "A workout struggle",
                "episode_index": 1,
                "total_episodes_in_entry": 1,
                "cleaned_text": "text",
                "entry_class": EntryClass.REFLECTION,
                "coherence_score": 0.8,
                "raw_text_hash": "abc",
            }
            del fields[missing]
            with pytest.raises(ValidationError):
                PreprocessedEpisode(**fields)

    def test_coherence_score_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            PreprocessedEpisode(
                episode_index=1, total_episodes_in_entry=1, cleaned_text="text",
                entry_class=EntryClass.REFLECTION, coherence_score=1.5,
                raw_text_hash="sha256:abc",
            )


class TestPreprocessingResult:
    def test_constructs_with_pending_reflections(self):
        result = PreprocessingResult(
            session_id="s1",
            coreference_map=CoreferenceMap(entry_id="e1"),
            quality_gate_decision=QualityGateDecision.RAW_CAPTURE,
            processing_time_ms=120,
            pending_reflections=["What bothered you most?"],
        )
        assert result.quality_gate_decision == QualityGateDecision.RAW_CAPTURE
        assert len(result.pending_reflections) == 1

    def test_adopted_framings_default_to_none_found(self):
        result = PreprocessingResult(
            session_id="s1",
            coreference_map=CoreferenceMap(entry_id="e1"),
            quality_gate_decision=QualityGateDecision.REFLECTION,
            processing_time_ms=120,
        )
        assert result.co_created_spans == []

    def test_adopted_framings_are_carried_when_found(self):
        result = PreprocessingResult(
            session_id="s1",
            coreference_map=CoreferenceMap(entry_id="e1"),
            quality_gate_decision=QualityGateDecision.REFLECTION,
            processing_time_ms=120,
            co_created_spans=["confidence is showing up scared"],
        )
        assert result.co_created_spans == ["confidence is showing up scared"]


class TestMicroextractionInput:
    def _episode(self):
        return PreprocessedEpisode(
            episode_id="ep_2026_06_11_001", episode_summary="a topic",
            episode_index=1, total_episodes_in_entry=1, cleaned_text="text",
            entry_class=EntryClass.REFLECTION, coherence_score=0.8,
            raw_text_hash="sha256:abc",
        )

    def test_constructs_with_an_episode_and_its_entry_facts(self):
        payload = MicroextractionInput(
            episode=self._episode(),
            coreference_map=CoreferenceMap(entry_id="e1"),
            entry_id="e1",
            event_date=date(2026, 6, 11),
            occurred_at=datetime(2026, 6, 11, 20, 0, tzinfo=UTC),
        )
        assert payload.episode.episode_id == "ep_2026_06_11_001"
        assert payload.source_modality == SourceModality.TEXT_ENTRY
        assert payload.co_created_spans == []

    def test_the_entry_must_be_named(self):
        with pytest.raises(ValidationError):
            MicroextractionInput(
                episode=self._episode(),
                coreference_map=CoreferenceMap(entry_id="e1"),
                entry_id="",
                event_date=date(2026, 6, 11),
                occurred_at=datetime(2026, 6, 11, 20, 0, tzinfo=UTC),
            )

    def test_there_is_nowhere_to_put_history(self):
        # The stage is blind to the past on purpose, and the contract is
        # where that is enforced: a caller cannot hand it candidates.
        with pytest.raises(ValidationError):
            MicroextractionInput(
                episode=self._episode(),
                coreference_map=CoreferenceMap(entry_id="e1"),
                entry_id="e1",
                event_date=date(2026, 6, 11),
                occurred_at=datetime(2026, 6, 11, 20, 0, tzinfo=UTC),
                historical_candidates=["pat_decision_saturation"],
            )


class TestExtractionResult:
    def test_constructs_with_observations(self, sample_observation):
        result = ExtractionResult(
            episode_id="ep_1", observations=[sample_observation],
            extraction_model="gemini-2.0-flash", validation_passed=True,
        )
        assert len(result.observations) == 1
        assert result.retry_count == 0

    def test_constructs_with_events_sessions_and_causal_chains(
        self, sample_event, sample_session, sample_causal_chain, sample_causal_step
    ):
        result = ExtractionResult(
            episode_id="ep_1", events=[sample_event], sessions=[sample_session],
            causal_chains=[sample_causal_chain], causal_steps=[sample_causal_step],
            extraction_model="gemini-2.0-flash", validation_passed=True,
        )
        assert len(result.events) == 1
        assert len(result.causal_steps) == 1


class TestCandidateNode:
    def test_semantic_requires_similarity_score(self):
        with pytest.raises(ValidationError, match="similarity_score"):
            CandidateNode(
                node_id="n1", node_type="PatternNode", content_preview="x",
                retrieval_source=CandidateRetrievalSource.SEMANTIC,
            )

    def test_structural_does_not_require_similarity_score(self):
        candidate = CandidateNode(
            node_id="n1", node_type="PatternNode", content_preview="x",
            retrieval_source=CandidateRetrievalSource.STRUCTURAL,
            structural_anchor_type="NAMED_PERSON",
            structural_anchor_value="person_jordan_001",
        )
        assert candidate.similarity_score is None


class TestRetrievalResultCandidateCap:
    def _candidate(self, node_id: str, source: CandidateRetrievalSource) -> CandidateNode:
        kwargs = {"node_id": node_id, "node_type": "PatternNode", "content_preview": "x", "retrieval_source": source}
        if source == CandidateRetrievalSource.SEMANTIC:
            kwargs["similarity_score"] = 0.5
        else:
            kwargs["structural_anchor_type"] = "NAMED_PERSON"
            kwargs["structural_anchor_value"] = "person_1"
        return CandidateNode(**kwargs)

    def test_exactly_eight_unique_candidates_accepted(self):
        candidates = [self._candidate(f"n{i}", CandidateRetrievalSource.SEMANTIC) for i in range(8)]
        result = RetrievalResult(
            source_node_id="obs_1", pass_a_candidates=candidates, retrieval_time_ms=10
        )
        assert len(result.pass_a_candidates) == 8

    def test_nine_unique_candidates_rejected(self):
        candidates = [self._candidate(f"n{i}", CandidateRetrievalSource.SEMANTIC) for i in range(9)]
        with pytest.raises(ValidationError, match="capped at 8"):
            RetrievalResult(
                source_node_id="obs_1", pass_a_candidates=candidates, retrieval_time_ms=10
            )

    def test_duplicate_node_ids_across_passes_deduplicated_before_cap(self):
        # 5 in pass A + 5 in pass B, but 3 of them share node_ids -> 7 unique, under cap
        pass_a = [self._candidate(f"n{i}", CandidateRetrievalSource.SEMANTIC) for i in range(5)]
        pass_b = [
            self._candidate(f"n{i}", CandidateRetrievalSource.STRUCTURAL) for i in range(2, 7)
        ]
        result = RetrievalResult(
            source_node_id="obs_1", pass_a_candidates=pass_a, pass_b_candidates=pass_b,
            retrieval_time_ms=10,
        )
        merged_ids = {c.node_id for c in result.pass_a_candidates} | {
            c.node_id for c in result.pass_b_candidates
        }
        assert len(merged_ids) == 7


class TestReconciliationResult:
    def test_evolve_without_delta_rejected(self):
        with pytest.raises(ValidationError, match="delta_description"):
            ReconciliationResult(
                source_node_id="obs_1", action=ReconciliationAction.EVOLVE,
                confidence=0.94, decision_model="gemini-2.0-pro",
                escalated_to_hitl=False, audit_node_id="d_1",
            )

    def test_evolve_with_delta_accepted(self):
        result = ReconciliationResult(
            source_node_id="obs_1", action=ReconciliationAction.EVOLVE,
            confidence=0.94, delta_description="Belief evolved.",
            decision_model="gemini-2.0-pro", escalated_to_hitl=False,
            audit_node_id="d_1",
        )
        assert result.action == ReconciliationAction.EVOLVE

    def test_merge_without_delta_is_fine(self):
        result = ReconciliationResult(
            source_node_id="obs_1", action=ReconciliationAction.MERGE,
            confidence=0.91, decision_model="gemini-2.0-flash",
            escalated_to_hitl=False, audit_node_id="d_1",
        )
        assert result.delta_description is None
