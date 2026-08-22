"""Tests for the store itself — how it is built, wired, and shut down."""

from __future__ import annotations

import json
import logging

from sqlalchemy import inspect

from lumen.config import AppConfig, ObservabilityConfig, OperationalConfig
from lumen.observability.logging import JsonFormatter
from lumen.operational.repositories import (
    DataErasureAuditRepository,
    HitlQueueRepository,
    OperationalStore,
    PipelineJobRepository,
    SessionBufferRepository,
    UserSettingsRepository,
)
from lumen.operational.sqlalchemy_impl import (
    SQLAlchemyOperationalStore,
    build_operational_store,
)


class TestStoreConstruction:
    def test_it_satisfies_the_interface_it_claims(self, ops_store):
        """
        Callers depend on the protocol, not on this class. If the two drift
        apart, a stand-in that satisfies the protocol would break at runtime.
        """
        assert isinstance(ops_store, OperationalStore)

    def test_each_repository_satisfies_its_interface(self, ops_store):
        assert isinstance(ops_store.buffers, SessionBufferRepository)
        assert isinstance(ops_store.jobs, PipelineJobRepository)
        assert isinstance(ops_store.hitl, HitlQueueRepository)
        assert isinstance(ops_store.settings, UserSettingsRepository)
        assert isinstance(ops_store.erasure, DataErasureAuditRepository)

    def test_it_can_build_its_own_connection(self, tmp_path):
        config = OperationalConfig(db_url=f"sqlite:///{tmp_path / 'own.db'}")
        with SQLAlchemyOperationalStore(config) as store:
            store.init_schema()
            assert "session_buffers" in inspect(store.engine).get_table_names()

    def test_creating_tables_directly_works_for_throwaway_databases(self, tmp_path):
        config = OperationalConfig(db_url=f"sqlite:///{tmp_path / 'quick.db'}")
        store = SQLAlchemyOperationalStore(config)
        try:
            store.init_schema()
            store.init_schema()  # doing it twice must be harmless
            assert "hitl_queue" in inspect(store.engine).get_table_names()
        finally:
            store.close()

    def test_it_is_wired_from_application_configuration(self, tmp_path):
        config = AppConfig(
            operational=OperationalConfig(db_url=f"sqlite:///{tmp_path / 'app.db'}")
        )
        store = build_operational_store(config)
        try:
            assert str(store.engine.url).endswith("app.db")
        finally:
            store.close()

    def test_it_works_as_a_context_manager(self, tmp_path):
        config = OperationalConfig(db_url=f"sqlite:///{tmp_path / 'ctx.db'}")
        with SQLAlchemyOperationalStore(config) as store:
            store.init_schema()
            assert store.engine is not None

    def test_a_borrowed_connection_is_left_open(self, ops_config, ops_engine):
        """
        A store that did not open the connection must not close it, or the
        fixture that owns it would be left with a dead engine.
        """
        store = SQLAlchemyOperationalStore(ops_config, engine=ops_engine)
        store.close()
        with ops_engine.connect() as connection:
            assert connection is not None


class TestNonSqliteDatabases:
    def test_the_sqlite_settings_are_skipped_elsewhere(self):
        """
        The pragma hook runs for every connection. On any other database it
        has to do nothing rather than fail.
        """
        from lumen.operational.engine import _apply_sqlite_pragmas

        class FakePostgresConnection:
            def cursor(self):  # pragma: no cover - must never be reached
                raise AssertionError("SQLite settings were applied to another database")

        _apply_sqlite_pragmas(FakePostgresConnection(), None)


class TestConfiguration:
    def test_the_operational_defaults_are_usable(self):
        """
        The decay window and queue cap are deployment choices, so the values
        themselves are not pinned here — only that they are sane. What matters
        is that nothing ships with a zero or negative window, which would
        process every conversation the moment it started.
        """
        config = OperationalConfig()
        assert config.session_decay_minutes > 0
        assert config.hitl_queue_cap > 0
        assert config.db_url.startswith("sqlite")

    def test_the_decay_window_can_be_set_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("LUMEN_SESSION_DECAY_MINUTES", "45")
        import importlib

        from lumen import config as config_module

        importlib.reload(config_module)
        try:
            assert config_module.OperationalConfig().session_decay_minutes == 45
        finally:
            monkeypatch.delenv("LUMEN_SESSION_DECAY_MINUTES")
            importlib.reload(config_module)

    def test_the_queue_cap_can_be_set_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("LUMEN_HITL_QUEUE_CAP", "12")
        import importlib

        from lumen import config as config_module

        importlib.reload(config_module)
        try:
            assert config_module.OperationalConfig().hitl_queue_cap == 12
        finally:
            monkeypatch.delenv("LUMEN_HITL_QUEUE_CAP")
            importlib.reload(config_module)

    def test_logging_defaults_are_sensible(self):
        config = ObservabilityConfig()
        assert config.log_level == "INFO"
        assert config.log_file.endswith(".jsonl")

    def test_the_application_config_carries_the_new_sections(self):
        config = AppConfig()
        assert isinstance(config.operational, OperationalConfig)
        assert isinstance(config.observability, ObservabilityConfig)
        assert config.default_user_id


class TestLogFormattingEdges:
    def test_a_stack_trace_is_kept_on_one_line(self):
        record = logging.LogRecord(
            name="lumen.test",
            level=logging.WARNING,
            pathname="/app/lumen/test.py",
            lineno=1,
            msg="with a stack",
            args=(),
            exc_info=None,
        )
        record.stack_info = 'Stack (most recent call last):\n  File "x", line 1'

        formatted = JsonFormatter().format(record)
        assert "\n" not in formatted
        assert "Stack (most recent call last)" in json.loads(formatted)["stack"]
