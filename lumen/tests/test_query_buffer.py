"""
What today's conversation keeps hold of.

The buffer is small and its rules interact, which is the whole reason it
gets its own suite. Five slots, five turns of patience, and one class of
record that is never pushed out — any two of those are simple and all three
together produce a state the specification did not describe: a buffer full
of protected records with something new arriving.
"""

from __future__ import annotations

import pytest

from lumen.query.buffer import (
    BufferEntry,
    SessionContextBuffer,
    cosine,
    word_overlap,
)
from lumen.schemas.enums import SignalStrength


def entry(
    node_id: str,
    *,
    signal: SignalStrength = SignalStrength.STANDARD,
    preview: str = "an earlier note about the same thing",
    vector: tuple[float, ...] | None = None,
) -> BufferEntry:
    """One remembered record, with only the fields a test cares about set."""
    return BufferEntry(
        node_id=node_id,
        node_type="ObservationNode",
        preview=preview,
        signal_strength=signal,
        vector=vector,
    )


class TestPuttingThingsIn:
    def test_a_record_is_held_after_it_is_surfaced(self):
        buffer = SessionContextBuffer()

        buffer.remember([entry("obs_1")], turn_index=3)

        assert "obs_1" in buffer
        assert buffer.node_ids == ("obs_1",)

    def test_the_turn_it_arrived_on_is_recorded(self):
        buffer = SessionContextBuffer()

        buffer.remember([entry("obs_1")], turn_index=3)

        held = buffer.entries[0]
        assert held.first_seen_turn == 3
        assert held.last_relevant_turn == 3

    def test_seeing_a_record_again_refreshes_it_rather_than_duplicating_it(self):
        buffer = SessionContextBuffer()
        buffer.remember([entry("obs_1")], turn_index=1)

        buffer.remember([entry("obs_1")], turn_index=6)

        assert len(buffer) == 1
        # The first sighting is what it says: this is the same subject
        # coming back, not a new one.
        assert buffer.entries[0].first_seen_turn == 1
        assert buffer.entries[0].last_relevant_turn == 6

    def test_a_refreshed_record_gains_a_position_it_was_missing(self):
        # A record that entered before its vector could be read gets one the
        # next time it comes up, rather than staying on word overlap forever.
        buffer = SessionContextBuffer()
        buffer.remember([entry("obs_1", vector=None)], turn_index=1)

        buffer.remember([entry("obs_1", vector=(1.0, 0.0))], turn_index=2)

        assert buffer.entries[0].vector == (1.0, 0.0)

    def test_marking_relevant_keeps_a_record_alive_without_adding_one(self):
        buffer = SessionContextBuffer()
        buffer.remember([entry("obs_1")], turn_index=1)

        buffer.mark_relevant(["obs_1", "obs_never_seen"], turn_index=7)

        assert buffer.node_ids == ("obs_1",)
        assert buffer.entries[0].last_relevant_turn == 7

    def test_the_most_recently_relevant_comes_first(self):
        buffer = SessionContextBuffer()
        buffer.remember([entry("obs_old")], turn_index=1)
        buffer.remember([entry("obs_new")], turn_index=5)

        assert [held.node_id for held in buffer.entries] == ["obs_new", "obs_old"]


class TestMakingRoom:
    def test_a_full_buffer_drops_the_least_missed_record(self):
        buffer = SessionContextBuffer(max_entries=2)
        buffer.remember([entry("obs_stale")], turn_index=1)
        buffer.remember([entry("obs_recent")], turn_index=4)

        buffer.remember([entry("obs_new")], turn_index=5)

        assert set(buffer.node_ids) == {"obs_recent", "obs_new"}

    def test_the_heaviest_records_are_not_the_ones_dropped(self):
        # Somebody who raises the hardest thing in their history and then
        # talks about work for ten minutes has not stopped being in the
        # middle of it.
        buffer = SessionContextBuffer(max_entries=2)
        buffer.remember(
            [entry("obs_critical", signal=SignalStrength.CRITICAL)], turn_index=1
        )
        buffer.remember([entry("obs_ordinary")], turn_index=4)

        buffer.remember([entry("obs_new")], turn_index=5)

        assert "obs_critical" in buffer
        assert "obs_ordinary" not in buffer

    def test_a_buffer_full_of_protected_records_refuses_the_newcomer(self):
        # The state the specification's two rules produce together and never
        # names. Nothing is lost — the record was still offered on this
        # turn — it just does not get carried forward.
        buffer = SessionContextBuffer(max_entries=2)
        for index in range(2):
            buffer.remember(
                [entry(f"obs_{index}", signal=SignalStrength.CRITICAL)],
                turn_index=index,
            )

        buffer.remember([entry("obs_new")], turn_index=5)

        assert "obs_new" not in buffer
        assert len(buffer) == 2

    def test_a_buffer_with_no_room_at_all_holds_nothing(self):
        buffer = SessionContextBuffer(max_entries=0)

        buffer.remember([entry("obs_1")], turn_index=1)

        assert len(buffer) == 0


