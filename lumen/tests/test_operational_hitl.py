"""Tests for the queue of items waiting on the user."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lumen.operational.enums import HitlEntryType, HitlItemStatus
from lumen.operational.repositories import RecordNotFoundError
from lumen.operational.schemas import HitlQueueItemRecord
from lumen.schemas.enums import HitlResolutionChoice, ReconciliationAction, SignalStrength

BASE_TIME = datetime(2026, 6, 11, 9, 0, tzinfo=UTC)


def _item(item_id: str, **overrides) -> HitlQueueItemRecord:
    defaults = {
        "id": item_id,
        "user_id": "local",
        "audit_node_id": f"d_{item_id}",
        "entry_type": HitlEntryType.BELOW_THRESHOLD,
        "signal_strength": SignalStrength.STANDARD,
        "created_at": BASE_TIME,
    }
    defaults.update(overrides)
    return HitlQueueItemRecord(**defaults)


class TestEnqueue:
    def test_an_item_is_stored(self, ops_store):
        ops_store.hitl.enqueue(_item("a"))
        assert ops_store.hitl.get("a") is not None

    def test_a_new_item_waits_for_the_user(self, ops_store):
        ops_store.hitl.enqueue(_item("a"))
        assert ops_store.hitl.get("a").status == HitlItemStatus.PENDING_HITL

    def test_the_full_detail_survives_the_round_trip(self, ops_store):
        ops_store.hitl.enqueue(
            _item(
                "a",
                entry_type=HitlEntryType.AMBIGUOUS_TIE,
                recommended_action=ReconciliationAction.EVOLVE,
                candidate_a_node_id="pat_x",
                candidate_b_node_id="pat_y",
                confidence_a=0.91,
                confidence_b=0.88,
                context_summary="Two patterns scored too close to separate.",
                observation_id="obs_2026_06_11_004",
                episode_id="ep_2026_06_11_001",
            )
        )
        stored = ops_store.hitl.get("a")
        assert stored.recommended_action == ReconciliationAction.EVOLVE
        assert stored.confidence_a == 0.91
        assert stored.candidate_b_node_id == "pat_y"
        assert stored.observation_id == "obs_2026_06_11_004"

    def test_ranks_are_worked_out_for_the_caller(self, ops_store):
        """
        Callers pass meaning — a type and a signal strength. Turning that into
        sort positions is the store's job.
        """
        ops_store.hitl.enqueue(
            _item(
                "a",
                entry_type=HitlEntryType.AMBIGUOUS_TIE,
                signal_strength=SignalStrength.CRITICAL,
            )
        )
        stored = ops_store.hitl.get("a")
        assert stored.priority_rank == 1
        assert stored.signal_rank == 3

    def test_the_signal_strength_reads_back(self, ops_store):
        ops_store.hitl.enqueue(_item("a", signal_strength=SignalStrength.HIGH))
        assert ops_store.hitl.get("a").signal_strength == SignalStrength.HIGH

    def test_it_adopts_the_current_trace_id(self, ops_store, bound_trace):
        ops_store.hitl.enqueue(_item("a"))
        assert ops_store.hitl.get("a").trace_id == bound_trace

    def test_an_unknown_item_reads_back_as_nothing(self, ops_store):
        assert ops_store.hitl.get("nope") is None


class TestLookupByDecision:
    def test_an_item_is_found_by_its_decision(self, ops_store):
        """The graph holds the decision; this table holds the queue state."""
        ops_store.hitl.enqueue(_item("a", audit_node_id="d_2026_06_11_007"))
        found = ops_store.hitl.get_by_audit_node("d_2026_06_11_007")
        assert found.id == "a"

    def test_an_unknown_decision_finds_nothing(self, ops_store):
        assert ops_store.hitl.get_by_audit_node("d_missing") is None


class TestPriorityOrdering:
    def test_ties_come_before_everything_else(self, ops_store):
        """
        A tie outranks a low-confidence call even when the low-confidence one
        carries a stronger signal.
        """
        ops_store.hitl.enqueue(
            _item(
                "below",
                entry_type=HitlEntryType.BELOW_THRESHOLD,
                signal_strength=SignalStrength.CRITICAL,
            )
        )
        ops_store.hitl.enqueue(
            _item(
                "tie",
                entry_type=HitlEntryType.AMBIGUOUS_TIE,
                signal_strength=SignalStrength.STANDARD,
            )
        )

        assert [i.id for i in ops_store.hitl.list_pending("local")] == ["tie", "below"]

    def test_failed_extractions_come_last(self, ops_store):
        for item_id, entry_type in [
            ("failed", HitlEntryType.EXTRACTION_FAILED),
            ("tie", HitlEntryType.AMBIGUOUS_TIE),
            ("below", HitlEntryType.BELOW_THRESHOLD),
        ]:
            ops_store.hitl.enqueue(_item(item_id, entry_type=entry_type))

        assert [i.id for i in ops_store.hitl.list_pending("local")] == [
            "tie", "below", "failed",
        ]

    def test_stronger_signals_come_first_within_a_type(self, ops_store):
        for item_id, strength in [
            ("standard", SignalStrength.STANDARD),
            ("critical", SignalStrength.CRITICAL),
            ("high", SignalStrength.HIGH),
        ]:
            ops_store.hitl.enqueue(_item(item_id, signal_strength=strength))

        assert [i.id for i in ops_store.hitl.list_pending("local")] == [
            "critical", "high", "standard",
        ]

    def test_older_items_come_first_when_otherwise_equal(self, ops_store):
        ops_store.hitl.enqueue(_item("newer", created_at=BASE_TIME + timedelta(hours=2)))
        ops_store.hitl.enqueue(_item("older", created_at=BASE_TIME))

        assert [i.id for i in ops_store.hitl.list_pending("local")] == ["older", "newer"]

    def test_all_three_rules_apply_together(self, ops_store):
        ops_store.hitl.enqueue(
            _item("tie_low", entry_type=HitlEntryType.AMBIGUOUS_TIE,
                  signal_strength=SignalStrength.STANDARD)
        )
        ops_store.hitl.enqueue(
            _item("tie_high", entry_type=HitlEntryType.AMBIGUOUS_TIE,
                  signal_strength=SignalStrength.CRITICAL)
        )
        ops_store.hitl.enqueue(
            _item("below_high", entry_type=HitlEntryType.BELOW_THRESHOLD,
                  signal_strength=SignalStrength.CRITICAL)
        )

        assert [i.id for i in ops_store.hitl.list_pending("local")] == [
            "tie_high", "tie_low", "below_high",
        ]

    def test_the_limit_is_respected(self, ops_store):
        for index in range(5):
            ops_store.hitl.enqueue(_item(f"item_{index}"))
        assert len(ops_store.hitl.list_pending("local", limit=3)) == 3


class TestPendingSet:
    def test_settled_items_leave_the_queue(self, ops_store):
        ops_store.hitl.enqueue(_item("a"))
        ops_store.hitl.update_status("a", HitlItemStatus.RESOLVED)
        assert ops_store.hitl.list_pending("local") == []

    def test_items_held_back_by_a_full_queue_still_count_as_waiting(self, ops_store):
        """
        They have not been decided, only deferred, so they still show up as
        outstanding work.
        """
        ops_store.hitl.enqueue(_item("a", status=HitlItemStatus.SUSPENDED_QUEUE_FULL))
        assert [i.id for i in ops_store.hitl.list_pending("local")] == ["a"]

    def test_only_this_user_is_counted(self, ops_store):
        ops_store.hitl.enqueue(_item("mine", user_id="local"))
        ops_store.hitl.enqueue(_item("theirs", user_id="someone_else"))
        assert ops_store.hitl.count_pending("local") == 1

    def test_counting_an_empty_queue_gives_zero(self, ops_store):
        assert ops_store.hitl.count_pending("local") == 0

    def test_the_count_matches_the_list(self, ops_store):
        for index in range(4):
            ops_store.hitl.enqueue(_item(f"item_{index}"))
        ops_store.hitl.update_status("item_0", HitlItemStatus.RESOLVED)
        assert ops_store.hitl.count_pending("local") == 3


class TestUpdateStatus:
    def test_resolving_records_when_and_how(self, ops_store):
        ops_store.hitl.enqueue(_item("a"))
        updated = ops_store.hitl.update_status(
            "a", HitlItemStatus.RESOLVED, HitlResolutionChoice.ACTION_A
        )
        assert updated.status == HitlItemStatus.RESOLVED
        assert updated.resolution_choice == HitlResolutionChoice.ACTION_A
        assert updated.resolved_at is not None

    def test_automatic_resolution_is_recorded_too(self, ops_store):
        ops_store.hitl.enqueue(_item("a"))
        updated = ops_store.hitl.update_status(
            "a",
            HitlItemStatus.AUTO_RESOLVED,
            HitlResolutionChoice.AUTO_BRANCH_AFTER_SNOOZE,
        )
        assert updated.resolved_at is not None
        assert updated.resolution_choice == HitlResolutionChoice.AUTO_BRANCH_AFTER_SNOOZE

    def test_holding_an_item_back_does_not_settle_it(self, ops_store):
        ops_store.hitl.enqueue(_item("a"))
        updated = ops_store.hitl.update_status("a", HitlItemStatus.SUSPENDED_QUEUE_FULL)
        assert updated.resolved_at is None

    def test_updating_an_unknown_item_is_refused(self, ops_store):
        with pytest.raises(RecordNotFoundError, match="no review item"):
            ops_store.hitl.update_status("ghost", HitlItemStatus.RESOLVED)
