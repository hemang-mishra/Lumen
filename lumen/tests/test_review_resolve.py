"""
Tests for turning one person's answer into the writing that was held back.

Two things run through all of it. An answer has to produce exactly what the
card offered, and an answer that cannot be produced safely has to be refused
rather than approximated — this is the one place in the review queue that
makes a permanent change to somebody's history.
"""

from __future__ import annotations

import pytest

from lumen.review.contracts import (
    ChoiceNotOffered,
    ResolutionChoice,
    StaleProposal,
)
from lumen.review.resolve import plan_resolution
from lumen.schemas.enums import (
    BookkeepingOperation,
    HitlResolutionChoice,
    LifecycleNodeStatus,
    ObservationType,
    ReconciliationAction,
)

AUDIT_ID = "d_2026_06_11_01_001"
ANSWER_ID = "d_2026_06_11_01_001_r"


@pytest.fixture
def rows_for(sample_pattern):
    """The records a proposal points at, as the graph would return them."""

    def _build(*, superseded: bool = False, missing: bool = False):
        if missing:
            return {}
        row = sample_pattern.to_graph_dict()
        row["_label"] = "PatternNode"
        if superseded:
            row["status"] = LifecycleNodeStatus.SUPERSEDED.value
        return {sample_pattern.node_id: row}

    return _build


class TestWhichAnswerWasGiven:
    """Each tap picks out the saved answer it means, and no other."""

    def test_approving_takes_the_recommendation(self, make_proposal, rows_for, moment):
        proposal = make_proposal(ReconciliationAction.REINFORCE)

        plan = plan_resolution(
            proposal, ResolutionChoice.APPROVE, at=moment, rows=rows_for()
        )

        assert plan.action_taken is ReconciliationAction.REINFORCE
        assert plan.recorded_choice is HitlResolutionChoice.ACTION_A

    def test_rejecting_stands_the_finding_on_its_own(
        self, make_proposal, rows_for, moment
    ):
        proposal = make_proposal(ReconciliationAction.MERGE)

        plan = plan_resolution(
            proposal, ResolutionChoice.REJECT, at=moment, rows=rows_for()
        )

        assert plan.action_taken is ReconciliationAction.BRANCH
        assert plan.recorded_choice is HitlResolutionChoice.CREATE_NEW

    def test_a_tie_can_be_broken_either_way(
        self, make_proposal, rows_for, moment, sample_pattern
    ):
        proposal = make_proposal(
            ReconciliationAction.REINFORCE,
            runner_up=ReconciliationAction.MERGE,
            runner_up_target=sample_pattern.node_id,
        )

        first = plan_resolution(
            proposal, ResolutionChoice.ACTION_A, at=moment, rows=rows_for()
        )
        second = plan_resolution(
            proposal, ResolutionChoice.ACTION_B, at=moment, rows=rows_for()
        )

        assert first.action_taken is ReconciliationAction.REINFORCE
        assert second.action_taken is ReconciliationAction.MERGE
        assert second.recorded_choice is HitlResolutionChoice.ACTION_B

    def test_a_tie_can_be_answered_with_neither(
        self, make_proposal, rows_for, moment, sample_pattern
    ):
        proposal = make_proposal(
            ReconciliationAction.REINFORCE,
            runner_up=ReconciliationAction.MERGE,
            runner_up_target=sample_pattern.node_id,
        )

        plan = plan_resolution(
            proposal, ResolutionChoice.CREATE_NEW, at=moment, rows=rows_for()
        )

        assert plan.action_taken is ReconciliationAction.BRANCH


class TestAnswersThatWereNotOffered:
    """A tap the card did not show is refused, never approximated."""

    def test_taking_a_second_reading_that_does_not_exist_is_refused(
        self, make_proposal, rows_for, moment
    ):
        proposal = make_proposal(ReconciliationAction.REINFORCE)

        with pytest.raises(ChoiceNotOffered):
            plan_resolution(
                proposal, ResolutionChoice.ACTION_B, at=moment, rows=rows_for()
            )

    def test_approving_a_tie_is_refused(
        self, make_proposal, rows_for, moment, sample_pattern
    ):
        # A tie has no recommendation to accept — that is what makes it a
        # tie. Silently reading APPROVE as "take the first" would decide
        # something on the person's behalf.
        proposal = make_proposal(
            ReconciliationAction.REINFORCE,
            runner_up=ReconciliationAction.MERGE,
            runner_up_target=sample_pattern.node_id,
        )

        with pytest.raises(ChoiceNotOffered):
            plan_resolution(
                proposal, ResolutionChoice.APPROVE, at=moment, rows=rows_for()
            )

    def test_the_refusal_says_what_was_offered(
        self, make_proposal, rows_for, moment
    ):
        proposal = make_proposal(ReconciliationAction.REINFORCE)

        with pytest.raises(ChoiceNotOffered) as raised:
            plan_resolution(
                proposal, ResolutionChoice.ACTION_B, at=moment, rows=rows_for()
            )

        assert "APPROVE" in raised.value.offered
        assert "REJECT" in raised.value.offered


