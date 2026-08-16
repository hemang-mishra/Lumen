"""
Tests for composing the graph's questions and tidying its answers.

No database anywhere. Every one of these is a string or a dict going in and
another coming out, which is the point of keeping this apart from the store:
the fiddly half is checkable without any infrastructure at all.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from lumen.graph import queries

MARCH = datetime(2026, 3, 1, tzinfo=UTC)
JUNE = datetime(2026, 6, 30, tzinfo=UTC)


class TestComposingFilters:
    def test_no_filters_and_everything_allowed_asks_nothing(self):
        built = queries.build_filters("ObservationNode", active_only=False)

        assert built.clause == ""
        assert built.where() == ""

    def test_a_date_range_becomes_two_comparisons(self):
        built = queries.build_filters(
            "BeliefNode", since=MARCH, until=JUNE, active_only=False
        )

        assert "n.valid_from >= $f_since" in built.clause
        assert "n.valid_from <= $f_until" in built.clause
        assert built.params["f_since"] == MARCH.isoformat()

    def test_dates_are_compared_as_the_text_they_are_stored_in(self):
        # They sort correctly as text, which is the only reason comparing
        # them this way works.
        built = queries.build_filters("BeliefNode", since=date(2026, 3, 1))

        assert built.params["f_since"] == "2026-03-01"

    def test_a_part_of_life_is_asked_of_the_records_that_record_one(self):
        built = queries.build_filters("BeliefNode", domain="CAREER", active_only=False)

        assert "n.domain = $f_domain" in built.clause
        assert built.params["f_domain"] == "CAREER"

    def test_a_filter_a_record_cannot_answer_is_dropped_not_refused(self, caplog):
        # An observation does not record a part of life, so there is no such
        # thing as an observation about work. Failing the whole request would
        # mean a caller had to know every table's shape to ask one question.
        built = queries.build_filters(
            "ObservationNode", domain="CAREER", active_only=False
        )

        assert "domain" not in built.clause
        assert built.params == {}

    def test_the_same_idea_under_two_names_is_found_under_both(self):
        # A pattern calls it era_tag and an episode historical_era.
        pattern = queries.build_filters("PatternNode", era_tag="exam_prep", active_only=False)
        episode = queries.build_filters("EpisodeNode", era_tag="exam_prep", active_only=False)

        assert "n.era_tag = $f_era" in pattern.clause
        assert "n.historical_era = $f_era" in episode.clause

    def test_a_record_with_no_date_at_all_skips_the_date_range(self):
        # A step in a causal sequence is not independently dated; it belongs
        # to whenever its chain was.
        built = queries.build_filters(
            "CausalStepNode", since=MARCH, until=JUNE, active_only=False
        )

        assert built.clause == ""

    def test_live_records_only_is_the_default(self):
        built = queries.build_filters("PatternNode")

        assert "n.status = 'ACTIVE'" in built.clause

    def test_an_episode_is_always_current(self):
        # It is a piece of writing, and writing does not stop having
        # happened, so it carries no status at all.
        built = queries.build_filters("EpisodeNode")

        assert built.clause == "true"

    def test_conditions_are_joined_rather_than_replacing_each_other(self):
        built = queries.build_filters(
            "BeliefNode", since=MARCH, domain="CAREER", signal_strength="HIGH"
        )

        assert built.clause.count(" AND ") == 3

    def test_the_alias_can_be_changed_for_a_query_with_two_records_in_it(self):
        built = queries.build_filters("BeliefNode", domain="CAREER", alias="b")

        assert "b.domain" in built.clause
        assert "b.status" in built.clause

    def test_extra_conditions_can_be_added_to_a_built_set(self):
        built = queries.build_filters("BeliefNode", active_only=False, domain="CAREER")

        assert built.and_with("n.version = 2") == "n.domain = $f_domain AND n.version = 2"

    def test_an_extra_condition_stands_alone_when_nothing_else_was_asked(self):
        built = queries.build_filters("BeliefNode", active_only=False)

        assert built.and_with("n.version = 2") == "n.version = 2"

    def test_every_table_is_covered(self):
        # A table missing from the list would silently answer no filters at
        # all, which reads as "nothing matched" rather than as a mistake.
        from lumen.graph.kuzu_impl import NODE_TABLES

        assert set(queries.FILTER_COLUMNS) == set(NODE_TABLES)

    def test_every_named_column_actually_exists(self):
        # Naming a column a table does not have is not a filter that
        # quietly matches nothing — it is an error from the database, so an
        # ordinary listing request would crash rather than come back empty.
        from lumen.graph.kuzu_impl import NODE_TABLES

        wrong = [
            f"{table}.{column}"
            for table, columns in queries.FILTER_COLUMNS.items()
            for column in columns.values()
            if f"{column} " not in NODE_TABLES[table]
        ]

        assert wrong == []

    def test_a_record_with_no_plain_status_is_always_considered_live(self):
        # An open question records how far it got rather than whether it is
        # live, so there is nothing to narrow on.
        assert queries.active_clause("OpenLoopNode") == "true"
        assert queries.active_clause("ContradictionNode") == "true"


class TestTimeTravel:
    def test_no_date_means_no_condition(self):
        assert queries.as_of_clause(None) is None

    def test_a_date_asks_which_records_already_existed(self):
        assert queries.as_of_clause(MARCH) == "n.valid_from <= $as_of"

    def test_withdrawn_links_are_left_out_by_default(self):
        # A rolled-back decision should not still be shaping what the graph
        # appears to say.
        assert queries.edge_liveness_clause() == "r.invalidated_at IS NULL"

    def test_they_can_be_asked_for_when_something_has_gone_wrong(self):
        assert queries.edge_liveness_clause(include_invalidated=True) == "true"

    def test_a_link_withdrawn_later_was_still_live_on_the_date_asked_about(self):
        clause = queries.edge_liveness_clause(as_of=MARCH)

        assert "invalidated_at > $as_of" in clause

    def test_asking_for_everything_wins_over_asking_about_a_date(self):
        assert (
            queries.edge_liveness_clause(include_invalidated=True, as_of=MARCH) == "true"
        )


class TestTidyingAnswers:
    def test_empty_columns_are_dropped(self):
        # Every kind of record is stored in one wide shape, so most of any
        # row is columns belonging to other kinds of record.
        row = {"node_id": "obs_1", "content": "something", "pattern_name": None, "domain": ""}

        assert queries.tidy_row(row) == {"node_id": "obs_1", "content": "something"}

    def test_lists_come_back_as_lists(self):
        row = {"node_id": "obs_1", "raw_evidence": '["I said this", "and this"]'}

        assert queries.tidy_row(row)["raw_evidence"] == ["I said this", "and this"]

    def test_a_note_that_merely_looks_like_a_list_is_left_alone(self):
        # Only the columns known to hold a list are parsed. Trying it
        # everywhere would turn a sentence starting with a bracket into
        # something else entirely.
        row = {"node_id": "obs_1", "content": '["not", "a", "list"]'}

        assert queries.tidy_row(row)["content"] == '["not", "a", "list"]'

    def test_a_list_column_holding_nonsense_is_kept_as_it_is(self, caplog):
        row = {"node_id": "obs_1", "raw_evidence": "not json"}

        assert queries.tidy_row(row)["raw_evidence"] == "not json"

    def test_the_stores_private_columns_do_not_leak(self):
        row = {"node_id": "obs_1", "_label": "ObservationNode", "_id": {"offset": 3}}

        assert queries.tidy_row(row) == {"node_id": "obs_1"}

    def test_the_kind_of_record_is_read_from_the_private_column(self):
        assert queries.node_type_of({"_label": "BeliefNode"}) == "BeliefNode"

    def test_a_row_with_no_kind_says_so_rather_than_guessing(self):
        assert queries.node_type_of({"node_id": "x"}) == "Unknown"

    def test_a_link_carries_its_two_ends_and_its_table(self):
        # Links have no identifier of their own, so that triple is the only
        # way to name a particular one — and it is what a rollback is told.
        tidied = queries.tidy_edge(
            "evolved_from_bel",
            "bel_v2",
            "bel_v1",
            {"valid_from": "2026-03-01", "decision_id": "d_1", "confidence": None},
        )

        assert tidied["edge_type"] == "evolved_from_bel"
        assert tidied["from_node_id"] == "bel_v2"
        assert tidied["to_node_id"] == "bel_v1"
        assert tidied["decision_id"] == "d_1"
        assert "confidence" not in tidied


class TestComparingEraNames:
    """
    How two spellings of one period of somebody's past are recognised as one.

    Nothing constrains how these get written, so the same era arrives as
    "HIGH_SCHOOL", "high school" and "High-School" depending on who wrote it.
    """

    @pytest.mark.parametrize(
        "written",
        ["HIGH_SCHOOL", "high school", "High-School", "  high   school  ", "High School"],
    )
    def test_the_same_period_written_any_way_compares_equal(self, written):
        assert queries.era_key(written) == "high_school"

    def test_genuinely_different_periods_stay_different(self):
        assert queries.era_key("high school") != queries.era_key("high school years")

    def test_a_name_with_nothing_in_it_compares_to_nothing(self):
        assert queries.era_key("  -- ") == ""

    def test_digits_are_part_of_a_name(self):
        assert queries.era_key("class of 2019") == "class_of_2019"
