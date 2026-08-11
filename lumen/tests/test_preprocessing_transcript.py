"""
Tests for the deterministic half of preprocessing.

Nothing here involves a language model, so every assertion is exact. That
matters most for the discard check: the rule that throws someone's writing
away is arithmetic, and these tests are what prove it stays arithmetic.
"""

from __future__ import annotations

from datetime import date

from lumen.pipeline.preprocessing import transcript
from lumen.schemas.enums import DialogueAct, SourceModality


class TestChatDetection:
    def test_buffer_with_an_assistant_reply_is_a_conversation(self, make_event):
        event = make_event([("USER", "hi"), ("AI", "hello"), ("USER", "so anyway")])
        assert transcript.is_chat_buffer(event) is True

    def test_buffer_of_only_user_messages_is_not_a_conversation(self, make_event):
        event = make_event([("USER", "one"), ("USER", "two")])
        assert transcript.is_chat_buffer(event) is False

    def test_empty_buffer_is_not_a_conversation(self, make_event):
        assert transcript.is_chat_buffer(make_event([])) is False


class TestUserMessages:
    def test_assistant_messages_are_dropped(self, make_event):
        event = make_event(
            [("USER", "mine"), ("AI", "not mine"), ("USER", "also mine")]
        )
        kept = transcript.user_messages(event.raw_buffer)
        assert [message.content for message in kept] == ["mine", "also mine"]

    def test_nothing_survives_an_assistant_only_buffer(self, make_event):
        event = make_event([("AI", "hello"), ("AI", "anyone there")])
        assert transcript.user_messages(event.raw_buffer) == []


class TestRendering:
    def test_monologue_joins_messages_with_blank_lines(self, make_event):
        event = make_event([("USER", "first"), ("USER", "second")])
        assert transcript.render_monologue(event.raw_buffer) == "first\n\nsecond"

    def test_monologue_skips_blank_messages(self, make_event):
        event = make_event([("USER", "first"), ("USER", "   "), ("USER", "third")])
        assert transcript.render_monologue(event.raw_buffer) == "first\n\nthird"

    def test_dialogue_labels_every_line_with_speaker_and_id(self, make_event):
        event = make_event([("USER", "hi"), ("AI", "hello")])
        rendered = transcript.render_dialogue(event.raw_buffer)
        assert rendered == "[m0] USER: hi\n[m1] AI: hello"

    def test_dialogue_skips_blank_messages(self, make_event):
        event = make_event([("USER", "hi"), ("AI", "  "), ("USER", "still here")])
        assert "  " not in transcript.render_dialogue(event.raw_buffer)


class TestWordCount:
    def test_counts_whitespace_separated_words(self):
        assert transcript.word_count("one two three") == 3

    def test_collapses_runs_of_whitespace(self):
        assert transcript.word_count("one   two\n\nthree\tfour") == 4

    def test_empty_text_counts_zero(self):
        assert transcript.word_count("   ") == 0


class TestTextHash:
    def test_same_text_gives_the_same_fingerprint(self):
        assert transcript.text_hash("hello") == transcript.text_hash("hello")

    def test_different_text_gives_a_different_fingerprint(self):
        assert transcript.text_hash("hello") != transcript.text_hash("hello ")

    def test_fingerprint_is_a_fixed_length_hex_string(self):
        digest = transcript.text_hash("anything at all")
        assert len(digest) == 32
        assert all(character in "0123456789abcdef" for character in digest)


class TestOperationalTurns:
    def test_all_requests_is_true(self):
        acts = {
            "m0": DialogueAct.OPERATIONAL_REQUEST,
            "m1": DialogueAct.OPERATIONAL_REQUEST,
        }
        assert transcript.all_turns_operational(acts) is True

    def test_one_expressive_turn_makes_it_false(self):
        acts = {
            "m0": DialogueAct.OPERATIONAL_REQUEST,
            "m1": DialogueAct.EXPRESSIVE,
        }
        assert transcript.all_turns_operational(acts) is False

    def test_knowing_nothing_is_not_the_same_as_knowing_they_were_requests(self):
        # An empty mapping means the classification never ran. Treating that
        # as "all requests" would discard sessions nobody looked at.
        assert transcript.all_turns_operational({}) is False


class TestExtractableText:
    def test_text_with_content_is_extractable(self):
        assert transcript.has_extractable_text("something") is True

    def test_whitespace_only_is_not(self):
        assert transcript.has_extractable_text("  \n\t ") is False

    def test_empty_string_is_not(self):
        assert transcript.has_extractable_text("") is False


class TestVoiceDetection:
    def test_voice_note_is_voice(self, make_event):
        event = make_event([("USER", "spoken")], source_modality=SourceModality.VOICE_NOTE)
        assert transcript.is_voice(event) is True

    def test_typed_entry_is_not_voice(self, make_event):
        event = make_event([("USER", "typed")], source_modality=SourceModality.TEXT_ENTRY)
        assert transcript.is_voice(event) is False


class TestMultiDateWarning:
    def test_single_date_buffer_says_nothing(self, make_event, captured_logs):
        transcript.warn_on_multi_date(make_event([("USER", "a"), ("USER", "b")]))
        assert captured_logs == []

    def test_multi_date_buffer_names_every_date_it_found(
        self, make_event, captured_logs
    ):
        event = make_event(
            [("USER", "a"), ("USER", "b")],
            message_dates=[date(2026, 6, 10), date(2026, 6, 11)],
        )
        transcript.warn_on_multi_date(event)

        assert len(captured_logs) == 1
        warning = captured_logs[0]
        assert warning["level"] == "WARNING"
        assert warning["message_event_dates"] == ["2026-06-10", "2026-06-11"]
        assert warning["session_event_date"] == "2026-06-11"

    def test_warning_does_not_stop_processing(self, make_event):
        event = make_event(
            [("USER", "a"), ("USER", "b")],
            message_dates=[date(2026, 6, 10), date(2026, 6, 11)],
        )
        assert transcript.warn_on_multi_date(event) is None
