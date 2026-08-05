"""Tests for pipeline run tracking."""

from __future__ import annotations

import pytest

from lumen.operational.enums import JobStatus, PipelineStage, StageStatus, WriteTarget
from lumen.operational.repositories import (
    IllegalStateTransitionError,
    RecordNotFoundError,
)
from lumen.operational.schemas import StageMetrics, WriteLogEntry


@pytest.fixture
def job(ops_store, buffer_with_messages):
    """A pending job for the sample buffer."""
    return ops_store.jobs.create_job(
        session_id=buffer_with_messages.session_id,
        user_id="local",
        config_snapshot={"thinking_model": "gemini-2.5-pro"},
    )


class TestCreateJob:
    def test_a_new_job_starts_pending(self, job):
        assert job.status == JobStatus.PENDING
        assert job.started_at is None
        assert job.retry_count == 0

    def test_the_job_id_is_readable(self, job):
        assert job.job_id.startswith("job_")

    def test_the_configuration_is_remembered(self, ops_store, job):
        """
        A re-run can then reproduce the original conditions, or deliberately
        differ from them, and it stays clear which happened.
        """
        stored = ops_store.jobs.get_job(job.job_id)
        assert stored.config_snapshot == {"thinking_model": "gemini-2.5-pro"}

    def test_it_adopts_the_current_trace_id(self, ops_store, buffer_with_messages, bound_trace):
        created = ops_store.jobs.create_job(buffer_with_messages.session_id, "local")
        assert created.trace_id == bound_trace

    def test_an_explicit_trace_id_wins(self, ops_store, buffer_with_messages, bound_trace):
        created = ops_store.jobs.create_job(
            buffer_with_messages.session_id, "local", trace_id="explicit"
        )
        assert created.trace_id == "explicit"

    def test_it_gets_a_trace_id_even_outside_a_run(self, ops_store, buffer_with_messages):
        created = ops_store.jobs.create_job(buffer_with_messages.session_id, "local")
        assert created.trace_id

    def test_an_unknown_job_reads_back_as_nothing(self, ops_store):
        assert ops_store.jobs.get_job("no-such-job") is None


class TestJobTransitions:
    def test_the_ordinary_path_works(self, ops_store, job):
        running = ops_store.jobs.transition(job.job_id, JobStatus.RUNNING)
        assert running.status == JobStatus.RUNNING
        assert running.started_at is not None

        done = ops_store.jobs.transition(job.job_id, JobStatus.COMPLETE)
        assert done.status == JobStatus.COMPLETE
        assert done.finished_at is not None

    def test_a_failure_records_why(self, ops_store, job):
        ops_store.jobs.transition(job.job_id, JobStatus.RUNNING)
        failed = ops_store.jobs.transition(
            job.job_id,
            JobStatus.FAILED,
            error_type="TimeoutError",
            error_message="the model did not respond",
        )
        assert failed.error_type == "TimeoutError"
        assert failed.error_message == "the model did not respond"

    def test_a_job_can_be_cancelled_before_it_starts(self, ops_store, job):
        cancelled = ops_store.jobs.transition(job.job_id, JobStatus.CANCELLED)
        assert cancelled.status == JobStatus.CANCELLED

    def test_skipping_straight_to_complete_is_refused(self, ops_store, job):
        """
        A job that jumps from pending to complete claims to have produced work
        it never did.
        """
        with pytest.raises(IllegalStateTransitionError, match="PENDING to COMPLETE"):
            ops_store.jobs.transition(job.job_id, JobStatus.COMPLETE)

    def test_a_finished_job_cannot_move_again(self, ops_store, job):
        ops_store.jobs.transition(job.job_id, JobStatus.RUNNING)
        ops_store.jobs.transition(job.job_id, JobStatus.COMPLETE)
        with pytest.raises(IllegalStateTransitionError):
            ops_store.jobs.transition(job.job_id, JobStatus.RUNNING)

    def test_a_cancelled_job_cannot_move_again(self, ops_store, job):
        ops_store.jobs.transition(job.job_id, JobStatus.CANCELLED)
        with pytest.raises(IllegalStateTransitionError):
            ops_store.jobs.transition(job.job_id, JobStatus.RUNNING)

    def test_transitioning_an_unknown_job_is_refused(self, ops_store):
        with pytest.raises(RecordNotFoundError):
            ops_store.jobs.transition("ghost", JobStatus.RUNNING)


