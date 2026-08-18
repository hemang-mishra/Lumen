"""
Tests for noticing which patterns have gone quiet.

The thresholds are the whole of this module, so most of these tests sit
exactly on one. The important case is the last: ageing is measured against the
end of the period being reported on, never against today, because a report is
a statement about a stretch of time and re-running last year's report should
not age everything in it by a year.
"""

from __future__ import annotations

from lumen.config import MacroConfig
from lumen.pipeline.macroextraction import aging
from lumen.schemas.enums import PatternAgeBand


class TestWhenAPatternCounts:
    def test_a_pattern_seen_recently_is_not_ageing(self, make_corpus, pattern_row):
        corpus = make_corpus(
            all_patterns=[
                pattern_row("pat_a", last_reinforced="2026-05-20T00:00:00+00:00")
            ]
        )

        assert aging.age_patterns(corpus, config=MacroConfig()) == []

    def test_a_pattern_quiet_for_half_a_year_is_cooling(self, make_corpus, pattern_row):
        corpus = make_corpus(
            all_patterns=[
                pattern_row("pat_a", last_reinforced="2025-10-01T00:00:00+00:00")
            ]
        )

        aged = aging.age_patterns(corpus, config=MacroConfig())

        assert aged[0].band is PatternAgeBand.COOLING
        assert aged[0].weight_multiplier == 0.85

    def test_a_pattern_quiet_for_over_a_year_is_dormant(self, make_corpus, pattern_row):
        corpus = make_corpus(
            all_patterns=[
                pattern_row("pat_a", last_reinforced="2024-01-01T00:00:00+00:00")
            ]
        )

        aged = aging.age_patterns(corpus, config=MacroConfig())

        assert aged[0].band is PatternAgeBand.DORMANT
        assert aged[0].weight_multiplier == 0.5

    def test_a_dormant_pattern_carries_a_question_rather_than_a_conclusion(
        self, make_corpus, pattern_row
    ):
        # Past a year the record genuinely cannot say whether something
        # resolved or just stopped being written down. The honest response is
        # to ask, and the question is fixed rather than reworded every time.
        corpus = make_corpus(
            all_patterns=[
                pattern_row("pat_a", last_reinforced="2024-01-01T00:00:00+00:00")
            ]
        )

        aged = aging.age_patterns(corpus, config=MacroConfig())

        assert aged[0].re_interrogation_prompt == aging.RE_INTERROGATION_PROMPT

    def test_a_cooling_pattern_carries_no_question(self, make_corpus, pattern_row):
        corpus = make_corpus(
            all_patterns=[
                pattern_row("pat_a", last_reinforced="2025-10-01T00:00:00+00:00")
            ]
        )

        assert aging.age_patterns(corpus, config=MacroConfig())[0].re_interrogation_prompt is None


class TestExactlyOnTheThresholds:
    def test_a_pattern_at_the_cooling_line_has_not_crossed_it(
        self, make_corpus, pattern_row
    ):
        # The period ends on 1 June; 180 days before that is 3 December.
        corpus = make_corpus(
            all_patterns=[
                pattern_row("pat_a", last_reinforced="2025-12-03T00:00:00+00:00")
            ]
        )

        assert aging.age_patterns(corpus, config=MacroConfig()) == []

    def test_a_pattern_one_day_past_the_line_has_crossed_it(
        self, make_corpus, pattern_row
    ):
        corpus = make_corpus(
            all_patterns=[
                pattern_row("pat_a", last_reinforced="2025-12-02T00:00:00+00:00")
            ]
        )

        assert aging.age_patterns(corpus, config=MacroConfig())[0].days_since_last_seen == 181

    def test_a_nonsensical_pair_of_thresholds_still_orders_them(
        self, make_corpus, pattern_row
    ):
        corpus = make_corpus(
            all_patterns=[
                pattern_row("pat_a", last_reinforced="2025-10-01T00:00:00+00:00")
            ]
        )

        aged = aging.age_patterns(
            corpus, config=MacroConfig(cooling_days=400, dormant_days=10)
        )

        assert aged == []


class TestWhatAgeingIsMeasuredAgainst:
    def test_it_is_measured_against_the_period_not_against_today(
        self, make_corpus, make_window, pattern_row
    ):
        # The same pattern, read by a report about 2026 and by one about 2027.
        # Only the second should call it dormant.
        pattern = pattern_row("pat_a", last_reinforced="2025-11-01T00:00:00+00:00")

        from datetime import datetime, timezone

        later = make_window(
            start=datetime(2027, 5, 1, tzinfo=timezone.utc),
            end=datetime(2027, 6, 1, tzinfo=timezone.utc),
        )

        near = aging.age_patterns(
            make_corpus(all_patterns=[pattern]), config=MacroConfig()
        )
        far = aging.age_patterns(
            make_corpus(window=later, all_patterns=[pattern]), config=MacroConfig()
        )

        assert near[0].band is PatternAgeBand.COOLING
        assert far[0].band is PatternAgeBand.DORMANT


class TestWhatIsLeftOut:
    def test_a_pattern_with_no_dates_at_all_is_skipped(self, make_corpus):
        corpus = make_corpus(all_patterns=[{"node_id": "pat_a", "pattern_name": "x"}])

        assert aging.age_patterns(corpus, config=MacroConfig()) == []

    def test_the_list_is_capped_quietest_first(self, make_corpus, pattern_row):
        corpus = make_corpus(
            all_patterns=[
                pattern_row("pat_older", last_reinforced="2024-01-01T00:00:00+00:00"),
                pattern_row("pat_old", last_reinforced="2025-01-01T00:00:00+00:00"),
                pattern_row("pat_recent", last_reinforced="2025-10-01T00:00:00+00:00"),
            ]
        )

        aged = aging.age_patterns(corpus, config=MacroConfig(aging_limit=2))

        assert [item.pattern_id for item in aged] == ["pat_older", "pat_old"]

    def test_a_pattern_falls_back_to_when_it_began(self, make_corpus):
        corpus = make_corpus(
            all_patterns=[
                {
                    "node_id": "pat_a",
                    "pattern_name": "x",
                    "valid_from": "2024-01-01T00:00:00+00:00",
                }
            ]
        )

        assert aging.age_patterns(corpus, config=MacroConfig())[0].band is PatternAgeBand.DORMANT


class TestReadingStoredDatesBack:
    def test_a_moment_already_read_as_one_is_kept(self):
        from datetime import datetime, timezone

        moment = datetime(2026, 5, 4, tzinfo=timezone.utc)

        assert aging._moment(moment) == moment

    def test_a_missing_date_is_nothing_rather_than_a_guess(self):
        assert aging._moment(None) is None
        assert aging._moment("") is None

    def test_an_unreadable_date_does_not_break_the_report(self, make_corpus):
        # One unreadable field should cost that pattern its line, not the
        # whole report.
        corpus = make_corpus(
            all_patterns=[
                {
                    "node_id": "pat_a",
                    "pattern_name": "x",
                    "last_reinforced_at": "a while ago",
                }
            ]
        )

        assert aging.age_patterns(corpus, config=MacroConfig()) == []

    def test_a_date_that_forgot_to_say_it_was_utc_is_read_as_utc(self, make_corpus):
        corpus = make_corpus(
            all_patterns=[
                {
                    "node_id": "pat_a",
                    "pattern_name": "x",
                    "last_reinforced_at": "2024-01-01T00:00:00",
                }
            ]
        )

        assert aging.age_patterns(corpus, config=MacroConfig())[0].band is PatternAgeBand.DORMANT
