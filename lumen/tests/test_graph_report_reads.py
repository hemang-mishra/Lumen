"""
Tests for the three reads that only a periodic report needs.

Run against a real Kuzu database, like the other read tests, because every one
of them is a query against typed tables and a stand-in answering from a Python
dictionary would pass whether or not the query was written correctly.

The first is the one that carries a decision. A report covers the days it is
about rather than the days somebody typed, so writing is selected on the day it
describes — and an entry made in June about a day in May belongs to May.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from lumen.graph.kuzu_impl import KuzuGraphProvider

UTC = timezone.utc


@pytest.fixture
def graph(tmp_path):
    """A real, empty Kuzu database."""
    provider = KuzuGraphProvider(str(tmp_path / "report_reads_db"))
    provider.init_schema()
    yield provider
    provider.close()


def episode(graph, node_id: str, *, happened_on: str, written_on: str | None = None) -> str:
    """One piece of writing, with the day it is about and the day it was made."""
    graph.write_node(
        "EpisodeNode",
        {
            "node_id": node_id,
            "entry_id": f"entry_{node_id}",
            "occurred_at": f"{happened_on}T20:00:00+00:00",
            "created_at": f"{written_on or happened_on}T21:00:00+00:00",
            "valid_from": f"{happened_on}T20:00:00+00:00",
            "event_date": happened_on,
            "session_label": "evening",
            "source_modality": "TEXT_ENTRY",
            "entry_class": "REFLECTION",
            "episode_summary": f"about {happened_on}",
            "episode_index": 1,
            "total_episodes_in_entry": 1,
            "coreference_map_id": f"cm_{node_id}",
            "reconciliation_status": "COMPLETE",
            "raw_text_hash": f"hash_{node_id}",
        },
    )
    return node_id


def observation(graph, node_id: str, *, episode_id: str) -> str:
    """One noticing inside a piece of writing."""
    graph.write_node(
        "ObservationNode",
        {
            "node_id": node_id,
            "episode_id": episode_id,
            "occurred_at": "2026-05-04T20:00:00+00:00",
            "created_at": "2026-05-04T20:00:00+00:00",
            "valid_from": "2026-05-04T20:00:00+00:00",
            "type": "PATTERN",
            "content": "the same thing again",
            "signal_strength": "STANDARD",
            "provenance": "USER_GENERATED",
            "verification_status": "IMPLICIT",
            "extraction_confidence": "STANDARD",
            "status": "ACTIVE",
            "extraction_model": "fake",
            "extraction_attempt": 1,
        },
    )
    graph.write_edge("contains_obs", episode_id, node_id, {"valid_from": "2026-05-04T20:00:00+00:00"})
    return node_id


def pattern(graph, node_id: str) -> str:
    """One standing pattern for links to point at."""
    graph.write_node(
        "PatternNode",
        {
            "node_id": node_id,
            "version": 1,
            "created_at": "2026-01-01T00:00:00+00:00",
            "valid_from": "2026-01-01T00:00:00+00:00",
            "last_reinforced_at": "2026-05-04T00:00:00+00:00",
            "pattern_name": "Comparison spiral",
            "pattern_description": "measures self against others",
            "domain": "EMOTIONAL",
            "signal_strength": "STANDARD",
            "provenance": "USER_GENERATED",
            "verification_status": "IMPLICIT",
            "evidence_count": 1,
            "query_frequency": 0,
            "is_canonical": True,
            "status": "ACTIVE",
        },
    )
    return node_id


def report(
    graph,
    node_id: str,
    *,
    report_type: str = "MONTHLY",
    period_start: str = "2026-05-01T00:00:00+00:00",
    created_at: str = "2026-06-04T00:00:00+00:00",
) -> str:
    """One finished report."""
    graph.write_node(
        "MacroextractionReportNode",
        {
            "node_id": node_id,
            "created_at": created_at,
            "report_type": report_type,
            "period_start": period_start,
            "period_end": "2026-06-01T00:00:00+00:00",
            "episodes_analyzed": 3,
            "archetype_shift_detected": False,
            "model_used": "fake",
            "status": "IMMUTABLE",
            "report_content": "{}",
        },
    )
    return node_id


class TestFindingTheWritingForAPeriod:
    def test_writing_is_matched_on_the_day_it_is_about(self, graph):
        # Written on 3 June, about 28 May. Anything summarising a month of
        # somebody's life has to call this May or it is summarising their
        # typing habits instead.
        episode(graph, "ep_late", happened_on="2026-05-28", written_on="2026-06-03")

        found = graph.find_episodes_by_event_date(date(2026, 5, 1), date(2026, 6, 1))

        assert [row["node_id"] for row in found] == ["ep_late"]

    def test_the_first_day_of_a_period_is_included(self, graph):
        episode(graph, "ep_first", happened_on="2026-05-01")

        found = graph.find_episodes_by_event_date(date(2026, 5, 1), date(2026, 6, 1))

        assert [row["node_id"] for row in found] == ["ep_first"]

    def test_the_day_a_period_ends_belongs_to_the_next_one(self, graph):
        # Half-open, so consecutive periods share nothing and nothing falls
        # between two of them.
        episode(graph, "ep_boundary", happened_on="2026-06-01")

        found = graph.find_episodes_by_event_date(date(2026, 5, 1), date(2026, 6, 1))

        assert found == []

    def test_writing_comes_back_oldest_first(self, graph):
        episode(graph, "ep_late", happened_on="2026-05-27")
        episode(graph, "ep_early", happened_on="2026-05-04")

        found = graph.find_episodes_by_event_date(date(2026, 5, 1), date(2026, 6, 1))

        assert [row["node_id"] for row in found] == ["ep_early", "ep_late"]

    def test_a_period_with_nothing_in_it_comes_back_empty(self, graph):
        assert graph.find_episodes_by_event_date(date(2026, 5, 1), date(2026, 6, 1)) == []

    def test_the_answer_can_be_limited_and_paged(self, graph):
        for day in range(1, 6):
            episode(graph, f"ep_{day}", happened_on=f"2026-05-0{day}")

        found = graph.find_episodes_by_event_date(
            date(2026, 5, 1), date(2026, 6, 1), limit=2, offset=2
        )

        assert [row["node_id"] for row in found] == ["ep_3", "ep_4"]


class TestFollowingLinksForAWholeBatch:
    def test_links_from_several_records_come_back_in_one_answer(self, graph):
        # The batched form exists because the alternative is one walk per
        # record, and a month is several hundred of them.
        episode(graph, "ep_1", happened_on="2026-05-04")
        pattern(graph, "pat_1")
        for index in range(3):
            observation(graph, f"obs_{index}", episode_id="ep_1")
            graph.write_edge(
                "reinforces_obs_pat",
                f"obs_{index}",
                "pat_1",
                {"valid_from": "2026-05-04T20:00:00+00:00"},
            )

        found = graph.find_standing_edges(
            ["obs_0", "obs_1", "obs_2"], edge_names=["reinforces_obs_pat"]
        )

        assert sorted(edge.from_node_id for edge in found) == ["obs_0", "obs_1", "obs_2"]

    def test_only_the_links_asked_for_are_followed(self, graph):
        episode(graph, "ep_1", happened_on="2026-05-04")
        pattern(graph, "pat_1")
        observation(graph, "obs_1", episode_id="ep_1")
        graph.write_edge(
            "reinforces_obs_pat", "obs_1", "pat_1", {"valid_from": "2026-05-04T20:00:00+00:00"}
        )

        found = graph.find_standing_edges(["obs_1"], edge_names=["branches_to_obs_pat"])

        assert found == []

    def test_a_withdrawn_link_is_not_followed(self, graph):
        # A decision that was rolled back should not still be shaping what the
        # graph appears to say.
        episode(graph, "ep_1", happened_on="2026-05-04")
        pattern(graph, "pat_1")
        observation(graph, "obs_1", episode_id="ep_1")
        graph.write_edge(
            "reinforces_obs_pat",
            "obs_1",
            "pat_1",
            {
                "valid_from": "2026-05-04T20:00:00+00:00",
                "invalidated_at": "2026-05-05T00:00:00+00:00",
            },
        )

        assert graph.find_standing_edges(["obs_1"]) == []

    def test_a_withdrawn_link_can_be_asked_for(self, graph):
        episode(graph, "ep_1", happened_on="2026-05-04")
        pattern(graph, "pat_1")
        observation(graph, "obs_1", episode_id="ep_1")
        graph.write_edge(
            "reinforces_obs_pat",
            "obs_1",
            "pat_1",
            {
                "valid_from": "2026-05-04T20:00:00+00:00",
                "invalidated_at": "2026-05-05T00:00:00+00:00",
            },
        )

        found = graph.find_standing_edges(["obs_1"], include_invalidated=True)

        assert len(found) == 1

    def test_asking_about_nothing_answers_nothing(self, graph):
        assert graph.find_standing_edges([]) == []

    def test_a_record_named_twice_is_only_answered_once(self, graph):
        episode(graph, "ep_1", happened_on="2026-05-04")
        pattern(graph, "pat_1")
        observation(graph, "obs_1", episode_id="ep_1")
        graph.write_edge(
            "reinforces_obs_pat", "obs_1", "pat_1", {"valid_from": "2026-05-04T20:00:00+00:00"}
        )

        found = graph.find_standing_edges(["obs_1", "obs_1"])

        assert len(found) == 1

    def test_more_records_than_fit_in_one_question_are_still_answered(self, graph):
        # The identifiers travel as a parameter, so they are sent a chunk at a
        # time and the answers joined.
        episode(graph, "ep_1", happened_on="2026-05-04")
        pattern(graph, "pat_1")
        wanted = []
        for index in range(12):
            observation(graph, f"obs_{index}", episode_id="ep_1")
            graph.write_edge(
                "reinforces_obs_pat",
                f"obs_{index}",
                "pat_1",
                {"valid_from": "2026-05-04T20:00:00+00:00"},
            )
            wanted.append(f"obs_{index}")

        from lumen.graph import kuzu_impl

        original = kuzu_impl._EDGE_LOOKUP_CHUNK
        kuzu_impl._EDGE_LOOKUP_CHUNK = 5
        try:
            found = graph.find_standing_edges(wanted, edge_names=["reinforces_obs_pat"])
        finally:
            kuzu_impl._EDGE_LOOKUP_CHUNK = original

        assert len(found) == 12


class TestFindingReportsAlreadyWritten:
    def test_nothing_written_answers_nothing(self, graph):
        assert graph.find_reports() == []

    def test_every_report_comes_back_newest_first(self, graph):
        report(graph, "macro_old", created_at="2026-05-04T00:00:00+00:00")
        report(graph, "macro_new", created_at="2026-06-04T00:00:00+00:00")

        found = graph.find_reports()

        assert [row["node_id"] for row in found] == ["macro_new", "macro_old"]

    def test_reports_can_be_narrowed_to_one_kind(self, graph):
        report(graph, "macro_monthly")
        report(graph, "macro_weekly", report_type="WEEKLY")

        found = graph.find_reports(report_type="WEEKLY")

        assert [row["node_id"] for row in found] == ["macro_weekly"]

    def test_one_periods_reports_can_be_asked_for_exactly(self, graph):
        # This check is what makes running the same period twice cost nothing,
        # which is what makes a schedule safe to fire more than once.
        report(graph, "macro_may", period_start="2026-05-01T00:00:00+00:00")
        report(graph, "macro_april", period_start="2026-04-01T00:00:00+00:00")

        found = graph.find_reports(
            report_type="MONTHLY", period_start=datetime(2026, 5, 1, tzinfo=UTC)
        )

        assert [row["node_id"] for row in found] == ["macro_may"]

    def test_two_reports_for_one_period_both_survive(self, graph):
        # Nothing here is ever overwritten, so a deliberate rebuild leaves
        # both and the newer is the one a reader is shown.
        report(graph, "macro_may", created_at="2026-06-04T00:00:00+00:00")
        report(graph, "macro_may_r2", created_at="2026-06-05T00:00:00+00:00")

        found = graph.find_reports(
            report_type="MONTHLY", period_start=datetime(2026, 5, 1, tzinfo=UTC)
        )

        assert [row["node_id"] for row in found] == ["macro_may_r2", "macro_may"]

    def test_the_answer_can_be_limited_and_paged(self, graph):
        for index in range(4):
            report(graph, f"macro_{index}", created_at=f"2026-06-0{index + 1}T00:00:00+00:00")

        found = graph.find_reports(limit=2, offset=1)

        assert [row["node_id"] for row in found] == ["macro_2", "macro_1"]


class TestAskingByDateAboutRecordsWithNoStartDate:
    def test_decisions_can_now_be_bounded_by_when_they_were_made(self, graph):
        # A note of a decision has no date it "became true" — it is made at
        # the moment it is taken, and that is what a date filter should use.
        for day, node_id in ((4, "d_early"), (20, "d_late")):
            graph.write_node(
                "DecisionAuditNode",
                {
                    "node_id": node_id,
                    "created_at": f"2026-05-{day:02d}T00:00:00+00:00",
                    "action": "BRANCH",
                    "source_node_id": "obs_1",
                    "target_node_id": "pat_1",
                    "confidence": 0.9,
                    "model_used": "fake",
                    "model_role": "THINKING",
                    "status": "ACTIVE",
                    "hitl_resolved": False,
                },
            )

        found = graph.find_nodes(
            ["DecisionAuditNode"],
            since=datetime(2026, 5, 10, tzinfo=UTC),
            until=datetime(2026, 5, 31, tzinfo=UTC),
            active_only=False,
        )

        assert [row["node_id"] for row in found] == ["d_late"]

    def test_a_record_that_belongs_to_someone_elses_moment_is_still_not_dated(self):
        # A step in a sequence has no moment of its own, so a date filter has
        # no honest answer and is left off rather than guessed at.
        from lumen.graph import queries

        assert queries.date_column("CausalStepNode") is None
        assert queries.date_column("DecisionAuditNode") == "created_at"
        assert queries.date_column("EpisodeNode") == "valid_from"
