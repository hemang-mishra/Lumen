"""
Tests for running a whole session and keeping its failures apart.

Two of these matter more than the rest. One entry losing a single topic must
not cost the other three, and an entry already in the graph must not be read
and decided a second time. Both are silent failures if they go wrong: the
first quietly loses writing, the second quietly records an entry as a repeat
of itself.
"""

from __future__ import annotations

import json

import pytest

from lumen.config import AppConfig
from lumen.operational.enums import BufferStatus, JobStatus, StageStatus
from lumen.pipeline.orchestration import episode as episode_module
from lumen.pipeline.orchestration import run_pipeline
from lumen.schemas.enums import (
    EntryClass,
    EpisodeRunStatus,
    PipelineStage,
    QualityGateDecision,
    SourceModality,
)

TWO_EPISODE_TEXT = (
    "I went to the cafe alone today and ate there without the usual dread. "
    "Then I saw what Alex had shipped this week and felt small and behind. "
    "I sat with it for a while and the pressure lifted on its own. "
    "I think the comparing is the thing that hurts, not the gap itself."
)


@pytest.fixture
def run(ops_store, graph_store, vector_store, embedder, full_run_providers):
    """Run the whole pipeline against real stores and scripted models."""

    def _run(event, *, overrides=None, providers=None, config=None):
        light, deep = providers or full_run_providers(overrides)
        return run_pipeline(
            event,
            graph=graph_store,
            vectors=vector_store,
            embedder=embedder,
            lightweight=light,
            thinking=deep,
            ops=ops_store,
            config=config or AppConfig(),
        )

    return _run


def _two_episodes() -> str:
    """A structure reply that splits the entry into two separate topics."""
    return json.dumps(
        {
            "episodes": [
                {
                    "episode_summary": "The cafe, and eating alone without dread",
                    "text": "I went to the cafe alone today and ate there without the usual dread.",
                    "overarching_themes": ["avoidance"],
                },
                {
                    "episode_summary": "Comparing himself to Alex",
                    "text": (
                        "Then I saw what Alex had shipped this week and felt small and behind. "
                        "I sat with it for a while and the pressure lifted on its own. "
                        "I think the comparing is the thing that hurts, not the gap itself."
                    ),
                    "overarching_themes": ["comparison"],
                },
            ],
            "coreference": {"resolved_entities": [], "ambiguous_refs": []},
        }
    )


def _scores(count: int, score: float = 0.85) -> str:
    return json.dumps(
        {
            "scores": [
                {"episode_index": index, "coherence_score": score, "reason": "clear"}
                for index in range(1, count + 1)
            ]
        }
    )


class TestTheOrdinaryRun:
    def test_all_four_stages_run_in_order(self, run, decayed_session, ops_store):
        report = run(decayed_session())

        stages = [
            (r.stage, r.status) for r in ops_store.jobs.get_stage_runs(report.job_id)
        ]
        assert [stage for stage, _ in stages] == [
            PipelineStage.STAGE_0_PREPROCESSING,
            PipelineStage.STAGE_1_MICROEXTRACTION,
            PipelineStage.STAGE_2_RETRIEVAL,
            PipelineStage.STAGE_3_RECONCILIATION,
            PipelineStage.STAGE_4_GRAPH_WRITE,
        ]
        assert all(status is StageStatus.COMPLETE for _, status in stages)

    def test_the_entry_ends_up_in_the_graph(self, run, decayed_session, graph_store):
        report = run(decayed_session())

        episode = report.episodes[0]
        assert graph_store.get_node(episode.episode_id) is not None
        assert episode.nodes_written > 0
        assert episode.edges_written > 0

    def test_what_was_said_becomes_findable(self, run, decayed_session):
        report = run(decayed_session())

        assert report.vectors_written > 0

    def test_the_conversation_is_marked_as_dealt_with(
        self, run, decayed_session, ops_store
    ):
        event = run(decayed_session()).session_id

        assert ops_store.buffers.get_buffer(event).status is BufferStatus.PROCESSED

    def test_one_trace_covers_the_whole_run(self, run, decayed_session, ops_store):
        report = run(decayed_session())

        trace = ops_store.jobs.get_trace(report.trace_id)
        assert trace is not None
        assert trace.job.job_id == report.job_id
        assert {run_.trace_id for run_ in trace.stage_runs} == {report.trace_id}


