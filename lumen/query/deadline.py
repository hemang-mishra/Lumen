"""
Putting a hard limit on how long one turn may wait.

Everywhere else in Lumen a slow model call is merely slow; the work is
happening in the background and nobody is sitting in front of it. Here
somebody is mid-sentence. A call that takes four seconds has already failed
whatever it eventually returns, because the conversation moved on without it.

Two things need this. Reading a turn gets a single call under a sub-second
deadline; fetching what the turn points at runs several searches side by
side under a shared one. Both want the same guarantee — whatever has not
finished by the time named is abandoned, and the turn goes on without it.

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
import time
from collections.abc import Callable, Mapping
from concurrent.futures import (
    ALL_COMPLETED,
    Future,
    ThreadPoolExecutor,
    TimeoutError as FutureTimeout,
    wait,
)
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class DeadlineExceeded(Exception):
    """The work did not finish inside the time the caller had."""


@dataclass(frozen=True)
class Attempt:
    """
    How one piece of parallel work ended.

    Three outcomes rather than two, because they call for different
    responses. A piece that answered has a value; a piece that broke has an
    error worth logging; a piece that simply ran out of time is still
    running and will finish into nothing. Collapsing the last two would hide
    a provider that is systematically too slow behind whatever error
    happened to be logged last.

    Attributes:
        name: What this piece of work was, for logs and reports.
        value: What it produced, when it finished in time.
        error: What it raised, if it raised.
        timed_out: True when the deadline passed with it still running.
        duration_ms: How long it took, or how long it was waited for.
    """

    name: str
    value: Any = None
    error: BaseException | None = None
    timed_out: bool = False
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        """Whether this piece produced an answer."""
        return self.error is None and not self.timed_out

    @property
    def failure(self) -> str | None:
        """A short word for what went wrong, or nothing if nothing did."""
        if self.timed_out:
            return "timed_out"
        if self.error is not None:
            return type(self.error).__name__
        return None


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

    def run_all(
        self,
        work: Mapping[str, Callable[[], Any]],
        *,
        timeout_seconds: float,
    ) -> list[Attempt]:
        """
        Do several pieces of work side by side under one shared deadline.

        Everything is submitted at once and the clock runs for the group, not
        for each piece: three searches under a three-second budget means three
        seconds in total, which is what the person waiting actually
        experiences.

        Nothing raises. A piece that broke and a piece that ran out of time
        both come back as their own outcome, because losing one search should
        cost that search and not the others — the whole reason for running
        them separately is that they fail in different places.

        Results come back in the order the work was given, so a caller can
        name its pieces and read them back by position without a lookup.
        """
        started = _now()
        futures: dict[str, Future[Any]] = {}
        refused: dict[str, BaseException] = {}

        for name, piece in work.items():
            # A copy each, not one copy shared. A context object can only be
            # entered by one thread at a time, so handing the same one to
            # several pieces makes every piece after the first fail — and
            # only when they genuinely overlap, which is always, here.
            context = contextvars.copy_context()
            try:
                futures[name] = self._pool.submit(context.run, piece)
            except RuntimeError as exc:
                # The pool is shut down. Reported as that piece failing
                # rather than raising, so the pieces that did start still
                # produce their answers.
                refused[name] = exc

        if futures:
            wait(
                list(futures.values()),
                timeout=max(timeout_seconds, 0.0),
                return_when=ALL_COMPLETED,
            )

        elapsed = _elapsed_ms(started)
        return [
            self._outcome(name, futures.get(name), refused.get(name), elapsed)
            for name in work
        ]

    def close(self) -> None:
        """Stop accepting work. Anything already running is left to finish."""
        self._pool.shutdown(wait=False)

    def _outcome(
        self,
        name: str,
        future: Future[Any] | None,
        refusal: BaseException | None,
        elapsed_ms: int,
    ) -> Attempt:
        """Read one piece of parallel work back into a plain description."""
        if refusal is not None:
            return Attempt(name=name, error=refusal, duration_ms=elapsed_ms)
        if future is None or not future.done():
            if future is not None:
                future.add_done_callback(self._note_late_arrival)
            return Attempt(name=name, timed_out=True, duration_ms=elapsed_ms)

        error = future.exception()
        if error is not None:
            return Attempt(name=name, error=error, duration_ms=elapsed_ms)
        return Attempt(name=name, value=future.result(), duration_ms=elapsed_ms)

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


def _now() -> float:
    """A reading of the clock that only ever moves forwards."""
    return time.perf_counter()


def _elapsed_ms(started: float) -> int:
    """How long since that reading, in whole milliseconds."""
    return max(int((_now() - started) * 1000), 0)


__all__ = ["DeadlineRunner", "DeadlineExceeded", "Attempt"]
