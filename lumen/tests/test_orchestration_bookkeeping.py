"""
Tests for the record a run keeps of itself.

The write log is the most valuable thing here. A graph that has gone wrong
is only fixable if it is explainable, and this is what turns any node back
into the conversation that produced it. It is also what the index repair
reads, so a gap in it is a record that stays unfindable forever.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lumen.config import AppConfig
from lumen.operational.enums import JobStatus, StageStatus, WriteTarget
from lumen.pipeline.orchestration import bookkeeping
from lumen.pipeline.orchestration.contracts import CommitReport
from lumen.schemas.enums import PipelineStage

MOMENT = datetime(2026, 6, 11, 20, 0, tzinfo=UTC)


@pytest.fixture
def job(ops_store, buffer_with_messages, make_event):
    """A run already under way, for tests that start after that point."""
    event = make_event(
        [("USER", "something")], session_id=buffer_with_messages.session_id
    )
    return bookkeeping.open_job(event, ops=ops_store, config=AppConfig())


class TestStartingARun:
    def test_the_run_is_marked_as_under_way(self, job):
        assert job.status is JobStatus.RUNNING

    def test_the_settings_it_used_are_kept(self, job):
        # So a later re-run can say whether it reproduced the original
        # conditions or deliberately differed from them.
        assert "providers" in job.config_snapshot
        assert "pipeline" in job.config_snapshot

    def test_no_credential_can_reach_the_stored_settings(self, job):
        # Credentials are properties rather than stored fields, so turning
        # the settings into plain data simply does not see them.
        stored = str(job.config_snapshot).lower()

        assert "api_key" not in stored
        assert "secret" not in stored


class TestRecordingAStage:
    def test_a_finished_stage_is_closed_out_with_its_timings(self, ops_store, job):
        with bookkeeping.stage_span(
            ops_store, job.job_id, PipelineStage.STAGE_1_MICROEXTRACTION
        ) as span:
            span.model_used = "fake-thinker"
            span.validation_passed = True

        run = ops_store.jobs.get_stage_runs(job.job_id)[0]
        assert run.status is StageStatus.COMPLETE
        assert run.model_used == "fake-thinker"
        assert run.validation_passed is True
        assert run.duration_ms is not None

    def test_a_stage_that_blew_up_still_gets_a_row(self, ops_store, job):
        # A missing row and a failed one mean very different things when
        # reading a run back afterwards.
        with pytest.raises(RuntimeError):
            with bookkeeping.stage_span(
                ops_store, job.job_id, PipelineStage.STAGE_1_MICROEXTRACTION
            ):
                raise RuntimeError("the model timed out")

        run = ops_store.jobs.get_stage_runs(job.job_id)[0]
        assert run.status is StageStatus.FAILED
        assert "the model timed out" in run.error_message

    def test_a_deliberately_skipped_stage_says_so(self, ops_store, job):
        with bookkeeping.stage_span(
            ops_store, job.job_id, PipelineStage.STAGE_2_RETRIEVAL
        ) as span:
            span.skip()

        assert ops_store.jobs.get_stage_runs(job.job_id)[0].status is StageStatus.SKIPPED

    def test_each_episode_gets_its_own_row_for_the_same_stage(self, ops_store, job):
        # Four episodes running the same stage are four first attempts, not
        # one stage retried three times.
        for episode_id in ("ep_1", "ep_2", "ep_3"):
            with bookkeeping.stage_span(
                ops_store,
                job.job_id,
                PipelineStage.STAGE_1_MICROEXTRACTION,
                episode_id=episode_id,
            ):
                pass

        runs = ops_store.jobs.get_stage_runs(job.job_id)
        assert [run.episode_id for run in runs] == ["ep_1", "ep_2", "ep_3"]
        assert {run.attempt for run in runs} == {1}

    def test_running_the_same_episode_again_counts_as_a_second_attempt(
        self, ops_store, job
    ):
        for _ in range(2):
            with bookkeeping.stage_span(
                ops_store,
                job.job_id,
                PipelineStage.STAGE_1_MICROEXTRACTION,
                episode_id="ep_1",
            ):
                pass

        runs = ops_store.jobs.get_stage_runs(job.job_id)
        assert [run.attempt for run in runs] == [1, 2]


class TestTheCoreferenceMap:
    def test_it_is_stored_where_the_episode_points(
        self, ops_store, job, make_preprocessing
    ):
        preprocessing = make_preprocessing()

        map_id = bookkeeping.save_coreference_map(
            preprocessing.coreference_map,
            ops=ops_store,
            job=job,
            entry_id="entry_1",
        )

        stored = ops_store.coref.get(map_id)
        assert stored is not None
        assert stored.resolved_entities[0]["resolved_to"] == "Alex"

    def test_saving_it_twice_replaces_rather_than_fails(
        self, ops_store, job, make_preprocessing
    ):
        # Re-running an entry reads the same pronouns the same way. A run
        # should not fail because it already succeeded once.
        preprocessing = make_preprocessing()
        args = dict(ops=ops_store, job=job, entry_id="entry_1")

        bookkeeping.save_coreference_map(preprocessing.coreference_map, **args)
        map_id = bookkeeping.save_coreference_map(preprocessing.coreference_map, **args)

        assert ops_store.coref.get(map_id) is not None

    def test_an_unknown_map_reads_back_as_nothing(self, ops_store):
        assert ops_store.coref.get("coref_nope") is None


class TestTheWriteLog:
    def test_every_record_link_and_search_entry_is_logged(self, ops_store, job):
        report = CommitReport(
            nodes_written=["ep_1", "obs_1"],
            edges_written=[("contains_obs", "ep_1", "obs_1")],
            vectors_written=["obs_1"],
        )

        bookkeeping.record_commit(
            report, ops=ops_store, job_id=job.job_id, episode_id="ep_1"
        )

        writes = ops_store.jobs.get_trace(job.trace_id).writes
        assert sum(1 for w in writes if w.target is WriteTarget.GRAPH_NODE) == 2
        assert sum(1 for w in writes if w.target is WriteTarget.GRAPH_EDGE) == 1
        assert sum(1 for w in writes if w.target is WriteTarget.VECTOR) == 1

    def test_each_write_names_the_episode_that_produced_it(self, ops_store, job):
        bookkeeping.record_commit(
            CommitReport(nodes_written=["obs_1"]),
            ops=ops_store,
            job_id=job.job_id,
            episode_id="ep_7",
        )

        assert ops_store.jobs.get_trace(job.trace_id).writes[0].episode_id == "ep_7"

    def test_any_record_can_be_traced_back_to_its_run(self, ops_store, job):
        # The whole point of the log: a year later, a node in the graph can
        # still be explained.
        bookkeeping.record_commit(
            CommitReport(nodes_written=["obs_1"]),
            ops=ops_store,
            job_id=job.job_id,
            episode_id="ep_1",
        )

        assert ops_store.jobs.find_job_for_node("obs_1").job_id == job.job_id


class TestTheReviewQueue:
    def test_undecided_items_are_put_in_front_of_the_person(
        self, ops_store, job, reconciliation_outcome
    ):
        added = bookkeeping.queue_escalations(
            reconciliation_outcome, ops=ops_store, job=job
        )

        assert added == 1
        assert ops_store.hitl.count_pending("local") == 1

    def test_a_queued_item_carries_what_was_being_decided(
        self, ops_store, job, reconciliation_outcome
    ):
        bookkeeping.queue_escalations(reconciliation_outcome, ops=ops_store, job=job)

        item = ops_store.hitl.list_pending("local")[0]
        assert item.observation_id == "obs_new_1"
        assert item.episode_id == "ep_new"
        assert item.recommended_action.value == "BRANCH"
        assert item.context_summary

    def test_running_the_entry_again_does_not_ask_twice(
        self, ops_store, job, reconciliation_outcome
    ):
        # Asking the person the same question twice is worse than not
        # asking at all.
        bookkeeping.queue_escalations(reconciliation_outcome, ops=ops_store, job=job)

        added = bookkeeping.queue_escalations(
            reconciliation_outcome, ops=ops_store, job=job
        )

        assert added == 0
        assert ops_store.hitl.count_pending("local") == 1

    def test_an_entry_with_nothing_outstanding_queues_nothing(
        self, ops_store, job, reconciliation_outcome
    ):
        settled = reconciliation_outcome.model_copy(update={"escalations": []})

        assert bookkeeping.queue_escalations(settled, ops=ops_store, job=job) == 0


class TestClosingARun:
    def test_a_clean_run_is_complete(self, ops_store, job):
        status = bookkeeping.close_job(
            ops=ops_store, job_id=job.job_id, failed_episodes=0, unindexed=0
        )

        assert status is JobStatus.COMPLETE

    def test_a_lost_episode_fails_the_run(self, ops_store, job):
        status = bookkeeping.close_job(
            ops=ops_store, job_id=job.job_id, failed_episodes=1, unindexed=0
        )

        assert status is JobStatus.FAILED

    def test_a_record_that_cannot_be_found_also_fails_the_run(self, ops_store, job):
        # Those records are real, correct and invisible. A run that reported
        # success would leave nobody with a reason to look.
        status = bookkeeping.close_job(
            ops=ops_store, job_id=job.job_id, failed_episodes=0, unindexed=2
        )

        assert status is JobStatus.FAILED
        assert "not searchable" in ops_store.jobs.get_job(job.job_id).error_message
