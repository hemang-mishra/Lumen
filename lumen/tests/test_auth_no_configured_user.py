"""
A test that reads the source rather than running it.

`AppConfig.default_user_id` was called `user_id` until identity became a real
thing, and the rename is the enforcement rather than a tidy-up. A route that
reads configuration to find out who is asking serves whoever the process was
started as — which, in a deployment with two people, is a data leak that looks
exactly like working software and produces no error anywhere.

There is no way to test that by calling something. The only reliable check is
that the web layer does not contain the reader at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

API = Path(__file__).resolve().parents[1] / "api"

# The only two files that may read the configured default, and why.
#
# deps.py is the seam. It is the one place that turns "there is no sign-in
# here" into an identity, which is what lets the existing single-user
# deployment and the whole test suite carry on unchanged. Every other part of
# the web layer receives the answer rather than working it out.
#
# main.py starts the jobs that run on a clock. Those have no request behind
# them, and the review sweep still has to be run for somebody. When there is
# more than one person it will iterate over them; today it is the one.
ALLOWED = {"deps.py", "main.py"}


def python_files(root: Path) -> list[Path]:
    """Every source file under a directory."""
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in str(path))


class TestTheWebLayerDoesNotKnowWhoTheUserIs:
    def test_no_route_reads_the_configured_default(self):
        offenders = [
            path.relative_to(API).as_posix()
            for path in python_files(API / "routes")
            if "default_user_id" in path.read_text()
        ]

        assert offenders == []

    def test_nor_does_anything_else_in_the_web_layer(self):
        offenders = [
            path.relative_to(API).as_posix()
            for path in python_files(API)
            if path.name not in ALLOWED and "default_user_id" in path.read_text()
        ]

        assert offenders == []

    def test_the_old_name_is_gone_everywhere(self):
        # Left behind, it would be a second answer to "who is this" that
        # nothing checks.
        offenders = []
        for path in python_files(API.parent):
            if "tests" in path.parts:
                continue
            text = path.read_text()
            if "config.user_id" in text or "settings.user_id" in text:
                offenders.append(path.as_posix())

        assert offenders == []

    def test_the_conversation_surface_has_no_user_of_its_own(self):
        # The defect this goal exists to close. The chat routes used to write
        # under a hardcoded name, so nothing else in the system — erasure
        # included — could find what they had stored.
        text = (API / "routes" / "chat.py").read_text()

        assert "CHAT_USER" not in text
        assert '"debug"' not in text


class TestEveryRouteAsksInstead:
    @pytest.mark.parametrize(
        "module",
        ["hitl.py", "ingest.py", "settings.py", "maintenance.py", "debug.py", "chat.py"],
    )
    def test_it_takes_a_request_scoped_identity(self, module):
        text = (API / "routes" / module).read_text()

        assert "Identity" in text
        assert "require_identity" in text or "get_identity" in text
