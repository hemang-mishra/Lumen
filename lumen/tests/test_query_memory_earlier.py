"""
Carrying the last few days into today.

Journalling is not a series of unrelated days. Somebody picks a thread back
up on Thursday that they let go of on Monday, and an assistant that starts
every morning knowing nothing about the week cannot follow that.

The rules worth pinning are the two that make it useful rather than merely
present: that "the last three days" counts days somebody actually talked
rather than squares on the calendar, and that when they do not all fit the
oldest goes first.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from lumen.config import ChatConfig
from lumen.providers.fake import FakeLLMProvider
from lumen.query.conversation import ConversationStore
from lumen.query.memory import ConversationMemory
from lumen.query.memory.contracts import DaySummary
from lumen.query.memory.earlier import HEADING, render

TODAY = date(2026, 8, 18)
NOW = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)


@pytest.fixture
def memory(ops_store):
    """A conversation memory over a real store."""

    def _build(**settings):
        return ConversationMemory(
            store=ConversationStore(ops_store.buffers),
            llm=FakeLLMProvider(["what the day was about"] * 30),
            config=ChatConfig(**settings),
        )

    return _build


def a_day(memory: ConversationMemory, on: date, *, summary: str | None) -> str:
    """A day with something said in it, and optionally a summary of it."""
    buffer = memory.store.open("tester", on=on)
    memory.store.append(buffer.session_id, role="user", content=f"about {on}")
    if summary is not None:
        memory.store.remember_summary(buffer.session_id, summary, 0)
    return buffer.session_id


class TestWhichDaysComeBack:
    def test_the_last_few_days_are_carried_into_today(self, memory):
        mind = memory(previous_days=3)
        a_day(mind, TODAY - timedelta(days=1), summary="yesterday's thread")
        a_day(mind, TODAY - timedelta(days=2), summary="the day before")
        today = a_day(mind, TODAY, summary=None)

        recalled = mind.recall(today)

        assert [day.summary for day in recalled.previous_days] == [
            "the day before",
            "yesterday's thread",
        ]

    def test_they_arrive_oldest_first(self, memory):
        """Read in the order they happened, like any other account of a week."""
        mind = memory(previous_days=3)
        a_day(mind, TODAY - timedelta(days=1), summary="most recent")
        a_day(mind, TODAY - timedelta(days=3), summary="oldest")
        today = a_day(mind, TODAY, summary=None)

        recalled = mind.recall(today)

        assert [day.on for day in recalled.previous_days] == [
            TODAY - timedelta(days=3),
            TODAY - timedelta(days=1),
        ]

    def test_it_counts_days_that_were_talked_not_days_on_the_calendar(self, memory):
        """
        Somebody who writes twice a week is exactly who this is for. Counting
        calendar days would hand them nothing on most mornings.
        """
        mind = memory(previous_days=2)
        a_day(mind, TODAY - timedelta(days=6), summary="last week")
        a_day(mind, TODAY - timedelta(days=9), summary="the week before")
        today = a_day(mind, TODAY, summary=None)

        recalled = mind.recall(today)

        assert len(recalled.previous_days) == 2

    def test_it_stops_reaching_back_eventually(self, memory):
        mind = memory(previous_days=3, previous_day_lookback=7)
        a_day(mind, TODAY - timedelta(days=30), summary="a month ago")
        today = a_day(mind, TODAY, summary=None)

        assert mind.recall(today).previous_days == ()

    def test_a_day_nobody_summarised_is_skipped(self, memory):
        mind = memory(previous_days=3)
        a_day(mind, TODAY - timedelta(days=1), summary=None)
        today = a_day(mind, TODAY, summary=None)

        assert mind.recall(today).previous_days == ()

    def test_today_is_never_one_of_the_earlier_days(self, memory):
        mind = memory(previous_days=3)
        today = a_day(mind, TODAY, summary="what today has been about")

        assert mind.recall(today).previous_days == ()

    def test_asking_for_none_reads_nothing(self, memory):
        mind = memory(previous_days=0)
        a_day(mind, TODAY - timedelta(days=1), summary="yesterday")
        today = a_day(mind, TODAY, summary=None)

        assert mind.recall(today).previous_days == ()

    def test_a_store_that_will_not_answer_costs_the_week_and_not_the_turn(
        self, memory, monkeypatch
    ):
        mind = memory(previous_days=3)
        today = a_day(mind, TODAY, summary=None)

        def broken(*args, **kwargs):
            raise RuntimeError("the store said no")

        monkeypatch.setattr(mind.store, "days_before", broken)

        recalled = mind.recall(today)

        assert recalled.previous_days == ()
        assert recalled.turns


class TestHowTheyRead:
    def test_each_day_is_said_the_way_somebody_would_say_it(self):
        block = render(
            (DaySummary(on=date(2026, 8, 17), summary="the walk that helped"),),
            now=NOW,
            max_tokens=700,
        )

        assert "Yesterday" in block
        assert "2026-08-17" not in block

    def test_nothing_renders_as_nothing_at_all(self):
        """
        A heading with nothing under it reads as a claim that there were no
        earlier days, which is a much stronger thing to say than silence.
        """
        assert render((), now=NOW, max_tokens=700) == ""

    def test_the_oldest_day_is_dropped_first_when_they_do_not_fit(self):
        days = (
            DaySummary(on=date(2026, 8, 11), summary="the oldest thing " * 20),
            DaySummary(on=date(2026, 8, 17), summary="yesterday"),
        )

        block = render(days, now=NOW, max_tokens=30)

        assert "yesterday" in block
        assert "the oldest thing" not in block

    def test_a_budget_of_nothing_renders_nothing(self):
        days = (DaySummary(on=date(2026, 8, 17), summary="yesterday"),)

        assert render(days, now=NOW, max_tokens=0) == ""

    def test_it_says_what_the_block_is_for(self):
        days = (DaySummary(on=date(2026, 8, 17), summary="yesterday"),)

        assert HEADING in render(days, now=NOW, max_tokens=700)
