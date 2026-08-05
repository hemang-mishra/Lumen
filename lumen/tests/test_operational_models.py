"""Tests for the table definitions and the database settings behind them."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from lumen.config import OperationalConfig
from lumen.operational import models
from lumen.operational.engine import (
    create_ops_engine,
    create_session_factory,
    session_scope,
)
from lumen.operational.engine import _redact

TODAY = date(2026, 6, 11)
NOW = datetime(2026, 6, 11, 21, 0, tzinfo=UTC)


class TestSchemaShape:
    def test_every_expected_table_exists(self, ops_engine):
        tables = set(inspect(ops_engine).get_table_names())
        assert {
            "session_buffers",
            "buffer_messages",
            "pipeline_jobs",
            "pipeline_stage_runs",
            "pipeline_write_log",
            "hitl_queue",
            "user_settings",
            "data_erasure_audit",
        } <= tables

    def test_erasure_table_stores_no_readable_identifier(self, ops_engine):
        """
        A record of a deletion must not preserve what was deleted, nor who it
        belonged to in readable form.
        """
        columns = {c["name"] for c in inspect(ops_engine).get_columns("data_erasure_audit")}
        assert "user_id_hash" in columns
        assert "user_id" not in columns
        # No column that could hold journal text.
        assert not (columns & {"content", "summary", "notes", "raw_text"})

    def test_review_queue_is_indexed_for_its_sort_order(self, ops_engine):
        indexes = inspect(ops_engine).get_indexes("hitl_queue")
        priority = next(i for i in indexes if i["name"] == "ix_hitl_priority")
        assert priority["column_names"] == [
            "user_id", "status", "priority_rank", "signal_rank", "created_at",
        ]


class TestSqliteSettings:
    def test_foreign_keys_are_enforced(self, ops_engine):
        """
        SQLite ignores foreign keys unless told not to. Without this, every
        relationship in the schema would be decoration.
        """
        with ops_engine.connect() as connection:
            assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1

    def test_write_ahead_logging_is_on(self, ops_engine):
        with ops_engine.connect() as connection:
            mode = connection.execute(text("PRAGMA journal_mode")).scalar()
        assert mode.lower() == "wal"

    def test_a_message_cannot_point_at_a_missing_buffer(self, ops_engine):
        factory = create_session_factory(ops_engine)
        with pytest.raises(IntegrityError):
            with session_scope(factory) as db:
                db.add(
                    models.BufferMessage(
                        message_id="orphan",
                        session_id="no-such-buffer",
                        seq=0,
                        role="USER",
                        content="hello",
                        timestamp=NOW,
                        event_date=TODAY,
                    )
                )

    def test_deleting_a_buffer_removes_its_messages(self, ops_engine):
        factory = create_session_factory(ops_engine)
        with session_scope(factory) as db:
            db.add(
                models.SessionBuffer(
                    session_id="sb_1", user_id="local", event_date=TODAY,
                    session_label="A", last_activity_at=NOW,
                )
            )
            db.flush()
            db.add(
                models.BufferMessage(
                    message_id="m1", session_id="sb_1", seq=0, role="USER",
                    content="hi", timestamp=NOW, event_date=TODAY,
                )
            )

        with session_scope(factory) as db:
            db.delete(db.get(models.SessionBuffer, "sb_1"))

        with session_scope(factory) as db:
            assert db.scalars(select(models.BufferMessage)).all() == []


class TestConstraints:
    def test_one_buffer_per_user_date_and_label(self, ops_engine):
        factory = create_session_factory(ops_engine)
        with session_scope(factory) as db:
            db.add(
                models.SessionBuffer(
                    session_id="sb_1", user_id="local", event_date=TODAY,
                    session_label="A", last_activity_at=NOW,
                )
            )

        with pytest.raises(IntegrityError):
            with session_scope(factory) as db:
                db.add(
                    models.SessionBuffer(
                        session_id="sb_2", user_id="local", event_date=TODAY,
                        session_label="A", last_activity_at=NOW,
                    )
                )

    def test_the_same_day_can_hold_differently_labelled_buffers(self, ops_engine):
        factory = create_session_factory(ops_engine)
        with session_scope(factory) as db:
            db.add_all(
                [
                    models.SessionBuffer(
                        session_id="sb_a", user_id="local", event_date=TODAY,
                        session_label="A", last_activity_at=NOW,
                    ),
                    models.SessionBuffer(
                        session_id="sb_b", user_id="local", event_date=TODAY,
                        session_label="B", last_activity_at=NOW,
                    ),
                ]
            )

        with session_scope(factory) as db:
            assert len(db.scalars(select(models.SessionBuffer)).all()) == 2

    def test_one_review_item_per_decision(self, ops_engine):
        factory = create_session_factory(ops_engine)
        with session_scope(factory) as db:
            db.add(
                models.HitlQueueItem(
                    id="h1", user_id="local", audit_node_id="d_2026_06_11_001",
                    entry_type="AMBIGUOUS_TIE", priority_rank=1, signal_rank=2,
                )
            )

        with pytest.raises(IntegrityError):
            with session_scope(factory) as db:
                db.add(
                    models.HitlQueueItem(
                        id="h2", user_id="local", audit_node_id="d_2026_06_11_001",
                        entry_type="BELOW_THRESHOLD", priority_rank=2, signal_rank=1,
                    )
                )


class TestSessionScope:
    def test_a_failing_block_leaves_nothing_behind(self, ops_engine):
        factory = create_session_factory(ops_engine)
        with pytest.raises(RuntimeError):
            with session_scope(factory) as db:
                db.add(
                    models.SessionBuffer(
                        session_id="sb_rollback", user_id="local", event_date=TODAY,
                        session_label="X", last_activity_at=NOW,
                    )
                )
                db.flush()
                raise RuntimeError("changed my mind")

        with session_scope(factory) as db:
            assert db.get(models.SessionBuffer, "sb_rollback") is None


class TestEngineCreation:
    def test_in_memory_databases_survive_between_connections(self):
        """
        An in-memory database normally disappears when its connection closes.
        Tests would then find an empty database, so the connection is shared.
        """
        engine = create_ops_engine(OperationalConfig(db_url="sqlite:///:memory:"))
        models.Base.metadata.create_all(engine)
        with engine.connect() as connection:
            assert "session_buffers" in inspect(engine).get_table_names()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1 FROM session_buffers"))
        engine.dispose()

    def test_passwords_are_hidden_before_reaching_a_log(self):
        redacted = _redact("postgresql://lumen:supersecret@db.example.com/lumen")
        assert "supersecret" not in redacted
        assert "lumen:***@db.example.com" in redacted

    def test_urls_without_credentials_are_untouched(self):
        assert _redact("sqlite:///./ops.db") == "sqlite:///./ops.db"

    def test_a_host_with_no_credentials_is_untouched(self):
        assert _redact("postgresql://@db.example.com/lumen") == (
            "postgresql://@db.example.com/lumen"
        )
