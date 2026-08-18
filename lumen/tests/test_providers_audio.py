"""
Listening to somebody, and speaking back.

Both take and return the audio itself rather than a path to it. A recording
arrives from a browser as bytes and goes straight to a model, so a file on
the way through would put somebody's voice on the filesystem for no reason.

The client is handed in, so nothing here needs a credential or a network.
"""

from __future__ import annotations

import struct
from types import SimpleNamespace

import pytest

from lumen.providers.audio import (
    GeminiSpeechProvider,
    GeminiTranscriptionProvider,
    to_wav,
)
from lumen.providers.errors import ProviderError, ProviderResponseError
from lumen.schemas.enums import ModelRole

SAID = "I finally went for that walk on my own today"


class FakeModels:
    """Stands in for client.models, remembering how it was called."""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def a_client(result=None, error=None):
    models = FakeModels(result=result, error=error)
    return SimpleNamespace(models=models), models


def a_transcript(text: str = SAID):
    return SimpleNamespace(text=text)


def some_audio(samples: bytes = b"\x01\x02\x03\x04"):
    """Something shaped like a reply carrying sound."""
    part = SimpleNamespace(inline_data=SimpleNamespace(data=samples))
    return SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))]
    )


class TestListening:
    def test_a_recording_comes_back_as_words(self, provider_config):
        client, _ = a_client(result=a_transcript())
        listener = GeminiTranscriptionProvider("m", provider_config, client=client)

        assert listener.transcribe(b"audio", mime_type="audio/webm").text == SAID

    def test_the_recording_is_sent_with_its_format(self, provider_config):
        client, models = a_client(result=a_transcript())
        listener = GeminiTranscriptionProvider("m", provider_config, client=client)

        listener.transcribe(b"audio", mime_type="audio/ogg")

        assert models.calls[0]["model"] == "m"
        assert models.calls[0]["contents"]

    def test_it_knows_what_job_it_is_doing(self, provider_config):
        listener = GeminiTranscriptionProvider("m", provider_config, client=object())

        assert listener.model_role is ModelRole.TRANSCRIPTION

    def test_no_audio_at_all_is_refused_before_anything_is_sent(self, provider_config):
        client, models = a_client(result=a_transcript())
        listener = GeminiTranscriptionProvider("m", provider_config, client=client)

        with pytest.raises(ProviderResponseError):
            listener.transcribe(b"", mime_type="audio/webm")

        assert models.calls == []

    def test_a_recording_nothing_could_be_heard_in_is_an_error(self, provider_config):
        """
        Almost always a broken upload or an unsupported format. Storing it as
        a turn nobody said would put a blank entry into somebody's history.
        """
        client, _ = a_client(result=a_transcript("   "))
        listener = GeminiTranscriptionProvider("m", provider_config, client=client)

        with pytest.raises(ProviderResponseError):
            listener.transcribe(b"audio", mime_type="audio/webm")

    def test_a_vendor_failure_becomes_one_of_ours(self, provider_config):
        client, _ = a_client(error=RuntimeError("the service is down"))
        listener = GeminiTranscriptionProvider("m", provider_config, client=client)

        with pytest.raises(ProviderError):
            listener.transcribe(b"audio", mime_type="audio/webm")

    def test_closing_it_is_harmless(self, provider_config):
        GeminiTranscriptionProvider("m", provider_config, client=object()).close()


class TestSpeaking:
    def test_text_comes_back_as_something_playable(self, provider_config):
        client, _ = a_client(result=some_audio())
        voice = GeminiSpeechProvider("m", provider_config, client=client)

        spoken = voice.synthesize("hello")

        assert spoken.mime_type == "audio/wav"
        assert spoken.audio.startswith(b"RIFF")

    def test_it_knows_what_job_it_is_doing(self, provider_config):
        voice = GeminiSpeechProvider("m", provider_config, client=object())

        assert voice.model_role is ModelRole.TTS

    def test_nothing_to_say_is_refused_before_anything_is_sent(self, provider_config):
        client, models = a_client(result=some_audio())
        voice = GeminiSpeechProvider("m", provider_config, client=client)

        with pytest.raises(ProviderResponseError):
            voice.synthesize("   ")

        assert models.calls == []

    def test_a_reply_with_no_sound_in_it_is_an_error(self, provider_config):
        client, _ = a_client(result=SimpleNamespace(candidates=[]))
        voice = GeminiSpeechProvider("m", provider_config, client=client)

        with pytest.raises(ProviderResponseError):
            voice.synthesize("hello")

    def test_a_vendor_failure_becomes_one_of_ours(self, provider_config):
        client, _ = a_client(error=RuntimeError("the service is down"))
        voice = GeminiSpeechProvider("m", provider_config, client=client)

        with pytest.raises(ProviderError):
            voice.synthesize("hello")

    def test_closing_it_is_harmless(self, provider_config):
        GeminiSpeechProvider("m", provider_config, client=object()).close()


class TestMakingRawSoundPlayable:
    """
    Speech models hand back samples with no file header, so one is built on
    the way out. That is exactly the kind of vendor detail that must not
    reach a caller: they should get something they can play, not something
    they have to know how to assemble.
    """

    def test_it_is_a_wav_file(self):
        assert to_wav(b"\x00\x00").startswith(b"RIFF")
        assert b"WAVE" in to_wav(b"\x00\x00")

    def test_the_header_is_the_standard_length(self):
        assert len(to_wav(b"")) == 44

    def test_the_samples_are_kept_exactly(self):
        samples = b"\x01\x02\x03\x04"

        assert to_wav(samples).endswith(samples)

    def test_the_header_says_how_much_sound_follows(self):
        samples = b"\x00" * 100
        header = to_wav(samples)[:44]

        (data_size,) = struct.unpack("<I", header[40:44])
        assert data_size == 100


class TestBuildingTheClientWhenNoneWasHandedIn:
    """
    The path the service actually takes. A client passed in is how the tests
    above avoid a network; without one, a real client is built from whichever
    credential is configured.
    """

    def test_a_listener_builds_its_own_client(self, monkeypatch, provider_config):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
        listener = GeminiTranscriptionProvider("m", provider_config)

        client, types = listener._connect()

        assert client is not None
        assert types is not None

    def test_a_voice_builds_its_own_client(self, monkeypatch, provider_config):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
        voice = GeminiSpeechProvider("m", provider_config)

        client, types = voice._connect()

        assert client is not None
        assert types is not None

    def test_a_missing_credential_says_which_one(self, monkeypatch, provider_config):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEYS", raising=False)

        with pytest.raises(ProviderError, match="GEMINI_API_KEY"):
            GeminiSpeechProvider("m", provider_config)._connect()
