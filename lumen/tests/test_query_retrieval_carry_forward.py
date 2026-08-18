"""
A search that finished after the turn had already been answered.

A running thread cannot be stopped, so a search that misses its deadline goes
on and produces an answer nobody is waiting for. Throwing it away means
paying for the work and getting nothing; keeping it means the next turn opens
with what the last one could not wait for.

Two things stop that being merely thrifty. What comes back is checked against
the sensitivity rules rather than trusted because it was fetched once — the
pass that would have checked it never finished. And it is only ever carried
one turn, because history about a question already left behind pulls the
conversation backwards.
"""

from __future__ import annotations

import time

import pytest

from lumen.config import QueryConfig
from lumen.providers.fake import FakeLLMProvider
from lumen.query.session import LateArrival, Mailbox
from lumen.schemas.enums import Domain, RetrievalPass, SignalStrength, TriggerType


class TestTheMailbox:
    def test_what_is_left_can_be_collected(self):
        box = Mailbox()
        box.leave(LateArrival(turn_index=1, candidates=("a",)))

        assert box.collect().turn_index == 1

    def test_collecting_empties_it(self):
        box = Mailbox()
        box.leave(LateArrival(turn_index=1, candidates=("a",)))
        box.collect()

        assert box.collect() is None

    def test_an_empty_slot_is_falsey(self):
        assert not Mailbox()

    def test_a_newer_answer_replaces_an_older_one(self):
        """
        One slot rather than a queue. A backlog of late answers is a system
        quietly falling further behind, and only the newest is still about
        roughly what is being talked about.
        """
        box = Mailbox()
        box.leave(LateArrival(turn_index=1, candidates=("old",)))
        box.leave(LateArrival(turn_index=2, candidates=("new",)))

        assert box.collect().candidates == ("new",)


class TestHowLongOneIsWorthKeeping:
    def test_the_very_next_turn_still_wants_it(self):
        assert LateArrival(turn_index=3, candidates=()).is_stale(4) is False

    def test_two_turns_later_is_too_late(self):
        assert LateArrival(turn_index=3, candidates=()).is_stale(5) is True

    def test_the_same_turn_is_obviously_fine(self):
        assert LateArrival(turn_index=3, candidates=()).is_stale(3) is False


class SlowThenFast:
    """A model that is too slow once and then answers normally."""

    provider_name = "slow-then-fast"
    model_name = "test"

    def __init__(self, delay: float, reply: str) -> None:
        self._delay = delay
        self._reply = reply
        self.calls = 0

    def generate_structured(self, prompt, response_model, **kwargs):
        self.calls += 1
        if self.calls == 1:
            time.sleep(self._delay)
        return FakeLLMProvider([self._reply]).generate_structured(
            prompt, response_model, **kwargs
        )


class TestCarryingItIntoTheNextTurn:
    def test_a_late_search_is_used_on_the_following_turn(
        self,
        make_retriever,
        chat_session,
        make_signal,
        make_trigger,
        seed_observation,
        hyde_replies,
    ):
        seed_observation("obs_1", "the invented record")
        retriever = make_retriever(
            llm=hyde_replies(["the invented record"]),
            config=QueryConfig(retrieval_budget_seconds=0.0),
        )
        first = make_signal(make_trigger(TriggerType.PATTERN_MENTION), turn_index=0)

        missed = retriever.retrieve(first, chat_session)
        assert missed.candidates == ()

        # Give the abandoned search time to land in the slot.
        for _ in range(50):
            if chat_session.late_arrivals:
                break
            time.sleep(0.02)

        second = retriever.retrieve(
            make_signal(make_trigger(TriggerType.PATTERN_MENTION), turn_index=1),
            chat_session,
        )

        assert "obs_1" in second.carried_forward

    def test_what_is_carried_is_ranked_below_anything_fresh(
        self, make_retriever, chat_session, make_signal, make_trigger, hyde_replies
    ):
        from lumen.query.retrieval.contracts import RetrievedNode

        carried = RetrievedNode(
            node_id="pat_late",
            node_type="PatternNode",
            preview="something found too late",
            found_by=RetrievalPass.SEMANTIC,
            rank_score=1.0,
        )
        chat_session.late_arrivals.leave(
            LateArrival(turn_index=0, candidates=(carried,))
        )

        bundle = make_retriever(llm=hyde_replies()).retrieve(
            make_signal(make_trigger(TriggerType.PATTERN_MENTION), turn_index=1),
            chat_session,
        )

        found = {node.node_id: node for node in bundle.candidates}
        assert found["pat_late"].rank_score == pytest.approx(0.9)

    def test_one_that_is_too_old_is_dropped(
        self, make_retriever, chat_session, make_signal, make_trigger, hyde_replies
    ):
        from lumen.query.retrieval.contracts import RetrievedNode

        chat_session.late_arrivals.leave(
            LateArrival(
                turn_index=0,
                candidates=(
                    RetrievedNode(
                        node_id="pat_stale",
                        node_type="PatternNode",
                        preview="about something already left behind",
                        found_by=RetrievalPass.SEMANTIC,
                        rank_score=1.0,
                    ),
                ),
            )
        )

        bundle = make_retriever(llm=hyde_replies()).retrieve(
            make_signal(make_trigger(TriggerType.PATTERN_MENTION), turn_index=5),
            chat_session,
        )

        assert bundle.carried_forward == ()

    def test_it_is_checked_against_the_sensitivity_rules_again(
        self, make_retriever, chat_session, make_signal, make_trigger, hyde_replies
    ):
        """
        The pass it came from never finished, so it was never gated at all —
        and what the person has opened up may have changed since.
        """
        from lumen.query.retrieval.contracts import RetrievedNode

        chat_session.late_arrivals.leave(
            LateArrival(
                turn_index=0,
                candidates=(
                    RetrievedNode(
                        node_id="bel_heavy",
                        node_type="BeliefNode",
                        preview="the heaviest thing in here",
                        found_by=RetrievalPass.SEMANTIC,
                        signal_strength=SignalStrength.CRITICAL,
                        domain=Domain.SELF_CONCEPT,
                        rank_score=1.0,
                    ),
                ),
            )
        )

        bundle = make_retriever(llm=hyde_replies()).retrieve(
            make_signal(make_trigger(TriggerType.PATTERN_MENTION), turn_index=1),
            chat_session,
        )

        assert "bel_heavy" in bundle.gated
        assert bundle.candidates == ()

    def test_a_briefing_built_from_one_says_it_is_slightly_behind(
        self, make_retriever, chat_session, make_signal, make_trigger, hyde_replies
    ):
        from lumen.config import ChatConfig
        from lumen.query.assembly import ContextAssembler
        from lumen.query.retrieval.contracts import RetrievedNode

        chat_session.late_arrivals.leave(
            LateArrival(
                turn_index=0,
                candidates=(
                    RetrievedNode(
                        node_id="pat_late",
                        node_type="PatternNode",
                        preview="found a moment too late",
                        found_by=RetrievalPass.SEMANTIC,
                        rank_score=1.0,
                    ),
                ),
            )
        )
        signal = make_signal(
            make_trigger(TriggerType.PATTERN_MENTION), turn_index=1
        )
        bundle = make_retriever(llm=hyde_replies()).retrieve(signal, chat_session)

        context = ContextAssembler(config=ChatConfig()).assemble(bundle, signal)

        assert context.deferred is True
