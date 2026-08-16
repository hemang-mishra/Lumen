"""
The parts of a model call that are the same whatever the vendor.

Every provider does the same dance: send a request, retry it if it failed for a
recoverable reason, time it, pull the text and token counts out of whatever came
back, and write one log line. Only the sending and the pulling-apart differ
between vendors.

So that shared sequence lives here once, and a vendor supplies just the two
pieces that are genuinely its own. Adding a third vendor means writing those
two pieces, not repeating the sequence a third time and getting one of the steps
subtly wrong.
"""

from __future__ import annotations

import contextvars
import json
import logging
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel

from lumen.config import ProviderConfig
from lumen.providers.errors import ProviderConfigurationError, ProviderError
from lumen.providers.results import ChatMessage, LLMResult, LLMUsage, StructuredResult
from lumen.providers.retry import call_with_retry
from lumen.providers.telemetry import log_embedding_call, log_llm_call
from lumen.schemas.enums import EmbeddingTaskType, ModelRole

logger = logging.getLogger(__name__)


# Vector widths we know without having to ask. An embedding model missing from
# here is refused rather than guessed at — see resolve_dimensions.
KNOWN_EMBEDDING_DIMENSIONS: dict[str, int] = {
    "text-embedding-004": 768,
    "text-embedding-005": 768,
    "gemini-embedding-001": 3072,
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
    "snowflake-arctic-embed": 1024,
    "bge-m3": 1024,
}


def normalise_model_name(model: str) -> str:
    """
    Drop a version tag from a model name.

    Local models are usually named like "nomic-embed-text:v1.5", and everything
    we look up by name cares about the model, not the tag.
    """
    return model.split(":", 1)[0].strip()


def resolve_dimensions(model: str, expected: int | None = None) -> int:
    """
    How wide the vectors from this model are.

    Refuses to guess. It would be easy to fall back to whatever width the
    vector store was configured for, but that quietly defeats the check that
    compares the two — they would agree by construction, and a genuinely
    mismatched model would sail through and only fail much later, when a write
    is rejected far away from the setting that caused it.

    So an unknown model stops the process here, where the message can name the
    model and say what to do about it. Somebody who knows the width can state it
    and carry on; what is refused is guessing, not proceeding.
    """
    known = KNOWN_EMBEDDING_DIMENSIONS.get(normalise_model_name(model))
    if known is not None:
        return known

    if expected is not None:
        return expected

    raise ProviderConfigurationError(
        f"the vector width of embedding model {model!r} is not known. Either add "
        f"it to KNOWN_EMBEDDING_DIMENSIONS, or set LUMEN_EMBEDDING_DIMENSIONS to "
        f"its width. It is not assumed, because assuming it would defeat the "
        f"check that the model and the vector store agree.",
        model=model,
        role=ModelRole.EMBEDDING,
    )


@dataclass
class RawResponse:
    """
    The useful parts of a vendor's reply, once the vendor-specific wrapping has
    been taken off.
    """

    text: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    finish_reason: str | None = None


