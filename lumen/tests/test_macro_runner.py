"""
Tests for running a report end to end, and for deciding what is owed.

Run against real embedded databases, because what is being checked is the
whole sequence — read the period, count it, ask for wording, save it — and a
stand-in for any one step would let the others pass while wired together
wrongly.

Three decisions live in the runner rather than in any of the steps, and each
has tests here: a period already covered costs nothing to ask for again, a
period with nothing in it leaves no document behind, and a model that cannot
be reached costs a report its prose rather than the whole period.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from lumen.config import AppConfig, MacroConfig
from lumen.pipeline.macroextraction import runner, windows
from lumen.providers.fake import FakeLLMProvider
from lumen.tests.conftest import registry_for
from lumen.schemas.enums import (
    MacroRunStatus,
    ModelRole,
    NarrativeStatus,
    ReportType,
)

# Whose reports these are. There is a graph per person now, so every call
# to the service says which one it is about.
USER = "local"

UTC = timezone.utc


def a_model(**wording) -> FakeLLMProvider:
    """A stand-in that answers one request for a report's wording."""
    wording.setdefault("headline", "A month of the same thing, mostly.")
    return FakeLLMProvider(
        [json.dumps(wording), json.dumps(wording), json.dumps(wording)],
        role=ModelRole.THINKING,
        model="fake-thinker",
    )


def may() -> object:
    """The window covering May 2026."""
    return windows.window_for(ReportType.MONTHLY, datetime(2026, 5, 15, tzinfo=UTC))


class TestRunningOnePeriod:
    def test_a_month_of_writing_produces_a_report_over_the_right_writing(
        self, graph_store, seed_month, seed_pattern
    ):
        # The acceptance case: a period is summarised, and the report is
        # joined to exactly the writing it drew on.
        seed_pattern("pat_a", name="Comparison with peers")
        for day in (4, 12, 20):
            seed_month(
                f"ep_{day}",
                day=day,
                observations=((f"obs_{day}", "PATTERN", "behind everyone again"),),
                patterns={f"obs_{day}": "pat_a"},
            )
        seed_month("ep_june", day=3, month=6)

        outcome = runner.run_report(may(), graph=graph_store, thinking=a_model())

        assert outcome.status is MacroRunStatus.WRITTEN
        assert outcome.episodes_analyzed == 3
        covering = graph_store.find_standing_edges(
            ["ep_4", "ep_12", "ep_20", "ep_june"], edge_names=["analyzed_in"]
        )
        assert sorted(edge.from_node_id for edge in covering) == [
            "ep_12",
            "ep_20",
            "ep_4",
        ]

    def test_the_counts_end_up_in_the_stored_document(
        self, graph_store, seed_month, seed_pattern
    ):
        seed_pattern("pat_a", name="Comparison with peers")
        for day in (4, 12):
            seed_month(
                f"ep_{day}",
                day=day,
                observations=((f"obs_{day}", "PATTERN", "x"),),
                patterns={f"obs_{day}": "pat_a"},
            )

        outcome = runner.run_report(may(), graph=graph_store, thinking=a_model())

        stored = json.loads(graph_store.get_node(outcome.report_id)["report_content"])
        assert stored["top_patterns"][0]["pattern_id"] == "pat_a"
        assert stored["top_patterns"][0]["episode_count"] == 2
        assert stored["headline"] == "A month of the same thing, mostly."

    def test_a_period_with_nothing_in_it_leaves_no_document(self, graph_store):
        # A month somebody did not write in should not leave behind a
        # document saying so.
        outcome = runner.run_report(may(), graph=graph_store, thinking=a_model())

        assert outcome.status is MacroRunStatus.EMPTY_WINDOW
        assert outcome.report_id is None
        assert graph_store.find_reports() == []

    def test_how_long_it_took_is_reported(self, graph_store, seed_month):
        seed_month("ep_1", day=4)

        outcome = runner.run_report(may(), graph=graph_store, thinking=a_model())

        assert outcome.duration_ms >= 0


