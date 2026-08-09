"""
Trying a model call again when it fails for a reason that might pass.

Only failures that were never about the answer are retried: a timeout, a
refused connection, a busy server, a hit rate limit. If the model replied and
the reply was unusable, that is not retried here — asking the same question the
same way gets the same answer, so improving the question is the caller's job.

Waiting is done with backoff and jitter. Backoff because a server that is
struggling should be given room; jitter because several workers that all wait
exactly two seconds will collide again in exactly two seconds.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import NamedTuple, TypeVar

from lumen.providers.errors import (
    ProviderError,
    ProviderRateLimitError,
    RetryableProviderError,
)
from lumen.schemas.enums import ModelRole

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Small helper so tests can watch what would have been waited for without
# actually waiting. Production passes time.sleep.
Sleeper = Callable[[float], None]


class RetryOutcome(NamedTuple):
    """
    What happened across all the attempts.

    latency_ms covers only the attempt that succeeded, which is how fast the
    model actually is. elapsed_ms covers everything, waiting included, which is
    how long the caller was held up.
    """

    value: object
    attempts: int
    latency_ms: int
    elapsed_ms: int


def call_with_retry(
    operation: Callable[[], T],
    *,
    provider: str,
    model: str,
    role: ModelRole,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    rate_limit_max_delay: float = 65.0,
    sleeper: Sleeper = time.sleep,
    random_source: random.Random | None = None,
) -> RetryOutcome:
    """
    Run something, retrying it while it keeps failing in recoverable ways.

    Raises the last failure once the attempts run out, with the attempt count
    filled in so the log line shows how hard it tried.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    rng = random_source or random
    started_all = time.perf_counter()
    last_error: RetryableProviderError | None = None

    for attempt in range(1, max_attempts + 1):
        started_attempt = time.perf_counter()
        try:
            value = operation()
        except RetryableProviderError as exc:
            last_error = exc
            exc.attempts = attempt

            if attempt == max_attempts:
                break

            delay = _delay_for(
                exc,
                attempt=attempt,
                base_delay=base_delay,
                max_delay=max_delay,
                rate_limit_max_delay=rate_limit_max_delay,
                rng=rng,
            )
            logger.warning(
                "model call failed, retrying",
                extra={
                    "provider": provider,
                    "model": model,
                    "model_role": role.value,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "error_type": type(exc).__name__,
                    "delay_ms": int(delay * 1000),
                },
            )
            sleeper(delay)
        else:
            return RetryOutcome(
                value=value,
                attempts=attempt,
                latency_ms=_ms_since(started_attempt),
                elapsed_ms=_ms_since(started_all),
            )

    # Only reachable once every attempt has failed.
    assert last_error is not None
    last_error.attempts = max_attempts
    raise last_error


def _delay_for(
    error: RetryableProviderError,
    *,
    attempt: int,
    base_delay: float,
    max_delay: float,
    rate_limit_max_delay: float,
    rng: random.Random | object,
) -> float:
    """
    How long to wait before the next attempt.

    A server that told us when to come back is believed, because it knows when
    its quota resets and we are only guessing. It is not believed without limit
    though: the value arrives over the network, and one unreasonable number
    would otherwise park a pipeline run for as long as it said. So it is capped
    at the longest wait we are ever willing to take.

    Failing that, the wait doubles each time up to a ceiling, and then a random
    amount up to that figure is chosen so simultaneous callers spread out
    instead of retrying in lockstep.

    Rate limits get a far higher ceiling than other failures. Cloud quotas are
    usually counted per minute, so a handful of quick retries all land inside
    the same exhausted minute and fail together; one longer wait actually
    reaches the next one.
    """
    is_rate_limit = isinstance(error, ProviderRateLimitError)
    ceiling = rate_limit_max_delay if is_rate_limit else max_delay

    if is_rate_limit and error.retry_after_seconds is not None:
        return min(ceiling, max(0.0, error.retry_after_seconds))

    window = min(ceiling, base_delay * (2 ** (attempt - 1)))
    return rng.uniform(0.0, window)  # type: ignore[union-attr]


def _ms_since(started: float) -> int:
    """Whole milliseconds since a perf_counter reading."""
    return int((time.perf_counter() - started) * 1000)


def attempts_from(error: BaseException) -> int:
    """How many attempts a failure represents, for callers writing log lines."""
    return error.attempts if isinstance(error, ProviderError) else 1


__all__ = ["RetryOutcome", "call_with_retry", "attempts_from"]
