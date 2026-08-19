"""
Tests for the object that actually answers a review question.

Wired to real stores throughout, because the thing most worth checking is
that answering a card genuinely changes the graph — and that the queue and
the graph never end up disagreeing about whether it did.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from lumen.operational.enums import HitlItemStatus
from lumen.operational.repositories import (
    IllegalStateTransitionError,
    RecordNotFoundError,
)
from lumen.review.contracts import ChoiceNotOffered, ResolutionChoice, StaleProposal
from lumen.review.service import MissingProposal
from lumen.schemas.enums import (
    DecisionStatus,
    HitlEntryType,
    HitlResolutionChoice,
    ReconciliationAction,
)


@pytest.fixture
def seeded(graph_store, sample_pattern, sample_observation, sample_episode):
    """
    The records a question points at, actually in the graph.

    Answering reads them back to check nothing has moved underneath the
    proposal, so they have to be really there rather than stubbed.
    """
    graph_store.write_node("EpisodeNode", sample_episode)
    graph_store.write_node("PatternNode", sample_pattern)
    graph_store.write_node("ObservationNode", sample_observation)
    return sample_pattern


class TestAnswering:
    """One tap, and the writing that was held back happens."""

    def test_approving_writes_what_was_held_back(
        self, reviewer, queued, seeded, graph_store, make_proposal, make_item,
        sample_observation,
    ):
        item = queued(
            proposal=make_proposal(
                ReconciliationAction.REINFORCE,
                item=make_item(node_id=sample_observation.node_id),
            )
        )

        outcome = reviewer.resolve("tester", item.id, ResolutionChoice.APPROVE)

        assert outcome.action_taken is ReconciliationAction.REINFORCE
        assert outcome.edges_written
        assert graph_store.get_node(outcome.new_audit_node_id) is not None

    def test_the_card_leaves_the_queue(
        self, reviewer, queued, seeded, ops_store, make_proposal, make_item,
        sample_observation,
    ):
        item = queued(
            proposal=make_proposal(item=make_item(node_id=sample_observation.node_id))
        )

        reviewer.resolve("tester", item.id, ResolutionChoice.APPROVE)

        settled = ops_store.hitl.get(item.id)
        assert settled.status is HitlItemStatus.RESOLVED
        assert settled.resolution_choice is HitlResolutionChoice.ACTION_A
        assert settled.resolved_action is ReconciliationAction.REINFORCE

    def test_the_waiting_note_is_stamped_in_the_graph(
        self, reviewer, queued, seeded, graph_store, sample_decision_audit,
        make_proposal, make_item, sample_observation,
    ):
        graph_store.write_node("DecisionAuditNode", sample_decision_audit)
        item = queued(
            proposal=make_proposal(
                audit_id=sample_decision_audit.node_id,
                item=make_item(node_id=sample_observation.node_id),
            )
        )

        reviewer.resolve("tester", item.id, ResolutionChoice.APPROVE)

        stamped = graph_store.get_node(sample_decision_audit.node_id)
        assert stamped["hitl_resolved"] is True
        assert stamped["hitl_resolution_user_choice"] == "ACTION_A"

    def test_answering_twice_is_refused(
        self, reviewer, queued, seeded, make_proposal, make_item, sample_observation
    ):
        # Two taps on one card would otherwise write the same change twice.
        item = queued(
            proposal=make_proposal(item=make_item(node_id=sample_observation.node_id))
        )
        reviewer.resolve("tester", item.id, ResolutionChoice.APPROVE)

        with pytest.raises(IllegalStateTransitionError):
            reviewer.resolve("tester", item.id, ResolutionChoice.APPROVE)

    def test_an_answer_the_card_did_not_offer_is_refused(
        self, reviewer, queued, seeded
    ):
        item = queued()

        with pytest.raises(ChoiceNotOffered):
            reviewer.resolve("tester", item.id, ResolutionChoice.ACTION_B)

    def test_deferring_is_not_an_answer(self, reviewer, queued, seeded):
        # It changes nothing in the graph and has its own call, so the code
        # that writes never holds a case where it writes nothing on purpose.
        item = queued()

        with pytest.raises(ChoiceNotOffered):
            reviewer.resolve("tester", item.id, ResolutionChoice.SNOOZE)

    def test_a_stale_recommendation_is_refused(
        self, reviewer, queued, seeded, graph_store, sample_pattern, moment
    ):
        graph_store.mark_superseded(sample_pattern.node_id, at=moment)
        item = queued()

        with pytest.raises(StaleProposal):
            reviewer.resolve("tester", item.id, ResolutionChoice.APPROVE)

    def test_standing_alone_still_works_on_a_stale_card(
        self, reviewer, queued, seeded, graph_store, sample_pattern, moment,
        make_proposal, make_item, sample_observation,
    ):
        graph_store.mark_superseded(sample_pattern.node_id, at=moment)
        item = queued(
            proposal=make_proposal(item=make_item(node_id=sample_observation.node_id))
        )

        outcome = reviewer.resolve("tester", item.id, ResolutionChoice.REJECT)

        assert outcome.action_taken is ReconciliationAction.BRANCH


class TestReadingTheQueue:
    """What is waiting, and how much of it."""

    def test_the_queue_lists_what_is_waiting(self, reviewer, queued, seeded):
        item = queued()

        view = reviewer.list_queue("tester")

        assert [card.item_id for card in view.cards] == [item.id]

    def test_ties_are_listed_before_low_confidence_items(
        self, reviewer, queued, make_proposal, seeded, sample_pattern
    ):
        queued(proposal=make_proposal(audit_id="d_low"))
        tie = queued(
            proposal=make_proposal(
                audit_id="d_tie",
                runner_up=ReconciliationAction.MERGE,
                runner_up_target=sample_pattern.node_id,
                entry_type=HitlEntryType.AMBIGUOUS_TIE,
            )
        )

        view = reviewer.list_queue("tester")

        assert view.cards[0].item_id == tie.id

    def test_an_item_with_nothing_recorded_is_still_listed(
        self, reviewer, queued, seeded
    ):
        # Leaving it out is what makes the count and the list disagree — a
        # screen saying forty are waiting above a list showing none.
        item = queued(save_proposal=False)

        cards = reviewer.list_queue("tester").cards

        assert [card.item_id for card in cards] == [item.id]
        assert cards[0].answerable is False
        assert cards[0].unanswerable_reason
        assert cards[0].options == []

    def test_the_counts_say_how_much_is_waiting(self, reviewer, queued, seeded):
        queued()

        counts = reviewer.counts("tester")

        assert counts.pending == 1
        assert counts.visible == 1
        assert counts.parked == 0
        assert counts.at_capacity is False
        assert counts.oldest_asked_at is not None

    def test_the_counts_settle_nothing(
        self, reviewer, queued, seeded, ops_store, moment
    ):
        # This is polled from every screen. A number that quietly decides
        # things as a side effect of being displayed is one nobody should
        # trust.
        item = queued(
            snooze_count=1, last_snoozed_at=moment - timedelta(days=400)
        )

        reviewer.counts("tester")

        assert ops_store.hitl.get(item.id).status is HitlItemStatus.PENDING_HITL

    def test_one_card_can_be_fetched_on_its_own(self, reviewer, queued, seeded):
        item = queued()

        assert reviewer.get_card("tester", item.id).item_id == item.id

    def test_a_card_with_nothing_recorded_says_so(self, reviewer, queued, seeded):
        item = queued(save_proposal=False)

        card = reviewer.get_card("tester", item.id)

        assert card.answerable is False
        assert "nothing recorded" in card.unanswerable_reason


class TestDeferring:
    """Putting a question off, and what that changes."""

    def test_a_deferred_item_disappears(self, reviewer, queued, seeded):
        item = queued()

        reviewer.snooze("tester", item.id)

        assert reviewer.list_queue("tester").cards == []

    def test_it_still_counts_as_unresolved(self, reviewer, queued, seeded):
        # Out of sight is not answered. It is still a question owed a reply.
        item = queued()

        reviewer.snooze("tester", item.id)

        assert reviewer.counts("tester").pending == 1

    def test_deferring_arms_the_clock(self, reviewer, queued, seeded, ops_store):
        item = queued()

        card = reviewer.snooze("tester", item.id)

        assert card.snooze_count == 1
        assert card.snoozed_until is not None
        assert card.auto_resolves_at is not None

    def test_a_settled_item_cannot_be_deferred(
        self, reviewer, queued, seeded, make_proposal, make_item, sample_observation
    ):
        item = queued(
            proposal=make_proposal(item=make_item(node_id=sample_observation.node_id))
        )
        reviewer.resolve("tester", item.id, ResolutionChoice.APPROVE)

        with pytest.raises(IllegalStateTransitionError):
            reviewer.snooze("tester", item.id)


class TestHousekeepingRunsItself:
    """The queue keeps itself honest for anybody who opens it."""

    def test_opening_the_queue_lets_parked_items_in(
        self, reviewer, queued, seeded, ops_store
    ):
        item = queued(status=HitlItemStatus.SUSPENDED_QUEUE_FULL)

        reviewer.list_queue("tester")

        assert ops_store.hitl.get(item.id).status is HitlItemStatus.PENDING_HITL

    def test_answering_lets_parked_items_in(
        self, reviewer, queued, make_proposal, seeded, make_item, sample_observation
    ):
        asked = queued(
            proposal=make_proposal(
                audit_id="d_asked",
                item=make_item(node_id=sample_observation.node_id),
            )
        )
        parked = queued(
            proposal=make_proposal(audit_id="d_parked"),
            status=HitlItemStatus.SUSPENDED_QUEUE_FULL,
        )

        outcome = reviewer.resolve("tester", asked.id, ResolutionChoice.APPROVE)

        assert parked.id in outcome.admitted

    def test_the_sweep_can_be_run_on_its_own(
        self, reviewer, queued, seeded, moment
    ):
        queued(status=HitlItemStatus.SUSPENDED_QUEUE_FULL)

        report = reviewer.sweep("tester")

        assert report.admitted


class TestSayingNo:
    """
    Declining a promotion writes nothing, and says nothing was written.

    The finding is already in the graph from the run that found it. What
    saying no prevents is its promotion into a standing record — nothing is
    removed, and nothing is claimed to have been decided beyond that.
    """

    def test_it_writes_nothing_at_all(
        self, reviewer, queued, seeded, make_proposal, make_item,
        sample_observation, graph_store,
    ):
        item = queued(
            proposal=make_proposal(
                ReconciliationAction.BRANCH,
                target_node_id="",
                item=make_item(node_id=sample_observation.node_id),
            )
        )
        before = graph_store.count_by_type()

        outcome = reviewer.resolve("tester", item.id, ResolutionChoice.REJECT)

        assert outcome.writes_nothing
        assert outcome.nodes_written == []
        assert graph_store.count_by_type() == before

    def test_it_is_recorded_as_the_person_saying_no(
        self, reviewer, queued, seeded, ops_store, make_proposal, make_item,
        sample_observation,
    ):
        item = queued(
            proposal=make_proposal(
                ReconciliationAction.BRANCH,
                target_node_id="",
                item=make_item(node_id=sample_observation.node_id),
            )
        )

        reviewer.resolve("tester", item.id, ResolutionChoice.REJECT)

        settled = ops_store.hitl.get(item.id)
        assert settled.status is HitlItemStatus.RESOLVED
        assert settled.resolution_choice is HitlResolutionChoice.DECLINED

    def test_the_note_stops_saying_somebody_will_look(
        self, reviewer, queued, seeded, graph_store, sample_decision_audit,
        make_proposal, make_item, sample_observation,
    ):
        graph_store.write_node("DecisionAuditNode", sample_decision_audit)
        item = queued(
            proposal=make_proposal(
                ReconciliationAction.BRANCH,
                target_node_id="",
                audit_id=sample_decision_audit.node_id,
                item=make_item(node_id=sample_observation.node_id),
            )
        )

        reviewer.resolve("tester", item.id, ResolutionChoice.REJECT)

        note = graph_store.get_node(sample_decision_audit.node_id)
        assert note["status"] == DecisionStatus.DISMISSED.value

    def test_saying_no_to_a_real_alternative_still_writes_it(
        self, reviewer, queued, seeded, make_proposal, make_item,
        sample_observation,
    ):
        # Against "this is the same as that", no means "record it separately"
        # — a real action, not a refusal.
        item = queued(
            proposal=make_proposal(
                ReconciliationAction.MERGE,
                item=make_item(node_id=sample_observation.node_id),
            )
        )

        outcome = reviewer.resolve("tester", item.id, ResolutionChoice.REJECT)

        assert outcome.recorded_choice is HitlResolutionChoice.CREATE_NEW
        assert outcome.action_taken is ReconciliationAction.BRANCH


class TestWithdrawing:
    """A question nobody can answer can be taken off the list."""

    def test_an_unanswerable_item_can_be_withdrawn(
        self, reviewer, queued, seeded, ops_store
    ):
        item = queued(save_proposal=False)

        outcome = reviewer.dismiss("tester", item.id)

        assert outcome.recorded_choice is HitlResolutionChoice.DISMISSED_UNANSWERABLE
        assert outcome.writes_nothing
        assert ops_store.hitl.get(item.id).status is HitlItemStatus.RESOLVED

    def test_withdrawing_writes_nothing_to_the_history(
        self, reviewer, queued, seeded, graph_store, sample_decision_audit
    ):
        graph_store.write_node("DecisionAuditNode", sample_decision_audit)
        item = queued(save_proposal=False)

        before = graph_store.count_by_type()
        reviewer.dismiss("tester", item.id)

        # The note is stamped; nothing new is created and nothing is removed.
        assert graph_store.count_by_type() == before

    def test_the_note_stops_claiming_somebody_will_look(
        self, reviewer, queued, seeded, graph_store, sample_decision_audit,
        make_proposal,
    ):
        graph_store.write_node("DecisionAuditNode", sample_decision_audit)
        item = queued(
            proposal=make_proposal(audit_id=sample_decision_audit.node_id),
            save_proposal=False,
        )

        reviewer.dismiss("tester", item.id)

        note = graph_store.get_node(sample_decision_audit.node_id)
        assert note["status"] == DecisionStatus.DISMISSED.value
        assert note["hitl_resolved"] is True

    def test_an_answerable_item_cannot_be_withdrawn(
        self, reviewer, queued, seeded
    ):
        # "I do not want to decide this" is what deferring is for. A question
        # with a real answer behind it is never quietly dropped.
        item = queued()

        with pytest.raises(ChoiceNotOffered):
            reviewer.dismiss("tester", item.id)

    def test_withdrawing_twice_is_refused(self, reviewer, queued, seeded):
        item = queued(save_proposal=False)
        reviewer.dismiss("tester", item.id)

        with pytest.raises(IllegalStateTransitionError):
            reviewer.dismiss("tester", item.id)


class TestQuestionsWithNothingToDecide:
    """
    Ones asked before it was clear they were not questions.

    The pipeline no longer raises these, but the ones already raised sit in
    the queue taking up room under the ceiling and asking for an answer that
    cannot matter.
    """

    def test_the_sweep_closes_them(
        self, reviewer, queued, seeded, make_proposal, make_item, ops_store
    ):
        from lumen.schemas.enums import ObservationType

        # A finding that belongs to the day it happened: it never becomes a
        # standing record, so no answer writes anything.
        nothing_to_decide = make_item(
            text="the coffee was cold", observation_type=ObservationType.EMOTION
        )
        item = queued(
            proposal=make_proposal(
                ReconciliationAction.BRANCH,
                target_node_id="",
                item=nothing_to_decide,
            )
        )

        report = reviewer.sweep("tester")

        assert item.id in report.closed
        assert ops_store.hitl.get(item.id).status is HitlItemStatus.RESOLVED

    def test_closing_them_writes_nothing(
        self, reviewer, queued, seeded, make_proposal, make_item, graph_store
    ):
        from lumen.schemas.enums import ObservationType

        queued(
            proposal=make_proposal(
                ReconciliationAction.BRANCH,
                target_node_id="",
                item=make_item(
                    text="the coffee was cold",
                    observation_type=ObservationType.EMOTION,
                ),
            )
        )
        before = graph_store.count_by_type()

        reviewer.sweep("tester")

        assert graph_store.count_by_type() == before

    def test_one_nobody_can_answer_is_left_for_the_person_to_see(
        self, reviewer, queued, seeded, ops_store
    ):
        # A question with nothing saved behind it also changes nothing, but
        # for a different reason — the working was lost, not absent. It may
        # have been a real question, so it is shown and withdrawn on purpose
        # rather than swept away before anybody sees it.
        item = queued(save_proposal=False)

        report = reviewer.sweep("tester")

        assert item.id not in report.closed
        assert ops_store.hitl.get(item.id).status is HitlItemStatus.PENDING_HITL

    def test_a_real_question_is_left_alone(
        self, reviewer, queued, seeded, ops_store
    ):
        # This one has a genuine difference behind it, so it stays.
        item = queued()

        report = reviewer.sweep("tester")

        assert item.id not in report.closed
        assert ops_store.hitl.get(item.id).status is HitlItemStatus.PENDING_HITL

    def test_one_can_be_withdrawn_by_hand_too(
        self, reviewer, queued, seeded, make_proposal, make_item
    ):
        from lumen.schemas.enums import ObservationType

        item = queued(
            proposal=make_proposal(
                ReconciliationAction.BRANCH,
                target_node_id="",
                item=make_item(
                    text="the coffee was cold",
                    observation_type=ObservationType.EMOTION,
                ),
            )
        )

        outcome = reviewer.dismiss("tester", item.id)

        assert outcome.recorded_choice is HitlResolutionChoice.DISMISSED_UNANSWERABLE

    def test_a_real_question_cannot_be_withdrawn(self, reviewer, queued, seeded):
        item = queued()

        with pytest.raises(ChoiceNotOffered):
            reviewer.dismiss("tester", item.id)


class TestWhoseQueueItIs:
    """One person's questions are not another person's to answer."""

    def test_somebody_elses_item_reads_as_missing(self, reviewer, queued, seeded):
        # Reported as missing rather than forbidden. "You may not touch
        # this" confirms it exists, which is a small leak from a store
        # holding somebody's private history.
        item = queued(user_id="someone-else")

        with pytest.raises(RecordNotFoundError):
            reviewer.get_card("tester", item.id)

    def test_somebody_elses_item_cannot_be_answered(self, reviewer, queued, seeded):
        item = queued(user_id="someone-else")

        with pytest.raises(RecordNotFoundError):
            reviewer.resolve("tester", item.id, ResolutionChoice.APPROVE)

    def test_an_item_that_does_not_exist_reads_as_missing(self, reviewer):
        with pytest.raises(RecordNotFoundError):
            reviewer.get_card("tester", "hitl_nothing")


def test_a_record_that_cannot_be_indexed_is_still_answered(
    reviewer, queued, seeded, ops_store, make_proposal, make_item,
    sample_observation, monkeypatch,
):
    """
    The graph is right and the decision is made, so the answer stands.

    What is lost is that a new record cannot be found by meaning yet, which
    is repairable and is reported rather than hidden.
    """
    from lumen.pipeline.orchestration import commit as writing

    item = queued(
        proposal=make_proposal(
            ReconciliationAction.BRANCH,
            target_node_id="",
            item=make_item(node_id=sample_observation.node_id),
        )
    )

    def refuse(_entries, *, vectors):
        return [], ["something"]

    monkeypatch.setattr(writing, "_write_index", refuse)

    outcome = reviewer.resolve("tester", item.id, ResolutionChoice.APPROVE)

    assert outcome.unindexed_node_ids == ["something"]
    assert ops_store.hitl.get(item.id).status is HitlItemStatus.RESOLVED