class TestAskingForTheSamePeriodTwice:
    def test_the_second_ask_costs_nothing_and_returns_the_first(
        self, graph_store, seed_month
    ):
        # This is what makes a schedule safe to fire more than once.
        seed_month("ep_1", day=4)
        first = runner.run_report(may(), graph=graph_store, thinking=a_model())

        model = a_model()
        second = runner.run_report(may(), graph=graph_store, thinking=model)

        assert second.status is MacroRunStatus.SKIPPED_EXISTING
        assert second.report_id == first.report_id
        assert model.calls == []

    def test_a_deliberate_rerun_writes_a_second_report(
        self, graph_store, seed_month
    ):
        seed_month("ep_1", day=4)
        first = runner.run_report(may(), graph=graph_store, thinking=a_model())

        second = runner.run_report(
            may(), graph=graph_store, thinking=a_model(), force=True
        )

        assert second.status is MacroRunStatus.WRITTEN
        assert second.report_id != first.report_id
        # Nothing is overwritten, so both survive.
        assert graph_store.get_node(first.report_id) is not None
        assert len(graph_store.find_reports(report_type="MONTHLY")) == 2

    def test_the_newer_of_two_reports_is_the_one_readers_are_shown(
        self, graph_store, seed_month
    ):
        seed_month("ep_1", day=4)
        runner.run_report(may(), graph=graph_store, thinking=a_model())
        second = runner.run_report(
            may(), graph=graph_store, thinking=a_model(), force=True
        )

        newest = graph_store.find_reports(report_type="MONTHLY", limit=1)

        assert str(newest[0]["node_id"]) == second.report_id


class TestWhenThereIsNoModel:
    def test_a_report_is_still_written_with_its_counts(
        self, graph_store, seed_month, seed_pattern
    ):
        # Every figure is arrived at without a model, and the counts are the
        # part that cannot be reconstructed later.
        seed_pattern("pat_a")
        seed_month(
            "ep_1",
            day=4,
            observations=(("obs_1", "PATTERN", "x"),),
            patterns={"obs_1": "pat_a"},
        )

        outcome = runner.run_report(may(), graph=graph_store, thinking=None)

        assert outcome.status is MacroRunStatus.WRITTEN
        assert outcome.narrative_status is NarrativeStatus.UNAVAILABLE
        stored = json.loads(graph_store.get_node(outcome.report_id)["report_content"])
        assert stored["top_patterns"][0]["pattern_id"] == "pat_a"
        assert stored["headline"] == ""


class TestTheTwoDayScan:
    def test_a_burst_produces_an_alert(self, graph_store):
        now = datetime(2026, 5, 20, 12, tzinfo=UTC)
        for index in range(3):
            graph_store.write_node(
                "DecisionAuditNode",
                {
                    "node_id": f"d_{index}",
                    "created_at": (now.replace(hour=9)).isoformat(),
                    "action": "BRANCH",
                    "source_node_id": f"obs_{index}",
                    "target_node_id": f"pat_{index}",
                    "confidence": 0.9,
                    "model_used": "fake",
                    "model_role": "THINKING",
                    "status": "ACTIVE",
                    "hitl_resolved": False,
                },
            )

        outcome = runner.run_shadow(now, graph=graph_store, lightweight=None)

        assert outcome.status is MacroRunStatus.WRITTEN
        stored = json.loads(graph_store.get_node(outcome.report_id)["report_content"])
        assert stored["shadow_micro_shift"]["detected"] is True

    def test_a_quiet_two_days_leaves_nothing_behind(self, graph_store):
        outcome = runner.run_shadow(
            datetime(2026, 5, 20, 12, tzinfo=UTC), graph=graph_store, lightweight=None
        )

        assert outcome.status is MacroRunStatus.NOT_DETECTED
        assert graph_store.find_reports() == []


class TestWhatIsOwed:
    def test_periods_never_covered_are_owed(self, graph_store):
        due = runner.due_now(datetime(2026, 7, 4, tzinfo=UTC), graph=graph_store)

        assert due
        assert due == sorted(due, key=lambda window: window.period_start)

    def test_a_period_already_covered_is_not_owed_again(
        self, graph_store, seed_month
    ):
        seed_month("ep_1", day=4, month=6)
        june = windows.window_for(ReportType.MONTHLY, datetime(2026, 6, 15, tzinfo=UTC))
        runner.run_report(june, graph=graph_store, thinking=a_model())

        due = runner.due_now(datetime(2026, 7, 4, tzinfo=UTC), graph=graph_store)

        assert june.key not in {window.key for window in due}


