"""
Tests that nothing secret ever gets written down.

A credential that reaches a log line is a credential in a file that outlives
the process, gets copied to a laptop, and is read by whoever is debugging
something unrelated six months later. The same is true of an error body, a
URL, and — specific to this system — the snapshot of settings written onto
every pipeline run.

The last one is why the two credentials here are computed values rather than
stored settings. Anything that walks the fields of a settings object would
carry them into the database, and one already does.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from lumen.auth import AuthService
from lumen.auth import keys as keymod
from lumen.config import AppConfig, AuthConfig
from lumen.tests.conftest_auth import FakeGoogleKeys, FakeTokenEndpoint, google_key, id_token

SECRET = "the-google-secret-nobody-should-ever-see"
CLIENT_ID = "client-1"


@pytest.fixture
def watched(graph_store, ops_store, monkeypatch, caplog):
    """An application signing somebody in, with every log line captured."""
    private, _ = keymod.generate()
    monkeypatch.setenv("LUMEN_JWT_PRIVATE_KEY", private)
    monkeypatch.delenv("LUMEN_JWT_PUBLIC_KEYS", raising=False)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", SECRET)

    from lumen.api.deps import get_graph, get_ops
    from lumen.api.events import EventBus
    from lumen.api.main import create_app
    from lumen.auth.google import GoogleIdentityProvider

    settings = AppConfig(
        auth=AuthConfig(
            enabled=True,
            google_client_id=CLIENT_ID,
            google_redirect_uri="https://app.example/cb",
            allowed_emails="person@example.com",
        )
    )
    app = create_app(settings)
    app.dependency_overrides[get_graph] = lambda: graph_store
    app.dependency_overrides[get_ops] = lambda: ops_store
    app.state.graph = graph_store
    app.state.ops = ops_store
    app.state.events = EventBus()
    app.state.config = settings

    key = google_key()
    app.state.auth = AuthService(
        repository=ops_store.identities,
        provider=GoogleIdentityProvider(
            settings.auth,
            client=FakeTokenEndpoint(id_token(key, client_id=CLIENT_ID)),
            keys=FakeGoogleKeys(key),
        ),
        keys=keymod.load(settings.auth),
        config=settings.auth,
    )

    caplog.set_level(logging.DEBUG)
    # Over https, because the session cookie is marked Secure in an ordinary
    # deployment and a client on plain http would simply not keep it — which
    # would make this test pass for the wrong reason.
    return (
        TestClient(
            app, base_url="https://testserver", raise_server_exceptions=False
        ),
        settings,
        caplog,
    )


def sign_in(client) -> tuple[str, str]:
    """Sign somebody in and hand back the two credentials it produced."""
    from lumen.api.routes.auth import REFRESH_COOKIE, STATE_COOKIE

    client.get("/auth/google/start")
    answer = client.post(
        "/auth/google/callback",
        json={"code": "the-authorization-code", "state": client.cookies.get(STATE_COOKIE)},
    )
    assert answer.status_code == 200, answer.text
    return answer.json()["access_token"], client.cookies.get(REFRESH_COOKIE)


class TestNothingReachesTheLog:
    def test_not_the_google_secret(self, watched):
        client, _, caplog = watched

        sign_in(client)

        assert SECRET not in caplog.text

    def test_not_the_authorization_code(self, watched):
        # A code is single-use and short-lived, and it is still an
        # authorization somebody could replay before it is spent.
        client, _, caplog = watched

        sign_in(client)

        assert "the-authorization-code" not in caplog.text

    def test_not_the_session_tokens(self, watched):
        client, _, caplog = watched

        access, refresh = sign_in(client)

        assert access not in caplog.text
        assert refresh not in caplog.text

    def test_not_the_signing_key(self, watched, monkeypatch):
        import os

        client, _, caplog = watched
        private = os.environ["LUMEN_JWT_PRIVATE_KEY"]

        sign_in(client)

        body = "".join(
            line for line in private.splitlines() if "-----" not in line
        )
        assert body[:40] not in caplog.text

    def test_a_failed_sign_in_says_nothing_either(self, watched):
        client, _, caplog = watched
        client.get("/auth/google/start")

        client.post(
            "/auth/google/callback",
            json={"code": "the-authorization-code", "state": "wrong"},
        )

        assert "the-authorization-code" not in caplog.text
        assert SECRET not in caplog.text


class TestNothingReachesAnAnswer:
    def test_the_secret_is_not_in_any_reply(self, watched):
        client, _, _ = watched

        started = client.get("/auth/google/start")
        access, _ = sign_in(client)

        assert SECRET not in started.text
        assert SECRET not in access

    def test_a_refusal_does_not_echo_the_code_back(self, watched):
        client, _, _ = watched
        client.get("/auth/google/start")

        answer = client.post(
            "/auth/google/callback",
            json={"code": "the-authorization-code", "state": "wrong"},
        )

        assert "the-authorization-code" not in answer.text

    def test_the_renewable_half_is_never_in_a_body(self, watched):
        client, _, _ = watched

        _, refresh = sign_in(client)
        renewed = client.post("/auth/refresh")

        assert refresh not in renewed.text


class TestNothingReachesTheStoredSettings:
    def test_the_credentials_are_not_fields(self, watched):
        # The reason they are computed values. A snapshot of settings is
        # written onto every pipeline run, and anything that walks the fields
        # would carry a credential into the database with it.
        _, settings, _ = watched

        snapshot = json.dumps(asdict(settings.auth))

        assert SECRET not in snapshot
        assert "private" not in snapshot

    def test_a_whole_config_snapshot_carries_nothing(self, watched):
        from lumen.pipeline.orchestration.bookkeeping import _config_snapshot

        _, settings, _ = watched

        snapshot = json.dumps(_config_snapshot(settings))

        assert SECRET not in snapshot

    def test_but_they_are_still_readable_by_name(self, watched):
        # Being unfindable by accident is the point; being unreachable
        # deliberately would mean nothing could sign anybody in.
        _, settings, _ = watched

        assert settings.auth.google_client_secret == SECRET

    def test_repr_says_nothing(self, watched):
        _, settings, _ = watched

        assert SECRET not in repr(settings.auth)
