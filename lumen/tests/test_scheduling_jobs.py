"""
Tests for the three jobs that drive services which already existed.

Each of these goals shipped an endpoint and a note saying something would
eventually call it. What is being checked here is small on purpose: that the
right service is called, that the count means what it says, and that a service
having a bad day becomes a failed job rather than a stopped clock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lumen.config import SchedulerConfig
from lumen.review.contracts import SweepReport
from lumen.scheduling.jobs import ReportsDue, ReviewSweep, ShadowScan
from lumen.pipeline.macroextraction.contracts import MacroWindow, ReportOutcome
from lumen.schemas.enums import MacroRunStatus, ReportType

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


def outcome(status: MacroRunStatus, report_id: str = "macro_1") -> ReportOutcome:
    """One report run, as the service reports it."""
    return ReportOutcome(
        status=status,
        window=MacroWindow(
            report_type=ReportType.WEEKLY,
            period_start=NOW - timedelta(days=7),
            period_end=NOW,
        ),
        report_id=report_id,
    )


class Reporter:
    """A stand-in for the thing that writes reports."""

    def __init__(self, due=(), shadow=MacroRunStatus.SKIPPED_EXISTING) -> None:
        self._due = list(due)
        self._shadow = shadow
        self.asked_due: list[datetime] = []
        self.asked_shadow: list[datetime] = []

    def run_due(self, now):
        self.asked_due.append(now)
        return self._due

    def run_shadow(self, now):
        self.asked_shadow.append(now)
        return outcome(self._shadow, "macro_shadow")


class Reviewer:
    """A stand-in for the review queue."""

    def __init__(self, **counts) -> None:
        self._counts = counts
        self.swept: list[str] = []

    def sweep(self, user_id):
        self.swept.append(user_id)
        return SweepReport(ran_at=NOW, **self._counts)


class TestWritingTheReportsThatAreDue:
    def test_it_asks_the_service_for_the_moment_it_was_given(self):
        reporter = Reporter()

        ReportsDue(reporter).run(NOW)

        assert reporter.asked_due == [NOW]

    def test_it_counts_only_the_reports_actually_written(self):
        # A period already covered costs nothing and is not news.
        reporter = Reporter(
            due=[
                outcome(MacroRunStatus.WRITTEN, "macro_1"),
                outcome(MacroRunStatus.SKIPPED_EXISTING, "macro_2"),
                outcome(MacroRunStatus.EMPTY_WINDOW, "macro_3"),
            ]
        )

        assert ReportsDue(reporter).run(NOW) == 1

    def test_nothing_due_is_nothing_done(self):
        assert ReportsDue(Reporter()).run(NOW) == 0

    def test_it_takes_its_interval_from_the_settings(self):
        job = ReportsDue(Reporter(), config=SchedulerConfig(reports_every_seconds=120))

        assert job.every == timedelta(seconds=120)


class TestLookingForSomethingShifting:
    def test_a_quiet_scan_writes_nothing_and_says_so(self):
        assert ShadowScan(Reporter()).run(NOW) == 0

    def test_a_burst_is_worth_one(self):
        reporter = Reporter(shadow=MacroRunStatus.WRITTEN)

        assert ShadowScan(reporter).run(NOW) == 1

    def test_it_asks_about_the_moment_it_was_given(self):
        reporter = Reporter()

        ShadowScan(reporter).run(NOW)

        assert reporter.asked_shadow == [NOW]


class TestHowOftenEachRuns:
    def test_the_shadow_scan_takes_its_interval_from_the_settings(self):
        job = ShadowScan(Reporter(), config=SchedulerConfig(shadow_every_seconds=90))

        assert job.every == timedelta(seconds=90)

    def test_the_sweep_takes_its_interval_from_the_settings(self):
        job = ReviewSweep(
            Reviewer(), user_id="tester", config=SchedulerConfig(sweep_every_seconds=45)
        )

        assert job.every == timedelta(seconds=45)


class TestTidyingTheQueue:
    def test_it_sweeps_for_the_configured_person(self):
        reviewer = Reviewer()

        ReviewSweep(reviewer, user_id="tester").run(NOW)

        assert reviewer.swept == ["tester"]

    def test_everything_it_moved_is_counted(self):
        reviewer = Reviewer(
            auto_resolved=["a", "b"], admitted=["c"], closed=["d"], still_pending=2
        )

        assert ReviewSweep(reviewer, user_id="tester").run(NOW) == 4

    def test_a_queue_with_nothing_to_do_is_nothing_done(self):
        assert ReviewSweep(Reviewer(), user_id="tester").run(NOW) == 0


class TestWhenAServiceIsHavingABadDay:
    @pytest.mark.parametrize(
        "job",
        [
            lambda broken: ReportsDue(broken),
            lambda broken: ShadowScan(broken),
        ],
    )
    def test_the_failure_reaches_the_scheduler_rather_than_being_hidden(self, job):
        # The scheduler is what decides that a failed job costs one turn.
        # Swallowing it here would make a broken service look like a quiet one.
        class Broken:
            def run_due(self, now):
                raise RuntimeError("the graph is unreachable")

            def run_shadow(self, now):
                raise RuntimeError("the graph is unreachable")

        with pytest.raises(RuntimeError):
            job(Broken()).run(NOW)