class TestWhenTheWorldMovedOn:
    """An answer built against an older graph is refused, not applied."""

    def test_a_replaced_record_refuses_the_recommendation(
        self, make_proposal, rows_for, moment
    ):
        proposal = make_proposal(ReconciliationAction.REINFORCE)

        with pytest.raises(StaleProposal):
            plan_resolution(
                proposal,
                ResolutionChoice.APPROVE,
                at=moment,
                rows=rows_for(superseded=True),
            )

    def test_a_vanished_record_refuses_the_recommendation(
        self, make_proposal, rows_for, moment
    ):
        proposal = make_proposal(ReconciliationAction.REINFORCE)

        with pytest.raises(StaleProposal):
            plan_resolution(
                proposal,
                ResolutionChoice.APPROVE,
                at=moment,
                rows=rows_for(missing=True),
            )

    def test_standing_alone_still_works_on_a_stale_card(
        self, make_proposal, rows_for, moment
    ):
        # It touches nothing that already exists, so nothing underneath it
        # can have moved. This is why a stale card is still answerable.
        proposal = make_proposal(ReconciliationAction.REINFORCE)

        plan = plan_resolution(
            proposal,
            ResolutionChoice.REJECT,
            at=moment,
            rows=rows_for(superseded=True),
        )

        assert plan.action_taken is ReconciliationAction.BRANCH


class TestTheTwoNotes:
    """Answering stamps the old note and writes a new one beside it."""

    def test_a_new_note_records_what_was_done(
        self, make_proposal, rows_for, moment
    ):
        proposal = make_proposal(ReconciliationAction.REINFORCE)

        plan = plan_resolution(
            proposal, ResolutionChoice.APPROVE, at=moment, rows=rows_for()
        )

        assert plan.new_audit.node_id == ANSWER_ID
        assert plan.new_audit.hitl_resolved is True
        assert plan.new_audit.hitl_resolution_user_choice is HitlResolutionChoice.ACTION_A
        assert plan.new_audit.action is ReconciliationAction.REINFORCE

    def test_the_new_note_says_a_person_decided_it(
        self, make_proposal, rows_for, moment
    ):
        # Naming whichever model failed to decide would be a lie about who
        # made the call.
        proposal = make_proposal(ReconciliationAction.REINFORCE)

        plan = plan_resolution(
            proposal, ResolutionChoice.APPROVE, at=moment, rows=rows_for()
        )

        assert plan.new_audit.model_used == "human-review"

    def test_the_waiting_note_is_stamped_rather_than_rewritten(
        self, make_proposal, rows_for, moment
    ):
        proposal = make_proposal(ReconciliationAction.REINFORCE)

        plan = plan_resolution(
            proposal, ResolutionChoice.APPROVE, at=moment, rows=rows_for()
        )

        stamps = [
            update
            for update in plan.write_plan.bookkeeping
            if update.operation is BookkeepingOperation.MARK_HITL_RESOLVED
        ]
        assert len(stamps) == 1
        assert stamps[0].node_id == AUDIT_ID
        assert stamps[0].choice is HitlResolutionChoice.ACTION_A
        assert stamps[0].resolved_action is ReconciliationAction.REINFORCE

    def test_the_two_notes_are_linked(self, make_proposal, rows_for, moment):
        proposal = make_proposal(ReconciliationAction.REINFORCE)

        plan = plan_resolution(
            proposal, ResolutionChoice.APPROVE, at=moment, rows=rows_for()
        )

        links = [
            edge
            for edge in plan.write_plan.edges
            if edge.from_node_id == AUDIT_ID and edge.to_node_id == ANSWER_ID
        ]
        assert len(links) == 1

    def test_answering_twice_would_mint_the_same_note(
        self, make_proposal, rows_for, moment
    ):
        # The identifier is worked out rather than generated, so a repeated
        # answer collides instead of quietly writing a second note.
        proposal = make_proposal(ReconciliationAction.REINFORCE)

        first = plan_resolution(
            proposal, ResolutionChoice.APPROVE, at=moment, rows=rows_for()
        )
        second = plan_resolution(
            proposal, ResolutionChoice.APPROVE, at=moment, rows=rows_for()
        )

        assert first.new_audit.node_id == second.new_audit.node_id

    def test_the_new_note_carries_a_way_back(
        self, make_proposal, rows_for, moment
    ):
        proposal = make_proposal(ReconciliationAction.REINFORCE)

        plan = plan_resolution(
            proposal, ResolutionChoice.APPROVE, at=moment, rows=rows_for()
        )

        assert plan.new_audit.rollback_pointer.edge_to_invalidate
        assert proposal.source_node_id in plan.new_audit.rollback_pointer.nodes_to_requeue

    def test_an_answer_writing_nothing_still_leaves_a_note(
        self, make_proposal, rows_for, moment, make_item
    ):
        # A finding that belongs to the day it happened stays with its
        # entry rather than becoming a standing claim. "Answered, and
        # nothing further was needed" is a real outcome and the commonest
        # one; it must not read as a failure.
        item = make_item(
            text="the coffee was cold",
            observation_type=ObservationType.EMOTION,
        )
        proposal = make_proposal(ReconciliationAction.MERGE, item=item)

        plan = plan_resolution(
            proposal, ResolutionChoice.REJECT, at=moment, rows=rows_for()
        )

        assert plan.writes_nothing
        assert plan.new_audit.node_id == ANSWER_ID
        assert plan.new_audit.edge_type_created is None


