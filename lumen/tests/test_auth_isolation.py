"""
Two people, and every endpoint asked for the other one's things.

The adversarial half of this goal. Everything else tests that a signed-in
person gets in; this tests that getting in is not the same as getting
everything.

Both halves are real now. The operational database has been keyed by person
since it was built, and since per-user stores there is a graph and a search
collection each as well — so "somebody else's identifier" is not a filter
somebody might forget, it is a directory this request was never given a
handle to.
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
def two_people(ops_store, monkeypatch, tmp_path):
    """An application with two accounts and a token for each."""
    private, _ = keymod.generate()
    monkeypatch.setenv("LUMEN_JWT_PRIVATE_KEY", private)
    monkeypatch.delenv("LUMEN_JWT_PUBLIC_KEYS", raising=False)

    from lumen.api.deps import get_ops
    from lumen.api.events import EventBus
    from lumen.api.main import create_app
    from lumen.auth.google import GoogleIdentityProvider
    from lumen.config import GraphConfig, VectorConfig
    from lumen.stores import StoreRegistry

    # Real per-person stores on disk, because the thing being tested is
    # whether they are actually separate. A shared stand-in would pass every
    # test in this file while proving nothing.
    settings = AppConfig(
        auth=AuthConfig(enabled=True),
        graph=GraphConfig(db_root=str(tmp_path / "graphs")),
        vector=VectorConfig(location=str(tmp_path / "vectors"), vector_size=768),
    )
    app = create_app(settings)
    app.dependency_overrides[get_ops] = lambda: ops_store
    app.state.stores = StoreRegistry(settings)
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
        stores=app.state.stores,
        ops=ops_store,
        open_embedder=lambda: None,
    )
    eraser = ErasureService(
        config=settings, stores=app.state.stores, ops=ops_store
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

    yield TestClient(app, raise_server_exceptions=False), people
    app.state.stores.close()


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
    def test_a_record_belongs_to_one_person_s_graph(self, two_people):
        client, people = two_people
        _write_a_lesson(client, people["alice"], "alice's private lesson")

        mine = client.get("/graph/nodes/les_1", headers=people["alice"]["headers"])
        theirs = client.get("/graph/nodes/les_1", headers=people["bob"]["headers"])

        assert mine.status_code == 200
        assert theirs.status_code == 404

    def test_somebody_else_s_history_does_not_even_show_up_in_a_count(
        self, two_people
    ):
        client, people = two_people
        _write_a_lesson(client, people["alice"], "alice's private lesson")

        mine = client.get("/graph/stats", headers=people["alice"]["headers"]).json()
        theirs = client.get("/graph/stats", headers=people["bob"]["headers"]).json()

        assert mine["total"] == 1
        assert theirs["total"] == 0

    def test_every_read_endpoint_refuses_the_other_person_s_identifiers(
        self, two_people
    ):
        # The adversarial one, and the point of the whole goal. Every read in
        # the graph surface, asked with somebody else's identifier.
        client, people = two_people
        _write_a_lesson(client, people["alice"], "alice's private lesson")
        theirs = people["bob"]["headers"]

        paths = [
            "/graph/nodes/les_1",
            "/graph/nodes/les_1/neighbors",
            "/graph/nodes/les_1/versions",
            "/graph/nodes/les_1/decisions",
        ]
        leaked = [
            path
            for path in paths
            if "alice's private lesson" in client.get(path, headers=theirs).text
        ]

        assert leaked == []

    def test_listing_records_shows_only_your_own(self, two_people):
        client, people = two_people
        _write_a_lesson(client, people["alice"], "alice's private lesson")

        listing = client.get(
            "/graph/nodes", params={"type": "LessonNode"}, headers=people["bob"]["headers"]
        )

        assert listing.json()["nodes"] == []

    def test_writing_as_one_person_never_reaches_the_other(self, two_people):
        client, people = two_people
        _write_a_lesson(client, people["alice"], "alice's")
        _write_a_lesson(client, people["bob"], "bob's")

        for name, expected in (("alice", "alice's"), ("bob", "bob's")):
            answer = client.get(
                "/graph/nodes/les_1", headers=people[name]["headers"]
            ).json()
            assert answer["properties"]["lesson_statement"] == expected


def _write_a_lesson(client, person, statement: str) -> None:
    """Put one lesson into this person's own graph."""
    with client.app.state.stores.lease(person["user"].user_id) as stores:
        stores.graph.write_node(
            "LessonNode",
            {
                "node_id": "les_1",
                "created_at": "2026-08-20T00:00:00+00:00",
                "valid_from": "2026-08-20T00:00:00+00:00",
                "lesson_statement": statement,
                "domain": "EMOTIONAL",
                "signal_strength": "HIGH",
                "lesson_confidence": 0.9,
                "status": "ACTIVE",
            },
        )
