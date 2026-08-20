"""
One thread, one clock, and every recurring job in the product.

The whole of the behaviour is in `tick`, which takes the moment as an argument
and returns what happened. That is not a testing convenience so much as the
design: a background loop whose rules can only be exercised by waiting is a
background loop nobody checks. The thread is a dozen lines wrapped around it.

Three rules do the work, and all three are about restraint.

**Never two passes at once.** A pass that arrives while the last one is still
going does nothing and says so. These jobs are minutes long and none of them is
urgent; a queue that grows while the machine is busy is how a laptop waking
from sleep starts nine reports at the same time.

**A job that throws costs that job one turn.** Each runs inside its own guard.
The alternative is one bad job killing the thread, which looks from outside
like a system that quietly stopped doing everything because one thing broke
once.

**Built stopped.** Constructing one starts nothing, so no test ever races a
thread it did not ask for, and starting is something the application does
deliberately.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from lumen.config import SchedulerConfig
from lumen.scheduling.contracts import Job, JobOutcome, SchedulerReport

logger = logging.getLogger(__name__)


class Scheduler:
    """
    Runs a set of jobs on their own intervals, on one background thread.

    The jobs are handed in, so this object has no idea what any of them do and
    cannot grow an opinion about it. Adding a fifth is a line where the set is
    built, never a change here.
    """

    def __init__(
        self,
        jobs: Sequence[Job],
        *,
        config: SchedulerConfig | None = None,
        on_report: Callable[[SchedulerReport], None] | None = None,
    ) -> None:
        """
        Args:
            jobs: What to run, and how often each wants to run.
            config: How often to wake, and whether to run at all.
            on_report: Told what each pass did. This is how anything watching
                — a socket, a page — hears about a run without the scheduler
                knowing such things exist.
        """
        self._jobs = list(jobs)
        self._config = config or SchedulerConfig()
        self._on_report = on_report

        self._last_run: dict[str, datetime] = {}
        self._running = threading.Lock()
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # The thread
    # ------------------------------------------------------------------

    def start(self) -> None:
        """
        Begin waking on the interval. Safe to call twice, and does nothing
        when this deployment has the clock switched off.
        """
        if not self._config.enabled:
            logger.info("the scheduler is switched off for this deployment")
            return
        if self._thread is not None and self._thread.is_alive():
            return

        self._stopped.clear()
        self._thread = threading.Thread(
            target=self._loop, name="lumen-scheduler", daemon=True
        )
        self._thread.start()
        logger.info(
            "scheduler started",
            extra={
                "jobs": [job.name for job in self._jobs],
                "poll_seconds": self._config.poll_seconds,
            },
        )

    def stop(self, timeout: float = 30.0) -> None:
        """
        Stop waking, and let whatever is running finish.

        The one in flight is allowed to complete because it is partway through
        writing somebody's history, and the transaction protecting it is worth
        more than a fast shutdown.
        """
        self._stopped.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("the scheduler did not stop in time")
        self._thread = None
        logger.info("scheduler stopped")

    @property
    def running(self) -> bool:
        """Whether the thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        """
        Wake, do a pass, wait again, until told to stop.

        Waiting on the stop signal rather than sleeping is what makes stopping
        immediate instead of up to one interval long.
        """
        while not self._stopped.wait(self._config.poll_seconds):
            try:
                self.tick(_now())
            except Exception:  # noqa: BLE001 — the thread must outlive anything
                logger.error("a scheduler pass failed outright", exc_info=True)

    # ------------------------------------------------------------------
    # One pass, which is all of the behaviour
    # ------------------------------------------------------------------

    def tick(self, now: datetime | None = None) -> SchedulerReport:
        """
        Ask every job whether it is due and run the ones that are.

        Returns what happened, whether or not anything did. A pass that finds
        nothing to do is a fact worth being able to see.
        """
        moment = now or _now()

        if not self._running.acquire(blocking=False):
            logger.info(
                "a scheduled pass was still running, so this one was skipped",
                extra={"at": moment.isoformat()},
            )
            report = SchedulerReport(at=moment, skipped=True)
            self._announce(report)
            return report

        try:
            outcomes = tuple(self._attempt(job, moment) for job in self._jobs)
        finally:
            self._running.release()

        report = SchedulerReport(at=moment, outcomes=outcomes)
        _log(report)
        self._announce(report)
        return report

    def _attempt(self, job: Job, now: datetime) -> JobOutcome:
        """
        Run one job if it is due, and never let it take anything else down.

        A job with no record of a previous run is due immediately. That is the
        useful direction on a restart: a machine that has been off for a day
        should catch up rather than wait out a full interval first.
        """
        if not self._is_due(job, now):
            return JobOutcome(name=job.name, ran=False)

        started = time.perf_counter()
        self._last_run[job.name] = now
        try:
            did = int(job.run(now) or 0)
        except Exception as exc:  # noqa: BLE001 — one bad job is not all of them
            logger.error(
                "a scheduled job could not run",
                exc_info=True,
                extra={"job": job.name},
            )
            return JobOutcome(
                name=job.name,
                duration_ms=_since(started),
                failure=type(exc).__name__,
            )
        return JobOutcome(name=job.name, did=max(did, 0), duration_ms=_since(started))

    def _is_due(self, job: Job, now: datetime) -> bool:
        """Whether enough time has passed since this job last ran."""
        last = self._last_run.get(job.name)
        if last is None:
            return True
        return (now - last) >= job.every

    def _announce(self, report: SchedulerReport) -> None:
        """
        Tell whoever is listening, without letting them break the clock.

        Anything watching a scheduler is a convenience. A page with a broken
        connection must not be able to stop the pipeline from running.
        """
        if self._on_report is None:
            return
        try:
            self._on_report(report)
        except Exception:  # noqa: BLE001 — a listener is never worth a job
            logger.warning("something listening to the scheduler failed", exc_info=True)


def _log(report: SchedulerReport) -> None:
    """One line per pass, and only when there is something to say."""
    if not report.did_anything and not report.failures:
        logger.debug("a scheduled pass found nothing to do")
        return
    logger.info(
        "a scheduled pass ran",
        extra={
            "did": {o.name: o.did for o in report.outcomes if o.worked},
            "failed": list(report.failures),
        },
    )


def _now() -> datetime:
    """The moment a pass is being made at."""
    return datetime.now(UTC)


def _since(started: float) -> int:
    """How long something took, in whole milliseconds."""
    return int((time.perf_counter() - started) * 1000)


__all__ = ["Scheduler"]