class TestAnEntryWithNothingInIt:
    """
    Nothing survived cleaning, so there is nothing to record.

    The only case where a person's input is thrown away, and it turns on a
    structural fact — the text is empty — rather than on any judgement about
    whether the writing was any good.
    """

    @pytest.fixture
    def empty_after_cleaning(self, run, decayed_session):
        # A recording of nothing but hesitation sounds. Every word in it is
        # one that can never carry meaning, so removing them leaves an empty
        # entry — which is a fact about the text, not an opinion about it.
        return lambda: run(
            decayed_session("um uh er um", source_modality=SourceModality.VOICE_NOTE),
            overrides={
                "normalize_voice": json.dumps(
                    {"cleaned_text": "", "detected_languages": [], "translated": False}
                )
            },
        )

    def test_nothing_is_written(self, empty_after_cleaning):
        report = empty_after_cleaning()

        assert report.quality_gate_decision is QualityGateDecision.DISCARD
        assert report.episodes == []
        assert report.nodes_written == 0

    def test_the_conversation_is_marked_as_discarded(
        self, empty_after_cleaning, ops_store
    ):
        report = empty_after_cleaning()

        buffer = ops_store.buffers.get_buffer(report.session_id)
        assert buffer.status is BufferStatus.DISCARDED

    def test_the_run_still_counts_as_a_success(self, empty_after_cleaning):
        # Nothing failed. The entry simply held nothing.
        assert empty_after_cleaning().job_status == JobStatus.COMPLETE.value


class TestAThinEntry:
    def test_searching_and_deciding_are_skipped(
        self, run, decayed_session, ops_store
    ):
        # Nothing in it will ever be compared against the past, so paying
        # for the search and the decision would buy nothing.
        report = run(
            decayed_session("Tired. Long day."),
            overrides={
                "normalize_text": json.dumps(
                    {
                        "cleaned_text": "Tired. Long day.",
                        "detected_languages": ["en"],
                        "translated": False,
                    }
                ),
            },
        )

        statuses = {
            r.stage: r.status for r in ops_store.jobs.get_stage_runs(report.job_id)
        }
        assert statuses[PipelineStage.STAGE_2_RETRIEVAL] is StageStatus.SKIPPED
        assert statuses[PipelineStage.STAGE_3_RECONCILIATION] is StageStatus.SKIPPED

    def test_it_is_still_saved(self, run, decayed_session, graph_store):
        report = run(
            decayed_session("Tired. Long day."),
            overrides={
                "normalize_text": json.dumps(
                    {
                        "cleaned_text": "Tired. Long day.",
                        "detected_languages": ["en"],
                        "translated": False,
                    }
                ),
            },
        )

        assert report.episodes[0].entry_class is EntryClass.RAW_CAPTURE
        assert graph_store.get_node(report.episodes[0].episode_id) is not None

    def test_nothing_about_it_is_left_open(self, run, decayed_session):
        # It was never going to be reconciled, so there is nothing pending.
        report = run(
            decayed_session("Tired. Long day."),
            overrides={
                "normalize_text": json.dumps(
                    {
                        "cleaned_text": "Tired. Long day.",
                        "detected_languages": ["en"],
                        "translated": False,
                    }
                ),
            },
        )

        assert report.episodes[0].status is EpisodeRunStatus.COMPLETE


class TestAnEpisodeNobodyCouldDecideAbout:
    """
    Deciding can fail on its own, with everything before it having worked.

    The stage still reports COMPLETE, because it ran and produced a coherent
    outcome: nothing decided, nothing written, everything waiting for a
    person. What was missing was any way to see *why* without knowing the
    trace id and grepping a log file — the run recorded only that it had
    happened.
    """

    def test_the_reason_is_recorded_on_the_run(
        self, run, decayed_session, ops_store
    ):
        report = run(
            decayed_session(),
            overrides={"decision": "not json at all"},
        )

        stage = next(
            record
            for record in ops_store.jobs.get_stage_runs(report.job_id)
            if record.stage is PipelineStage.STAGE_3_RECONCILIATION
        )

        assert stage.validation_passed is False
        assert "unparseable" in stage.output_payload["no_decision_because"]

    def test_a_run_that_decided_normally_records_no_reason(
        self, run, decayed_session, ops_store
    ):
        report = run(decayed_session())

        stage = next(
            record
            for record in ops_store.jobs.get_stage_runs(report.job_id)
            if record.stage is PipelineStage.STAGE_3_RECONCILIATION
        )

        assert stage.validation_passed is True
        assert "no_decision_because" not in stage.output_payload


