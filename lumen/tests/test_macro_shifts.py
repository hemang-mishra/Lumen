"""
Tests for telling a shift apart from several unrelated things changing.

The threshold behaviour is the point, so most of these sit either side of it.
Two other things are worth guarding. A short report must not claim to have
looked, because "no shift" from a weekly report and "no shift" from a checked
quarterly report read identically and only one is true. And a pattern that
happens as often as it did but is now caught in the act has changed — counting
only frequency would score that as no change at all.
"""

from __future__ import annotations

from lumen.config import MacroConfig
from lumen.pipeline.macroextraction import shifts
from lumen.schemas.enums import PatternTrend, ReportType


def loosening(count: int) -> dict[str, int]:
    """A set of patterns that all fired less often than before."""
    return {f"pat_{i}": 5 for i in range(count)}


class TestWhenAShiftIsCalled:
    def test_five_patterns_moving_together_is_a_shift(
        self, make_corpus, make_window, pattern_row
    ):
        corpus = make_corpus(
            window=make_window(ReportType.QUARTERLY),
            comparison_counts=loosening(5),
            all_patterns=[pattern_row(f"pat_{i}") for i in range(5)],
        )

        found = shifts.detect_shift(corpus, {}, config=MacroConfig())

        assert found.detected is True
        assert len(found.contributing_patterns) == 5

    def test_four_patterns_is_not_yet_a_shift(
        self, make_corpus, make_window, pattern_row
    ):
        # Individual patterns move all the time. A few of them moving is a
        # hard month, not a change in how somebody sees themselves.
        corpus = make_corpus(
            window=make_window(ReportType.QUARTERLY),
            comparison_counts=loosening(4),
            all_patterns=[pattern_row(f"pat_{i}") for i in range(4)],
        )

        assert shifts.detect_shift(corpus, {}, config=MacroConfig()).detected is False

    def test_the_threshold_is_configurable(self, make_corpus, make_window):
        corpus = make_corpus(
            window=make_window(ReportType.QUARTERLY), comparison_counts=loosening(3)
        )

        found = shifts.detect_shift(
            corpus, {}, config=MacroConfig(archetype_min_patterns=3)
        )

        assert found.detected is True

    def test_patterns_moving_in_opposite_directions_are_not_a_shift(
        self, make_corpus, make_window
    ):
        corpus = make_corpus(
            window=make_window(ReportType.QUARTERLY),
            comparison_counts={"pat_a": 5, "pat_b": 5, "pat_c": 0, "pat_d": 0, "pat_e": 0},
        )

        found = shifts.detect_shift(
            corpus,
            {"pat_c": {"ep_1"}, "pat_d": {"ep_1"}, "pat_e": {"ep_1"}},
            config=MacroConfig(),
        )

        assert found.detected is False

    def test_the_comparison_stretch_is_reported_either_way(
        self, make_corpus, make_window
    ):
        corpus = make_corpus(
            window=make_window(ReportType.QUARTERLY), comparison_counts={"pat_a": 3}
        )

        found = shifts.detect_shift(corpus, {}, config=MacroConfig())

        assert found.comparison_start is not None
        assert found.comparison_end == corpus.window.period_start


class TestWhichReportsEvenLook:
    def test_a_weekly_report_does_not_pretend_to_have_looked(
        self, make_corpus, make_window
    ):
        corpus = make_corpus(
            window=make_window(ReportType.WEEKLY), comparison_counts=loosening(9)
        )

        found = shifts.detect_shift(corpus, {}, config=MacroConfig())

        assert found.detected is False
        assert found.contributing_patterns == ()
        assert found.comparison_start is None

    def test_a_monthly_report_looks(self, make_corpus, make_window):
        corpus = make_corpus(
            window=make_window(ReportType.MONTHLY), comparison_counts=loosening(5)
        )

        assert shifts.detect_shift(corpus, {}, config=MacroConfig()).detected is True

    def test_a_report_with_nothing_on_either_side_finds_nothing(
        self, make_corpus, make_window
    ):
        corpus = make_corpus(window=make_window(ReportType.QUARTERLY))

        assert shifts.detect_shift(corpus, {}, config=MacroConfig()).detected is False


