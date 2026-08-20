"""
The recurring work, one small object each.

Every job here drives a service that already existed and could already be
called by hand. That is the point of the shape: each of these goals shipped an
endpoint and a note saying something would eventually call it, and what was
missing was never the work — it was the caller.

So each job holds its service, knows how often it wants to run, and returns a
count. None of them knows what a scheduler is, which is what lets any of them
be run on its own, at a moment of somebody's choosing, exactly as before.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from lumen.config import SchedulerConfig
from lumen.schemas.enums import MacroRunStatus

logger = logging.getLogger(__name__)


class ReportsDue:
    """
    Writes the periodic reports whose time has come.

    The service decides which periods are due, refuses to write one twice,
    and spends nothing on a period with nothing in it. All this adds is
    somebody asking.
    """

    name = "reports-due"

    def __init__(self, reporter, *, config: SchedulerConfig | None = None) -> None:
        self._reporter = reporter
        self._config = config or SchedulerConfig()

    @property
    def every(self) -> timedelta:
        return timedelta(seconds=self._config.reports_every_seconds)

    def run(self, now: datetime) -> int:
        """Write whatever is due, and say how many were actually written."""
        outcomes = self._reporter.run_due(now)
        written = [
            outcome
            for outcome in outcomes
            if outcome.status is MacroRunStatus.WRITTEN
        ]
        if written:
            logger.info(
                "periodic reports were written",
                extra={"written": [o.report_id for o in written]},
            )
        return len(written)


class ShadowScan:
    """
    Looks for several beliefs moving at once, over the last couple of days.

    The one job here that is about something happening rather than something
    being due. It writes nothing at all unless it finds a burst, which is why
    it can be asked often.
    """

    name = "shadow-scan"

    def __init__(self, reporter, *, config: SchedulerConfig | None = None) -> None:
        self._reporter = reporter
        self._config = config or SchedulerConfig()

    @property
    def every(self) -> timedelta:
        return timedelta(seconds=self._config.shadow_every_seconds)

    def run(self, now: datetime) -> int:
        """Scan, and say whether it found anything worth writing down."""
        outcome = self._reporter.run_shadow(now)
        if outcome.status is not MacroRunStatus.WRITTEN:
            return 0
        logger.info(
            "something appears to be shifting", extra={"report_id": outcome.report_id}
        )
        return 1


class ReviewSweep:
    """
    Keeps the review queue honest without anybody opening it.

    Settles what has been deferred long enough to count as settled, and lets
    in anything parked behind the cap. Both were built to run whenever the
    queue is touched; this is for the weeks nobody touches it.
    """

    name = "review-sweep"

    def __init__(
        self, reviewer, *, user_id: str, config: SchedulerConfig | None = None
    ) -> None:
        self._reviewer = reviewer
        self._user_id = user_id
        self._config = config or SchedulerConfig()

    @property
    def every(self) -> timedelta:
        return timedelta(seconds=self._config.sweep_every_seconds)

    def run(self, now: datetime) -> int:
        """Run the housekeeping, and say how many items it moved."""
        report = self._reviewer.sweep(self._user_id)
        moved = (
            len(report.auto_resolved) + len(report.admitted) + len(report.closed)
        )
        if moved:
            logger.info(
                "the review queue was tidied",
                extra={
                    "settled": len(report.auto_resolved),
                    "closed": len(report.closed),
                    "admitted": len(report.admitted),
                },
            )
        return moved


__all__ = ["ReportsDue", "ShadowScan", "ReviewSweep"]