class TestAnEpisodeThatCouldNotBeRead:
    def test_it_is_saved_and_marked_as_needing_attention(
        self, run, decayed_session, graph_store
    ):
        # An episode nobody could read looks exactly like an empty one.
        # Saving nothing would file a lost entry as an uneventful day.
        report = run(
            decayed_session(),
            overrides={"extract_reflection": "not json at all"},
        )

        episode = report.episodes[0]
        assert episode.status is EpisodeRunStatus.SUSPENDED
        stored = graph_store.get_node(episode.episode_id)
        assert stored["reconciliation_status"] == "SUSPENDED"

    def test_searching_and_deciding_are_skipped(
        self, run, decayed_session, ops_store
    ):
        report = run(
            decayed_session(),
            overrides={"extract_reflection": "not json at all"},
        )

        statuses = {
            r.stage: r.status for r in ops_store.jobs.get_stage_runs(report.job_id)
        }
        assert statuses[PipelineStage.STAGE_2_RETRIEVAL] is StageStatus.SKIPPED


@pytest.fixture
def run_two_episodes(run, decayed_session, full_run_providers, monkeypatch):
    """
    Run an entry split into two topics, optionally breaking one of them.

    Breaking a topic is done by making the thinking half raise for that one,
    which is as close as a test can get to the real causes — a model timing
    out, a reply that makes no sense, a disk filling up.
    """

    def _run(*, break_episode: int | None = None):
        light, deep = full_run_providers(
            {"structure": _two_episodes(), "triage": _scores(2)}
        )
        real = episode_module.think
        seen: list[str] = []

        def maybe_fail(payload, **kwargs):
            seen.append(payload.episode.episode_id)
            if len(seen) == break_episode:
                raise RuntimeError("this one broke")
            return real(payload, **kwargs)

        if break_episode is not None:
            monkeypatch.setattr(episode_module, "think", maybe_fail)

        return run(decayed_session(TWO_EPISODE_TEXT), providers=(light, deep))

    return _run


class TestFailuresStayInTheirOwnEpisode:
    def test_one_bad_episode_does_not_cost_the_others(
        self, run_two_episodes, graph_store
    ):
        # The rule this whole file exists for. Losing a good topic because
        # an unrelated one broke is the worse outcome by a wide margin.
        report = run_two_episodes(break_episode=2)

        kept, lost = report.episodes
        assert kept.status is not EpisodeRunStatus.FAILED
        assert lost.status is EpisodeRunStatus.FAILED
        assert graph_store.get_node(kept.episode_id) is not None

    def test_a_failed_episode_saves_nothing_of_its_own(
        self, run_two_episodes, graph_store
    ):
        report = run_two_episodes(break_episode=2)

        lost = report.episodes[1]
        assert graph_store.get_node(lost.episode_id) is None
        assert lost.nodes_written == 0

    def test_what_went_wrong_is_recorded_against_that_episode(self, run_two_episodes):
        report = run_two_episodes(break_episode=2)

        assert "this one broke" in report.episodes[1].error

    def test_the_run_as_a_whole_reports_failure(self, run_two_episodes):
        assert run_two_episodes(break_episode=2).job_status == JobStatus.FAILED.value


class TestEpisodeOrdering:
    def test_both_episodes_of_an_entry_are_saved(self, run_two_episodes):
        report = run_two_episodes()

        assert len(report.episodes) == 2
        assert all(e.status is not EpisodeRunStatus.FAILED for e in report.episodes)

    def test_the_second_episode_is_chained_to_the_first(
        self, run_two_episodes, graph_store
    ):
        report = run_two_episodes()

        first, second = (e.episode_id for e in report.episodes)
        assert graph_store.get_node(second) is not None
        assert graph_store.get_node(first) is not None

    def test_an_episode_after_a_failure_still_saves(self, run_two_episodes):
        # It chains to the last episode that actually saved. Pointing at one
        # whose save was undone would be a link to a record that does not
        # exist, and the plan would refuse the whole episode over it.
        report = run_two_episodes(break_episode=1)

        assert report.episodes[0].status is EpisodeRunStatus.FAILED
        assert report.episodes[1].status is not EpisodeRunStatus.FAILED
        assert report.episodes[1].nodes_written > 0


