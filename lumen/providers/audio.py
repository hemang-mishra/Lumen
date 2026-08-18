"""
Listening to somebody and speaking back.

Two jobs that share almost nothing except being about sound. One turns a
recording into words so it can be treated as an ordinary turn; the other
turns a finished reply into something to listen to.

Both are handed audio directly rather than a path to a file. A recording
arrives from a browser as bytes and is about to go straight to a model, so
writing it to disk in between would put somebody's voice on the filesystem
and gain nothing.

**Speech to text has no local option, and that is worth saying plainly.**
Every other job in Lumen can be pointed at a model running on your own
machine. The local runtime this project uses does not do speech at all, so a
deployment that has to keep everything local simply does not get voice. That
is a real limit, not an oversight.
"""

from __future__ import annotations

import logging
import struct
import time
from typing import Any

from lumen.config import ProviderConfig
from lumen.providers.errors import ProviderResponseError
from lumen.providers.results import Speech, Transcript
from lumen.providers.telemetry import log_llm_call
from lumen.schemas.enums import ModelRole

logger = logging.getLogger(__name__)

# What to ask for when listening. Deliberately bare: anything resembling
# instructions invites the model to summarise or tidy, and a transcript that
# has been improved is no longer what the person said.
TRANSCRIBE_INSTRUCTION = (
    "Write out exactly what is said in this recording, word for word. "
    "Do not summarise it, correct it, or add anything. "
    "If nothing is said, answer with nothing at all."
)

# Speech models hand back raw samples with no file header, so one has to be
# built before anything can play them. These are the shape Gemini returns.
PCM_SAMPLE_RATE = 24_000
PCM_CHANNELS = 1
PCM_BYTES_PER_SAMPLE = 2


class GeminiTranscriptionProvider:
    """
    Speech to text through Gemini.

    Chosen because its models take audio directly and the client is already
    a dependency. The recording is sent as one part alongside a short
    instruction, and what comes back is treated as the words themselves.
    """

    provider_name = "gemini"

    def __init__(
        self,
        model: str,
        config: ProviderConfig,
        client: Any | None = None,
    ) -> None:
        self.model_name = model
        self.model_role = ModelRole.TRANSCRIPTION
        self._config = config
        self._client = client
        self._types: Any | None = None

    def transcribe(self, audio: bytes, *, mime_type: str) -> Transcript:
        """
        Listen to a recording and return what was said.

        An empty answer is an error rather than an empty transcript. A
        recording that produced no words is almost always a broken upload or
        an unsupported format, and storing it as a turn nobody said would put
        a blank entry into somebody's history.
        """
        if not audio:
            raise ProviderResponseError(
                "there is no audio to listen to",
                provider=self.provider_name,
                model=self.model_name,
                role=self.model_role,
            )

        started = time.perf_counter()
        failure: BaseException | None = None
        text = ""
        try:
            reply = self._ask(audio, mime_type)
            text = (getattr(reply, "text", None) or "").strip()
            if not text:
                raise ProviderResponseError(
                    "nothing could be heard in the recording",
                    provider=self.provider_name,
                    model=self.model_name,
                    role=self.model_role,
                )
            return Transcript(
                text=text,
                provider=self.provider_name,
                model=self.model_name,
            )
        except BaseException as exc:
            failure = exc
            raise
        finally:
            log_llm_call(
                provider=self.provider_name,
                model=self.model_name,
                role=self.model_role,
                operation="transcribe",
                outcome="COMPLETE" if failure is None else "FAILED",
                latency_ms=_ms_since(started),
                elapsed_ms=_ms_since(started),
                attempts=1,
                error_type=type(failure).__name__ if failure else None,
                error_detail=str(failure) if failure else None,
                prompt=f"<{len(audio)} bytes of {mime_type}>",
                completion=text or None,
                log_prompts=self._config.log_prompts,
            )

    def close(self) -> None:
        """Nothing is held open between calls."""

    def _ask(self, audio: bytes, mime_type: str) -> Any:
        """Send the recording, translating any failure on the way out."""
        from lumen.providers.gemini import map_error

        client, types = self._connect()
        try:
            return client.models.generate_content(
                model=self.model_name,
                contents=[
                    types.Part.from_bytes(data=audio, mime_type=mime_type),
                    TRANSCRIBE_INSTRUCTION,
                ],
            )
        except BaseException as exc:
            raise map_error(
                exc, model=self.model_name, role=self.model_role
            ) from exc

    def _connect(self) -> tuple[Any, Any]:
        """The client and the SDK's type helpers, built once."""
        from lumen.providers.gemini import build_client, sdk_types

        if self._client is None:
            self._client = build_client(self._config, role=self.model_role)
        if self._types is None:
            self._types = sdk_types()
        return self._client, self._types


