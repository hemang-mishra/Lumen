"""
Running Lumen.

    uv run python -m lumen

One worker, always, and not as a default that can be raised. Every store
Lumen uses is embedded — Kuzu takes a file lock on its database directory,
Qdrant runs out of a local folder, and the operational database is SQLite —
so a second worker process is not twice the capacity. It is a second process
trying to open files the first one holds, which fails at startup if you are
lucky and corrupts assumptions if you are not.

That is a real ceiling rather than a setting: scaling past one process means
moving Kuzu and Qdrant to server mode and taking the pipeline out of the web
process entirely, which is its own piece of work.
"""

from __future__ import annotations

import argparse
import logging

from lumen.env import load_env

logger = logging.getLogger(__name__)


def main() -> None:
    """Read the configuration, then serve."""
    parser = argparse.ArgumentParser(
        prog="lumen",
        description="Serve the Lumen API and its test pages.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on")
    parser.add_argument("--port", type=int, default=8000, help="port to listen on")
    parser.add_argument(
        "--reload",
        action="store_true",
        help=(
            "restart when the code changes. For working on the pages — a "
            "reload part way through an import abandons that run."
        ),
    )
    args = parser.parse_args()

    # Before anything builds a configuration object, because every setting is
    # read once, at construction.
    load_env()

    import uvicorn

    print(f"Lumen is at http://{args.host}:{args.port}/ui/")
    uvicorn.run(
        "lumen.api.main:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        # Not configurable. See the note at the top of this file.
        workers=1,
    )


if __name__ == "__main__":
    main()
