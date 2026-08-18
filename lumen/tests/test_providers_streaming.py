"""
Sending a reply as it is written.

Streaming is not a nicety here. A considered reply takes several seconds to
produce, and the difference between watching it appear and waiting in silence
is the difference between a pause that reads as thought and one that reads as
lag.

The interesting behaviour is not the happy path — it is what happens when a
reply breaks after some of it is already on somebody's screen, which is the
one failure in this system that cannot be retried away.
"""

from __future__ import annotations

import pytest

from lumen.providers.errors import StreamInterrupted
from lumen.providers.fake import FakeLLMProvider
from lumen.providers.protocols import LLMProvider, StreamingLLMProvider
from lumen.providers.results import ChatMessage
from lumen.schemas.enums import ModelRole

HELLO = [ChatMessage(role="user", content="tell me something")]
REPLY = "That sounds like it cost you something to do at all"


def provider(script=None, **kwargs) -> FakeLLMProvider:
    """A scripted provider in the conversation role."""
    return FakeLLMProvider(
        script if script is not None else [REPLY],
        role=ModelRole.CONVERSATION,
        **kwargs,
    )


def said(chunks) -> str:
    """The reply as the person would have read it."""
    return "".join(chunk.text for chunk in chunks)


class TestTheInterface:
    def test_streaming_is_its_own_interface(self):
        """
        Kept separate so something that only needs a finished answer is not
        made to care about streaming. Only the live conversation reads a
        reply piece by piece.
        """
        assert isinstance(provider(), StreamingLLMProvider)
        assert isinstance(provider(), LLMProvider)

    def test_an_empty_conversation_is_refused_before_anything_is_sent(self):
        model = provider()

        with pytest.raises(ValueError):
            model.stream_text([])

        assert model.calls == []

    def test_nothing_is_sent_until_somebody_reads(self):
        """A turn that is prepared and then dropped should cost nothing."""
        model = provider()

        model.stream_text(HELLO)

        assert model.calls == []


class TestAReplyArriving:
    def test_the_pieces_join_up_into_the_whole_reply(self):
        chunks = list(provider().stream_text(HELLO))

        assert said(chunks) == REPLY

    def test_it_arrives_in_more_than_one_piece(self):
        chunks = [c for c in provider().stream_text(HELLO) if not c.final]

        assert len(chunks) > 1

    def test_the_last_piece_carries_the_totals_rather_than_words(self):
        chunks = list(provider().stream_text(HELLO))
        final = chunks[-1]

        assert final.final is True
        assert final.text == ""
        assert final.finish_reason == "STOP"
        assert final.usage.completion_tokens == len(REPLY.split(" "))

    def test_it_records_how_long_until_the_first_word(self):
        """
        The number streaming exists to shorten, and the only one that
        describes what the person actually experienced.
        """
        final = list(provider().stream_text(HELLO))[-1]

        assert final.first_chunk_ms is not None
        assert final.elapsed_ms is not None
        assert final.elapsed_ms >= final.first_chunk_ms

    def test_the_system_instruction_is_passed_along(self):
        model = provider()

        list(model.stream_text(HELLO, system_instruction="be warm"))

        assert model.calls[0].system_instruction == "be warm"

    def test_it_is_recorded_as_a_streaming_call(self):
        model = provider()

        list(model.stream_text(HELLO))

        assert model.calls[0].operation == "stream_text"


class TestWhenAReplyBreaksHalfway:
    """
    The one failure in Lumen that cannot be retried.

    Everywhere else a failed call is quietly tried again, which works because
    nobody has seen the failed attempt. Here the words are already on the
    screen, and a second reply starting underneath the first would be worse
    than stopping.
    """

    def test_it_raises_rather_than_ending_quietly(self):
        model = provider(break_after=2)

        with pytest.raises(StreamInterrupted):
            list(model.stream_text(HELLO))

    def test_what_had_already_been_said_is_carried_on_the_error(self):
        model = provider(break_after=2)

        with pytest.raises(StreamInterrupted) as caught:
            list(model.stream_text(HELLO))

        assert caught.value.said
        assert REPLY.startswith(caught.value.said.rstrip())

    def test_the_reader_saw_exactly_what_the_error_reports(self):
        # The two must agree, or the stored conversation and the screen
        # disagree about what was said.
        model = provider(break_after=2)
        seen = []

        with pytest.raises(StreamInterrupted) as caught:
            for chunk in model.stream_text(HELLO):
                seen.append(chunk.text)

        assert "".join(seen) == caught.value.said

    def test_it_is_not_tried_again(self):
        model = provider([REPLY, REPLY], break_after=2)

        with pytest.raises(StreamInterrupted):
            list(model.stream_text(HELLO))

        assert len(model.calls) == 1


