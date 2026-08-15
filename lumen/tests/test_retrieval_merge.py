"""
Tests for deciding which candidates actually reach reconciliation.

Two rules do all the work here and both are about what to lose.

A node found by both halves keeps its structural copy, because that copy
records which anchor led to it and the other one does not.

When there are too many, the resembling ones go first. That reads backwards
until you consider what each half is for: anything found by resemblance was
found by being close, so losing one loses a near-match, while anything
found by an anchor was found precisely because closeness would never have
reached it.
"""

from __future__ import annotations

import pytest

from lumen.pipeline.retrieval.merge import merge
from lumen.schemas.enums import CandidateRetrievalSource, StructuralAnchorType
from lumen.schemas.pipeline import CandidateNode, RetrievalResult


def resembling(node_id: str, score: float = 0.9) -> CandidateNode:
    return CandidateNode(
        node_id=node_id,
        node_type="ObservationNode",
        content_preview=f"content of {node_id}",
        similarity_score=score,
        retrieval_source=CandidateRetrievalSource.SEMANTIC,
    )


def anchored(node_id: str, anchor=StructuralAnchorType.NAMED_PERSON) -> CandidateNode:
    return CandidateNode(
        node_id=node_id,
        node_type="ObservationNode",
        content_preview=f"content of {node_id}",
        retrieval_source=CandidateRetrievalSource.STRUCTURAL,
        structural_anchor_type=anchor,
        structural_anchor_value="Alex",
    )


class TestBothHalvesArrive:
    def test_they_stay_apart(self):
        semantic, structural = merge([resembling("a")], [anchored("b")], cap=8)

        assert [c.node_id for c in semantic] == ["a"]
        assert [c.node_id for c in structural] == ["b"]

    def test_either_half_may_be_empty(self):
        semantic, structural = merge([], [anchored("b")], cap=8)

        assert semantic == []
        assert [c.node_id for c in structural] == ["b"]

    def test_nothing_at_all_is_fine(self):
        assert merge([], [], cap=8) == ([], [])


class TestRepeats:
    def test_a_node_found_twice_keeps_its_anchor(self):
        # The anchored copy records how it was found, which is information
        # the resembling copy does not carry.
        semantic, structural = merge([resembling("same")], [anchored("same")], cap=8)

        assert semantic == []
        assert [c.node_id for c in structural] == ["same"]
        assert structural[0].structural_anchor_type is StructuralAnchorType.NAMED_PERSON

    def test_a_repeat_within_one_half_is_dropped(self):
        semantic, _ = merge([resembling("a"), resembling("a")], [], cap=8)

        assert [c.node_id for c in semantic] == ["a"]

    def test_two_anchors_finding_the_same_node_keep_the_first(self):
        found_by_person = anchored("same", StructuralAnchorType.NAMED_PERSON)
        found_by_era = anchored("same", StructuralAnchorType.HISTORICAL_ERA)

        _, structural = merge([], [found_by_person, found_by_era], cap=8)

        assert len(structural) == 1
        assert structural[0].structural_anchor_type is StructuralAnchorType.NAMED_PERSON


class TestTheCap:
    def test_the_total_never_exceeds_it(self):
        semantic, structural = merge(
            [resembling(f"s{i}") for i in range(6)],
            [anchored(f"a{i}") for i in range(4)],
            cap=8,
        )

        assert len(semantic) + len(structural) == 8

    def test_resembling_candidates_lose_first(self):
        # Losing an anchored candidate loses the only route there was to
        # it; losing a resembling one loses a near-match among others.
        semantic, structural = merge(
            [resembling(f"s{i}") for i in range(6)],
            [anchored(f"a{i}") for i in range(5)],
            cap=6,
        )

        assert len(structural) == 5
        assert len(semantic) == 1

    def test_anchors_filling_the_cap_leave_no_room(self):
        semantic, structural = merge(
            [resembling("s0")], [anchored(f"a{i}") for i in range(8)], cap=8
        )

        assert semantic == []
        assert len(structural) == 8

    def test_the_best_resembling_ones_are_the_ones_kept(self):
        # The list arrives in the order the search ranked it — closeness
        # counted together with each node's weight — so taking from the
        # front honours that ranking rather than undoing it.
        ranked = [resembling("best", 0.95), resembling("middle", 0.8), resembling("worst", 0.6)]

        semantic, _ = merge(ranked, [], cap=2)

        assert [c.node_id for c in semantic] == ["best", "middle"]

    def test_more_anchors_than_the_cap_are_trimmed_as_a_last_resort(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING):
            semantic, structural = merge(
                [resembling("s0")], [anchored(f"a{i}") for i in range(10)], cap=8
            )

        assert len(structural) == 8
        assert semantic == []
        assert any("more anchored candidates" in r.getMessage() for r in caplog.records)


class TestTheResultAccepts:
    @pytest.mark.parametrize("cap", [1, 4, 8])
    def test_what_merge_produces_always_builds_a_result(self, cap):
        # The result model enforces the same limit independently. If the
        # two ever disagreed, a legitimate retrieval would fail to build.
        semantic, structural = merge(
            [resembling(f"s{i}") for i in range(10)],
            [anchored(f"a{i}") for i in range(10)],
            cap=cap,
        )

        result = RetrievalResult(
            source_node_id="obs_new",
            pass_a_candidates=semantic,
            pass_b_candidates=structural,
            retrieval_time_ms=1,
        )

        assert len(result.pass_a_candidates) + len(result.pass_b_candidates) <= 8

    def test_the_configured_cap_matches_what_the_result_allows(self):
        from lumen.config import PipelineConfig

        # Eight is the number the result model refuses to exceed. Keeping
        # the configured default equal to it means the merge cuts before
        # building something that would be rejected.
        assert PipelineConfig().merged_candidate_cap == 8