class TestReruns:
    def test_a_failed_job_can_be_started_again(self, ops_store, job):
        ops_store.jobs.transition(job.job_id, JobStatus.RUNNING)
        ops_store.jobs.transition(job.job_id, JobStatus.FAILED, error_type="ValueError")
        restarted = ops_store.jobs.transition(job.job_id, JobStatus.RUNNING)
        assert restarted.status == JobStatus.RUNNING

    def test_each_rerun_is_counted(self, ops_store, job):
        """
        Three quiet retries look very different from one attempt, so the count
        has to be visible.
        """
        ops_store.jobs.transition(job.job_id, JobStatus.RUNNING)
        for _ in range(3):
            ops_store.jobs.transition(job.job_id, JobStatus.FAILED)
            ops_store.jobs.transition(job.job_id, JobStatus.RUNNING)

        assert ops_store.jobs.get_job(job.job_id).retry_count == 3

    def test_restarting_clears_the_previous_error(self, ops_store, job):
        ops_store.jobs.transition(job.job_id, JobStatus.RUNNING)
        ops_store.jobs.transition(
            job.job_id, JobStatus.FAILED, error_type="ValueError", error_message="bad"
        )
        restarted = ops_store.jobs.transition(job.job_id, JobStatus.RUNNING)
        assert restarted.error_type is None
        assert restarted.error_message is None


class TestStageRuns:
    def test_a_stage_records_what_went_into_it(self, ops_store, job):
        run = ops_store.jobs.start_stage(
            job.job_id,
            PipelineStage.STAGE_1_MICROEXTRACTION,
            input_payload={"episode_id": "ep_2026_06_11_001"},
        )
        assert run.status == StageStatus.RUNNING
        assert run.input_payload == {"episode_id": "ep_2026_06_11_001"}
        assert run.attempt == 1

    def test_the_job_shows_where_it_is(self, ops_store, job):
        ops_store.jobs.start_stage(job.job_id, PipelineStage.STAGE_2_RETRIEVAL)
        assert (
            ops_store.jobs.get_job(job.job_id).current_stage
            == PipelineStage.STAGE_2_RETRIEVAL
        )

    def test_finishing_records_how_it_went(self, ops_store, job):
        run = ops_store.jobs.start_stage(job.job_id, PipelineStage.STAGE_1_MICROEXTRACTION)
        finished = ops_store.jobs.finish_stage(
            run.id,
            StageStatus.COMPLETE,
            metrics=StageMetrics(
                duration_ms=1420,
                model_used="gemini-2.5-pro",
                validation_passed=True,
                retry_count=0,
            ),
            output_payload={"observations": 4},
        )

        assert finished.status == StageStatus.COMPLETE
        assert finished.duration_ms == 1420
        assert finished.model_used == "gemini-2.5-pro"
        assert finished.validation_passed is True
        assert finished.output_payload == {"observations": 4}

    def test_the_duration_is_worked_out_when_not_given(self, ops_store, job):
        run = ops_store.jobs.start_stage(job.job_id, PipelineStage.STAGE_0_PREPROCESSING)
        finished = ops_store.jobs.finish_stage(run.id, StageStatus.COMPLETE)
        assert finished.duration_ms is not None
        assert finished.duration_ms >= 0

    def test_a_failing_stage_records_the_reason(self, ops_store, job):
        run = ops_store.jobs.start_stage(job.job_id, PipelineStage.STAGE_1_MICROEXTRACTION)
        finished = ops_store.jobs.finish_stage(
            run.id, StageStatus.FAILED, error_message="the output would not validate"
        )
        assert finished.status == StageStatus.FAILED
        assert finished.error_message == "the output would not validate"

    def test_retrying_a_stage_gets_its_own_row(self, ops_store, job):
        """
        Keeping each attempt separately is what makes it possible to see that
        a stage succeeded only on its third try.
        """
        first = ops_store.jobs.start_stage(job.job_id, PipelineStage.STAGE_1_MICROEXTRACTION)
        ops_store.jobs.finish_stage(first.id, StageStatus.FAILED)
        second = ops_store.jobs.start_stage(job.job_id, PipelineStage.STAGE_1_MICROEXTRACTION)

        assert second.attempt == 2
        assert first.id != second.id
        assert len(ops_store.jobs.get_stage_runs(job.job_id)) == 2

    def test_attempts_are_counted_per_stage(self, ops_store, job):
        ops_store.jobs.start_stage(job.job_id, PipelineStage.STAGE_0_PREPROCESSING)
        run = ops_store.jobs.start_stage(job.job_id, PipelineStage.STAGE_1_MICROEXTRACTION)
        assert run.attempt == 1

    def test_stages_come_back_in_order(self, ops_store, job):
        for stage in (
            PipelineStage.STAGE_0_PREPROCESSING,
            PipelineStage.STAGE_1_MICROEXTRACTION,
            PipelineStage.STAGE_2_RETRIEVAL,
        ):
            ops_store.jobs.start_stage(job.job_id, stage)

        runs = ops_store.jobs.get_stage_runs(job.job_id)
        assert [r.stage for r in runs] == [
            PipelineStage.STAGE_0_PREPROCESSING,
            PipelineStage.STAGE_1_MICROEXTRACTION,
            PipelineStage.STAGE_2_RETRIEVAL,
        ]

    def test_starting_a_stage_on_an_unknown_job_is_refused(self, ops_store):
        with pytest.raises(RecordNotFoundError):
            ops_store.jobs.start_stage("ghost", PipelineStage.STAGE_0_PREPROCESSING)

    def test_finishing_an_unknown_stage_is_refused(self, ops_store):
        with pytest.raises(RecordNotFoundError, match="no stage run"):
            ops_store.jobs.finish_stage(9999, StageStatus.COMPLETE)