class TestAProviderThatCannotStream:
    def test_it_says_so_plainly(self):
        """
        Not every provider has to stream, and one that cannot should be
        possible to write rather than impossible. Neither hook is abstract,
        so the base says what is missing instead of refusing to construct.
        """
        from lumen.providers.base import BaseLLMProvider

        class Mute(FakeLLMProvider):
            _request_stream = BaseLLMProvider._request_stream

        with pytest.raises(NotImplementedError, match="cannot send a reply"):
            list(Mute([REPLY]).stream_text(HELLO))

    def test_it_is_not_dressed_up_as_a_reply_that_broke(self):
        """
        A capability gap is a statement about what the provider is, not about
        this call. Reporting it as an interrupted reply would send somebody
        looking for a network problem.
        """
        from lumen.providers.base import BaseLLMProvider

        class Mute(FakeLLMProvider):
            _request_stream = BaseLLMProvider._request_stream

        with pytest.raises(NotImplementedError):
            list(Mute([REPLY]).stream_text(HELLO))


class TestBuildingTheVoiceProviders:
    """The factory's builders, which nothing else reaches."""

    def test_a_gemini_listener_can_be_built(self, monkeypatch, provider_config):
        from lumen.providers.factory import _build_gemini_transcription

        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")

        assert _build_gemini_transcription("m", provider_config).provider_name == "gemini"

    def test_a_gemini_voice_can_be_built(self, monkeypatch, provider_config):
        from lumen.providers.factory import _build_gemini_speech

        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")

        assert _build_gemini_speech("m", provider_config).provider_name == "gemini"


class TestTheStandInsRefusalPaths:
    def test_a_listener_given_no_audio_refuses(self):
        from lumen.providers.errors import ProviderResponseError
        from lumen.providers.fake import FakeTranscriptionProvider

        with pytest.raises(ProviderResponseError):
            FakeTranscriptionProvider(["anything"]).transcribe(b"", mime_type="audio/webm")

    def test_a_listener_runs_out_of_script_rather_than_inventing(self):
        from lumen.providers.errors import FakeScriptExhaustedError
        from lumen.providers.fake import FakeTranscriptionProvider

        listener = FakeTranscriptionProvider([])

        with pytest.raises(FakeScriptExhaustedError):
            listener.transcribe(b"audio", mime_type="audio/webm")

    def test_a_voice_given_nothing_to_say_refuses(self):
        from lumen.providers.errors import ProviderResponseError
        from lumen.providers.fake import FakeSpeechProvider

        with pytest.raises(ProviderResponseError):
            FakeSpeechProvider().synthesize("   ")

    def test_both_note_that_they_were_closed(self):
        from lumen.providers.fake import FakeSpeechProvider, FakeTranscriptionProvider

        listener, voice = FakeTranscriptionProvider(), FakeSpeechProvider()
        listener.close()
        voice.close()

        assert listener.closed and voice.closed


class TestAProviderThatOnlyHalfImplementsStreaming:
    def test_reading_a_piece_it_cannot_read_says_so(self):
        """
        Both hooks refuse together. A provider that could start a stream but
        not read it would fail somewhere much less obvious.
        """
        from lumen.providers.base import BaseLLMProvider

        class HalfWay(FakeLLMProvider):
            _read_chunk = BaseLLMProvider._read_chunk

        with pytest.raises(NotImplementedError):
            list(HalfWay([REPLY]).stream_text(HELLO))


class TestTheStandInsChunkReader:
    def test_a_piece_that_is_plain_text_is_read_as_text(self):
        """
        The scripted stream normally hands over ready-made pieces. Anything
        else is treated as the words themselves, which is what makes a
        hand-written stream usable in a test without extra ceremony.
        """
        model = provider()

        assert model._read_chunk("some words").text == "some words"


class TestRefusingARoleThatIsNotATextModel:
    def test_asking_for_embeddings_as_a_text_model_points_elsewhere(self):
        from lumen.config import AppConfig, ProviderConfig
        from lumen.providers.errors import ProviderConfigurationError
        from lumen.providers.factory import get_llm_provider

        config = AppConfig(providers=ProviderConfig(embedding_provider="fake"))

        with pytest.raises(ProviderConfigurationError, match="get_embedding_provider"):
            get_llm_provider(ModelRole.EMBEDDING, config)
