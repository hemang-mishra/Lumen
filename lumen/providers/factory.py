"""
Turning a role into a working provider.

The rest of the application asks for a kind of thinking — something fast,
something that reasons deeply, something that makes vectors — and this is what
hands back an object that can do it. Which company is behind that object is read
from configuration and known only here.

Two things are deliberately absent.

There is no database anywhere in this file. Which model backs a role is a
decision made by whoever deploys Lumen, set in the environment and fixed while
the process runs. It is not a preference the person writing journal entries
expresses, so there is no table it could be changed in and no way to change it
while running.

There is no list of vendors baked into the lookup functions either. Providers
register themselves by name, so supporting another one means adding an entry
rather than editing the code that does the choosing.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from lumen.config import AppConfig, ProviderConfig
from lumen.providers.errors import ProviderConfigurationError
from lumen.providers.protocols import EmbeddingProvider, LLMProvider
from lumen.schemas.enums import ModelRole

logger = logging.getLogger(__name__)


# How to build a provider once the name has been resolved.
LLMBuilder = Callable[[str, ModelRole, ProviderConfig], LLMProvider]
EmbeddingBuilder = Callable[[str, ProviderConfig], EmbeddingProvider]

# The roles that mean "a model that generates text". Asking for one of the
# others here is a mistake in the calling code, not a configuration problem.
_TEXT_ROLES = frozenset({ModelRole.LIGHTWEIGHT, ModelRole.THINKING})

# Roles that exist but have no implementation yet, and what will bring them.
_NOT_YET_IMPLEMENTED = {
    ModelRole.TRANSCRIPTION: "speech to text arrives with voice input",
    ModelRole.TTS: "text to speech arrives with voice output",
}

# What the stand-in embedding provider falls back to when nothing else says.
# Both real default models are this wide.
_COMMON_VECTOR_WIDTH = 768

_llm_builders: dict[str, LLMBuilder] = {}
_embedding_builders: dict[str, EmbeddingBuilder] = {}

# Built providers, kept so a new network client is not made for every call.
_cache: dict[tuple[str, str, str, str], Any] = {}
_cache_lock = threading.Lock()


def register_llm_provider(name: str, builder: LLMBuilder) -> None:
    """Make a text provider available under a name."""
    _llm_builders[name] = builder


def register_embedding_provider(name: str, builder: EmbeddingBuilder) -> None:
    """Make an embedding provider available under a name."""
    _embedding_builders[name] = builder


def get_llm_provider(role: ModelRole, config: AppConfig | None = None) -> LLMProvider:
    """
    The text provider configured for a role.

    The same object comes back each time, because the underlying network client
    holds connections that should not be rebuilt for every journal entry.
    """
    settings = config or AppConfig()
    _require_text_role(role)

    provider_name, model_name = settings.providers.resolve(role)
    builder = _llm_builders.get(provider_name)
    if builder is None:
        raise ProviderConfigurationError(
            f"{provider_name!r} is not a known provider for the {role.value} role. "
            f"Available: {sorted(_llm_builders)}",
            provider=provider_name,
            model=model_name,
            role=role,
        )

    return _cached(
        key=("llm", role.value, provider_name, model_name),
        build=lambda: builder(model_name, role, settings.providers),
    )


def get_embedding_provider(config: AppConfig | None = None) -> EmbeddingProvider:
    """
    The embedding provider, checked against the vector store it will feed.

    The width of the vectors a model produces has to match the width the store
    was set up for. Checking here means a mismatch stops the process while
    somebody is watching, instead of surfacing much later as a rejected write
    with no obvious cause.
    """
    settings = config or AppConfig()
    provider_name, model_name = settings.providers.resolve(ModelRole.EMBEDDING)

    builder = _embedding_builders.get(provider_name)
    if builder is None:
        raise ProviderConfigurationError(
            f"{provider_name!r} is not a known embedding provider. "
            f"Available: {sorted(_embedding_builders)}",
            provider=provider_name,
            model=model_name,
            role=ModelRole.EMBEDDING,
        )

    provider = _cached(
        key=("embedding", ModelRole.EMBEDDING.value, provider_name, model_name),
        build=lambda: builder(model_name, settings.providers),
    )

    expected = settings.vector.vector_size
    if provider.dimensions != expected:
        raise ProviderConfigurationError(
            f"model {model_name!r} produces vectors of {provider.dimensions} numbers "
            f"but the vector store expects {expected}. Either point LUMEN_EMBEDDING_MODEL "
            f"at a model of the right width, or set LUMEN_VECTOR_SIZE to "
            f"{provider.dimensions} and rebuild the collection.",
            provider=provider_name,
            model=model_name,
            role=ModelRole.EMBEDDING,
        )

    return provider


def validate_providers(config: AppConfig | None = None) -> None:
    """
    Build every configured provider now, so problems surface at startup.

    Providers are otherwise built the first time something needs one, which
    means a missing credential is not noticed until a pipeline run is already
    underway and has written state. Calling this while the process is starting
    turns that into a clear failure with nobody's work half done.

    Nothing is thrown away afterwards: the providers stay cached, so this
    doubles as warming them up.
    """
    settings = config or AppConfig()
    for role in sorted(_TEXT_ROLES, key=lambda item: item.value):
        get_llm_provider(role, settings)
    get_embedding_provider(settings)

    logger.info(
        "provider configuration checked",
        extra={
            "roles": [role.value for role in sorted(_TEXT_ROLES, key=lambda r: r.value)]
            + [ModelRole.EMBEDDING.value],
        },
    )


def close_all_providers() -> None:
    """
    Release every provider that has been built.

    Called when the process is shutting down. Each one may be holding network
    connections open, and the objects live for as long as the process does, so
    nothing else would ever close them.
    """
    with _cache_lock:
        providers = list(_cache.values())
        _cache.clear()

    for provider in providers:
        try:
            provider.close()
        except Exception:  # a failure to tidy up should not stop shutdown
            logger.warning(
                "a provider raised while closing",
                exc_info=True,
                extra={"provider": getattr(provider, "provider_name", "unknown")},
            )


def reset_provider_cache() -> None:
    """
    Forget the built providers without closing them.

    Only used by tests, which need each case to start from nothing. Running
    code has no reason for this: the configuration cannot change while the
    process is alive.
    """
    with _cache_lock:
        _cache.clear()


def _cached(*, key: tuple[str, str, str, str], build: Callable[[], Any]) -> Any:
    """Return the provider for this key, building it the first time."""
    with _cache_lock:
        existing = _cache.get(key)
        if existing is not None:
            return existing

    # Built outside the lock, since constructing a client can be slow and there
    # is no harm in two threads briefly building the same thing.
    provider = build()

    with _cache_lock:
        return _cache.setdefault(key, provider)


def _require_text_role(role: ModelRole) -> None:
    """Refuse roles that do not name a text-generating model."""
    if role in _TEXT_ROLES:
        return

    if role in _NOT_YET_IMPLEMENTED:
        raise ProviderConfigurationError(
            f"the {role.value} role has no implementation yet — "
            f"{_NOT_YET_IMPLEMENTED[role]}",
            role=role,
        )

    raise ProviderConfigurationError(
        f"the {role.value} role does not name a text model. Use "
        f"get_embedding_provider() for embeddings, or ask for "
        f"{sorted(item.value for item in _TEXT_ROLES)}.",
        role=role,
    )


def _build_gemini_llm(model: str, role: ModelRole, config: ProviderConfig) -> LLMProvider:
    """Build a Gemini text provider, importing the SDK only now."""
    from lumen.providers.gemini import GeminiLLMProvider

    return GeminiLLMProvider(model, role, config)


def _build_gemini_embedding(model: str, config: ProviderConfig) -> EmbeddingProvider:
    """Build a Gemini embedding provider, importing the SDK only now."""
    from lumen.providers.gemini import GeminiEmbeddingProvider

    return GeminiEmbeddingProvider(model, config)


def _build_ollama_llm(model: str, role: ModelRole, config: ProviderConfig) -> LLMProvider:
    """Build an Ollama text provider, importing the SDK only now."""
    from lumen.providers.ollama import OllamaLLMProvider

    return OllamaLLMProvider(model, role, config)


def _build_ollama_embedding(model: str, config: ProviderConfig) -> EmbeddingProvider:
    """Build an Ollama embedding provider, importing the SDK only now."""
    from lumen.providers.ollama import OllamaEmbeddingProvider

    return OllamaEmbeddingProvider(model, config)


def _build_fake_llm(model: str, role: ModelRole, config: ProviderConfig) -> LLMProvider:
    """
    Build a scripted text provider, picking up whatever script was left for
    this role.
    """
    from lumen.providers.fake import FakeLLMProvider, fake_scripts

    return FakeLLMProvider(
        fake_scripts.get(role),
        model=model,
        role=role,
        config=config,
    )


def _build_fake_embedding(model: str, config: ProviderConfig) -> EmbeddingProvider:
    """
    Build a repeatable stand-in embedding provider.

    A stand-in pretends to be whatever it is named after, so a name with a known
    width uses that width. Failing that it uses a stated one. Failing that it
    falls back to the most common width there is, because a stand-in should not
    be the thing that stops a test or an offline run from starting.
    """
    from lumen.providers.base import KNOWN_EMBEDDING_DIMENSIONS, normalise_model_name
    from lumen.providers.fake import FakeEmbeddingProvider

    dimensions = (
        KNOWN_EMBEDDING_DIMENSIONS.get(normalise_model_name(model))
        or config.embedding_dimensions
        or _COMMON_VECTOR_WIDTH
    )
    return FakeEmbeddingProvider(model=model, dimensions=dimensions, config=config)


# The vendors that ship with Lumen. The SDK for each is loaded only if that
# vendor is actually chosen, so a machine running everything locally never
# needs a cloud library installed.
register_llm_provider("gemini", _build_gemini_llm)
register_llm_provider("ollama", _build_ollama_llm)
register_llm_provider("fake", _build_fake_llm)

register_embedding_provider("gemini", _build_gemini_embedding)
register_embedding_provider("ollama", _build_ollama_embedding)
register_embedding_provider("fake", _build_fake_embedding)


__all__ = [
    "get_llm_provider",
    "get_embedding_provider",
    "validate_providers",
    "close_all_providers",
    "reset_provider_cache",
    "register_llm_provider",
    "register_embedding_provider",
]
