"""
Writing down the exact shape of this API, so the browser can be typed from it.

The front end does not hand-write the types of anything this service returns.
It generates them from the description produced here, which means a field
renamed in Python becomes a compile error in TypeScript instead of an
`undefined` somebody finds three screens later.

Two things about how this is produced are deliberate.

**The description is a function of the code and nothing else.** Every
`LUMEN_*` variable is cleared before the settings are built, because the
application mounts some routes only when a setting says so. Left alone, the
description would depend on whichever developer ran the command and on what
their .env file happened to say that day.

**Sockets are described too.** The generated description covers requests and
responses only — nothing in it can express a conversation over a socket. Since
a browser has to match those message names exactly, they are collected from the
modules that define them and written in alongside, under a name of our own.
"""

from __future__ import annotations

import argparse
import json
import os
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from lumen.config import AppConfig

# Where the description lives, and what generates from it. Relative to the
# repository root so the command works from anywhere inside it.
DEFAULT_OUTPUT = Path("frontend/openapi.json")

# The extra section holding what a socket can send. Prefixed, because the
# format reserves plain names for itself and lets anybody add their own this
# way.
SOCKET_SECTION = "x-lumen-socket-events"

# Every setting this service reads starts with this.
_PREFIX = "LUMEN_"

# What to say to somebody looking at a failed comparison. It is the whole fix.
REGENERATE = "uv run python -m lumen.api.schema_dump"


def canonical_config() -> AppConfig:
    """
    The settings the published description is built from.

    Not the settings of any real deployment — the settings of the code
    itself. Uploads are switched on because a service with them switched off
    has fewer routes, and the front end needs the types of all of them.
    """
    with _only_these_settings(LUMEN_ENABLE_INGEST="true"):
        return AppConfig()


def build_schema() -> dict[str, Any]:
    """
    The full description of this API, sockets included.

    Building the application is enough: it registers every route without
    opening a single database, because the stores are opened when it starts
    serving rather than when it is put together.
    """
    from lumen.api.main import create_app

    app = create_app(canonical_config())
    schema = app.openapi()
    schema[SOCKET_SECTION] = socket_events()
    return schema


def socket_events() -> dict[str, list[str]]:
    """
    What each socket can send, keyed by the address it is reached at.

    Collected from the modules that own the messages rather than listed
    here, so this cannot quietly fall behind them.
    """
    from lumen.api.events import SCHEDULER_EVENTS
    from lumen.api.routes.chat import socket_frame_kinds
    from lumen.ingest.worker import WORKER_EVENTS

    watching = sorted({*WORKER_EVENTS, *SCHEDULER_EVENTS})
    return {"/chat/ws": list(socket_frame_kinds()), "/events/ws": watching}


def write(destination: Path) -> Path:
    """Write the description out, formatted so a diff of it is readable."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(_as_text(build_schema()), encoding="utf-8")
    return destination


def differs_from(destination: Path) -> bool:
    """
    Whether what is on disk still describes this code.

    Compared as parsed data rather than as text, so that formatting or the
    order keys happen to come out in can never fail a check on its own.
    """
    if not destination.exists():
        return True
    try:
        stored = json.loads(destination.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return True
    return stored != build_schema()


def _as_text(schema: dict[str, Any]) -> str:
    """One stable rendering, so two machines produce the same file."""
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


@contextmanager
def _only_these_settings(**overrides: str) -> Iterator[None]:
    """
    Run something as if the only settings in the world were these.

    Every Lumen variable is taken away first and put back afterwards, because
    this runs inside a test suite as often as it runs from a terminal and must
    leave the process exactly as it found it.
    """
    saved = {
        key: os.environ.pop(key)
        for key in list(os.environ)
        if key.startswith(_PREFIX)
    }
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key in list(os.environ):
            if key.startswith(_PREFIX):
                del os.environ[key]
        os.environ.update(saved)


def main(argv: list[str] | None = None) -> int:
    """
    Write the description, or check the one on disk still matches.

    Returns a non-zero code when a check fails, so it can be used anywhere
    that treats an exit code as a verdict.
    """
    parser = argparse.ArgumentParser(description="Describe this API for the front end.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT, help="where to write")
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; fail if what is on disk is out of date",
    )
    args = parser.parse_args(argv)

    if args.check:
        if differs_from(args.out):
            print(f"{args.out} is out of date. Regenerate it with:\n  {REGENERATE}")
            return 1
        print(f"{args.out} is up to date.")
        return 0

    print(f"wrote {write(args.out)}")
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised as a command
    raise SystemExit(main())
