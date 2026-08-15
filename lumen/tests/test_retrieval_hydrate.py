"""
Tests for reading graph rows back as candidates.

A row arrives wide — the graph returns the union of every column across
every node table, so most of what comes back is empty — and which column
holds the readable part depends on the kind of node. That mapping is the
whole job here, and both of its escape hatches lean the same way: produce
something usable rather than lose a real historical match to an unusual
row.
"""

from __future__ import annotations

import pytest

from lumen.pipeline.retrieval.hydrate import (
    PREVIEW_LENGTH,
    preview_of,
    signal_of,
    to_candidates,
)
from lumen.schemas.enums import (
    CandidateRetrievalSource,
    SignalStrength,
    StructuralAnchorType,
)


class TestFindingTheReadablePart:
    @pytest.mark.parametrize(
        "column, value",
        [
            ("content", "felt small"),
            ("pattern_name", "Comparison spiral"),
            ("belief_statement", "Effort only counts if it succeeds"),
            ("event_summary", "Ate at the cafe"),
            ("session_summary", "Worked out where it started"),
            ("episode_summary", "An evening of comparing"),
            ("loop_description", "Am I staying out of meaning or fear?"),
        ],
    )
    def test_each_kind_of_node_says_what_it_is_in_its_own_column(self, column, value):
        assert preview_of({"node_id": "n1", column: value}) == value

    def test_the_first_filled_column_wins(self):
        row = {"node_id": "n1", "content": "the finding", "episode_summary": "the entry"}

        assert preview_of(row) == "the finding"

    def test_an_empty_column_is_passed_over(self):
        row = {"node_id": "n1", "content": "   ", "pattern_name": "Comparison spiral"}

        assert preview_of(row) == "Comparison spiral"

    def test_a_long_preview_is_shortened(self):
        # Eight candidates at full length would crowd out the entry being
        # reconciled against them.
        row = {"node_id": "n1", "content": "x" * 1000}

        assert len(preview_of(row)) == PREVIEW_LENGTH

    def test_a_row_with_nothing_recognisable_still_produces_something(self):
        # Losing a real historical match because its table names things
        # unusually would be worse than an ugly preview.
        assert preview_of({"node_id": "obs_odd"}) == "obs_odd"

    def test_a_row_with_no_id_at_all_still_produces_something(self):
        assert preview_of({}) == "unknown node"


class TestReadingWeight:
    @pytest.mark.parametrize(
        "stored, expected",
        [
            ("HIGH", SignalStrength.HIGH),
            ("CRITICAL", SignalStrength.CRITICAL),
            ("STANDARD", SignalStrength.STANDARD),
        ],
    )
    def test_a_recorded_weight_is_read(self, stored, expected):
        assert signal_of({"signal_strength": stored}) is expected

    def test_a_node_that_records_no_weight_is_ordinary(self):
        # Not every kind of node has this. Treating it as ordinary can only
        # fail to promote something, never promote what did not earn it.
        assert signal_of({"node_id": "ep_1"}) is SignalStrength.STANDARD

    def test_an_unrecognised_weight_is_treated_as_ordinary(self):
        assert signal_of({"signal_strength": "ENORMOUS"}) is SignalStrength.STANDARD


class TestBuildingAnchoredCandidates:
    def test_each_row_records_the_anchor_that_found_it(self):
        rows = [{"node_id": "obs_1", "_label": "ObservationNode", "content": "x"}]

        built = to_candidates(
            rows, anchor=StructuralAnchorType.NAMED_PERSON, value="Alex"
        )

        assert built[0].retrieval_source is CandidateRetrievalSource.STRUCTURAL
        assert built[0].structural_anchor_type is StructuralAnchorType.NAMED_PERSON
        assert built[0].structural_anchor_value == "Alex"

    def test_the_kind_of_node_comes_from_the_row(self):
        rows = [{"node_id": "bel_1", "_label": "BeliefNode", "belief_statement": "x"}]

        built = to_candidates(rows, anchor=StructuralAnchorType.HISTORICAL_ERA, value="e")

        assert built[0].node_type == "BeliefNode"

    def test_a_row_with_no_id_is_skipped(self):
        rows = [{"content": "no id here"}, {"node_id": "obs_1", "content": "fine"}]

        built = to_candidates(rows, anchor=StructuralAnchorType.NAMED_PERSON, value="A")

        assert [c.node_id for c in built] == ["obs_1"]

    def test_nothing_found_builds_nothing(self):
        assert to_candidates([], anchor=StructuralAnchorType.NAMED_PERSON, value="A") == []

    def test_an_anchored_candidate_carries_no_closeness(self):
        # It was not found by being close, and inventing a number would
        # invite reconciliation to compare it against ones that were.
        rows = [{"node_id": "obs_1", "_label": "ObservationNode", "content": "x"}]

        built = to_candidates(rows, anchor=StructuralAnchorType.NAMED_PERSON, value="A")

        assert built[0].similarity_score is None
