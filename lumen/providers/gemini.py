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
import threading
from typing import Any

from pydantic import BaseModel

from lumen.config import ProviderConfig
from lumen.providers.base import (
    BaseEmbeddingProvider,
    BaseLLMProvider,
    RawResponse,
    resolve_dimensions,
)
from lumen.providers.keyring import ApiKeyPool
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


def _response_schema(response_model: type[BaseModel]) -> dict[str, Any]:
    """
    Turn a model class into a schema the API will accept.

    Every contract in the pipeline forbids unexpected fields, which is what
    makes a malformed reply fail here rather than three stages downstream.
    Pydantic writes that as `additionalProperties: false`, the SDK passes it
    through as `additional_properties`, and Gemini rejects the whole request
    for naming a field it does not have — so every structured call fails, on
    every model, with a message about JSON rather than about the entry.

    Dropping the key costs nothing: it is a constraint on what the *model* may
    return, and the reply is validated against the real class on the way back
    in, where a stray field is still refused.
    """
    def without_the_rejected_key(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                key: without_the_rejected_key(value)
                for key, value in node.items()
                if key != "additionalProperties"
            }
        if isinstance(node, list):
            return [without_the_rejected_key(item) for item in node]
        return node

    return without_the_rejected_key(response_model.model_json_schema())


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


class _ClientSource:
    """
    Where a request gets its SDK client from.

    Both Gemini providers need the same thing: either the client a test handed
    them, or one of several clients built from the configured keys, chosen
    fresh for every request. That choice is here rather than in each provider,
    so the two cannot drift apart.

    One client is built per key and then kept. Building is cheap but not free,
    and a client holds a connection pool worth reusing; with ten keys this
    means ten long-lived clients rather than one per request.
    """

    def __init__(
        self,
        *,
        pool: ApiKeyPool | None,
        client: Any | None,
        genai: Any | None,
        model: str,
        role: ModelRole,
    ) -> None:
        self._pool = pool
        self._fixed = client
        self._genai = genai
        self._model = model
        self._role = role
        self._clients: dict[str, Any] = {}
        self._last_key: str | None = None
        # Embedding batches can run several at a time, so two threads can ask
        # for the same not-yet-built client at once.
        self._lock = threading.Lock()

    @property
    def size(self) -> int:
        """How many distinct credentials requests can be spread over."""
        return len(self._pool) if self._pool is not None else 1

    def acquire(self) -> Any:
        """
        A client to send the next request with.

        Whatever the last attempt used is passed as the key to avoid. On a
        retry that is the key that just failed, which is the case rotation
        exists for: a call that died on a rate limit goes back out under a
        different meter rather than pushing on the empty one. On an ordinary
        request it simply keeps two calls in a row off the same meter, which
        is the same thing one step earlier.
        """
        if self._pool is None:
            return self._fixed

        with self._lock:
            key = self._pool.select(exclude=self._last_key)
            rotated = self._last_key is not None and key != self._last_key
            self._last_key = key

            client = self._clients.get(key)
            if client is None:
                client = self._genai.Client(api_key=key)
                self._clients[key] = client

        if rotated:
            logger.info(
                "rotated to a different API key",
                extra={
                    "provider": "gemini",
                    "model": self._model,
                    "model_role": self._role.value,
                    "api_key_slot": self._pool.label_for(key),
                },
            )
        return client


def _client_source(
    config: ProviderConfig,
    *,
    model: str,
    role: ModelRole,
    client: Any | None,
) -> tuple[_ClientSource, Any]:
    """
    Work out how this provider will get clients, and load the SDK types.

    A client passed in wins outright — that is how tests exercise request
    shaping and reply unpacking with no key and no network. Otherwise every
    configured key becomes a rotation slot; a deployment with one key gets a
    pool of one and behaves exactly as it did before rotation existed.
    """
    genai, types, _ = _import_sdk()

    if client is not None:
        source = _ClientSource(pool=None, client=client, genai=None, model=model, role=role)
        return source, types

    keys = config.gemini_api_keys
    if not keys:
        raise ProviderConfigurationError(
            "no Gemini credential found; set GEMINI_API_KEY (or GOOGLE_API_KEY), "
            "or GEMINI_API_KEYS / GEMINI_API_KEY_1..N to rotate over several",
            provider="gemini",
            model=model,
            role=role,
        )

    try:
        pool = ApiKeyPool(keys, strategy=config.key_rotation_strategy)
    except ValueError as exc:
        raise ProviderConfigurationError(
            f"{exc}. Check LUMEN_KEY_ROTATION_STRATEGY.",
            provider="gemini",
            model=model,
            role=role,
            cause=exc,
        ) from exc

    logger.info(
        "gemini credentials loaded",
        extra={
            "provider": "gemini",
            "model": model,
            "model_role": role.value,
            "api_key_count": len(pool),
            "key_rotation_strategy": pool.strategy,
        },
    )
    source = _ClientSource(pool=pool, client=None, genai=genai, model=model, role=role)
    return source, types


class GeminiLLMProvider(BaseLLMProvider):
    """
    Text generation through Gemini.

    A client can be handed in instead of being built, which is what lets tests
    exercise the request shaping and reply unpacking without a network or a key.

    Where the deployment configured several keys, each request picks one of
    them — see _ClientSource.
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
        self._clients, self._types = _client_source(
            config, model=model, role=role, client=client
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

        The schema is derived from the class rather than written by hand, so
        it cannot drift away from the code it describes. One key is removed on
        the way past — see _response_schema.
        """
        config = self._types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_response_schema(response_model),
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

    def _rate_limit_backoff_max(self) -> float:
        """
        Wait out a rate limit only when there is nothing else to try.

        With one key a 429 means the quota minute has to pass, so the long
        ceiling stands. With several, the retry goes out under a different key
        and a minute of waiting would throw away the point of having them.
        """
        if self._clients.size > 1:
            return self._config.backoff_max_seconds
        return self._config.rate_limit_backoff_max_seconds

    def _call(self, *, contents: Any, config: Any) -> Any:
        """Send one request, translating any SDK failure on the way out."""
        try:
            return self._clients.acquire().models.generate_content(
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

        self._clients, self._types = _client_source(
            config, model=model, role=ModelRole.EMBEDDING, client=client
        )

    def _rate_limit_backoff_max(self) -> float:
        """As on the text provider: only wait out a limit with nowhere to go."""
        if self._clients.size > 1:
            return self._config.backoff_max_seconds
        return self._config.rate_limit_backoff_max_seconds

    def _embed_chunk(
        self,
        texts: list[str],
        task_type: EmbeddingTaskType,
    ) -> list[list[float]]:
        """Embed a batch, telling the API what the text will be used for."""
        try:
            reply = self._clients.acquire().models.embed_content(
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
