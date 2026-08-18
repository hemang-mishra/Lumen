"""
Tests for the calendar arithmetic behind periodic reports.

Worth testing carefully out of proportion to its size. A mistake here is
invisible in a way a mistake elsewhere is not — a report covering eight days
instead of seven still looks exactly like a report, and the figures in it are
all internally consistent. Nothing downstream can catch it.

The cases cluster around three things: where the boundaries fall (including
the awkward ones, year ends and quarter starts), whether a period is ready to
be reported on, and which overdue periods win a limited number of slots.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lumen.config import MacroConfig
from lumen.pipeline.macroextraction import windows
from lumen.schemas.enums import ReportType

UTC = timezone.utc


def at(year: int, month: int, day: int, hour: int = 12) -> datetime:
    """One moment, spelled out so a test reads as a date rather than a number."""
    return datetime(year, month, day, hour, tzinfo=UTC)


class TestWherePeriodsBegin:
    def test_a_week_runs_monday_to_monday(self):
        window = windows.window_for(ReportType.WEEKLY, at(2026, 5, 14))

        assert window.start_date.isoformat() == "2026-05-11"
        assert window.end_date.isoformat() == "2026-05-18"

    def test_a_week_containing_a_monday_starts_on_it(self):
        window = windows.window_for(ReportType.WEEKLY, at(2026, 5, 11, 0))

        assert window.start_date.isoformat() == "2026-05-11"

    def test_a_month_runs_first_to_first(self):
        window = windows.window_for(ReportType.MONTHLY, at(2026, 5, 20))

        assert window.start_date.isoformat() == "2026-05-01"
        assert window.end_date.isoformat() == "2026-06-01"

    def test_december_rolls_into_the_next_year(self):
        window = windows.window_for(ReportType.MONTHLY, at(2026, 12, 20))

        assert window.end_date.isoformat() == "2027-01-01"

    @pytest.mark.parametrize(
        "month,expected_start,expected_end",
        [
            (2, "2026-01-01", "2026-04-01"),
            (5, "2026-04-01", "2026-07-01"),
            (8, "2026-07-01", "2026-10-01"),
            (11, "2026-10-01", "2027-01-01"),
        ],
    )
    def test_quarters_land_on_the_four_calendar_boundaries(
        self, month, expected_start, expected_end
    ):
        window = windows.window_for(ReportType.QUARTERLY, at(2026, month, 15))

        assert window.start_date.isoformat() == expected_start
        assert window.end_date.isoformat() == expected_end

    def test_a_shadow_window_has_no_place_on_a_calendar(self):
        # It is the last two days from wherever "now" is, so asking which
        # calendar slot contains a moment is a question about the wrong thing.
        with pytest.raises(ValueError, match="not off a calendar"):
            windows.window_for(ReportType.SHADOW, at(2026, 5, 14))

    def test_the_end_of_a_period_belongs_to_the_next_one(self):
        may = windows.window_for(ReportType.MONTHLY, at(2026, 5, 20))
        june = windows.window_for(ReportType.MONTHLY, at(2026, 6, 20))

        # Half-open, so the boundary is in exactly one of them. Anything else
        # counts one piece of writing in two consecutive months.
        assert may.period_end == june.period_start


class TestTheShadowWindow:
    def test_it_looks_back_the_configured_number_of_hours(self):
        now = at(2026, 5, 14, 9)

        window = windows.shadow_window(now, config=MacroConfig())

        assert window.period_end == now
        assert now - window.period_start == timedelta(hours=48)

    def test_a_nonsensical_width_still_produces_a_usable_window(self):
        window = windows.shadow_window(
            at(2026, 5, 14), config=MacroConfig(shadow_window_hours=0)
        )

        assert window.period_end > window.period_start


class TestThePeriodBefore:
    def test_the_month_before_may_is_april(self):
        may = windows.window_for(ReportType.MONTHLY, at(2026, 5, 20))

        april = windows.previous_window(may)

        assert april.start_date.isoformat() == "2026-04-01"
        assert april.period_end == may.period_start

    def test_the_month_before_january_is_last_december(self):
        january = windows.window_for(ReportType.MONTHLY, at(2026, 1, 20))

        assert windows.previous_window(january).start_date.isoformat() == "2025-12-01"

    def test_the_quarter_before_q1_is_last_q4(self):
        q1 = windows.window_for(ReportType.QUARTERLY, at(2026, 2, 1))

        assert windows.previous_window(q1).start_date.isoformat() == "2025-10-01"

    def test_the_shadow_window_before_one_is_the_two_days_before_it(self):
        window = windows.shadow_window(at(2026, 5, 14), config=MacroConfig())

        earlier = windows.previous_window(window)

        assert earlier.period_end == window.period_start
        assert earlier.period_end - earlier.period_start == timedelta(hours=48)


class TestTheComparisonStretch:
    def test_it_reaches_back_from_where_the_window_starts(self):
        may = windows.window_for(ReportType.MONTHLY, at(2026, 5, 20))

        comparison = windows.comparison_window(may, config=MacroConfig())

        assert comparison.period_end == may.period_start
        assert (comparison.period_end - comparison.period_start).days == 90


class TestWhetherAPeriodIsReady:
    def test_a_month_is_not_reported_the_moment_it_ends(self):
        # Reports cover when things happened, and a report is never rewritten,
        # so running on the first would freeze May before the last entries
        # about May had been written.
        may = windows.window_for(ReportType.MONTHLY, at(2026, 5, 20))

        assert not windows.is_due(may, at(2026, 6, 1, 0), config=MacroConfig())

    def test_a_month_becomes_ready_once_the_grace_has_passed(self):
        may = windows.window_for(ReportType.MONTHLY, at(2026, 5, 20))

        assert windows.is_due(may, at(2026, 6, 4, 0), config=MacroConfig())

    def test_the_grace_is_configurable_per_kind(self):
        week = windows.window_for(ReportType.WEEKLY, at(2026, 5, 14))
        config = MacroConfig(weekly_grace_days=7)

        assert not windows.is_due(week, at(2026, 5, 20), config=config)
        assert windows.is_due(week, at(2026, 5, 25), config=config)

    def test_a_naive_moment_is_read_as_utc_rather_than_refused(self):
        # A timestamp that came back from the database having forgotten to say
        # it was UTC should not turn an ordinary comparison into an error.
        may = windows.window_for(ReportType.MONTHLY, at(2026, 5, 20))

        assert windows.is_due(may, datetime(2026, 6, 10), config=MacroConfig())


class TestWhatIsOverdue:
    def test_a_fresh_system_is_owed_the_periods_that_have_closed(self):
        due = windows.reports_due(at(2026, 7, 4), set(), config=MacroConfig())

        kinds = {window.report_type for window in due}
        assert ReportType.MONTHLY in kinds
        assert ReportType.QUARTERLY in kinds

    def test_a_period_already_covered_is_not_owed(self):
        june = windows.window_for(ReportType.MONTHLY, at(2026, 6, 15))

        due = windows.reports_due(at(2026, 7, 4), {june.key}, config=MacroConfig())

        assert june.key not in {window.key for window in due}

    def test_a_period_still_inside_its_grace_is_not_owed(self):
        due = windows.reports_due(at(2026, 7, 2), set(), config=MacroConfig())

        june = windows.window_for(ReportType.MONTHLY, at(2026, 6, 15))
        assert june.key not in {window.key for window in due}

    def test_nothing_is_owed_when_everything_is_covered(self):
        config = MacroConfig()
        everything = {
            window.key
            for window in windows.reports_due(at(2026, 7, 4), set(), config=config)
        }
        # Ask again claiming those are done. Anything still owed would be a
        # period the first answer had already been capped out of, so the cap
        # is raised for the second question.
        remaining = windows.reports_due(
            at(2026, 7, 4),
            everything,
            config=MacroConfig(catchup_periods=1, max_runs_per_invocation=10),
        )
        assert not remaining

    def test_a_long_silence_produces_the_periods_it_missed(self):
        # Switched off for six months, the honest question is which slots on
        # the calendar are empty — not "what has happened since last time",
        # because there was no last time.
        due = windows.reports_due(
            at(2026, 7, 4),
            set(),
            config=MacroConfig(max_runs_per_invocation=50),
        )

        assert len(due) > 6

    def test_no_more_than_the_cap_is_ever_returned(self):
        due = windows.reports_due(
            at(2026, 7, 4), set(), config=MacroConfig(max_runs_per_invocation=2)
        )

        assert len(due) == 2

    def test_the_slots_go_to_the_most_recent_periods(self):
        due = windows.reports_due(
            at(2026, 7, 4), set(), config=MacroConfig(max_runs_per_invocation=2)
        )

        # Last month and last quarter both closed on the first of July, and
        # both are worth more than the same month two years ago.
        assert {window.period_end for window in due} == {at(2026, 7, 1, 0)}

    def test_a_month_outranks_a_week_that_closed_the_same_day(self):
        due = windows.reports_due(
            at(2026, 6, 5), set(), config=MacroConfig(max_runs_per_invocation=1)
        )

        assert due[0].report_type is ReportType.MONTHLY

    def test_what_is_returned_runs_oldest_first(self):
        due = windows.reports_due(
            at(2026, 7, 4), set(), config=MacroConfig(max_runs_per_invocation=6)
        )

        # Each period is compared against the one before it, so producing them
        # in order means the comparison is already written when it is needed.
        assert due == sorted(due, key=lambda window: window.period_start)

    def test_a_cap_below_one_still_runs_something(self):
        due = windows.reports_due(
            at(2026, 7, 4), set(), config=MacroConfig(max_runs_per_invocation=0)
        )

        assert len(due) == 1


class TestWhetherTheShadowScanShouldRun:
    def test_it_runs_when_it_has_never_run(self):
        assert windows.shadow_due(at(2026, 5, 14), None, config=MacroConfig())

    def test_it_does_not_run_again_straight_away(self):
        assert not windows.shadow_due(
            at(2026, 5, 14, 12), at(2026, 5, 14, 9), config=MacroConfig()
        )

    def test_it_runs_again_after_the_spacing_has_passed(self):
        assert windows.shadow_due(
            at(2026, 5, 15, 12), at(2026, 5, 14, 9), config=MacroConfig()
        )


class TestAWindowItself:
    def test_a_period_must_move_forwards(self):
        from lumen.pipeline.macroextraction.contracts import MacroWindow

        with pytest.raises(ValueError, match="after period_start"):
            MacroWindow(
                report_type=ReportType.MONTHLY,
                period_start=at(2026, 6, 1),
                period_end=at(2026, 5, 1),
            )

    def test_a_period_is_identified_by_its_kind_and_its_start(self):
        week = windows.window_for(ReportType.WEEKLY, at(2026, 6, 1))
        month = windows.window_for(ReportType.MONTHLY, at(2026, 6, 1))

        # Both begin on the same morning and are not the same period.
        assert week.period_start == month.period_start
        assert week.key != month.key
