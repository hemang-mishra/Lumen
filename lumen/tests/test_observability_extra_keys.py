"""
A guard against a log line that raises instead of logging.

`logging` refuses to build a record whose `extra` names one of the fields a
LogRecord already carries. It does not warn — it raises KeyError, from inside
the logging call, at the moment the line would have been written.

That makes it a nasty class of bug for two reasons. It fires in production
and not in tests, because the test suite leaves logging at WARNING and
`logger.info(...)` returns before it ever builds a record. And it takes down
whatever was doing the logging, so a line added purely for observability
becomes the thing that breaks the request.

One real instance of this shipped and was caught by running the service by
hand: `extra={"filename": ...}` on the import path, which would have failed
every single upload. Hence this file, which reads the source rather than
running it, and so covers every log line in the codebase including the ones
no test happens to reach.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]

# Everything a LogRecord already carries. Taken from a real record rather
# than typed out, so it cannot drift from whatever the running Python
# actually reserves. "message" and "asctime" are rejected too, by name,
# because they are built during formatting rather than at creation.
RESERVED = set(
    vars(logging.LogRecord("n", logging.INFO, "p", 1, "m", None, None))
) | {"message", "asctime"}

# Not a crash, but not a value either. Lumen's own filter stamps the current
# trace id onto every record as it passes, so a caller who puts one in
# `extra` has it quietly replaced by the ambient one — and the line then
# claims to be about a run it is not about. Two of these had already shipped.
FILTER_OWNED = {"trace_id"}


def extra_keys_in(source: str) -> list[tuple[int, str]]:
    """
    Every literal key handed to a logging call through `extra=`.

    Reads the syntax tree rather than the text, so a key split across lines
    or sitting inside a nested call is still found. Keys built at runtime
    are invisible here, which is the accepted limit: every one in this
    codebase is written out in full.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "extra" or not isinstance(keyword.value, ast.Dict):
                continue
            for key in keyword.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    found.append((getattr(key, "lineno", 0), key.value))
    return found


@pytest.mark.parametrize(
    "path",
    sorted(PACKAGE.rglob("*.py")),
    ids=lambda path: str(path.relative_to(PACKAGE)),
)
def test_no_log_line_names_a_field_a_record_already_has(path):
    clashes = [
        f"{path.relative_to(PACKAGE)}:{line} uses extra={{{key!r}: ...}}"
        for line, key in extra_keys_in(path.read_text(encoding="utf-8"))
        if key in RESERVED
    ]

    assert clashes == [], (
        "these log lines would raise KeyError instead of logging:\n  "
        + "\n  ".join(clashes)
    )


@pytest.mark.parametrize(
    "path",
    sorted(PACKAGE.rglob("*.py")),
    ids=lambda path: str(path.relative_to(PACKAGE)),
)
def test_no_log_line_tries_to_set_the_trace_id_itself(path):
    clashes = [
        f"{path.relative_to(PACKAGE)}:{line} uses extra={{{key!r}: ...}}"
        for line, key in extra_keys_in(path.read_text(encoding="utf-8"))
        if key in FILTER_OWNED
    ]

    assert clashes == [], (
        "the trace id filter overwrites these, so the line would be stamped "
        "with the ambient run rather than the one it names; give the field "
        "another name:\n  " + "\n  ".join(clashes)
    )


class TestTheGuardItself:
    def test_it_finds_a_clash(self):
        # The guard is only worth having if it would have caught the real one.
        source = 'logger.info("read an export", extra={"filename": name})'

        assert extra_keys_in(source) == [(1, "filename")]

    def test_it_leaves_ordinary_keys_alone(self):
        source = 'logger.info("staged", extra={"batch_id": b, "source_file": f})'

        assert {key for _, key in extra_keys_in(source)}.isdisjoint(RESERVED)

    def test_the_trace_id_is_not_a_crash_only_a_lie(self):
        # Worth keeping the two apart: one would raise, the other logs
        # happily with the wrong value in it.
        assert "trace_id" not in RESERVED

    def test_it_reads_through_nesting(self):
        source = "log(x, extra={\n    'module': 1,\n})"

        assert extra_keys_in(source) == [(2, "module")]
