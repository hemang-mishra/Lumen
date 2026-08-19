"""
Tests for how many questions a person is asked at once.

The ceiling exists to protect attention, and the thing worth guarding
against is it turning into permission to decide things unasked. Nothing here
ever settles anything — past the ceiling, questions wait outside.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from lumen.operational.enums import HitlItemStatus
from lumen.operational.schemas import HitlQueueItemRecord
from lumen.review import capacity
from lumen.schemas.enums import HitlEntryType, SignalStrength

CAP = 40


def parked(
    item_id: str,
    *,
    priority: int = 2,
    signal: int = 1,
    minutes_old: int = 0,
    moment=None,
) -> HitlQueueItemRecord:
    """One question waiting outside a full queue."""
    from lumen.tests.conftest import MOMENT

    return HitlQueueItemRecord(
        id=item_id,
        user_id="tester",
        audit_node_id=f"d_{item_id}",
        entry_type=HitlEntryType.BELOW_THRESHOLD,
        status=HitlItemStatus.SUSPENDED_QUEUE_FULL,
        priority_rank=priority,
        signal_rank=signal,
        created_at=(moment or MOMENT) - timedelta(minutes=minutes_old),
    )


class TestWhetherThereIsRoom:
    """One question more, or not."""

    @pytest.mark.parametrize(
        ("pending", "expected"), [(0, True), (39, True), (40, False), (41, False)]
    )
    def test_room_runs_out_at_the_ceiling(self, pending, expected):
        assert capacity.has_room(pending=pending, cap=CAP) is expected

    def test_a_new_question_is_asked_while_there_is_room(self):
        assert (
            capacity.entry_status(pending=10, cap=CAP)
            is HitlItemStatus.PENDING_HITL
        )

    def test_a_new_question_waits_outside_a_full_queue(self):
        # Parked, never decided. The ceiling protects attention; it is not
        # permission to guess.
        assert (
            capacity.entry_status(pending=CAP, cap=CAP)
            is HitlItemStatus.SUSPENDED_QUEUE_FULL
        )


class TestLettingThingsIn:
    """What comes in when answering something makes room."""

    def test_nothing_comes_in_while_the_queue_is_full(self):
        assert (
            capacity.admissions(pending=CAP, cap=CAP, parked=[parked("a")]) == []
        )

    def test_nothing_comes_in_when_nothing_is_waiting(self):
        assert capacity.admissions(pending=0, cap=CAP, parked=[]) == []

    def test_only_as_many_as_fit_come_in(self):
        waiting = [parked(f"item-{index}") for index in range(5)]

        assert len(capacity.admissions(pending=CAP - 2, cap=CAP, parked=waiting)) == 2

    def test_ties_come_in_before_low_confidence_items(self):
        # A tie parked behind twenty ordinary items would otherwise be the
        # last thing asked, which is the wrong way round.
        waiting = [parked("ordinary", priority=2), parked("tie", priority=1)]

        assert capacity.admissions(pending=CAP - 1, cap=CAP, parked=waiting) == ["tie"]

    def test_stronger_signals_come_in_first(self):
        waiting = [parked("routine", signal=1), parked("critical", signal=3)]

        assert (
            capacity.admissions(pending=CAP - 1, cap=CAP, parked=waiting)
            == ["critical"]
        )

    def test_the_longest_wait_breaks_a_draw(self):
        waiting = [parked("newer", minutes_old=0), parked("older", minutes_old=60)]

        assert (
            capacity.admissions(pending=CAP - 1, cap=CAP, parked=waiting) == ["older"]
        )

    def test_an_item_stored_before_the_ranks_existed_still_gets_in(self):
        # Sorted last rather than crashing. It is worth asking about, just
        # not first.
        from lumen.tests.conftest import MOMENT

        old = HitlQueueItemRecord(
            id="ancient",
            user_id="tester",
            audit_node_id="d_ancient",
            entry_type=HitlEntryType.BELOW_THRESHOLD,
            status=HitlItemStatus.SUSPENDED_QUEUE_FULL,
            created_at=MOMENT,
        )

        assert capacity.admissions(pending=0, cap=CAP, parked=[old]) == ["ancient"]

    def test_an_item_with_no_date_still_gets_in(self):
        nameless = HitlQueueItemRecord(
            id="undated",
            user_id="tester",
            audit_node_id="d_undated",
            entry_type=HitlEntryType.BELOW_THRESHOLD,
            signal_strength=SignalStrength.STANDARD,
            status=HitlItemStatus.SUSPENDED_QUEUE_FULL,
            priority_rank=2,
            signal_rank=1,
        )

        assert capacity.admissions(pending=0, cap=CAP, parked=[nameless]) == ["undated"]

    def test_a_queue_over_its_ceiling_lets_nothing_in(self):
        # Reachable if the cap is lowered while items are already waiting.
        assert (
            capacity.admissions(pending=CAP + 5, cap=CAP, parked=[parked("a")]) == []
        )
