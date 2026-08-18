"""
Tests for the two-day scan that notices a shift while it is happening.

Two conditions have to hold together, and the second does the real work:
enough decisions, and enough separate things affected. One belief turned over
repeatedly across a hard evening is a person working something through, and a
scan counting decisions alone would call that a shift.

The other thing guarded here is silence. Finding nothing is the ordinary
outcome and must leave nothing behind — a daily note saying nothing shifted
would bury the days when something did.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lumen.config import MacroConfig
from lumen.pipeline.macroextraction import shadow, windows

UTC = timezone.utc
NOW = datetime(2026, 5, 20, 12, tzinfo=UTC)


def decision(graph, node_id: str, *, action: str, target: str, at=None, status="ACTIVE"):
    """Put one note of a decision into the graph."""
    when = (at or NOW - timedelta(hours=3)).isoformat()
    graph.write_node(
        "DecisionAuditNode",
        {
            "node_id": node_id,
            "created_at": when,
            "action": action,
            "source_node_id": f"obs_for_{node_id}",
            "target_node_id": target,
            "confidence": 0.9,
            "model_used": "fake",
            "model_role": "THINKING",
            "status": status,
            "hitl_resolved": False,
        },
    )
    return node_id


def window(config: MacroConfig | None = None):
    """The stretch the scan looks back over."""
    return windows.shadow_window(NOW, config=config or MacroConfig())


class TestWhenABurstIsCalled:
    def test_three_decisions_across_three_things_is_a_burst(self, graph_store):
        for index in range(3):
            decision(graph_store, f"d_{index}", action="BRANCH", target=f"pat_{index}")

        finding, decisions = shadow.scan(
            window(), graph=graph_store, config=MacroConfig()
        )

        assert finding.detected is True
        assert finding.branch_count == 3
        assert finding.target_count == 3
        assert len(decisions) == 3

    def test_two_decisions_is_below_the_bar(self, graph_store):
        for index in range(2):
            decision(graph_store, f"d_{index}", action="BRANCH", target=f"pat_{index}")

        finding, _ = shadow.scan(window(), graph=graph_store, config=MacroConfig())

        assert finding.detected is False

    def test_one_thing_worked_over_repeatedly_is_not_a_burst(self, graph_store):
        # Somebody turning the same belief over across a hard evening. Enough
        # decisions, but only one thing moved.
        for index in range(4):
            decision(graph_store, f"d_{index}", action="CONTRADICT", target="bel_same")

        finding, _ = shadow.scan(window(), graph=graph_store, config=MacroConfig())

        assert finding.detected is False
        assert finding.target_count == 1

    def test_tensions_and_new_directions_are_counted_separately(self, graph_store):
        decision(graph_store, "d_1", action="BRANCH", target="pat_1")
        decision(graph_store, "d_2", action="CONTRADICT", target="bel_1")
        decision(graph_store, "d_3", action="CONTRADICT", target="bel_2")

        finding, _ = shadow.scan(window(), graph=graph_store, config=MacroConfig())

        assert finding.branch_count == 1
        assert finding.contradict_count == 2

    def test_the_thresholds_are_configurable(self, graph_store):
        decision(graph_store, "d_1", action="BRANCH", target="pat_1")

        finding, _ = shadow.scan(
            window(),
            graph=graph_store,
            config=MacroConfig(shadow_min_decisions=1, shadow_min_targets=1),
        )

        assert finding.detected is True


class TestWhichDecisionsCount:
    def test_confirming_what_was_already_there_is_not_movement(self, graph_store):
        for index in range(4):
            decision(
                graph_store, f"d_{index}", action="REINFORCE", target=f"pat_{index}"
            )

        finding, decisions = shadow.scan(
            window(), graph=graph_store, config=MacroConfig()
        )

        assert finding.detected is False
        assert decisions == []

    def test_a_decision_still_waiting_on_the_person_has_changed_nothing(
        self, graph_store
    ):
        for index in range(3):
            decision(
                graph_store,
                f"d_{index}",
                action="BRANCH",
                target=f"pat_{index}",
                status="PENDING_HITL",
            )

        finding, _ = shadow.scan(window(), graph=graph_store, config=MacroConfig())

        assert finding.detected is False

    def test_a_rolled_back_decision_is_ignored(self, graph_store):
        decision(graph_store, "d_1", action="BRANCH", target="pat_1")
        decision(graph_store, "d_2", action="BRANCH", target="pat_2")
        decision(
            graph_store, "d_3", action="BRANCH", target="pat_3", status="ROLLED_BACK"
        )

        finding, _ = shadow.scan(window(), graph=graph_store, config=MacroConfig())

        assert finding.detected is False

    def test_a_decision_from_last_week_is_outside_the_window(self, graph_store):
        for index in range(3):
            decision(
                graph_store,
                f"d_{index}",
                action="BRANCH",
                target=f"pat_{index}",
                at=NOW - timedelta(days=6),
            )

        finding, _ = shadow.scan(window(), graph=graph_store, config=MacroConfig())

        assert finding.detected is False

    def test_a_quiet_two_days_finds_nothing(self, graph_store):
        finding, decisions = shadow.scan(
            window(), graph=graph_store, config=MacroConfig()
        )

        assert finding.detected is False
        assert finding.trigger_nodes == ()
        assert decisions == []


class TestWhatABurstPointsAt:
    def test_the_writing_behind_the_decisions_is_found(self, graph_store, seed_month):
        # A report records what it looked at, and for this kind that is
        # reached through the finding each decision was made about.
        seed_month(
            "ep_1", day=19, observations=(("obs_for_d_0", "EMOTION", "something moved"),)
        )
        for index in range(3):
            decision(graph_store, f"d_{index}", action="BRANCH", target=f"pat_{index}")

        finding, _ = shadow.scan(window(), graph=graph_store, config=MacroConfig())

        assert finding.episode_ids == ("ep_1",)

    def test_decisions_whose_findings_are_gone_point_at_nothing(self, graph_store):
        for index in range(3):
            decision(graph_store, f"d_{index}", action="BRANCH", target=f"pat_{index}")

        finding, _ = shadow.scan(window(), graph=graph_store, config=MacroConfig())

        assert finding.episode_ids == ()

    def test_the_decisions_themselves_are_named(self, graph_store):
        for index in range(3):
            decision(graph_store, f"d_{index}", action="BRANCH", target=f"pat_{index}")

        finding, _ = shadow.scan(window(), graph=graph_store, config=MacroConfig())

        assert finding.trigger_nodes == ("d_0", "d_1", "d_2")


class TestWhenTheScanLastRan:
    def test_nothing_written_means_it_has_never_run(self, graph_store):
        assert shadow.last_scan_at(graph_store) is None

    def test_the_last_alert_is_when_it_last_found_something(self, graph_store):
        graph_store.write_node(
            "MacroextractionReportNode",
            {
                "node_id": "macro_shadow_1",
                "created_at": NOW.isoformat(),
                "report_type": "SHADOW",
                "period_start": (NOW - timedelta(hours=48)).isoformat(),
                "period_end": NOW.isoformat(),
                "episodes_analyzed": 1,
                "archetype_shift_detected": False,
                "model_used": "fake",
                "status": "IMMUTABLE",
                "report_content": "{}",
            },
        )

        assert shadow.last_scan_at(graph_store) is not None


class TestWhenTheLastAlertCannotBeRead:
    def test_an_unreadable_timestamp_reads_as_never_having_run(self, graph_store):
        # Answering "never" makes the scan run again, which is the safe way
        # to be wrong about this.
        graph_store.write_node(
            "MacroextractionReportNode",
            {
                "node_id": "macro_shadow_broken",
                "created_at": "whenever",
                "report_type": "SHADOW",
                "period_start": "2026-05-18T12:00:00+00:00",
                "period_end": "2026-05-20T12:00:00+00:00",
                "episodes_analyzed": 0,
                "archetype_shift_detected": False,
                "model_used": "fake",
                "status": "IMMUTABLE",
                "report_content": "{}",
            },
        )

        assert shadow.last_scan_at(graph_store) is None
