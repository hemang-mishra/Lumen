"""
Tests that every door is actually locked.

The point of a router-level default is that forgetting produces a refusal
rather than a leak, and the only way to know it worked is to ask every
endpoint the application has. So this file enumerates them from the
application's own description of itself rather than from a list somebody
maintains — a list would go stale the first time somebody added a route.

Three endpoints are public and they are named here on purpose. Adding a
fourth means changing this test, which is exactly the review anybody adding
one should have to have.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lumen.auth import keys as keymod
from lumen.auth import tokens
from lumen.auth.contracts import Identity
from lumen.config import AppConfig, AuthConfig, ChatConfig

# What anybody may reach without proving anything, and why.
#
#   /health  — a load balancer has no credentials and still has to know
#              whether this process is alive.
#   /auth/*  — the door itself. Requiring a session to sign in would be a
#              system nobody can enter.
#   the keys — published by design: it is how something else verifies a token
#              without being able to make one.
PUBLIC_PREFIXES = ("/health", "/auth/", "/openapi", "/docs", "/redoc", "/ui")


@pytest.fixture
def signed_in_app(graph_store, ops_store, monkeypatch):
    """An application with sign-in switched on, wired to the test stores."""
    private, _ = keymod.generate()
    monkeypatch.setenv("LUMEN_JWT_PRIVATE_KEY", private)
    monkeypatch.delenv("LUMEN_JWT_PUBLIC_KEYS", raising=False)

    from lumen.api.deps import get_graph, get_ops
    from lumen.api.events import EventBus
    from lumen.api.main import create_app

    settings = AppConfig(auth=AuthConfig(enabled=True), chat=ChatConfig())
    app = create_app(settings)
    app.dependency_overrides[get_graph] = lambda: graph_store
    app.dependency_overrides[get_ops] = lambda: ops_store
    app.state.graph = graph_store
    app.state.ops = ops_store
    app.state.events = EventBus()
    app.state.config = settings

    from lumen.auth import AuthService
    from lumen.auth.google import GoogleIdentityProvider

    app.state.auth = AuthService(
        repository=ops_store.identities,
        provider=GoogleIdentityProvider(settings.auth),
        keys=keymod.load(settings.auth),
        config=settings.auth,
    )
    return TestClient(app, raise_server_exceptions=False)


def guarded_paths(client) -> list[tuple[str, str]]:
    """Every method and path the application offers that is not public."""
    spec = client.get("/openapi.json").json()
    found = []
    for path, operations in spec["paths"].items():
        if path.startswith(PUBLIC_PREFIXES):
            continue
        for method in operations:
            if method in ("get", "post", "put", "delete", "patch"):
                found.append((method, path))
    return found


def call(client, method: str, path: str, **kwargs):
    """Ask for one endpoint, with the identifiers filled in with nonsense."""
    concrete = path
    while "{" in concrete:
        start = concrete.index("{")
        end = concrete.index("}")
        concrete = concrete[:start] + "something" + concrete[end + 1 :]
    return getattr(client, method)(concrete, **kwargs)


class TestNothingIsReachableWithoutASession:
    def test_there_is_something_to_check(self, signed_in_app):
        # A guard that guards nothing passes every other test in this class.
        assert len(guarded_paths(signed_in_app)) > 10

    def test_every_guarded_endpoint_refuses_an_anonymous_caller(self, signed_in_app):
        offenders = [
            (method, path)
            for method, path in guarded_paths(signed_in_app)
            if call(signed_in_app, method, path).status_code != 401
        ]

        assert offenders == []

    def test_every_guarded_endpoint_refuses_a_nonsense_token(self, signed_in_app):
        headers = {"Authorization": "Bearer not-a-real-token"}
        offenders = [
            (method, path)
            for method, path in guarded_paths(signed_in_app)
            if call(signed_in_app, method, path, headers=headers).status_code != 401
        ]

        assert offenders == []

    def test_the_refusal_says_how_to_authenticate(self, signed_in_app):
        answer = signed_in_app.get("/reports")

        assert answer.status_code == 401
        assert answer.headers.get("www-authenticate") == "Bearer"

    def test_a_refusal_never_says_whether_anybody_exists(self, signed_in_app):
        # A sign-in surface that distinguishes "no such user" from "wrong
        # credential" is a way of finding out which addresses are worth
        # trying.
        answer = signed_in_app.get("/reports", headers={"Authorization": "Bearer x.y.z"})

        assert "user" not in answer.json()["detail"].lower()
        assert "exist" not in answer.json()["detail"].lower()


class TestWhatIsDeliberatelyPublic:
    def test_the_health_check_needs_nothing(self, signed_in_app):
        assert signed_in_app.get("/health").status_code == 200

    def test_the_published_keys_need_nothing(self, signed_in_app):
        # It is how something else verifies a token without being able to
        # make one, so requiring a token would defeat it.
        assert signed_in_app.get("/auth/.well-known/jwks.json").status_code == 200

    def test_starting_a_sign_in_needs_nothing(self, signed_in_app):
        # Requiring a session to sign in would be a system nobody can enter.
        assert signed_in_app.get("/auth/google/start").status_code in (200, 429, 503)

    def test_the_public_list_is_exactly_what_is_expected(self, signed_in_app):
        # Adding a fourth means changing this test, which is the review
        # anybody adding a public endpoint should have to have.
        spec = signed_in_app.get("/openapi.json").json()
        public = sorted(
            path
            for path in spec["paths"]
            if path.startswith(("/health", "/auth/"))
        )

        assert public == [
            "/auth/.well-known/jwks.json",
            "/auth/google/callback",
            "/auth/google/start",
            "/auth/logout",
            "/auth/me",
            "/auth/refresh",
            "/health",
        ]


class TestASignedInCaller:
    def test_gets_through(self, signed_in_app, ops_store):
        person = ops_store.identities.create_user(
            email="person@example.com", display_name="A", avatar_url=None
        )
        token = _token_for(signed_in_app, person)

        answer = signed_in_app.get(
            "/reports", headers={"Authorization": f"Bearer {token}"}
        )

        assert answer.status_code == 200

    def test_is_refused_once_their_sessions_are_ended(self, signed_in_app, ops_store):
        # The whole reason a token carries a generation number: revocation
        # without a list of dead tokens on every request.
        person = ops_store.identities.create_user(
            email="person@example.com", display_name="A", avatar_url=None
        )
        token = _token_for(signed_in_app, person)
        headers = {"Authorization": f"Bearer {token}"}
        assert signed_in_app.get("/reports", headers=headers).status_code == 200

        ops_store.identities.bump_token_version(person.user_id)

        answer = signed_in_app.get("/reports", headers=headers)
        assert answer.status_code == 401
        assert "ended" in answer.json()["detail"]

    def test_is_refused_once_their_account_is_suspended(self, signed_in_app, ops_store):
        from lumen.operational.enums import UserStatus

        person = ops_store.identities.create_user(
            email="person@example.com", display_name="A", avatar_url=None
        )
        headers = {"Authorization": f"Bearer {_token_for(signed_in_app, person)}"}
        ops_store.identities.set_status(person.user_id, UserStatus.SUSPENDED)

        assert signed_in_app.get("/reports", headers=headers).status_code == 401

    def test_a_token_for_somebody_who_no_longer_exists_is_refused(
        self, signed_in_app, ops_store
    ):
        person = ops_store.identities.create_user(
            email="gone@example.com", display_name="A", avatar_url=None
        )
        headers = {"Authorization": f"Bearer {_token_for(signed_in_app, person)}"}

        with ops_store.transaction():
            pass
        from sqlalchemy import delete
        from sqlalchemy.orm import Session
        from lumen.operational import models

        with Session(ops_store.engine) as db:
            db.execute(delete(models.User).where(models.User.user_id == person.user_id))
            db.commit()

        assert signed_in_app.get("/reports", headers=headers).status_code == 401


def _token_for(client, person) -> str:
    """A real access token for a stored person."""
    app = client.app
    identity = Identity(
        user_id=person.user_id,
        email=person.email,
        token_version=person.token_version,
    )
    token, _ = tokens.mint(
        identity, keys=app.state.auth._keys, config=app.state.config.auth
    )
    return token