class TestLettingGo:
    def test_a_record_nobody_returns_to_drops_out(self):
        buffer = SessionContextBuffer(max_idle_turns=5)
        buffer.remember([entry("obs_1")], turn_index=1)

        dropped = buffer.evict_stale(turn_index=6)

        assert dropped == ("obs_1",)
        assert len(buffer) == 0

    def test_a_record_still_within_its_patience_stays(self):
        buffer = SessionContextBuffer(max_idle_turns=5)
        buffer.remember([entry("obs_1")], turn_index=1)

        assert buffer.evict_stale(turn_index=5) == ()
        assert "obs_1" in buffer

    def test_the_heaviest_records_survive_a_long_digression(self):
        buffer = SessionContextBuffer(max_idle_turns=2)
        buffer.remember(
            [entry("obs_critical", signal=SignalStrength.CRITICAL)], turn_index=1
        )

        buffer.evict_stale(turn_index=40)

        assert "obs_critical" in buffer

    def test_clearing_forgets_everything(self):
        buffer = SessionContextBuffer()
        buffer.remember(
            [entry("obs_critical", signal=SignalStrength.CRITICAL)], turn_index=1
        )

        buffer.clear()

        assert len(buffer) == 0


class TestWhatStillApplies:
    def test_a_record_pointing_the_same_way_is_relevant(self):
        buffer = SessionContextBuffer()
        buffer.remember([entry("obs_1", vector=(1.0, 0.0))], turn_index=1)

        still = buffer.relevant_to(vector=[1.0, 0.0], keywords=(), threshold=0.35)

        assert [held.node_id for held, _ in still] == ["obs_1"]
        assert still[0][1] == pytest.approx(1.0)

    def test_a_record_pointing_elsewhere_is_not(self):
        buffer = SessionContextBuffer()
        buffer.remember([entry("obs_1", vector=(0.0, 1.0))], turn_index=1)

        still = buffer.relevant_to(vector=[1.0, 0.0], keywords=(), threshold=0.35)

        assert still == []

    def test_the_closest_comes_first(self):
        buffer = SessionContextBuffer()
        buffer.remember(
            [
                entry("obs_near", vector=(1.0, 0.0)),
                entry("obs_further", vector=(0.7, 0.7)),
            ],
            turn_index=1,
        )

        still = buffer.relevant_to(vector=[1.0, 0.0], keywords=(), threshold=0.3)

        assert [held.node_id for held, _ in still] == ["obs_near", "obs_further"]

    def test_a_record_with_no_stored_position_falls_back_to_words(self):
        # What an old or partly-indexed graph looks like. A blunter measure
        # is used rather than none, because a conversation losing its thread
        # is worse than a slightly wrong sense of relevance.
        buffer = SessionContextBuffer()
        buffer.remember(
            [entry("obs_1", preview="the resistance to going out alone")],
            turn_index=1,
        )

        still = buffer.relevant_to(
            vector=[1.0, 0.0], keywords=("resistance", "alone"), threshold=0.5
        )

        assert [held.node_id for held, _ in still] == ["obs_1"]

    def test_no_position_on_either_side_also_falls_back_to_words(self):
        buffer = SessionContextBuffer()
        buffer.remember([entry("obs_1", preview="going out alone")], turn_index=1)

        still = buffer.relevant_to(
            vector=None, keywords=("alone",), threshold=0.5
        )

        assert [held.node_id for held, _ in still] == ["obs_1"]

    def test_an_empty_buffer_has_nothing_to_say(self):
        buffer = SessionContextBuffer()

        assert buffer.relevant_to(vector=[1.0], keywords=(), threshold=0.0) == []


