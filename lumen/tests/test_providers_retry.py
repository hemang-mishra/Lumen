"""
Tests for retrying model calls.

None of these actually wait. The thing that sleeps is passed in, so a test can
check what the wait *would* have been and move on, which keeps a suite that
covers several failure paths fast.
"""

from __future__ import annotations

import random

import pytest

from lumen.providers.errors import (
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from lumen.providers.retry import attempts_from, call_with_retry
from lumen.schemas.enums import ModelRole


def run(operation, *, sleeper=lambda _: None, **overrides):
    """Call the retry helper with sensible defaults for a test."""
    settings = {
        "provider": "test",
        "model": "test-model",
        "role": ModelRole.LIGHTWEIGHT,
        "max_attempts": 3,
        "base_delay": 1.0,
        "max_delay": 8.0,
        "rate_limit_max_delay": 65.0,
        "sleeper": sleeper,
        "random_source": random.Random(1234),
    }
    settings.update(overrides)
    return call_with_retry(operation, **settings)


class _FailsThenWorks:
    """Fails a set number of times, then returns a value."""

    def __init__(self, failures: int, error=None, value="ok"):
        self.remaining = failures
        self.error = error or ProviderTimeoutError("too slow")
        self.value = value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise self.error
        return self.value


class TestSucceeding:
    def test_a_call_that_works_first_time_is_not_retried(self):
        operation = _FailsThenWorks(failures=0)
        outcome = run(operation)
        assert outcome.value == "ok"
        assert outcome.attempts == 1
        assert operation.calls == 1

    @pytest.mark.parametrize("failures,expected_attempts", [(1, 2), (2, 3)])
    def test_it_keeps_trying_until_it_works(self, failures, expected_attempts):
        operation = _FailsThenWorks(failures=failures)
        outcome = run(operation)
        assert outcome.value == "ok"
        assert outcome.attempts == expected_attempts

    def test_it_waits_between_attempts(self):
        waits: list[float] = []
        run(_FailsThenWorks(failures=2), sleeper=waits.append)
        assert len(waits) == 2

    def test_a_call_that_works_first_time_never_waits(self):
        waits: list[float] = []
        run(_FailsThenWorks(failures=0), sleeper=waits.append)
        assert waits == []


class TestGivingUp:
    def test_the_last_failure_is_raised_once_attempts_run_out(self):
        with pytest.raises(ProviderTimeoutError):
            run(_FailsThenWorks(failures=5))

    def test_it_stops_at_the_attempt_limit(self):
        operation = _FailsThenWorks(failures=5)
        with pytest.raises(ProviderTimeoutError):
            run(operation, max_attempts=2)
        assert operation.calls == 2

    def test_the_failure_says_how_many_attempts_were_made(self):
        with pytest.raises(ProviderTimeoutError) as caught:
            run(_FailsThenWorks(failures=5), max_attempts=3)
        assert caught.value.attempts == 3

    def test_it_does_not_wait_after_the_final_attempt(self):
        """Waiting before giving up would delay the failure for nothing."""
        waits: list[float] = []
        with pytest.raises(ProviderTimeoutError):
            run(_FailsThenWorks(failures=5), max_attempts=3, sleeper=waits.append)
        assert len(waits) == 2

    def test_a_single_attempt_means_no_retrying(self):
        operation = _FailsThenWorks(failures=1)
        with pytest.raises(ProviderTimeoutError):
            run(operation, max_attempts=1)
        assert operation.calls == 1

    def test_asking_for_no_attempts_is_rejected(self):
        with pytest.raises(ValueError, match="at least 1"):
            run(_FailsThenWorks(failures=0), max_attempts=0)


class TestNotRetrying:
    def test_a_rejected_request_is_not_tried_again(self):
        """The model answered; asking the same thing gets the same answer."""
        calls = {"count": 0}

        def operation():
            calls["count"] += 1
            raise ProviderResponseError("rejected")

        with pytest.raises(ProviderResponseError):
            run(operation)
        assert calls["count"] == 1

    def test_an_unrelated_exception_passes_straight_through(self):
        def operation():
            raise KeyError("something else entirely")

        with pytest.raises(KeyError):
            run(operation)


class TestBackoff:
    def test_waits_never_exceed_the_ceiling(self):
        waits: list[float] = []
        with pytest.raises(ProviderTimeoutError):
            run(
                _FailsThenWorks(failures=9),
                max_attempts=8,
                base_delay=1.0,
                max_delay=4.0,
                sleeper=waits.append,
            )
        assert all(0.0 <= wait <= 4.0 for wait in waits)

    def test_the_window_grows_with_each_attempt(self):
        """
        Checked as an upper bound rather than an exact figure, because the wait
        is randomised on purpose so simultaneous callers do not collide.
        """
        waits: list[float] = []
        with pytest.raises(ProviderTimeoutError):
            run(
                _FailsThenWorks(failures=9),
                max_attempts=4,
                base_delay=1.0,
                max_delay=100.0,
                sleeper=waits.append,
            )
        assert waits[0] <= 1.0
        assert waits[1] <= 2.0
        assert waits[2] <= 4.0

    def test_the_wait_is_randomised(self):
        """Fixed waits would have several workers retry in lockstep forever."""
        first: list[float] = []
        second: list[float] = []
        with pytest.raises(ProviderTimeoutError):
            run(_FailsThenWorks(failures=9), max_attempts=6, sleeper=first.append,
                random_source=random.Random(1))
        with pytest.raises(ProviderTimeoutError):
            run(_FailsThenWorks(failures=9), max_attempts=6, sleeper=second.append,
                random_source=random.Random(2))
        assert first != second


class TestRateLimits:
    def test_a_rate_limit_gets_the_larger_ceiling(self):
        """
        Quotas are counted per minute, so several short retries all land inside
        the same exhausted minute. One longer wait actually reaches the next one.
        """
        waits: list[float] = []
        with pytest.raises(ProviderRateLimitError):
            run(
                _FailsThenWorks(failures=9, error=ProviderRateLimitError("slow down")),
                max_attempts=6,
                base_delay=1.0,
                max_delay=8.0,
                rate_limit_max_delay=65.0,
                sleeper=waits.append,
            )
        assert max(waits) > 8.0

    def test_other_failures_keep_the_smaller_ceiling(self):
        waits: list[float] = []
        with pytest.raises(ProviderUnavailableError):
            run(
                _FailsThenWorks(failures=9, error=ProviderUnavailableError("busy")),
                max_attempts=6,
                base_delay=1.0,
                max_delay=8.0,
                rate_limit_max_delay=65.0,
                sleeper=waits.append,
            )
        assert max(waits) <= 8.0

    def test_the_wait_the_server_asked_for_wins(self):
        """It knows when the quota resets; we are only guessing."""
        waits: list[float] = []
        error = ProviderRateLimitError("slow down", retry_after_seconds=12.5)
        run(_FailsThenWorks(failures=1, error=error), sleeper=waits.append)
        assert waits == [12.5]

    def test_an_unreasonable_wait_is_capped(self):
        """
        The figure comes over the network. Believing an hour would park a
        pipeline run for an hour, so it is trusted only up to the longest wait
        we are willing to take.
        """
        waits: list[float] = []
        error = ProviderRateLimitError("slow down", retry_after_seconds=3600.0)
        run(
            _FailsThenWorks(failures=1, error=error),
            rate_limit_max_delay=65.0,
            sleeper=waits.append,
        )
        assert waits == [65.0]

    def test_a_negative_wait_is_treated_as_no_wait(self):
        waits: list[float] = []
        error = ProviderRateLimitError("slow down", retry_after_seconds=-5.0)
        run(_FailsThenWorks(failures=1, error=error), sleeper=waits.append)
        assert waits == [0.0]


class TestTimings:
    def test_both_timings_are_reported(self):
        outcome = run(_FailsThenWorks(failures=0))
        assert outcome.latency_ms >= 0
        assert outcome.elapsed_ms >= 0

    def test_the_attempt_time_excludes_waiting(self):
        """
        latency_ms is how fast the model is; elapsed_ms is how long the caller
        waited. A call that retried twice should not look like a slow model.
        """

        def slow_sleeper(seconds: float) -> None:
            import time

            time.sleep(0.05)

        outcome = run(_FailsThenWorks(failures=2), sleeper=slow_sleeper)
        assert outcome.elapsed_ms >= 100
        assert outcome.latency_ms < 50


class TestAttemptsHelper:
    def test_it_reads_the_count_off_a_provider_failure(self):
        error = ProviderTimeoutError("too slow")
        error.attempts = 3
        assert attempts_from(error) == 3

    def test_anything_else_counts_as_one(self):
        assert attempts_from(ValueError("unrelated")) == 1