class GeminiSpeechProvider:
    """
    Text to speech through Gemini.

    The models here answer with raw sound samples and no file header, so one
    is built on the way out. That is exactly the kind of vendor detail that
    must not leak upwards — a caller should get something it can play, not
    something it has to know how to assemble.
    """

    provider_name = "gemini"

    def __init__(
        self,
        model: str,
        config: ProviderConfig,
        client: Any | None = None,
        voice: str = "Kore",
    ) -> None:
        self.model_name = model
        self.model_role = ModelRole.TTS
        self._config = config
        self._client = client
        self._types: Any | None = None
        self._voice = voice

    def synthesize(self, text: str) -> Speech:
        """Say the text out loud and return something that can be played."""
        spoken = text.strip()
        if not spoken:
            raise ProviderResponseError(
                "there is nothing to say",
                provider=self.provider_name,
                model=self.model_name,
                role=self.model_role,
            )

        started = time.perf_counter()
        failure: BaseException | None = None
        try:
            samples = _first_audio(self._ask(spoken))
            if not samples:
                raise ProviderResponseError(
                    "the model returned no audio",
                    provider=self.provider_name,
                    model=self.model_name,
                    role=self.model_role,
                )
            return Speech(
                audio=to_wav(samples),
                mime_type="audio/wav",
                provider=self.provider_name,
                model=self.model_name,
            )
        except BaseException as exc:
            failure = exc
            raise
        finally:
            log_llm_call(
                provider=self.provider_name,
                model=self.model_name,
                role=self.model_role,
                operation="synthesize",
                outcome="COMPLETE" if failure is None else "FAILED",
                latency_ms=_ms_since(started),
                elapsed_ms=_ms_since(started),
                attempts=1,
                error_type=type(failure).__name__ if failure else None,
                error_detail=str(failure) if failure else None,
                prompt=spoken,
                log_prompts=self._config.log_prompts,
            )

    def close(self) -> None:
        """Nothing is held open between calls."""

    def _ask(self, text: str) -> Any:
        """Send the text, translating any failure on the way out."""
        from lumen.providers.gemini import map_error

        client, types = self._connect()
        try:
            return client.models.generate_content(
                model=self.model_name,
                contents=text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=self._voice
                            )
                        )
                    ),
                ),
            )
        except BaseException as exc:
            raise map_error(
                exc, model=self.model_name, role=self.model_role
            ) from exc

    def _connect(self) -> tuple[Any, Any]:
        """The client and the SDK's type helpers, built once."""
        from lumen.providers.gemini import build_client, sdk_types

        if self._client is None:
            self._client = build_client(self._config, role=self.model_role)
        if self._types is None:
            self._types = sdk_types()
        return self._client, self._types


def to_wav(samples: bytes) -> bytes:
    """
    Wrap raw sound samples in the header that makes them a playable file.

    Written by hand rather than with a library because it is forty-four bytes
    of fixed structure, and the alternative is a dependency that exists to
    write forty-four bytes.
    """
    data_size = len(samples)
    byte_rate = PCM_SAMPLE_RATE * PCM_CHANNELS * PCM_BYTES_PER_SAMPLE
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        PCM_CHANNELS,
        PCM_SAMPLE_RATE,
        byte_rate,
        PCM_CHANNELS * PCM_BYTES_PER_SAMPLE,
        PCM_BYTES_PER_SAMPLE * 8,
        b"data",
        data_size,
    )
    return header + samples


def _first_audio(reply: Any) -> bytes:
    """Dig the sound out of whatever shape the reply arrived in."""
    for candidate in getattr(reply, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None)
            if data:
                return bytes(data)
    return b""


def _ms_since(started: float) -> int:
    """How long since a reading of the clock, in whole milliseconds."""
    return max(int((time.perf_counter() - started) * 1000), 0)


__all__ = [
    "GeminiTranscriptionProvider",
    "GeminiSpeechProvider",
    "to_wav",
    "TRANSCRIBE_INSTRUCTION",
]
