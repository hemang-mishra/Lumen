"""
Tests for telling the assistant that something appears to be shifting.

The scan that produces these has existed since the reports were built and
nothing was ever told about them, which made an alert a note in a file rather
than something the system knew.

Most of this file is about restraint. An alert is rare, it goes stale, it is
never shown to somebody in the middle of a bad ten minutes, and it is charged
to the same allowance as everything else — because a line exempt from the
budget is a line that grows the prompt every time the scan fires.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from lumen.tests.conftest import registry_for

from lumen.config import ChatConfig, MacroConfig
from lumen.query.alerts import ShadowAlertReader
from lumen.query.assembly import block
from lumen.query.assembly.stage import ContextAssembler
from lumen.query.retrieval.contracts import RetrievalBundle
from lumen.schemas.enums import EmotionalRegister, ReportType
from lumen.schemas.query import RetrievalSignal

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)

# Whose history the alert is about. There is one per person now.
USER = "usr_local"


@pytest.fixture
def shadow_report(graph_store):
    """Write one alert into the graph, with a chosen age and finding."""

    def _write(
        *,
        hours_ago: int = 1,
        detected: bool = True,
        summary: str = "three beliefs about work moved together",
        node_id: str = "macro_shadow_1",
    ) -> str:
        raised = NOW - timedelta(hours=hours_ago)
        graph_store.write_node(
            "MacroextractionReportNode",
            {
                "node_id": node_id,
                "created_at": raised,
                "report_type": ReportType.SHADOW.value,
                "period_start": raised - timedelta(days=2),
                "period_end": raised,
                "episodes_analyzed": 3,
                "archetype_shift_detected": False,
                "model_used": "fake",
                "status": "IMMUTABLE",
                "report_content": json.dumps(
                    {
                        "shadow_micro_shift": {
                            "detected": detected,
                            "summary": summary,
                        }
                    }
                ),
            },
        )
        return node_id

    return _write


class TestWhenThereIsSomethingToSay:
    def test_a_recent_alert_comes_back_as_one_line(self, graph_store, shadow_report):
        shadow_report()

        alert = ShadowAlertReader(registry_for(graph_store)).current(USER, NOW)

        assert alert is not None
        assert "three beliefs about work moved together" in alert

    def test_nothing_at_all_is_the_common_answer(self, graph_store):
        assert ShadowAlertReader(registry_for(graph_store)).current(USER, NOW) is None

    def test_a_scan_that_found_nothing_says_nothing(self, graph_store, shadow_report):
        shadow_report(detected=False)

        assert ShadowAlertReader(registry_for(graph_store)).current(USER, NOW) is None

    def test_an_alert_with_no_words_in_it_says_nothing(
        self, graph_store, shadow_report
    ):
        shadow_report(summary="   ")

        assert ShadowAlertReader(registry_for(graph_store)).current(USER, NOW) is None


class TestWhenItHasStopped_BeingNews:
    def test_an_old_alert_is_not_mentioned(self, graph_store, shadow_report):
        # Mentioning last month's burst would make the assistant sound like
        # it had lost track of when things happened.
        shadow_report(hours_ago=200)

        assert ShadowAlertReader(registry_for(graph_store)).current(USER, NOW) is None

    def test_the_window_comes_from_the_settings(self, graph_store, shadow_report):
        shadow_report(hours_ago=30)

        near = ShadowAlertReader(registry_for(graph_store), config=MacroConfig(shadow_repeat_hours=48)
        ).current(USER, NOW)
        far = ShadowAlertReader(registry_for(graph_store), config=MacroConfig(shadow_repeat_hours=12)
        ).current(USER, NOW)

        assert near is not None
        assert far is None


class TestWhenTheStoreMisbehaves:
    def test_a_graph_that_refuses_costs_the_alert_and_not_the_turn(self):
        class Refuses:
            def find_reports(self, **kwargs):
                raise RuntimeError("the graph is unreachable")

        assert ShadowAlertReader(Refuses()).current(USER, NOW) is None

    def test_an_unreadable_body_costs_the_alert_and_not_the_turn(
        self, graph_store, shadow_report
    ):
        graph_store.write_node(
            "MacroextractionReportNode",
            {
                "node_id": "macro_shadow_broken",
                "created_at": NOW,
                "report_type": ReportType.SHADOW.value,
                "period_start": NOW - timedelta(days=2),
                "period_end": NOW,
                "episodes_analyzed": 0,
                "archetype_shift_detected": False,
                "model_used": "fake",
                "status": "IMMUTABLE",
                "report_content": "not json at all",
            },
        )

        assert ShadowAlertReader(registry_for(graph_store)).current(USER, NOW) is None


class TestReadingStoredThingsBack:
    def test_an_alert_with_no_readable_date_is_not_mentioned(self, graph_store):
        # It cannot be shown to be recent, and an alert that might be from
        # last year is not one worth saying out loud.
        graph_store.write_node(
            "MacroextractionReportNode",
            {
                "node_id": "macro_shadow_undated",
                "created_at": "some time ago",
                "report_type": ReportType.SHADOW.value,
                "period_start": NOW - timedelta(days=2),
                "period_end": NOW,
                "episodes_analyzed": 1,
                "archetype_shift_detected": False,
                "model_used": "fake",
                "status": "IMMUTABLE",
                "report_content": json.dumps(
                    {"shadow_micro_shift": {"detected": True, "summary": "x"}}
                ),
            },
        )

        assert ShadowAlertReader(registry_for(graph_store)).current(USER, NOW) is None

    def test_a_body_that_is_already_a_document_is_read_as_one(self):
        # A different store could hand these back parsed rather than as text.
        from lumen.query.alerts import _summary_of

        assert (
            _summary_of(
                {
                    "report_content": {
                        "shadow_micro_shift": {"detected": True, "summary": "moved"}
                    }
                }
            )
            == "moved"
        )

    def test_a_body_that_is_empty_says_nothing(self):
        from lumen.query.alerts import _summary_of

        assert _summary_of({"report_content": None}) == ""
        assert _summary_of({}) == ""


class TestWhereItEndsUp:
    def _briefing(self, alert, register=EmotionalRegister.STABLE):
        return ContextAssembler().assemble(
            RetrievalBundle(session_id="s", turn_index=0),
            RetrievalSignal(
                session_id="s", turn_index=0, emotional_register=register
            ),
            now=NOW,
            alert=alert,
        )

    def test_it_reaches_the_briefing(self):
        context = self._briefing("three beliefs moved together")

        assert context.alert == "three beliefs moved together"
        assert "three beliefs moved together" in block.render(context)

    def test_it_is_worded_as_something_to_hold_rather_than_announce(self):
        # Being told "you are shifting" by software is not a conversation
        # anybody asked for.
        rendered = block.render(self._briefing("something moved"))

        assert block.ALERT_HEADING in rendered
        assert "hold in mind rather than to raise" in rendered

    def test_a_turn_that_found_no_history_still_carries_it(self):
        # It is a fact about the last two days rather than about what this
        # turn asked for.
        context = self._briefing("something moved")

        assert context.is_empty
        assert block.render(context) != ""

    def test_nothing_is_said_to_somebody_in_crisis(self):
        # A system that interrupts a hard moment to report on itself has
        # misread what it is for.
        context = self._briefing("something moved", EmotionalRegister.CRISIS)

        assert context.alert is None
        assert block.render(context) == ""

    def test_it_is_charged_to_the_same_allowance_as_everything_else(self):
        # A line exempt from the budget is a line that grows the prompt every
        # time the scan fires.
        without = self._briefing(None)
        with_alert = self._briefing("something moved")

        assert with_alert.estimated_tokens > without.estimated_tokens
        assert with_alert.token_budget == without.token_budget
