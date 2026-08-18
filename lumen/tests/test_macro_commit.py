"""
Tests for saving a finished report.

Two things are being defended. A report and the links to everything it read go
down together or not at all, because a report joined to half its evidence is
indistinguishable from one that genuinely only read half. And a report is
never added to the search index — it is *about* somebody's history, and
letting it come back as a search result would let the system quote its own
summary back to the person as though they had said it.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lumen.pipeline.macroextraction import commit
from lumen.schemas.enums import ReportStatus, ReportType
from lumen.schemas.nodes import MacroextractionReportNode

UTC = timezone.utc


def a_report(node_id: str = "macro_monthly_2026_05_01") -> MacroextractionReportNode:
    """One finished report, ready to be stored."""
    return MacroextractionReportNode(
        node_id=node_id,
        created_at=datetime(2026, 6, 4, tzinfo=UTC),
        report_type=ReportType.MONTHLY,
        period_start=datetime(2026, 5, 1, tzinfo=UTC),
        period_end=datetime(2026, 6, 1, tzinfo=UTC),
        episodes_analyzed=2,
        model_used="fake-thinker",
        status=ReportStatus.IMMUTABLE,
        report_content={"headline": "A month."},
    )


class TestStoringAReport:
    def test_the_report_is_saved_and_named(self, graph_store, seed_month):
        seed_month("ep_1", day=4)

        node_id = commit.write(a_report(), ("ep_1",), graph=graph_store)

        assert node_id == "macro_monthly_2026_05_01"
        assert graph_store.get_node(node_id) is not None

    def test_one_link_is_written_per_piece_of_writing(self, graph_store, seed_month):
        seed_month("ep_1", day=4)
        seed_month("ep_2", day=9)

        commit.write(a_report(), ("ep_1", "ep_2"), graph=graph_store)

        covering = graph_store.find_standing_edges(
            ["ep_1", "ep_2"], edge_names=["analyzed_in"]
        )
        assert len(covering) == 2

    def test_the_same_piece_of_writing_is_not_linked_twice(
        self, graph_store, seed_month
    ):
        seed_month("ep_1", day=4)

        commit.write(a_report(), ("ep_1", "ep_1"), graph=graph_store)

        covering = graph_store.find_standing_edges(["ep_1"], edge_names=["analyzed_in"])
        assert len(covering) == 1

    def test_a_report_that_read_nothing_still_stores(self, graph_store):
        node_id = commit.write(a_report(), (), graph=graph_store)

        assert graph_store.get_node(node_id) is not None

    def test_a_report_is_not_added_to_the_search_index(
        self, graph_store, vector_store, seed_month
    ):
        # Nothing here touches the index, and that is the guarantee: a report
        # can never come back as history inside a conversation, quoted as
        # though the person had said it.
        seed_month("ep_1", day=4)

        commit.write(a_report(), ("ep_1",), graph=graph_store)

        assert vector_store.get_vectors(["macro_monthly_2026_05_01"]) == {}


class TestWhenSavingGoesWrong:
    def test_a_failure_partway_through_leaves_no_links_behind(
        self, graph_store, seed_month
    ):
        # A second report under a name already taken cannot be stored. What
        # matters is that the links it was going to make are not left behind
        # either — half-written coverage would say this report read something
        # it never did.
        seed_month("ep_1", day=4)
        seed_month("ep_2", day=9)
        commit.write(a_report(), ("ep_1",), graph=graph_store)

        with pytest.raises(Exception):
            commit.write(a_report(), ("ep_2",), graph=graph_store)

        assert graph_store.find_standing_edges(["ep_2"], edge_names=["analyzed_in"]) == []


class TestCountingWhatIsAlreadyThere:
    def test_a_period_never_covered_counts_as_none(self, graph_store):
        assert (
            commit.count_existing(
                graph_store,
                report_type="MONTHLY",
                period_start=datetime(2026, 5, 1, tzinfo=UTC),
            )
            == 0
        )

    def test_a_covered_period_is_counted(self, graph_store):
        commit.write(a_report(), (), graph=graph_store)

        assert (
            commit.count_existing(
                graph_store,
                report_type="MONTHLY",
                period_start=datetime(2026, 5, 1, tzinfo=UTC),
            )
            == 1
        )

    def test_a_different_period_is_not_counted(self, graph_store):
        commit.write(a_report(), (), graph=graph_store)

        assert (
            commit.count_existing(
                graph_store,
                report_type="MONTHLY",
                period_start=datetime(2026, 4, 1, tzinfo=UTC),
            )
            == 0
        )

    def test_a_different_kind_of_report_is_not_counted(self, graph_store):
        commit.write(a_report(), (), graph=graph_store)

        assert (
            commit.count_existing(
                graph_store,
                report_type="WEEKLY",
                period_start=datetime(2026, 5, 1, tzinfo=UTC),
            )
            == 0
        )
