"""
The shapes a clock and its jobs speak in.

A job is anything with a name, an interval, and something to do. That is
deliberately almost nothing: the scheduler's whole responsibility is deciding
*when*, and every job it drives already exists as a method on a service that
somebody can call by hand. Keeping the contract this thin is what stops the
clock from growing opinions about the work.

The reports exist because a background thread that says nothing is a
background thread nobody can debug. A job that was not due, a job that ran and
found nothing to do, and a job that threw are three different facts, and from
outside they all look like "nothing happened".
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


@runtime_checkable
class Job(Protocol):
    """
    One recurring piece of work.

    Attributes:
        name: What to call it in a log. Short and stable.
        every: How long to leave between runs.
    """

    name: str
    every: timedelta

    def run(self, now: datetime) -> int:
        """
        Do the work, and say how many things it touched.

        The count is what makes a quiet system distinguishable from a stopped
        one: zero means it looked and there was nothing, which is a different
        fact from never having looked.
        """
        ...


class JobOutcome(BaseModel):
    """
    What one job did on one tick.

    Attributes:
        name: Which job.
        ran: False when it was not due yet. Not a failure — most ticks are
            mostly this.
        did: How many things it acted on.
        duration_ms: How long it took.
        failure: A short word for what went wrong, or nothing. A job that
            throws costs itself one turn and nothing else.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    ran: bool = True
    did: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    failure: str | None = None

    @property
    def worked(self) -> bool:
        """Whether this job actually did something."""
        return self.ran and self.failure is None and self.did > 0


class SchedulerReport(BaseModel):
    """
    One pass over every job.

    Attributes:
        at: The moment the pass was made for.
        outcomes: What each job did, in the order they were tried.
        skipped: True when this pass did nothing because the previous one was
            still running. Recorded rather than hidden — a system that is
            always skipping is one whose jobs take longer than its interval,
            and that is invisible otherwise.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    at: datetime
    outcomes: tuple[JobOutcome, ...] = ()
    skipped: bool = False

    @property
    def did_anything(self) -> bool:
        """Whether this pass changed anything at all."""
        return any(outcome.worked for outcome in self.outcomes)

    @property
    def failures(self) -> tuple[str, ...]:
        """The jobs that could not run, by name."""
        return tuple(
            outcome.name for outcome in self.outcomes if outcome.failure is not None
        )


__all__ = ["Job", "JobOutcome", "SchedulerReport"]
