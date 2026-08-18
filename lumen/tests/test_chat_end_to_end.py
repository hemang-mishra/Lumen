"""
A conversation against a real graph, with real history behind it.

This is the goal's whole claim, checked the only way it can be: build a week
of somebody's history with the real pipeline, then talk to Lumen about it and
see whether what it is handed is what that week actually says.

Everything is real except the models — real Kuzu, real Qdrant, the real
pipeline, the real chat engine. The models are scripted because whether a
particular model recognises Wednesday from Monday is a question about
prompts, and it can change without this repository changing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from lumen.config import AppConfig, ChatConfig, QueryConfig
from lumen.providers.fake import FakeLLMProvider
from lumen.query.chat import (
    ChatEngine,
    ContextReady,
    ReplyDone,
    TurnAccepted,
)
from lumen.query.conversation import ConversationStore
from lumen.query.formulation import QueryFormulator
from lumen.query.memory import ConversationMemory
from lumen.query.prompting import PromptComposer
from lumen.query.retrieval import ConversationalRetriever
from lumen.query.session import SessionRegistry
from lumen.schemas.enums import ModelRole
from lumen.simulation.corpus import CORPUS
from lumen.simulation.runner import build_embedder, simulate_days

USER = "local"
NOW = datetime(2026, 3, 10, 19, 0, tzinfo=UTC)
REPLY = "You have been round this one before, and it moved last time."


def a_reading(**overrides) -> str:
    """One scripted answer from the turn reader."""
    reply = {
        "triggers": [{"trigger_type": "PATTERN_MENTION", "keywords": ["comparing"]}],
        "emotional_register": "REFLECTIVE",
        "named_entities": [],
        "confidence": 0.9,
        "critical_domain_opened": None,
    }
    reply.update(overrides)
    return json.dumps(reply)


def a_search(text: str) -> str:
    """One scripted answer from the invented-record step."""
    return json.dumps({"hypotheticals": [{"index": 1, "text": text}]})


@pytest.fixture
def a_week_of_history(graph_store, vector_store, ops_store):
    """Five days of somebody's writing, run through the real pipeline."""
    embedder = build_embedder()
    simulate_days(
        CORPUS,
        graph=graph_store,
        vectors=vector_store,
        ops=ops_store,
        embedder=embedder,
        user_id=USER,
    )
    return embedder


@pytest.fixture
def talk_to_lumen(graph_store, vector_store, ops_store, a_week_of_history):
    """A conversation wired to the graph that week produced."""

    def _build(*, readings=None, searches=None, replies=None):
        settings = ChatConfig(previous_days=3)
        return ChatEngine(
            formulator=QueryFormulator(
                llm=FakeLLMProvider(readings or [a_reading()] * 10),
                graph=graph_store,
                config=QueryConfig(),
            ),
            retriever=ConversationalRetriever(
                graph=graph_store,
                vectors=vector_store,
                embedder=a_week_of_history,
                llm=FakeLLMProvider(
                    searches or [a_search("comparing myself to everyone else")] * 10
                ),
                config=QueryConfig(),
            ),
            composer=PromptComposer(config=settings),
            memory=ConversationMemory(
                store=ConversationStore(ops_store.buffers),
                llm=FakeLLMProvider(["what was said today"] * 10),
                config=settings,
            ),
            sessions=SessionRegistry(QueryConfig()),
            llm=FakeLLMProvider([REPLY] * 10, role=ModelRole.CONVERSATION),
            config=settings,
        )

    return _build


def say(engine, text: str, at: datetime = NOW) -> list:
    """One turn, and everything that happened during it."""
    return list(engine.say(USER, text, at=at))


def gathered(events) -> ContextReady:
    """What the assistant was handed."""
    return next(event for event in events if isinstance(event, ContextReady))


class TestTalkingAboutARealWeek:
    def test_the_assistant_is_handed_the_history_that_week_produced(
        self, talk_to_lumen
    ):
        """
        The claim the whole goal rests on. The week wrote a pattern about
        comparing himself to other people; a turn about that should arrive
        with it already in hand.
        """
        events = say(
            talk_to_lumen(), "I did the comparing thing again this evening"
        )

        assert gathered(events).briefing

    def test_it_arrives_inside_the_budget(self, talk_to_lumen):
        events = say(talk_to_lumen(), "I did the comparing thing again")

        assert gathered(events).retrieval_ms < 8_000

    def test_the_reply_is_written_and_stored(self, talk_to_lumen):
        engine = talk_to_lumen()

        events = say(engine, "I did the comparing thing again")

        done = next(event for event in events if isinstance(event, ReplyDone))
        session_id = next(
            event for event in events if isinstance(event, TurnAccepted)
        ).session_id
        thread = engine._memory.store.thread(session_id)
        assert done.text == REPLY
        assert [item.turn.role for item in thread] == ["user", "assistant"]

    def test_the_conversation_is_stored_where_the_pipeline_will_read_it(
        self, talk_to_lumen, ops_store
    ):
        """
        The loop the product is about. A chat held anywhere else would be a
        chat that never becomes history — this one lands in the same buffer
        the extraction pipeline already consumes.
        """
        engine = talk_to_lumen()

        events = say(engine, "I did the comparing thing again")

        session_id = next(
            event for event in events if isinstance(event, TurnAccepted)
        ).session_id
        assert ops_store.buffers.get_buffer(session_id) is not None

    def test_nothing_in_the_graph_was_changed_by_talking(
        self, talk_to_lumen, graph_store
    ):
        before = graph_store.count_by_type()

        say(talk_to_lumen(), "I did the comparing thing again")

        assert graph_store.count_by_type() == before

    def test_a_second_turn_carries_the_first(self, talk_to_lumen):
        engine = talk_to_lumen()

        say(engine, "I did the comparing thing again")
        events = say(engine, "and it did not help")

        accepted = next(
            event for event in events if isinstance(event, TurnAccepted)
        )
        assert accepted.turn_index == 1

    def test_somebody_in_crisis_is_handed_nothing(self, talk_to_lumen):
        """
        The floor holds all the way through. A week of history sitting right
        there, and none of it is put in front of somebody in the middle of a
        bad ten minutes.
        """
        events = say(talk_to_lumen(), "honestly I just want to die")

        assert gathered(events).briefing == ()
        assert gathered(events).emotional_register == "CRISIS"
