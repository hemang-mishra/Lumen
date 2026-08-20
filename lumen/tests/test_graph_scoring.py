"""
Tests for what a stored record is worth when history is searched.

Most of this file sits exactly on a threshold, because the thresholds are
where the behaviour is. The rest is about the directions each rule fails in:
a missing date, a clock disagreement and an unreadable status all have a safe
answer and an unsafe one, and every one of them here takes the safe one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lumen.config import ScoringConfig
from lumen.graph import scoring
from lumen.schemas.enums import PatternAgeBand

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


def quiet_for(days: int) -> str:
    """A stored date that many days before this file's fixed moment."""
    return (NOW - timedelta(days=days)).isoformat()


def pattern(**fields) -> dict:
    """A stored pattern row, with nothing to discount it unless said."""
    return {
        "node_id": "pat_1",
        "_label": "PatternNode",
        "signal_strength": "STANDARD",
        "verification_status": "IMPLICIT",
        "query_frequency": 0,
        **fields,
    }


class TestHowMuchAgeCosts:
    @pytest.mark.parametrize(
        "days,band,weight",
        [
            (0, PatternAgeBand.FRESH, 1.0),
            (29, PatternAgeBand.FRESH, 1.0),
            (30, PatternAgeBand.COOLING, 0.85),
            (179, PatternAgeBand.COOLING, 0.85),
            (180, PatternAgeBand.STALE, 0.70),
            (364, PatternAgeBand.STALE, 0.70),
            (365, PatternAgeBand.DORMANT, 0.50),
            (4000, PatternAgeBand.DORMANT, 0.50),
        ],
    )
    def test_each_band_at_its_exact_day(self, days, band, weight):
        last_seen = NOW - timedelta(days=days)
        config = ScoringConfig()

        assert scoring.age_band(last_seen, NOW, config=config) is band
        assert scoring.recency_weight(last_seen, NOW, config=config) == weight

    def test_nothing_ever_decays_to_nothing(self):
        # An old record ranks lower and stays reachable. A search that dropped
        # old material would lose exactly the long-running things worth
        # keeping.
        ancient = NOW - timedelta(days=20_000)

        assert scoring.recency_weight(ancient, NOW, config=ScoringConfig()) > 0

    def test_a_record_with_no_readable_date_is_not_treated_as_old(self):
        # The cautious direction: a missing date must never cost a record its
        # place, because nothing about it says the record is old.
        assert scoring.recency_weight(None, NOW, config=ScoringConfig()) == 1.0
        assert scoring.quiet_days(None, NOW) == 0

    def test_a_record_dated_in_the_future_is_not_penalised(self):
        # Imports can produce these, and two clocks disagreeing should not
        # look like anything about the record.
        ahead = NOW + timedelta(days=5)

        assert scoring.quiet_days(ahead, NOW) == 0
        assert scoring.recency_weight(ahead, NOW, config=ScoringConfig()) == 1.0

    def test_a_stored_date_with_no_timezone_is_read_as_utc(self):
        bare = datetime(2026, 8, 19, 12)

        assert scoring.quiet_days(bare, NOW) == 1

    def test_turning_decay_off_removes_it_entirely(self):
        ancient = NOW - timedelta(days=900)
        config = ScoringConfig(decay_enabled=False)

        assert scoring.recency_weight(ancient, NOW, config=config) == 1.0
        # The band is still reported honestly. It is a fact about the record
        # rather than about the setting.
        assert scoring.age_band(ancient, NOW, config=config) is PatternAgeBand.DORMANT


class TestThresholdsOutOfOrder:
    def test_they_are_sorted_rather_than_left_unreachable(self):
        # Set this way round, "cooling" would sit past "dormant" and no
        # record could ever fall into the band between them.
        config = ScoringConfig(fresh_days=100, cooling_days=10, dormant_days=5)
        thresholds = scoring.Thresholds.of(config)

        assert thresholds.fresh <= thresholds.cooling <= thresholds.dormant

    def test_a_negative_threshold_is_read_as_none(self):
        thresholds = scoring.Thresholds.of(ScoringConfig(fresh_days=-30))

        assert thresholds.fresh == 0


