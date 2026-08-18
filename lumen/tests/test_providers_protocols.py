"""
Tests that every provider really does satisfy the interfaces.

Worth checking explicitly, because the interfaces are structural: a provider
that quietly renamed a method would still import, still construct, and only fail
when something actually called it.
"""

from __future__ import annotations

import inspect

import pytest

from lumen.providers.errors import (
    ProviderConfigurationError,
    ProviderContentBlockedError,
)
from lumen.providers.factory import (
    _build_gemini_embedding,
    _build_gemini_llm,
    _build_ollama_embedding,
    _build_ollama_llm,
)
from lumen.providers.fake import FakeEmbeddingProvider, FakeLLMProvider
from lumen.providers.gemini import GeminiEmbeddingProvider, GeminiLLMProvider
from lumen.providers.gemini import _map_error as map_gemini_error
from lumen.providers.gemini import _name_of
from lumen.providers.ollama import OllamaEmbeddingProvider, OllamaLLMProvider
from lumen.providers.ollama import _map_error as map_ollama_error
from lumen.providers.protocols import (
    AudioTranscriptionProvider,
    EmbeddingProvider,
    LLMProvider,
    TTSProvider,
)
from lumen.schemas.enums import ModelRole

TEXT_PROVIDERS = [GeminiLLMProvider, OllamaLLMProvider, FakeLLMProvider]
EMBEDDING_PROVIDERS = [GeminiEmbeddingProvider, OllamaEmbeddingProvider, FakeEmbeddingProvider]


def build_text_provider(cls, config):
    """Build one of the text providers with a stand-in client."""
    if cls is FakeLLMProvider:
        return cls([], config=config)
    return cls("some-model", ModelRole.LIGHTWEIGHT, config, client=object())


def build_embedding_provider(cls, config):
    """Build one of the embedding providers with a stand-in client."""
    if cls is FakeEmbeddingProvider:
        return cls(config=config)
    return cls("text-embedding-004", config, client=object())


class TestTextProviders:
    @pytest.mark.parametrize("cls", TEXT_PROVIDERS)
    def test_it_satisfies_the_interface(self, cls, provider_config):
        assert isinstance(build_text_provider(cls, provider_config), LLMProvider)

    @pytest.mark.parametrize("cls", TEXT_PROVIDERS)
    def test_it_names_itself(self, cls, provider_config):
        provider = build_text_provider(cls, provider_config)
        assert isinstance(provider.provider_name, str) and provider.provider_name

    @pytest.mark.parametrize("cls", TEXT_PROVIDERS)
    def test_it_knows_its_model_and_role(self, cls, provider_config):
        provider = build_text_provider(cls, provider_config)
        assert provider.model_name
        assert isinstance(provider.model_role, ModelRole)

    @pytest.mark.parametrize("cls", TEXT_PROVIDERS)
    def test_each_provider_has_its_own_name(self, cls, provider_config):
        """So the cache and the log lines can tell them apart."""
        names = {
            build_text_provider(other, provider_config).provider_name
            for other in TEXT_PROVIDERS
        }
        assert len(names) == len(TEXT_PROVIDERS)


class TestEmbeddingProviders:
    @pytest.mark.parametrize("cls", EMBEDDING_PROVIDERS)
    def test_it_satisfies_the_interface(self, cls, provider_config):
        assert isinstance(build_embedding_provider(cls, provider_config), EmbeddingProvider)

    @pytest.mark.parametrize("cls", EMBEDDING_PROVIDERS)
    def test_it_declares_a_vector_width(self, cls, provider_config):
        provider = build_embedding_provider(cls, provider_config)
        assert isinstance(provider.dimensions, int) and provider.dimensions > 0


class TestTheAudioInterfaces:
    """
    Both take and return the audio itself rather than a path to it.

    A recording arrives from a browser as bytes and is about to go straight
    to a model. A file on the way through would put somebody's voice on the
    filesystem and buy nothing.
    """

    def test_listening_takes_the_recording_and_its_format(self):
        signature = inspect.signature(AudioTranscriptionProvider.transcribe)

        assert list(signature.parameters) == ["self", "audio", "mime_type"]

    def test_speaking_takes_text_and_gives_back_a_recording(self):
        signature = inspect.signature(TTSProvider.synthesize)

        assert list(signature.parameters) == ["self", "text"]

    def test_both_have_a_real_implementation_now(self):
        from lumen.providers.audio import (
            GeminiSpeechProvider,
            GeminiTranscriptionProvider,
        )

        assert isinstance(GeminiTranscriptionProvider, type)
        assert hasattr(GeminiTranscriptionProvider, "transcribe")
        assert hasattr(GeminiSpeechProvider, "synthesize")

    def test_a_scripted_stand_in_ships_for_both(self):
        """
        Voice is the one job with no local option at all, so without these
        nothing about the spoken path could run on a machine with no
        credential.
        """
        from lumen.providers.fake import FakeSpeechProvider, FakeTranscriptionProvider

        assert isinstance(FakeTranscriptionProvider(), AudioTranscriptionProvider)
        assert isinstance(FakeSpeechProvider(), TTSProvider)


