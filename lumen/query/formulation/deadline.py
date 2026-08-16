"""
Putting a hard limit on how long one turn may wait.

Everywhere else in Lumen a slow model call is merely slow; the work is
happening in the background and nobody is sitting in front of it. Here
somebody is mid-sentence. A call that takes four seconds has already failed
whatever it eventually returns, because the conversation moved on without it.

There is no timeout on the model providers themselves — adding one would
change every provider for the sake of this single caller — so the limit is
imposed from outside: run the call on another thread and stop waiting when
the time is up.

Three things about doing it this way are worth knowing, and none of them are
avoidable in Python:

The abandoned call is not cancelled. A running thread cannot be stopped from
outside, so it finishes on its own and its answer is thrown away. That is
acceptable because the call has no side effects, but it means a provider that
is slow for everyone will pile up threads, so the pool is bounded and a late
arrival is logged rather than passing unnoticed.

The trace identifier has to be carried across by hand. Context set on one
thread is not visible on another, so the call is run inside a copy of the
caller's context and its log lines stay tied to the turn that caused them.

Submitting can itself fail when every worker is busy. That is reported as
the deadline being missed, because from the turn's point of view it is the
same thing: no answer arrived in time.
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class DeadlineExceeded(Exception):
    """The work did not finish inside the time the caller had."""


class DeadlineRunner(Generic[T]):
    """
    Runs one piece of work with a time limit.

    Holds its own small thread pool for the life of the process. Building a
    pool per call would add the cost of starting a thread to every turn, and
    unbounded threads would let a slow provider quietly consume the machine.

    Closing it is optional and only matters when a process wants a clean
    shutdown; the pool releases its threads when the process ends either way.
    """

    def __init__(self, *, max_workers: int = 4, name: str = "formulate") -> None:
        self._pool = ThreadPoolExecutor(
            max_workers=max(int(max_workers), 1), thread_name_prefix=name
        )
        self._name = name

    def run(self, work: Callable[[], T], *, timeout_seconds: float) -> T:
        """
        Do the work, or raise DeadlineExceeded when the time runs out.

        Anything the work itself raises comes straight back to the caller, so
        a provider failing and a provider being slow stay two different
        outcomes with two different answers.
        """
        context = contextvars.copy_context()
        try:
            future: Future[T] = self._pool.submit(context.run, work)
        except RuntimeError as exc:
            # Raised when the pool is already shut down, and reported as a
            # miss because that is what it is from the turn's side.
            raise DeadlineExceeded("no worker was available") from exc

        try:
            return future.result(timeout=max(timeout_seconds, 0.0))
        except FutureTimeout as exc:
            future.add_done_callback(self._note_late_arrival)
            raise DeadlineExceeded(
                f"work did not finish within {timeout_seconds:.2f}s"
            ) from exc

    def close(self) -> None:
        """Stop accepting work. Anything already running is left to finish."""
        self._pool.shutdown(wait=False)

    def _note_late_arrival(self, future: Future[T]) -> None:
        """
        Record that an abandoned call eventually came back.

        Worth a line because it is the only evidence that the model is
        systematically too slow rather than occasionally unlucky. Without it,
        every missed deadline looks like a one-off.
        """
        failed = future.exception() is not None
        logger.debug(
            "a call that had already been given up on finished",
            extra={"runner": self._name, "failed": failed},
        )


__all__ = ["DeadlineRunner", "DeadlineExceeded"]