class TestHowOnePatternIsClassified:
    def test_firing_more_often_is_an_increase(self, make_corpus, make_window):
        corpus = make_corpus(
            window=make_window(ReportType.QUARTERLY), comparison_counts={"pat_a": 1}
        )

        found = shifts.detect_shift(corpus, {"pat_a": {"e1", "e2"}}, config=MacroConfig())

        assert found.contributing_patterns[0].trend is PatternTrend.FREQUENCY_INCREASING

    def test_firing_less_often_is_a_decrease(self, make_corpus, make_window):
        corpus = make_corpus(
            window=make_window(ReportType.QUARTERLY), comparison_counts={"pat_a": 4}
        )

        found = shifts.detect_shift(corpus, {"pat_a": {"e1"}}, config=MacroConfig())

        assert found.contributing_patterns[0].trend is PatternTrend.FREQUENCY_DECREASING

    def test_being_caught_in_the_act_more_often_is_a_change(
        self, make_corpus, make_window
    ):
        # Same frequency, more awareness. This is a real change and the
        # obvious measure would miss it entirely.
        corpus = make_corpus(
            window=make_window(ReportType.QUARTERLY),
            comparison_counts={"pat_a": 2},
            awareness_counts={"pat_a": 3},
            previous_awareness_counts={"pat_a": 0},
        )

        found = shifts.detect_shift(corpus, {"pat_a": {"e1", "e2"}}, config=MacroConfig())

        assert found.contributing_patterns[0].trend is PatternTrend.AWARENESS_INCREASING

    def test_a_pattern_that_did_not_move_at_all_is_left_out(
        self, make_corpus, make_window
    ):
        corpus = make_corpus(
            window=make_window(ReportType.QUARTERLY), comparison_counts={"pat_a": 1}
        )

        found = shifts.detect_shift(corpus, {"pat_a": {"e1"}}, config=MacroConfig())

        assert found.contributing_patterns == ()

    def test_a_fading_pattern_is_counted_once_not_twice(
        self, make_corpus, make_window
    ):
        # Both fading and being caught more often would otherwise let one
        # pattern count towards the threshold under two headings.
        corpus = make_corpus(
            window=make_window(ReportType.QUARTERLY),
            comparison_counts={"pat_a": 5},
            awareness_counts={"pat_a": 4},
            previous_awareness_counts={"pat_a": 1},
        )

        found = shifts.detect_shift(corpus, {"pat_a": {"e1"}}, config=MacroConfig())

        assert len(found.contributing_patterns) == 1
        assert found.contributing_patterns[0].trend is PatternTrend.FREQUENCY_DECREASING

    def test_a_pattern_absent_from_one_side_is_still_considered(
        self, make_corpus, make_window
    ):
        # Appearing for the first time and stopping entirely are the two
        # clearest movements there are.
        corpus = make_corpus(window=make_window(ReportType.QUARTERLY))

        found = shifts.detect_shift(corpus, {"pat_new": {"e1"}}, config=MacroConfig())

        assert found.contributing_patterns[0].pattern_id == "pat_new"


class TestNamingWhatMoved:
    def test_a_pattern_is_labelled_from_whichever_list_holds_it(
        self, make_corpus, make_window, pattern_row
    ):
        corpus = make_corpus(
            window=make_window(ReportType.QUARTERLY),
            comparison_counts={"pat_a": 3},
            all_patterns=[pattern_row("pat_a", name="Seeking approval")],
        )

        found = shifts.detect_shift(corpus, {}, config=MacroConfig())

        assert found.contributing_patterns[0].label == "Seeking approval"

    def test_an_unknown_pattern_keeps_its_identifier(self, make_corpus, make_window):
        corpus = make_corpus(
            window=make_window(ReportType.QUARTERLY), comparison_counts={"pat_a": 3}
        )

        found = shifts.detect_shift(corpus, {}, config=MacroConfig())

        assert found.contributing_patterns[0].label == "pat_a"