class TestTheMeasures:
    def test_identical_directions_score_one(self):
        assert cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_opposite_directions_are_floored_at_zero(self):
        # A negative similarity is a real measurement and a meaningless
        # relevance, so it is clamped rather than allowed to rank below
        # nothing.
        assert cosine([1.0, 0.0], [-1.0, 0.0]) == 0.0

    def test_different_widths_score_zero_rather_than_raising(self):
        # Only happens when the embedding model changed under a running
        # process, which is reported elsewhere. It should cost this turn its
        # continuity, not the conversation.
        assert cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0

    def test_an_empty_vector_scores_zero(self):
        assert cosine([], []) == 0.0

    def test_a_zero_length_vector_scores_zero(self):
        assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_word_overlap_counts_the_share_of_words_present(self):
        assert word_overlap(
            ("resistance", "alone", "campus"), "the resistance to being alone"
        ) == pytest.approx(2 / 3)

    def test_word_overlap_ignores_case(self):
        assert word_overlap(("Resistance",), "the resistance") == 1.0

    def test_word_overlap_with_nothing_to_compare_is_zero(self):
        assert word_overlap((), "anything at all") == 0.0
        assert word_overlap(("   ",), "anything at all") == 0.0


class TestTheTwoMeasurementsAreNotOneScale:
    """
    Relevance is measured two ways and they do not mean the same thing at the
    same number. A cosine of 0.4 between a question and a record is a real
    resemblance; a word overlap of 0.4 means two of five keywords appear
    somewhere in the text, which happens by accident.

    Held to one threshold, the stand-in waves through everything the buffer
    holds — on exactly the turns where the search had already failed to
    produce a vector, so the conversation is least able to afford it.
    """

    def test_the_stand_in_is_held_to_its_own_harder_bar(self):
        buffer = SessionContextBuffer()
        buffer.remember(
            [
                BufferEntry(
                    node_id="pat_1",
                    node_type="PatternNode",
                    preview="counting hours instead of counting progress",
                )
            ],
            turn_index=0,
        )

        # Two of five words appear: enough for a lenient bar, not for this one.
        assert (
            buffer.relevant_to(
                vector=None,
                keywords=("counting", "hours", "sleep", "money", "family"),
                threshold=0.35,
                keyword_threshold=0.6,
            )
            == []
        )

    def test_a_real_overlap_still_gets_through(self):
        buffer = SessionContextBuffer()
        buffer.remember(
            [
                BufferEntry(
                    node_id="pat_1",
                    node_type="PatternNode",
                    preview="counting hours instead of counting progress",
                )
            ],
            turn_index=0,
        )

        found = buffer.relevant_to(
            vector=None,
            keywords=("counting", "hours", "progress"),
            threshold=0.35,
            keyword_threshold=0.6,
        )

        assert [entry.node_id for entry, _ in found] == ["pat_1"]

    def test_the_measured_comparison_keeps_the_measured_bar(self):
        # A record with a position in the index is judged on distance, and
        # the harder bar for the stand-in must not leak onto it.
        buffer = SessionContextBuffer()
        buffer.remember(
            [
                BufferEntry(
                    node_id="pat_1",
                    node_type="PatternNode",
                    preview="nothing in common with the words asked about",
                    vector=(1.0, 0.0),
                )
            ],
            turn_index=0,
        )

        found = buffer.relevant_to(
            vector=[0.8, 0.6],  # cosine 0.8 — comfortably relevant
            keywords=("nothing", "shared"),
            threshold=0.35,
            keyword_threshold=0.95,
        )

        assert [entry.node_id for entry, _ in found] == ["pat_1"]

    def test_one_threshold_is_still_allowed_and_applies_to_both(self):
        # The second bar is optional, so nothing that only knows about one
        # threshold has to be changed to keep working.
        buffer = SessionContextBuffer()
        buffer.remember(
            [
                BufferEntry(
                    node_id="pat_1",
                    node_type="PatternNode",
                    preview="counting hours instead of counting progress",
                )
            ],
            turn_index=0,
        )

        found = buffer.relevant_to(
            vector=None,
            keywords=("counting", "hours", "sleep", "money", "family"),
            threshold=0.35,
        )

        assert [entry.node_id for entry, _ in found] == ["pat_1"]
