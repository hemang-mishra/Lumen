"""
Structured logging.

Logs go out as one JSON object per line. That makes them machine-readable
without giving up the ability to just open the file and read it, and it means
a whole run can be pulled out later with a filter on trace id.

The trace id is added by a filter attached to the handlers rather than to
individual loggers. Handlers see every record in the process, so modules that
were written before any of this existed get their trace ids for free, with no
changes to their logging calls.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from lumen.observability.trace import get_trace_id

if TYPE_CHECKING:
    from lumen.config import ObservabilityConfig

# Attributes every LogRecord carries. Anything outside this set was supplied by
# the caller through extra={...}, so it belongs in the JSON output.
_STANDARD_RECORD_FIELDS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "taskName",
        "trace_id",
    }
)

# Marks the handlers this module installs, so re-configuring replaces its own
# handlers without disturbing any that a test or host application added.
_LUMEN_HANDLER_FLAG = "_lumen_managed"


class TraceIdFilter(logging.Filter):
    """
    Stamps the current trace id onto every log record passing through.

    Records created outside a traced run get None, which is honest — it means
    the line came from startup or a one-off script rather than a pipeline run.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id()
        return True


class JsonFormatter(logging.Formatter):
    """
    Renders a log record as a single line of JSON.

    Anything passed through extra={...} is merged into the top level of the
    object, so a caller can attach stage names, durations, or model names and
    have them come out as real fields instead of being buried in the message.

    Exceptions become a single "exception" string field. A multi-line traceback
    would otherwise break the one-event-per-line rule that makes the file
    parseable.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": getattr(record, "trace_id", None),
            "module": record.module,
            "line": record.lineno,
        }

        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS and not key.startswith("_"):
                payload[key] = _as_jsonable(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """
    Human-readable console output for development.

    Shows a short prefix of the trace id — enough to tell two runs apart at a
    glance without eating half the terminal width.
    """

    def __init__(self) -> None:
        super().__init__(fmt="%(asctime)s %(levelname)-7s %(name)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        trace_id = getattr(record, "trace_id", None)
        return f"{base}  [trace {trace_id[:8]}]" if trace_id else base


def _as_jsonable(value: Any) -> Any:
    """Convert a value into something json.dumps can handle."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _as_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_as_jsonable(v) for v in value]
    return str(value)


def configure_logging(config: "ObservabilityConfig | None" = None) -> None:
    """
    Set up logging for the whole process. Call this once at startup.

    Safe to call again — the handlers installed by a previous call are removed
    first, so repeated calls (which happen constantly under pytest) do not
    produce duplicate log lines.
    """
    # Imported here rather than at the top of the file: logging sits beneath
    # everything else, and configuration reaches back up to the schemas. Doing
    # this at call time keeps that from becoming an import cycle.
    from lumen.config import ObservabilityConfig

    settings = config or ObservabilityConfig()
    root = logging.getLogger()

    _remove_managed_handlers(root)
    root.setLevel(settings.log_level.upper())

    trace_filter = TraceIdFilter()

    if settings.log_file:
        root.addHandler(_build_file_handler(settings, trace_filter))

    if settings.log_to_console:
        console = logging.StreamHandler()
        console.setFormatter(JsonFormatter() if settings.console_json else ConsoleFormatter())
        console.addFilter(trace_filter)
        _mark_managed(console)
        root.addHandler(console)

    # SQLAlchemy logs every statement at INFO, which drowns out everything
    # else. The echo_sql setting is the intended way to turn that back on.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def _build_file_handler(
    settings: "ObservabilityConfig", trace_filter: logging.Filter
) -> logging.Handler:
    """Create the rotating JSON file handler, making its directory if needed."""
    log_dir = os.path.dirname(os.path.abspath(settings.log_file))
    os.makedirs(log_dir, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        settings.log_file,
        maxBytes=settings.max_bytes,
        backupCount=settings.backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(JsonFormatter())
    handler.addFilter(trace_filter)
    _mark_managed(handler)
    return handler


def _mark_managed(handler: logging.Handler) -> None:
    """Tag a handler as ours so a later configure call can clean it up."""
    setattr(handler, _LUMEN_HANDLER_FLAG, True)


def _remove_managed_handlers(logger_obj: logging.Logger) -> None:
    """Detach and close handlers installed by a previous configure call."""
    for handler in list(logger_obj.handlers):
        if getattr(handler, _LUMEN_HANDLER_FLAG, False):
            logger_obj.removeHandler(handler)
            handler.close()


__all__ = [
    "TraceIdFilter",
    "JsonFormatter",
    "ConsoleFormatter",
    "configure_logging",
]
