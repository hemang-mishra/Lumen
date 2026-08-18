"""
Talk to Lumen: `python -m lumen.chat`.

Reads what you type, answers, and shows what it drew on underneath each
reply. Ends on an empty line, or on Ctrl-D.

This is the surface the compression templates and the voice are actually
judged on. A test can prove the right records reached the model; whether the
answer is any good is a judgement made by reading one.
"""

from __future__ import annotations

import sys

from lumen.chat.session import build_runner, converse
from lumen.config import AppConfig
from lumen.env import load_env
from lumen.observability.logging import configure_logging


def main() -> int:
    """Hold a conversation until the person stops typing."""
    load_env()
    settings = AppConfig()
    configure_logging(settings.observability)

    print("Talking to Lumen. Say something, or press enter on an empty line to stop.")
    with build_runner(settings) as runner:
        converse(runner, _typed_lines(), echo=False)
    print("\nbye.")
    return 0


def _typed_lines():
    """Whatever is typed, one line at a time, until an empty one."""
    while True:
        try:
            line = input("\nyou: ").strip()
        except EOFError:
            return
        if not line:
            return
        yield line


if __name__ == "__main__":
    sys.exit(main())
