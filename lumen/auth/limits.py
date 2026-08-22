"""
Slowing down whoever is trying to sign in too fast.

Sign-in is the only door in the system that opens to somebody who has not
proved anything yet, so it is the only one with a limit. Everything else is
behind a token, and limiting authenticated traffic is a job for something in
front of the service rather than a counter inside it.

Counted two ways, because they catch different things. Per caller catches one
machine working through a list. Per address catches a list of machines working
on one account — which is the same attack from a botnet and looks like nothing
at all if you only count callers.

Held in memory, which is honest about what it is: this is one process, and a
limiter that survived a restart would need a store and a story about clock
skew for a door that is already the least interesting way into this system.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timedelta

from lumen.config import AuthConfig

logger = logging.getLogger(__name__)


class SignInLimiter:
    """
    Counts recent attempts and says when there have been too many.

    Deliberately not a decorator or a middleware. It is asked a question and
    answers it, so the route decides what to do and a test can drive a
    hundred attempts without a clock.
    """

    def __init__(self, config: AuthConfig | None = None) -> None:
        self._config = config or AuthConfig()
        self._attempts: dict[str, deque[datetime]] = {}
        self._lock = threading.Lock()

    def allows(self, *keys: str | None, now: datetime) -> bool:
        """
        Whether this attempt may proceed, counting it if so.

        Every key given is counted separately and any one of them being over
        its limit refuses the attempt. Asking and counting are one step, so
        two requests arriving together cannot both be told yes on the same
        remaining allowance.
        """
        window = timedelta(seconds=max(self._config.signin_window_seconds, 1))
        ceiling = max(self._config.signin_attempts, 1)
        real = [key for key in keys if key]
        if not real:
            return True

        with self._lock:
            for key in real:
                recent = self._recent(key, now, window)
                if len(recent) >= ceiling:
                    logger.warning(
                        "sign-in is being attempted faster than a person tries it",
                        extra={"attempts": len(recent)},
                    )
                    return False
            for key in real:
                self._attempts.setdefault(key, deque()).append(now)
        return True

    def forget(self, *keys: str | None) -> None:
        """
        Clear the count for these, after a sign-in that worked.

        Somebody who mistyped their way through four attempts and then
        succeeded should not be one attempt away from being locked out for
        the rest of the window.
        """
        with self._lock:
            for key in keys:
                if key:
                    self._attempts.pop(key, None)

    def _recent(self, key: str, now: datetime, window: timedelta) -> deque[datetime]:
        """
        This key's attempts inside the window, dropping the ones that aged out.

        Pruning on read rather than on a timer is what keeps this free of a
        background thread; a key nobody asks about costs a few timestamps
        until somebody does.
        """
        held = self._attempts.setdefault(key, deque())
        cutoff = now - window
        while held and held[0] < cutoff:
            held.popleft()
        return held


__all__ = ["SignInLimiter"]
