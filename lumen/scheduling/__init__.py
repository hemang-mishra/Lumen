"""
The clock the product runs on.

Every part of Lumen works and almost none of it used to run on its own. A
conversation that had gone quiet stayed a conversation; reports knew when they
were due and nothing asked; the review queue had housekeeping and nothing ran
it. Each of those shipped an endpoint and a note saying something would call it.

This is the caller: one background thread that wakes on an interval, asks each
job whether it is due, and runs the ones that are. One thread rather than four
timers, because four would be four things to start, four to stop, and four ways
for two jobs to reach the same store at the same moment.

Nothing here knows how any of the work is done. Every job is a small object
holding a service that already existed and could already be driven by hand.
"""

from lumen.scheduling.contracts import Job, JobOutcome, SchedulerReport
from lumen.scheduling.scheduler import Scheduler

__all__ = ["Job", "JobOutcome", "SchedulerReport", "Scheduler"]