class TestVendorIsolation:
    def test_only_the_vendor_modules_mention_their_libraries(self):
        """
        The whole abstraction rests on this. One stray import elsewhere and
        swapping vendors stops being a configuration change.
        """
        import pathlib

        package = pathlib.Path(__file__).parent.parent / "providers"
        allowed = {"gemini.py", "ollama.py"}

        offenders = []
        for path in package.glob("*.py"):
            if path.name in allowed:
                continue
            text = path.read_text()
            if "google.genai" in text or "import ollama" in text:
                offenders.append(path.name)

        assert offenders == []

    def test_the_package_imports_without_any_vendor_library(self, monkeypatch):
        """
        Vendor libraries load only when that vendor is chosen, so a machine
        running everything locally never needs a cloud library installed.
        """
        import subprocess
        import sys

        script = (
            "import sys\n"
            "for name in list(sys.modules):\n"
            "    if 'google' in name or 'ollama' in name:\n"
            "        del sys.modules[name]\n"
            "import lumen.providers\n"
            "loaded = [n for n in sys.modules if n.startswith(('google', 'ollama'))]\n"
            "print('LOADED:' + ','.join(sorted(loaded)))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=True
        )
        assert "LOADED:" in result.stdout
        assert result.stdout.strip().endswith("LOADED:")


class TestBuildingForReal:
    """
    Constructing the providers the way the factory does, without a stand-in
    client. No call is made, so no network is needed — but the client really is
    built, which is the part that would otherwise never be exercised until
    somebody ran the application.
    """

    def test_a_gemini_text_provider_can_be_built(self, monkeypatch, provider_config):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
        provider = _build_gemini_llm("gemini-2.5-flash", ModelRole.LIGHTWEIGHT, provider_config)
        assert provider.provider_name == "gemini"

    def test_a_gemini_embedding_provider_can_be_built(self, monkeypatch, provider_config):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
        provider = _build_gemini_embedding("text-embedding-004", provider_config)
        assert provider.dimensions == 768

    def test_an_ollama_text_provider_can_be_built(self, provider_config):
        """Needs no credential, which is the point of a local provider."""
        provider = _build_ollama_llm("llama3.3", ModelRole.THINKING, provider_config)
        assert provider.provider_name == "ollama"

    def test_an_ollama_embedding_provider_can_be_built(self, provider_config):
        provider = _build_ollama_embedding("nomic-embed-text", provider_config)
        assert provider.dimensions == 768

    def test_closing_a_real_provider_is_safe(self, provider_config):
        _build_ollama_llm("llama3.3", ModelRole.LIGHTWEIGHT, provider_config).close()


class TestErrorsAreNotWrappedTwice:
    """
    A failure that has already been translated passes through unchanged. Without
    this, a configuration error raised while reading a reply would be rewrapped
    as something generic and become retryable.
    """

    def test_gemini_leaves_an_already_translated_error_alone(self):
        original = ProviderContentBlockedError("refused")
        assert map_gemini_error(original, model="m", role=ModelRole.LIGHTWEIGHT) is original

    def test_ollama_leaves_an_already_translated_error_alone(self):
        original = ProviderConfigurationError("bad setup")
        assert (
            map_ollama_error(original, model="m", role=ModelRole.LIGHTWEIGHT, host="h")
            is original
        )


class TestReadingSdkEnumNames:
    """
    The SDK sometimes hands back an enum and sometimes a plain string. Both are
    flattened to the same thing so the rest of the code does not have to care.
    """

    def test_nothing_stays_nothing(self):
        assert _name_of(None) is None

    def test_an_enum_gives_its_name(self):
        from enum import Enum

        class Reason(Enum):
            SAFETY = 1

        assert _name_of(Reason.SAFETY) == "SAFETY"

    def test_a_plain_string_is_returned_as_is(self):
        assert _name_of("MAX_TOKENS") == "MAX_TOKENS"
