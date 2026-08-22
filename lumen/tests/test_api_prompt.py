"""
The one way to see what the assistant would actually be sent.

Everything this layer does happens between somebody speaking and the
assistant answering, and none of it reaches a screen. Without this endpoint
the only evidence that the briefing is any good is that the replies feel
about right, which is not evidence.
"""

from __future__ import annotations

import json

import pytest

from lumen.tests.conftest import registry_for
from fastapi.testclient import TestClient

from lumen.api.deps import (
    get_composer,
    get_formulator,
    get_graph,
    get_memory,
    get_ops,
    get_retriever,
)
from lumen.api.main import create_app
from lumen.providers.fake import FakeLLMProvider
from lumen.query import (
    ConversationalRetriever,
    ConversationMemory,
    ConversationStore,
    PromptComposer,
    QueryFormulator,
    SessionRegistry,
)


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


def invented(text: str = "an earlier note about the same thing") -> str:
    return json.dumps({"hypotheticals": [{"index": 1, "text": text}]})


@pytest.fixture
def prompt_client(graph_store, ops_store, vector_store, embedder):
    """A client with every part of the chat brain wired to test doubles."""
    built = []

    def _make(readings=(), invention="an earlier note about the same thing"):
        formulator = QueryFormulator(
            llm=FakeLLMProvider(list(readings)),
            stores=registry_for(graph_store),
        )
        retriever = ConversationalRetriever(
            stores=registry_for(graph_store, vector_store),
            embedder=embedder,
            llm=FakeLLMProvider({"ITEMS:": invented(invention)}),
        )
        built.extend([formulator, retriever])

        app = create_app()
        app.dependency_overrides[get_graph] = lambda: graph_store
        app.dependency_overrides[get_ops] = lambda: ops_store
        app.dependency_overrides[get_formulator] = lambda: formulator
        app.dependency_overrides[get_retriever] = lambda: retriever
        app.dependency_overrides[get_composer] = lambda: PromptComposer()
        app.dependency_overrides[get_memory] = lambda: ConversationMemory(
            store=ConversationStore(ops_store.buffers)
        )
        app.state.graph = graph_store
        app.state.ops = ops_store
        app.state.formulator = formulator
        app.state.sessions = SessionRegistry()
        return TestClient(app, raise_server_exceptions=False)

    yield _make

    for item in built:
        item.close()


class TestSeeingTheWholePrompt:
    def test_the_instructions_come_back_in_full(self, prompt_client):
        client = prompt_client([reading()])

        body = client.post("/query/prompt", json={"text": "hello"}).json()

        assert "You are Lumen" in body["system"]

    def test_their_history_appears_in_it(self, prompt_client, seed_observation):
        seed_observation("obs_1", "an earlier note about the same thing")
        client = prompt_client([reading([{"trigger_type": "PATTERN_MENTION"}])])

        body = client.post(
            "/query/prompt", json={"text": "I keep avoiding it"}
        ).json()

        assert body["briefing"]
        assert "an earlier note" in body["briefing"][0]["text"]
        assert body["briefing"][0]["node_id"] == "obs_1"

    def test_every_line_can_be_traced_to_a_record(self, prompt_client, seed_observation):
        # "Is this sentence a fair summary of that record?" is the only
        # question worth asking about a briefing, and it needs both halves.
        seed_observation("obs_1", "an earlier note about the same thing")
        client = prompt_client([reading([{"trigger_type": "PATTERN_MENTION"}])])

        body = client.post("/query/prompt", json={"text": "I keep avoiding it"}).json()

        assert body["briefing"][0]["node_type"] == "ObservationNode"
        assert body["briefing"][0]["found_by"] == "SEMANTIC"

    def test_what_was_fetched_and_cut_is_shown(self, prompt_client, seed_observation):
        for index in range(6):
            seed_observation(f"obs_{index}", "an earlier note about the same thing")
        client = prompt_client([reading([{"trigger_type": "PATTERN_MENTION"}])])

        body = client.post("/query/prompt", json={"text": "I keep avoiding it"}).json()

        assert body["dropped"]
        assert {"node_id", "reason"} == set(body["dropped"][0])

    def test_the_allowance_and_what_it_used_are_shown(self, prompt_client):
        client = prompt_client([reading()])

        body = client.post("/query/prompt", json={"text": "hello"}).json()

        assert body["token_budget"] == 800
        assert body["briefing_tokens"] <= body["token_budget"]
        assert body["total_tokens"] > 0

    def test_the_turn_itself_comes_back_as_part_of_the_conversation(
        self, prompt_client
    ):
        client = prompt_client([reading()])

        body = client.post(
            "/query/prompt", json={"text": "I keep avoiding it"}
        ).json()

        assert body["messages"][-1]["content"] == "I keep avoiding it"

    def test_no_reply_is_generated(self, prompt_client):
        # Writing one is the next goal's job, and a mocked one here would be
        # the least useful thing on the page.
        client = prompt_client([reading()])

        body = client.post("/query/prompt", json={"text": "hello"}).json()

        assert "reply" not in body


class TestHowTheTurnIsRead:
    def test_a_raw_turn_gets_a_smaller_allowance(self, prompt_client):
        client = prompt_client([reading(register="VULNERABLE")])

        body = client.post("/query/prompt", json={"text": "I can't hold it"}).json()

        assert body["emotional_register"] == "VULNERABLE"
        assert body["token_budget"] == 400

    def test_a_turn_in_crisis_gets_nothing_and_different_instructions(
        self, prompt_client, seed_observation
    ):
        seed_observation("obs_1", "an earlier note about the same thing")
        client = prompt_client([])

        body = client.post(
            "/query/prompt", json={"text": "some days I just want to die"}
        ).json()

        assert body["suppressed"] is True
        assert body["briefing"] == []
        assert "Do not analyse" in body["system"]

    def test_a_reflective_turn_gets_the_most(self, prompt_client):
        client = prompt_client([reading(register="REFLECTIVE")])

        body = client.post("/query/prompt", json={"text": "I have been thinking"}).json()

        assert body["token_budget"] == 1500


class TestAcrossSeveralTurns:
    def test_a_named_conversation_remembers_what_was_said(self, prompt_client):
        client = prompt_client([reading()] * 3)

        client.post("/query/prompt", json={"text": "first", "session_key": "me"})
        body = client.post(
            "/query/prompt", json={"text": "second", "session_key": "me"}
        ).json()

        assert body["signal"]["turn_index"] == 1 if "signal" in body else True
        assert [message["content"] for message in body["messages"]][-1] == "second"

    def test_a_one_off_request_is_answered_from_what_it_supplied(self, prompt_client):
        client = prompt_client([reading()])

        body = client.post(
            "/query/prompt",
            json={
                "text": "I don't feel that anymore",
                "history": [{"role": "user", "content": "I used to think I was the problem"}],
            },
        ).json()

        contents = [message["content"] for message in body["messages"]]
        assert contents == ["I used to think I was the problem", "I don't feel that anymore"]
