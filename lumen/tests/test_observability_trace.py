"""Tests for trace ids."""

from __future__ import annotations

import threading
import uuid

import pytest

from lumen.observability.trace import (
    bind_trace,
    get_trace_id,
    new_trace_id,
    reset_trace_id,
    set_trace_id,
    span,
)


class TestTraceIdBasics:
    def test_new_trace_id_is_a_uuid(self):
        uuid.UUID(new_trace_id())

    def test_new_trace_ids_are_unique(self):
        assert len({new_trace_id() for _ in range(100)}) == 100

    def test_no_trace_id_outside_a_run(self):
        assert get_trace_id() is None

    def test_set_and_reset(self):
        token = set_trace_id("abc")
        assert get_trace_id() == "abc"
        reset_trace_id(token)
        assert get_trace_id() is None

    def test_empty_trace_id_is_refused(self):
        with pytest.raises(ValueError, match="must not be empty"):
            set_trace_id("")


class TestBindTrace:
    def test_generates_an_id_when_none_given(self):
        with bind_trace() as trace_id:
            assert get_trace_id() == trace_id
            uuid.UUID(trace_id)

    def test_uses_the_id_it_is_given(self):
        with bind_trace("fixed-id") as trace_id:
            assert trace_id == "fixed-id"
            assert get_trace_id() == "fixed-id"

    def test_clears_on_exit(self):
        with bind_trace("temporary"):
            pass
        assert get_trace_id() is None

    def test_clears_even_when_the_block_raises(self):
        with pytest.raises(RuntimeError):
            with bind_trace("failing"):
                raise RuntimeError("boom")
        assert get_trace_id() is None

    def test_nesting_restores_the_outer_id(self):
        with bind_trace("outer"):
            with bind_trace("inner"):
                assert get_trace_id() == "inner"
            assert get_trace_id() == "outer"
        assert get_trace_id() is None


class TestTraceIsolation:
    def test_threads_do_not_share_a_trace_id(self):
        """
        Two runs happening at once must not see each other's trace id. This is
        the property that makes the whole idea trustworthy.
        """
        seen: dict[str, str | None] = {}
        started = threading.Barrier(2)

        def worker(name: str) -> None:
            with bind_trace(f"trace-{name}"):
                started.wait(timeout=5)
                seen[name] = get_trace_id()

        threads = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        assert seen == {"a": "trace-a", "b": "trace-b"}

    def test_a_new_thread_starts_untraced(self):
        result: list[str | None] = []

        with bind_trace("main-thread"):
            thread = threading.Thread(target=lambda: result.append(get_trace_id()))
            thread.start()
            thread.join(timeout=5)

        assert result == [None]


class TestSpan:
    def test_logs_completion_with_a_duration(self, captured_logs):
        with span("stage_1"):
            pass

        entry = _find(captured_logs, "span")
        assert entry["span"] == "stage_1"
        assert entry["outcome"] == "COMPLETE"
        assert entry["duration_ms"] >= 0

    def test_carries_fields_given_up_front(self, captured_logs):
        with span("stage_1", stage="STAGE_1"):
            pass
        assert _find(captured_logs, "span")["stage"] == "STAGE_1"

    def test_carries_fields_added_while_running(self, captured_logs):
        with span("stage_1") as fields:
            fields["model_used"] = "gemini-2.5-pro"

        assert _find(captured_logs, "span")["model_used"] == "gemini-2.5-pro"

    def test_a_failure_is_logged_and_re_raised(self, captured_logs):
        with pytest.raises(ValueError, match="bad input"):
            with span("stage_1"):
                raise ValueError("bad input")

        entry = _find(captured_logs, "span")
        assert entry["outcome"] == "FAILED"
        assert entry["error_type"] == "ValueError"
        assert entry["level"] == "ERROR"
        assert "bad input" in entry["exception"]

    def test_carries_the_current_trace_id(self, captured_logs, bound_trace):
        with span("stage_1"):
            pass
        assert _find(captured_logs, "span")["trace_id"] == bound_trace


def _find(entries: list[dict], key: str) -> dict:
    """Return the last log entry carrying the given field."""
    matches = [entry for entry in entries if key in entry]
    assert matches, f"no log entry contained {key!r}"
    return matches[-1]