class TestWhatGetsWritten:
    """The plan that comes out is one the writer can carry out as it stands."""

    def test_links_point_at_the_note_that_answered(
        self, make_proposal, rows_for, moment
    ):
        # The saved links name the note that says nobody could decide.
        # Following one back to find out why it exists must land on the
        # decision that was actually acted on.
        proposal = make_proposal(ReconciliationAction.MERGE)

        plan = plan_resolution(
            proposal, ResolutionChoice.APPROVE, at=moment, rows=rows_for()
        )

        decided = [
            edge.edge.decision_id
            for edge in plan.write_plan.edges
            if hasattr(edge.edge, "decision_id")
        ]
        assert decided
        assert set(decided) == {ANSWER_ID}

    def test_records_written_long_ago_are_named_as_already_there(
        self, make_proposal, rows_for, moment
    ):
        # Without this the plan refuses itself for pointing at records it
        # does not create — all of which were saved when the entry ran.
        proposal = make_proposal(ReconciliationAction.REINFORCE)

        plan = plan_resolution(
            proposal, ResolutionChoice.APPROVE, at=moment, rows=rows_for()
        )

        known = plan.write_plan.existing_node_ids
        assert proposal.source_node_id in known
        assert proposal.audit_node_id in known

    def test_the_new_version_of_a_belief_is_carried_over(
        self, make_proposal, rows_for, moment
    ):
        proposal = make_proposal(
            ReconciliationAction.EVOLVE,
            delta_description="it used to be about proving myself",
        )

        plan = plan_resolution(
            proposal, ResolutionChoice.APPROVE, at=moment, rows=rows_for()
        )

        # The next version, plus the note. Rebuilt as the real kind of record
        # rather than the shared base type.
        kinds = {planned.node_type for planned in plan.write_plan.nodes}
        assert "DecisionAuditNode" in kinds
        assert len(plan.write_plan.nodes) > 1

    def test_the_small_updates_come_along(self, make_proposal, rows_for, moment):
        proposal = make_proposal(ReconciliationAction.REINFORCE)

        plan = plan_resolution(
            proposal, ResolutionChoice.APPROVE, at=moment, rows=rows_for()
        )

        operations = {update.operation for update in plan.write_plan.bookkeeping}
        assert BookkeepingOperation.RECORD_REINFORCEMENT in operations
        assert BookkeepingOperation.MARK_HITL_RESOLVED in operations


class TestRunningOutOfTime:
    """An item nobody answered is recorded as exactly that."""

    def test_the_recorded_choice_can_be_overridden(
        self, make_proposal, rows_for, moment
    ):
        proposal = make_proposal(ReconciliationAction.MERGE)

        plan = plan_resolution(
            proposal,
            ResolutionChoice.REJECT,
            at=moment,
            rows=rows_for(),
            recorded_choice=HitlResolutionChoice.AUTO_BRANCH_AFTER_SNOOZE,
        )

        # Same graph write as a rejection, recorded differently — the graph
        # must never claim somebody chose this.
        assert plan.action_taken is ReconciliationAction.BRANCH
        assert plan.recorded_choice is HitlResolutionChoice.AUTO_BRANCH_AFTER_SNOOZE
        assert (
            plan.new_audit.hitl_resolution_user_choice
            is HitlResolutionChoice.AUTO_BRANCH_AFTER_SNOOZE
        )
