"""
Tests for the published description of this API, and the check that keeps it true.

The front end generates its types from a file in this repository. That file is
only worth anything if it still describes the code, so the comparison lives
here rather than in a build script somewhere else: a response model changed in
Python breaks the Python test suite, which is where the person who changed it
is already looking.

The rest is about the description being the same on every machine. A
description that depends on the developer's .env would produce a check that
fails for reasons nobody can reproduce, which is a check people learn to
ignore.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lumen.api.schema_dump import (
    DEFAULT_OUTPUT,
    SOCKET_SECTION,
    build_schema,
    canonical_config,
    differs_from,
    main,
    socket_events,
    write,
)


def _repository_root() -> Path:
    """The directory the published description is written relative to."""
    return Path(__file__).resolve().parents[2]


class TestTheDescriptionIsTheSameEverywhere:
    def test_it_does_not_depend_on_the_environment(self, monkeypatch):
        # Somebody with uploads switched off in their own environment must
        # still produce the description of a service that has them.
        monkeypatch.setenv("LUMEN_ENABLE_INGEST", "false")
        monkeypatch.setenv("LUMEN_AUTH_ENABLED", "true")

        schema = build_schema()

        assert "/ingest/file" in schema["paths"]

    def test_it_leaves_the_environment_exactly_as_it_found_it(self, monkeypatch):
        monkeypatch.setenv("LUMEN_ENABLE_INGEST", "false")
        monkeypatch.setenv("LUMEN_GRAPH_DB_ROOT", "/somewhere/of/mine")
        import os

        before = dict(os.environ)

        build_schema()

        assert dict(os.environ) == before

    def test_building_it_twice_gives_the_same_thing(self):
        assert build_schema() == build_schema()

    def test_the_settings_it_uses_are_the_plain_ones_of_the_code(self):
        assert canonical_config().ingest.enabled is True


class TestWhatIsDescribed:
    def test_every_route_is_in_it(self, api_client):
        described = set(build_schema()["paths"])
        served = {
            route.path
            for route in api_client.app.routes
            if getattr(route, "include_in_schema", False)
            and getattr(route, "methods", None)
        }

        assert served - described == set()

    def test_sockets_are_described_even_though_the_format_cannot(self):
        # Nothing generated can express a conversation over a socket, so the
        # message names are carried alongside instead of being lost.
        events = build_schema()[SOCKET_SECTION]

        assert "turn.accepted" in events["/chat/ws"]
        assert "run_finished" in events["/events/ws"]

    def test_socket_messages_are_read_off_the_classes_that_send_them(self):
        # Read, not listed. A new kind of message is in this list the moment
        # it exists, without anybody remembering to add it.
        from lumen.query.chat.contracts import TurnAccepted, TurnFailed

        kinds = socket_events()["/chat/ws"]

        assert TurnAccepted.model_fields["kind"].default in kinds
        assert TurnFailed.model_fields["kind"].default in kinds

    def test_the_watching_socket_carries_what_the_worker_announces(self):
        from lumen.ingest.worker import WORKER_EVENTS

        watching = socket_events()["/events/ws"]

        assert set(WORKER_EVENTS) <= set(watching)

    def test_the_watching_socket_carries_what_the_clock_announces(self):
        from lumen.api.events import SCHEDULER_EVENTS

        watching = socket_events()["/events/ws"]

        assert set(SCHEDULER_EVENTS) <= set(watching)


class TestTheCommittedFileStillDescribesTheCode:
    def test_it_is_up_to_date(self):
        published = _repository_root() / DEFAULT_OUTPUT
        message = (
            f"{DEFAULT_OUTPUT} no longer describes this API. The front end is "
            "typed from it, so regenerate it in the same change:\n"
            "  uv run python -m lumen.api.schema_dump\n"
            "  cd frontend && npm run types:generate"
        )

        assert not differs_from(published), message


class TestTheCheckItself:
    def test_a_file_that_matches_passes(self, tmp_path):
        written = write(tmp_path / "openapi.json")

        assert differs_from(written) is False

    def test_a_file_that_has_fallen_behind_fails(self, tmp_path):
        written = write(tmp_path / "openapi.json")
        stale = json.loads(written.read_text())
        del stale["paths"]["/health"]
        written.write_text(json.dumps(stale))

        assert differs_from(written) is True

    def test_a_file_that_is_not_there_fails(self, tmp_path):
        assert differs_from(tmp_path / "nothing.json") is True

    def test_a_file_that_is_not_readable_fails_rather_than_raising(self, tmp_path):
        # Half a file is a fallen-behind file. Nobody needs a stack trace to
        # be told to regenerate it.
        broken = tmp_path / "openapi.json"
        broken.write_text("{ this is not json")

        assert differs_from(broken) is True


class TestTheCommand:
    def test_writing_it_reports_where_it_went(self, tmp_path, capsys):
        code = main(["--out", str(tmp_path / "openapi.json")])

        assert code == 0
        assert (tmp_path / "openapi.json").exists()
        assert "wrote" in capsys.readouterr().out

    def test_checking_an_up_to_date_file_succeeds(self, tmp_path, capsys):
        target = write(tmp_path / "openapi.json")

        assert main(["--out", str(target), "--check"]) == 0
        assert "up to date" in capsys.readouterr().out

    def test_checking_a_stale_file_fails_and_says_how_to_fix_it(self, tmp_path, capsys):
        target = tmp_path / "openapi.json"
        target.write_text("{}")

        code = main(["--out", str(target), "--check"])

        assert code == 1
        assert "lumen.api.schema_dump" in capsys.readouterr().out

    def test_it_makes_the_directory_it_writes_into(self, tmp_path):
        written = write(tmp_path / "nested" / "deeper" / "openapi.json")

        assert written.exists()


class TestTheBrowserIsAllowedToCall:
    """
    The browser talks to this service directly, from another address, so the
    permission to do that is part of the front end's foundation rather than a
    detail of the service.
    """

    def test_a_configured_origin_is_allowed(self):
        from lumen.config import AppConfig, AuthConfig
        from lumen.api.main import create_app

        app = create_app(
            AppConfig(auth=AuthConfig(allowed_origins="http://localhost:5173"))
        )

        assert _cors_of(app) is not None

    def test_nothing_is_allowed_when_nothing_is_configured(self):
        # No cross-origin access at all is the honest answer. A setting that
        # looks permissive and silently fails is worse than an absent one.
        from lumen.config import AppConfig, AuthConfig
        from lumen.api.main import create_app

        app = create_app(AppConfig(auth=AuthConfig(allowed_origins="")))

        assert _cors_of(app) is None

    def test_the_session_cookie_is_allowed_through(self):
        # The renewable half of a session is a cookie, and a browser will not
        # send it cross-origin unless the service says it may.
        from lumen.config import AppConfig, AuthConfig
        from lumen.api.main import create_app

        app = create_app(
            AppConfig(auth=AuthConfig(allowed_origins="http://localhost:5173"))
        )

        assert _cors_of(app).kwargs["allow_credentials"] is True

    def test_a_wildcard_is_never_produced_from_an_empty_setting(self):
        # The pairing browsers refuse outright: any origin, with credentials.
        from lumen.config import AppConfig, AuthConfig
        from lumen.api.main import create_app

        app = create_app(AppConfig(auth=AuthConfig(allowed_origins=" , ")))

        assert _cors_of(app) is None

    @pytest.mark.parametrize(
        "configured",
        ["http://localhost:5173", "http://localhost:5173,https://lumen.example"],
    )
    def test_only_the_exact_origins_named(self, configured):
        from lumen.config import AppConfig, AuthConfig
        from lumen.api.main import create_app

        app = create_app(AppConfig(auth=AuthConfig(allowed_origins=configured)))

        assert _cors_of(app).kwargs["allow_origins"] == configured.split(",")


def _cors_of(app):
    """The cross-origin middleware on an application, if it has one."""
    from fastapi.middleware.cors import CORSMiddleware

    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            return middleware
    return None
