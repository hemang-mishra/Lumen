"""Tests for user settings and how they combine with configuration."""

from __future__ import annotations

import pytest

from lumen.config import ProviderConfig
from lumen.operational.repositories import UnknownSettingKeyError
from lumen.operational.sqlalchemy_impl import KNOWN_SETTING_KEYS, resolve_provider_config
from lumen.schemas.enums import ModelRole


class TestReadAndWrite:
    def test_a_setting_survives_the_round_trip(self, ops_store):
        ops_store.settings.set("local", "providers.thinking.model", "gemini-2.5-pro")
        assert ops_store.settings.get("local", "providers.thinking.model") == "gemini-2.5-pro"

    def test_an_unset_setting_reads_back_as_nothing(self, ops_store):
        assert ops_store.settings.get("local", "providers.thinking.model") is None

    def test_a_setting_can_be_changed(self, ops_store):
        ops_store.settings.set("local", "providers.thinking.provider", "gemini")
        ops_store.settings.set("local", "providers.thinking.provider", "ollama")
        assert ops_store.settings.get("local", "providers.thinking.provider") == "ollama"

    def test_numbers_keep_their_type(self, ops_store):
        ops_store.settings.set("local", "pipeline.session_decay_minutes", 30)
        value = ops_store.settings.get("local", "pipeline.session_decay_minutes")
        assert value == 30
        assert isinstance(value, int)

    def test_users_do_not_see_each_other_s_settings(self, ops_store):
        ops_store.settings.set("alice", "providers.thinking.model", "gemini-2.5-pro")
        assert ops_store.settings.get("bob", "providers.thinking.model") is None

    def test_everything_a_user_set_can_be_read_at_once(self, ops_store):
        ops_store.settings.set("local", "providers.thinking.model", "gemini-2.5-pro")
        ops_store.settings.set("local", "hitl.queue_cap", 10)

        assert ops_store.settings.get_all("local") == {
            "providers.thinking.model": "gemini-2.5-pro",
            "hitl.queue_cap": 10,
        }

    def test_a_user_with_no_settings_reads_back_empty(self, ops_store):
        assert ops_store.settings.get_all("local") == {}

    def test_records_carry_their_change_time(self, ops_store):
        ops_store.settings.set("local", "hitl.queue_cap", 10)
        records = ops_store.settings.get_records("local")
        assert len(records) == 1
        assert records[0].key == "hitl.queue_cap"
        assert records[0].updated_at is not None


class TestDelete:
    def test_removing_an_override_restores_the_default(self, ops_store):
        ops_store.settings.set("local", "hitl.queue_cap", 5)
        assert ops_store.settings.delete("local", "hitl.queue_cap") is True
        assert ops_store.settings.get("local", "hitl.queue_cap") is None

    def test_removing_something_that_was_never_set_reports_nothing_happened(self, ops_store):
        assert ops_store.settings.delete("local", "hitl.queue_cap") is False


class TestUnknownKeys:
    def test_an_unrecognised_key_is_refused(self, ops_store):
        """
        Storing it would leave the user believing they changed something that
        nothing will ever read.
        """
        with pytest.raises(UnknownSettingKeyError, match="not a recognised setting"):
            ops_store.settings.set("local", "providers.thnking.model", "oops")

    def test_the_refusal_lists_what_is_valid(self, ops_store):
        with pytest.raises(UnknownSettingKeyError, match="providers.thinking.model"):
            ops_store.settings.set("local", "made.up.key", "x")

    def test_every_role_has_settings(self, ops_store):
        for role in ModelRole:
            assert f"providers.{role.value.lower()}.provider" in KNOWN_SETTING_KEYS
            assert f"providers.{role.value.lower()}.model" in KNOWN_SETTING_KEYS


class TestResolveProviderConfig:
    def test_no_overrides_changes_nothing(self):
        base = ProviderConfig()
        assert resolve_provider_config(base, {}) is base

    def test_a_saved_setting_beats_the_default(self):
        resolved = resolve_provider_config(
            ProviderConfig(), {"providers.thinking.model": "llama3.3:70b"}
        )
        assert resolved.thinking_model == "llama3.3:70b"

    def test_untouched_roles_keep_their_values(self):
        base = ProviderConfig()
        resolved = resolve_provider_config(base, {"providers.thinking.model": "other"})
        assert resolved.embedding_model == base.embedding_model
        assert resolved.lightweight_provider == base.lightweight_provider

    def test_a_whole_role_can_be_redirected(self):
        resolved = resolve_provider_config(
            ProviderConfig(),
            {
                "providers.thinking.provider": "ollama",
                "providers.thinking.model": "llama3.3:70b",
            },
        )
        assert resolved.resolve(ModelRole.THINKING) == ("ollama", "llama3.3:70b")

    def test_every_role_can_be_redirected_at_once(self):
        """
        Someone wanting everything to run locally changes all the roles. That
        is a one-time choice, not something the pipeline decides per entry.
        """
        overrides = {}
        for role in ModelRole:
            overrides[f"providers.{role.value.lower()}.provider"] = "ollama"
            overrides[f"providers.{role.value.lower()}.model"] = "local-model"

        resolved = resolve_provider_config(ProviderConfig(), overrides)
        for role in ModelRole:
            assert resolved.resolve(role) == ("ollama", "local-model")

    def test_empty_values_are_ignored(self):
        """A half-filled settings table must not blank out a working setup."""
        base = ProviderConfig()
        resolved = resolve_provider_config(
            base, {"providers.thinking.model": "", "providers.thinking.provider": None}
        )
        assert resolved.thinking_model == base.thinking_model
        assert resolved.thinking_provider == base.thinking_provider

    def test_unrelated_keys_are_ignored(self):
        base = ProviderConfig()
        resolved = resolve_provider_config(base, {"hitl.queue_cap": 5})
        assert resolved is base

    def test_the_original_configuration_is_untouched(self):
        base = ProviderConfig()
        original = base.thinking_model
        resolve_provider_config(base, {"providers.thinking.model": "changed"})
        assert base.thinking_model == original

    def test_saved_settings_flow_through_the_store(self, ops_store):
        ops_store.settings.set("local", "providers.embedding.provider", "ollama")
        ops_store.settings.set("local", "providers.embedding.model", "nomic-embed-large")

        resolved = resolve_provider_config(
            ProviderConfig(), ops_store.settings.get_all("local")
        )
        assert resolved.resolve(ModelRole.EMBEDDING) == ("ollama", "nomic-embed-large")
