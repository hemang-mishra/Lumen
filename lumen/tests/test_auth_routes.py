"""
Tests for the sign-in endpoints themselves.

Mostly about cookies, because that is where the security of a browser session
lives. The renewable half of a session has to be unreadable to scripts, sent
only to the endpoint that renews it, and gone when somebody signs out — and
each of those is one flag that is easy to get wrong and impossible to notice.

The state check is the other half. A sign-in that comes back without the flow
it left on is somebody else's sign-in arriving in this browser.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lumen.api.routes.auth import REFRESH_COOKIE, STATE_COOKIE, VERIFIER_COOKIE
from lumen.auth import AuthService
from lumen.auth import keys as keymod
from lumen.config import AppConfig, AuthConfig
from lumen.tests.conftest_auth import FakeGoogleKeys, FakeTokenEndpoint, google_key, id_token

CLIENT_ID = "client-1"


@pytest.fixture
def signing_in(graph_store, ops_store, monkeypatch):
    """An application whose Google is local and whose keys are fresh."""
    private, _ = keymod.generate()
    monkeypatch.setenv("LUMEN_JWT_PRIVATE_KEY", private)
    monkeypatch.delenv("LUMEN_JWT_PUBLIC_KEYS", raising=False)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "a-secret")

    def _build(*, allowed="person@example.com", secure=False, **token_overrides):
        from lumen.api.deps import get_graph, get_ops
        from lumen.api.events import EventBus
        from lumen.api.main import create_app
        from lumen.auth.google import GoogleIdentityProvider

        settings = AppConfig(
            auth=AuthConfig(
                enabled=True,
                google_client_id=CLIENT_ID,
                google_redirect_uri="https://app.example/cb",
                allowed_emails=allowed,
                cookie_secure=secure,
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
        token = id_token(key, client_id=CLIENT_ID, **token_overrides)
        app.state.auth = AuthService(
            repository=ops_store.identities,
            provider=GoogleIdentityProvider(
                settings.auth,
                client=FakeTokenEndpoint(token),
                keys=FakeGoogleKeys(key),
            ),
            keys=keymod.load(settings.auth),
            config=settings.auth,
        )
        # Over https when the deployment marks its cookies Secure, because a
        # browser would not store one otherwise and neither does this client.
        base = "https://testserver" if secure else "http://testserver"
        return TestClient(app, base_url=base, raise_server_exceptions=False)

    return _build


def sign_in(client: TestClient):
    """Go all the way through a sign-in and return the final answer."""
    started = client.get("/auth/google/start")
    state = client.cookies.get(STATE_COOKIE)
    return client.post(
        "/auth/google/callback", json={"code": "a-code", "state": state}
    ), started


class TestStartingASignIn:
    def test_it_hands_back_somewhere_to_go(self, signing_in):
        answer = signing_in().get("/auth/google/start")

        assert answer.status_code == 200
        assert answer.json()["authorization_url"].startswith("https://accounts.google")

    def test_the_two_secrets_are_cookies_and_not_in_the_body(self, signing_in):
        # Handing them to a script would defeat the only thing they are for.
        client = signing_in()

        answer = client.get("/auth/google/start")

        assert set(answer.json()) == {"authorization_url"}
        assert client.cookies.get(STATE_COOKIE)
        assert client.cookies.get(VERIFIER_COOKIE)

    def test_those_cookies_are_unreadable_to_scripts(self, signing_in):
        answer = signing_in().get("/auth/google/start")

        for header in answer.headers.get_list("set-cookie"):
            assert "HttpOnly" in header


class TestFinishingOne:
    def test_it_gives_back_a_session(self, signing_in):
        answer, _ = sign_in(signing_in())

        assert answer.status_code == 200
        body = answer.json()
        assert body["access_token"]
        assert body["token_type"] == "Bearer"
        assert body["user"]["email"] == "person@example.com"

    def test_the_renewable_half_is_never_in_the_body(self, signing_in):
        # It belongs in a cookie a script cannot read; putting it in the body
        # would mean any script on the page could take it.
        answer, _ = sign_in(signing_in())

        assert "refresh_token" not in answer.json()

    def test_the_renewable_half_is_a_cookie_with_the_right_flags(self, signing_in):
        client = signing_in()
        answer, _ = sign_in(client)

        cookie = _set_cookie(answer, REFRESH_COOKIE)
        assert "HttpOnly" in cookie
        assert "Path=/auth" in cookie

    def test_it_is_marked_secure_when_the_deployment_is(self, signing_in):
        client = signing_in(secure=True)
        answer, _ = sign_in(client)

        cookie = _set_cookie(answer, REFRESH_COOKIE)
        assert "Secure" in cookie
        assert "samesite=none" in cookie.lower()

    def test_the_in_flight_cookies_are_cleared_afterwards(self, signing_in):
        client = signing_in()
        answer, _ = sign_in(client)

        assert not client.cookies.get(STATE_COOKIE)
        assert not client.cookies.get(VERIFIER_COOKIE)

    def test_a_person_appears_in_the_store(self, signing_in, ops_store):
        sign_in(signing_in())

        assert ops_store.identities.find_by_email("person@example.com")


class TestTheStateCheck:
    def test_a_sign_in_with_no_state_at_all_is_refused(self, signing_in):
        client = signing_in()
        client.get("/auth/google/start")
        client.cookies.delete(STATE_COOKIE)

        answer = client.post(
            "/auth/google/callback", json={"code": "a-code", "state": "anything"}
        )

        assert answer.status_code == 400

    def test_a_sign_in_carrying_somebody_elses_state_is_refused(self, signing_in):
        # Without this, a third party can complete a sign-in inside this
        # browser's session.
        client = signing_in()
        client.get("/auth/google/start")

        answer = client.post(
            "/auth/google/callback",
            json={"code": "a-code", "state": "a-state-from-another-flow"},
        )

        assert answer.status_code == 400

    def test_nobody_is_created_when_the_state_is_wrong(self, signing_in, ops_store):
        client = signing_in()
        client.get("/auth/google/start")

        client.post(
            "/auth/google/callback", json={"code": "a-code", "state": "wrong"}
        )

        assert ops_store.identities.find_by_email("person@example.com") is None


class TestWhoIsAllowedAnAccount:
    def test_somebody_on_the_list_gets_in(self, signing_in):
        answer, _ = sign_in(signing_in(allowed="person@example.com"))

        assert answer.status_code == 200

    def test_somebody_who_is_not_is_refused(self, signing_in):
        # An open sign-in on a reachable host hands a database and a model
        # budget to whoever finds the port.
        answer, _ = sign_in(signing_in(allowed="somebody-else@example.com"))

        assert answer.status_code == 403

    def test_the_list_ignores_capitals(self, signing_in):
        answer, _ = sign_in(signing_in(allowed="Person@Example.com"))

        assert answer.status_code == 200


class TestRenewingAndEnding:
    def test_renewing_gives_a_new_token(self, signing_in):
        client = signing_in()
        first, _ = sign_in(client)

        renewed = client.post("/auth/refresh")

        assert renewed.status_code == 200
        assert renewed.json()["access_token"] != first.json()["access_token"]

    def test_renewing_with_no_session_is_refused(self, signing_in):
        assert signing_in().post("/auth/refresh").status_code == 401

    def test_using_a_renewal_twice_is_refused(self, signing_in):
        client = signing_in()
        sign_in(client)
        stolen = client.cookies.get(REFRESH_COOKIE)
        client.post("/auth/refresh")

        client.cookies.set(REFRESH_COOKIE, stolen, path="/auth")
        assert client.post("/auth/refresh").status_code == 401

    def test_signing_out_clears_the_cookie(self, signing_in):
        client = signing_in()
        sign_in(client)

        answer = client.post("/auth/logout")

        assert answer.status_code == 200
        assert not client.cookies.get(REFRESH_COOKIE)

    def test_signing_out_without_a_session_is_still_fine(self, signing_in):
        # Already in the state being asked for.
        assert signing_in().post("/auth/logout").status_code == 200

    def test_a_signed_out_session_cannot_be_renewed(self, signing_in):
        client = signing_in()
        sign_in(client)
        held = client.cookies.get(REFRESH_COOKIE)
        client.post("/auth/logout")

        client.cookies.set(REFRESH_COOKIE, held, path="/auth")
        assert client.post("/auth/refresh").status_code == 401


class TestWhoAmI:
    def test_it_answers_for_a_signed_in_caller(self, signing_in):
        client = signing_in()
        answer, _ = sign_in(client)
        token = answer.json()["access_token"]

        me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

        assert me.status_code == 200
        assert me.json()["email"] == "person@example.com"

    def test_it_refuses_an_anonymous_caller(self, signing_in):
        assert signing_in().get("/auth/me").status_code == 401


class TestThePublishedKeys:
    def test_they_are_served_to_anybody(self, signing_in):
        answer = signing_in().get("/auth/.well-known/jwks.json")

        assert answer.status_code == 200
        assert answer.json()["keys"]

    def test_they_carry_no_private_half(self, signing_in):
        document = signing_in().get("/auth/.well-known/jwks.json").json()

        for key in document["keys"]:
            assert "d" not in key


def _set_cookie(response, name: str) -> str:
    """The Set-Cookie header for one cookie, so its flags can be read."""
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header
    raise AssertionError(f"no {name} cookie was set")
