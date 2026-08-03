"""
Test suite for lumen/schemas/edges.py.

Covers each edge model's required payload, the logical->physical resolver's
exact parity with lumen/graph/kuzu_impl.py EDGE_REGISTRY, and rejection of
invalid (logical, from, to) combinations.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from lumen.graph.kuzu_impl import EDGE_REGISTRY
from lumen.schemas.edges import (
    LOGICAL_TO_PHYSICAL,
    DialecticEdge,
    EvolvedFromEdge,
    LogicalEdgeType,
    LumenEdge,
    ReconciliationEdge,
    RegulatesEdge,
    UnsupportedEdgeError,
    resolve_edge_table,
)

NOW = datetime(2026, 6, 11, 10, 30, 0)
LATER = datetime(2026, 6, 12, 10, 30, 0)


# ---------------------------------------------------------------------------
# LumenEdge base behavior
# ---------------------------------------------------------------------------


class TestLumenEdgeBase:
    def test_constructs_with_minimal_fields(self):
        edge = LumenEdge(source_node_id="a", target_node_id="b", valid_from=NOW)
        assert edge.invalidated_at is None

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            LumenEdge(source_node_id="a", target_node_id="b", valid_from=NOW, made_up=1)

    def test_to_graph_dict_omits_none_invalidated_at(self):
        edge = LumenEdge(source_node_id="a", target_node_id="b", valid_from=NOW)
        assert "invalidated_at" not in edge.to_graph_dict()

    def test_to_graph_dict_serializes_datetime(self):
        edge = LumenEdge(source_node_id="a", target_node_id="b", valid_from=NOW)
        assert edge.to_graph_dict()["valid_from"] == NOW.isoformat()


# ---------------------------------------------------------------------------
# Rule A3-14: invalidated_at cannot precede valid_from
# ---------------------------------------------------------------------------


class TestRuleInvalidationOrder:
    def test_invalidated_before_valid_from_rejected(self):
        with pytest.raises(ValidationError, match="cannot precede"):
            LumenEdge(
                source_node_id="a", target_node_id="b",
                valid_from=LATER, invalidated_at=NOW,
            )

    def test_invalidated_after_valid_from_accepted(self):
        edge = LumenEdge(
            source_node_id="a", target_node_id="b",
            valid_from=NOW, invalidated_at=LATER,
        )
        assert edge.invalidated_at == LATER

    def test_invalidated_equal_to_valid_from_accepted(self):
        edge = LumenEdge(
            source_node_id="a", target_node_id="b",
            valid_from=NOW, invalidated_at=NOW,
        )
        assert edge.invalidated_at == NOW


# ---------------------------------------------------------------------------
# Rule A3-14: reconciliation edges require decision_id
# ---------------------------------------------------------------------------


class TestReconciliationEdgeRequiresDecisionId:
    def test_missing_decision_id_rejected(self):
        with pytest.raises(ValidationError):
            ReconciliationEdge(source_node_id="a", target_node_id="b", valid_from=NOW, confidence=0.9)

    def test_with_decision_id_accepted(self):
        edge = ReconciliationEdge(
            source_node_id="a", target_node_id="b", valid_from=NOW,
            decision_id="d_1", confidence=0.9,
        )
        assert edge.decision_id == "d_1"

    def test_confidence_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            ReconciliationEdge(
                source_node_id="a", target_node_id="b", valid_from=NOW,
                decision_id="d_1", confidence=1.5,
            )


class TestEvolvedFromEdge:
    def test_requires_decision_id(self):
        edge = EvolvedFromEdge(
            source_node_id="bel_v2", target_node_id="bel_v1", valid_from=NOW,
            decision_id="d_2026_06_11_007", confidence=0.94,
        )
        assert edge.decision_id == "d_2026_06_11_007"


# ---------------------------------------------------------------------------
# Rule A3-15: dialectic / regulates required summary fields
# ---------------------------------------------------------------------------


class TestDialecticEdge:
    def test_requires_tension_summary(self):
        with pytest.raises(ValidationError):
            DialecticEdge(
                source_node_id="bel_a", target_node_id="bel_b", valid_from=NOW,
                decision_id="d_1", confidence=0.89,
            )

    def test_with_tension_summary_accepted(self):
        edge = DialecticEdge(
            source_node_id="bel_a", target_node_id="bel_b", valid_from=NOW,
            decision_id="d_1", confidence=0.89,
            tension_summary="Both truths are simultaneously held.",
        )
        assert edge.tension_summary


class TestRegulatesEdge:
    def test_requires_regulation_summary(self):
        with pytest.raises(ValidationError):
            RegulatesEdge(
                source_node_id="obs_1", target_node_id="pat_1", valid_from=NOW,
                decision_id="d_1", confidence=0.83,
            )

    def test_with_regulation_summary_accepted(self):
        edge = RegulatesEdge(
            source_node_id="obs_1", target_node_id="pat_1", valid_from=NOW,
            decision_id="d_1", confidence=0.83,
            regulation_summary="User caught the spiral mid-sentence.",
        )
        assert edge.regulation_summary


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


class TestResolverParityWithEdgeRegistry:
    def test_resolver_value_set_matches_edge_registry_exactly(self):
        registry_names = {name for _, _, name in EDGE_REGISTRY}
        resolver_names = set(LOGICAL_TO_PHYSICAL.values())
        assert resolver_names == registry_names

    def test_resolver_key_triples_match_edge_registry_from_to_pairs(self):
        registry_triples = {
            (from_t, to_t, name) for from_t, to_t, name in EDGE_REGISTRY
        }
        resolver_triples = {
            (from_t, to_t, name) for (_, from_t, to_t), name in LOGICAL_TO_PHYSICAL.items()
        }
        assert resolver_triples == registry_triples

    def test_all_20_logical_types_represented(self):
        logical_types_in_resolver = {lt for (lt, _, _) in LOGICAL_TO_PHYSICAL}
        assert logical_types_in_resolver == set(LogicalEdgeType)


class TestResolveEdgeTable:
    def test_resolves_contains_episode_to_observation(self):
        assert resolve_edge_table(
            LogicalEdgeType.CONTAINS, "EpisodeNode", "ObservationNode"
        ) == "contains_obs"

    def test_resolves_same_as_pattern_to_pattern(self):
        assert resolve_edge_table(
            LogicalEdgeType.SAME_AS, "PatternNode", "PatternNode"
        ) == "same_as_pat_pat"

    def test_resolves_dialectic_belief_to_pattern(self):
        assert resolve_edge_table(
            LogicalEdgeType.DIALECTIC, "BeliefNode", "PatternNode"
        ) == "dialectic_bel_pat"

    def test_invalid_pair_raises_unsupported_edge_error(self):
        with pytest.raises(UnsupportedEdgeError):
            resolve_edge_table(LogicalEdgeType.CONTAINS, "EpisodeNode", "PatternNode")

    def test_error_message_lists_valid_pairs(self):
        with pytest.raises(UnsupportedEdgeError, match="ObservationNode"):
            resolve_edge_table(LogicalEdgeType.CONTAINS, "EpisodeNode", "PatternNode")

    def test_wrong_logical_type_for_valid_node_pair_raises(self):
        # EpisodeNode -> ObservationNode is valid for CONTAINS, not for MENTIONS
        with pytest.raises(UnsupportedEdgeError):
            resolve_edge_table(LogicalEdgeType.MENTIONS, "EpisodeNode", "ObservationNode")
