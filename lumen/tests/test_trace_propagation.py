"""
End-to-end test of trace ids and transactions.

A trace id is only worth having if one id genuinely reaches everything a run
produces. These tests drive a stand-in pipeline and check that it does.
"""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime

import pytest

from lumen.observability.trace import bind_trace, get_trace_id, span
from lumen.operational.enums import (
    BufferStatus,
    JobStatus,
    PipelineStage,
    StageStatus,
    WriteTarget,
)
from lumen.operational.schemas import BufferMessageRecord, StageMetrics
from lumen.schemas.pipeline import ExtractionResult, PreprocessingResult
from lumen.schemas.enums import QualityGateDecision
from lumen.schemas.pipeline import CoreferenceMap

TODAY = date(2026, 6, 11)


def _run_mock_pipeline(store, session_id: str, user_id: str = "local") -> str:
    """
    Drive a session through three stages, the way the real orchestrator will.

    Nothing here calls a model — the point is to check that the trace id and
    the recorded history come out right, which does not need real extraction.
    """
    job = store.jobs.create_job(session_id=session_id, user_id=user_id)
    store.jobs.transition(job.job_id, JobStatus.RUNNING)

    for stage in (
        PipelineStage.STAGE_0_PREPROCESSING,
        PipelineStage.STAGE_1_MICROEXTRACTION,
        PipelineStage.STAGE_2_RETRIEVAL,
    ):
        with span(stage.value, stage=stage.value) as fields:
            run = store.jobs.start_stage(job.job_id, stage, input_payload={"n": 1})
            fields["model_used"] = "gemini-2.5-flash"
            store.jobs.finish_stage(
                run.id,
                StageStatus.COMPLETE,
                metrics=StageMetrics(
                    model_used="gemini-2.5-flash", validation_passed=True
                ),
                output_payload={"ok": True},
            )

    store.jobs.record_write(
        job.job_id,
        PipelineStage.STAGE_4_GRAPH_WRITE,
        WriteTarget.GRAPH_NODE,
        node_id="obs_2026_06_11_004",
    )
    store.jobs.transition(job.job_id, JobStatus.COMPLETE)
    return job.job_id


class TestTraceReachesEverything:
    def test_one_id_covers_the_whole_run(self, ops_store, buffer_with_messages, captured_logs):
        with bind_trace("run-alpha"):
            job_id = _run_mock_pipeline(ops_store, buffer_with_messages.session_id)

        trace = ops_store.jobs.get_trace("run-alpha")
        assert trace is not None
        assert trace.job.job_id == job_id
        assert len(trace.stage_runs) == 3
        assert len(trace.writes) == 1

    def test_every_log_line_carries_the_id(self, ops_store, buffer_with_messages, captured_logs):
        with bind_trace("run-alpha"):
            _run_mock_pipeline(ops_store, buffer_with_messages.session_id)

        during_run = [entry for entry in captured_logs if entry["trace_id"] is not None]
        assert during_run, "the run produced no traced log lines"
        assert {entry["trace_id"] for entry in during_run} == {"run-alpha"}

    def test_every_stored_row_carries_the_id(self, ops_store, buffer_with_messages):
        with bind_trace("run-alpha"):
            _run_mock_pipeline(ops_store, buffer_with_messages.session_id)

        trace = ops_store.jobs.get_trace("run-alpha")
        assert trace.job.trace_id == "run-alpha"
        assert {run.trace_id for run in trace.stage_runs} == {"run-alpha"}
        assert {write.trace_id for write in trace.writes} == {"run-alpha"}

    def test_models_built_during_the_run_carry_the_id(self, ops_store, buffer_with_messages):
        """
        Stage results pick the id up on their own, so no stage has to remember
        to pass it along.
        """
        with bind_trace("run-alpha"):
            decay_event = ops_store.buffers.build_decay_event(
                buffer_with_messages.session_id
            )
            preprocessing = PreprocessingResult(
                session_id=buffer_with_messages.session_id,
                coreference_map=CoreferenceMap(entry_id="entry_1"),
                quality_gate_decision=QualityGateDecision.REFLECTION,
                processing_time_ms=120,
            )
            extraction = ExtractionResult(
                episode_id="ep_2026_06_11_001",
                extraction_model="gemini-2.5-pro",
                validation_passed=True,
            )

        assert decay_event.trace_id == "run-alpha"
        assert preprocessing.trace_id == "run-alpha"
        assert extraction.trace_id == "run-alpha"

    def test_the_stage_history_is_readable_afterwards(self, ops_store, buffer_with_messages):
        with bind_trace("run-alpha"):
            _run_mock_pipeline(ops_store, buffer_with_messages.session_id)

        trace = ops_store.jobs.get_trace("run-alpha")
        assert [run.stage for run in trace.stage_runs] == [
            PipelineStage.STAGE_0_PREPROCESSING,
            PipelineStage.STAGE_1_MICROEXTRACTION,
            PipelineStage.STAGE_2_RETRIEVAL,
        ]
        assert all(run.model_used == "gemini-2.5-flash" for run in trace.stage_runs)
        assert all(run.validation_passed for run in trace.stage_runs)
        assert all(run.duration_ms is not None for run in trace.stage_runs)

    def test_a_node_leads_back_to_the_conversation_it_came_from(
        self, ops_store, buffer_with_messages
    ):
        with bind_trace("run-alpha"):
            _run_mock_pipeline(ops_store, buffer_with_messages.session_id)

        job = ops_store.jobs.find_job_for_node("obs_2026_06_11_004")
        assert job.session_id == buffer_with_messages.session_id

        messages = ops_store.buffers.get_messages(job.session_id)
        assert "second-guessing the architecture call" in messages[0].content


