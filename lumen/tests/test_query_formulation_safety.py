"""
The two checks that run before any model is asked anything.

One of them protects somebody having the worst moment of their year. The
other saves a call on "yeah". They are tested together because they are the
same kind of thing — a fixed rule in plain code, deliberately dull, standing
in front of a judgement that cannot be relied on absolutely.
"""

from __future__ import annotations

import json

import pytest

from lumen.query.formulation import safety, triage


class TestNormalising:
    @pytest.mark.parametrize(
        "written",
        ["I CANT GO ON ANYMORE!!", "i  cant   go on anymore.", "I cant go on anymore"],
    )
    def test_capitalisation_and_spacing_normalise_away(self, written):
        assert safety.normalise(written) == "i cant go on anymore"

    def test_punctuation_becomes_a_gap_rather_than_disappearing(self):
        # Removing it outright would join two words into one and let a
        # phrase hide inside an unrelated sentence.
        assert safety.normalise("done.finished") == "done finished"

    def test_an_apostrophe_splits_the_word_it_sits_in(self):
        # A consequence of the rule above, and the reason the phrase list
        # carries both spellings of the contractions it needs. Both still
        # reach the same decision, which is what actually matters.
        assert safety.normalise("I can't go on anymore") == "i can t go on anymore"
        assert safety.in_crisis("I can't go on anymore")
        assert safety.in_crisis("I cant go on anymore")

    def test_an_empty_sentence_normalises_to_nothing(self):
        assert safety.normalise("   ") == ""


class TestTheDistressFloor:
    @pytest.mark.parametrize("phrase", sorted(safety.CRISIS_PHRASES))
    def test_every_listed_phrase_fires(self, phrase):
        assert safety.in_crisis(f"honestly {phrase} lately")

    @pytest.mark.parametrize(
        "written",
        [
            "I don't want to be here anymore, I just want to die",
            "sometimes I think I'd be better off dead",
            "I've been having suicidal thoughts again",
            "I can't go on anymore",
        ],
    )
    def test_it_fires_on_how_people_actually_write(self, written):
        assert safety.in_crisis(written)

    @pytest.mark.parametrize(
        "written",
        [
            "I'm feeling pretty low today",
            "work has been crushing me this week",
            "I was really upset after that conversation",
            "I felt like giving up on the project",
        ],
    )
    def test_it_stays_quiet_on_ordinary_difficulty(self, written):
        # A floor that fired on sadness would switch off the memory of a
        # system whose entire purpose is remembering somebody's difficulty.
        assert not safety.in_crisis(written)

    def test_an_empty_turn_is_not_a_crisis(self):
        assert not safety.in_crisis("   ")

    def test_capitalisation_and_punctuation_cannot_hide_a_phrase(self):
        assert safety.in_crisis("I. WANT. TO. DIE.")

    def test_what_is_logged_is_the_phrase_and_not_the_sentence(self, captured_logs):
        # Knowing the floor fired is useful. Writing somebody's worst moment
        # into a log file is not.
        safety.in_crisis("after everything today I just want to die honestly")

        written = json.dumps(captured_logs)
        assert "want to die" in written
        assert "after everything today" not in written

    def test_the_list_is_small_enough_to_read(self):
        # Every entry has to be unambiguous in every reading. A list that
        # grew past a page would stop being checkable by eye, which is the
        # only reason to trust it over the model.
        assert len(safety.CRISIS_PHRASES) <= 40


class TestTurnsNotWorthACall:
    @pytest.mark.parametrize(
        "written", ["yeah", "Go on.", "THANKS!", "  ok  ", "makes sense"]
    )
    def test_a_plain_acknowledgement_is_trivial(self, written):
        assert triage.is_trivial(written)

    @pytest.mark.parametrize(
        "written",
        [
            "right, so about my father",
            "yeah, that's exactly what happened with my sister",
            "ok but why does it always come back to this",
            "I can't anymore",
            "true of everything since school",
        ],
    )
    def test_a_sentence_that_merely_starts_that_way_is_not(self, written):
        assert not triage.is_trivial(written)

    def test_short_is_not_the_same_as_trivial(self):
        # The shortest turns in this kind of conversation are frequently the
        # heaviest, so nothing here may be a length rule.
        assert not triage.is_trivial("I hate him.")
        assert not triage.is_trivial("she left")

    def test_nothing_trivial_would_also_trip_the_floor(self):
        # If the two lists ever overlapped, the order they run in would
        # start to matter and a distress phrase could be answered as small
        # talk.
        assert not any(safety.in_crisis(phrase) for phrase in triage.TRIVIAL_TURNS)
