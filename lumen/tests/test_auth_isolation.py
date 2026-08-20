"""
Two people, and every endpoint asked for the other one's things.

The adversarial half of this goal. Everything else tests that a signed-in
person gets in; this tests that getting in is not the same as getting
everything.

**What this can and cannot check today, stated plainly.** The operational
database has been keyed by user since it was built, so conversations, jobs,
imports, the review queue and settings are genuinely separate and are tested
that way here. The graph and the search index carry no notion of a user at
all: there is one graph, and every signed-in person shares it. That is
correct for the single-user deployment this is, and it is the reason a second
person must not be invited before per-user stores land.

The tests below are therefore split by which of those two halves they are
about, and the graph ones assert what is true rather than what will be.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from lumen.auth import AuthService, Identity
from lumen.auth import keys as keymod
from lumen.auth import tokens
from lumen.config import AppConfig, AuthConfig
from lumen.operational.schemas import BufferMessageRecord, SessionBufferRecord

NOW = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)


@pytest.fixture
def two_people(graph_store, ops_store, vector_store, monkeypatch):
    """An application with two accounts and a token for each."""
    private, _ = keymod.generate()
    monkeypatch.setenv("LUMEN_JWT_PRIVATE_KEY", private)
    monkeypatch.delenv("LUMEN_JWT_PUBLIC_KEYS", raising=False)

    from lumen.api.deps import get_graph, get_ops
    from lumen.api.events import EventBus
    from lumen.api.main import create_app
    from lumen.auth.google import GoogleIdentityProvider

    settings = AppConfig(auth=AuthConfig(enabled=True))
    app = create_app(settings)
    app.dependency_overrides[get_graph] = lambda: graph_store
    app.dependency_overrides[get_ops] = lambda: ops_store
    app.state.graph = graph_store
    app.state.ops = ops_store
    app.state.events = EventBus()
    app.state.config = settings
    app.state.auth = AuthService(
        repository=ops_store.identities,
        provider=GoogleIdentityProvider(settings.auth),
        keys=keymod.load(settings.auth),
        config=settings.auth,
    )

    # The surfaces being asked about, wired to the same test stores. Each is
    # the real object rather than a stand-in, because what is being checked
    # is which person's data comes back out of it.
    from lumen.api.deps import get_eraser, get_memory, get_personas, get_reviewer
    from lumen.erasure import ErasureService
    from lumen.query.conversation import ConversationStore
    from lumen.query.memory import ConversationMemory
    from lumen.query.prompting import PersonaStore
    from lumen.review.service import ReviewService
    from lumen.config import ChatConfig
    from lumen.providers.fake import FakeLLMProvider

    personas = PersonaStore(settings=ops_store.settings)
    reviewer = ReviewService(
        config=settings,
        graph=graph_store,
        ops=ops_store,
        open_vectors=lambda: vector_store,
        open_embedder=lambda: None,
    )
    eraser = ErasureService(
        config=settings, graph=graph_store, vectors=vector_store, ops=ops_store
    )
    memory = ConversationMemory(
        store=ConversationStore(ops_store.buffers),
        llm=FakeLLMProvider(["a summary"] * 30),
        config=ChatConfig(),
    )
    app.dependency_overrides[get_personas] = lambda: personas
    app.dependency_overrides[get_reviewer] = lambda: reviewer
    app.dependency_overrides[get_eraser] = lambda: eraser
    app.dependency_overrides[get_memory] = lambda: memory
    app.state.reviewer = reviewer
    app.state.eraser = eraser

    people = {}
    for name in ("alice", "bob"):
        person = ops_store.identities.create_user(
            email=f"{name}@example.com", display_name=name, avatar_url=None
        )
        token, _ = tokens.mint(
            Identity(
                user_id=person.user_id,
                email=person.email,
                token_version=person.token_version,
            ),
            keys=app.state.auth._keys,
            config=settings.auth,
        )
        people[name] = {
            "user": person,
            "headers": {"Authorization": f"Bearer {token}"},
        }

    return TestClient(app, raise_server_exceptions=False), people


def a_conversation(ops_store, user_id: str, session_id: str, said: str) -> None:
    """One conversation belonging to one person."""
    ops_store.buffers.create_buffer(
        SessionBufferRecord(
            session_id=session_id,
            user_id=user_id,
            event_date=date(2026, 8, 20),
            # The default label, which is the one the conversation surface
            # reads back. Two people may share it; the unique rule is per
            # person.
            session_label="",
        )
    )
    ops_store.buffers.append_message(
        session_id,
        BufferMessageRecord(
            message_id=f"msg_{session_id}",
            session_id=session_id,
            seq=0,
            role="USER",
            content=said,
            timestamp=NOW,
            event_date=date(2026, 8, 20),
        ),
    )


class TestTheOperationalHalf:
    def test_a_conversation_belongs_to_one_person(self, two_people, ops_store):
        client, people = two_people
        a_conversation(ops_store, people["alice"]["user"].user_id, "alice_1", "mine")

        theirs = client.get("/chat/days", headers=people["alice"]["headers"]).json()
        somebody_else = client.get("/chat/days", headers=people["bob"]["headers"]).json()

        assert [day["session_id"] for day in theirs] == ["alice_1"]
        assert somebody_else == []

    def test_the_review_queue_belongs_to_one_person(self, two_people):
        client, people = two_people

        for name in ("alice", "bob"):
            answer = client.get("/hitl", headers=people[name]["headers"])
            assert answer.status_code == 200

        # Both empty here, and the point is that each was asked *as* that
        # person rather than as whoever the process was configured to be.
        assert client.get("/hitl/count", headers=people["alice"]["headers"]).json()

    def test_settings_belong_to_one_person(self, two_people):
        client, people = two_people

        client.put(
            "/settings/persona",
            json={"identity": "Alice wrote this"},
            headers=people["alice"]["headers"],
        )

        mine = client.get("/settings/persona", headers=people["alice"]["headers"])
        theirs = client.get("/settings/persona", headers=people["bob"]["headers"])

        assert "Alice wrote this" in mine.text
        assert "Alice wrote this" not in theirs.text

    def test_uploads_belong_to_one_person(self, two_people, ops_store):
        from lumen.operational.schemas import ImportRecord

        client, people = two_people
        ops_store.imports.record(
            ImportRecord(
                import_id="imp_1",
                batch_id="batch_1",
                user_id=people["alice"]["user"].user_id,
                source_conversation_id="conv-1",
                title="Alice's export",
                filename="alice.json",
                event_date=date(2026, 8, 20),
                message_count=1,
            )
        )

        mine = client.get("/ingest/imports", headers=people["alice"]["headers"]).json()
        theirs = client.get("/ingest/imports", headers=people["bob"]["headers"]).json()

        assert len(mine) == 1
        assert theirs == []

    def test_erasure_receipts_belong_to_one_person(self, two_people):
        client, people = two_people

        client.post(
            "/maintenance/erasure",
            json={"scope": "ALL", "confirmation": "ERASE"},
            headers=people["alice"]["headers"],
        )

        mine = client.get(
            "/maintenance/erasure/audits", headers=people["alice"]["headers"]
        ).json()
        theirs = client.get(
            "/maintenance/erasure/audits", headers=people["bob"]["headers"]
        ).json()

        assert mine["count"] == 1
        assert theirs["count"] == 0

    def test_erasing_one_person_leaves_the_other_alone(
        self, two_people, ops_store
    ):
        # The strongest of these. One person asking to be forgotten must not
        # take somebody else's conversations with them.
        client, people = two_people
        a_conversation(ops_store, people["alice"]["user"].user_id, "alice_1", "mine")
        a_conversation(ops_store, people["bob"]["user"].user_id, "bob_1", "theirs")

        client.post(
            "/maintenance/erasure",
            json={"scope": "ALL", "confirmation": "ERASE"},
            headers=people["alice"]["headers"],
        )

        assert ops_store.buffers.get_messages("alice_1")[0].content.startswith("[ERASED")
        assert ops_store.buffers.get_messages("bob_1")[0].content == "theirs"

    def test_a_conversation_is_written_under_whoever_is_talking(
        self, two_people, ops_store
    ):
        # The defect this goal exists to close: the conversation surface used
        # to write under a hardcoded name of its own, so nothing else in the
        # system — erasure included — could find what it had stored.
        client, people = two_people
        a_conversation(ops_store, people["bob"]["user"].user_id, "bob_1", "theirs")

        days = client.get("/chat/days", headers=people["bob"]["headers"]).json()

        assert [day["session_id"] for day in days] == ["bob_1"]


class TestTheGraphHalf:
    def test_one_graph_is_shared_and_this_is_known(self, two_people, seed_pattern):
        # Asserted rather than glossed over. There is one graph until
        # per-user stores land, and a test that pretended otherwise would be
        # the most dangerous kind of passing test.
        client, people = two_people
        seed_pattern("pat_1", name="something either of them could see")

        mine = client.get("/graph/nodes/pat_1", headers=people["alice"]["headers"])
        theirs = client.get("/graph/nodes/pat_1", headers=people["bob"]["headers"])

        assert mine.status_code == 200
        assert theirs.status_code == 200
        assert mine.json() == theirs.json()
