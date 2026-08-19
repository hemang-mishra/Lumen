"""
Tests for the queue's own storage: what it shows, what it hides, what it keeps.

The distinction these lean on hardest is between "unresolved" and "being
asked right now". A deferred item is still owed an answer and still counts
against the ceiling; it is simply not on screen. A parked item is not being
asked at all. Conflating any two of the three breaks either the ceiling or
the ability to defer something.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from lumen.operational.enums import HitlItemStatus
from lumen.operational.repositories import (
    IllegalStateTransitionError,
    RecordNotFoundError,
)
from lumen.schemas.enums import HitlResolutionChoice, ReconciliationAction


class TestWhatIsShown:
    """Listing separates being asked from merely being unresolved."""

    def test_a_waiting_item_is_shown(self, ops_store, queued, moment):
        item = queued()

        shown = ops_store.hitl.list_visible("tester", now=moment)

        assert [row.id for row in shown] == [item.id]

    def test_a_deferred_item_is_hidden_while_it_rests(
        self, ops_store, queued, moment
    ):
        queued(snoozed_until=moment + timedelta(hours=24))

        assert ops_store.hitl.list_visible("tester", now=moment) == []

    def test_a_deferred_item_comes_back_when_its_time_is_up(
        self, ops_store, queued, moment
    ):
        item = queued(snoozed_until=moment + timedelta(hours=24))

        shown = ops_store.hitl.list_visible(
            "tester", now=moment + timedelta(hours=25)
        )

        assert [row.id for row in shown] == [item.id]

    def test_a_parked_item_is_not_shown(self, ops_store, queued, moment):
        queued(status=HitlItemStatus.SUSPENDED_QUEUE_FULL)

        assert ops_store.hitl.list_visible("tester", now=moment) == []

    def test_a_deferred_item_still_counts_as_unresolved(
        self, ops_store, queued, moment
    ):
        # Out of sight is not answered. It is still a question owed a reply,
        # and it still takes up room.
        queued(snoozed_until=moment + timedelta(hours=24))

        assert ops_store.hitl.count_pending("tester") == 1
        assert ops_store.hitl.count_asked("tester") == 1

    def test_a_parked_item_counts_as_unresolved_but_not_as_asked(
        self, ops_store, queued
    ):
        # It has to be outside the ceiling it is queued behind, or nothing
        # parked would ever get in.
        queued(status=HitlItemStatus.SUSPENDED_QUEUE_FULL)

        assert ops_store.hitl.count_pending("tester") == 1
        assert ops_store.hitl.count_asked("tester") == 0

    def test_only_this_person_s_items_are_listed(self, ops_store, queued, moment):
        queued(user_id="someone-else")

        assert ops_store.hitl.list_visible("tester", now=moment) == []


class TestParkedItems:
    """The ones held outside a full queue."""

    def test_they_can_be_listed(self, ops_store, queued):
        item = queued(status=HitlItemStatus.SUSPENDED_QUEUE_FULL)

        assert [row.id for row in ops_store.hitl.list_parked("tester")] == [item.id]

    def test_a_waiting_item_is_not_among_them(self, ops_store, queued):
        queued()

        assert ops_store.hitl.list_parked("tester") == []


class TestDeferring:
    """What putting a question off actually records."""

    def test_it_counts_the_deferral_and_sets_a_return_date(
        self, ops_store, queued, moment
    ):
        item = queued()
        returns = moment + timedelta(hours=24)

        updated = ops_store.hitl.snooze(item.id, until=returns, at=moment)

        assert updated.snooze_count == 1
        assert updated.last_snoozed_at == moment
        assert updated.snoozed_until == returns

    def test_deferring_twice_counts_twice(self, ops_store, queued, moment):
        item = queued()

        ops_store.hitl.snooze(item.id, until=moment, at=moment)
        updated = ops_store.hitl.snooze(item.id, until=moment, at=moment)

        assert updated.snooze_count == 2

    def test_a_settled_item_cannot_be_deferred(self, ops_store, queued):
        item = queued()
        ops_store.hitl.update_status(item.id, HitlItemStatus.RESOLVED)

        with pytest.raises(IllegalStateTransitionError):
            ops_store.hitl.snooze(item.id, until=None, at=None)

    def test_deferring_something_that_does_not_exist_is_refused(
        self, ops_store, moment
    ):
        with pytest.raises(RecordNotFoundError):
            ops_store.hitl.snooze("nothing", until=moment, at=moment)


class TestWhatSettlesItself:
    """Only something already deferred once ever settles without an answer."""

    def test_a_long_deferred_item_is_found(self, ops_store, queued, moment):
        item = queued(snooze_count=1, last_snoozed_at=moment - timedelta(days=8))

        found = ops_store.hitl.find_auto_resolvable(
            "tester", cutoff=moment - timedelta(days=7)
        )

        assert [row.id for row in found] == [item.id]

    def test_an_untouched_item_is_never_found(self, ops_store, queued, moment):
        queued()

        assert (
            ops_store.hitl.find_auto_resolvable(
                "tester", cutoff=moment + timedelta(days=400)
            )
            == []
        )

    def test_a_recently_deferred_item_is_not_found(self, ops_store, queued, moment):
        queued(snooze_count=1, last_snoozed_at=moment)

        assert (
            ops_store.hitl.find_auto_resolvable(
                "tester", cutoff=moment - timedelta(days=7)
            )
            == []
        )

    def test_a_parked_item_is_never_found(self, ops_store, queued, moment):
        queued(
            status=HitlItemStatus.SUSPENDED_QUEUE_FULL,
            snooze_count=1,
            last_snoozed_at=moment - timedelta(days=8),
        )

        assert (
            ops_store.hitl.find_auto_resolvable(
                "tester", cutoff=moment - timedelta(days=7)
            )
            == []
        )


class TestSettling:
    """Answering a question, and refusing to answer it twice."""

    def test_it_records_the_answer_and_what_was_done(self, ops_store, queued):
        item = queued()

        settled = ops_store.hitl.update_status(
            item.id,
            HitlItemStatus.RESOLVED,
            resolution_choice=HitlResolutionChoice.ACTION_A,
            resolved_action=ReconciliationAction.REINFORCE,
        )

        assert settled.resolution_choice is HitlResolutionChoice.ACTION_A
        assert settled.resolved_action is ReconciliationAction.REINFORCE
        assert settled.resolved_at is not None

    def test_settling_twice_is_refused(self, ops_store, queued):
        item = queued()
        ops_store.hitl.update_status(item.id, HitlItemStatus.RESOLVED)

        with pytest.raises(IllegalStateTransitionError):
            ops_store.hitl.update_status(item.id, HitlItemStatus.RESOLVED)

    def test_settling_clears_a_pending_deferral(self, ops_store, queued, moment):
        # Leaving the date behind would keep a settled item out of any view
        # that reads it — confusing rather than harmful, and free to fix.
        item = queued(snoozed_until=moment + timedelta(hours=24))

        settled = ops_store.hitl.update_status(item.id, HitlItemStatus.RESOLVED)

        assert settled.snoozed_until is None

    def test_a_parked_item_can_still_be_let_in(self, ops_store, queued):
        item = queued(status=HitlItemStatus.SUSPENDED_QUEUE_FULL)

        updated = ops_store.hitl.update_status(item.id, HitlItemStatus.PENDING_HITL)

        assert updated.status is HitlItemStatus.PENDING_HITL


class TestKeepingWhatWasGoingToBeWritten:
    """The saved proposal, stored against the decision it belongs to."""

    def test_it_can_be_saved_and_read_back(self, ops_store, queued, make_proposal):
        proposal = make_proposal()
        queued(proposal=proposal)

        stored = ops_store.hitl.get_proposal(proposal.audit_node_id)

        assert stored is not None
        from lumen.schemas.pipeline import FrozenProposal

        assert FrozenProposal.model_validate_json(stored) == proposal

    def test_saving_it_again_replaces_it(self, ops_store, queued, make_proposal):
        # Re-running an entry produces the same held-back decisions, and the
        # second run's working is as good as the first's.
        proposal = make_proposal()
        queued(proposal=proposal)

        ops_store.hitl.save_proposal(proposal.audit_node_id, '{"replaced": true}')

        assert ops_store.hitl.get_proposal(proposal.audit_node_id) == '{"replaced": true}'

    def test_nothing_comes_back_when_nothing_was_saved(self, ops_store, queued):
        item = queued(save_proposal=False)

        assert ops_store.hitl.get_proposal(item.audit_node_id) is None


def test_the_migration_adds_and_removes_everything_it_owns(tmp_path):
    """The new columns and table can be added and taken away again."""
    from sqlalchemy import create_engine, inspect

    from lumen.operational.migrator import downgrade_to_base, upgrade_to_head

    engine = create_engine(f"sqlite:///{tmp_path / 'review.db'}")
    try:
        upgrade_to_head(engine)
        inspector = inspect(engine)
        columns = {column["name"] for column in inspector.get_columns("hitl_queue")}
        assert {"snoozed_until", "resolved_action"} <= columns
        assert "hitl_proposals" in inspector.get_table_names()

        downgrade_to_base(engine)
        assert "hitl_proposals" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
