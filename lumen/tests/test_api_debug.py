"""
Tests for the endpoints that explain a past run.

These are the two questions every complaint about the graph starts with:
what happened during that run, and where did this particular record come
from. Both are answered from the run log rather than from the graph, which
is why a trace identifier never had to be stored on every node.
"""

from __future__ import annotations

import pytest

from lumen.config import AppConfig
from lumen.pipeline.orchestration import run_pipeline


@pytest.fixture
def a_processed_entry(
    ops_store, graph_store, vector_store, embedder, full_run_providers, decayed_session
):
    """Run one real journal entry all the way through."""
    light, deep = full_run_providers()
    return run_pipeline(
        decayed_session(),
        graph=graph_store,
        vectors=vector_store,
        embedder=embedder,
        lightweight=light,
        thinking=deep,
        ops=ops_store,
        config=AppConfig(),
    )


@pytest.fixture
def a_written_node(ops_store, a_processed_entry) -> str:
    trace = ops_store.jobs.get_trace(a_processed_entry.trace_id)
    return next(
        write.node_id
        for write in trace.writes
        if write.target.value == "GRAPH_NODE" and write.node_id
    )


class TestTheRunTrace:
    def test_every_stage_comes_back_in_order(self, api_client, a_processed_entry):
        body = api_client.get(f"/debug/traces/{a_processed_entry.trace_id}").json()

        assert [run["stage"] for run in body["stage_runs"]] == [
            "STAGE_0_PREPROCESSING",
            "STAGE_1_MICROEXTRACTION",
            "STAGE_2_RETRIEVAL",
            "STAGE_3_RECONCILIATION",
            "STAGE_4_GRAPH_WRITE",
        ]

    def test_each_stage_says_how_long_it_took_and_which_model(
        self, api_client, a_processed_entry
    ):
        body = api_client.get(f"/debug/traces/{a_processed_entry.trace_id}").json()

        assert all(run["duration_ms"] is not None for run in body["stage_runs"])
        reading = next(
            r for r in body["stage_runs"] if r["stage"] == "STAGE_1_MICROEXTRACTION"
        )
        assert reading["model_used"]

    def test_what_went_into_and_came_out_of_a_stage_is_kept(
        self, api_client, a_processed_entry
    ):
        # What makes a stage explainable after the fact rather than only
        # countable.
        body = api_client.get(f"/debug/traces/{a_processed_entry.trace_id}").json()

        reading = next(
            r for r in body["stage_runs"] if r["stage"] == "STAGE_1_MICROEXTRACTION"
        )
        assert reading["input_payload"]
        assert reading["output_payload"]

    def test_everything_the_run_wrote_is_listed(self, api_client, a_processed_entry):
        body = api_client.get(f"/debug/traces/{a_processed_entry.trace_id}").json()

        targets = {write["target"] for write in body["writes"]}
        assert {"GRAPH_NODE", "GRAPH_EDGE", "VECTOR"} <= targets

    def test_the_job_itself_is_included(self, api_client, a_processed_entry):
        body = api_client.get(f"/debug/traces/{a_processed_entry.trace_id}").json()

        assert body["job"]["job_id"] == a_processed_entry.job_id
        assert body["job"]["session_id"] == a_processed_entry.session_id

    def test_a_run_that_never_happened_is_a_404(self, api_client):
        response = api_client.get("/debug/traces/no-such-trace")

        assert response.status_code == 404
        assert response.json()["kind"] == "trace"


class TestWhereARecordCameFrom:
    def test_a_record_leads_back_to_its_run_and_conversation(
        self, api_client, a_written_node, a_processed_entry
    ):
        # Without this, a node in the graph is a claim with no way back to
        # the writing that produced it.
        body = api_client.get(f"/debug/nodes/{a_written_node}/provenance").json()

        assert body["node_id"] == a_written_node
        assert body["job_id"] == a_processed_entry.job_id
        assert body["trace_id"] == a_processed_entry.trace_id
        assert body["session_id"] == a_processed_entry.session_id

    def test_it_names_the_piece_of_writing_too(
        self, api_client, a_written_node, a_processed_entry
    ):
        body = api_client.get(f"/debug/nodes/{a_written_node}/provenance").json()

        assert body["episode_id"] == a_processed_entry.episodes[0].episode_id

    def test_it_says_when_the_record_was_saved(self, api_client, a_written_node):
        body = api_client.get(f"/debug/nodes/{a_written_node}/provenance").json()

        assert body["written_at"]

    def test_a_record_nothing_ever_wrote_is_a_404(self, api_client):
        response = api_client.get("/debug/nodes/never_written/provenance")

        assert response.status_code == 404
        assert response.json()["kind"] == "provenance for node"
