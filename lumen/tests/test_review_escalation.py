"""
Tests for what happens when the pipeline gives up on something.

This is the join between the two halves. The pipeline decides it cannot
settle a question and stops; everything here is about what it leaves behind
so the question can be answered later — and about the ceiling, which is the
one place the pipeline is told to stop asking.
"""

from __future__ import annotations

import pytest

from lumen.operational.enums import HitlItemStatus
from lumen.pipeline.orchestration import bookkeeping
from lumen.schemas.enums import HitlEntryType, ReconciliationAction


@pytest.fixture
def escalating(sample_decision_audit, make_proposal):
    """
    One episode's outcome, with a single thing left undecided.

    Built around the real saved proposal rather than a stand-in, so what
    reaches the queue is what the queue will later have to answer with.
    """
    from lumen.schemas.pipeline import (
        HitlEscalation,
        ReconciliationOutcome,
        ReconciliationResult,
    )

    def _build(*, audit_id: str | None = None, proposal=None, keep: bool = True):
        audit_id = audit_id or sample_decision_audit.node_id
        proposal = proposal or make_proposal(audit_id=audit_id)
        return ReconciliationOutcome(
            episode_id="ep_new",
            results=[
                ReconciliationResult(
                    source_node_id=proposal.source_node_id,
                    action=ReconciliationAction.REINFORCE,
                    target_node_id=proposal.primary.target_node_id,
                    confidence=0.6,
                    decision_model="fake-light",
                    escalated_to_hitl=True,
                    audit_node_id=audit_id,
                )
            ],
            escalations=[
                HitlEscalation(
                    audit_node_id=audit_id,
                    source_node_id=proposal.source_node_id,
                    episode_id="ep_new",
                    entry_type=HitlEntryType.BELOW_THRESHOLD,
                    summary="held back: not confident enough",
                    proposal=proposal if keep else None,
                )
            ],
            decision_model="fake-light",
        )

    return _build


@pytest.fixture
def job(ops_store, buffer_with_messages, make_event):
    """A pipeline run for the escalations to belong to."""
    from lumen.config import AppConfig

    event = make_event(
        [("USER", "something")], session_id=buffer_with_messages.session_id
    )
    return bookkeeping.open_job(event, ops=ops_store, config=AppConfig())


class TestReachingTheQueue:
    """An undecided item becomes a question somebody can answer."""

    def test_it_is_queued(self, ops_store, escalating, job):
        added = bookkeeping.queue_escalations(escalating(), ops=ops_store, job=job)

        assert added == 1
        assert ops_store.hitl.count_asked("local") == 1

    def test_what_it_was_going_to_write_is_kept_with_it(
        self, ops_store, escalating, job, sample_decision_audit
    ):
        bookkeeping.queue_escalations(escalating(), ops=ops_store, job=job)

        assert ops_store.hitl.get_proposal(sample_decision_audit.node_id) is not None

    def test_the_second_reading_is_recorded_on_the_row(
        self, ops_store, escalating, job, make_proposal, sample_pattern,
        sample_decision_audit,
    ):
        # So the queue can be read back without unpacking what was saved.
        proposal = make_proposal(
            ReconciliationAction.REINFORCE,
            audit_id=sample_decision_audit.node_id,
            runner_up=ReconciliationAction.MERGE,
            runner_up_target=sample_pattern.node_id,
        )
        bookkeeping.queue_escalations(
            escalating(proposal=proposal), ops=ops_store, job=job
        )

        row = ops_store.hitl.get_by_audit_node(sample_decision_audit.node_id)
        assert row.candidate_b_node_id == sample_pattern.node_id
        assert row.confidence_b is not None

    def test_the_same_question_is_never_asked_twice(
        self, ops_store, escalating, job
    ):
        outcome = escalating()

        bookkeeping.queue_escalations(outcome, ops=ops_store, job=job)
        added = bookkeeping.queue_escalations(outcome, ops=ops_store, job=job)

        assert added == 0
        assert ops_store.hitl.count_pending("local") == 1

    def test_an_item_with_nothing_kept_is_still_queued(
        self, ops_store, escalating, job
    ):
        # Better a question somebody can see and cannot answer than one that
        # vanished. The card says plainly that there is nothing to carry out.
        added = bookkeeping.queue_escalations(
            escalating(keep=False), ops=ops_store, job=job
        )

        assert added == 1


class TestTheCeiling:
    """Past the ceiling, questions wait outside rather than being decided."""

    def test_questions_are_asked_while_there_is_room(
        self, ops_store, escalating, job, config_with_cap
    ):
        bookkeeping.queue_escalations(
            escalating(), ops=ops_store, job=job, config=config_with_cap(5)
        )

        assert ops_store.hitl.count_asked("local") == 1

    def test_a_question_arriving_at_a_full_queue_waits_outside(
        self, ops_store, escalating, job, make_proposal, config_with_cap
    ):
        first = escalating(audit_id="d_first", proposal=make_proposal(audit_id="d_first"))
        second = escalating(
            audit_id="d_second", proposal=make_proposal(audit_id="d_second")
        )
        cap_of_one = config_with_cap(1)

        bookkeeping.queue_escalations(first, ops=ops_store, job=job, config=cap_of_one)
        bookkeeping.queue_escalations(second, ops=ops_store, job=job, config=cap_of_one)

        parked = ops_store.hitl.get_by_audit_node("d_second")
        assert parked.status is HitlItemStatus.SUSPENDED_QUEUE_FULL

    def test_a_parked_question_is_not_decided(
        self, ops_store, escalating, job, make_proposal, config_with_cap
    ):
        # The ceiling protects attention. It is not permission to guess.
        cap_of_zero = config_with_cap(1)
        bookkeeping.queue_escalations(
            escalating(audit_id="d_a", proposal=make_proposal(audit_id="d_a")),
            ops=ops_store,
            job=job,
            config=cap_of_zero,
        )
        bookkeeping.queue_escalations(
            escalating(audit_id="d_b", proposal=make_proposal(audit_id="d_b")),
            ops=ops_store,
            job=job,
            config=cap_of_zero,
        )

        parked = ops_store.hitl.get_by_audit_node("d_b")
        assert parked.resolved_at is None
        assert parked.resolution_choice is None

    def test_the_ceiling_counts_within_one_run_too(
        self, ops_store, escalating, job, make_proposal, config_with_cap
    ):
        # Two undecided items in one entry, with room for one. The second
        # must not slip in because the count was read before either landed.
        from lumen.schemas.pipeline import HitlEscalation

        outcome = escalating(audit_id="d_a", proposal=make_proposal(audit_id="d_a"))
        second = make_proposal(audit_id="d_b")
        outcome = outcome.model_copy(
            update={
                "escalations": [
                    *outcome.escalations,
                    HitlEscalation(
                        audit_node_id="d_b",
                        source_node_id=second.source_node_id,
                        episode_id="ep_new",
                        entry_type=HitlEntryType.BELOW_THRESHOLD,
                        summary="also held back",
                        proposal=second,
                    ),
                ]
            }
        )

        bookkeeping.queue_escalations(
            outcome, ops=ops_store, job=job, config=config_with_cap(1)
        )

        assert ops_store.hitl.count_asked("local") == 1
        assert ops_store.hitl.count_pending("local") == 2
