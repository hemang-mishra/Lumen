"""
What a provider hands back.

These are the models that cross the line between a provider and the rest of
the application. Keeping them as validated models rather than loose
dictionaries means a caller can rely on the shape of what it receives no
matter which vendor produced it.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from lumen.schemas.enums import ModelRole


class ChatMessage(BaseModel):
    """One turn in a conversation."""

    model_config = ConfigDict(frozen=True)

    role: Literal["user", "assistant", "system"]
    content: str


class LLMUsage(BaseModel):
    """
    How many tokens a call consumed.

    Every field is optional because not every provider reports them, and a
    missing count should read as "unknown" rather than as zero.
    """

    model_config = ConfigDict(frozen=True)

    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class LLMResult(BaseModel):
    """
    The outcome of a text generation call.

    Two timings are recorded because they answer different questions.
    latency_ms is how long the attempt that worked took, which is the model's
    real speed. elapsed_ms is the whole call including any waiting between
    retries, which is what the caller actually sat through. Reporting one
    number for both would make a call that retried twice look like a very slow
    model.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    provider: str
    model: str
    model_role: ModelRole
    usage: LLMUsage = LLMUsage()
    latency_ms: int = Field(ge=0)
    elapsed_ms: int = Field(ge=0)
    attempts: int = Field(default=1, ge=1)
    finish_reason: str | None = None


class TextChunk(BaseModel):
    """
    One piece of a reply as it is being written.

    Most chunks are just text. The last one carries the totals instead —
    token counts, why the model stopped, and two timings. It is marked
    `final` so a caller can tell the difference without guessing.

    The two timings answer different questions. first_chunk_ms is how long
    the person stared at nothing before words started appearing, which is
    what streaming exists to shorten. elapsed_ms is how long the whole reply
    took to finish.
    """

    model_config = ConfigDict(frozen=True)

    text: str = ""
    final: bool = False
    usage: LLMUsage = LLMUsage()
    finish_reason: str | None = None
    first_chunk_ms: int | None = Field(default=None, ge=0)
    elapsed_ms: int | None = Field(default=None, ge=0)


class Transcript(BaseModel):
    """
    What somebody said, once a recording has been listened to.

    The language and duration come along because the extraction pipeline has
    fields for both — an entry that was spoken in Hindi and written down in
    English should say so, rather than looking like it was typed in English.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    language: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0.0)
    provider: str = ""
    model: str = ""


class Speech(BaseModel):
    """
    A spoken reply, as bytes ready to be played.

    Carries its own format, because what comes back depends on the model and
    a player given the wrong type produces silence rather than an error.
    """

    model_config = ConfigDict(frozen=True)

    audio: bytes
    mime_type: str = "audio/wav"
    provider: str = ""
    model: str = ""


class StructuredResult(LLMResult):
    """
    The outcome of a call that asked for JSON.

    data holds the parsed object, or None when the text could not be parsed at
    all. That is the only judgement made here about the content, and it is
    purely mechanical: json parsing either worked or it did not. Whether the
    parsed object is *correct* is somebody else's question, which is why the
    original text is kept — a caller that wants to ask the model again needs to
    show it what it got wrong.
    """

    data: dict[str, Any] | None = None
    parse_error: str | None = None

    @property
    def parsed_ok(self) -> bool:
        """True when the response was readable as JSON."""
        return self.data is not None


__all__ = [
    "ChatMessage",
    "LLMUsage",
    "LLMResult",
    "StructuredResult",
    "TextChunk",
    "Transcript",
    "Speech",
]