class BaseLLMProvider(ABC):
    """
    Shared behaviour for anything that talks to a text model.

    Subclasses send the request and unpack the reply. Everything around
    that — retrying, timing, parsing JSON, logging — happens here.
    """

    provider_name: ClassVar[str] = "base"

    def __init__(self, model: str, role: ModelRole, config: ProviderConfig) -> None:
        self.model_name = model
        self.model_role = role
        self._config = config

    def _rate_limit_backoff_max(self) -> float:
        """
        The longest this provider is willing to wait out a rate limit.

        Overridable because the right answer depends on what a retry can do
        differently. A provider with one credential can only wait for the
        quota minute to roll over, so it waits a long time. One that can send
        the retry under a different credential has somewhere fresh to go and
        should not sit out a minute first.
        """
        return self._config.rate_limit_backoff_max_seconds

    # ----- the interface callers use -----

    def generate_structured(
        self,
        prompt: str,
        response_model: type[BaseModel],
        *,
        system_instruction: str | None = None,
        temperature: float | None = None,
    ) -> StructuredResult:
        """
        Ask for JSON shaped like the given model class.

        The result carries the parsed object when the text was readable, and
        the raw text either way. Unreadable JSON is reported, not raised: the
        layer that knows what the data should mean is better placed to decide
        whether to ask again, and it needs the original text to say what went
        wrong.
        """

        def send() -> Any:
            return self._request_structured(
                prompt=prompt,
                response_model=response_model,
                system_instruction=system_instruction,
                temperature=self._temperature(temperature),
            )

        raw, outcome = self._send("generate_structured", send, prompt_text=prompt)
        data, parse_error = _parse_json(raw.text)

        if parse_error is not None:
            logger.warning(
                "model returned text that is not valid JSON",
                extra={
                    "provider": self.provider_name,
                    "model": self.model_name,
                    "model_role": self.model_role.value,
                    "parse_error": parse_error,
                },
            )

        return StructuredResult(
            text=raw.text,
            provider=self.provider_name,
            model=self.model_name,
            model_role=self.model_role,
            usage=raw.usage,
            latency_ms=outcome.latency_ms,
            elapsed_ms=outcome.elapsed_ms,
            attempts=outcome.attempts,
            finish_reason=raw.finish_reason,
            data=data,
            parse_error=parse_error,
        )

    def generate_text(
        self,
        messages: list[ChatMessage],
        *,
        system_instruction: str | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        """Hold a normal conversation and return what the model said."""
        if not messages:
            raise ValueError("messages must not be empty")

        def send() -> Any:
            return self._request_text(
                messages=messages,
                system_instruction=system_instruction,
                temperature=self._temperature(temperature),
            )

        joined = "\n".join(f"{m.role}: {m.content}" for m in messages)
        raw, outcome = self._send("generate_text", send, prompt_text=joined)

        return LLMResult(
            text=raw.text,
            provider=self.provider_name,
            model=self.model_name,
            model_role=self.model_role,
            usage=raw.usage,
            latency_ms=outcome.latency_ms,
            elapsed_ms=outcome.elapsed_ms,
            attempts=outcome.attempts,
            finish_reason=raw.finish_reason,
        )

    def close(self) -> None:
        """
        Release anything held open.

        A no-op by default so a provider with nothing to release does not have
        to say so.
        """

    # ----- what a subclass fills in -----

    @abstractmethod
    def _request_structured(
        self,
        *,
        prompt: str,
        response_model: type[BaseModel],
        system_instruction: str | None,
        temperature: float,
    ) -> Any:
        """Send a request asking for JSON and return the vendor's reply."""

    @abstractmethod
    def _request_text(
        self,
        *,
        messages: list[ChatMessage],
        system_instruction: str | None,
        temperature: float,
    ) -> Any:
        """Send a conversation and return the vendor's reply."""

    @abstractmethod
    def _read_response(self, reply: Any) -> RawResponse:
        """
        Pull the text, token counts and stop reason out of a vendor's reply.

        This is also where a reply that arrived but cannot be used is turned
        into the right error — content refused by a safety filter, or output cut
        short by a token limit.
        """

    # ----- shared plumbing -----

    def _send(
        self,
        operation: str,
        send: Any,
        *,
        prompt_text: str,
    ) -> tuple[RawResponse, Any]:
        """
        Run one call: retry it as needed, unpack it, and log exactly one line
        whether it worked or not.
        """
        started = time.perf_counter()
        raw: RawResponse | None = None
        failure: BaseException | None = None
        outcome = None

        try:
            outcome = call_with_retry(
                lambda: self._read_response(send()),
                provider=self.provider_name,
                model=self.model_name,
                role=self.model_role,
                max_attempts=self._config.max_attempts,
                base_delay=self._config.backoff_base_seconds,
                max_delay=self._config.backoff_max_seconds,
                rate_limit_max_delay=self._rate_limit_backoff_max(),
            )
            raw = outcome.value  # type: ignore[assignment]
            return raw, outcome
        except BaseException as exc:  # logged, then passed straight on
            failure = exc
            raise
        finally:
            log_llm_call(
                provider=self.provider_name,
                model=self.model_name,
                role=self.model_role,
                operation=operation,
                outcome="COMPLETE" if failure is None else "FAILED",
                latency_ms=outcome.latency_ms if outcome else _ms_since(started),
                elapsed_ms=outcome.elapsed_ms if outcome else _ms_since(started),
                attempts=_attempts_of(failure) if outcome is None else outcome.attempts,
                usage=raw.usage if raw else None,
                finish_reason=raw.finish_reason if raw else None,
                error_type=type(failure).__name__ if failure else None,
                error_detail=str(failure) if failure else None,
                prompt=prompt_text,
                completion=raw.text if raw else None,
                log_prompts=self._config.log_prompts,
            )

    def _temperature(self, override: float | None) -> float:
        """The temperature to use, preferring what the caller asked for."""
        return self._config.temperature if override is None else override


class BaseEmbeddingProvider(ABC):
    """
    Shared behaviour for anything that turns text into vectors.

    Subclasses embed one batch. Splitting the work up, keeping the results in
    order, optionally running batches side by side, and logging happen here.
    """

    provider_name: ClassVar[str] = "base"

    def __init__(self, model: str, config: ProviderConfig, dimensions: int) -> None:
        self.model_name = model
        self.dimensions = dimensions
        self._config = config

    def _rate_limit_backoff_max(self) -> float:
        """As on BaseLLMProvider: how long to wait out a rate limit."""
        return self._config.rate_limit_backoff_max_seconds

    # ----- the interface callers use -----

    def embed_text(
        self,
        text: str,
        *,
        task_type: EmbeddingTaskType = EmbeddingTaskType.DOCUMENT,
    ) -> list[float]:
        """Turn one piece of text into a vector."""
        return self.embed_batch([text], task_type=task_type)[0]

    def embed_batch(
        self,
        texts: list[str],
        *,
        task_type: EmbeddingTaskType = EmbeddingTaskType.DOCUMENT,
    ) -> list[list[float]]:
        """
        Turn several pieces of text into vectors, in the order they were given.

        If any part of the batch fails after its retries, the whole call fails.
        Handing back a shorter list would leave the caller lining vectors up
        against the wrong ids, and a vector attached to the wrong entry is worse
        than having to do the work again.
        """
        if not texts:
            return []

        started = time.perf_counter()
        failure: BaseException | None = None
        try:
            prepared = [self._prepare_text(text, task_type) for text in texts]
            chunks = _chunked(prepared, self._config.embed_batch_size)
            results = self._embed_chunks(chunks, task_type)

            vectors = [vector for chunk in results for vector in chunk]
            if len(vectors) != len(texts):
                raise ProviderError(
                    f"expected {len(texts)} vectors but got {len(vectors)}",
                    provider=self.provider_name,
                    model=self.model_name,
                    role=ModelRole.EMBEDDING,
                )
            return vectors
        except BaseException as exc:
            failure = exc
            raise
        finally:
            log_embedding_call(
                provider=self.provider_name,
                model=self.model_name,
                operation="embed_batch",
                outcome="COMPLETE" if failure is None else "FAILED",
                elapsed_ms=_ms_since(started),
                text_count=len(texts),
                task_type=task_type.value,
                error_type=type(failure).__name__ if failure else None,
                error_detail=str(failure) if failure else None,
            )

    def close(self) -> None:
        """Release anything held open."""

    # ----- what a subclass fills in -----

    @abstractmethod
    def _embed_chunk(
        self,
        texts: list[str],
        task_type: EmbeddingTaskType,
    ) -> list[list[float]]:
        """Embed one batch of already-prepared texts."""

    def _prepare_text(self, text: str, task_type: EmbeddingTaskType) -> str:
        """
        Adjust the text before it is sent.

        Left alone by default. Providers whose models expect the task to be
        stated as a prefix on the text override this.
        """
        return text

    # ----- shared plumbing -----

    def _embed_chunks(
        self,
        chunks: list[list[str]],
        task_type: EmbeddingTaskType,
    ) -> list[list[list[float]]]:
        """
        Embed every chunk, either one at a time or several at once.

        Concurrency is off unless it has been asked for. Firing several requests
        at a metered cloud API is the quickest way to trip its rate limit, and
        the whole batch then fails — slower and in order beats fast and refused.

        When threads are used, each one runs inside its own copy of the current
        context. Without that the trace id belonging to this run would not follow
        the work across, and every log line written by a worker would come out
        unattached to the entry that caused it.

        A copy each, rather than one copy shared between them, because a context
        can only be in use in one place at a time — sharing one would fail as
        soon as two workers actually overlapped.
        """
        workers = max(1, self._config.embed_max_workers)
        if workers == 1 or len(chunks) == 1:
            return [self._call_chunk(chunk, task_type) for chunk in chunks]

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(contextvars.copy_context().run, self._call_chunk, chunk, task_type)
                for chunk in chunks
            ]
            return [future.result() for future in futures]

    def _call_chunk(
        self,
        texts: list[str],
        task_type: EmbeddingTaskType,
    ) -> list[list[float]]:
        """Embed one chunk, retrying recoverable failures."""
        outcome = call_with_retry(
            lambda: self._embed_chunk(texts, task_type),
            provider=self.provider_name,
            model=self.model_name,
            role=ModelRole.EMBEDDING,
            max_attempts=self._config.max_attempts,
            base_delay=self._config.backoff_base_seconds,
            max_delay=self._config.backoff_max_seconds,
            rate_limit_max_delay=self._rate_limit_backoff_max(),
        )
        return outcome.value  # type: ignore[return-value]


def _parse_json(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """
    Read text as a JSON object.

    Returns the object, or a description of why it could not be read. A list or
    a bare number is treated as a failure too: callers asked for an object and
    need to know they did not get one.
    """
    stripped = text.strip()
    if not stripped:
        return None, "response was empty"

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"

    if not isinstance(parsed, dict):
        return None, f"expected a JSON object but got {type(parsed).__name__}"

    return parsed, None


def _chunked(items: list[str], size: int) -> list[list[str]]:
    """Split a list into pieces of at most the given size."""
    step = max(1, size)
    return [items[index : index + step] for index in range(0, len(items), step)]


def _ms_since(started: float) -> int:
    """Whole milliseconds since a perf_counter reading."""
    return int((time.perf_counter() - started) * 1000)


def _attempts_of(error: BaseException | None) -> int:
    """How many attempts a failure represents."""
    if isinstance(error, ProviderError):
        return error.attempts
    return 1


__all__ = [
    "KNOWN_EMBEDDING_DIMENSIONS",
    "RawResponse",
    "BaseLLMProvider",
    "BaseEmbeddingProvider",
    "normalise_model_name",
    "resolve_dimensions",
]
