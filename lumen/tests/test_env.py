"""
Tests for reading a .env file.

Small surface, one thing that actually matters: a variable somebody set
deliberately is not quietly replaced by a file. Everything else here is
about not falling over when the file is absent, which is the normal state
for a deployment that sets its environment some other way.
"""

from __future__ import annotations

import os

from lumen.env import PROJECT_ROOT, load_env


class TestReadingTheFile:
    def test_values_in_the_file_reach_the_environment(self, tmp_path, monkeypatch):
        monkeypatch.delenv("LUMEN_TEST_ONLY", raising=False)
        target = tmp_path / ".env"
        target.write_text("LUMEN_TEST_ONLY=from_the_file\n")

        assert load_env(target) is True
        assert os.environ["LUMEN_TEST_ONLY"] == "from_the_file"

    def test_a_variable_already_set_wins(self, tmp_path, monkeypatch):
        # Somebody putting a variable on the command line meant it. A file
        # is a default, and a default that overrules an instruction is not
        # a default.
        monkeypatch.setenv("LUMEN_TEST_ONLY", "from_the_command_line")
        target = tmp_path / ".env"
        target.write_text("LUMEN_TEST_ONLY=from_the_file\n")

        load_env(target)

        assert os.environ["LUMEN_TEST_ONLY"] == "from_the_command_line"

    def test_the_file_can_be_told_to_win(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LUMEN_TEST_ONLY", "from_the_command_line")
        target = tmp_path / ".env"
        target.write_text("LUMEN_TEST_ONLY=from_the_file\n")

        load_env(target, override=True)

        assert os.environ["LUMEN_TEST_ONLY"] == "from_the_file"


class TestWhenThereIsNoFile:
    def test_a_missing_file_is_not_a_failure(self, tmp_path):
        assert load_env(tmp_path / "nothing-here") is False

    def test_the_default_location_is_beside_the_project(self):
        # Named rather than guessed, so that running from a subdirectory
        # still finds the same file.
        assert (PROJECT_ROOT / "pyproject.toml").exists()