class TestWhatConfirmationIsWorth:
    def test_something_the_person_said_themselves_counts_in_full(self):
        row = pattern(verification_status="IMPLICIT")

        assert scoring.trust_weight(row, config=ScoringConfig()) == 1.0

    def test_something_they_confirmed_counts_in_full(self):
        row = pattern(verification_status="VERIFIED")

        assert scoring.trust_weight(row, config=ScoringConfig()) == 1.0

    def test_an_unconfirmed_suggestion_counts_for_less(self):
        # Otherwise the system slowly starts quoting itself back and calling
        # it the person's own history.
        row = pattern(verification_status="UNVERIFIED")

        assert scoring.trust_weight(row, config=ScoringConfig()) == 0.5

    def test_an_unreadable_status_is_treated_as_the_person_s_own(self):
        # This can only fail to demote something, never demote something that
        # did not deserve it.
        assert scoring.trust_weight(pattern(verification_status="???"), config=ScoringConfig()) == 1.0
        assert scoring.trust_weight(pattern(verification_status=None), config=ScoringConfig()) == 1.0


class TestWhatUsefulnessEarns:
    def test_each_time_it_helped_lifts_it_a_little(self):
        row = pattern(query_frequency=3)

        assert scoring.frequency_weight(row, config=ScoringConfig()) == pytest.approx(1.3)

    def test_the_lift_is_capped(self):
        # The cap is the whole reason this is safe to have: being shown makes
        # a record more likely to be shown, and without a ceiling that loop
        # runs away.
        row = pattern(query_frequency=500)

        assert scoring.frequency_weight(row, config=ScoringConfig()) == 1.5

    def test_a_record_never_shown_is_not_penalised(self):
        assert scoring.frequency_weight(pattern(), config=ScoringConfig()) == 1.0

    def test_an_unreadable_counter_is_read_as_none(self):
        assert scoring.frequency_weight(pattern(query_frequency="lots"), config=ScoringConfig()) == 1.0
        assert scoring.frequency_weight(pattern(query_frequency=-4), config=ScoringConfig()) == 1.0

    def test_it_can_be_switched_off(self):
        row = pattern(query_frequency=5)

        assert scoring.frequency_weight(row, config=ScoringConfig(frequency_enabled=False)) == 1.0


class TestWhichDateIsRead:
    def test_a_pattern_uses_when_it_was_last_evidenced(self):
        row = pattern(
            last_reinforced_at=quiet_for(10),
            occurred_at=quiet_for(900),
            created_at=quiet_for(900),
        )

        assert scoring.weigh(row, now=NOW, config=ScoringConfig()).quiet_days == 10

    def test_a_record_with_no_such_stamp_falls_back_to_when_it_happened(self):
        row = {"node_id": "obs_1", "_label": "ObservationNode", "occurred_at": quiet_for(200)}

        assert scoring.weigh(row, now=NOW, config=ScoringConfig()).quiet_days == 200

    def test_an_unreadable_date_is_the_same_as_none(self):
        row = pattern(last_reinforced_at="last Tuesday")

        assert scoring.weigh(row, now=NOW, config=ScoringConfig()).recency == 1.0


class TestPuttingItTogether:
    def test_the_parts_are_kept_beside_the_total(self):
        # A ranking nobody can explain is a ranking nobody can fix.
        row = pattern(
            signal_strength="CRITICAL",
            verification_status="UNVERIFIED",
            query_frequency=2,
            last_reinforced_at=quiet_for(400),
        )

        weights = scoring.weigh(row, now=NOW, config=ScoringConfig())

        assert weights.signal == 2.0
        assert weights.recency == 0.5
        assert weights.trust == 0.5
        assert weights.frequency == pytest.approx(1.2)
        assert weights.multiplier == pytest.approx(2.0 * 0.5 * 0.5 * 1.2)

    def test_the_score_starts_from_how_good_the_match_was(self):
        row = pattern(signal_strength="HIGH", last_reinforced_at=quiet_for(1))

        assert scoring.final_score(0.4, row, now=NOW, config=ScoringConfig()) == pytest.approx(0.6)

    def test_a_negative_starting_point_is_read_as_no_match(self):
        assert scoring.final_score(-1.0, pattern(), now=NOW, config=ScoringConfig()) == 0.0

    def test_an_ordinary_recent_record_is_worth_exactly_what_it_matched(self):
        row = pattern(last_reinforced_at=quiet_for(1))

        assert scoring.final_score(0.73, row, now=NOW, config=ScoringConfig()) == pytest.approx(0.73)
