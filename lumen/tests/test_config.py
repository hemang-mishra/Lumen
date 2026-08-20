"""
Test suite for lumen/config.py, focused on ProviderConfig — the single
point of configuration for every AI provider role (see
lumen.schemas.enums.ModelRole and implementation/Goal_2_Plan.md).
"""

from __future__ import annotations

import dataclasses
import os

import pytest

from lumen.config import (
    AppConfig,
    GraphConfig,
    ObservabilityConfig,
    OperationalConfig,
    PipelineConfig,
    ProviderConfig,
    VectorConfig,
)
from lumen.schemas.enums import ModelRole

# Every setting here can be overridden from the environment, so a developer who
# happens to export one would otherwise see these tests fail for no reason.
_CONFIG_ENV_PREFIXES = ("LUMEN_", "GEMINI_API_KEY", "GOOGLE_API_KEY")


@pytest.fixture(autouse=True)
def clean_config_env(monkeypatch):
    """Run every test in this module against a bare environment."""
    for name in list(os.environ):
        if name.startswith(_CONFIG_ENV_PREFIXES):
            monkeypatch.delenv(name, raising=False)


class TestProviderConfigDefaults:
    def test_all_five_roles_have_defaults(self):
        cfg = ProviderConfig()
        assert cfg.lightweight_provider and cfg.lightweight_model
        assert cfg.thinking_provider and cfg.thinking_model
        assert cfg.embedding_provider and cfg.embedding_model
        assert cfg.transcription_provider and cfg.transcription_model
        assert cfg.tts_provider and cfg.tts_model

    def test_is_frozen(self):
        cfg = ProviderConfig()
        with pytest.raises(Exception):
            cfg.lightweight_model = "something-else"


class TestProviderConfigResolve:
    @pytest.mark.parametrize(
        "role,expected_provider,expected_model",
        [
            (ModelRole.LIGHTWEIGHT, "gemini", "gemini-2.5-flash"),
            (ModelRole.THINKING, "gemini", "gemini-2.5-pro"),
            (ModelRole.CONVERSATION, "gemini", "gemini-2.5-flash"),
            (ModelRole.EMBEDDING, "gemini", "text-embedding-004"),
            (ModelRole.TRANSCRIPTION, "gemini", "gemini-2.5-flash"),
            (ModelRole.TTS, "gemini", "gemini-2.5-flash-preview-tts"),
        ],
    )
    def test_resolve_returns_configured_pair(self, role, expected_provider, expected_model):
        cfg = ProviderConfig()
        assert cfg.resolve(role) == (expected_provider, expected_model)

    def test_resolve_reflects_custom_config(self):
        cfg = ProviderConfig(thinking_provider="ollama", thinking_model="llama-3.3-70b")
        assert cfg.resolve(ModelRole.THINKING) == ("ollama", "llama-3.3-70b")
        # other roles remain independently configured — no coupling between roles
        assert cfg.resolve(ModelRole.LIGHTWEIGHT) == ("gemini", "gemini-2.5-flash")

    def test_all_roles_are_independently_resolvable(self):
        """No role's resolution depends on another's — this is the whole point
        of not forcing a single local/cloud decision across all roles."""
        cfg = ProviderConfig(
            lightweight_provider="ollama", lightweight_model="phi-3",
            embedding_provider="ollama", embedding_model="nomic-embed-text",
        )
        assert cfg.resolve(ModelRole.LIGHTWEIGHT) == ("ollama", "phi-3")
        assert cfg.resolve(ModelRole.EMBEDDING) == ("ollama", "nomic-embed-text")
        assert cfg.resolve(ModelRole.THINKING) == ("gemini", "gemini-2.5-pro")


class TestAppConfigComposesProviderConfig:
    def test_default_app_config_has_provider_config(self):
        app_cfg = AppConfig()
        assert isinstance(app_cfg.providers, ProviderConfig)
        assert isinstance(app_cfg.graph, GraphConfig)
        assert isinstance(app_cfg.vector, VectorConfig)

    def test_app_config_provider_role_resolvable(self):
        app_cfg = AppConfig()
        assert app_cfg.providers.resolve(ModelRole.THINKING) == ("gemini", "gemini-2.5-pro")

    def test_default_app_config_has_pipeline_config(self):
        assert isinstance(AppConfig().pipeline, PipelineConfig)


