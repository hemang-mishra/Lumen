"""
The one way to see what a sentence actually fetches.

The reading endpoint answers whether the router is any good. This one
answers the question after it — whether the searches find the right things —
and it is the only way to see the third search at all, because that one
exists to notice that two turns of a conversation are about the same thing
and a single request has only one turn.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from lumen.api.deps import get_formulator, get_graph, get_ops, get_retriever
from lumen.api.main import create_app
from lumen.providers.errors import ProviderError
from lumen.providers.fake import FakeLLMProvider
from lumen.query import SessionRegistry
from lumen.query.formulation import QueryFormulator
from lumen.query.retrieval import ConversationalRetriever


def reading(triggers=None, register="STABLE") -> str:
    """The JSON the turn reader would return."""
    return json.dumps(
        {
            "triggers": triggers or [],
            "named_entities": [],
            "emotional_register": register,
            "confidence": 0.8,
            "critical_domain_opened": None,
        }
    )


def invented(texts) -> str:
    """The JSON the invented-record request would return."""
    return json.dumps(
        {
            "hypotheticals": [
                {"index": position, "text": text}
                for position, text in enumerate(texts, start=1)
            ]
        }
    )


@pytest.fixture
def retrieve_client(graph_store, ops_store, vector_store, embedder):
    """A client whose reader and searcher both answer from scripts."""
    built = []

    def _make(readings=(), inventions=("an earlier note",)):
        formulator = QueryFormulator(
            llm=FakeLLMProvider(list(readings)), graph=graph_store
        )
        retriever = ConversationalRetriever(
            graph=graph_store,
            vectors=vector_store,
            embedder=embedder,
            llm=FakeLLMProvider({"ITEMS:": invented(inventions)}),
        )
        built.extend([formulator, retriever])

        app = create_app()
        app.dependency_overrides[get_graph] = lambda: graph_store
        app.dependency_overrides[get_ops] = lambda: ops_store
        app.dependency_overrides[get_formulator] = lambda: formulator
        app.dependency_overrides[get_retriever] = lambda: retriever
        app.state.graph = graph_store
        app.state.ops = ops_store
        app.state.formulator = formulator
        app.state.sessions = SessionRegistry()
        return TestClient(app, raise_server_exceptions=False)

    yield _make

    for item in built:
        item.close()


class TestFetchingForASentence:
    def test_a_sentence_comes_back_with_what_was_found(
        self, retrieve_client, seed_observation
    ):
        seed_observation("obs_1", "an earlier note")
        client = retrieve_client([reading([{"trigger_type": "PATTERN_MENTION"}])])

        body = client.post(
            "/query/retrieve", json={"text": "I keep avoiding it"}
        ).json()

        assert body["outcome"] == "RETRIEVED"
        assert [node["node_id"] for node in body["candidates"]] == ["obs_1"]

    def test_the_reading_comes_back_with_it(self, retrieve_client):
        # Neither half is judgeable alone: the records make no sense without
        # the reasons that fetched them.
        client = retrieve_client([reading([{"trigger_type": "PATTERN_MENTION"}])])

        body = client.post("/query/retrieve", json={"text": "I keep avoiding it"}).json()

        assert body["signal"]["retrieval_triggers"][0]["trigger_type"] == (
            "PATTERN_MENTION"
        )

    def test_each_search_reports_separately(self, retrieve_client):
        client = retrieve_client([reading([{"trigger_type": "PATTERN_MENTION"}])])

        body = client.post("/query/retrieve", json={"text": "I keep avoiding it"}).json()

        assert {report["which"] for report in body["passes"]} == {
            "SEMANTIC",
            "STRUCTURAL",
            "CONTINUITY",
        }

    def test_a_turn_with_no_reason_fetches_nothing(self, retrieve_client):
        client = retrieve_client([])

        body = client.post("/query/retrieve", json={"text": "thanks"}).json()

        assert body["outcome"] == "NOT_NEEDED"
        assert body["candidates"] == []

    def test_a_turn_in_crisis_fetches_nothing_and_says_why(self, retrieve_client):
        client = retrieve_client([])

        body = client.post(
            "/query/retrieve", json={"text": "some days I just want to die"}
        ).json()

        assert body["outcome"] == "SUPPRESSED"

    def test_earlier_messages_can_be_supplied(self, retrieve_client):
        client = retrieve_client([reading([{"trigger_type": "PROGRESS_CLAIM"}])])

        response = client.post(
            "/query/retrieve",
            json={
                "text": "I don't feel that anymore",
                "history": [
                    {"role": "user", "content": "I used to think I was the problem"},
                    {"role": "assistant", "content": "What changed?"},
                ],
            },
        )

        assert response.status_code == 200
        assert response.json()["signal"]["turn_index"] == 2


class TestStayingInOneConversation:
    def test_without_a_key_each_call_starts_fresh(self, retrieve_client):
        client = retrieve_client([reading([{"trigger_type": "PATTERN_MENTION"}])] * 2)

        first = client.post("/query/retrieve", json={"text": "one"}).json()
        second = client.post("/query/retrieve", json={"text": "two"}).json()

        assert first["signal"]["turn_index"] == 0
        assert second["signal"]["turn_index"] == 0

    def test_a_key_carries_the_conversation_forward(self, retrieve_client):
        client = retrieve_client([reading([{"trigger_type": "PATTERN_MENTION"}])] * 2)

        client.post("/query/retrieve", json={"text": "one", "session_key": "me"})
        second = client.post(
            "/query/retrieve", json={"text": "two", "session_key": "me"}
        ).json()

        assert second["signal"]["turn_index"] == 1

    def test_what_the_conversation_is_holding_is_visible(
        self, retrieve_client, seed_observation
    ):
        # The whole reason this surface takes a key: today's thread cannot be
        # seen from a single request.
        seed_observation("obs_1", "an earlier note")
        client = retrieve_client([reading([{"trigger_type": "PATTERN_MENTION"}])] * 2)

        client.post("/query/retrieve", json={"text": "one", "session_key": "me"})
        second = client.post(
            "/query/retrieve", json={"text": "two", "session_key": "me"}
        ).json()

        assert second["buffered"] == ["obs_1"]

    def test_a_record_carried_forward_is_marked_as_carried(
        self, retrieve_client, seed_observation
    ):
        seed_observation("obs_1", "an earlier note")
        client = retrieve_client([reading([{"trigger_type": "PATTERN_MENTION"}])] * 2)

        client.post("/query/retrieve", json={"text": "one", "session_key": "me"})
        second = client.post(
            "/query/retrieve", json={"text": "two", "session_key": "me"}
        ).json()

        assert second["candidates"][0]["boosted"] is True


class TestWhatCrossesTheBoundary:
    def test_the_machinery_the_next_stage_needs_does_not(
        self, retrieve_client, seed_observation
    ):
        # Candidates carry the whole record internally so the next stage can
        # compress it without reading the graph again. That is machinery, and
        # machinery does not belong on a web response.
        seed_observation("obs_1", "an earlier note")
        client = retrieve_client([reading([{"trigger_type": "PATTERN_MENTION"}])])

        body = client.post("/query/retrieve", json={"text": "one"}).json()

        assert "properties" not in body["candidates"][0]

    def test_what_was_held_back_is_named_rather_than_vanishing(
        self, retrieve_client, seed_pattern, index_node
    ):
        seed_pattern(
            "pat_critical",
            name="an earlier note",
            signal="CRITICAL",
            domain="SELF_CONCEPT",
        )
        index_node("pat_critical", "an earlier note", node_type="PatternNode")
        client = retrieve_client([reading([{"trigger_type": "PATTERN_MENTION"}])])

        body = client.post("/query/retrieve", json={"text": "one"}).json()

        assert body["gated"] == ["pat_critical"]
        assert body["candidates"] == []


class TestWhenNothingCanSearch:
    def test_a_deployment_with_no_model_refuses_plainly(
        self, graph_store, ops_store
    ):
        # An empty list of records would read as "this person has no
        # history", which is the one answer that must never be given by
        # mistake.
        class NoStack:
            def get(self):
                raise ProviderError("no embedder configured")

        formulator = QueryFormulator(llm=FakeLLMProvider([]), graph=graph_store)
        app = create_app()
        app.dependency_overrides[get_graph] = lambda: graph_store
        app.dependency_overrides[get_ops] = lambda: ops_store
        app.dependency_overrides[get_formulator] = lambda: formulator
        app.state.graph = graph_store
        app.state.ops = ops_store
        app.state.formulator = formulator
        app.state.sessions = SessionRegistry()
        app.state.search = NoStack()

        with TestClient(app, raise_server_exceptions=False) as _:
            pass
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/query/retrieve", json={"text": "I keep avoiding it"})

        assert response.status_code == 503
        formulator.close()

    def test_a_deployment_that_cannot_search_at_all_refuses(
        self, graph_store, ops_store
    ):
        formulator = QueryFormulator(llm=FakeLLMProvider([]), graph=graph_store)
        app = create_app()
        app.dependency_overrides[get_graph] = lambda: graph_store
        app.dependency_overrides[get_ops] = lambda: ops_store
        app.dependency_overrides[get_formulator] = lambda: formulator
        app.state.graph = graph_store
        app.state.ops = ops_store
        app.state.formulator = formulator
        app.state.sessions = SessionRegistry()

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/query/retrieve", json={"text": "I keep avoiding it"})

        assert response.status_code == 503
        formulator.close()
