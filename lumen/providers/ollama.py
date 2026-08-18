"""
Ollama, behind the provider interfaces.

This is what makes running Lumen entirely on one machine a configuration
change. Every role can point here instead of at a cloud service, and nothing
above this file notices.

Like the Gemini module, the SDK is imported inside the functions that use it, so
this file can be imported and tested without the package installed.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from lumen.config import ProviderConfig
from lumen.providers.base import (
    BaseEmbeddingProvider,
    BaseLLMProvider,
    RawChunk,
    RawResponse,
    normalise_model_name,
    resolve_dimensions,
)
from lumen.providers.errors import (
    ProviderConfigurationError,
    ProviderError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from lumen.providers.results import ChatMessage, LLMUsage
from lumen.schemas.enums import EmbeddingTaskType, ModelRole

logger = logging.getLogger(__name__)


# Some embedding models expect to be told what the text is for by having a short
# prefix stuck on the front of it, because that is how they were trained.
# Ollama's own API has no field for this, so the prefix is added here.
#
# Keyed by model rather than by provider on purpose. This scheme belongs to the
# nomic models, not to Ollama. Putting these words in front of a model that
# never saw them during training would corrupt every vector it produced, so a
# model that is not listed gets nothing added.
_TASK_PREFIXES: dict[str, dict[EmbeddingTaskType, str]] = {
    "nomic-embed-text": {
        EmbeddingTaskType.DOCUMENT: "search_document: ",
        EmbeddingTaskType.QUERY: "search_query: ",
        EmbeddingTaskType.SIMILARITY: "clustering: ",
        EmbeddingTaskType.CLASSIFICATION: "classification: ",
    },
}


def _import_sdk() -> Any:
    """Load the Ollama SDK, explaining what to install if it is missing."""
    try:
        import ollama
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ProviderConfigurationError(
            "the ollama package is not installed; run `uv add ollama` or "
            "configure these roles to use a cloud provider instead",
            provider="ollama",
        ) from exc
    return ollama


def _map_error(
    exc: BaseException,
    *,
    model: str,
    role: ModelRole,
    host: str,
) -> ProviderError:
    """
    Turn an SDK exception into one of ours.

    Two cases get their own wording because they are what actually goes wrong
    with a local model, and a generic message would send someone hunting in the
    wrong place: the daemon is not running, or the model was never downloaded.
    """
    if isinstance(exc, ProviderError):
        return exc

    ollama = _import_sdk()
    shared = {"provider": "ollama", "model": model, "role": role}

    if isinstance(exc, ollama.ResponseError):
        status = getattr(exc, "status_code", None)
        message = str(exc)

        if status == 404:
            return ProviderConfigurationError(
                f"model {model!r} is not available on the Ollama server. "
                f"Run `ollama pull {model}` first.",
                cause=exc,
                **shared,
            )
        if status is not None and 500 <= status < 600:
            return ProviderUnavailableError(
                f"the Ollama server reported a problem: {message}",
                cause=exc,
                **shared,
            )
        return ProviderResponseError(f"request rejected: {message}", cause=exc, **shared)

    name = type(exc).__name__
    if "Timeout" in name:
        return ProviderTimeoutError(f"the call timed out: {exc}", cause=exc, **shared)
    if isinstance(exc, ConnectionError) or "Connect" in name or "RequestError" in name:
        return ProviderUnavailableError(
            f"could not reach Ollama at {host}. Is the daemon running? ({exc})",
            cause=exc,
            **shared,
        )

    return ProviderError(f"unexpected failure: {exc}", cause=exc, **shared)


class OllamaLLMProvider(BaseLLMProvider):
    """Text generation through a local Ollama server."""

    provider_name = "ollama"

    def __init__(
        self,
        model: str,
        role: ModelRole,
        config: ProviderConfig,
        client: Any | None = None,
    ) -> None:
        super().__init__(model, role, config)
        self._host = config.ollama_host

        if client is not None:
            self._client = client
            return

        ollama = _import_sdk()
        self._client = ollama.Client(
            host=config.ollama_host,
            timeout=config.resolve_timeout(role),
        )

    def _request_structured(
        self,
        *,
        prompt: str,
        response_model: type[BaseModel],
        system_instruction: str | None,
        temperature: float,
    ) -> Any:
        """
        Ask for JSON in the shape of a model class.

        Ollama takes the shape as a plain JSON schema, which the model class can
        produce itself — so again, no schema is written by hand.
        """
        messages = [ChatMessage(role="user", content=prompt)]
        return self._chat(
            messages=messages,
            system_instruction=system_instruction,
            temperature=temperature,
            response_format=response_model.model_json_schema(),
        )

    def _request_text(
        self,
        *,
        messages: list[ChatMessage],
        system_instruction: str | None,
        temperature: float,
    ) -> Any:
        """Send a conversation."""
        return self._chat(
            messages=messages,
            system_instruction=system_instruction,
            temperature=temperature,
            response_format=None,
        )

    def _chat(
        self,
        *,
        messages: list[ChatMessage],
        system_instruction: str | None,
        temperature: float,
        response_format: dict[str, Any] | None,
    ) -> Any:
        """
        Send one request, translating any SDK failure on the way out.

        Ollama has no separate field for a system instruction, so it goes in as
        the first message.
        """
        payload = []
        if system_instruction:
            payload.append({"role": "system", "content": system_instruction})
        payload.extend({"role": m.role, "content": m.content} for m in messages)

        try:
            return self._client.chat(
                model=self.model_name,
                messages=payload,
                format=response_format,
                options={"temperature": temperature},
            )
        except BaseException as exc:
            raise _map_error(
                exc, model=self.model_name, role=self.model_role, host=self._host
            ) from exc

    def _request_stream(
        self,
        *,
        messages: list[ChatMessage],
        system_instruction: str | None,
        temperature: float,
    ) -> Any:
        """Send a conversation and get the reply back as it is written."""
        payload = []
        if system_instruction:
            payload.append({"role": "system", "content": system_instruction})
        payload.extend({"role": m.role, "content": m.content} for m in messages)

        try:
            return self._client.chat(
                model=self.model_name,
                messages=payload,
                options={"temperature": temperature},
                stream=True,
            )
        except BaseException as exc:
            raise _map_error(
                exc, model=self.model_name, role=self.model_role, host=self._host
            ) from exc

    def _read_chunk(self, piece: Any) -> RawChunk:
        """
        Take the text out of one piece of the stream.

        The totals only arrive on the piece marked done, so they are read
        from that one and ignored everywhere else.
        """
        done = bool(_read(piece, "done"))
        return RawChunk(
            text=_message_content(piece) or "",
            usage=_usage(piece) if done else None,
            finish_reason=_read(piece, "done_reason") if done else None,
        )

    def _read_response(self, reply: Any) -> RawResponse:
        """
        Take the text and token counts out of a reply.

        There is no safety filtering to account for here — a local model answers
        or it does not.
        """
        text = _message_content(reply)
        if not text:
            raise ProviderResponseError(
                "the model returned no text",
                provider=self.provider_name,
                model=self.model_name,
                role=self.model_role,
            )

        return RawResponse(
            text=text,
            usage=_usage(reply),
            finish_reason=_read(reply, "done_reason"),
        )


class OllamaEmbeddingProvider(BaseEmbeddingProvider):
    """Vectors through a local Ollama server."""

    provider_name = "ollama"

    def __init__(
        self,
        model: str,
        config: ProviderConfig,
        client: Any | None = None,
        dimensions: int | None = None,
    ) -> None:
        super().__init__(
            model,
            config,
            dimensions
            if dimensions is not None
            else resolve_dimensions(model, config.embedding_dimensions),
        )
        self._host = config.ollama_host
        self._prefixes = _TASK_PREFIXES.get(normalise_model_name(model))
        self._warned_about_prefixes = False

        if client is not None:
            self._client = client
            return

        ollama = _import_sdk()
        self._client = ollama.Client(host=config.ollama_host, timeout=config.timeout_seconds)

    def _prepare_text(self, text: str, task_type: EmbeddingTaskType) -> str:
        """
        Put the task prefix on the front of the text, when this model uses one.

        A model we have no prefixes for is left alone, and warned about once.
        Guessing would be worse than doing nothing: the wrong prefix is training
        the model never saw, and it would spoil every vector rather than just
        making search a little weaker.
        """
        if self._prefixes is None:
            self._warn_once_about_missing_prefixes()
            return text
        return f"{self._prefixes[task_type]}{text}"

    def _warn_once_about_missing_prefixes(self) -> None:
        """Say something the first time, then stay quiet."""
        if self._warned_about_prefixes:
            return
        self._warned_about_prefixes = True
        logger.warning(
            "embedding model has no known task prefixes, so text is sent as-is; "
            "search quality may be lower than the model is capable of",
            extra={
                "provider": self.provider_name,
                "model": self.model_name,
                "known_models": sorted(_TASK_PREFIXES),
            },
        )

    def _embed_chunk(
        self,
        texts: list[str],
        task_type: EmbeddingTaskType,
    ) -> list[list[float]]:
        """Embed a batch. Ollama takes a list natively, so one call is enough."""
        try:
            reply = self._client.embed(model=self.model_name, input=texts)
        except BaseException as exc:
            raise _map_error(
                exc, model=self.model_name, role=ModelRole.EMBEDDING, host=self._host
            ) from exc

        vectors = [list(vector) for vector in (_read(reply, "embeddings") or [])]
        if len(vectors) != len(texts):
            raise ProviderResponseError(
                f"asked for {len(texts)} vectors but received {len(vectors)}",
                provider=self.provider_name,
                model=self.model_name,
                role=ModelRole.EMBEDDING,
            )
        return vectors


def _read(reply: Any, key: str) -> Any:
    """
    Read a field from a reply.

    The SDK returns objects, but tests and older versions hand back plain
    dictionaries, so both are supported.
    """
    if isinstance(reply, dict):
        return reply.get(key)
    return getattr(reply, key, None)


def _message_content(reply: Any) -> str:
    """The text the model said."""
    message = _read(reply, "message")
    if message is None:
        return ""
    if isinstance(message, dict):
        return message.get("content") or ""
    return getattr(message, "content", None) or ""


def _usage(reply: Any) -> LLMUsage:
    """
    Read the token counts.

    Ollama reports how many tokens it read and how many it produced, which map
    onto prompt and completion. The total is worked out from them.
    """
    prompt_tokens = _read(reply, "prompt_eval_count")
    completion_tokens = _read(reply, "eval_count")
    total = None
    if prompt_tokens is not None or completion_tokens is not None:
        total = (prompt_tokens or 0) + (completion_tokens or 0)
    return LLMUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total,
    )


__all__ = ["OllamaLLMProvider", "OllamaEmbeddingProvider"]
