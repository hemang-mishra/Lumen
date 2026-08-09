"""Tests for user settings and how they combine with configuration."""

from __future__ import annotations

import pytest

from lumen.operational.repositories import UnknownSettingKeyError
from lumen.operational.sqlalchemy_impl import KNOWN_SETTING_KEYS
from lumen.schemas.enums import ModelRole


class TestReadAndWrite:
    def test_a_setting_survives_the_round_trip(self, ops_store):
        ops_store.settings.set("local", "logging.level", "DEBUG")
        assert ops_store.settings.get("local", "logging.level") == "DEBUG"

    def test_an_unset_setting_reads_back_as_nothing(self, ops_store):
        assert ops_store.settings.get("local", "logging.level") is None

    def test_a_setting_can_be_changed(self, ops_store):
        ops_store.settings.set("local", "logging.level", "DEBUG")
        ops_store.settings.set("local", "logging.level", "WARNING")
        assert ops_store.settings.get("local", "logging.level") == "WARNING"

    def test_numbers_keep_their_type(self, ops_store):
        ops_store.settings.set("local", "pipeline.session_decay_minutes", 30)
        value = ops_store.settings.get("local", "pipeline.session_decay_minutes")
        assert value == 30
        assert isinstance(value, int)

    def test_users_do_not_see_each_other_s_settings(self, ops_store):
        ops_store.settings.set("alice", "logging.level", "DEBUG")
        assert ops_store.settings.get("bob", "logging.level") is None

    def test_everything_a_user_set_can_be_read_at_once(self, ops_store):
        ops_store.settings.set("local", "logging.level", "DEBUG")
        ops_store.settings.set("local", "hitl.queue_cap", 10)

        assert ops_store.settings.get_all("local") == {
            "logging.level": "DEBUG",
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
            ops_store.settings.set("local", "piepline.session_decay_minutes", "oops")

    def test_the_refusal_lists_what_is_valid(self, ops_store):
        with pytest.raises(UnknownSettingKeyError, match="hitl.queue_cap"):
            ops_store.settings.set("local", "made.up.key", "x")


class TestProviderSelectionIsNotASetting:
    """
    Which model backs a role is a deployment property the maintainer sets in the
    environment, read once at startup. It is not a user preference, so there is
    no settings key for it and no way to change it at runtime.
    """

    def test_no_role_has_a_settings_key(self):
        for role in ModelRole:
            assert f"providers.{role.value.lower()}.provider" not in KNOWN_SETTING_KEYS
            assert f"providers.{role.value.lower()}.model" not in KNOWN_SETTING_KEYS

    def test_no_known_key_mentions_providers_at_all(self):
        assert not [key for key in KNOWN_SETTING_KEYS if key.startswith("providers.")]

    def test_writing_a_provider_setting_is_refused(self, ops_store):
        with pytest.raises(UnknownSettingKeyError):
            ops_store.settings.set("local", "providers.thinking.model", "llama3.3:70b")

    def test_no_key_could_carry_a_credential(self):
        """Credentials come from the environment and are never persisted."""
        for key in KNOWN_SETTING_KEYS:
            assert "key" not in key
            assert "secret" not in key
            assert "token" not in key
