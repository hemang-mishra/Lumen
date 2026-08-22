"""Tests for structured logging."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest

from lumen.config import ObservabilityConfig
from lumen.observability.logging import (
    ConsoleFormatter,
    JsonFormatter,
    TraceIdFilter,
    configure_logging,
)
from lumen.observability.trace import bind_trace

logger = logging.getLogger("lumen.tests.logging")


def _record(**kwargs) -> logging.LogRecord:
    """Build a log record for formatting tests."""
    defaults = {
        "name": "lumen.test",
        "level": logging.INFO,
        "pathname": "/app/lumen/test.py",
        "lineno": 42,
        "msg": "hello",
        "args": (),
        "exc_info": None,
    }
    defaults.update(kwargs)
    return logging.LogRecord(**defaults)


class TestJsonFormatter:
    def test_output_is_one_json_object(self):
        payload = json.loads(JsonFormatter().format(_record()))
        assert payload["msg"] == "hello"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "lumen.test"
        assert payload["line"] == 42

    def test_timestamp_is_iso_and_utc(self):
        payload = json.loads(JsonFormatter().format(_record()))
        parsed = datetime.fromisoformat(payload["ts"])
        assert parsed.tzinfo is not None

    def test_message_arguments_are_substituted(self):
        payload = json.loads(JsonFormatter().format(_record(msg="got %s", args=("it",))))
        assert payload["msg"] == "got it"

    def test_extra_fields_are_merged_in(self):
        record = _record()
        record.stage = "STAGE_1"
        record.duration_ms = 1420
        payload = json.loads(JsonFormatter().format(record))
        assert payload["stage"] == "STAGE_1"
        assert payload["duration_ms"] == 1420

    def test_trace_id_is_absent_when_untraced(self):
        assert json.loads(JsonFormatter().format(_record()))["trace_id"] is None

    def test_exception_becomes_a_single_field(self):
        try:
            raise ValueError("something broke")
        except ValueError:
            import sys

            record = _record(exc_info=sys.exc_info())

        payload = json.loads(JsonFormatter().format(record))
        assert "something broke" in payload["exception"]
        # The whole record still has to fit on one line.
        assert "\n" not in JsonFormatter().format(record)

    def test_values_that_are_not_json_still_serialize(self):
        record = _record()
        record.when = datetime(2026, 6, 11, tzinfo=UTC)
        record.items = [1, "two", {"three": 3}]
        record.unusual = object()

        payload = json.loads(JsonFormatter().format(record))
        assert payload["when"].startswith("2026-06-11")
        assert payload["items"] == [1, "two", {"three": 3}]
        assert isinstance(payload["unusual"], str)


class TestTraceIdFilter:
    def test_adds_the_current_trace_id(self):
        record = _record()
        with bind_trace("trace-xyz"):
            TraceIdFilter().filter(record)
        assert record.trace_id == "trace-xyz"

    def test_adds_none_outside_a_run(self):
        record = _record()
        TraceIdFilter().filter(record)
        assert record.trace_id is None

    def test_never_drops_a_record(self):
        assert TraceIdFilter().filter(_record()) is True


class TestConsoleFormatter:
    def test_shows_a_short_trace_prefix(self):
        record = _record()
        record.trace_id = "abcdef1234567890"
        assert "[trace abcdef12]" in ConsoleFormatter().format(record)

    def test_omits_the_prefix_when_untraced(self):
        record = _record()
        record.trace_id = None
        assert "[trace" not in ConsoleFormatter().format(record)


class TestConfigureLogging:
    def test_writes_json_lines_to_the_configured_file(self, tmp_path):
        log_file = tmp_path / "lumen.jsonl"
        configure_logging(
            ObservabilityConfig(log_file=str(log_file), log_to_console=False)
        )
        try:
            with bind_trace("file-trace"):
                logger.info("written to file", extra={"stage": "STAGE_0"})
        finally:
            _reset_logging()

        lines = [json.loads(line) for line in log_file.read_text().splitlines()]
        entry = next(item for item in lines if item["msg"] == "written to file")
        assert entry["trace_id"] == "file-trace"
        assert entry["stage"] == "STAGE_0"

    def test_creates_the_log_directory(self, tmp_path):
        log_file = tmp_path / "nested" / "deeper" / "lumen.jsonl"
        configure_logging(
            ObservabilityConfig(log_file=str(log_file), log_to_console=False)
        )
        try:
            logger.info("made the directory")
        finally:
            _reset_logging()

        assert log_file.exists()

    def test_calling_it_twice_does_not_duplicate_lines(self, tmp_path):
        log_file = tmp_path / "lumen.jsonl"
        settings = ObservabilityConfig(log_file=str(log_file), log_to_console=False)
        configure_logging(settings)
        configure_logging(settings)
        try:
            logger.info("only once")
        finally:
            _reset_logging()

        lines = [
            line
            for line in log_file.read_text().splitlines()
            if "only once" in line
        ]
        assert len(lines) == 1

    def test_console_output_can_be_json(self, tmp_path, capsys):
        configure_logging(
            ObservabilityConfig(
                log_file=str(tmp_path / "l.jsonl"),
                log_to_console=True,
                console_json=True,
            )
        )
        try:
            logger.info("to the console")
        finally:
            _reset_logging()

        printed = capsys.readouterr().err
        assert json.loads(printed.strip().splitlines()[-1])["msg"] == "to the console"

    def test_sql_logging_is_quiet_by_default(self, tmp_path):
        configure_logging(
            ObservabilityConfig(log_file=str(tmp_path / "l.jsonl"), log_to_console=False)
        )
        try:
            assert logging.getLogger("sqlalchemy.engine").level == logging.WARNING
        finally:
            _reset_logging()

    def test_level_is_taken_from_configuration(self, tmp_path):
        configure_logging(
            ObservabilityConfig(
                log_file=str(tmp_path / "l.jsonl"),
                log_to_console=False,
                log_level="WARNING",
            )
        )
        try:
            assert logging.getLogger().level == logging.WARNING
        finally:
            _reset_logging()

    def test_logs_from_other_modules_also_get_trace_ids(self, tmp_path):
        """
        Modules written before any of this existed should be covered too,
        because the trace id is attached at the handler rather than by each
        logging call.
        """
        log_file = tmp_path / "lumen.jsonl"
        configure_logging(
            ObservabilityConfig(log_file=str(log_file), log_to_console=False)
        )
        try:
            with bind_trace("legacy-trace"):
                logging.getLogger("lumen.graph.kuzu_impl").warning("an older module")
        finally:
            _reset_logging()

        entry = next(
            json.loads(line)
            for line in log_file.read_text().splitlines()
            if "an older module" in line
        )
        assert entry["trace_id"] == "legacy-trace"


class TestTheSuiteStaysOutOfTheRealLogFile:
    """
    A test run must not append to the file a running Lumen writes.

    This is not tidiness. Scripted failures — a stand-in raising "the model
    went away" — land in the production log looking exactly like real ones,
    and the log is the first place anybody goes to find out why a real import
    failed.
    """

    def test_the_default_log_file_is_not_the_shipped_one(self):
        assert ObservabilityConfig().log_file != "./logs/lumen.jsonl"

    def test_starting_an_application_does_not_redirect_the_rest_of_the_run(
        self, tmp_path
    ):
        """
        configure_logging installs its handler on the root logger, and nothing
        detaches it at shutdown — so one lifespan used to capture every test
        that ran after it.
        """
        from fastapi.testclient import TestClient

        from lumen.api.main import create_app
        from lumen.config import AppConfig, GraphConfig, OperationalConfig

        app = create_app(
            AppConfig(
                graph=GraphConfig(db_root=str(tmp_path / "graph")),
                operational=OperationalConfig(db_url=f"sqlite:///{tmp_path / 'o.db'}"),
            )
        )

        with TestClient(app):
            # Only the handlers Lumen installed; pytest attaches its own.
            written_to = [
                handler.baseFilename
                for handler in logging.getLogger().handlers
                if getattr(handler, "_lumen_managed", False)
                and hasattr(handler, "baseFilename")
            ]

        assert written_to, "the application installed no file handler at all"
        assert all("lumen-test-logs" in path for path in written_to), written_to


def _reset_logging() -> None:
    """Detach the handlers a test installed, so later tests start clean."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_lumen_managed", False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(logging.WARNING)


@pytest.fixture(autouse=True)
def _clean_logging_state():
    """Make sure no test leaves handlers behind for the next one."""
    yield
    _reset_logging()
