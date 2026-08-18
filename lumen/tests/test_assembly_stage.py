"""
Deciding how much of somebody's history this turn carries.

The allowance is set by how the person sounds, and the four cases are not
variations on one rule — they are four different judgements. Crisis is zero.
Raw is small, unquoted and limited to settled patterns rather than notes
about single bad evenings. Ordinary is moderate. Thinking-it-through is
generous.

The rest is about what gets cut and why: repeats, too many of one kind, and
the budget itself.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lumen.config import ChatConfig
from lumen.query.assembly import ContextAssembler, select
from lumen.query.assembly.budget import estimate_tokens, policy_for
from lumen.query.retrieval.contracts import (
    PassReport,
    RetrievalBundle,
    RetrievedNode,
)
from lumen.schemas.enums import EmotionalRegister, RetrievalPass, RetrievalOutcome
from lumen.schemas.query import RetrievalSignal

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def found(
    node_id: str = "n_1",
    *,
    node_type: str = "PatternNode",
    preview: str = "avoiding going places alone",
    score: float = 0.8,
    when: datetime | None = None,
    **properties,
) -> RetrievedNode:
    """One record as retrieval hands it over."""
    return RetrievedNode(
        node_id=node_id,
        node_type=node_type,
        preview=preview,
        found_by=RetrievalPass.SEMANTIC,
        similarity=min(score, 1.0),
        occurred_at=when,
        rank_score=score,
        properties=properties,
    )


KINDS = ["PatternNode", "BeliefNode", "OpenLoopNode", "LessonNode"]


def a_varied_history(count: int) -> list[RetrievedNode]:
    """
    Several unrelated records of different kinds.

    Varied on purpose: a briefing built from eight patterns is the exact
    thing the per-kind cap exists to prevent, so a test about the *count*
    allowance has to hand over a mixture or it ends up testing the cap.
    """
    subjects = [
        "avoiding going places alone",
        "measuring himself against people ahead of him",
        "whether rest has to be earned",
        "leaving conversations before they finish",
        "the evening walk that fixes nothing and helps",
        "saying yes when he means later",
        "the first ten minutes being the whole problem",
        "counting hours instead of counting progress",
    ]
    return [
        found(f"n_{index}", node_type=KINDS[index % len(KINDS)], preview=subject)
        for index, subject in enumerate(subjects[:count])
    ]


def bundle(*nodes: RetrievedNode) -> RetrievalBundle:
    return RetrievalBundle(
        session_id="tester_2026_08_17",
        turn_index=0,
        outcome=RetrievalOutcome.RETRIEVED if nodes else RetrievalOutcome.NOTHING,
        candidates=tuple(nodes),
    )


def signal(register: EmotionalRegister = EmotionalRegister.STABLE) -> RetrievalSignal:
    return RetrievalSignal(
        session_id="tester_2026_08_17", turn_index=0, emotional_register=register
    )


@pytest.fixture
def assemble():
    """Assemble a briefing at a fixed moment, so dates can be checked."""

    def _assemble(*nodes, register=EmotionalRegister.STABLE, **settings):
        assembler = ContextAssembler(config=ChatConfig(**settings))
        return assembler.assemble(bundle(*nodes), signal(register), now=NOW)

    return _assemble


class TestHowMuchEachMomentAllows:
    def test_an_ordinary_turn_gets_a_moderate_briefing(self, assemble):
        context = assemble(*a_varied_history(8))

        assert len(context.items) == 4
        assert context.token_budget == 800

    def test_somebody_thinking_out_loud_gets_more(self, assemble):
        context = assemble(
            *a_varied_history(8), register=EmotionalRegister.REFLECTIVE
        )

        assert len(context.items) == 6
        assert context.token_budget == 1500

    def test_somebody_raw_gets_less(self, assemble):
        context = assemble(
            *a_varied_history(8), register=EmotionalRegister.VULNERABLE
        )

        assert len(context.items) == 2

    def test_somebody_in_crisis_gets_nothing_at_all(self, assemble):
        context = assemble(
            found("n_1"), found("n_2"), register=EmotionalRegister.CRISIS
        )

        assert context.items == ()
        assert context.token_budget == 0

    def test_and_that_is_recorded_as_withheld_rather_than_absent(self, assemble):
        # "There was nothing to say" and "there was plenty and this was not
        # the moment" are different facts, and only one is about the graph.
        context = assemble(found("n_1"), register=EmotionalRegister.CRISIS)

        assert context.suppressed is True

    def test_an_empty_turn_is_not_recorded_as_withheld(self, assemble):
        context = assemble()

        assert context.is_empty is True
        assert context.suppressed is False


class TestWhenTheyAreRaw:
    def test_nothing_is_quoted_back_at_them(self, assemble):
        context = assemble(
            found(node_type="BeliefNode", preview="I always fall short"),
            register=EmotionalRegister.VULNERABLE,
        )

        assert '"' not in context.items[0].text

    def test_only_settled_records_are_offered(self, assemble):
        # The useful thing at that moment is the shape of a recurring
        # problem, not a transcript of the last time it happened.
        context = assemble(
            found("obs_1", node_type="ObservationNode", preview="a bad evening", score=0.9),
            found("pat_1", node_type="PatternNode", preview="the recurring shape", score=0.5),
            register=EmotionalRegister.VULNERABLE,
        )

        assert [item.node_id for item in context.items] == ["pat_1"]
        assert context.dropped[0].reason == select.NOT_SETTLED_ENOUGH

    def test_ordinary_turns_are_not_limited_that_way(self, assemble):
        context = assemble(
            found("obs_1", node_type="ObservationNode", preview="a bad evening")
        )

        assert [item.node_id for item in context.items] == ["obs_1"]


class TestWhatGetsCut:
    def test_the_same_thing_said_twice_is_offered_once(self, assemble):
        # A strong theme will happily produce four near-identical records and
        # fill the whole allowance with variations on itself.
        context = assemble(
            found("n_1", preview="avoiding going places alone", score=0.9),
            found("n_2", preview="avoiding going places alone", score=0.8),
        )

        assert [item.node_id for item in context.items] == ["n_1"]
        assert context.dropped[0].reason == select.DUPLICATE

    def test_two_genuinely_different_things_both_get_through(self, assemble):
        context = assemble(
            found("n_1", preview="avoiding going places alone"),
            found("n_2", preview="pushing through work deadlines regardless"),
        )

        assert len(context.items) == 2

    def test_too_many_of_one_kind_is_refused(self, assemble):
        # Six patterns and nothing else is a worse briefing than three
        # patterns and a belief, even when the six rank higher.
        context = assemble(
            found("n_1", preview="avoiding going places alone"),
            found("n_2", preview="leaving conversations early"),
            found("n_3", preview="counting hours instead of progress"),
            register=EmotionalRegister.REFLECTIVE,
            per_kind_cap=2,
        )

        assert len(context.items) == 2
        assert context.dropped[0].reason == select.TOO_MANY_OF_A_KIND

    def test_the_cap_leaves_room_for_a_different_kind(self, assemble):
        # The point of the cap: variety, not scarcity. A belief still gets in
        # behind two patterns.
        context = assemble(
            found("pat_1", preview="avoiding going places alone"),
            found("pat_2", preview="leaving conversations early"),
            found("pat_3", preview="counting hours instead of progress"),
            found("bel_1", node_type="BeliefNode", preview="rest has to be earned"),
            register=EmotionalRegister.REFLECTIVE,
            per_kind_cap=2,
        )

        assert [item.node_id for item in context.items] == ["pat_1", "pat_2", "bel_1"]

    def test_the_token_budget_is_respected(self, assemble):
        long_preview = "a very long recollection " * 20
        context = assemble(
            found("n_1", preview=long_preview),
            found("n_2", preview="something else entirely, quite different"),
            stable_tokens=40,
        )

        assert context.estimated_tokens <= 40
        assert any(item.reason == select.OVER_BUDGET for item in context.dropped)

    def test_what_was_cut_is_named_with_the_rule_that_cut_it(self, assemble):
        # A briefing that disappoints is usually explained by what is
        # missing, and that is invisible otherwise.
        context = assemble(*a_varied_history(8))

        assert {item.reason for item in context.dropped} == {select.OVER_COUNT}


class TestOrdering:
    def test_the_best_scoring_comes_first(self, assemble):
        context = assemble(
            found("n_low", preview="a lesser thing", score=0.2),
            found("n_high", preview="the main thing", score=0.9),
        )

        assert [item.node_id for item in context.items] == ["n_high", "n_low"]

    def test_a_tie_is_settled_towards_the_more_recent(self, assemble):
        # Between two equally relevant things, the live one is the one worth
        # mentioning. Not a decay curve — that has its own goal.
        context = assemble(
            found("n_old", preview="an older thing", score=0.5, when=NOW - timedelta(days=300)),
            found("n_new", preview="a newer thing", score=0.5, when=NOW - timedelta(days=2)),
        )

        assert [item.node_id for item in context.items] == ["n_new", "n_old"]

    def test_a_record_with_no_date_only_ever_loses_a_tie(self, assemble):
        context = assemble(
            found("n_dated", preview="a dated thing", score=0.5, when=NOW),
            found("n_undated", preview="an undated thing", score=0.5),
        )

        assert [item.node_id for item in context.items] == ["n_dated", "n_undated"]


class TestWhatComesBack:
    def test_each_line_can_be_traced_to_its_record(self, assemble):
        context = assemble(found("pat_alone"))

        assert context.items[0].node_id == "pat_alone"

    def test_a_carried_record_is_still_marked_as_carried(self, assemble):
        carried = found("n_1").model_copy(update={"boosted": True})

        context = assemble(carried)

        assert context.items[0].boosted is True

    def test_what_was_spent_is_reported(self, assemble):
        context = assemble(found("n_1", preview="avoiding going places alone"))

        assert context.estimated_tokens == context.items[0].tokens
        assert context.estimated_tokens > 0

    def test_a_deferred_briefing_says_so(self, assemble):
        assembler = ContextAssembler()

        context = assembler.assemble(
            bundle(found("n_1")), signal(), now=NOW, deferred=True
        )

        assert context.deferred is True


class TestTheEstimate:
    def test_it_grows_with_the_text(self):
        assert estimate_tokens("x" * 400) > estimate_tokens("x" * 40)

    def test_nothing_costs_nothing(self):
        assert estimate_tokens("") == 0

    def test_it_rounds_up_rather_than_down(self):
        # Erring towards "bigger than it is" costs a sentence. Erring the
        # other way costs a truncated prompt.
        assert estimate_tokens("abc", chars_per_token=4.0) == 1

    def test_a_nonsense_setting_cannot_make_it_divide_by_nothing(self):
        assert estimate_tokens("abcd", chars_per_token=0.0) == 4


class TestThePolicies:
    def test_every_way_somebody_can_sound_has_an_allowance(self):
        for register in EmotionalRegister:
            assert policy_for(register) is not None

    def test_only_crisis_allows_nothing(self):
        allowed = {
            register
            for register in EmotionalRegister
            if policy_for(register).injects_anything
        }

        assert allowed == set(EmotionalRegister) - {EmotionalRegister.CRISIS}


class TestComparingTwoBriefings:
    def test_identical_text_is_a_complete_overlap(self):
        assert select.overlap("the same words here", "the same words here") == 1.0

    def test_unrelated_text_overlaps_barely_at_all(self):
        assert select.overlap("avoiding going out", "deadlines at work") < 0.3

    def test_a_short_line_inside_a_longer_one_counts_as_a_repeat(self):
        # Measured against the shorter of the two, which is the case worth
        # catching: one briefing that says everything another does, plus more.
        score = select.overlap(
            "avoiding solo trips",
            "avoiding solo trips whenever the weekend comes around",
        )

        assert score == 1.0

    def test_two_empty_lines_do_not_count_as_the_same_thing(self):
        assert select.overlap("", "") == 0.0


class TestCarryingTheFailureForward:
    """
    Retrieval keeps four empty answers apart and this is where they were
    being collapsed back into one. The briefing is empty in all of them; only
    one of them means the person has no such history.
    """

    @staticmethod
    def _unreachable(which=RetrievalPass.SEMANTIC) -> RetrievalBundle:
        return RetrievalBundle(
            session_id="tester_2026_08_17",
            turn_index=0,
            outcome=RetrievalOutcome.UNAVAILABLE,
            passes=(PassReport(which=which, ran=True, failure="SearchUnavailable"),),
        )

    @staticmethod
    def _assembled(bundle_in, register=EmotionalRegister.STABLE):
        return ContextAssembler(config=ChatConfig()).assemble(
            bundle_in, signal(register), now=NOW
        )

    def test_a_failed_search_is_marked_as_one(self):
        context = self._assembled(self._unreachable())

        assert context.search_failed is True
        assert context.is_empty

    def test_a_search_that_found_nothing_is_not(self):
        assert self._assembled(bundle()).search_failed is False

    def test_a_search_that_found_something_is_not(self):
        assert self._assembled(bundle(found())).search_failed is False

    def test_it_survives_the_crisis_path_too(self):
        # Crisis returns early on a different branch, and the fact still has
        # to be recorded — the prompt decides separately not to act on it.
        context = self._assembled(
            self._unreachable(RetrievalPass.STRUCTURAL), EmotionalRegister.CRISIS
        )

        assert context.search_failed is True
        assert context.suppressed is True
