"""
Saying what is happening, while it happens.

Most of what Lumen does now happens without anybody asking for it — a
conversation becomes history, a report is written, the review queue tidies
itself. All of that used to be invisible until somebody reloaded a page.

This is the small piece that makes it visible. Anything that does something
worth knowing about publishes a line; anything watching receives it from the
moment it connects.

Three decisions shape it.

**Broadcast, not delivered.** Nothing is stored and nothing is replayed for
somebody who was not looking. The queue count, the runs and the reports are
all readable from their own endpoints; this is for watching, not for
record-keeping. The short backlog exists only so a page that has just opened
is not blank.

**A slow listener drops its own messages, never anybody else's.** Each gets a
small queue of its own and loses the oldest when it fills. A browser left open
on a sleeping laptop must not be able to hold up the pipeline.

**Nothing that does the work knows this exists.** The scheduler and the worker
are handed somewhere to publish; the pipeline, the review queue and the
reports are not told anything at all.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from collections.abc import AsyncIterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# How many messages one listener may fall behind before it starts losing the
# oldest. Small on purpose: a listener this far behind is not watching
# anything live, and the point of the thing is live.
LISTENER_BACKLOG = 100

# What the clock announces when one of its passes actually did something.
# Named here, next to the bus that carries them, for the same reason the
# worker names its own: a browser matches on the exact string.
JOB_RAN = "job_ran"
JOB_FAILED = "job_failed"

# Everything the scheduled work can announce.
SCHEDULER_EVENTS: tuple[str, ...] = (JOB_RAN, JOB_FAILED)


class Event(BaseModel):
    """
    One thing that happened, as anybody watching receives it.

    Attributes:
        kind: What happened, in one word. Clients switch on this.
        at: When.
        payload: Whatever that kind carries. Deliberately loose — this is a
            notification, and anything a client needs properly it fetches
            from the endpoint that owns it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str = Field(min_length=1)
    at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class EventBus:
    """
    Somewhere to publish, and somewhere to listen.

    Publishing happens on ordinary threads — the scheduler's, the worker's —
    and listening happens on the event loop, so handing a message across is
    done in the one way that is safe from both sides.
    """

    def __init__(self, *, history: int = 50) -> None:
        self._recent: deque[Event] = deque(maxlen=max(int(history), 1))
        self._listeners: list[asyncio.Queue] = []
        # Learned when something subscribes, which is the only moment it can
        # be known. Nothing before that has anywhere to be delivered.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def publish(self, kind: str, **payload: Any) -> Event:
        """
        Say that something happened.

        Safe to call from any thread, and safe to call when nobody is
        listening — which is most of the time.
        """
        event = Event(kind=kind, at=datetime.now(UTC), payload=payload)

        with self._lock:
            self._recent.append(event)
            listeners = list(self._listeners)

        for queue in listeners:
            self._offer(queue, event)
        return event

    def recent(self, limit: int = 50) -> list[Event]:
        """The last few things that happened, oldest first."""
        with self._lock:
            held = list(self._recent)
        return held[-max(int(limit), 1) :]

    @contextmanager
    def subscribe(self):
        """
        A queue of everything published from now on, removed on the way out.

        A context manager because a listener that is never removed is a slow
        leak of memory and of work: every publish would keep filling a queue
        nobody reads.

        Subscribing is also where the bus learns which loop the listeners are
        on. That is the only moment it can be known for certain — publishing
        happens on whatever thread did the work, and a loop recorded at
        startup would be wrong for anything that came later.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=LISTENER_BACKLOG)
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._listeners.append(queue)
        try:
            yield queue
        finally:
            with self._lock:
                if queue in self._listeners:
                    self._listeners.remove(queue)

    async def listen(self) -> AsyncIterator[Event]:
        """Everything published from now on, one at a time."""
        with self.subscribe() as queue:
            while True:
                yield await queue.get()

    @property
    def listeners(self) -> int:
        """How many things are watching."""
        with self._lock:
            return len(self._listeners)

    def _offer(self, queue: asyncio.Queue, event: Event) -> None:
        """
        Put one message on one listener's queue, from whatever thread we are on.

        A full queue loses its oldest message rather than blocking. The
        listener is behind; the thing that published is usually the pipeline,
        and it has better things to do than wait for a browser.
        """
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(_put, queue, event)
        except RuntimeError:
            # The loop has closed under us, which happens while shutting
            # down. There is nobody left to tell.
            logger.debug("nothing is listening any more")


def _put(queue: asyncio.Queue, event: Event) -> None:
    """Add one message, discarding the oldest if there is no room."""
    if queue.full():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        logger.debug("a listener is too far behind to keep up")


__all__ = ["Event", "EventBus", "LISTENER_BACKLOG"]
