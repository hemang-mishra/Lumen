"""
Pass C — carrying today's thread into the next turn.

Two behaviours, and they are easy to confuse. A remembered record the other
searches did not find is offered again as a candidate of its own. A
remembered record they did find is not duplicated — it is named so the merge
can lift the copy that already exists.
"""

from __future__ import annotations

import pytest

from lumen.config import QueryConfig
from lumen.query.buffer import BufferEntry, SessionContextBuffer
from lumen.query.retrieval import continuity
from lumen.query.retrieval.contracts import RetrievedNode
from lumen.schemas.enums import RetrievalPass, SignalStrength


def remembered(node_id: str, vector=(1.0, 0.0), **fields) -> BufferEntry:
    """One record already surfaced today."""
    return BufferEntry(
        node_id=node_id,
        node_type=fields.pop("node_type", "PatternNode"),
        preview=fields.pop("preview", "the critic brain pattern"),
        vector=vector,
        **fields,
    )


def found(node_id: str) -> RetrievedNode:
    """One record this turn's other searches turned up."""
    return RetrievedNode(
        node_id=node_id,
        node_type="PatternNode",
        preview="found this turn",
        found_by=RetrievalPass.SEMANTIC,
        similarity=0.8,
        rank_score=0.8,
    )


@pytest.fixture
def buffer():
    """A buffer holding one relevant record."""
    held = SessionContextBuffer()
    held.remember([remembered("pat_critic")], turn_index=3)
    return held


class TestOfferingSomethingAgain:
    def test_a_still_relevant_record_nobody_found_is_offered_again(self, buffer):
        # The case this pass exists for. The afternoon's realisation and the
        # evening's question about where it started share no wording at all.
        revisited, boosts = continuity.revisit(
            buffer,
            already_found=set(),
            query_vector=[1.0, 0.0],
            keywords=(),
            config=QueryConfig(),
        )

        assert [node.node_id for node in revisited] == ["pat_critic"]
        assert boosts == {"pat_critic": pytest.approx(1.0)}

    def test_it_is_marked_as_carried_rather_than_found(self, buffer):
        revisited, _ = continuity.revisit(
            buffer,
            already_found=set(),
            query_vector=[1.0, 0.0],
            keywords=(),
            config=QueryConfig(),
        )

        assert revisited[0].found_by is RetrievalPass.CONTINUITY
        assert revisited[0].boosted is True

    def test_being_part_of_today_counts_for_more(self, buffer):
        revisited, _ = continuity.revisit(
            buffer,
            already_found=set(),
            query_vector=[1.0, 0.0],
            keywords=(),
            config=QueryConfig(session_boost_multiplier=1.3),
        )

        assert revisited[0].rank_score == pytest.approx(1.3)

    def test_a_record_the_others_already_found_is_not_offered_twice(self, buffer):
        revisited, boosts = continuity.revisit(
            buffer,
            already_found={"pat_critic"},
            query_vector=[1.0, 0.0],
            keywords=(),
            config=QueryConfig(),
        )

        assert revisited == []
        # Named anyway, so the merge can lift the copy that exists.
        assert "pat_critic" in boosts

    def test_a_record_that_no_longer_applies_is_left_alone(self, buffer):
        revisited, boosts = continuity.revisit(
            buffer,
            already_found=set(),
            query_vector=[0.0, 1.0],
            keywords=(),
            config=QueryConfig(),
        )

        assert revisited == []
        assert boosts == {}

    def test_an_empty_thread_costs_nothing(self):
        revisited, boosts = continuity.revisit(
            SessionContextBuffer(),
            already_found=set(),
            query_vector=[1.0, 0.0],
            keywords=(),
            config=QueryConfig(),
        )

        assert (revisited, boosts) == ([], {})

    def test_without_a_measurement_it_falls_back_to_words(self):
        # What happens when the meaning-based search could not run. Blunter,
        # and better than a conversation that forgets itself.
        held = SessionContextBuffer()
        held.remember(
            [remembered("pat_critic", vector=None, preview="the critic brain")],
            turn_index=1,
        )

        revisited, _ = continuity.revisit(
            held,
            already_found=set(),
            query_vector=None,
            keywords=("critic",),
            config=QueryConfig(),
        )

        assert [node.node_id for node in revisited] == ["pat_critic"]


class TestRememberingWhatSurvived:
    def test_a_kept_record_becomes_something_worth_remembering(self):
        entries = continuity.to_entries([found("pat_1")], vectors={})

        assert entries[0].node_id == "pat_1"
        assert entries[0].preview == "found this turn"

    def test_its_position_is_kept_where_the_index_knows_one(self):
        # Cached once, so every later turn's comparison is arithmetic rather
        # than another search.
        entries = continuity.to_entries(
            [found("pat_1")], vectors={"pat_1": [0.5, 0.5]}
        )

        assert entries[0].vector == (0.5, 0.5)

    def test_a_record_the_index_has_never_seen_keeps_no_position(self):
        entries = continuity.to_entries([found("pat_1")], vectors={"other": [1.0]})

        assert entries[0].vector is None

    def test_the_weight_travels_with_it(self):
        # It is what decides whether this record can be pushed out later.
        node = found("pat_1").model_copy(
            update={"signal_strength": SignalStrength.CRITICAL}
        )

        entries = continuity.to_entries([node], vectors={})

        assert entries[0].protected is True
