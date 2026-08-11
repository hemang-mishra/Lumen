"""
Tests for pattern-based filler removal.

The important tests here are the ones about what is *not* removed. Stripping
an "um" is easy; the risk is a pattern that also eats "right" at the start of
a sentence or "like" used as a verb, which changes what somebody said. Those
words are handled by the model instead, and these tests hold that line.
"""

from __future__ import annotations

import pytest

from lumen.pipeline.preprocessing.fillers import strip_standalone_fillers


class TestRemoval:
    def test_removes_hesitation_sounds_and_tidies_the_punctuation(self):
        raw = "So um, I was like, really frustrated with uh the whole situation"
        cleaned, removed = strip_standalone_fillers(raw)

        assert removed == 2
        assert cleaned == "So, I was like, really frustrated with the whole situation"

    def test_leaves_a_line_that_starts_on_a_stray_comma_clean(self):
        cleaned, removed = strip_standalone_fillers("Hmm, I don't know")
        assert removed == 1
        assert cleaned == "I don't know"

    def test_collapses_the_gap_left_behind(self):
        cleaned, _ = strip_standalone_fillers("I went uh there")
        assert cleaned == "I went there"

    @pytest.mark.parametrize(
        "sound", ["uh", "um", "umm", "uhh", "hmm", "mmm", "erm", "er", "uh-huh", "mm-hmm"]
    )
    def test_every_listed_sound_is_recognised(self, sound):
        _, removed = strip_standalone_fillers(f"I said {sound} nothing")
        assert removed == 1

    def test_matching_ignores_capitalisation(self):
        _, removed = strip_standalone_fillers("Um I think so")
        assert removed == 1

    def test_counts_every_removal(self):
        _, removed = strip_standalone_fillers("um uh um hmm")
        assert removed == 4


class TestPreservation:
    def test_discourse_opener_right_survives(self):
        raw = "Right so the issue was basically that nobody told me"
        cleaned, removed = strip_standalone_fillers(raw)

        assert removed == 0
        assert cleaned == raw

    @pytest.mark.parametrize(
        "word", ["like", "you know", "right", "basically", "literally"]
    )
    def test_context_dependent_words_are_left_for_the_model(self, word):
        raw = f"I {word} said it"
        cleaned, removed = strip_standalone_fillers(raw)

        assert removed == 0
        assert cleaned == raw

    def test_a_filler_spelling_inside_a_longer_word_is_untouched(self):
        raw = "I bought an umbrella and a hummus wrap"
        cleaned, removed = strip_standalone_fillers(raw)

        assert removed == 0
        assert cleaned == raw

    def test_longer_spellings_are_matched_whole(self):
        # "uh-huh" must not be torn apart into "uh" plus a dangling "-huh".
        cleaned, removed = strip_standalone_fillers("She said uh-huh and moved on")
        assert removed == 1
        assert "huh" not in cleaned

    def test_paragraph_breaks_survive(self):
        cleaned, _ = strip_standalone_fillers("First um thought.\n\nSecond thought.")
        assert cleaned == "First thought.\n\nSecond thought."


class TestEdges:
    def test_empty_text_is_returned_unchanged(self):
        assert strip_standalone_fillers("") == ("", 0)

    def test_text_with_nothing_to_remove_is_not_reformatted(self):
        raw = "  spacing   that would   be tidied  if we touched it  "
        cleaned, removed = strip_standalone_fillers(raw)

        assert removed == 0
        assert cleaned == raw

    def test_text_that_is_only_fillers_becomes_empty(self):
        cleaned, removed = strip_standalone_fillers("um uh hmm")
        assert removed == 3
        assert cleaned == ""
