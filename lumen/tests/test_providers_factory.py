"""
Tests for turning a role into a provider.

Beyond the obvious resolution behaviour, two design rules are checked here that
would be easy to erode later: nothing in this layer may reach for the database,
and asking for a role that cannot generate text is treated as a mistake in the
calling code rather than quietly doing something surprising.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from lumen.config import AppConfig, ProviderConfig, VectorConfig
from lumen.providers.errors import ProviderConfigurationError
from lumen.providers.factory import (
    close_all_providers,
    get_embedding_provider,
    get_llm_provider,
    register_embedding_provider,
    register_llm_provider,
    reset_provider_cache,
    validate_providers,
)
from lumen.providers.fake import FakeEmbeddingProvider, FakeLLMProvider, fake_scripts
from lumen.providers.protocols import EmbeddingProvider, LLMProvider
from lumen.providers.results import ChatMessage
from lumen.schemas.enums import ModelRole


class Answer(BaseModel):
    """A small shape for the structured-output checks."""

    answer: str


def all_fake(**overrides) -> AppConfig:
    """Configuration with every role pointed at the scripted stand-ins."""
    settings = {
        "lightweight_provider": "fake",
        "thinking_provider": "fake",
        "embedding_provider": "fake",
        "embedding_model": "fake-embedding",
        "max_attempts": 1,
        "backoff_base_seconds": 0.0,
    }
    settings.update(overrides)
    return AppConfig(providers=ProviderConfig(**settings))


class TestResolvingRoles:
    def test_a_fast_role_resolves(self):
        assert isinstance(get_llm_provider(ModelRole.LIGHTWEIGHT, all_fake()), LLMProvider)

    def test_a_deep_reasoning_role_resolves(self):
        assert isinstance(get_llm_provider(ModelRole.THINKING, all_fake()), LLMProvider)

    def test_the_embedding_role_resolves(self):
        assert isinstance(get_embedding_provider(all_fake()), EmbeddingProvider)

    def test_the_configured_model_is_used(self):
        config = all_fake(thinking_model="a-specific-model")
        assert get_llm_provider(ModelRole.THINKING, config).model_name == "a-specific-model"

    def test_the_provider_knows_which_role_it_serves(self):
        provider = get_llm_provider(ModelRole.THINKING, all_fake())
        assert provider.model_role is ModelRole.THINKING

    def test_roles_are_configured_independently(self):
        """One role moving to another vendor leaves the rest alone."""
        config = all_fake(thinking_provider="fake", lightweight_provider="fake",
                          thinking_model="deep-model", lightweight_model="fast-model")
        assert get_llm_provider(ModelRole.THINKING, config).model_name == "deep-model"
        assert get_llm_provider(ModelRole.LIGHTWEIGHT, config).model_name == "fast-model"

    def test_configuration_is_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("LUMEN_LIGHTWEIGHT_PROVIDER", "fake")
        monkeypatch.setenv("LUMEN_LIGHTWEIGHT_MODEL", "from-the-environment")
        provider = get_llm_provider(ModelRole.LIGHTWEIGHT)
        assert provider.model_name == "from-the-environment"


class TestRefusingBadRequests:
    def test_an_unknown_provider_name_is_refused(self):
        config = all_fake(lightweight_provider="nonesuch")
        with pytest.raises(ProviderConfigurationError, match="nonesuch"):
            get_llm_provider(ModelRole.LIGHTWEIGHT, config)

    def test_the_refusal_lists_what_is_available(self):
        config = all_fake(lightweight_provider="nonesuch")
        with pytest.raises(ProviderConfigurationError, match="gemini"):
            get_llm_provider(ModelRole.LIGHTWEIGHT, config)

    def test_an_unknown_embedding_provider_is_refused(self):
        config = all_fake(embedding_provider="nonesuch")
        with pytest.raises(ProviderConfigurationError, match="nonesuch"):
            get_embedding_provider(config)

    def test_asking_for_embeddings_as_a_text_model_is_refused(self):
        """A mistake in the calling code, so it says which function to use."""
        with pytest.raises(ProviderConfigurationError, match="get_embedding_provider"):
            get_llm_provider(ModelRole.EMBEDDING, all_fake())

    @pytest.mark.parametrize("role", [ModelRole.TRANSCRIPTION, ModelRole.TTS])
    def test_roles_with_no_implementation_say_so(self, role):
        with pytest.raises(ProviderConfigurationError, match="no implementation yet"):
            get_llm_provider(role, all_fake())

    def test_the_message_says_what_will_bring_it(self):
        with pytest.raises(ProviderConfigurationError, match="voice"):
            get_llm_provider(ModelRole.TRANSCRIPTION, all_fake())


class TestVectorWidthCheck:
    def test_a_matching_width_is_accepted(self):
        config = AppConfig(
            providers=ProviderConfig(embedding_provider="fake", embedding_model="nomic-embed-text"),
            vector=VectorConfig(vector_size=768),
        )
        assert get_embedding_provider(config).dimensions == 768

    def test_a_mismatched_width_is_refused(self):
        """
        Caught here rather than surfacing much later as a rejected write, far
        from the setting that caused it.
        """
        config = AppConfig(
            providers=ProviderConfig(embedding_provider="fake", embedding_model="mxbai-embed-large"),
            vector=VectorConfig(vector_size=768),
        )
        with pytest.raises(ProviderConfigurationError, match="1024"):
            get_embedding_provider(config)

    def test_the_refusal_names_both_numbers(self):
        config = AppConfig(
            providers=ProviderConfig(embedding_provider="fake", embedding_model="mxbai-embed-large"),
            vector=VectorConfig(vector_size=768),
        )
        with pytest.raises(ProviderConfigurationError, match="768"):
            get_embedding_provider(config)

    def test_the_refusal_says_what_to_change(self):
        config = AppConfig(
            providers=ProviderConfig(embedding_provider="fake", embedding_model="mxbai-embed-large"),
            vector=VectorConfig(vector_size=768),
        )
        with pytest.raises(ProviderConfigurationError, match="LUMEN_VECTOR_SIZE"):
            get_embedding_provider(config)


class TestCaching:
    def test_the_same_provider_comes_back_each_time(self):
        """The network client underneath should not be rebuilt per journal entry."""
        config = all_fake()
        first = get_llm_provider(ModelRole.LIGHTWEIGHT, config)
        second = get_llm_provider(ModelRole.LIGHTWEIGHT, config)
        assert first is second

    def test_different_roles_get_different_providers(self):
        config = all_fake()
        fast = get_llm_provider(ModelRole.LIGHTWEIGHT, config)
        deep = get_llm_provider(ModelRole.THINKING, config)
        assert fast is not deep

    def test_a_different_model_gets_a_different_provider(self):
        first = get_llm_provider(ModelRole.LIGHTWEIGHT, all_fake(lightweight_model="one"))
        second = get_llm_provider(ModelRole.LIGHTWEIGHT, all_fake(lightweight_model="two"))
        assert first is not second

    def test_embedding_providers_are_cached_too(self):
        config = all_fake()
        assert get_embedding_provider(config) is get_embedding_provider(config)

    def test_clearing_the_cache_gives_a_fresh_provider(self):
        config = all_fake()
        first = get_llm_provider(ModelRole.LIGHTWEIGHT, config)
        reset_provider_cache()
        assert get_llm_provider(ModelRole.LIGHTWEIGHT, config) is not first


class TestStartupCheck:
    def test_a_working_configuration_passes(self):
        validate_providers(all_fake())

    def test_a_broken_configuration_fails_here(self):
        """
        The whole point: without this, a bad setting is not noticed until a
        pipeline run is already underway and has written state.
        """
        config = all_fake(thinking_provider="nonesuch")
        with pytest.raises(ProviderConfigurationError):
            validate_providers(config)

    def test_a_bad_embedding_width_is_caught_at_startup(self):
        config = AppConfig(
            providers=ProviderConfig(
                lightweight_provider="fake",
                thinking_provider="fake",
                embedding_provider="fake",
                embedding_model="mxbai-embed-large",
            ),
            vector=VectorConfig(vector_size=768),
        )
        with pytest.raises(ProviderConfigurationError):
            validate_providers(config)

    def test_checking_also_warms_the_cache(self):
        """Nothing is thrown away, so the first real call is not slowed down."""
        config = all_fake()
        validate_providers(config)
        provider = get_llm_provider(ModelRole.LIGHTWEIGHT, config)
        assert get_llm_provider(ModelRole.LIGHTWEIGHT, config) is provider

    def test_a_missing_credential_is_reported(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        config = AppConfig(providers=ProviderConfig(lightweight_provider="gemini"))
        with pytest.raises(ProviderConfigurationError, match="GEMINI_API_KEY"):
            validate_providers(config)


class TestShutdown:
    def test_every_provider_is_closed(self):
        config = all_fake()
        fast = get_llm_provider(ModelRole.LIGHTWEIGHT, config)
        deep = get_llm_provider(ModelRole.THINKING, config)

        close_all_providers()

        assert fast.closed is True
        assert deep.closed is True

    def test_the_cache_is_emptied(self):
        config = all_fake()
        first = get_llm_provider(ModelRole.LIGHTWEIGHT, config)
        close_all_providers()
        assert get_llm_provider(ModelRole.LIGHTWEIGHT, config) is not first

    def test_closing_with_nothing_built_is_fine(self):
        close_all_providers()

    def test_one_provider_failing_does_not_stop_the_rest(self):
        """Tidying up should not be the thing that breaks a shutdown."""

        class Awkward(FakeLLMProvider):
            def close(self):
                raise RuntimeError("will not close")

        register_llm_provider("awkward", lambda model, role, config: Awkward([], model=model, role=role, config=config))
        try:
            config = all_fake(lightweight_provider="awkward", thinking_provider="fake")
            get_llm_provider(ModelRole.LIGHTWEIGHT, config)
            good = get_llm_provider(ModelRole.THINKING, config)

            close_all_providers()

            assert good.closed is True
        finally:
            from lumen.providers.factory import _llm_builders

            _llm_builders.pop("awkward", None)


class TestExtensibility:
    def test_a_new_text_provider_can_be_added_without_editing_the_factory(self):
        """
        Which is the point of a registry: supporting another vendor means adding
        an entry, not changing the code that does the choosing.
        """

        def build(model, role, config):
            return FakeLLMProvider(["registered"], model=model, role=role, config=config)

        register_llm_provider("brand-new", build)
        try:
            config = all_fake(lightweight_provider="brand-new")
            provider = get_llm_provider(ModelRole.LIGHTWEIGHT, config)
            result = provider.generate_text([ChatMessage(role="user", content="hi")])
            assert result.text == "registered"
        finally:
            from lumen.providers.factory import _llm_builders

            _llm_builders.pop("brand-new", None)

    def test_a_new_embedding_provider_can_be_added(self):
        def build(model, config):
            return FakeEmbeddingProvider(model=model, dimensions=768, config=config)

        register_embedding_provider("brand-new", build)
        try:
            provider = get_embedding_provider(all_fake(embedding_provider="brand-new"))
            assert provider.dimensions == 768
        finally:
            from lumen.providers.factory import _embedding_builders

            _embedding_builders.pop("brand-new", None)


class TestNoDatabaseDependency:
    def test_the_providers_package_does_not_import_the_operational_store(self):
        """
        Which model backs a role is decided by whoever deploys Lumen, in the
        environment, and cannot be changed while running. An import edge to the
        database layer is the only way a runtime override could appear, so its
        absence is what actually holds that rule in place.
        """
        import pathlib

        package = pathlib.Path(__file__).parent.parent / "providers"
        offenders = [
            path.name
            for path in package.glob("*.py")
            if "lumen.operational" in path.read_text()
        ]
        assert offenders == []

    def test_choosing_a_provider_touches_no_database(self, monkeypatch):
        """A second line of defence, in case an import arrives indirectly."""
        import lumen.operational.sqlalchemy_impl as store

        def fail(*args, **kwargs):
            raise AssertionError("the factory must not build an operational store")

        monkeypatch.setattr(store, "SQLAlchemyOperationalStore", fail)
        get_llm_provider(ModelRole.LIGHTWEIGHT, all_fake())


class TestScriptedProviderThroughConfiguration:
    def test_a_script_left_for_a_role_is_picked_up(self):
        """
        A script cannot travel through an environment variable, so it is left in
        a shared place for the factory to find. This is what lets a whole
        pipeline run offline.
        """
        fake_scripts.register(ModelRole.LIGHTWEIGHT, ['{"answer": "from the script"}'])
        provider = get_llm_provider(ModelRole.LIGHTWEIGHT, all_fake())
        assert provider.generate_structured("q", Answer).data == {"answer": "from the script"}

    def test_scripts_are_kept_per_role(self):
        fake_scripts.register(ModelRole.LIGHTWEIGHT, ["fast reply"])
        fake_scripts.register(ModelRole.THINKING, ["considered reply"])

        message = [ChatMessage(role="user", content="hi")]
        config = all_fake()
        assert get_llm_provider(ModelRole.LIGHTWEIGHT, config).generate_text(message).text == "fast reply"
        assert get_llm_provider(ModelRole.THINKING, config).generate_text(message).text == "considered reply"

    def test_a_fake_embedding_matches_the_width_of_what_it_stands_in_for(self):
        config = all_fake(embedding_model="nomic-embed-text")
        assert get_embedding_provider(config).dimensions == 768

    def test_a_fake_with_an_unrecognised_name_matches_the_vector_store(self):
        """A stand-in should never be the reason a size check fails."""
        config = AppConfig(
            providers=ProviderConfig(embedding_provider="fake", embedding_model="whatever"),
            vector=VectorConfig(vector_size=768),
        )
        assert get_embedding_provider(config).dimensions == 768


class TestStatingAnUnknownModelsWidth:
    """
    The escape hatch the refusal message points at. Without this, the advice in
    that message would be advice nobody could act on.
    """

    def test_a_stated_width_lets_an_unknown_model_be_used(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        config = AppConfig(
            providers=ProviderConfig(
                embedding_provider="gemini",
                embedding_model="some-brand-new-model",
                embedding_dimensions=1536,
            ),
            vector=VectorConfig(vector_size=1536),
        )
        assert get_embedding_provider(config).dimensions == 1536

    def test_without_a_stated_width_it_is_still_refused(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        config = AppConfig(
            providers=ProviderConfig(
                embedding_provider="gemini",
                embedding_model="some-brand-new-model",
            ),
        )
        with pytest.raises(ProviderConfigurationError, match="not known"):
            get_embedding_provider(config)

    def test_a_stated_width_that_disagrees_with_the_store_is_still_caught(self, monkeypatch):
        """Stating a width is permission to proceed, not permission to be wrong."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        config = AppConfig(
            providers=ProviderConfig(
                embedding_provider="gemini",
                embedding_model="some-brand-new-model",
                embedding_dimensions=1536,
            ),
            vector=VectorConfig(vector_size=768),
        )
        with pytest.raises(ProviderConfigurationError, match="1536"):
            get_embedding_provider(config)

    def test_a_known_model_ignores_a_stated_width(self, monkeypatch):
        """What we already know beats what somebody typed."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        config = AppConfig(
            providers=ProviderConfig(
                embedding_provider="gemini",
                embedding_model="text-embedding-004",
                embedding_dimensions=1536,
            ),
            vector=VectorConfig(vector_size=768),
        )
        assert get_embedding_provider(config).dimensions == 768

    def test_it_can_be_set_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("LUMEN_EMBEDDING_DIMENSIONS", "1024")
        assert ProviderConfig().embedding_dimensions == 1024

    def test_it_is_normally_unset(self):
        assert ProviderConfig().embedding_dimensions is None
