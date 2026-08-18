"""
Settling three answers into one list.

Two rules carry this file. A record found by an anchor keeps the copy that
knows why it was found. When there is too much, the resembling ones go
first — which reads backwards until you remember that an anchor was the only
route to its record, while a near-match is one of many.
"""

from __future__ import annotations

import pytest

from lumen.query.retrieval import merge
from lumen.query.retrieval.contracts import RetrievedNode
from lumen.schemas.enums import RetrievalPass, StructuralAnchorType


def node(
    node_id: str,
    *,
    found_by: RetrievalPass = RetrievalPass.SEMANTIC,
    score: float = 0.5,
    anchor: StructuralAnchorType | None = None,
) -> RetrievedNode:
    """One candidate, with only what the merge reads set."""
    return RetrievedNode(
        node_id=node_id,
        node_type="PatternNode",
        preview="a record",
        found_by=found_by,
        similarity=score if found_by is RetrievalPass.SEMANTIC else None,
        anchor_type=anchor,
        rank_score=score,
    )


class TestWhichCopySurvives:
    def test_a_record_found_both_ways_keeps_the_anchor_copy(self):
        # The anchor copy knows *why* it is here — this person's name, this
        # period of their life — and that changes how much to trust it. The
        # resembling copy knows only that the words were close.
        kept = merge.merge(
            [node("pat_1", score=0.9)],
            [
                node(
                    "pat_1",
                    found_by=RetrievalPass.STRUCTURAL,
                    score=0.6,
                    anchor=StructuralAnchorType.NAMED_PERSON,
                )
            ],
            cap=10,
        )

        assert len(kept) == 1
        assert kept[0].found_by is RetrievalPass.STRUCTURAL
        assert kept[0].anchor_type is StructuralAnchorType.NAMED_PERSON

    def test_two_copies_from_one_search_keep_the_better_match(self):
        kept = merge.merge(
            [node("pat_1", score=0.3), node("pat_1", score=0.9)], cap=10
        )

        assert kept[0].rank_score == pytest.approx(0.9)

    def test_a_carried_copy_yields_to_a_found_one(self):
        kept = merge.merge(
            [node("pat_1", found_by=RetrievalPass.CONTINUITY, score=0.9)],
            [node("pat_1", score=0.4)],
            cap=10,
        )

        assert kept[0].found_by is RetrievalPass.SEMANTIC


class TestOrderAndCap:
    def test_the_best_scoring_comes_first(self):
        kept = merge.merge(
            [node("pat_low", score=0.2), node("pat_high", score=0.9)], cap=10
        )

        assert [item.node_id for item in kept] == ["pat_high", "pat_low"]

    def test_an_anchor_wins_a_tie(self):
        # A tie broken towards the copy that knows why it exists.
        kept = merge.merge(
            [node("pat_semantic", score=0.5)],
            [node("pat_anchor", found_by=RetrievalPass.STRUCTURAL, score=0.5)],
            cap=10,
        )

        assert [item.node_id for item in kept] == ["pat_anchor", "pat_semantic"]

    def test_the_cap_is_respected(self):
        kept = merge.merge(
            [node(f"pat_{index}", score=index / 10) for index in range(6)], cap=2
        )

        assert len(kept) == 2

    def test_a_cap_of_nothing_keeps_nothing(self):
        assert merge.merge([node("pat_1")], cap=0) == []

    def test_nothing_found_stays_nothing(self):
        assert merge.merge([], [], cap=5) == []


class TestTheContinuityBoost:
    def test_a_record_today_has_already_seen_counts_for_more(self):
        kept = merge.merge(
            [node("pat_1", score=0.5)],
            boosts={"pat_1": 0.8},
            boost_multiplier=1.3,
            cap=5,
        )

        assert kept[0].rank_score == pytest.approx(0.65)
        assert kept[0].boosted is True

    def test_a_record_today_has_not_seen_is_left_alone(self):
        kept = merge.merge(
            [node("pat_1", score=0.5)],
            boosts={"pat_other": 0.8},
            boost_multiplier=1.3,
            cap=5,
        )

        assert kept[0].rank_score == pytest.approx(0.5)
        assert kept[0].boosted is False

    def test_a_carried_record_is_not_boosted_twice(self):
        # It already carries the boost from being carried.
        carried = node("pat_1", found_by=RetrievalPass.CONTINUITY, score=1.3)

        kept = merge.merge(
            [carried.model_copy(update={"boosted": True})],
            boosts={"pat_1": 1.0},
            boost_multiplier=1.3,
            cap=5,
        )

        assert kept[0].rank_score == pytest.approx(1.3)

    def test_the_boost_can_change_the_order(self):
        # The point of the boost: the thread of this conversation beats a
        # slightly closer record arriving cold.
        kept = merge.merge(
            [node("pat_cold", score=0.6), node("pat_thread", score=0.5)],
            boosts={"pat_thread": 0.9},
            boost_multiplier=1.3,
            cap=5,
        )

        assert [item.node_id for item in kept] == ["pat_thread", "pat_cold"]
