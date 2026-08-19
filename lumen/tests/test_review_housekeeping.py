"""
Tests for the two things in the review queue that happen on a clock.

The rule worth guarding hardest: only something already deferred once ever
settles itself. Acting on an item nobody has looked at would make permanent
changes to somebody's history on the strength of their silence.
"""

from __future__ import annotations

from datetime import timedelta

from lumen.operational.enums import HitlItemStatus
from lumen.review import housekeeping
from lumen.schemas.enums import HitlResolutionChoice

CAP = 40


class Answers:
    """
    A stand-in for settling an item nobody answered.

    Records what it was asked to settle instead of settling it, so these
    tests are about which items get chosen rather than about what settling
    one writes.
    """

    def __init__(self, *, fails: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self._fails = fails or set()

    def __call__(self, item_id):
        self.calls.append(item_id)
        if item_id in self._fails:
            raise RuntimeError("this one cannot be settled")
        return None


def sweep(ops, resolver, *, now, days: int = 7, cap: int = CAP):
    """Run one housekeeping pass with the reading arguments filled in."""
    return housekeeping.sweep(
        "tester",
        ops=ops,
        resolver=resolver,
        cap=cap,
        auto_resolve_days=days,
        now=now,
    )


class TestSettlingWhatRanOutOfTime:
    """An item deferred and then forgotten eventually settles itself."""

    def test_a_long_deferred_item_settles_itself(self, ops_store, queued, moment):
        item = queued(snooze_count=1, last_snoozed_at=moment - timedelta(days=8))
        answers = Answers()

        report = sweep(ops_store, answers, now=moment)

        assert report.auto_resolved == [item.id]

    def test_it_asks_for_exactly_the_items_that_expired(
        self, ops_store, queued, make_proposal, moment
    ):
        # What settling one *means* is not decided here. This picks the
        # items; whoever is handed in decides what to do with them, which is
        # why a tie and a recommendation can be settled by the same pass
        # despite calling the same outcome different things.
        expired = queued(
            proposal=make_proposal(audit_id="d_expired"),
            snooze_count=1,
            last_snoozed_at=moment - timedelta(days=8),
        )
        queued(proposal=make_proposal(audit_id="d_fresh"))
        answers = Answers()

        sweep(ops_store, answers, now=moment)

        assert answers.calls == [expired.id]

    def test_an_item_nobody_has_touched_never_settles_itself(
        self, ops_store, queued, moment
    ):
        queued()
        answers = Answers()

        report = sweep(ops_store, answers, now=moment)

        assert report.auto_resolved == []
        assert answers.calls == []

    def test_an_item_deferred_recently_is_left_alone(
        self, ops_store, queued, moment
    ):
        queued(snooze_count=1, last_snoozed_at=moment - timedelta(days=2))

        report = sweep(ops_store, Answers(), now=moment)

        assert report.auto_resolved == []

    def test_the_waiting_period_is_configurable(self, ops_store, queued, moment):
        queued(snooze_count=1, last_snoozed_at=moment - timedelta(days=3))

        report = sweep(ops_store, Answers(), now=moment, days=2)

        assert len(report.auto_resolved) == 1

    def test_one_item_that_cannot_be_settled_does_not_stop_the_rest(
        self, ops_store, queued, make_proposal, moment
    ):
        # A single unanswerable question must not freeze the queue for
        # everything behind it.
        long_ago = moment - timedelta(days=8)
        broken = queued(
            proposal=make_proposal(audit_id="d_broken"),
            snooze_count=1,
            last_snoozed_at=long_ago,
        )
        fine = queued(
            proposal=make_proposal(audit_id="d_fine"),
            snooze_count=1,
            last_snoozed_at=long_ago,
        )

        report = sweep(ops_store, Answers(fails={broken.id}), now=moment)

        assert broken.id in report.failed
        assert fine.id in report.auto_resolved


class TestLettingParkedItemsIn:
    """Items held outside a full queue come in when there is room."""

    def test_a_parked_item_comes_in_when_there_is_space(
        self, ops_store, queued, moment
    ):
        item = queued(status=HitlItemStatus.SUSPENDED_QUEUE_FULL)

        report = sweep(ops_store, Answers(), now=moment)

        assert report.admitted == [item.id]
        assert ops_store.hitl.get(item.id).status is HitlItemStatus.PENDING_HITL

    def test_nothing_comes_in_while_the_queue_is_full(
        self, ops_store, queued, make_proposal, moment
    ):
        for index in range(3):
            queued(proposal=make_proposal(audit_id=f"d_asked_{index}"))
        parked = queued(
            proposal=make_proposal(audit_id="d_parked"),
            status=HitlItemStatus.SUSPENDED_QUEUE_FULL,
        )

        report = sweep(ops_store, Answers(), now=moment, cap=3)

        assert report.admitted == []
        assert (
            ops_store.hitl.get(parked.id).status
            is HitlItemStatus.SUSPENDED_QUEUE_FULL
        )

    def test_nothing_happens_when_nothing_is_parked(self, ops_store, queued, moment):
        queued()

        assert sweep(ops_store, Answers(), now=moment).admitted == []


class TestTheReport:
    """A pass that settles things on somebody's behalf says what it did."""

    def test_it_reports_what_is_still_waiting(self, ops_store, queued, moment):
        queued()

        report = sweep(ops_store, Answers(), now=moment)

        assert report.still_pending == 1
        assert report.oldest_pending_at is not None
        assert report.ran_at == moment

    def test_a_second_pass_finds_nothing_left_to_do(
        self, ops_store, queued, moment
    ):
        # Both halves pick their work by looking at what state things are
        # in, so running twice is harmless.
        queued(status=HitlItemStatus.SUSPENDED_QUEUE_FULL)

        first = sweep(ops_store, Answers(), now=moment)
        second = sweep(ops_store, Answers(), now=moment)

        assert first.admitted
        assert second.admitted == []

    def test_an_empty_queue_reports_nothing(self, ops_store, moment):
        report = sweep(ops_store, Answers(), now=moment)

        assert report.auto_resolved == []
        assert report.admitted == []
        assert report.still_pending == 0
        assert report.oldest_pending_at is None


def test_settling_happens_before_admitting(ops_store, queued, make_proposal, moment):
    """Closing something is what frees the room for the next thing in."""
    queued(
        proposal=make_proposal(audit_id="d_expired"),
        snooze_count=1,
        last_snoozed_at=moment - timedelta(days=8),
    )
    parked = queued(
        proposal=make_proposal(audit_id="d_parked"),
        status=HitlItemStatus.SUSPENDED_QUEUE_FULL,
    )

    # A stand-in that settles for real, so the room genuinely appears.
    def settle(item_id):
        ops_store.hitl.update_status(
            item_id,
            HitlItemStatus.AUTO_RESOLVED,
            resolution_choice=HitlResolutionChoice.AUTO_BRANCH_AFTER_SNOOZE,
        )

    report = sweep(ops_store, settle, now=moment, cap=1)

    assert parked.id in report.admitted
