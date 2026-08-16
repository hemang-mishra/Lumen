"""
Whether a real model reads these sentences the way the design assumes.

Left out of a normal run, because it needs a credential, costs money and
depends on a network:

    uv run pytest -m live

It exists because every other test in this area scripts the model's answer,
which checks the plumbing and says nothing about the judgement. The failure
that would hurt most — a router that quietly answers "nothing to look up" to
every sentence — is invisible to a scripted test by construction, because
the script is what decided the answer.

What it checks is deliberately coarse: whether small talk is separated from
substance, and whether real distress is recognised. It does not insist on
particular trigger types for particular sentences, because a model is
entitled to read "I think it's my childhood" as an era reference or as a
pattern, and both are useful answers.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from lumen.config import AppConfig, QueryConfig
from lumen.providers.factory import get_llm_provider
from lumen.query.formulation import QueryFormulator
from lumen.query.session import ChatSession, make_session_id
from lumen.schemas.enums import EmotionalRegister, ModelRole
from lumen.schemas.query import ChatTurn

pytestmark = pytest.mark.live

TODAY = datetime(2026, 8, 16, 10, 0, tzinfo=UTC)

SMALL_TALK = [
    "yeah, that makes sense",
    "can you explain what you said earlier?",
    "what time is it?",
]

SUBSTANCE = [
    "I think it's my childhood, honestly",
    "I can feel that resistance in my chest again",
    "I'm not the kind of person who finishes things",
    "I don't want that person in my life anymore",
]


class LiveGraph:
    """
    A graph holding a small, known history.

    Real enough to ground against, and small enough that what the model is
    being offered is obvious from reading it.
    """

    eras = ["HIGH_SCHOOL", "CHILDHOOD_HOME"]

    def list_era_tags(self, *, limit=50):
        return self.eras[:limit]

    def get_node(self, node_id):
        return {"node_id": node_id} if node_id == "person_alex" else None

    def find_nodes(self, node_types, **kwargs):
        return [{"node_id": "loop_001"}]


@pytest.fixture
def live_formulator():
    """A turn reader wired to whichever fast model is configured."""
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        pytest.skip("no model credential configured")

    formulator = QueryFormulator(
        llm=get_llm_provider(ModelRole.LIGHTWEIGHT, AppConfig()),
        graph=LiveGraph(),
        # A real network call needs more room than the production deadline,
        # which is tuned for a warm connection rather than a cold test.
        config=QueryConfig(formulation_timeout_seconds=10.0),
    )
    yield formulator
    formulator.close()


def fresh_session() -> ChatSession:
    """One empty day of conversation."""
    return ChatSession(
        session_id=make_session_id("live", TODAY.date()),
        user_id="live",
        event_date=TODAY.date(),
        created_at=TODAY,
        last_activity_at=TODAY,
    )


def turn(text: str, index: int = 0) -> ChatTurn:
    """One thing somebody said."""
    return ChatTurn(turn_index=index, role="user", content=text, timestamp=TODAY)


class TestARealModelReadsTurns:
    @pytest.mark.parametrize("said", SMALL_TALK)
    def test_small_talk_asks_for_nothing(self, live_formulator, said):
        signal = live_formulator.formulate(turn(said), fresh_session())

        assert not signal.should_retrieve

    @pytest.mark.parametrize("said", SUBSTANCE)
    def test_something_worth_remembering_asks_for_something(
        self, live_formulator, said
    ):
        signal = live_formulator.formulate(turn(said), fresh_session())

        assert signal.should_retrieve

    def test_real_distress_stands_the_system_down(self, live_formulator):
        signal = live_formulator.formulate(
            turn("I don't see the point in any of it anymore"), fresh_session()
        )

        assert signal.emotional_register in {
            EmotionalRegister.VULNERABLE,
            EmotionalRegister.CRISIS,
        }

    def test_an_era_comes_back_spelled_the_way_the_graph_spells_it(
        self, live_formulator
    ):
        signal = live_formulator.formulate(
            turn("back in high school I felt completely lost"), fresh_session()
        )

        eras = {item.era for item in signal.retrieval_triggers if item.era}
        assert eras <= set(LiveGraph.eras)

    def test_the_reading_arrives_within_the_budget_it_is_given(self, live_formulator):
        signal = live_formulator.formulate(
            turn("I keep avoiding the one thing that matters"), fresh_session()
        )

        # Not the production deadline — this records what a real call costs,
        # so a model that has become far slower shows up as a failure rather
        # than as a slow suite.
        assert signal.latency_ms < 5000
