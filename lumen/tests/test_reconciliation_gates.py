"""
Tests for the checks applied after a model answers.

This is where most of the value of the stage sits, because every one of
these rules exists to stop something that would otherwise look like success.
An over-eager merge, a change declared on the strength of one good day, a
low-confidence guess quietly recorded as a new discovery — none of them
raise, none of them fail a test elsewhere, and all of them are permanent.

The rules are checked one at a time and then in order, because the order is
part of the design: a tie is caught before a confidence bar, since two
readings at 0.92 and 0.90 are both confident and still a coin toss.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lumen.pipeline.reconciliation import gates
from lumen.pipeline.reconciliation.contracts import (
    ConfirmedDecision,
    GateRule,
    HistoricalNode,
    ItemDecision,
    ProposedAction,
)
from lumen.schemas.enums import (
    ModelRole,
    ObservationType,
    Provenance,
    ReconciliationAction,
    SignalStrength,
)

NOW = datetime(2026, 6, 11, 20, 0, tzinfo=UTC)


def historical(
    node_id: str = "pat_old",
    *,
    node_type: str = "PatternNode",
    age_days: int = 5,
) -> HistoricalNode:
    return HistoricalNode(
        node_id=node_id,
        node_type=node_type,
        preview="an existing record",
        valid_from=NOW - timedelta(days=age_days),
        row={"node_id": node_id, "_label": node_type},
    )


def context(*records: HistoricalNode, graph=None, anchor: bool = True):
    return gates.GateContext(
        history={record.node_id: record for record in records},
        graph=graph,
        has_causal_anchor=anchor,
        now=NOW,
    )


class CountingGraph:
    """A graph that only answers how many decisions were made before."""

    def __init__(self, count: int = 0, *, raises: bool = False) -> None:
        self.count = count
        self.raises = raises

    def count_prior_decisions(self, target_node_id, *, actions):
        if self.raises:
            raise RuntimeError("database gone")
        return self.count


def proposed(
    action: str,
    *,
    target: str | None = "pat_old",
    confidence: float = 0.95,
    runner_up: tuple[str, float] | None = None,
    **extra,
) -> ItemDecision:
    return ItemDecision(
        item_index=1,
        primary=ProposedAction(
            action=action,
            target_node_id=target,
            confidence=confidence,
            reason="because",
        ),
        runner_up=(
            ProposedAction(
                action=runner_up[0], target_node_id=target, confidence=runner_up[1]
            )
            if runner_up
            else None
        ),
        **extra,
    )


class TestReadingAnAnswer:
    def test_it_carries_the_reading_across(self, make_item, make_candidate):
        item = make_item(candidates=[make_candidate("pat_old")])

        decision = gates.interpret(
            item, proposed("MERGE"), verdict=None, model_used="fake-light"
        )

        assert decision.action is ReconciliationAction.MERGE
        assert decision.target_node_id == "pat_old"
        assert decision.target_type == "PatternNode"
        assert decision.is_refused is False

    def test_an_action_nobody_recognises_is_refused(self, make_item):
        # Picking the nearest real action would turn a model's typo into a
        # permanent change to someone's history.
        decision = gates.interpret(
            make_item(), proposed("MERGE_ISH"), verdict=None, model_used="fake"
        )

        assert decision.refusal is GateRule.UNKNOWN_ACTION

    def test_a_target_that_was_never_offered_is_ignored(self, make_item, make_candidate):
        # Otherwise a model could reconcile against a record the search
        # never surfaced, which nobody has checked is relevant.
        item = make_item(candidates=[make_candidate("pat_old")])

        decision = gates.interpret(
            item, proposed("MERGE", target="pat_invented"), verdict=None, model_used="f"
        )

        assert decision.target_node_id is None

    def test_confidence_outside_the_range_is_brought_back_in(self, make_item):
        decision = gates.interpret(
            make_item(), proposed("BRANCH", target=None, confidence=4.2),
            verdict=None,
            model_used="fake",
        )

        assert decision.confidence == 1.0

    def test_a_second_opinion_that_overrules_replaces_the_reading(
        self, make_item, make_candidate
    ):
        item = make_item(candidates=[make_candidate("pat_old")])
        verdict = ConfirmedDecision(
            item_index=1,
            confirmed=False,
            primary=ProposedAction(action="BRANCH", confidence=0.8),
        )

        decision = gates.interpret(
            item, proposed("EVOLVE"), verdict=verdict, model_used="fake-thinker"
        )

        assert decision.action is ReconciliationAction.BRANCH
        assert decision.escalated is True
        assert decision.model_used == "fake-thinker"

    def test_a_second_opinion_that_confirms_keeps_the_reading_and_its_wording(
        self, make_item, make_candidate
    ):
        item = make_item(candidates=[make_candidate("bel_old", node_type="BeliefNode")])
        verdict = ConfirmedDecision(
            item_index=1,
            confirmed=True,
            primary=ProposedAction(action="EVOLVE", confidence=0.94),
            delta_description="he no longer needs the reassurance",
        )

        decision = gates.interpret(
            item,
            proposed("EVOLVE", target="bel_old"),
            verdict=verdict,
            model_used="fake-thinker",
        )

        assert decision.action is ReconciliationAction.EVOLVE
        assert decision.delta_description == "he no longer needs the reassurance"
        assert decision.model_role is ModelRole.THINKING

    def test_a_finding_the_person_took_over_is_noted(self, make_item, make_candidate):
        item = make_item(
            provenance=Provenance.CO_CREATED,
            candidates=[make_candidate("bel_old", node_type="BeliefNode")],
        )

        decision = gates.interpret(
            item, proposed("EVOLVE", target="bel_old"), verdict=None, model_used="f"
        )

        assert decision.co_created_origin is True


class TestWhetherTheActionIsEvenPossible:
    def test_a_supported_action_passes(self, make_item, make_settled):
        decision = make_settled(ReconciliationAction.MERGE)

        assert gates.check_possible(decision, context(historical())).is_refused is False

    def test_an_unsupported_pairing_falls_to_the_second_reading(
        self, make_item, make_settled
    ):
        # An event cannot be "the same as" anything — there is no such link.
        # Reinforcing it can be.
        decision = make_settled(
            ReconciliationAction.MERGE,
            item=make_item(node_type="EventNode", node_id="evt_1"),
            runner_up=ReconciliationAction.REINFORCE,
            runner_up_confidence=0.85,
        )

        checked = gates.check_possible(decision, context(historical()))

        assert checked.action is ReconciliationAction.REINFORCE
        assert checked.confidence == 0.85
        assert checked.altered_by is GateRule.IMPOSSIBLE_ACTION
        assert checked.is_refused is False

    def test_it_waits_for_a_person_when_neither_reading_is_possible(
        self, make_item, make_settled
    ):
        decision = make_settled(
            ReconciliationAction.MERGE,
            item=make_item(node_type="EventNode", node_id="evt_1"),
        )

        assert (
            gates.check_possible(decision, context(historical())).refusal
            is GateRule.IMPOSSIBLE_ACTION
        )

    def test_an_action_needing_a_target_is_refused_without_one(self, make_settled):
        decision = make_settled(
            ReconciliationAction.MERGE, target_node_id=None, target_type=None
        )

        assert (
            gates.check_possible(decision, context()).refusal
            is GateRule.IMPOSSIBLE_ACTION
        )

    def test_a_tension_needs_a_finding_that_can_become_a_record(
        self, make_item, make_settled
    ):
        # A tension runs between two lasting records. A note about feeling
        # tired has nothing to put at its end.
        decision = make_settled(
            ReconciliationAction.DIALECTIC,
            item=make_item(observation_type=ObservationType.EMOTION),
            target_node_id="bel_old",
            target_type="BeliefNode",
        )

        assert (
            gates.check_possible(decision, context()).refusal
            is GateRule.IMPOSSIBLE_ACTION
        )


class TestTies:
    def test_two_close_readings_wait_for_a_person(self, make_settled):
        decision = make_settled(
            ReconciliationAction.MERGE,
            confidence=0.92,
            runner_up=ReconciliationAction.REINFORCE,
            runner_up_confidence=0.90,
        )

        checked = gates.check_tie(decision, context())

        assert checked.refusal is GateRule.TIE
        assert checked.action is ReconciliationAction.AMBIGUOUS

    def test_low_confidence_readings_that_are_close_are_also_a_tie(self, make_settled):
        # A tie is about the gap, not the height. Both of these mean the
        # model cannot tell the two readings apart.
        decision = make_settled(
            ReconciliationAction.MERGE,
            confidence=0.61,
            runner_up=ReconciliationAction.BRANCH,
            runner_up_confidence=0.59,
        )

        assert gates.check_tie(decision, context()).refusal is GateRule.TIE

    def test_a_clear_gap_is_not_a_tie(self, make_settled):
        decision = make_settled(
            ReconciliationAction.MERGE,
            confidence=0.92,
            runner_up=ReconciliationAction.REINFORCE,
            runner_up_confidence=0.60,
        )

        assert gates.check_tie(decision, context()).is_refused is False

    def test_the_same_action_twice_is_not_a_tie(self, make_settled):
        # Two readings that agree are agreement, however close the numbers.
        decision = make_settled(
            ReconciliationAction.MERGE,
            confidence=0.92,
            runner_up=ReconciliationAction.MERGE,
            runner_up_confidence=0.91,
        )

        assert gates.check_tie(decision, context()).is_refused is False

    def test_no_second_reading_is_not_a_tie(self, make_settled):
        assert gates.check_tie(make_settled(), context()).is_refused is False


class TestOneGoodDayIsNotAChange:
    def test_a_long_held_belief_resists_a_first_deviation(self, make_settled):
        decision = make_settled(
            ReconciliationAction.EVOLVE,
            target_node_id="bel_old",
            target_type="BeliefNode",
            confidence=0.95,
            delta_description="he did it alone today",
        )

        checked = gates.check_trial_not_trait(
            decision,
            context(
                historical("bel_old", node_type="BeliefNode", age_days=400),
                graph=CountingGraph(0),
            ),
        )

        assert checked.action is ReconciliationAction.BRANCH
        assert checked.altered_by is GateRule.TRIAL_NOT_TRAIT
        assert checked.runner_up_action is ReconciliationAction.EVOLVE

    def test_the_record_it_deviates_from_is_kept(self, make_settled):
        # This is what makes the second occasion countable. Without it the
        # first deviation leaves no trace against that belief and every
        # deviation is forever the first one.
        decision = make_settled(
            ReconciliationAction.EVOLVE,
            target_node_id="bel_old",
            target_type="BeliefNode",
            delta_description="once",
        )

        checked = gates.check_trial_not_trait(
            decision,
            context(
                historical("bel_old", node_type="BeliefNode", age_days=400),
                graph=CountingGraph(0),
            ),
        )

        assert checked.target_node_id == "bel_old"

    def test_a_second_deviation_is_allowed_through(self, make_settled):
        decision = make_settled(
            ReconciliationAction.EVOLVE,
            target_node_id="bel_old",
            target_type="BeliefNode",
            delta_description="again, and now consistently",
        )

        checked = gates.check_trial_not_trait(
            decision,
            context(
                historical("bel_old", node_type="BeliefNode", age_days=400),
                graph=CountingGraph(1),
            ),
        )

        assert checked.action is ReconciliationAction.EVOLVE

    def test_a_recent_record_is_not_protected(self, make_settled):
        decision = make_settled(
            ReconciliationAction.EVOLVE,
            target_node_id="bel_new",
            target_type="BeliefNode",
            delta_description="changed his mind quickly",
        )

        checked = gates.check_trial_not_trait(
            decision,
            context(
                historical("bel_new", node_type="BeliefNode", age_days=10),
                graph=CountingGraph(0),
            ),
        )

        assert checked.action is ReconciliationAction.EVOLVE

    def test_a_breakthrough_the_person_named_gets_through(self, make_item, make_settled):
        # Their own explicit self-awareness outranks the system's caution:
        # at that point they know something no amount of counting shows.
        decision = make_settled(
            ReconciliationAction.EVOLVE,
            item=make_item(
                observation_type=ObservationType.METACOGNITIVE_BREAKTHROUGH,
                signal=SignalStrength.CRITICAL,
            ),
            target_node_id="bel_old",
            target_type="BeliefNode",
            delta_description="he saw the whole pattern at once",
        )

        checked = gates.check_trial_not_trait(
            decision,
            context(
                historical("bel_old", node_type="BeliefNode", age_days=400),
                graph=CountingGraph(0),
            ),
        )

        assert checked.action is ReconciliationAction.EVOLVE

    def test_a_breakthrough_carrying_high_weight_also_bypasses(
        self, make_item, make_settled
    ):
        # Either of the two weights a breakthrough is allowed to have counts.
        # A breakthrough cannot be ordinary — the finding would not have been
        # accepted at extraction — so the type and the weight agree by
        # construction.
        decision = make_settled(
            ReconciliationAction.EVOLVE,
            item=make_item(
                observation_type=ObservationType.METACOGNITIVE_BREAKTHROUGH,
                signal=SignalStrength.HIGH,
            ),
            target_node_id="bel_old",
            target_type="BeliefNode",
            confidence=0.95,
            delta_description="a realisation",
        )

        checked = gates.check_trial_not_trait(
            decision,
            context(
                historical("bel_old", node_type="BeliefNode", age_days=400),
                graph=CountingGraph(0),
            ),
        )

        assert checked.action is ReconciliationAction.EVOLVE

    def test_near_certainty_still_gets_through(self, make_settled):
        decision = make_settled(
            ReconciliationAction.EVOLVE,
            target_node_id="bel_old",
            target_type="BeliefNode",
            confidence=0.99,
            delta_description="unmistakable",
        )

        checked = gates.check_trial_not_trait(
            decision,
            context(
                historical("bel_old", node_type="BeliefNode", age_days=400),
                graph=CountingGraph(0),
            ),
        )

        assert checked.action is ReconciliationAction.EVOLVE

    def test_a_graph_that_cannot_answer_keeps_the_cautious_path(self, make_settled):
        decision = make_settled(
            ReconciliationAction.EVOLVE,
            target_node_id="bel_old",
            target_type="BeliefNode",
            delta_description="once",
        )

        checked = gates.check_trial_not_trait(
            decision,
            context(
                historical("bel_old", node_type="BeliefNode", age_days=400),
                graph=CountingGraph(raises=True),
            ),
        )

        assert checked.action is ReconciliationAction.BRANCH

    def test_a_record_with_no_date_is_not_treated_as_old(self, make_settled):
        decision = make_settled(
            ReconciliationAction.EVOLVE,
            target_node_id="bel_old",
            target_type="BeliefNode",
            delta_description="changed",
        )
        undated = HistoricalNode(
            node_id="bel_old", node_type="BeliefNode", valid_from=None
        )

        checked = gates.check_trial_not_trait(
            decision, context(undated, graph=CountingGraph(0))
        )

        assert checked.action is ReconciliationAction.EVOLVE

    def test_safer_actions_are_left_alone(self, make_settled):
        decision = make_settled(ReconciliationAction.REINFORCE, confidence=0.85)

        checked = gates.check_trial_not_trait(
            decision, context(historical(age_days=400), graph=CountingGraph(0))
        )

        assert checked.action is ReconciliationAction.REINFORCE


class TestABadWeekIsNotANewPerson:
    def test_a_short_spike_is_recorded_on_its_own(self, make_settled):
        decision = make_settled(
            ReconciliationAction.EVOLVE,
            target_node_id="pat_old",
            confidence=0.96,
            delta_description="he is exhausted now",
            is_local_extremum=True,
        )

        checked = gates.check_local_extremum(decision, context(historical()))

        assert checked.action is ReconciliationAction.BRANCH
        assert checked.altered_by is GateRule.LOCAL_EXTREMUM

    def test_a_genuine_change_is_untouched(self, make_settled):
        decision = make_settled(
            ReconciliationAction.EVOLVE,
            target_node_id="pat_old",
            delta_description="settled into a different rhythm",
            is_local_extremum=False,
        )

        assert (
            gates.check_local_extremum(decision, context(historical())).action
            is ReconciliationAction.EVOLVE
        )


class TestAChangeNeedsACause:
    def test_a_change_with_nothing_behind_it_waits(self, make_settled):
        decision = make_settled(
            ReconciliationAction.EVOLVE, delta_description="changed"
        )

        checked = gates.check_causal_anchor(decision, context(anchor=False))

        assert checked.refusal is GateRule.NO_CAUSAL_ANCHOR

    def test_an_entry_with_a_session_is_fine(self, make_settled):
        decision = make_settled(
            ReconciliationAction.EVOLVE, delta_description="changed"
        )

        assert gates.check_causal_anchor(decision, context(anchor=True)).is_refused is False

    def test_other_actions_do_not_need_one(self, make_settled):
        decision = make_settled(ReconciliationAction.MERGE)

        assert gates.check_causal_anchor(decision, context(anchor=False)).is_refused is False


class TestActionsThatMustSaySomething:
    @pytest.mark.parametrize(
        "action",
        [
            ReconciliationAction.EVOLVE,
            ReconciliationAction.CONTRADICT,
            ReconciliationAction.DIALECTIC,
            ReconciliationAction.REGULATE,
        ],
    )
    def test_each_is_refused_without_its_sentence(self, action, make_settled):
        # Writing the sentence here would be the system inventing a claim
        # about someone's inner life to fill a blank.
        decision = make_settled(action, target_type="BeliefNode")

        assert (
            gates.check_required_wording(decision, context()).refusal
            is GateRule.MISSING_WORDING
        )

    def test_a_change_that_says_what_changed_passes(self, make_settled):
        decision = make_settled(
            ReconciliationAction.EVOLVE, delta_description="he stopped apologising"
        )

        assert gates.check_required_wording(decision, context()).is_refused is False

    def test_actions_that_need_no_sentence_pass(self, make_settled):
        assert (
            gates.check_required_wording(
                make_settled(ReconciliationAction.MERGE), context()
            ).is_refused
            is False
        )


class TestConfidence:
    @pytest.mark.parametrize(
        ("action", "just_under"),
        [
            (ReconciliationAction.MERGE, 0.87),
            (ReconciliationAction.REINFORCE, 0.79),
            (ReconciliationAction.BRANCH, 0.74),
            (ReconciliationAction.REGULATE, 0.81),
        ],
    )
    def test_below_the_bar_waits_for_a_person(self, action, just_under, make_settled):
        decision = make_settled(
            action, confidence=just_under, regulation_summary="caught it"
        )

        assert (
            gates.check_confidence(decision, context()).refusal
            is GateRule.BELOW_THRESHOLD
        )

    def test_below_the_bar_never_becomes_a_new_record(self, make_settled):
        # Quietly turning an unsure reading into "this is new" is how a
        # graph fills with duplicates of things the person has said for
        # years, and nothing about it ever looks like an error.
        decision = make_settled(ReconciliationAction.MERGE, confidence=0.5)

        checked = gates.check_confidence(decision, context())

        assert checked.action is ReconciliationAction.MERGE
        assert checked.is_refused is True

    def test_exactly_at_the_bar_is_allowed(self, make_settled):
        decision = make_settled(ReconciliationAction.MERGE, confidence=0.88)

        assert gates.check_confidence(decision, context()).is_refused is False


class TestTheChecksTogether:
    def test_they_stop_at_the_first_refusal(self, make_settled):
        # A tie and a low confidence in the same reading is one item to
        # show a person, described by the first thing that was wrong.
        decision = make_settled(
            ReconciliationAction.MERGE,
            confidence=0.60,
            runner_up=ReconciliationAction.REINFORCE,
            runner_up_confidence=0.58,
        )

        settled = gates.apply_gates(decision, context(historical()))

        assert settled.refusal is GateRule.TIE

    def test_a_clean_reading_passes_every_check(self, make_settled):
        decision = make_settled(ReconciliationAction.MERGE, confidence=0.95)

        settled = gates.apply_gates(decision, context(historical()))

        assert settled.is_refused is False
        assert settled.action is ReconciliationAction.MERGE

    def test_an_already_refused_reading_is_left_alone(self, make_settled):
        decision = make_settled(ReconciliationAction.MERGE).refuse(GateRule.UNREADABLE)

        assert gates.apply_gates(decision, context()).refusal is GateRule.UNREADABLE

    def test_the_order_is_the_documented_one(self):
        assert [gate.__name__ for gate in gates.GATES] == [
            "check_possible",
            "check_tie",
            "check_trial_not_trait",
            "check_local_extremum",
            "check_causal_anchor",
            "check_required_wording",
            "check_confidence",
        ]


class TestTheTwoFailuresThatLookLikeSuccess:
    def test_a_finding_nobody_could_decide_about_writes_nothing(self, make_item):
        decision = gates.unreadable(make_item(), model_used="fake")

        assert decision.refusal is GateRule.UNREADABLE
        assert decision.confidence == 0.0

    def test_a_failed_search_is_never_recorded_as_a_new_thought(self, make_item):
        # This is the whole reason the search reports the difference. A
        # search that found nothing means the thought is new; a search that
        # never ran means nobody knows, and recording it as new files a
        # decade-old pattern as a fresh discovery.
        decision = gates.unsearchable(make_item(search_failed=True), model_used="fake")

        assert decision.refusal is GateRule.SEARCH_FAILED
        assert decision.is_refused is True


class TestReadingAnswersThatArriveOddly:
    def test_a_tie_is_possible_whatever_the_records_are(self, make_item, make_settled):
        # A tie is about the model, not about the records. It has to survive
        # the possibility check to reach the person who will settle it.
        decision = make_settled(
            ReconciliationAction.AMBIGUOUS,
            item=make_item(node_type="EventNode", node_id="evt_1"),
        )

        assert gates.check_possible(decision, context()).is_refused is False

    def test_without_a_graph_a_deviation_counts_as_the_first(self, make_settled):
        # Nothing to count with. The cautious reading is that this is the
        # first time, which records the moment instead of the change.
        decision = make_settled(
            ReconciliationAction.EVOLVE,
            target_node_id="bel_old",
            target_type="BeliefNode",
            delta_description="once",
        )

        checked = gates.check_trial_not_trait(
            decision,
            context(historical("bel_old", node_type="BeliefNode", age_days=400)),
        )

        assert checked.action is ReconciliationAction.BRANCH

    def test_an_action_outside_the_eight_is_never_possible(self, make_settled):
        from lumen.pipeline.reconciliation.catalog import is_action_possible

        assert (
            is_action_possible(
                ReconciliationAction.AMBIGUOUS,
                source_type="ObservationNode",
                target_type="PatternNode",
                can_become_standing=True,
            )
            is False
        )
