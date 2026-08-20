"""
Reading a stored row into something a conversation can use.

All three searches end here, so a mistake in this file shows up as every
candidate being subtly wrong rather than as a broken search. Most of it is
about kinds of record that keep the same idea under different column names.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lumen.graph.rows import (
    CONTENT_TABLES,
    RETIRED_STATUSES,
    SIGNAL_WEIGHT,
    is_live_content,
    preview_of,
    signal_of,
)
from lumen.query.retrieval.hydrate import Weighting, has_id, to_node
from lumen.schemas.enums import (
    Domain,
    RetrievalPass,
    SignalStrength,
    StructuralAnchorType,
    TriggerType,
)


# A fixed moment, so nothing in this file depends on the day it is run.
NOW = datetime(2026, 6, 15, 12, tzinfo=UTC)
WEIGHING = Weighting.at(NOW)


def row(**fields):
    """One stored row, in the wide shape the graph answers with."""
    return {"node_id": "n_1", "_label": "PatternNode", **fields}


def read(stored, **how):
    """Read a row into a candidate, against this file's fixed moment."""
    return to_node(stored, weighting=how.pop("weighting", WEIGHING), **how)


class TestReadingARow:
    def test_the_kind_of_record_survives(self):
        node = read(row(), found_by=RetrievalPass.SEMANTIC, similarity=0.5)

        assert node.node_type == "PatternNode"

    def test_the_readable_part_is_found_whatever_it_is_called(self):
        assert preview_of(row(pattern_name="Comparison spiral")) == "Comparison spiral"
        assert preview_of({"content": "a note"}) == "a note"
        assert preview_of({"belief_statement": "I am behind"}) == "I am behind"

    def test_a_record_with_no_recognised_text_falls_back_to_its_name(self):
        # An ugly preview beats losing a real historical match because its
        # table names things unusually.
        assert preview_of({"node_id": "n_9"}) == "n_9"
        assert preview_of({}) == "unknown node"

    def test_long_text_is_shortened(self):
        assert len(preview_of({"content": "x" * 500})) == 240

    def test_the_area_of_life_is_read_where_the_record_names_one(self):
        node = read(
            row(domain="SELF_CONCEPT"), found_by=RetrievalPass.SEMANTIC, similarity=0.5
        )

        assert node.domain is Domain.SELF_CONCEPT

    def test_a_record_naming_no_area_leaves_it_unset(self):
        # True of every individual observation, and the reason the
        # sensitivity gate has a rule for it.
        node = read(row(), found_by=RetrievalPass.SEMANTIC, similarity=0.5)

        assert node.domain is None

    def test_an_unknown_area_is_treated_as_none_rather_than_failing(self):
        node = read(
            row(domain="ASTROLOGY"), found_by=RetrievalPass.SEMANTIC, similarity=0.5
        )

        assert node.domain is None

    def test_a_record_naming_no_period_leaves_it_unset(self):
        node = read(row(), found_by=RetrievalPass.SEMANTIC, similarity=0.5)

        assert node.era_tag is None

    def test_a_blank_period_counts_as_none(self):
        node = read(
            row(era_tag="   "), found_by=RetrievalPass.SEMANTIC, similarity=0.5
        )

        assert node.era_tag is None

    def test_the_period_of_life_is_read_from_either_column(self):
        # Patterns and beliefs call it one thing, episodes another.
        tagged = read(
            row(era_tag="hostel"), found_by=RetrievalPass.SEMANTIC, similarity=0.5
        )
        episode = read(
            row(_label="EpisodeNode", historical_era="hostel"),
            found_by=RetrievalPass.SEMANTIC,
            similarity=0.5,
        )

        assert tagged.era_tag == episode.era_tag == "hostel"

    def test_lists_come_back_as_lists(self):
        node = read(
            row(raw_evidence='["what they said"]'),
            found_by=RetrievalPass.SEMANTIC,
            similarity=0.5,
        )

        assert node.properties["raw_evidence"] == ["what they said"]

    def test_empty_columns_are_dropped(self):
        node = read(
            row(pattern_description="", era_tag=None),
            found_by=RetrievalPass.SEMANTIC,
            similarity=0.5,
        )

        assert "pattern_description" not in node.properties
        assert "era_tag" not in node.properties


