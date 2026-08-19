"""
The two things in the review queue that happen on a clock.

An item somebody deferred and then never came back to eventually settles
itself, and items parked because the queue was full are let in once there is
room. Neither needs a person, and neither should wait for one.

There is no background timer in the system yet, so this runs whenever the
queue is touched — opened, or answered — and can also be run on demand. That
makes the queue self-correcting for anybody who uses it, and gives a
scheduler exactly one thing to call when there is one.

Only an item that was deferred at least once ever settles itself. Something
nobody has looked at waits indefinitely, however old it gets. Deferring
something is a signal that it was seen and weighed; never opening it is not,
and acting on silence would let the system make permanent changes to
somebody's history that they never agreed to.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Protocol

from lumen.operational.enums import HitlItemStatus
from lumen.review import capacity
from lumen.review.contracts import SweepReport

logger = logging.getLogger(__name__)


class Resolver(Protocol):
    """
    Whatever can settle an item nobody answered.

    Named as a shape rather than a class so this module never has to know
    how settling works. It picks *which* items have run out of time; the
    thing handed in decides what settling one means — including which of a
    layout's words for "record it on its own" applies, which differs between
    a recommendation and a tie.
    """

    def __call__(self, item_id: str) -> object:
        ...


def sweep(
    user_id: str,
    *,
    ops,
    resolver: Resolver,
    cap: int,
    auto_resolve_days: int,
    now: datetime,
) -> SweepReport:
    """
    Settle what has run out of time, then let in what has been waiting.

    In that order, because settling something frees the room that lets the
    next thing in. Doing it the other way round would leave a queue that is
    briefly full of items it is about to close.

    Nothing here is destructive if it runs twice. Both halves pick their work
    by looking at what state things are in, so a second pass a second later
    finds nothing left to do.
    """
    auto_resolved, failed = _auto_resolve(
        user_id, ops=ops, resolver=resolver, cutoff=now - timedelta(days=auto_resolve_days)
    )
    admitted = _admit(user_id, ops=ops, cap=cap)

    report = SweepReport(
        ran_at=now,
        auto_resolved=auto_resolved,
        admitted=admitted,
        failed=failed,
        still_pending=ops.hitl.count_pending(user_id),
        oldest_pending_at=ops.hitl.oldest_pending_at(user_id),
    )

    if auto_resolved or admitted or failed:
        logger.info(
            "review queue housekeeping",
            extra={
                "auto_resolved": len(auto_resolved),
                "admitted": len(admitted),
                "failed": len(failed),
                "still_pending": report.still_pending,
            },
        )
    return report


def _auto_resolve(
    user_id: str, *, ops, resolver: Resolver, cutoff: datetime
) -> tuple[list[str], list[str]]:
    """
    Close the items that were deferred and then ran out of time.

    They become their own separate thing — the same outcome as turning the
    suggestion down, recorded differently so the graph never claims somebody
    chose it.

    One item that cannot be settled is logged and stepped over. Letting it
    stop the pass would mean a single unanswerable question freezes the whole
    queue for everybody behind it.
    """
    settled: list[str] = []
    failed: list[str] = []

    for item in ops.hitl.find_auto_resolvable(user_id, cutoff=cutoff):
        try:
            resolver(item.id)
        except Exception:
            logger.warning(
                "could not settle a review item that ran out of time",
                extra={"item_id": item.id},
                exc_info=True,
            )
            failed.append(item.id)
            continue
        settled.append(item.id)

    return settled, failed


def _admit(user_id: str, *, ops, cap: int) -> list[str]:
    """
    Let in whatever was parked because the queue was full.

    In the queue's own order, so the most important thing waiting outside is
    the first thing let in rather than the last.
    """
    parked = ops.hitl.list_parked(user_id)
    if not parked:
        return []

    # Measured against what is actually being asked, not everything
    # unresolved. Counting the parked items against the ceiling they are
    # queued behind would mean nothing ever got in.
    letting_in = capacity.admissions(
        pending=ops.hitl.count_asked(user_id), cap=cap, parked=parked
    )
    for item_id in letting_in:
        ops.hitl.update_status(item_id, HitlItemStatus.PENDING_HITL)
    return letting_in


__all__ = ["sweep", "Resolver"]
