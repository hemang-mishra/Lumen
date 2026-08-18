"""
Everything the assistant is sent, for one turn.

This is the goal's actual deliverable: given what was fetched, how the person
sounds, and what the conversation has been about, here is exactly what would
go to the model. The tests are about the joins — that each part arrives, that
crisis changes all of them together, and that the cost of the whole thing is
reported rather than only the briefing's share.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lumen.config import ChatConfig
from lumen.query.memory.contracts import Recollection
from lumen.query.prompting import PromptComposer
from lumen.query.retrieval.contracts import RetrievalBundle, RetrievedNode
from lumen.schemas.enums import (
    EmotionalRegister,
    RetrievalOutcome,
    RetrievalPass,
)
from lumen.schemas.query import ChatTurn, RetrievalSignal

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def found(preview: str = "avoiding going places alone") -> RetrievedNode:
    return RetrievedNode(
        node_id="pat_1",
        node_type="PatternNode",
        preview=preview,
        found_by=RetrievalPass.SEMANTIC,
        similarity=0.8,
        rank_score=0.8,
        properties={"evidence_count": 4},
    )


def turn(content: str, index: int = 0, role: str = "user") -> ChatTurn:
    return ChatTurn(turn_index=index, role=role, content=content, timestamp=NOW)


@pytest.fixture
def compose():
    """Build the whole prompt for one turn."""

    def _compose(
        *nodes,
        register=EmotionalRegister.STABLE,
        summary=None,
        turns=(),
        deferred=False,
        **settings,
    ):
        composer = PromptComposer(config=ChatConfig(**settings))
        return composer.compose(
            bundle=RetrievalBundle(
                session_id="tester_2026_08_17",
                turn_index=0,
                outcome=RetrievalOutcome.RETRIEVED if nodes else RetrievalOutcome.NOTHING,
                candidates=tuple(nodes),
            ),
            signal=RetrievalSignal(
                session_id="tester_2026_08_17",
                turn_index=0,
                emotional_register=register,
            ),
            recollection=Recollection(
                summary=summary, turns=tuple(turns), total_turns=len(turns)
            ),
            now=NOW,
            deferred=deferred,
        )

    return _compose


class TestWhatArrives:
    def test_the_instructions_are_there(self, compose):
        assert "You are Lumen" in compose().system

    def test_their_history_is_there(self, compose):
        prompt = compose(found())

        assert "avoiding going places alone" in prompt.system
        assert "Seen 4 times" in prompt.system

    def test_the_conversation_so_far_is_there(self, compose):
        prompt = compose(summary="They came in about work.", turns=[turn("a", 0)] * 1)

        assert "They came in about work." in prompt.system
        assert prompt.summary == "They came in about work."

    def test_the_recent_turns_are_there(self, compose):
        prompt = compose(turns=[turn("I keep putting it off", 0)])

        assert [message.content for message in prompt.messages] == [
            "I keep putting it off"
        ]

    def test_the_briefing_can_be_traced_back(self, compose):
        # Every line the assistant reads has a record behind it, so "is this
        # a fair summary of that?" is answerable.
        prompt = compose(found())

        assert prompt.context.items[0].node_id == "pat_1"


class TestWhenSomebodyIsInCrisis:
    def test_no_history_reaches_the_assistant(self, compose):
        prompt = compose(found(), register=EmotionalRegister.CRISIS)

        assert "avoiding going places alone" not in prompt.system
        assert prompt.context.items == ()

    def test_and_the_instructions_change_with_it(self, compose):
        prompt = compose(found(), register=EmotionalRegister.CRISIS)

        assert "Do not analyse" in prompt.system
        assert "How to be with them" not in prompt.system

    def test_the_conversation_so_far_is_left_out_too(self, compose):
        # Somebody in the middle of a bad ten minutes does not need the last
        # hour reflected back at them.
        prompt = compose(
            register=EmotionalRegister.CRISIS,
            summary="They came in about work.",
            turns=[turn("a", 0)],
        )

        assert "They came in about work." not in prompt.system
        assert prompt.summary is None

    def test_what_they_just_said_still_reaches_it(self, compose):
        # Withholding the history is the point. Withholding the person would
        # be absurd.
        prompt = compose(
            register=EmotionalRegister.CRISIS, turns=[turn("I can't do this", 0)]
        )

        assert [message.content for message in prompt.messages] == ["I can't do this"]

    def test_it_is_recorded_as_withheld(self, compose):
        prompt = compose(found(), register=EmotionalRegister.CRISIS)

        assert prompt.context.suppressed is True


class TestTheCost:
    def test_the_whole_prompt_is_measured_and_not_just_the_briefing(self, compose):
        # A briefing that fits its own allowance can still sit inside an
        # enormous prompt if the conversation behind it is long.
        prompt = compose(found(), turns=[turn("x" * 400, index) for index in range(4)])

        assert prompt.estimated_tokens > prompt.context.estimated_tokens

    def test_an_empty_turn_still_costs_the_instructions(self, compose):
        assert compose().estimated_tokens > 0


class TestCarriedForward:
    def test_a_briefing_from_the_previous_turn_says_so(self, compose):
        # Context about a moment that has passed should not be read as though
        # it were about this one.
        prompt = compose(found(), deferred=True)

        assert prompt.context.deferred is True
        assert "slightly behind the conversation" in prompt.system
