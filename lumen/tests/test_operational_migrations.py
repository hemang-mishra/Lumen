"""
Tests for the migrations.

Migrations are the only way the schema is built, so they need to work in both
directions and never fall behind the table definitions in code.
"""

from __future__ import annotations

from sqlalchemy import inspect

from lumen.operational.engine import create_ops_engine
from lumen.operational.migrator import (
    build_alembic_config,
    detect_schema_drift,
    downgrade_to_base,
    upgrade_to_head,
)

EXPECTED_TABLES = {
    "session_buffers",
    "buffer_messages",
    "pipeline_jobs",
    "pipeline_stage_runs",
    "pipeline_write_log",
    "hitl_queue",
    "user_settings",
    "data_erasure_audit",
    "imports",
}


class TestMigrations:
    def test_upgrading_creates_every_table(self, ops_config):
        engine = create_ops_engine(ops_config)
        try:
            upgrade_to_head(engine)
            assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())
        finally:
            engine.dispose()

    def test_downgrading_removes_every_table(self, ops_config):
        engine = create_ops_engine(ops_config)
        try:
            upgrade_to_head(engine)
            downgrade_to_base(engine)
            remaining = set(inspect(engine).get_table_names())
            assert not (EXPECTED_TABLES & remaining)
        finally:
            engine.dispose()

    def test_upgrading_twice_changes_nothing(self, ops_config):
        engine = create_ops_engine(ops_config)
        try:
            upgrade_to_head(engine)
            before = set(inspect(engine).get_table_names())
            upgrade_to_head(engine)
            assert set(inspect(engine).get_table_names()) == before
        finally:
            engine.dispose()

    def test_the_version_is_recorded(self, ops_engine):
        assert "alembic_version" in inspect(ops_engine).get_table_names()


class TestSchemaDrift:
    def test_the_migration_matches_the_table_definitions(self, ops_engine):
        """
        Catches a model that was changed without a matching migration. Without
        this check the two would only be found to disagree much later, against
        a database that already holds real data.
        """
        drift = detect_schema_drift(ops_engine)
        assert drift == [], f"models and migrations disagree: {drift}"


class TestAlembicConfig:
    def test_the_script_location_is_resolved(self):
        location = build_alembic_config().get_main_option("script_location")
        assert location.endswith("lumen/operational/migrations")

    def test_an_engine_supplies_the_connection_url(self, ops_engine):
        config = build_alembic_config(ops_engine)
        assert config.get_main_option("sqlalchemy.url") == str(ops_engine.url)