class TestWhenItHappened:
    def test_the_moment_it_happened_is_preferred(self):
        node = read(
            row(
                occurred_at="2026-06-01T10:00:00+00:00",
                valid_from="2026-06-02T10:00:00+00:00",
            ),
            found_by=RetrievalPass.SEMANTIC,
            similarity=0.5,
        )

        assert node.occurred_at == datetime(2026, 6, 1, 10, tzinfo=UTC)

    def test_a_standing_record_falls_back_to_when_it_became_true(self):
        # A belief has no single moment it occurred.
        node = read(
            row(valid_from="2026-06-02T10:00:00+00:00"),
            found_by=RetrievalPass.SEMANTIC,
            similarity=0.5,
        )

        assert node.occurred_at == datetime(2026, 6, 2, 10, tzinfo=UTC)

    def test_a_trailing_z_is_understood(self):
        node = read(
            row(occurred_at="2026-06-01T10:00:00Z"),
            found_by=RetrievalPass.SEMANTIC,
            similarity=0.5,
        )

        assert node.occurred_at == datetime(2026, 6, 1, 10, tzinfo=UTC)

    def test_an_unreadable_date_costs_the_ordering_and_not_the_turn(self):
        node = read(
            row(occurred_at="last Tuesday"),
            found_by=RetrievalPass.SEMANTIC,
            similarity=0.5,
        )

        assert node.occurred_at is None

    def test_a_store_that_answers_with_a_real_date_is_taken_as_it_is(self):
        # Kuzu keeps these in text columns, so this branch is for the store
        # after it — the whole point of the provider protocol is that one can
        # be swapped in, and a graph that returns real timestamps should not
        # have them thrown away as unparseable.
        moment = datetime(2026, 6, 1, 10, tzinfo=UTC)

        node = read(
            row(occurred_at=moment), found_by=RetrievalPass.SEMANTIC, similarity=0.5
        )

        assert node.occurred_at == moment

    def test_a_record_with_no_date_at_all_leaves_it_unset(self):
        node = read(row(), found_by=RetrievalPass.SEMANTIC, similarity=0.5)

        assert node.occurred_at is None


class TestScoring:
    def test_a_measured_match_is_weighted_by_the_record(self):
        node = read(
            row(signal_strength="CRITICAL"),
            found_by=RetrievalPass.SEMANTIC,
            similarity=0.5,
        )

        assert node.rank_score == pytest.approx(1.0)
        assert node.similarity == pytest.approx(0.5)

    def test_an_anchor_match_uses_the_configured_base_and_no_measurement(self):
        node = read(
            row(),
            found_by=RetrievalPass.STRUCTURAL,
            anchor_type=StructuralAnchorType.NAMED_PERSON,
            anchor_value="Alex",
            base_score=0.6,
        )

        assert node.similarity is None
        assert node.rank_score == pytest.approx(0.6)

    def test_a_match_with_neither_scores_nothing(self):
        node = read(row(), found_by=RetrievalPass.STRUCTURAL)

        assert node.rank_score == 0.0

    def test_the_reason_that_led_there_is_carried(self):
        node = read(
            row(),
            found_by=RetrievalPass.STRUCTURAL,
            trigger_type=TriggerType.NAMED_PERSON,
            base_score=0.6,
        )

        assert node.trigger_type is TriggerType.NAMED_PERSON


class TestWhatCountsAsHistory:
    def test_a_live_record_of_a_content_kind_counts(self):
        assert is_live_content(row(status="ACTIVE")) is True

    def test_machinery_does_not(self):
        assert is_live_content(row(_label="DecisionAuditNode")) is False

    @pytest.mark.parametrize("status", sorted(RETIRED_STATUSES))
    def test_a_retired_record_does_not(self, status):
        assert is_live_content(row(status=status)) is False

    def test_a_record_with_no_status_counts(self):
        # An open question records how far it got rather than whether it is
        # live, so there is no plain status to check.
        assert is_live_content(row(_label="OpenLoopNode")) is True

    def test_a_piece_of_writing_is_not_offered_as_history(self):
        # An episode is where history came from, not a piece of it. Offering
        # one would put a whole entry in front of the AI instead of the
        # finding drawn out of it.
        assert is_live_content(row(_label="EpisodeNode")) is False

    def test_every_content_kind_is_something_the_graph_has(self):
        from lumen.graph.kuzu_impl import NODE_TABLES

        assert CONTENT_TABLES <= set(NODE_TABLES)


class TestWeight:
    def test_an_unmarked_record_is_ordinary(self):
        assert signal_of({}) is SignalStrength.STANDARD

    def test_a_nonsense_marking_is_read_as_ordinary(self):
        # Safe in one direction only: it can fail to promote something,
        # never promote something that did not earn it.
        assert signal_of({"signal_strength": "ENORMOUS"}) is SignalStrength.STANDARD

    def test_every_strength_has_a_weight(self):
        assert set(SIGNAL_WEIGHT) == set(SignalStrength)


class TestUsability:
    def test_a_row_with_an_identifier_is_usable(self):
        assert has_id({"node_id": "n_1"}) is True

    def test_a_row_without_one_is_not(self):
        assert has_id({"content": "orphaned"}) is False
