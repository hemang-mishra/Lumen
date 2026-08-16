"""
Tests that the written week is well-formed and still says what it claims.

These guard against a corpus that has quietly stopped exercising what it was
written to exercise. A day reworded until it passes, a reply left behind
after the day around it changed, an arc that no longer contains an EVOLVE —
each of those turns the multi-day test into an expensive way of asserting
nothing, and none of them would fail any assertion in that test.
"""

from __future__ import annotations

from datetime import timedelta

import json

import pytest

from lumen.simulation import corpus
from lumen.simulation.corpus import CORPUS, replies_for
from lumen.simulation.runner import STEP_MARKERS


class TestTheWeekItself:
    def test_it_is_five_consecutive_days(self):
        assert len(CORPUS) == 5

        for earlier, later in zip(CORPUS, CORPUS[1:], strict=False):
            assert later.event_date - earlier.event_date == timedelta(days=1)

    def test_the_days_are_numbered_in_order(self):
        assert [day.day for day in CORPUS] == [1, 2, 3, 4, 5]

    def test_every_day_says_what_it_is_for(self):
        # Written before the replies were. If a day is reworded until a test
        # passes, this is what it should be checked against.
        for day in CORPUS:
            assert len(day.intent) > 40, f"day {day.day} does not say what it is for"

    def test_every_day_has_something_written_in_it(self):
        for day in CORPUS:
            assert len(day.text.split()) >= 30, (
                f"day {day.day} is short enough to be treated as a thin entry, "
                "which would skip the stages this corpus exists to exercise"
            )


class TestTheThemes:
    def test_every_theme_a_day_claims_is_a_real_one(self):
        known = {theme.name for theme in corpus.THEMES}

        for day in CORPUS:
            assert set(day.themes) <= known, f"day {day.day} names an unknown theme"

    def test_the_running_thread_is_in_most_of_the_week(self):
        # The whole point is one thing said repeatedly. If the thread thins
        # out, the accumulation being tested stops being tested.
        threaded = [day for day in CORPUS if "comparison" in day.themes]

        assert len(threaded) >= 4

    def test_each_days_text_actually_contains_its_themes(self):
        from lumen.simulation.themes import ThemedEmbeddingProvider

        embedder = ThemedEmbeddingProvider(corpus.THEMES, dimensions=32)

        for day in CORPUS:
            found = set(embedder.themes_in(day.text))
            assert set(day.themes) <= found, (
                f"day {day.day} claims themes {day.themes} but its words only "
                f"reach {sorted(found)}"
            )

    def test_the_week_is_not_all_one_subject(self):
        # A week about exactly one thing would not show that the system can
        # tell two threads apart.
        subjects = {theme for day in CORPUS for theme in day.themes}

        assert len(subjects) >= 2


class TestTheArc:
    def test_something_is_created_on_the_first_day(self):
        assert CORPUS[0].expects.new_patterns == 1

    def test_the_middle_of_the_week_accumulates_rather_than_creates(self):
        # This is the failure the whole goal exists to catch: the same thing
        # said again becoming a second record.
        for day in CORPUS[1:3]:
            assert day.expects.reinforced, f"day {day.day} should add evidence"
            assert day.expects.new_patterns == 0
            assert day.expects.new_beliefs == 0

    def test_a_belief_is_created_and_then_replaced(self):
        creates = [day for day in CORPUS if day.expects.new_beliefs]
        evolves = [day for day in CORPUS if day.expects.evolves]

        assert creates, "no day creates a belief, so no version chain can form"
        assert evolves, "no day replaces one, so no version chain can form"
        assert creates[0].day < evolves[0].day

    def test_one_day_holds_two_separate_subjects(self):
        # Which is what puts two episodes in one entry, and so what
        # exercises the ordering between them.
        assert any(day.expects.episodes > 1 for day in CORPUS)


class TestTheReplies:
    def test_every_step_a_day_answers_is_a_step_that_exists(self):
        # A reply left behind after the day around it changed answers a
        # prompt that is never sent, and nothing else would notice.
        known = {step for step, _ in STEP_MARKERS}

        for day in CORPUS:
            unknown = set(replies_for(day)) - known
            assert unknown == set(), f"day {day.day} answers unknown steps: {unknown}"

    def test_every_day_answers_the_steps_every_entry_reaches(self):
        for day in CORPUS:
            answers = replies_for(day)
            for step in ("normalize_text", "structure", "triage", "extract_reflection"):
                assert step in answers, f"day {day.day} has no reply for {step}"

    def test_every_reply_is_readable(self):
        for day in CORPUS:
            for step, reply in replies_for(day).items():
                try:
                    json.loads(reply)
                except ValueError as exc:  # pragma: no cover - a failure message
                    pytest.fail(f"day {day.day}'s {step} reply is not readable: {exc}")

    def test_a_day_that_splits_in_two_says_so_in_both_places(self):
        # The reply that does the splitting and the expectation that counts
        # the pieces have to agree, or the day silently tests something else.
        for day in CORPUS:
            if "structure" not in day.replies:
                assert day.expects.episodes == 1
                continue
            pieces = json.loads(day.replies["structure"])["episodes"]
            assert len(pieces) == day.expects.episodes

    def test_a_consequential_action_is_put_to_the_careful_model(self):
        # Evolving, contradicting and holding two things at once are all
        # re-asked before they count, so a day claiming one needs an answer
        # for that second question too.
        for day in CORPUS:
            decisions = json.loads(day.replies.get("decision", '{"decisions":[]}'))
            actions = {
                item["primary"]["action"] for item in decisions["decisions"]
            }
            if actions & {"EVOLVE", "CONTRADICT", "DIALECTIC"}:
                assert "escalation" in day.replies, (
                    f"day {day.day} claims a consequential action but has no "
                    "answer for the confirming question"
                )

    def test_a_day_that_reinforces_names_a_record_an_earlier_day_created(self):
        # Pointing at a record nothing ever made would be quietly refused,
        # and the day would look like it simply decided nothing.
        created = {corpus.PATTERN_COMPARISON, corpus.BELIEF_PACE}

        for day in CORPUS:
            for target in (*day.expects.reinforced, *day.expects.evolves):
                assert target in created, (
                    f"day {day.day} points at {target}, which no day creates"
                )