class TestWriteLog:
    def test_a_node_write_is_recorded(self, ops_store, job):
        ops_store.jobs.record_write(
            job.job_id,
            PipelineStage.STAGE_4_GRAPH_WRITE,
            WriteTarget.GRAPH_NODE,
            node_id="pat_decision_saturation",
        )
        trace = ops_store.jobs.get_trace(job.trace_id)
        assert [w.node_id for w in trace.writes] == ["pat_decision_saturation"]

    def test_an_edge_write_is_recorded(self, ops_store, job):
        ops_store.jobs.record_write(
            job.job_id,
            PipelineStage.STAGE_4_GRAPH_WRITE,
            WriteTarget.GRAPH_EDGE,
            edge_type="reinforces_obs_pat",
            from_id="obs_2026_06_11_004",
            to_id="pat_decision_saturation",
        )
        write = ops_store.jobs.get_trace(job.trace_id).writes[0]
        assert write.edge_type == "reinforces_obs_pat"
        assert write.from_id == "obs_2026_06_11_004"

    def test_an_edge_without_endpoints_is_refused(self, ops_store, job):
        """An entry that names nothing records nothing useful."""
        with pytest.raises(ValueError, match="edge write must record"):
            ops_store.jobs.record_write(
                job.job_id,
                PipelineStage.STAGE_4_GRAPH_WRITE,
                WriteTarget.GRAPH_EDGE,
                edge_type="reinforces_obs_pat",
            )

    def test_a_node_write_without_a_node_is_refused(self, ops_store, job):
        with pytest.raises(ValueError, match="must record a node_id"):
            ops_store.jobs.record_write(
                job.job_id, PipelineStage.STAGE_4_GRAPH_WRITE, WriteTarget.GRAPH_NODE
            )

    def test_writing_against_an_unknown_job_is_refused(self, ops_store):
        with pytest.raises(RecordNotFoundError):
            ops_store.jobs.record_write(
                "ghost", PipelineStage.STAGE_4_GRAPH_WRITE,
                WriteTarget.GRAPH_NODE, node_id="pat_x",
            )


class TestTraceReconstruction:
    def test_a_whole_run_can_be_assembled(self, ops_store, job):
        ops_store.jobs.transition(job.job_id, JobStatus.RUNNING)
        run = ops_store.jobs.start_stage(job.job_id, PipelineStage.STAGE_1_MICROEXTRACTION)
        ops_store.jobs.finish_stage(run.id, StageStatus.COMPLETE)
        ops_store.jobs.record_write(
            job.job_id, PipelineStage.STAGE_4_GRAPH_WRITE,
            WriteTarget.GRAPH_NODE, node_id="obs_2026_06_11_004",
        )

        trace = ops_store.jobs.get_trace(job.trace_id)
        assert trace.job.job_id == job.job_id
        assert len(trace.stage_runs) == 1
        assert len(trace.writes) == 1

    def test_an_unknown_trace_reads_back_as_nothing(self, ops_store):
        assert ops_store.jobs.get_trace("no-such-trace") is None

    def test_a_node_can_be_traced_back_to_its_run(self, ops_store, job):
        """
        This is what replaces storing a trace id on every node in the graph:
        given a node, the run that made it is still one lookup away.
        """
        ops_store.jobs.record_write(
            job.job_id, PipelineStage.STAGE_4_GRAPH_WRITE,
            WriteTarget.GRAPH_NODE, node_id="bel_solitude_decision_v2",
        )
        found = ops_store.jobs.find_job_for_node("bel_solitude_decision_v2")
        assert found.job_id == job.job_id
        assert found.trace_id == job.trace_id

    def test_an_unwritten_node_has_no_run(self, ops_store):
        assert ops_store.jobs.find_job_for_node("pat_never_written") is None


class TestWriteLogEntryValidation:
    def test_a_vector_write_needs_a_node(self):
        with pytest.raises(ValueError, match="must record a node_id"):
            WriteLogEntry(
                job_id="job_1", trace_id="t", stage=PipelineStage.STAGE_4_GRAPH_WRITE,
                target=WriteTarget.VECTOR,
            )

    def test_a_valid_vector_write_is_accepted(self):
        entry = WriteLogEntry(
            job_id="job_1", trace_id="t", stage=PipelineStage.STAGE_4_GRAPH_WRITE,
            target=WriteTarget.VECTOR, node_id="obs_1",
        )
        assert entry.node_id == "obs_1"
