"""
Tests for counting which records keep turning out to be the useful ones.

Every rule here is about what *not* to count. A search returns a dozen
candidates and three reach the assistant; a conversation circles one subject
for twenty turns; a database refuses a write while somebody is mid-sentence.
Counting the wrong thing in any of those makes the counter a measure of
something else, and the number feeds straight back into what gets found next
time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lumen.tests.conftest import registry_for

from lumen.config import ScoringConfig
from lumen.query.assembly.contracts import AssembledContext, ContextItem
from lumen.query.frequency import QueryHitRecorder
from lumen.query.session import ChatSession
from lumen.schemas.enums import RetrievalPass

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


def briefing(*node_ids: str) -> AssembledContext:
    """A briefing that put these records in front of the assistant."""
    return AssembledContext(
        items=tuple(
            ContextItem(
                node_id=node_id,
                node_type="PatternNode",
                text=f"a line about {node_id}",
                tokens=6,
                found_by=RetrievalPass.SEMANTIC,
            )
            for node_id in node_ids
        )
    )


@pytest.fixture
def session():
    """One day's conversation, in memory."""
    return ChatSession(
        session_id="tester_2026_08_20", user_id="tester", event_date=NOW.date()
    )


@pytest.fixture
def recorder(graph_store):
    """The recorder, writing to a real graph."""
    return QueryHitRecorder(registry_for(graph_store), config=ScoringConfig())


class TestWhatCounts:
    def test_a_record_that_reached_the_assistant_is_counted(
        self, recorder, session, graph_store, seed_pattern
    ):
        seed_pattern("pat_1")

        assert recorder.note(session, briefing("pat_1"), at=NOW) == 1
        assert graph_store.get_node("pat_1")["query_frequency"] == 1

    def test_what_was_found_and_left_out_is_not_counted(
        self, recorder, session, graph_store, seed_pattern
    ):
        # The counter has to measure what helped, not what the search liked.
        # It feeds back into ranking, so measuring the search with it would
        # make the search agree with itself more every day.
        seed_pattern("pat_kept")
        seed_pattern("pat_dropped")

        recorder.note(session, briefing("pat_kept"), at=NOW)

        assert graph_store.get_node("pat_kept")["query_frequency"] == 1
        assert graph_store.get_node("pat_dropped")["query_frequency"] == 0

    def test_kinds_that_keep_no_counter_are_skipped_quietly(
        self, recorder, session, seed_pattern, seed_observation
    ):
        # A conversation's briefing is mostly notes and episodes. Those keep
        # no counter, and that is not an error.
        seed_pattern("pat_1")
        seed_observation("obs_1", "a note")

        assert recorder.note(session, briefing("pat_1", "obs_1"), at=NOW) == 1

    def test_an_empty_briefing_costs_nothing(self, recorder, session):
        assert recorder.note(session, AssembledContext(), at=NOW) == 0


class TestOncePerDay:
    def test_a_record_used_all_afternoon_counts_once(
        self, recorder, session, graph_store, seed_pattern
    ):
        # Somebody who spends a whole conversation on one subject has one
        # concern, not twenty. Without this, a single afternoon would
        # outrank years of history permanently.
        seed_pattern("pat_1")

        for _ in range(12):
            recorder.note(session, briefing("pat_1"), at=NOW)

        assert graph_store.get_node("pat_1")["query_frequency"] == 1

    def test_the_same_record_counts_again_tomorrow(
        self, recorder, graph_store, seed_pattern
    ):
        seed_pattern("pat_1")
        today = ChatSession(
            session_id="tester_2026_08_20", user_id="tester", event_date=NOW.date()
        )
        tomorrow = ChatSession(
            session_id="tester_2026_08_21",
            user_id="tester",
            event_date=(NOW + timedelta(days=1)).date(),
        )

        recorder.note(today, briefing("pat_1"), at=NOW)
        recorder.note(tomorrow, briefing("pat_1"), at=NOW + timedelta(days=1))

        assert graph_store.get_node("pat_1")["query_frequency"] == 2

    def test_claiming_and_reporting_are_one_step(self, session):
        # Two calls would leave a gap where the same record could be claimed
        # twice, which is the whole thing this prevents.
        assert session.claim_query_hits(["a", "b"]) == ["a", "b"]
        assert session.claim_query_hits(["a", "b", "c"]) == ["c"]

    def test_a_record_named_twice_in_one_briefing_counts_once(
        self, recorder, session, graph_store, seed_pattern
    ):
        seed_pattern("pat_1")

        assert recorder.note(session, briefing("pat_1", "pat_1"), at=NOW) == 1


class TestWhenSomethingGoesWrong:
    def test_a_graph_that_refuses_the_write_costs_the_count_and_nothing_else(
        self, session, captured_logs
    ):
        # Nobody is waiting on this — it runs after the reply has gone out —
        # and a lost count costs a record a fraction of a point of ranking.
        class Refuses:
            def record_query_hits(self, node_ids, *, at):
                raise RuntimeError("the graph is busy")

        recorder = QueryHitRecorder(Refuses(), config=ScoringConfig())

        assert recorder.note(session, briefing("pat_1"), at=NOW) == 0

    def test_counting_can_be_switched_off(self, session, seed_pattern, graph_store):
        seed_pattern("pat_1")
        recorder = QueryHitRecorder(
            graph_store, config=ScoringConfig(frequency_enabled=False)
        )

        assert recorder.note(session, briefing("pat_1"), at=NOW) == 0
        assert graph_store.get_node("pat_1")["query_frequency"] == 0

    def test_an_unknown_record_is_not_an_error(self, recorder, session):
        assert recorder.note(session, briefing("pat_never_existed"), at=NOW) == 0