class TestCatchingUp:
    def test_everything_owed_is_run_oldest_first(self, graph_store, seed_month):
        for month in (4, 5, 6):
            seed_month(f"ep_{month}", day=10, month=month)

        outcomes = runner.run_due(
            datetime(2026, 7, 4, tzinfo=UTC),
            graph=graph_store,
            thinking=a_model(),
            config=AppConfig(macro=MacroConfig(max_runs_per_invocation=3)),
        )

        written = [item for item in outcomes if item.wrote_something]
        assert written
        starts = [item.window.period_start for item in written]
        assert starts == sorted(starts)

    def test_the_two_day_scan_runs_alongside_the_periodic_ones(
        self, graph_store, seed_month
    ):
        seed_month("ep_1", day=10, month=6)

        outcomes = runner.run_due(
            datetime(2026, 7, 4, tzinfo=UTC), graph=graph_store, thinking=a_model()
        )

        assert any(
            outcome.window.report_type is ReportType.SHADOW for outcome in outcomes
        )

    def test_a_deployment_that_wants_none_of_this_gets_none_of_it(
        self, graph_store, seed_month
    ):
        seed_month("ep_1", day=10, month=6)

        outcomes = runner.run_due(
            datetime(2026, 7, 4, tzinfo=UTC),
            graph=graph_store,
            thinking=a_model(),
            config=AppConfig(macro=MacroConfig(enabled=False)),
        )

        assert outcomes == []
        assert graph_store.find_reports() == []

    def test_nothing_owed_produces_no_reports(self, graph_store):
        outcomes = runner.run_due(
            datetime(2026, 7, 4, tzinfo=UTC), graph=graph_store, thinking=a_model()
        )

        assert all(not outcome.wrote_something for outcome in outcomes)


class TestTheServiceThatRoutesUse:
    def test_it_builds_a_report_without_exposing_the_graph(
        self, graph_store, ops_store, seed_month
    ):
        from lumen.pipeline.macroextraction.service import MacroextractionService

        seed_month("ep_1", day=4)
        service = MacroextractionService(
            config=AppConfig(), stores=registry_for(graph_store), ops=ops_store
        )
        service._models = {ModelRole.THINKING: a_model()}

        outcome = service.run(USER, may())

        assert outcome.status is MacroRunStatus.WRITTEN
        # Nothing on the surface hands back a store to write through.
        assert not hasattr(service, "graph")

    def test_a_missing_model_is_remembered_rather_than_retried(
        self, graph_store, monkeypatch
    ):
        from lumen.pipeline.macroextraction import service as service_module
        from lumen.pipeline.macroextraction.service import MacroextractionService
        from lumen.providers.errors import ProviderError

        attempts = []

        def refuse(role, config):
            attempts.append(role)
            raise ProviderError("no credential")

        monkeypatch.setattr(service_module, "get_llm_provider", refuse)
        service = MacroextractionService(
            config=AppConfig(), stores=registry_for(graph_store)
        )

        service.run(USER, may())
        service.run(USER, may())

        assert attempts.count(ModelRole.THINKING) == 1


class TestTheServiceCatchingUp:
    def test_it_runs_everything_owed_and_the_two_day_scan(
        self, graph_store, ops_store, seed_month
    ):
        from lumen.pipeline.macroextraction.service import MacroextractionService

        seed_month("ep_1", day=10, month=6)
        service = MacroextractionService(
            config=AppConfig(), stores=registry_for(graph_store), ops=ops_store
        )
        service._models = {
            ModelRole.THINKING: a_model(),
            ModelRole.LIGHTWEIGHT: None,
        }

        outcomes = service.run_due(USER, datetime(2026, 7, 4, tzinfo=UTC))

        assert any(outcome.wrote_something for outcome in outcomes)
        assert any(
            outcome.window.report_type is ReportType.SHADOW for outcome in outcomes
        )

    def test_it_can_be_asked_what_is_owed_without_building_anything(
        self, graph_store
    ):
        from lumen.pipeline.macroextraction.service import MacroextractionService

        service = MacroextractionService(
            config=AppConfig(), stores=registry_for(graph_store)
        )

        due = service.due(USER, datetime(2026, 7, 4, tzinfo=UTC))

        assert due
        assert graph_store.find_reports() == []

    def test_it_can_be_asked_for_the_two_day_scan_on_its_own(self, graph_store):
        from lumen.pipeline.macroextraction.service import MacroextractionService

        service = MacroextractionService(
            config=AppConfig(), stores=registry_for(graph_store)
        )
        service._models = {ModelRole.LIGHTWEIGHT: None}

        outcome = service.run_shadow(USER, datetime(2026, 5, 20, 12, tzinfo=UTC))

        assert outcome.status is MacroRunStatus.NOT_DETECTED
