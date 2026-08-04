"""
Trace ids — the thread that ties one run together.

Every journal entry that enters the pipeline is given a trace id. That id is
stored in a context variable, which means any code running underneath can read
it without being handed it as an argument. Logs, database rows, and pipeline
models all pick it up on their own.

Context variables are the right tool here because each thread gets its own
value and each async task inherits a copy when it starts. A background worker
processing entry A can never see the trace id of entry B.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

logger = logging.getLogger(__name__)

# Holds the trace id for whatever work the current thread or task is doing.
# None means we are outside of any traced run.
_trace_id: ContextVar[str | None] = ContextVar("lumen_trace_id", default=None)


def new_trace_id() -> str:
    """Create a fresh trace id."""
    return str(uuid.uuid4())


def get_trace_id() -> str | None:
    """
    Return the trace id for the current run, or None if there isn't one.

    Safe to call from anywhere. Code that runs both inside and outside a
    pipeline run does not need to guard the call.
    """
    return _trace_id.get()


def set_trace_id(trace_id: str) -> Token[str | None]:
    """
    Set the trace id for the current run.

    Returns a token that restores the previous value when passed to
    reset_trace_id. Prefer the bind_trace context manager, which does the
    restoring for you.
    """
    if not trace_id:
        raise ValueError("trace_id must not be empty")
    return _trace_id.set(trace_id)


def reset_trace_id(token: Token[str | None]) -> None:
    """Restore whatever trace id was in place before the matching set call."""
    _trace_id.reset(token)


@contextmanager
def bind_trace(trace_id: str | None = None) -> Generator[str]:
    """
    Run a block of code under a trace id, creating one if not given.

    Everything inside the block — logs, database writes, nested calls — is
    tagged with this id. The previous value is restored on the way out, even
    if the block raises, so nesting works correctly.

        with bind_trace() as trace_id:
            process_session(...)
    """
    resolved = trace_id or new_trace_id()
    token = set_trace_id(resolved)
    try:
        yield resolved
    finally:
        reset_trace_id(token)


@contextmanager
def span(name: str, **fields: Any) -> Generator[dict[str, Any]]:
    """
    Time a block of work and log one line describing how it went.

    The dictionary handed back can be filled in while the block runs; whatever
    is in it gets logged alongside the duration. This is how a pipeline stage
    reports which model it used and whether validation passed.

        with span("stage_1_extraction", stage="STAGE_1") as s:
            result = extract(episode)
            s["model_used"] = result.extraction_model

    A failing block still logs, at error level, with the duration it managed
    before failing. The exception is then re-raised untouched.
    """
    extra: dict[str, Any] = {"span": name, **fields}
    started = time.perf_counter()
    try:
        yield extra
    except Exception as exc:
        extra["duration_ms"] = _elapsed_ms(started)
        extra["outcome"] = "FAILED"
        extra["error_type"] = type(exc).__name__
        logger.error("span %s failed", name, extra=extra, exc_info=True)
        raise
    else:
        extra["duration_ms"] = _elapsed_ms(started)
        extra["outcome"] = "COMPLETE"
        logger.info("span %s complete", name, extra=extra)


def _elapsed_ms(started: float) -> int:
    """Milliseconds since the given perf_counter reading."""
    return int((time.perf_counter() - started) * 1000)


__all__ = [
    "new_trace_id",
    "get_trace_id",
    "set_trace_id",
    "reset_trace_id",
    "bind_trace",
    "span",
]