class TestPipelineConfig:
    def test_the_documented_defaults_are_what_ship(self):
        cfg = PipelineConfig()
        assert cfg.min_reflection_words == 30
        assert cfg.coherence_threshold == 0.4
        assert cfg.reflection_prompt_count == 3
        assert cfg.max_episodes_per_session == 12
        assert cfg.max_observations_per_episode == 25
        assert cfg.max_causal_chains_per_episode == 5
        assert cfg.max_causal_steps_per_chain == 12
        assert cfg.max_extraction_attempts == 3

    @pytest.mark.parametrize(
        "variable,value,field,expected",
        [
            ("LUMEN_MIN_REFLECTION_WORDS", "50", "min_reflection_words", 50),
            ("LUMEN_COHERENCE_THRESHOLD", "0.65", "coherence_threshold", 0.65),
            ("LUMEN_REFLECTION_PROMPT_COUNT", "5", "reflection_prompt_count", 5),
            ("LUMEN_MAX_EPISODES", "4", "max_episodes_per_session", 4),
            ("LUMEN_MAX_OBSERVATIONS", "9", "max_observations_per_episode", 9),
            ("LUMEN_MAX_CAUSAL_CHAINS", "2", "max_causal_chains_per_episode", 2),
            ("LUMEN_MAX_CAUSAL_STEPS", "6", "max_causal_steps_per_chain", 6),
            ("LUMEN_MAX_EXTRACTION_ATTEMPTS", "1", "max_extraction_attempts", 1),
        ],
    )
    def test_each_threshold_is_overridable_on_its_own(
        self, monkeypatch, variable, value, field, expected
    ):
        monkeypatch.setenv(variable, value)
        cfg = PipelineConfig()

        assert getattr(cfg, field) == expected
        # The others keep their defaults.
        untouched = {
            "min_reflection_words": 30,
            "coherence_threshold": 0.4,
            "reflection_prompt_count": 3,
            "max_episodes_per_session": 12,
            "max_observations_per_episode": 25,
            "max_causal_chains_per_episode": 5,
            "max_causal_steps_per_chain": 12,
            "max_extraction_attempts": 3,
        }
        del untouched[field]
        for name, default in untouched.items():
            assert getattr(cfg, name) == default

    def test_the_environment_is_read_when_the_config_is_built(self, monkeypatch):
        # Set after this module was imported, so a value captured at import
        # time would be the old one.
        monkeypatch.setenv("LUMEN_MIN_REFLECTION_WORDS", "77")
        assert PipelineConfig().min_reflection_words == 77


