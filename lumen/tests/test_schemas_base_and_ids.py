"""
Test suite for lumen/schemas/base.py and lumen/schemas/ids.py directly —
the hierarchy, mixins, and ID helpers that nodes.py/edges.py build on.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from lumen.schemas.base import (
    GraphNode,
    LumenNode,
    PersonRefsMixin,
    SignalProvenanceMixin,
    TemporalNode,
    VersionedNode,
    model_to_graph_dict,
)
from lumen.schemas.enums import Provenance, SignalStrength
from lumen.schemas.ids import (
    NODE_ID_PREFIXES,
    SEMANTIC_ID_RE,
    make_node_id,
    make_slug_node_id,
)

NOW = datetime(2026, 6, 11, 10, 30, 0)


class TestGraphNode:
    def test_requires_non_empty_node_id(self):
        with pytest.raises(ValidationError):
            GraphNode(node_id="")

    def test_strips_whitespace(self):
        node = GraphNode(node_id="  n1  ")
        assert node.node_id == "n1"

    def test_extra_forbidden(self):
        with pytest.raises(ValidationError):
            GraphNode(node_id="n1", unexpected="x")


class TestLumenNodeTemporalNode:
    def test_lumen_node_requires_created_at(self):
        with pytest.raises(ValidationError):
            LumenNode(node_id="n1")

    def test_temporal_node_requires_valid_from(self):
        with pytest.raises(ValidationError):
            TemporalNode(node_id="n1", created_at=NOW)


class TestVersionedNode:
    def test_default_version_is_one(self):
        node = VersionedNode(node_id="n1", created_at=NOW, valid_from=NOW, last_reinforced_at=NOW)
        assert node.version == 1
        assert node.previous_version_id is None

    def test_evidence_count_and_query_frequency_default_zero(self):
        node = VersionedNode(node_id="n1", created_at=NOW, valid_from=NOW, last_reinforced_at=NOW)
        assert node.evidence_count == 0
        assert node.query_frequency == 0


class TestSignalProvenanceMixin:
    def test_signal_strength_alias(self):
        class Combined(TemporalNode, SignalProvenanceMixin):
            pass

        node = Combined(
            node_id="n1", created_at=NOW, valid_from=NOW,
            extraction_signal_strength=SignalStrength.CRITICAL,
            provenance=Provenance.USER_GENERATED,
        )
        assert node.signal_strength == SignalStrength.CRITICAL


class TestPersonRefsMixin:
    def test_default_is_empty_list(self):
        class WithRefs(GraphNode, PersonRefsMixin):
            pass

        node = WithRefs(node_id="n1")
        assert node.person_refs == []


class TestModelToGraphDict:
    def test_function_matches_method(self):
        node = LumenNode(node_id="n1", created_at=NOW)
        assert model_to_graph_dict(node) == node.to_graph_dict()


class TestMakeNodeId:
    def test_produces_expected_format(self):
        assert make_node_id("obs", date(2026, 6, 11), 4) == "obs_2026_06_11_004"

    def test_zero_pads_sequence(self):
        assert make_node_id("ep", date(2026, 1, 5), 1) == "ep_2026_01_05_001"

    def test_matches_semantic_id_regex(self):
        node_id = make_node_id("obs", date(2026, 6, 11), 4)
        assert SEMANTIC_ID_RE.match(node_id)

    def test_negative_seq_rejected(self):
        with pytest.raises(ValueError):
            make_node_id("obs", date(2026, 6, 11), -1)

    def test_empty_prefix_rejected(self):
        with pytest.raises(ValueError):
            make_node_id("", date(2026, 6, 11), 1)


class TestMakeSlugNodeId:
    def test_produces_expected_format(self):
        assert make_slug_node_id("pat", "Decision Saturation") == "pat_decision_saturation"

    def test_normalizes_punctuation(self):
        assert make_slug_node_id("pat", "Risk-Averse!! Behavior") == "pat_risk_averse_behavior"

    def test_matches_semantic_id_regex(self):
        node_id = make_slug_node_id("pat", "Decision Saturation")
        assert SEMANTIC_ID_RE.match(node_id)

    def test_empty_slug_rejected(self):
        with pytest.raises(ValueError):
            make_slug_node_id("pat", "   ")

    def test_empty_prefix_rejected(self):
        with pytest.raises(ValueError):
            make_slug_node_id("", "some slug")


class TestNodeIdPrefixes:
    def test_covers_all_fifteen_node_types(self):
        expected = {
            "EpisodeNode", "ObservationNode", "EventNode", "SessionNode",
            "CausalChainNode", "CausalStepNode", "PatternNode", "BeliefNode",
            "LessonNode", "AdoptedPrincipleNode", "PersonEntityNode",
            "DecisionAuditNode", "ContradictionNode",
            "MacroextractionReportNode", "OpenLoopNode",
        }
        assert set(NODE_ID_PREFIXES.keys()) == expected