class TestConcurrentRuns:
    def test_two_runs_at_once_stay_separate(self, ops_store):
        """
        The whole idea falls apart if one run can see another's id, so this is
        checked with two real threads rather than by reasoning about it.
        """
        results: dict[str, str] = {}
        errors: list[Exception] = []
        ready = threading.Barrier(2)

        def worker(name: str) -> None:
            try:
                buffer = ops_store.buffers.find_or_create("local", TODAY, name)
                ops_store.buffers.append_message(
                    buffer.session_id,
                    BufferMessageRecord(
                        message_id=f"{name}_msg",
                        session_id=buffer.session_id,
                        seq=0,
                        role="USER",
                        content="something happened",
                        timestamp=datetime.now(UTC),
                        event_date=TODAY,
                    ),
                )
                with bind_trace(f"run-{name}"):
                    ready.wait(timeout=5)
                    job_id = _run_mock_pipeline(ops_store, buffer.session_id)
                    results[name] = job_id
            except Exception as exc:
                errors.append(exc)
                ready.abort()

        threads = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert not errors, f"a worker failed: {errors}"

        trace_a = ops_store.jobs.get_trace("run-a")
        trace_b = ops_store.jobs.get_trace("run-b")
        assert trace_a.job.job_id == results["a"]
        assert trace_b.job.job_id == results["b"]
        # Neither run picked up anything belonging to the other.
        assert len(trace_a.stage_runs) == 3
        assert len(trace_b.stage_runs) == 3
        assert trace_a.job.session_id != trace_b.job.session_id


class TestTransactions:
    def test_grouped_writes_all_land_together(self, ops_store, buffer_with_messages):
        with ops_store.transaction():
            job = ops_store.jobs.create_job(buffer_with_messages.session_id, "local")
            ops_store.jobs.transition(job.job_id, JobStatus.RUNNING)
            ops_store.buffers.mark_status(
                buffer_with_messages.session_id, BufferStatus.DISPATCHED
            )

        assert ops_store.jobs.get_job(job.job_id).status == JobStatus.RUNNING
        assert (
            ops_store.buffers.get_buffer(buffer_with_messages.session_id).status
            == BufferStatus.DISPATCHED
        )

    def test_a_failure_undoes_the_whole_group(self, ops_store, buffer_with_messages):
        """
        A run must not record that it wrote a node while failing to record the
        edge that gives the node meaning.
        """
        with pytest.raises(RuntimeError):
            with ops_store.transaction():
                job = ops_store.jobs.create_job(buffer_with_messages.session_id, "local")
                ops_store.jobs.record_write(
                    job.job_id,
                    PipelineStage.STAGE_4_GRAPH_WRITE,
                    WriteTarget.GRAPH_NODE,
                    node_id="obs_rolled_back",
                )
                raise RuntimeError("the graph write failed")

        assert ops_store.jobs.find_job_for_node("obs_rolled_back") is None
        assert ops_store.buffers.get_buffer(buffer_with_messages.session_id) is not None

    def test_nesting_leaves_the_outer_group_in_charge(self, ops_store, buffer_with_messages):
        with pytest.raises(RuntimeError):
            with ops_store.transaction():
                job = ops_store.jobs.create_job(buffer_with_messages.session_id, "local")
                with ops_store.transaction():
                    ops_store.jobs.transition(job.job_id, JobStatus.RUNNING)
                raise RuntimeError("the outer block failed")

        assert ops_store.jobs.get_job(job.job_id) is None


class TestUntracedWork:
    def test_work_outside_a_run_still_functions(self, ops_store, buffer_with_messages):
        """
        Not everything happens inside a pipeline run. Those paths must keep
        working, just without a trace id.
        """
        assert get_trace_id() is None
        job = ops_store.jobs.create_job(buffer_with_messages.session_id, "local")
        assert job.trace_id

    def test_models_built_outside_a_run_have_no_trace_id(self, ops_store, buffer_with_messages):
        assert get_trace_id() is None
        event = ops_store.buffers.build_decay_event(buffer_with_messages.session_id)
        assert event.trace_id is None
