"""
Google Gemini, behind the provider interfaces.

This file and the Ollama one are the only places that know a vendor SDK exists.
Everything else works through the interfaces, which is what makes swapping
vendors a configuration change.

The SDK is imported inside the functions that need it rather than at the top of
the file. That way this module can be imported, and its logic tested, on a
machine where the package is not installed — which matters because a deployment
running everything locally has no reason to install a cloud SDK.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from lumen.config import ProviderConfig
from lumen.providers.base import (
    BaseEmbeddingProvider,
    BaseLLMProvider,
    RawResponse,
    resolve_dimensions,
)
from lumen.providers.errors import (
    ProviderConfigurationError,
    ProviderContentBlockedError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from lumen.providers.results import ChatMessage, LLMUsage
from lumen.schemas.enums import EmbeddingTaskType, ModelRole

logger = logging.getLogger(__name__)


# Gemini's own names for the task a piece of text is being embedded for.
_TASK_TYPES: dict[EmbeddingTaskType, str] = {
    EmbeddingTaskType.DOCUMENT: "RETRIEVAL_DOCUMENT",
    EmbeddingTaskType.QUERY: "RETRIEVAL_QUERY",
    EmbeddingTaskType.SIMILARITY: "SEMANTIC_SIMILARITY",
    EmbeddingTaskType.CLASSIFICATION: "CLASSIFICATION",
}

# Stop reasons that mean the answer cannot be used.
_BLOCKED_FINISH_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"}
_TRUNCATED_FINISH_REASONS = {"MAX_TOKENS"}


def _import_sdk() -> tuple[Any, Any, Any]:
    """Load the Google SDK, explaining what to install if it is missing."""
    try:
        from google import genai
        from google.genai import errors as genai_errors
        from google.genai import types as genai_types
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ProviderConfigurationError(
            "the google-genai package is not installed; run `uv add google-genai` "
            "or configure these roles to use a local provider instead",
            provider="gemini",
        ) from exc
    return genai, genai_types, genai_errors


def _permissive_safety_settings(genai_types: Any) -> list[Any]:
    """
    Turn the safety filters down as far as the API allows.

    Journal entries talk about self-harm, violence and conflict, and a filter
    cannot tell describing a feeling apart from encouraging an act. Those
    entries are the ones most worth keeping, so refusing them would quietly
    lose the material this whole system exists to understand.

    Anything the filters still refuse is reported as its own kind of error, so
    it can go to a person rather than being retried into a loop.
    """
    categories = [
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    ]
    return [
        genai_types.SafetySetting(category=category, threshold="BLOCK_NONE")
        for category in categories
    ]


def _map_error(exc: BaseException, *, model: str, role: ModelRole) -> ProviderError:
    """
    Turn an SDK exception into one of ours.

    The decision that matters is whether trying again could help. That is read
    from the type and the status code, never from the wording of a message,
    because wording changes between library versions.
    """
    if isinstance(exc, ProviderError):
        return exc

    _, _, genai_errors = _import_sdk()
    shared = {"provider": "gemini", "model": model, "role": role}

    if isinstance(exc, genai_errors.APIError):
        code = getattr(exc, "code", None)
        message = getattr(exc, "message", None) or str(exc)

        if code == 429:
            return ProviderRateLimitError(
                f"rate limit reached: {message}",
                retry_after_seconds=_retry_after(exc),
                cause=exc,
                **shared,
            )
        if code == 401 or code == 403:
            return ProviderConfigurationError(
                f"credentials rejected: {message}. Check GEMINI_API_KEY.",
                cause=exc,
                **shared,
            )
        if code == 404:
            return ProviderConfigurationError(
                f"model {model!r} was not found: {message}",
                cause=exc,
                **shared,
            )
        if code is not None and 500 <= code < 600:
            return ProviderUnavailableError(
                f"the service is having trouble: {message}",
                cause=exc,
                **shared,
            )
        return ProviderResponseError(f"request rejected: {message}", cause=exc, **shared)

    name = type(exc).__name__
    if "Timeout" in name:
        return ProviderTimeoutError(f"the call timed out: {exc}", cause=exc, **shared)
    if "Connect" in name or "Network" in name:
        return ProviderUnavailableError(f"could not reach the service: {exc}", cause=exc, **shared)

    return ProviderError(f"unexpected failure: {exc}", cause=exc, **shared)


def _retry_after(exc: BaseException) -> float | None:
    """The wait the server asked for, if it said."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


