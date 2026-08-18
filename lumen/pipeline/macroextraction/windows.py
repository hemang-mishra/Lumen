"""
Working out which stretches of time a report should cover, and which are late.

Nothing here reads a store or asks a model. It is calendar arithmetic and one
comparison, kept apart from everything else because it is the part a schedule
will call every few minutes forever, and because a mistake in it is invisible
in a way a mistake in the rest of the package is not — a report that quietly
covers eight days instead of seven still looks like a report.

Two rules run through all of it. Periods are half-open, so nothing is counted
twice and nothing falls between two of them. And a period is never reported on
the instant it ends: reports cover when things happened rather than when they
were written, so a few days are left for late entries to land before the
period is frozen.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from lumen.config import MacroConfig
from lumen.pipeline.macroextraction.contracts import MacroWindow
from lumen.schemas.enums import ReportType

logger = logging.getLogger(__name__)

# The four periodic kinds, in the order a catch-up should run them. Shortest
# first, so a system coming back after a long silence produces the weeks
# before the month that contains them.
PERIODIC_TYPES: tuple[ReportType, ...] = (
    ReportType.WEEKLY,
    ReportType.MONTHLY,
    ReportType.QUARTERLY,
)

# Which month each quarter begins in.
_QUARTER_START_MONTHS: tuple[int, ...] = (1, 4, 7, 10)


def window_for(report_type: ReportType, anchor: datetime) -> MacroWindow:
    """
    The window of the given kind that contains a moment.

    The shadow window is the odd one out: it is the two days up to the moment
    asked about rather than a slot on a calendar, because it exists to notice
    something happening now rather than to summarise a period that ended.
    """
    moment = _as_utc(anchor)

    if report_type is ReportType.SHADOW:
        raise ValueError("a shadow window is measured back from now, not off a calendar")

    day = moment.date()
    if report_type is ReportType.WEEKLY:
        start = day - timedelta(days=day.weekday())
        end = start + timedelta(days=7)
    elif report_type is ReportType.MONTHLY:
        start = day.replace(day=1)
        end = _add_months(start, 1)
    elif report_type is ReportType.QUARTERLY:
        start = date(day.year, _quarter_start_month(day.month), 1)
        end = _add_months(start, 3)
    else:  # pragma: no cover - the enum has no fifth member
        raise ValueError(f"no window rule for {report_type}")

    return MacroWindow(
        report_type=report_type,
        period_start=_midnight(start),
        period_end=_midnight(end),
    )


def shadow_window(now: datetime, *, config: MacroConfig) -> MacroWindow:
    """The stretch the near-real-time scan looks back over."""
    moment = _as_utc(now)
    return MacroWindow(
        report_type=ReportType.SHADOW,
        period_start=moment - timedelta(hours=max(config.shadow_window_hours, 1)),
        period_end=moment,
    )


def previous_window(window: MacroWindow) -> MacroWindow:
    """
    The window of the same kind immediately before this one.

    This is what makes "stopped happening" sayable at all. A pattern absent
    from one month is only news if it was present in the month before, and
    without a comparison the report can only ever list what is there.
    """
    if window.report_type is ReportType.SHADOW:
        length = window.period_end - window.period_start
        return MacroWindow(
            report_type=ReportType.SHADOW,
            period_start=window.period_start - length,
            period_end=window.period_start,
        )
    return window_for(window.report_type, window.period_start - timedelta(days=1))


def comparison_window(window: MacroWindow, *, config: MacroConfig) -> MacroWindow:
    """
    The longer stretch a shift is measured against.

    An identity-level change does not happen inside a month. It is visible
    only against a run of them, so the comparison reaches back a set number
    of days from where this window began rather than to the period before.
    """
    span = timedelta(days=max(config.archetype_window_days, 1))
    return MacroWindow(
        report_type=window.report_type,
        period_start=window.period_start - span,
        period_end=window.period_start,
    )


def grace_days(report_type: ReportType, config: MacroConfig) -> int:
    """How long to wait after a period ends before reporting on it."""
    return {
        ReportType.WEEKLY: config.weekly_grace_days,
        ReportType.MONTHLY: config.monthly_grace_days,
        ReportType.QUARTERLY: config.quarterly_grace_days,
        ReportType.SHADOW: 0,
    }[report_type]


def is_due(window: MacroWindow, now: datetime, *, config: MacroConfig) -> bool:
    """Whether enough time has passed since this window ended to report on it."""
    ready = window.period_end + timedelta(days=grace_days(window.report_type, config))
    return _as_utc(now) >= ready


def reports_due(
    now: datetime,
    existing: set[tuple[str, str]],
    *,
    config: MacroConfig,
) -> list[MacroWindow]:
    """
    Every period that should have a report by now and does not, oldest first.

    Deliberately answers about periods rather than about "since last time".
    A system that was switched off for six months has no last time, and the
    honest question is which slots on the calendar are empty — which is also
    why turning it back on produces the missed weeks rather than one report
    covering half a year.

    The number returned is capped. Each report is a model call over a lot of
    history, and a schedule waking up to forty of them at once is a surprise
    bill rather than a catch-up.
    """
    due: list[MacroWindow] = []

    for report_type in PERIODIC_TYPES:
        for window in _recent_windows(report_type, now, config=config):
            if window.key in existing:
                continue
            if not is_due(window, now, config=config):
                continue
            due.append(window)

    # Chosen newest first and then run oldest first. Which ones to run is a
    # question of usefulness — last month matters more than the same month
    # two years ago — and the order to run them in is a question of reading:
    # a period is compared against the one before it, so producing them in
    # order means each has the comparison it wants already written.
    #
    # Two periods that ended on the same day are ranked longer first. A month
    # and its final week both close on the first, and of the two the month is
    # the one somebody wants to read.
    cap = max(config.max_runs_per_invocation, 1)
    due.sort(key=_priority, reverse=True)
    if len(due) > cap:
        logger.info(
            "more periods are overdue than one run will cover",
            extra={"overdue": len(due), "running": cap},
        )
    chosen = due[:cap]
    chosen.sort(key=lambda window: (window.period_start, window.report_type.value))
    return chosen


def shadow_due(
    now: datetime, last_shadow_at: datetime | None, *, config: MacroConfig
) -> bool:
    """
    Whether the two-day scan should run again.

    Spaced out rather than run on every tick. The scan looks back over two
    days, so running it every few minutes would keep finding the same burst
    and have nothing new to say about it.
    """
    if last_shadow_at is None:
        return True
    gap = _as_utc(now) - _as_utc(last_shadow_at)
    return gap >= timedelta(hours=max(config.shadow_repeat_hours, 1))


def _priority(window: MacroWindow) -> tuple[datetime, timedelta, datetime]:
    """How much a period deserves one of the limited slots in a catch-up."""
    return (
        window.period_end,
        window.period_end - window.period_start,
        window.period_start,
    )


def _recent_windows(
    report_type: ReportType, now: datetime, *, config: MacroConfig
) -> list[MacroWindow]:
    """
    The last several windows of one kind, oldest first, ending with the
    period that has just closed.
    """
    windows: list[MacroWindow] = []
    cursor = window_for(report_type, now)

    for _ in range(max(config.catchup_periods, 1)):
        cursor = previous_window(cursor)
        windows.append(cursor)

    windows.reverse()
    return windows


def _quarter_start_month(month: int) -> int:
    """The first month of whichever quarter a month falls in."""
    return _QUARTER_START_MONTHS[(month - 1) // 3]


def _add_months(start: date, count: int) -> date:
    """
    The first of the month, a number of months on.

    Only ever applied to the first of a month, which is what keeps it simple
    — there is no last-day-of-February question to answer.
    """
    zero_based = start.month - 1 + count
    return date(start.year + zero_based // 12, zero_based % 12 + 1, 1)


def _midnight(day: date) -> datetime:
    """The start of a day, in UTC."""
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def _as_utc(moment: datetime) -> datetime:
    """
    A moment with a timezone on it.

    A naive timestamp is read as UTC rather than refused. Everything stored
    is written in UTC, and refusing one that merely forgot to say so would
    turn a round-trip through the database into an error.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


__all__ = [
    "PERIODIC_TYPES",
    "window_for",
    "shadow_window",
    "previous_window",
    "comparison_window",
    "grace_days",
    "is_due",
    "reports_due",
    "shadow_due",
]
