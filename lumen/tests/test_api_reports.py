"""
Tests for the routes that read periodic reports and ask for another.

The reads are ordinary and are checked for shape and for refusing plainly. The
last route is the interesting one: it is only the second thing in the whole web
surface that can change the graph, and what it holds is not a graph handle but
the thing that builds reports. The tests here check both that it works and that
every non-writing outcome comes back as an ordinary answer — "already covered"
and "nothing to cover" are the two most likely results of pressing the button,
and neither is a failure.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from lumen.schemas.enums import ReportStatus, ReportType
from lumen.schemas.nodes import MacroextractionReportNode

UTC = timezone.utc


def store_report(
    graph,
    node_id: str = "macro_monthly_2026_05_01",
    *,
    report_type: ReportType = ReportType.MONTHLY,
    month: int = 5,
    content: dict | None = None,
    episode_ids: tuple[str, ...] = (),
) -> str:
    """Put one finished report into the graph, joined to its writing."""
    from lumen.pipeline.macroextraction import commit

    report = MacroextractionReportNode(
        node_id=node_id,
        created_at=datetime(2026, month + 1, 4, tzinfo=UTC),
        report_type=report_type,
        period_start=datetime(2026, month, 1, tzinfo=UTC),
        period_end=datetime(2026, month + 1, 1, tzinfo=UTC),
        episodes_analyzed=len(episode_ids),
        model_used="fake-thinker",
        status=ReportStatus.IMMUTABLE,
        report_content=content
        or {
            "headline": "A month of the same thing.",
            "meta": {"narrative_status": "OK"},
        },
    )
    return commit.write(report, episode_ids, graph=graph)


class TestListingReports:
    def test_nothing_written_lists_nothing(self, api_client):
        page = api_client.get("/reports").json()

        assert page["reports"] == []
        assert page["count"] == 0

    def test_a_report_appears_with_its_period_and_headline(
        self, api_client, graph_store
    ):
        store_report(graph_store)

        page = api_client.get("/reports").json()

        entry = page["reports"][0]
        assert entry["report_id"] == "macro_monthly_2026_05_01"
        assert entry["report_type"] == "MONTHLY"
        assert entry["headline"] == "A month of the same thing."
        assert entry["narrative_status"] == "OK"

    def test_the_whole_document_is_not_sent_in_a_listing(
        self, api_client, graph_store
    ):
        # Answering "which reports exist" with twenty full documents would
        # send megabytes to answer a question about names and dates.
        store_report(graph_store)

        entry = api_client.get("/reports").json()["reports"][0]

        assert "content" not in entry

    def test_a_listing_can_be_narrowed_to_one_kind(self, api_client, graph_store):
        store_report(graph_store, "macro_monthly_2026_05_01")
        store_report(
            graph_store,
            "macro_weekly_2026_05_04",
            report_type=ReportType.WEEKLY,
        )

        page = api_client.get("/reports?type=WEEKLY").json()

        assert [item["report_id"] for item in page["reports"]] == [
            "macro_weekly_2026_05_04"
        ]

    def test_a_kind_that_does_not_exist_is_refused_plainly(self, api_client):
        response = api_client.get("/reports?type=FORTNIGHTLY")

        assert response.status_code == 400
        assert "no such kind" in response.json()["detail"]

    def test_a_listing_can_be_paged(self, api_client, graph_store):
        store_report(graph_store, "macro_monthly_2026_04_01", month=4)
        store_report(graph_store, "macro_monthly_2026_05_01", month=5)

        page = api_client.get("/reports?limit=1&offset=1").json()

        assert page["count"] == 1
        assert page["offset"] == 1

    def test_an_unreasonable_page_size_is_refused(self, api_client):
        assert api_client.get("/reports?limit=5000").status_code == 422


class TestReadingOneReport:
    def test_the_whole_document_comes_back(self, api_client, graph_store):
        store_report(graph_store)

        detail = api_client.get("/reports/macro_monthly_2026_05_01").json()

        assert detail["report"]["report_id"] == "macro_monthly_2026_05_01"
        assert detail["content"]["headline"] == "A month of the same thing."

    def test_the_writing_it_read_is_named(self, api_client, graph_store, seed_month):
        # What makes a report checkable. Every figure came from these and
        # nothing else, so a reader who doubts a claim has somewhere to look.
        seed_month("ep_1", day=4)
        seed_month("ep_2", day=9)
        store_report(graph_store, episode_ids=("ep_1", "ep_2"))

        detail = api_client.get("/reports/macro_monthly_2026_05_01").json()

        assert detail["episode_ids"] == ["ep_1", "ep_2"]

    def test_an_unknown_report_is_a_plain_not_found(self, api_client):
        assert api_client.get("/reports/macro_nonsense").status_code == 404

    def test_asking_for_something_that_is_not_a_report_is_not_found(
        self, api_client, graph_store, seed_month
    ):
        seed_month("ep_1", day=4)

        assert api_client.get("/reports/ep_1").status_code == 404

    def test_an_unreadable_document_still_answers_with_its_period(
        self, api_client, graph_store
    ):
        # The envelope still says which period it covered, which is worth
        # more than an error.
        graph_store.write_node(
            "MacroextractionReportNode",
            {
                "node_id": "macro_broken",
                "created_at": "2026-06-04T00:00:00+00:00",
                "report_type": "MONTHLY",
                "period_start": "2026-05-01T00:00:00+00:00",
                "period_end": "2026-06-01T00:00:00+00:00",
                "episodes_analyzed": 2,
                "archetype_shift_detected": False,
                "model_used": "fake",
                "status": "IMMUTABLE",
                "report_content": "this is not json",
            },
        )

        detail = api_client.get("/reports/macro_broken").json()

        assert detail["content"] == {}
        assert detail["report"]["episodes_analyzed"] == 2


class TestWhatIsOverdue:
    def test_the_periods_a_schedule_would_run_are_listed(self, api_client):
        due = api_client.get("/reports/due").json()

        assert due
        assert {"report_type", "period_start", "period_end"} <= set(due[0])

    def test_nothing_is_actually_built_by_asking(self, api_client, graph_store):
        # The decision is worth being able to look at before anything is
        # spent acting on it.
        api_client.get("/reports/due")

        assert graph_store.find_reports() == []


class TestBuildingOneByHand:
    def test_a_period_with_writing_in_it_is_summarised(
        self, api_client, graph_store, seed_month
    ):
        seed_month("ep_1", day=4)

        outcome = api_client.post(
            "/reports/run",
            json={"report_type": "MONTHLY", "period_start": "2026-05-15"},
        ).json()

        assert outcome["status"] == "WRITTEN"
        assert outcome["episodes_analyzed"] == 1
        assert graph_store.get_node(outcome["report_id"]) is not None

    def test_any_day_inside_a_period_names_that_period(
        self, api_client, graph_store, seed_month
    ):
        # The boundaries are worked out from the calendar rather than taken
        # as given, so nobody can ask for a five-week month.
        seed_month("ep_1", day=4)

        outcome = api_client.post(
            "/reports/run",
            json={"report_type": "MONTHLY", "period_start": "2026-05-27"},
        ).json()

        assert outcome["period_start"].startswith("2026-05-01")
        assert outcome["period_end"].startswith("2026-06-01")

    def test_naming_no_day_uses_the_period_that_has_just_closed(self, api_client):
        # The period still running would produce a report that goes stale by
        # tomorrow.
        outcome = api_client.post("/reports/run", json={"report_type": "MONTHLY"}).json()

        assert outcome["period_end"] <= datetime.now(UTC).isoformat()

    def test_a_period_with_nothing_in_it_is_an_ordinary_answer(self, api_client):
        outcome = api_client.post(
            "/reports/run",
            json={"report_type": "MONTHLY", "period_start": "2026-05-15"},
        ).json()

        assert outcome["status"] == "EMPTY_WINDOW"
        assert outcome["report_id"] is None

    def test_pressing_it_twice_is_safe(self, api_client, graph_store, seed_month):
        seed_month("ep_1", day=4)
        body = {"report_type": "MONTHLY", "period_start": "2026-05-15"}
        first = api_client.post("/reports/run", json=body).json()

        second = api_client.post("/reports/run", json=body).json()

        assert second["status"] == "SKIPPED_EXISTING"
        assert second["report_id"] == first["report_id"]

    def test_a_rebuild_can_be_asked_for_explicitly(
        self, api_client, graph_store, seed_month
    ):
        seed_month("ep_1", day=4)
        body = {"report_type": "MONTHLY", "period_start": "2026-05-15"}
        first = api_client.post("/reports/run", json=body).json()

        second = api_client.post("/reports/run", json={**body, "force": True}).json()

        assert second["status"] == "WRITTEN"
        assert second["report_id"] != first["report_id"]

    def test_the_two_day_scan_can_be_asked_for(self, api_client):
        outcome = api_client.post("/reports/run", json={"report_type": "SHADOW"}).json()

        assert outcome["report_type"] == "SHADOW"
        assert outcome["status"] == "NOT_DETECTED"

    def test_a_kind_that_does_not_exist_is_refused(self, api_client):
        response = api_client.post("/reports/run", json={"report_type": "DAILY"})

        assert response.status_code == 422

    def test_a_report_built_without_a_model_says_its_wording_is_missing(
        self, api_client, seed_month
    ):
        seed_month("ep_1", day=4)

        outcome = api_client.post(
            "/reports/run",
            json={"report_type": "MONTHLY", "period_start": "2026-05-15"},
        ).json()

        assert outcome["narrative_status"] == "UNAVAILABLE"

    def test_what_was_built_is_immediately_readable(
        self, api_client, seed_month
    ):
        seed_month("ep_1", day=4)
        outcome = api_client.post(
            "/reports/run",
            json={"report_type": "MONTHLY", "period_start": "2026-05-15"},
        ).json()

        detail = api_client.get(f"/reports/{outcome['report_id']}").json()

        assert detail["episode_ids"] == ["ep_1"]


class TestWhatTheReadRoutesCannotDo:
    def test_the_read_routes_ask_for_something_that_cannot_write(self):
        # A write is not merely discouraged here. The read routes ask for a
        # reader, so a write would fail before it ran — the method is not on
        # the type they were handed.
        import typing

        from lumen.api.routes import reports

        for route in (reports.list_reports, reports.get_report):
            hints = typing.get_type_hints(route)
            assert hints["store"].__name__ == "ReadOnlyGraph"
            assert not hasattr(hints["store"], "write_node")

    def test_only_the_building_route_holds_anything_that_can_write(self):
        # And what it holds is not a graph. If this file ever reaches for one
        # directly, the separation the arrangement depends on has gone.
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1] / "api" / "routes" / "reports.py"
        ).read_text()

        assert "GraphProvider" not in source
        assert "write_node" not in source
