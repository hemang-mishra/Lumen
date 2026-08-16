"""
Reading a .env file before anything asks for configuration.

Every setting in Lumen is read from the environment when a config object is
built, which is deliberate and works perfectly for a process someone started
by hand with the variables already set. It does nothing for the ordinary
case: a checkout, a .env file next to it, and `uvicorn`.

So this exists, and the only thing that matters about it is *when* it runs.
A .env loaded after AppConfig() has been constructed changes nothing, because
the values were already read. Every entry point calls this first.

Loading is best-effort and never fatal. A missing file is the normal state
for a deployment that sets its variables some other way.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# The project root, two levels up from this file.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path | str | None = None, *, override: bool = False) -> bool:
    """
    Read a .env file into the environment.

    Args:
        path: Which file to read. Defaults to `.env` beside the project.
        override: Whether a value in the file beats one already in the
            environment. Off by default, because a variable somebody set on
            the command line is a deliberate act and a file is a default.

    Returns:
        Whether a file was actually read.
    """
    target = Path(path) if path else PROJECT_ROOT / ".env"

    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - python-dotenv is a dependency
        logger.debug("python-dotenv is not installed, so .env is not read")
        return False

    if not target.exists():
        logger.debug("no .env file at %s", target)
        return False

    load_dotenv(target, override=override)
    logger.info("read configuration from %s", target)
    return True


__all__ = ["load_env", "PROJECT_ROOT"]
