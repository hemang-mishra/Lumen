"""
Test suite for lumen/config.py, focused on ProviderConfig — the single
point of configuration for every AI provider role (see
lumen.schemas.enums.ModelRole and implementation/Goal_2_Plan.md).
"""

from __future__ import annotations

import pytest

from lumen.config import AppConfig, GraphConfig, ProviderConfig, VectorConfig
from lumen.schemas.enums import ModelRole


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
            (ModelRole.EMBEDDING, "gemini", "text-embedding-004"),
            (ModelRole.TRANSCRIPTION, "whisper_cpp", "base.en"),
            (ModelRole.TTS, "macos", "default"),
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
            embedding_provider="ollama", embedding_model="nomic-embed-large",
        )
        assert cfg.resolve(ModelRole.LIGHTWEIGHT) == ("ollama", "phi-3")
        assert cfg.resolve(ModelRole.EMBEDDING) == ("ollama", "nomic-embed-large")
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
