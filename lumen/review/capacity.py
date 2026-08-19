"""
How many questions a person is asked at once.

The queue has a ceiling. Past it, new questions are parked rather than
asked — and parked is not the same as decided. Nothing is guessed to make
room; the items wait, and are let in the moment somebody answers something
and space appears.

The ceiling exists to protect attention, not to license the system to settle
things on its own. A queue of two hundred is a queue nobody opens, which is
the same as having no review at all except that it also feels like a
reproach.

Plain arithmetic, no database. The cap can be reasoned about on its own.
"""

from __future__ import annotations

from collections.abc import Sequence

from lumen.operational.enums import HitlItemStatus
from lumen.operational.schemas import HitlQueueItemRecord


def has_room(*, pending: int, cap: int) -> bool:
    """Whether one more question can be asked without passing the ceiling."""
    return pending < cap


def entry_status(*, pending: int, cap: int) -> HitlItemStatus:
    """
    Where a newly raised question starts out.

    Waiting for the person while there is room, parked once there is not.
    Never decided — the only two states a new question can be in are "asked"
    and "not asked yet".
    """
    if has_room(pending=pending, cap=cap):
        return HitlItemStatus.PENDING_HITL
    return HitlItemStatus.SUSPENDED_QUEUE_FULL


def admissions(
    *,
    pending: int,
    cap: int,
    parked: Sequence[HitlQueueItemRecord],
) -> list[str]:
    """
    Which parked questions fit now, in the order they should be let in.

    Ordered the same way the queue itself is ordered, so a tie parked behind
    twenty routine items is admitted first. Letting them in by arrival time
    would mean the most important thing waiting is the last thing asked.
    """
    room = max(cap - pending, 0)
    if room == 0 or not parked:
        return []

    in_order = sorted(parked, key=_queue_order)
    return [item.id for item in in_order[:room]]


def _queue_order(item: HitlQueueItemRecord) -> tuple[int, int, float]:
    """
    The queue's ordering as a sort key.

    Ties first, then stronger signals, then whatever has waited longest.
    Missing ranks sort last rather than crashing: an item stored before the
    ranks existed is worth asking about, just not first.
    """
    created = item.created_at.timestamp() if item.created_at else 0.0
    return (
        item.priority_rank if item.priority_rank is not None else 99,
        -(item.signal_rank if item.signal_rank is not None else 0),
        created,
    )


__all__ = ["has_room", "entry_status", "admissions"]
