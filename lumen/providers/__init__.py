"""
Model providers.

Everything the rest of the application needs to talk to a language or embedding
model. Ask for a role, get something that satisfies one of the interfaces here,
and never find out which company answered.

    from lumen.providers import get_llm_provider
    from lumen.schemas.enums import ModelRole

    provider = get_llm_provider(ModelRole.LIGHTWEIGHT)
    result = provider.generate_structured(prompt, MyModel)

Vendor libraries are imported only inside the module for that vendor, and only
when that vendor is actually chosen.
"""

from lumen.providers.errors import (
    FakeScriptExhaustedError,
    ProviderConfigurationError,
    ProviderContentBlockedError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RetryableProviderError,
)
from lumen.providers.factory import (
    close_all_providers,
    get_embedding_provider,
    get_llm_provider,
    register_embedding_provider,
    register_llm_provider,
    reset_provider_cache,
    validate_providers,
)
from lumen.providers.fake import (
    FakeEmbeddingProvider,
    FakeLLMProvider,
    RecordedCall,
    fake_scripts,
)
from lumen.providers.protocols import (
    AudioTranscriptionProvider,
    EmbeddingProvider,
    LLMProvider,
    TTSProvider,
)
from lumen.providers.results import ChatMessage, LLMResult, LLMUsage, StructuredResult

__all__ = [
    # Choosing a provider
    "get_llm_provider",
    "get_embedding_provider",
    "validate_providers",
    "close_all_providers",
    "reset_provider_cache",
    "register_llm_provider",
    "register_embedding_provider",
    # Interfaces
    "LLMProvider",
    "EmbeddingProvider",
    "AudioTranscriptionProvider",
    "TTSProvider",
    # What comes back
    "ChatMessage",
    "LLMUsage",
    "LLMResult",
    "StructuredResult",
    # What can go wrong
    "ProviderError",
    "RetryableProviderError",
    "ProviderTimeoutError",
    "ProviderRateLimitError",
    "ProviderUnavailableError",
    "ProviderConfigurationError",
    "ProviderResponseError",
    "ProviderContentBlockedError",
    "FakeScriptExhaustedError",
    # Standing in for a real model
    "FakeLLMProvider",
    "FakeEmbeddingProvider",
    "RecordedCall",
    "fake_scripts",
]
