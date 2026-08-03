"""
Integration tests: Pydantic node model -> GraphProvider.write_node() -> Kuzu
-> get_node() round trip.

Verifies the provider refactor (Section B8 of implementation/Goal_2_Plan.md)
actually persists model data correctly through the real Kuzu embedded DB,
not just that to_graph_dict() produces the right shape in isolation.

Note: KuzuGraphProvider.get_node() runs an untyped MATCH (n) query (Goal 1
behavior, unchanged here) which returns the union of every node table's
columns with unmatched ones as None — so these tests assert specific keys
match, not exact dict equality, consistent with lumen/tests/test_kuzu_impl.py.
"""

from __future__ import annotations

import json

import pytest

from lumen.graph.kuzu_impl import KuzuGraphProvider


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_schema_integration_db")


@pytest.fixture
def provider(db_path):
    p = KuzuGraphProvider(db_path)
    p.init_schema()
    yield p
    p.close()


class TestNodeModelRoundTrip:
    def test_episode_node_round_trip(self, provider, sample_episode):
        provider.write_node("EpisodeNode", sample_episode)
        fetched = provider.get_node(sample_episode.node_id)
        assert fetched is not None
        assert fetched["episode_summary"] == sample_episode.episode_summary
        assert fetched["episode_index"] == sample_episode.episode_index
        assert json.loads(fetched["overarching_themes"]) == sample_episode.overarching_themes

    def test_observation_node_round_trip(self, provider, sample_observation):
        provider.write_node("ObservationNode", sample_observation)
        fetched = provider.get_node(sample_observation.node_id)
        assert fetched is not None
        assert fetched["type"] == sample_observation.type.value
        assert fetched["signal_strength"] == "HIGH"
        assert fetched["verification_status"] == "IMPLICIT"
        assert json.loads(fetched["person_refs"]) == []

    def test_pattern_node_round_trip(self, provider, sample_pattern):
        provider.write_node("PatternNode", sample_pattern)
        fetched = provider.get_node(sample_pattern.node_id)
        assert fetched is not None
        assert fetched["pattern_name"] == sample_pattern.pattern_name
        assert fetched["domain"] == "COGNITIVE_STYLE"
        assert fetched["is_canonical"] is True
        assert json.loads(fetched["archetype_tags"]) == ["high_conscientiousness"]

    def test_belief_node_round_trip(self, provider, sample_belief):
        provider.write_node("BeliefNode", sample_belief)
        fetched = provider.get_node(sample_belief.node_id)
        assert fetched is not None
        assert fetched["belief_statement"] == sample_belief.belief_statement
        assert fetched["is_contradicted"] is False

    def test_decision_audit_node_round_trip(self, provider, sample_decision_audit):
        provider.write_node("DecisionAuditNode", sample_decision_audit)
        fetched = provider.get_node(sample_decision_audit.node_id)
        assert fetched is not None
        assert fetched["action"] == "MERGE"
        rollback = json.loads(fetched["rollback_pointer"])
        assert rollback["edge_to_invalidate"] == "edge_2026_06_11_009"
        assert rollback["nodes_to_requeue"] == ["obs_2026_06_11_004"]

    def test_person_entity_node_round_trip(self, provider, sample_person):
        """PersonEntityNode has no created_at/valid_from — verifies that still writes fine."""
        provider.write_node("PersonEntityNode", sample_person)
        fetched = provider.get_node(sample_person.node_id)
        assert fetched is not None
        assert fetched["canonical_name"] == "Jordan"
        assert json.loads(fetched["aliases"]) == ["J", "my colleague Jordan"]

    def test_dict_callers_still_work_after_refactor(self, provider):
        """Regression guard: raw-dict callers (Goal 1 pattern) still work unchanged."""
        node_id = provider.write_node(
            "PatternNode",
            {"node_id": "pat_dict_style", "pattern_name": "Dict Style", "version": 1,
             "is_canonical": True, "status": "ACTIVE"},
        )
        assert node_id == "pat_dict_style"
        fetched = provider.get_node("pat_dict_style")
        assert fetched["pattern_name"] == "Dict Style"


class TestAllFifteenNodeTypesWritable:
    """Every node model must be writable through the refactored write_node()."""

    ALL_FIXTURES = [
        ("EpisodeNode", "sample_episode"),
        ("ObservationNode", "sample_observation"),
        ("EventNode", "sample_event"),
        ("SessionNode", "sample_session"),
        ("CausalChainNode", "sample_causal_chain"),
        ("CausalStepNode", "sample_causal_step"),
        ("PatternNode", "sample_pattern"),
        ("BeliefNode", "sample_belief"),
        ("LessonNode", "sample_lesson"),
        ("AdoptedPrincipleNode", "sample_adopted_principle"),
        ("PersonEntityNode", "sample_person"),
        ("DecisionAuditNode", "sample_decision_audit"),
        ("ContradictionNode", "sample_contradiction"),
        ("MacroextractionReportNode", "sample_macro_report"),
        ("OpenLoopNode", "sample_open_loop"),
    ]

    @pytest.mark.parametrize("node_type,fixture_name", ALL_FIXTURES)
    def test_write_and_read_back(self, provider, node_type, fixture_name, request):
        node = request.getfixturevalue(fixture_name)
        written_id = provider.write_node(node_type, node)
        assert written_id == node.node_id
        fetched = provider.get_node(node.node_id)
        assert fetched is not None
        assert fetched["node_id"] == node.node_id
