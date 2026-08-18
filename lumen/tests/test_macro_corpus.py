"""
Tests for reading one stretch of history out of a real graph.

Run against an actual embedded database rather than a stand-in, because what
this module does *is* the queries. A stand-in answering from a dictionary
would pass whether or not the query was written correctly, which is the only
thing worth testing here.

The case that matters most is the first one. A report covers when things
happened, not when they were written down, and an entry made in June about a
day in May belongs to May.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from lumen.config import MacroConfig
from lumen.pipeline.macroextraction import corpus, windows
from lumen.schemas.enums import ReportType

UTC = timezone.utc


def may(graph) -> object:
    """The window covering May 2026."""
    return windows.window_for(ReportType.MONTHLY, datetime(2026, 5, 15, tzinfo=UTC))


class TestWhichWritingBelongsToAPeriod:
    def test_writing_is_placed_by_when_it_happened(self, graph_store, seed_month):
        # Written on 3 June, about 28 May. It belongs to May, because the
        # report is about the person's life rather than their typing habits.
        seed_month("ep_late", day=28, written_on=date(2026, 6, 3))

        gathered = corpus.gather(
            may(graph_store), graph=graph_store, config=MacroConfig()
        )

        assert gathered.episode_ids == ("ep_late",)

    def test_writing_from_another_month_is_left_out(self, graph_store, seed_month):
        seed_month("ep_may", day=12)
        seed_month("ep_june", day=12, month=6)

        gathered = corpus.gather(
            may(graph_store), graph=graph_store, config=MacroConfig()
        )

        assert gathered.episode_ids == ("ep_may",)

    def test_the_last_day_of_a_period_is_included(self, graph_store, seed_month):
        seed_month("ep_last", day=31)

        gathered = corpus.gather(
            may(graph_store), graph=graph_store, config=MacroConfig()
        )

        assert gathered.episode_ids == ("ep_last",)

    def test_writing_arrives_oldest_first(self, graph_store, seed_month):
        seed_month("ep_late", day=27)
        seed_month("ep_early", day=4)

        gathered = corpus.gather(
            may(graph_store), graph=graph_store, config=MacroConfig()
        )

        assert gathered.episode_ids == ("ep_early", "ep_late")

    def test_a_period_with_nothing_in_it_reads_as_empty(self, graph_store):
        gathered = corpus.gather(
            may(graph_store), graph=graph_store, config=MacroConfig()
        )

        assert gathered.is_empty is True
        assert gathered.episodes == []


class TestWhenThereIsTooMuchToRead:
    def test_a_cap_is_recorded_rather_than_hidden(self, graph_store, seed_month):
        # A partial summary presented as a whole one is a wrong answer that
        # looks right, so hitting the cap is carried into the report.
        for day in range(1, 6):
            seed_month(f"ep_{day}", day=day)

        gathered = corpus.gather(
            may(graph_store),
            graph=graph_store,
            config=MacroConfig(max_episodes_per_window=3),
        )

        assert gathered.truncated is True
        assert len(gathered.episodes) == 3

    def test_reading_everything_is_not_reported_as_cut_short(
        self, graph_store, seed_month
    ):
        seed_month("ep_1", day=4)

        gathered = corpus.gather(
            may(graph_store), graph=graph_store, config=MacroConfig()
        )

        assert gathered.truncated is False


class TestWhatWritingProduced:
    def test_the_noticings_inside_a_piece_of_writing_are_read(
        self, graph_store, seed_month
    ):
        seed_month(
            "ep_1",
            day=4,
            observations=(("obs_1", "EMOTION", "felt behind"),),
        )

        gathered = corpus.gather(
            may(graph_store), graph=graph_store, config=MacroConfig()
        )

        assert [item.node_id for item in gathered.episodes[0].observations] == ["obs_1"]
        assert gathered.episodes[0].observations[0].content == "felt behind"

    def test_links_to_standing_records_are_followed(self, graph_store, seed_month, seed_pattern):
        seed_pattern("pat_a", name="Comparison")
        seed_month(
            "ep_1",
            day=4,
            observations=(("obs_1", "PATTERN", "same thing again"),),
            patterns={"obs_1": "pat_a"},
        )

        gathered = corpus.gather(
            may(graph_store), graph=graph_store, config=MacroConfig()
        )

        assert [(link.from_id, link.to_id) for link in gathered.links] == [
            ("obs_1", "pat_a")
        ]
        assert gathered.patterns["pat_a"]["pattern_name"] == "Comparison"

    def test_links_are_sorted_by_what_they_point_at(
        self, graph_store, seed_month, seed_pattern
    ):
        seed_pattern("pat_a")
        seed_month(
            "ep_1",
            day=4,
            observations=(("obs_1", "PATTERN", "x"),),
            patterns={"obs_1": "pat_a"},
        )

        gathered = corpus.gather(
            may(graph_store), graph=graph_store, config=MacroConfig()
        )

        assert gathered.links[0].to_type == "pattern"

    def test_a_period_with_no_links_still_reads(self, graph_store, seed_month):
        seed_month("ep_1", day=4, observations=(("obs_1", "EMOTION", "x"),))

        gathered = corpus.gather(
            may(graph_store), graph=graph_store, config=MacroConfig()
        )

        assert gathered.links == []
        assert gathered.patterns == {}


class TestReachingOutsideThePeriod:
    def test_the_period_before_is_counted_for_comparison(
        self, graph_store, seed_month, seed_pattern
    ):
        # Read directly rather than taken from April's own report, so the
        # comparison survives a period that was never reported on.
        seed_pattern("pat_a")
        seed_month(
            "ep_april",
            day=10,
            month=4,
            observations=(("obs_april", "PATTERN", "x"),),
            patterns={"obs_april": "pat_a"},
        )
        seed_month("ep_may", day=10, observations=(("obs_may", "EMOTION", "y"),))

        gathered = corpus.gather(
            may(graph_store), graph=graph_store, config=MacroConfig()
        )

        assert gathered.previous_pattern_episodes == {"pat_a": 1}
        assert gathered.previous_pattern_frequency == {"pat_a": 100.0}
        assert gathered.previous_episode_count == 1

    def test_an_empty_earlier_period_compares_to_nothing(
        self, graph_store, seed_month
    ):
        seed_month("ep_may", day=10)

        gathered = corpus.gather(
            may(graph_store), graph=graph_store, config=MacroConfig()
        )

        assert gathered.previous_pattern_frequency == {}
        assert gathered.previous_episode_count == 0

    def test_every_live_pattern_is_read_whatever_its_date(
        self, graph_store, seed_month, seed_pattern
    ):
        # Ageing is a statement about absence, so the patterns worth reporting
        # on are exactly the ones that did not appear.
        seed_pattern("pat_ancient", valid_from="2024-01-01T00:00:00+00:00")
        seed_month("ep_1", day=4)

        gathered = corpus.gather(
            may(graph_store), graph=graph_store, config=MacroConfig()
        )

        assert "pat_ancient" in {
            str(row.get("node_id")) for row in gathered.all_patterns
        }

    def test_a_retired_pattern_is_not_read(
        self, graph_store, seed_month, seed_pattern
    ):
        seed_pattern("pat_gone", status="SUPERSEDED")
        seed_month("ep_1", day=4)

        gathered = corpus.gather(
            may(graph_store), graph=graph_store, config=MacroConfig()
        )

        assert "pat_gone" not in {
            str(row.get("node_id")) for row in gathered.all_patterns
        }

    def test_a_quarterly_report_gathers_the_longer_comparison(
        self, graph_store, seed_month, seed_pattern
    ):
        seed_pattern("pat_a")
        seed_month(
            "ep_jan",
            day=15,
            month=1,
            observations=(("obs_jan", "PATTERN", "x"),),
            patterns={"obs_jan": "pat_a"},
        )
        seed_month("ep_apr", day=15, month=4)

        quarterly = windows.window_for(
            ReportType.QUARTERLY, datetime(2026, 5, 15, tzinfo=UTC)
        )
        gathered = corpus.gather(quarterly, graph=graph_store, config=MacroConfig())

        assert gathered.comparison_counts == {"pat_a": 1}

    def test_a_weekly_report_gathers_no_long_comparison(
        self, graph_store, seed_month
    ):
        seed_month("ep_1", day=13)

        weekly = windows.window_for(
            ReportType.WEEKLY, datetime(2026, 5, 13, tzinfo=UTC)
        )
        gathered = corpus.gather(weekly, graph=graph_store, config=MacroConfig())

        assert gathered.comparison_counts == {}


class TestNoticingSelfAwareness:
    def test_catching_yourself_in_the_act_is_counted(
        self, graph_store, seed_month, seed_pattern
    ):
        seed_pattern("pat_a")
        seed_month(
            "ep_1",
            day=4,
            observations=(("obs_1", "METACOGNITIVE_INTERRUPT", "caught myself doing it"),),
            patterns={"obs_1": "pat_a"},
        )

        gathered = corpus.gather(
            may(graph_store), graph=graph_store, config=MacroConfig()
        )

        assert gathered.awareness_counts == {"pat_a": 1}

    def test_an_ordinary_noticing_is_not_counted_as_awareness(
        self, graph_store, seed_month, seed_pattern
    ):
        seed_pattern("pat_a")
        seed_month(
            "ep_1",
            day=4,
            observations=(("obs_1", "EMOTION", "x"),),
            patterns={"obs_1": "pat_a"},
        )

        gathered = corpus.gather(
            may(graph_store), graph=graph_store, config=MacroConfig()
        )

        assert gathered.awareness_counts == {}


class TestTheReviewQueue:
    def test_what_is_waiting_is_read_when_a_store_was_given(
        self, graph_store, ops_store, seed_month
    ):
        from lumen.operational.schemas import HitlQueueItemRecord
        from lumen.schemas.enums import HitlEntryType

        ops_store.hitl.enqueue(
            HitlQueueItemRecord(
                id="hitl_1",
                user_id="local",
                audit_node_id="d_1",
                entry_type=HitlEntryType.AMBIGUOUS_TIE,
            )
        )
        seed_month("ep_1", day=4)

        gathered = corpus.gather(
            may(graph_store), graph=graph_store, ops=ops_store, config=MacroConfig()
        )

        count, oldest = gathered.pending_review
        assert count == 1
        assert oldest is not None

    def test_no_store_reads_as_nothing_waiting(self, graph_store, seed_month):
        # A report is about somebody's history; the state of a review queue is
        # a footnote in it, and its absence should not refuse the report.
        seed_month("ep_1", day=4)

        gathered = corpus.gather(
            may(graph_store), graph=graph_store, config=MacroConfig()
        )

        assert gathered.pending_review == (0, None)


class TestSortingLinksByWhatTheyPointAt:
    def test_each_kind_of_link_is_recognised_from_its_name(self):
        # Read off the name rather than by fetching the record, so several
        # hundred links can be sorted without a query each.
        assert corpus._target_kind("mentions_obs") == "person"
        assert corpus._target_kind("adopted_as_sess") == "principle"
        assert corpus._target_kind("reinforces_obs_bel") == "belief"
        assert corpus._target_kind("branches_to_evt_pat") == "pattern"
        assert corpus._target_kind("regulates_obs") == "pattern"
        assert corpus._target_kind("contains_obs") == "other"

    def test_a_principle_link_is_gathered_without_being_mistaken_for_a_pattern(
        self, graph_store, seed_month
    ):
        graph_store.write_node(
            "AdoptedPrincipleNode",
            {
                "node_id": "prin_1",
                "created_at": "2026-05-01T00:00:00+00:00",
                "valid_from": "2026-05-01T00:00:00+00:00",
                "adopted_at": "2026-05-01T00:00:00+00:00",
                "principle_statement": "Consistency over intensity",
                "principle_name": "Consistency",
                "domain": "HEALTH",
                "lifecycle_state": "ACTIVE",
                "lifecycle_updated_at": "2026-05-01T00:00:00+00:00",
                "source_session_id": "sess_1",
                "provenance": "USER_GENERATED",
                "last_referenced_at": "2026-05-01T00:00:00+00:00",
                "evidence_count": 1,
                "status": "ACTIVE",
                "lifecycle_history": '[{"state": "ACTIVE", "at": "2026-05-01T00:00:00+00:00", "reason": "adopted"}]',
            },
        )
        seed_month("ep_1", day=4, observations=(("obs_1", "LESSON", "held to it"),))
        graph_store.write_edge(
            "adopted_as_obs", "obs_1", "prin_1", {"valid_from": "2026-05-04T20:00:00+00:00"}
        )

        gathered = corpus.gather(
            may(graph_store), graph=graph_store, config=MacroConfig()
        )

        assert [link.to_type for link in gathered.links] == ["principle"]
        assert gathered.patterns == {}


class TestReadingStoredDatesBack:
    def test_a_day_already_read_as_a_day_is_kept(self):
        assert corpus._as_date(date(2026, 5, 4)) == date(2026, 5, 4)

    def test_a_moment_is_narrowed_to_its_day(self):
        assert corpus._as_date(datetime(2026, 5, 4, 20, tzinfo=UTC)) == date(2026, 5, 4)

    def test_an_unreadable_day_does_not_break_the_reading(self):
        # A single unreadable field should cost that field, not the report.
        assert corpus._as_date("sometime in May") == date.min

    def test_a_moment_already_read_as_one_is_kept(self):
        moment = datetime(2026, 5, 4, 20, tzinfo=UTC)

        assert corpus._as_datetime(moment) == moment

    def test_an_unreadable_moment_does_not_break_the_reading(self):
        assert corpus._as_datetime("later that evening") == datetime.min


class TestQuestionsTheWritingSettled:
    def test_writing_that_closes_a_question_is_noticed(self, graph_store, seed_month):
        graph_store.write_node(
            "OpenLoopNode",
            {
                "node_id": "loop_1",
                "created_at": "2026-04-01T00:00:00+00:00",
                "valid_from": "2026-04-01T00:00:00+00:00",
                "loop_description": "Why do I hesitate to ask?",
                "loop_category": "SELF_CONCEPT",
                "provenance": "AI_GENERATED",
                "source_episode_id": "ep_april",
                "resolution_status": "OPEN",
                "last_referenced_at": "2026-05-04T00:00:00+00:00",
            },
        )
        seed_month("ep_1", day=4)
        graph_store.write_edge(
            "closes", "ep_1", "loop_1", {"valid_from": "2026-05-04T20:00:00+00:00"}
        )

        gathered = corpus.gather(
            may(graph_store), graph=graph_store, config=MacroConfig()
        )

        assert gathered.closed_loop_ids == ("loop_1",)

    def test_nothing_settled_is_reported_as_nothing(self, graph_store, seed_month):
        seed_month("ep_1", day=4)

        gathered = corpus.gather(
            may(graph_store), graph=graph_store, config=MacroConfig()
        )

        assert gathered.closed_loop_ids == ()


class TestFindingsThatAreNotNoticings:
    def test_events_and_sessions_count_as_things_a_pattern_can_hang_off(
        self, graph_store, seed_month, seed_pattern
    ):
        seed_pattern("pat_a")
        seed_month("ep_1", day=4)
        graph_store.write_node(
            "EventNode",
            {
                "node_id": "evt_1",
                "episode_id": "ep_1",
                "occurred_at": "2026-05-04T20:00:00+00:00",
                "created_at": "2026-05-04T20:00:00+00:00",
                "valid_from": "2026-05-04T20:00:00+00:00",
                "event_summary": "the review meeting",
                "signal_strength": "HIGH",
                "status": "ACTIVE",
            },
        )
        graph_store.write_edge(
            "contains_evt", "ep_1", "evt_1", {"valid_from": "2026-05-04T20:00:00+00:00"}
        )
        graph_store.write_edge(
            "reinforces_evt_pat",
            "evt_1",
            "pat_a",
            {"valid_from": "2026-05-04T20:00:00+00:00"},
        )

        gathered = corpus.gather(
            may(graph_store), graph=graph_store, config=MacroConfig()
        )

        assert "evt_1" in gathered.episodes[0].finding_ids
        assert [link.to_id for link in gathered.links] == ["pat_a"]


class TestAwarenessAcrossTheLongerStretch:
    def test_catching_yourself_earlier_on_is_counted_for_comparison(
        self, graph_store, seed_month, seed_pattern
    ):
        # The comparison needs both halves, or a habit that is now caught in
        # the act cannot be told from one that always was.
        seed_pattern("pat_a")
        seed_month("ep_may", day=10)
        graph_store.write_node(
            "EpisodeNode",
            {
                "node_id": "ep_feb",
                "entry_id": "entry_feb",
                "occurred_at": "2026-02-10T20:00:00+00:00",
                "created_at": "2026-02-10T21:00:00+00:00",
                "valid_from": "2026-02-10T20:00:00+00:00",
                "event_date": "2026-02-10",
                "session_label": "evening",
                "source_modality": "TEXT_ENTRY",
                "entry_class": "REFLECTION",
                "episode_summary": "february",
                "episode_index": 1,
                "total_episodes_in_entry": 1,
                "coreference_map_id": "cm_feb",
                "reconciliation_status": "COMPLETE",
                "raw_text_hash": "hash_feb",
            },
        )
        graph_store.write_node(
            "ObservationNode",
            {
                "node_id": "obs_feb",
                "episode_id": "ep_feb",
                "occurred_at": "2026-02-10T20:00:00+00:00",
                "created_at": "2026-02-10T20:00:00+00:00",
                "valid_from": "2026-02-10T20:00:00+00:00",
                "type": "METACOGNITIVE_INTERRUPT",
                "content": "caught myself doing it",
                "signal_strength": "HIGH",
                "provenance": "USER_GENERATED",
                "verification_status": "IMPLICIT",
                "extraction_confidence": "STANDARD",
                "status": "ACTIVE",
                "extraction_model": "fake",
                "extraction_attempt": 1,
            },
        )
        graph_store.write_edge(
            "contains_obs", "ep_feb", "obs_feb", {"valid_from": "2026-02-10T20:00:00+00:00"}
        )
        graph_store.write_edge(
            "reinforces_obs_pat",
            "obs_feb",
            "pat_a",
            {"valid_from": "2026-02-10T20:00:00+00:00"},
        )

        quarterly = windows.window_for(
            ReportType.QUARTERLY, datetime(2026, 5, 15, tzinfo=UTC)
        )
        gathered = corpus.gather(quarterly, graph=graph_store, config=MacroConfig())

        assert gathered.previous_awareness_counts == {"pat_a": 1}