class TestRunningAnEntryAgain:
    def test_an_episode_already_in_the_graph_is_left_alone(
        self, run, decayed_session, ops_store
    ):
        event = decayed_session()
        run(event)

        second = run(event)

        assert second.episodes[0].status is EpisodeRunStatus.SKIPPED
        assert second.episodes[0].nodes_written == 0

    def test_nothing_is_read_or_decided_a_second_time(
        self, run, decayed_session, ops_store
    ):
        # Deciding again would compare the entry against the graph's copy of
        # its own previous conclusions and record it as a repeat of itself.
        event = decayed_session()
        run(event)

        second = run(event)

        stages = {
            r.stage
            for r in ops_store.jobs.get_stage_runs(second.job_id)
        }
        assert PipelineStage.STAGE_1_MICROEXTRACTION not in stages
        assert PipelineStage.STAGE_3_RECONCILIATION not in stages

    def test_nothing_is_duplicated(self, run, decayed_session, ops_store):
        event = decayed_session()
        first = run(event)

        second = run(event)

        assert second.nodes_written == 0
        assert first.nodes_written > 0


class TestWhenRecordsCannotBeMadeFindable:
    """
    The one kind of damage a transaction cannot prevent.

    The graph and the search index cannot be written to as one, so a record
    can land correctly and still be invisible to every future search. That
    looks exactly like success from every angle, which is why none of it is
    allowed to pass quietly.
    """

    @pytest.fixture
    def run_with_broken_index(
        self, ops_store, graph_store, embedder, full_run_providers, decayed_session
    ):
        class BrokenIndex:
            def upsert(self, node_id, vector, payload):
                raise RuntimeError("the index is down")

        def _run():
            light, deep = full_run_providers()
            return run_pipeline(
                decayed_session(),
                graph=graph_store,
                vectors=BrokenIndex(),
                embedder=embedder,
                lightweight=light,
                thinking=deep,
                ops=ops_store,
                config=AppConfig(),
            )

        return _run

    def test_the_entry_is_still_saved(self, run_with_broken_index, graph_store):
        # Those records are correct. Undoing them would be the wrong repair.
        report = run_with_broken_index()

        assert report.episodes[0].nodes_written > 0
        assert graph_store.get_node(report.episodes[0].episode_id) is not None

    def test_the_records_that_cannot_be_found_are_named(self, run_with_broken_index):
        report = run_with_broken_index()

        assert report.unindexed_node_ids

    def test_the_run_reports_failure(self, run_with_broken_index):
        # Reporting success would leave nobody with a reason to look.
        assert run_with_broken_index().job_status == JobStatus.FAILED.value

    def test_the_gap_is_recoverable_from_the_log(self, run_with_broken_index, ops_store):
        # The run log holds what was written and what was indexed, so the
        # difference between them is exactly the list a repair needs.
        report = run_with_broken_index()
        trace = ops_store.jobs.get_trace(report.trace_id)

        written = {w.node_id for w in trace.writes if w.target.value == "GRAPH_NODE"}
        indexed = {w.node_id for w in trace.writes if w.target.value == "VECTOR"}
        assert set(report.unindexed_node_ids) <= written
        assert indexed == set()


class TestWhenTheConversationCannotBeUpdated:
    def test_the_run_is_not_thrown_away_over_it(
        self, ops_store, graph_store, vector_store, embedder, full_run_providers,
        decayed_session, monkeypatch, caplog,
    ):
        # The entry is already in the graph by this point. Failing the run
        # over a status flag would discard finished work.
        def refuse(*args, **kwargs):
            raise RuntimeError("the store is busy")

        monkeypatch.setattr(ops_store.buffers, "mark_status", refuse)
        light, deep = full_run_providers()

        report = run_pipeline(
            decayed_session(),
            graph=graph_store,
            vectors=vector_store,
            embedder=embedder,
            lightweight=light,
            thinking=deep,
            ops=ops_store,
            config=AppConfig(),
        )

        assert report.episodes[0].nodes_written > 0
        assert "could not update the conversation" in caplog.text


class TestUndecidedItems:
    def test_they_reach_the_review_queue(self, run, decayed_session, ops_store):
        # Without this, every undecided item produced before the review
        # screen exists would be silently discarded — and those are exactly
        # the items the system was least sure about.
        report = run(decayed_session())

        assert sum(e.escalations for e in report.episodes) > 0
        assert ops_store.hitl.count_pending("local") > 0

    def test_an_episode_holding_one_is_marked_as_open(self, run, decayed_session):
        report = run(decayed_session())

        assert report.episodes[0].status is EpisodeRunStatus.SUSPENDED
