"""
The interfaces every model provider satisfies.

These are the contracts the rest of the application is written against. A
pipeline stage asks for the kind of thinking it needs and calls these methods;
it never learns which company answered, and swapping one out is a
configuration change rather than an edit.

They are kept as four separate interfaces rather than one large one, so
something that only turns text into vectors is not forced to pretend it can
hold a conversation.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from lumen.providers.results import ChatMessage, LLMResult, StructuredResult
from lumen.schemas.enums import EmbeddingTaskType, ModelRole


@runtime_checkable
class LLMProvider(Protocol):
    """
    Something that can generate text, and generate JSON matching a shape we
    ask for.
    """

    provider_name: str
    model_name: str
    model_role: ModelRole

    def generate_structured(
        self,
        prompt: str,
        response_model: type[BaseModel],
        *,
        system_instruction: str | None = None,
        temperature: float | None = None,
    ) -> StructuredResult:
        """
        Ask the model for JSON in the shape of the given model class.

        The shape is described by passing the class itself, so nobody has to
        hand-write a JSON schema and keep it in step with the code.

        What comes back is not validated against that class. Checking the
        answer, and asking again when it is wrong, is the job of the layer
        above; this one only reports whether the text could be parsed.
        """
        ...

    def generate_text(
        self,
        messages: list[ChatMessage],
        *,
        system_instruction: str | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        """Hold a normal conversation and return what the model said."""
        ...

    def close(self) -> None:
        """Release the network connection, if this provider holds one."""
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Something that turns text into vectors."""

    provider_name: str
    model_name: str
    dimensions: int

    def embed_text(
        self,
        text: str,
        *,
        task_type: EmbeddingTaskType = EmbeddingTaskType.DOCUMENT,
    ) -> list[float]:
        """Turn one piece of text into a vector."""
        ...

    def embed_batch(
        self,
        texts: list[str],
        *,
        task_type: EmbeddingTaskType = EmbeddingTaskType.DOCUMENT,
    ) -> list[list[float]]:
        """
        Turn several pieces of text into vectors in one go.

        The vectors come back in the same order as the texts went in. Callers
        line them up against their own list of ids, so that ordering is part of
        the promise, not an accident of how it happens to be implemented.
        """
        ...

    def close(self) -> None:
        """Release the network connection, if this provider holds one."""
        ...


class AudioTranscriptionProvider(Protocol):
    """
    Something that turns recorded speech into text.

    Declared now so the shape is agreed, but nothing implements it yet — the
    pipeline works on text, and there is no voice input to convert.
    """

    def transcribe(self, audio_file_path: str) -> str:
        """Read an audio file and return what was said."""
        ...


class TTSProvider(Protocol):
    """
    Something that turns text into spoken audio.

    Declared now, implemented later, for the same reason as transcription.
    """

    def synthesize(self, text: str, output_path: str) -> str:
        """Speak the text into an audio file and return where it was written."""
        ...


__all__ = [
    "LLMProvider",
    "EmbeddingProvider",
    "AudioTranscriptionProvider",
    "TTSProvider",
]
