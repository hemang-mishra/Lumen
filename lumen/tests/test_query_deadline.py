"""
Running several searches side by side under one shared deadline.

The single-call form is exercised by the turn-reading suite. This one is
about the parallel form, where the interesting cases are all partial: one
piece finishes and another does not, or one raises while another answers.
Every one of those has to leave the pieces that worked intact, because the
whole reason for running them separately is that they fail in different
places.
"""

from __future__ import annotations

import threading
import time

import pytest

from lumen.observability.trace import bind_trace, get_trace_id
from lumen.query.deadline import Attempt, DeadlineRunner


@pytest.fixture
def runner():
    """A small pool, closed when the test finishes."""
    pool = DeadlineRunner(max_workers=3, name="test-parallel")
    yield pool
    pool.close()


class TestRunningSeveralPieces:
    def test_every_piece_answers(self, runner):
        attempts = runner.run_all(
            {"first": lambda: 1, "second": lambda: 2}, timeout_seconds=2.0
        )

        assert [attempt.value for attempt in attempts] == [1, 2]
        assert all(attempt.ok for attempt in attempts)

    def test_answers_come_back_in_the_order_they_were_given(self, runner):
        # Position is how the caller names its pieces, so a runner that
        # returned them in completion order would silently swap two results.
        attempts = runner.run_all(
            {
                "slow": lambda: (time.sleep(0.05), "slow")[1],
                "fast": lambda: "fast",
            },
            timeout_seconds=2.0,
        )

        assert [attempt.name for attempt in attempts] == ["slow", "fast"]
        assert [attempt.value for attempt in attempts] == ["slow", "fast"]

    def test_nothing_to_do_is_answered_with_nothing(self, runner):
        assert runner.run_all({}, timeout_seconds=1.0) == []

    def test_the_pieces_really_do_run_at_the_same_time(self, runner):
        # Two pieces that each wait on the other can only both finish if
        # they are running together.
        first_started = threading.Event()
        second_started = threading.Event()

        def first():
            first_started.set()
            return second_started.wait(timeout=2.0)

        def second():
            second_started.set()
            return first_started.wait(timeout=2.0)

        attempts = runner.run_all(
            {"first": first, "second": second}, timeout_seconds=3.0
        )

        assert [attempt.value for attempt in attempts] == [True, True]


class TestWhenOnePieceGoesWrong:
    def test_a_piece_that_raises_does_not_cost_the_others(self, runner):
        def broken():
            raise RuntimeError("the index is not there")

        attempts = runner.run_all(
            {"broken": broken, "fine": lambda: "answer"}, timeout_seconds=2.0
        )

        assert attempts[0].ok is False
        assert attempts[0].failure == "RuntimeError"
        assert attempts[1].value == "answer"

    def test_a_piece_that_runs_over_is_reported_as_that_and_not_as_an_error(
        self, runner
    ):
        # A provider that is always slow and one that is always broken are
        # different problems with different fixes, and they must not arrive
        # looking alike.
        attempts = runner.run_all(
            {"slow": lambda: time.sleep(1.0), "fast": lambda: "here"},
            timeout_seconds=0.05,
        )

        assert attempts[0].timed_out is True
        assert attempts[0].failure == "timed_out"
        assert attempts[1].value == "here"

    def test_a_closed_pool_reports_every_piece_as_failed(self, runner):
        runner.close()

        attempts = runner.run_all({"anything": lambda: 1}, timeout_seconds=1.0)

        assert attempts[0].ok is False
        assert attempts[0].failure == "RuntimeError"

    def test_a_negative_budget_is_treated_as_no_time_at_all(self, runner):
        attempts = runner.run_all(
            {"slow": lambda: time.sleep(0.5)}, timeout_seconds=-1.0
        )

        assert attempts[0].timed_out is True


class TestWhatAnAttemptSays:
    def test_an_answer_is_not_a_failure(self):
        assert Attempt(name="a", value=1).ok is True
        assert Attempt(name="a", value=1).failure is None

    def test_timing_out_wins_over_any_error(self):
        # Both can be true of an abandoned piece that later breaks. The
        # deadline is the fact that explains the turn.
        attempt = Attempt(name="a", error=ValueError(), timed_out=True)

        assert attempt.failure == "timed_out"


class TestTracing:
    def test_each_piece_keeps_the_trace_of_the_turn_that_caused_it(self, runner):
        # Context set on one thread is invisible on another, so this is
        # carried across by hand. Without it, the log lines from a search
        # belong to nothing.
        with bind_trace("trace-parallel"):
            attempts = runner.run_all(
                {"first": get_trace_id, "second": get_trace_id},
                timeout_seconds=2.0,
            )

        assert [attempt.value for attempt in attempts] == [
            "trace-parallel",
            "trace-parallel",
        ]
