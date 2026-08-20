"""
Tests for the clock every recurring job runs on.

All of it drives `tick` directly with a chosen moment. A background loop whose
rules can only be exercised by waiting is a background loop nobody checks, and
the point of taking the clock as an argument is that none of these tests
sleeps or starts a thread.

The three rules being checked are all about restraint: never two passes at
once, a job that throws costs only itself, and constructing one starts
nothing.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from lumen.config import SchedulerConfig
from lumen.scheduling import Scheduler

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


class Counter:
    """A job that records when it ran and reports a chosen count."""

    def __init__(self, name: str = "counter", *, every_seconds: int = 60, did: int = 1):
        self.name = name
        self.every = timedelta(seconds=every_seconds)
        self.did = did
        self.runs: list[datetime] = []

    def run(self, now: datetime) -> int:
        self.runs.append(now)
        return self.did


class Broken:
    """A job that cannot run."""

    name = "broken"
    every = timedelta(seconds=60)

    def run(self, now: datetime) -> int:
        raise RuntimeError("the store is unreachable")


def stopped(**overrides) -> SchedulerConfig:
    """Settings with the thread switched off, since these drive tick by hand."""
    return SchedulerConfig(enabled=False, **overrides)


class TestWhenAJobIsDue:
    def test_a_job_with_no_history_runs_immediately(self):
        # The useful direction on a restart: a machine that has been off for
        # a day should catch up rather than wait out a full interval first.
        job = Counter()

        Scheduler([job], config=stopped()).tick(NOW)

        assert job.runs == [NOW]

    def test_it_does_not_run_again_before_its_interval(self):
        job = Counter(every_seconds=300)
        clock = Scheduler([job], config=stopped())

        clock.tick(NOW)
        clock.tick(NOW + timedelta(seconds=299))

        assert len(job.runs) == 1

    def test_it_runs_again_once_the_interval_has_passed(self):
        job = Counter(every_seconds=300)
        clock = Scheduler([job], config=stopped())

        clock.tick(NOW)
        clock.tick(NOW + timedelta(seconds=300))

        assert len(job.runs) == 2

    def test_a_job_that_is_not_due_says_so_rather_than_saying_nothing(self):
        # "Not due" and "ran and found nothing" are different facts, and from
        # outside they look identical.
        job = Counter(every_seconds=300)
        clock = Scheduler([job], config=stopped())
        clock.tick(NOW)

        report = clock.tick(NOW + timedelta(seconds=1))

        assert report.outcomes[0].ran is False
        assert report.did_anything is False

    def test_each_job_keeps_its_own_interval(self):
        often = Counter("often", every_seconds=60)
        rarely = Counter("rarely", every_seconds=3600)
        clock = Scheduler([often, rarely], config=stopped())

        clock.tick(NOW)
        clock.tick(NOW + timedelta(seconds=60))

        assert len(often.runs) == 2
        assert len(rarely.runs) == 1


class TestWhatAPassReports:
    def test_it_says_what_each_job_did(self):
        clock = Scheduler([Counter(did=4)], config=stopped())

        report = clock.tick(NOW)

        assert report.outcomes[0].did == 4
        assert report.outcomes[0].worked is True

    def test_a_job_that_found_nothing_is_not_a_job_that_worked(self):
        clock = Scheduler([Counter(did=0)], config=stopped())

        report = clock.tick(NOW)

        assert report.outcomes[0].ran is True
        assert report.outcomes[0].worked is False

    def test_a_job_returning_nothing_at_all_counts_as_none(self):
        class Silent:
            name = "silent"
            every = timedelta(seconds=1)

            def run(self, now):
                return None

        report = Scheduler([Silent()], config=stopped()).tick(NOW)

        assert report.outcomes[0].did == 0


class TestWhenAJobBreaks:
    def test_it_costs_that_job_and_nothing_else(self):
        # One bad job killing the thread looks, from outside, like a system
        # that quietly stopped doing everything because one thing broke once.
        good = Counter("good")
        clock = Scheduler([Broken(), good], config=stopped())

        report = clock.tick(NOW)

        assert good.runs == [NOW]
        assert report.failures == ("broken",)

    def test_the_failure_is_named_rather_than_swallowed(self):
        report = Scheduler([Broken()], config=stopped()).tick(NOW)

        assert report.outcomes[0].failure == "RuntimeError"

    def test_a_broken_job_still_waits_its_interval_before_trying_again(self):
        # Otherwise a job failing fast would be retried on every pass, which
        # is how one unreachable store fills a log in an afternoon.
        job = Broken()
        clock = Scheduler([job], config=stopped())

        clock.tick(NOW)
        second = clock.tick(NOW + timedelta(seconds=1))

        assert second.outcomes[0].ran is False


class TestNeverTwoAtOnce:
    def test_a_pass_arriving_during_one_is_skipped(self):
        # These jobs are minutes long and none is urgent. A queue that grows
        # while the machine is busy is how a laptop waking from sleep starts
        # nine reports.
        started = threading.Event()
        allowed = threading.Event()
        reports = []

        class Slow:
            name = "slow"
            every = timedelta(seconds=1)

            def run(self, now):
                started.set()
                allowed.wait(timeout=5)
                return 1

        clock = Scheduler([Slow()], config=stopped())
        thread = threading.Thread(target=lambda: clock.tick(NOW))
        thread.start()
        started.wait(timeout=5)

        reports.append(clock.tick(NOW + timedelta(seconds=5)))
        allowed.set()
        thread.join(timeout=5)

        assert reports[0].skipped is True
        assert reports[0].outcomes == ()

    def test_a_skipped_pass_is_still_announced(self):
        # A system that is always skipping is one whose jobs take longer than
        # its interval, and that is invisible otherwise.
        seen = []
        clock = Scheduler([], config=stopped(), on_report=seen.append)
        clock._running.acquire()

        clock.tick(NOW)

        assert seen[0].skipped is True


class TestTellingSomebody:
    def test_every_pass_is_announced(self):
        seen = []
        clock = Scheduler([Counter()], config=stopped(), on_report=seen.append)

        clock.tick(NOW)

        assert len(seen) == 1
        assert seen[0].outcomes[0].name == "counter"

    def test_a_listener_that_breaks_does_not_stop_the_clock(self):
        # A page with a broken connection must not be able to stop the
        # pipeline from running.
        def explode(report):
            raise RuntimeError("the socket is gone")

        job = Counter()
        clock = Scheduler([job], config=stopped(), on_report=explode)

        clock.tick(NOW)

        assert job.runs == [NOW]


class TestStartingAndStopping:
    def test_constructing_one_starts_nothing(self):
        # So no test ever races a thread it did not ask for.
        clock = Scheduler([Counter()], config=SchedulerConfig(poll_seconds=0.01))

        assert clock.running is False

    def test_a_deployment_with_the_clock_off_never_starts_one(self):
        clock = Scheduler([Counter()], config=stopped())

        clock.start()

        assert clock.running is False

    def test_starting_twice_is_one_thread(self):
        clock = Scheduler([], config=SchedulerConfig(poll_seconds=5))
        try:
            clock.start()
            first = clock._thread
            clock.start()

            assert clock._thread is first
        finally:
            clock.stop(timeout=2)

    def test_stopping_one_that_never_started_is_fine(self):
        Scheduler([], config=stopped()).stop()

    def test_a_thread_that_will_not_stop_is_reported_rather_than_waited_on(self):
        # Shutting down has to finish. A job that has hung is worth a warning
        # and not a service that will not exit.
        holding = threading.Event()

        class Hangs:
            name = "hangs"
            every = timedelta(seconds=0)

            def run(self, now):
                holding.wait(timeout=5)
                return 0

        clock = Scheduler([Hangs()], config=SchedulerConfig(poll_seconds=0.01))
        try:
            clock.start()
            threading.Event().wait(0.2)
            clock.stop(timeout=0.05)

            assert clock._thread is None
        finally:
            holding.set()

    def test_a_pass_that_fails_outright_does_not_kill_the_thread(self):
        # Everything inside a pass is already guarded, so reaching this means
        # something unforeseen. The thread still has to outlive it.
        seen = []

        def explode(report):
            seen.append(report)
            raise BaseException("something nobody expected")

        clock = Scheduler([], config=SchedulerConfig(poll_seconds=0.01))
        clock._on_report = None
        original = clock.tick

        def broken(now=None):
            seen.append(now)
            raise RuntimeError("a pass failed outright")

        clock.tick = broken
        try:
            clock.start()
            threading.Event().wait(0.15)

            assert clock.running is True
            assert seen
        finally:
            clock.tick = original
            clock.stop(timeout=2)

    def test_it_actually_runs_when_started(self):
        job = Counter(every_seconds=0)
        clock = Scheduler([job], config=SchedulerConfig(poll_seconds=0.01))
        try:
            clock.start()
            deadline = threading.Event()
            deadline.wait(timeout=1.0)
        finally:
            clock.stop(timeout=2)

        assert job.runs
        assert clock.running is False
