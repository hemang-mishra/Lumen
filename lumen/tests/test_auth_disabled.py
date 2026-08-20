"""
Tests that switching sign-in off changes nothing.

This is the seam that made the rest of the goal possible. The existing
deployment has one person and no notion of an account, and the whole test
suite is written against it — so identity had to arrive in a way that could be
turned off completely rather than in a way everything else had to be rewritten
around.

What "off" means precisely: the request-scoped identity is the configured
default, and no route can tell the difference.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lumen.api.deps import get_identity
from lumen.config import AppConfig, AuthConfig


@pytest.fixture
def anonymous_app(graph_store, ops_store):
    """The application as it has always been: one person, no sign-in."""
    from lumen.api.deps import get_graph, get_ops
    from lumen.api.events import EventBus
    from lumen.api.main import create_app

    settings = AppConfig(auth=AuthConfig(enabled=False), default_user_id="somebody")
    app = create_app(settings)
    app.dependency_overrides[get_graph] = lambda: graph_store
    app.dependency_overrides[get_ops] = lambda: ops_store
    app.state.graph = graph_store
    app.state.ops = ops_store
    app.state.events = EventBus()
    app.state.config = settings

    # Present even with sign-in off, exactly as the real startup builds it:
    # a deployment given no keys can neither issue nor verify, and saying so
    # is more useful than a route that is not there.
    from lumen.api.deps import get_memory
    from lumen.auth import AuthService
    from lumen.auth import keys as keymod
    from lumen.auth.google import GoogleIdentityProvider
    from lumen.config import ChatConfig
    from lumen.providers.fake import FakeLLMProvider
    from lumen.query.conversation import ConversationStore
    from lumen.query.memory import ConversationMemory

    app.state.auth = AuthService(
        repository=ops_store.identities,
        provider=GoogleIdentityProvider(settings.auth),
        keys=keymod.load(settings.auth),
        config=settings.auth,
    )
    app.dependency_overrides[get_memory] = lambda: ConversationMemory(
        store=ConversationStore(ops_store.buffers),
        llm=FakeLLMProvider(["a summary"] * 30),
        config=ChatConfig(),
    )
    return TestClient(app, raise_server_exceptions=False)


class TestNothingIsAsked:
    def test_an_anonymous_caller_gets_through(self, anonymous_app):
        assert anonymous_app.get("/reports").status_code == 200

    def test_a_nonsense_token_is_not_even_looked_at(self, anonymous_app):
        # Nothing is verified because nothing is required. A deployment with
        # sign-in off should not fail differently depending on what somebody
        # sent it.
        answer = anonymous_app.get(
            "/reports", headers={"Authorization": "Bearer nonsense"}
        )

        assert answer.status_code == 200

    def test_the_health_check_still_answers(self, anonymous_app):
        assert anonymous_app.get("/health").status_code == 200


class TestWhoEverythingBelongsTo:
    def test_it_is_the_configured_default(self, anonymous_app):
        from starlette.requests import Request

        scope = {
            "type": "http",
            "app": anonymous_app.app,
            "headers": [],
            "query_string": b"",
        }
        identity = get_identity(Request(scope))

        assert identity.user_id == "somebody"

    def test_it_says_it_is_not_a_real_sign_in(self, anonymous_app):
        # So a caller can tell a person from a fallback without reading
        # configuration itself.
        from starlette.requests import Request

        scope = {
            "type": "http",
            "app": anonymous_app.app,
            "headers": [],
            "query_string": b"",
        }

        assert get_identity(Request(scope)).authenticated is False

    def test_everything_is_written_under_that_one_name(
        self, anonymous_app, ops_store
    ):
        # The A0 defect in reverse: with sign-in off there is still exactly
        # one answer to "whose is this", and every surface uses it.
        from datetime import date, datetime, timezone

        from lumen.operational.schemas import BufferMessageRecord, SessionBufferRecord

        ops_store.buffers.create_buffer(
            SessionBufferRecord(
                session_id="chat_1",
                user_id="somebody",
                event_date=date(2026, 8, 20),
                session_label="",
            )
        )
        ops_store.buffers.append_message(
            "chat_1",
            BufferMessageRecord(
                message_id="m1",
                session_id="chat_1",
                seq=0,
                role="USER",
                content="something private",
                timestamp=datetime(2026, 8, 20, tzinfo=timezone.utc),
                event_date=date(2026, 8, 20),
            ),
        )

        days = anonymous_app.get("/chat/days").json()

        assert [day["session_id"] for day in days] == ["chat_1"]


class TestTheSignInSurfaceIsStillThere:
    def test_the_keys_document_answers_even_with_no_keys(self, anonymous_app):
        # A deployment given no keys can neither issue nor verify, and saying
        # so with an empty document is better than a route that is missing.
        answer = anonymous_app.get("/auth/.well-known/jwks.json")

        assert answer.status_code == 200
        assert answer.json() == {"keys": []}

    def test_asking_who_you_are_answers_the_default(self, anonymous_app, ops_store):
        # There is no row for the configured default — it is a setting, not a
        # person — so this is honestly a 404 rather than an invented profile.
        assert anonymous_app.get("/auth/me").status_code == 404