class GeminiLLMProvider(BaseLLMProvider):
    """
    Text generation through Gemini.

    A client can be handed in instead of being built, which is what lets tests
    exercise the request shaping and reply unpacking without a network or a key.
    """

    provider_name = "gemini"

    def __init__(
        self,
        model: str,
        role: ModelRole,
        config: ProviderConfig,
        client: Any | None = None,
    ) -> None:
        super().__init__(model, role, config)

        if client is not None:
            self._client = client
            _, self._types, _ = _import_sdk()
            return

        if not config.gemini_api_key:
            raise ProviderConfigurationError(
                "no Gemini credential found; set GEMINI_API_KEY (or GOOGLE_API_KEY)",
                provider="gemini",
                model=model,
                role=role,
            )

        genai, self._types, _ = _import_sdk()
        self._client = genai.Client(api_key=config.gemini_api_key)

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

        The class is handed to the SDK directly, which builds the schema from
        it. Nobody writes a schema by hand, so it cannot drift away from the
        code it is supposed to describe.
        """
        config = self._types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=response_model,
            temperature=temperature,
            system_instruction=system_instruction,
            safety_settings=_permissive_safety_settings(self._types),
            http_options=self._http_options(),
        )
        return self._call(contents=prompt, config=config)

    def _request_text(
        self,
        *,
        messages: list[ChatMessage],
        system_instruction: str | None,
        temperature: float,
    ) -> Any:
        """Send a conversation."""
        config = self._types.GenerateContentConfig(
            temperature=temperature,
            system_instruction=system_instruction,
            safety_settings=_permissive_safety_settings(self._types),
            http_options=self._http_options(),
        )
        return self._call(contents=self._to_contents(messages), config=config)

    def _call(self, *, contents: Any, config: Any) -> Any:
        """Send one request, translating any SDK failure on the way out."""
        try:
            return self._client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config,
            )
        except BaseException as exc:
            raise _map_error(exc, model=self.model_name, role=self.model_role) from exc

    def _read_response(self, reply: Any) -> RawResponse:
        """Take the text, token counts and stop reason out of a reply."""
        self._raise_if_blocked(reply)

        candidates = getattr(reply, "candidates", None) or []
        finish_reason = _finish_reason(candidates[0]) if candidates else None

        if finish_reason in _BLOCKED_FINISH_REASONS:
            raise ProviderContentBlockedError(
                f"the response was refused by a safety filter ({finish_reason})",
                blocked_categories=_blocked_categories(candidates[0]),
                provider=self.provider_name,
                model=self.model_name,
                role=self.model_role,
            )

        text = getattr(reply, "text", None) or ""

        if finish_reason in _TRUNCATED_FINISH_REASONS:
            raise ProviderResponseError(
                "the response was cut short by the token limit, so it is incomplete",
                provider=self.provider_name,
                model=self.model_name,
                role=self.model_role,
            )

        if not text:
            raise ProviderResponseError(
                "the model returned no text",
                provider=self.provider_name,
                model=self.model_name,
                role=self.model_role,
            )

        return RawResponse(text=text, usage=_usage(reply), finish_reason=finish_reason)

    def _raise_if_blocked(self, reply: Any) -> None:
        """Refuse a reply that was stopped before the model even answered."""
        feedback = getattr(reply, "prompt_feedback", None)
        reason = getattr(feedback, "block_reason", None)
        if reason is None:
            return
        raise ProviderContentBlockedError(
            f"the prompt was refused by a safety filter ({_name_of(reason)})",
            blocked_categories=(_name_of(reason),),
            provider=self.provider_name,
            model=self.model_name,
            role=self.model_role,
        )

    def _to_contents(self, messages: list[ChatMessage]) -> list[Any]:
        """
        Convert our messages into the SDK's shape.

        Gemini calls the assistant "model", and has no message role for system
        instructions — those are set on the request instead, so any system
        message here is folded in as user text rather than dropped.
        """
        contents = []
        for message in messages:
            role = "model" if message.role == "assistant" else "user"
            contents.append(
                self._types.Content(
                    role=role,
                    parts=[self._types.Part.from_text(text=message.content)],
                )
            )
        return contents

    def _http_options(self) -> Any:
        """Per-request settings, currently just how long to wait."""
        timeout_ms = int(self._config.resolve_timeout(self.model_role) * 1000)
        return self._types.HttpOptions(timeout=timeout_ms)


class GeminiEmbeddingProvider(BaseEmbeddingProvider):
    """Vectors through Gemini."""

    provider_name = "gemini"

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

        if client is not None:
            self._client = client
            _, self._types, _ = _import_sdk()
            return

        if not config.gemini_api_key:
            raise ProviderConfigurationError(
                "no Gemini credential found; set GEMINI_API_KEY (or GOOGLE_API_KEY)",
                provider="gemini",
                model=model,
                role=ModelRole.EMBEDDING,
            )

        genai, self._types, _ = _import_sdk()
        self._client = genai.Client(api_key=config.gemini_api_key)

    def _embed_chunk(
        self,
        texts: list[str],
        task_type: EmbeddingTaskType,
    ) -> list[list[float]]:
        """Embed a batch, telling the API what the text will be used for."""
        try:
            reply = self._client.models.embed_content(
                model=self.model_name,
                contents=texts,
                config=self._types.EmbedContentConfig(task_type=_TASK_TYPES[task_type]),
            )
        except BaseException as exc:
            raise _map_error(exc, model=self.model_name, role=ModelRole.EMBEDDING) from exc

        embeddings = getattr(reply, "embeddings", None) or []
        vectors = [list(getattr(item, "values", None) or []) for item in embeddings]

        if len(vectors) != len(texts):
            raise ProviderResponseError(
                f"asked for {len(texts)} vectors but received {len(vectors)}",
                provider=self.provider_name,
                model=self.model_name,
                role=ModelRole.EMBEDDING,
            )
        return vectors


def _usage(reply: Any) -> LLMUsage:
    """Read the token counts, if the reply carried any."""
    metadata = getattr(reply, "usage_metadata", None)
    if metadata is None:
        return LLMUsage()
    return LLMUsage(
        prompt_tokens=getattr(metadata, "prompt_token_count", None),
        completion_tokens=getattr(metadata, "candidates_token_count", None),
        total_tokens=getattr(metadata, "total_token_count", None),
    )


def _finish_reason(candidate: Any) -> str | None:
    """Why the model stopped, as a plain string."""
    return _name_of(getattr(candidate, "finish_reason", None))


def _blocked_categories(candidate: Any) -> tuple[str, ...]:
    """Which safety categories were triggered."""
    ratings = getattr(candidate, "safety_ratings", None) or []
    return tuple(
        _name_of(rating.category)
        for rating in ratings
        if getattr(rating, "blocked", False) and getattr(rating, "category", None)
    )


def _name_of(value: Any) -> str | None:
    """
    The readable name of an SDK enum value.

    The SDK sometimes hands back an enum and sometimes a plain string, so both
    are flattened to the same thing.
    """
    if value is None:
        return None
    return getattr(value, "name", None) or str(value)


__all__ = ["GeminiLLMProvider", "GeminiEmbeddingProvider"]