class TestEnvironmentIsReadOnConstruction:
    """
    Config reads the environment when an object is built, not when the module
    is imported. Field defaults evaluate once at class-creation time, so the
    naive version of this silently ignores any variable set after the first
    import — including everything in a .env file loaded during startup.
    """

    def test_a_role_can_be_redirected_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("LUMEN_THINKING_PROVIDER", "ollama")
        monkeypatch.setenv("LUMEN_THINKING_MODEL", "llama3.3:70b")
        assert ProviderConfig().resolve(ModelRole.THINKING) == ("ollama", "llama3.3:70b")

    def test_each_role_is_overridable_independently(self, monkeypatch):
        monkeypatch.setenv("LUMEN_EMBEDDING_PROVIDER", "ollama")
        monkeypatch.setenv("LUMEN_EMBEDDING_MODEL", "nomic-embed-text")

        cfg = ProviderConfig()
        assert cfg.resolve(ModelRole.EMBEDDING) == ("ollama", "nomic-embed-text")
        assert cfg.resolve(ModelRole.THINKING) == ("gemini", "gemini-2.5-pro")
        assert cfg.resolve(ModelRole.LIGHTWEIGHT) == ("gemini", "gemini-2.5-flash")

    def test_every_role_can_be_moved_to_a_local_provider(self, monkeypatch):
        """The one-time deployment choice a maintainer makes to run offline."""
        for role in ModelRole:
            monkeypatch.setenv(f"LUMEN_{role.value}_PROVIDER", "ollama")
            monkeypatch.setenv(f"LUMEN_{role.value}_MODEL", "local-model")

        cfg = ProviderConfig()
        for role in ModelRole:
            assert cfg.resolve(role) == ("ollama", "local-model")

    def test_a_later_change_is_picked_up_by_a_new_instance(self, monkeypatch):
        monkeypatch.setenv("LUMEN_THINKING_MODEL", "first")
        assert ProviderConfig().thinking_model == "first"

        monkeypatch.setenv("LUMEN_THINKING_MODEL", "second")
        assert ProviderConfig().thinking_model == "second"

    def test_the_other_config_objects_read_the_environment_too(self, monkeypatch):
        monkeypatch.setenv("LUMEN_GRAPH_DB_PATH", "/tmp/graph.db")
        monkeypatch.setenv("LUMEN_VECTOR_SIZE", "1024")
        monkeypatch.setenv("LUMEN_OPS_DB_URL", "sqlite:///./other.db")
        monkeypatch.setenv("LUMEN_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("LUMEN_USER_ID", "someone")

        assert GraphConfig().db_path == "/tmp/graph.db"
        assert VectorConfig().vector_size == 1024
        assert OperationalConfig().db_url == "sqlite:///./other.db"
        assert ObservabilityConfig().log_level == "DEBUG"
        assert AppConfig().default_user_id == "someone"

    def test_nested_config_inside_app_config_sees_the_environment(self, monkeypatch):
        monkeypatch.setenv("LUMEN_LIGHTWEIGHT_PROVIDER", "ollama")
        assert AppConfig().providers.lightweight_provider == "ollama"


class TestBooleanEnvironmentValues:
    @pytest.mark.parametrize("raw,expected", [
        ("true", True), ("TRUE", True), ("1", True), ("yes", True),
        ("false", False), ("FALSE", False), ("0", False), ("no", False),
    ])
    def test_recognised_values(self, monkeypatch, raw, expected):
        monkeypatch.setenv("LUMEN_OPS_DB_ECHO", raw)
        assert OperationalConfig().echo_sql is expected

    def test_an_unrecognised_value_keeps_the_default(self, monkeypatch):
        """Better to keep a working default than to guess what "maybe" meant."""
        monkeypatch.setenv("LUMEN_OPS_DB_ECHO", "maybe")
        assert OperationalConfig().echo_sql is False

        monkeypatch.setenv("LUMEN_LOG_CONSOLE", "maybe")
        assert ObservabilityConfig().log_to_console is True

    def test_console_logging_is_on_unless_switched_off(self, monkeypatch):
        assert ObservabilityConfig().log_to_console is True
        monkeypatch.setenv("LUMEN_LOG_CONSOLE", "false")
        assert ObservabilityConfig().log_to_console is False


class TestCredentialsCannotLeak:
    """
    Credentials are a property, not a field. Config objects get snapshotted —
    pipeline_jobs.config_snapshot holds one per run — and anything that walks
    the dataclass fields would carry a plaintext key into the database.
    """

    SECRET = "sk-do-not-store-this-anywhere"

    def test_the_key_is_readable_when_asked_for_by_name(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", self.SECRET)
        assert ProviderConfig().gemini_api_key == self.SECRET

    def test_google_api_key_is_accepted_as_a_fallback(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", self.SECRET)
        assert ProviderConfig().gemini_api_key == self.SECRET

    def test_an_absent_key_reads_as_nothing(self):
        assert ProviderConfig().gemini_api_key is None

    def test_the_key_is_not_a_dataclass_field(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", self.SECRET)
        names = {f.name for f in dataclasses.fields(ProviderConfig())}
        assert "gemini_api_key" not in names

    def test_asdict_cannot_carry_the_key(self, monkeypatch):
        """This is the path into pipeline_jobs.config_snapshot."""
        monkeypatch.setenv("GEMINI_API_KEY", self.SECRET)
        assert self.SECRET not in str(dataclasses.asdict(ProviderConfig()))
        assert self.SECRET not in str(dataclasses.asdict(AppConfig()))

    def test_repr_cannot_carry_the_key(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", self.SECRET)
        assert self.SECRET not in repr(ProviderConfig())
        assert self.SECRET not in repr(AppConfig())

    def test_two_configs_compare_equal_regardless_of_the_key(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", self.SECRET)
        with_key = ProviderConfig()
        monkeypatch.delenv("GEMINI_API_KEY")
        without_key = ProviderConfig()
        assert with_key == without_key

    def test_a_rotated_key_takes_effect_without_rebuilding_config(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "old-key")
        cfg = ProviderConfig()
        monkeypatch.setenv("GEMINI_API_KEY", "new-key")
        assert cfg.gemini_api_key == "new-key"

    def test_a_whole_set_of_keys_cannot_leak_either(self, monkeypatch):
        """The plural form has to keep the same promise as the singular one."""
        monkeypatch.setenv("GEMINI_API_KEYS", f"{self.SECRET},{self.SECRET}-two")
        assert self.SECRET not in str(dataclasses.asdict(AppConfig()))
        assert self.SECRET not in repr(AppConfig())
        names = {f.name for f in dataclasses.fields(ProviderConfig())}
        assert "gemini_api_keys" not in names


class TestSeveralCredentials:
    """
    Quotas are metered per key, so a deployment with several keys can do
    several times the work in a minute — provided the keys are all found.
    """

    def test_no_keys_reads_as_an_empty_set(self):
        assert ProviderConfig().gemini_api_keys == ()

    def test_the_single_key_form_still_works(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "solo")
        assert ProviderConfig().gemini_api_keys == ("solo",)

    def test_a_comma_separated_list_is_read_in_order(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEYS", "one,two,three")
        assert ProviderConfig().gemini_api_keys == ("one", "two", "three")

    def test_spacing_around_a_comma_is_forgiven(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEYS", " one , two ,, three ")
        assert ProviderConfig().gemini_api_keys == ("one", "two", "three")

    def test_numbered_variables_are_read_from_one_upwards(self, monkeypatch):
        for index, value in enumerate(["one", "two", "three"], start=1):
            monkeypatch.setenv(f"GEMINI_API_KEY_{index}", value)
        assert ProviderConfig().gemini_api_keys == ("one", "two", "three")

    def test_a_gap_in_the_numbering_ends_the_list(self, monkeypatch):
        """
        Silently jumping the gap would make GEMINI_API_KEY_4, sitting above a
        commented-out third, look like it was being used when it was not.
        """
        monkeypatch.setenv("GEMINI_API_KEY_1", "one")
        monkeypatch.setenv("GEMINI_API_KEY_2", "two")
        monkeypatch.setenv("GEMINI_API_KEY_4", "four")
        assert ProviderConfig().gemini_api_keys == ("one", "two")

    def test_the_forms_combine(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEYS", "one,two")
        monkeypatch.setenv("GEMINI_API_KEY_1", "three")
        monkeypatch.setenv("GEMINI_API_KEY", "four")
        assert ProviderConfig().gemini_api_keys == ("one", "two", "three", "four")

    def test_the_same_key_named_twice_counts_once(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEYS", "one,two")
        monkeypatch.setenv("GEMINI_API_KEY", "one")
        assert ProviderConfig().gemini_api_keys == ("one", "two")

    def test_the_singular_reads_the_first_of_several(self, monkeypatch):
        """Callers that only ever wanted "a credential" keep working."""
        monkeypatch.setenv("GEMINI_API_KEYS", "one,two")
        assert ProviderConfig().gemini_api_key == "one"

    def test_added_keys_take_effect_without_rebuilding_config(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEYS", "one")
        cfg = ProviderConfig()
        monkeypatch.setenv("GEMINI_API_KEYS", "one,two")
        assert cfg.gemini_api_keys == ("one", "two")

    def test_the_rotation_strategy_defaults_to_random(self):
        assert ProviderConfig().key_rotation_strategy == "random"

    def test_the_rotation_strategy_is_configurable(self, monkeypatch):
        monkeypatch.setenv("LUMEN_KEY_ROTATION_STRATEGY", "round_robin")
        assert ProviderConfig().key_rotation_strategy == "round_robin"
