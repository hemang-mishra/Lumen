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


class TestFindingARunAtAll:
    def test_recent_runs_are_listed(self, api_client, a_processed_entry):
        # Every other endpoint here is keyed by a trace id, and until this
        # existed nothing in the system handed one out. A person looking at
        # a graph they do not recognise had no way in.
        body = api_client.get("/debug/traces").json()

        assert [run["trace_id"] for run in body["runs"]] == [a_processed_entry.trace_id]

    def test_a_listed_run_says_enough_to_pick_it_out(self, api_client, a_processed_entry):
        run = api_client.get("/debug/traces").json()["runs"][0]

        assert run["job_id"] == a_processed_entry.job_id
        assert run["session_id"] == a_processed_entry.session_id
        assert run["status"] == "COMPLETE"
        assert run["created_at"]

    def test_the_trace_it_names_can_then_be_fetched(self, api_client, a_processed_entry):
        trace_id = api_client.get("/debug/traces").json()["runs"][0]["trace_id"]

        assert api_client.get(f"/debug/traces/{trace_id}").status_code == 200

    def test_a_system_that_has_run_nothing_lists_nothing(self, api_client):
        assert api_client.get("/debug/traces").json() == {"runs": []}

    def test_the_list_can_be_kept_short(self, api_client, a_processed_entry):
        assert len(api_client.get("/debug/traces?limit=1").json()["runs"]) == 1

    def test_an_absurd_limit_is_refused(self, api_client):
        assert api_client.get("/debug/traces?limit=100000").status_code == 422


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


class TestReadingWhatWasActuallyWritten:
    """
    An episode keeps a summary and a hash of its text, never the text.

    That is right for a store of conclusions and useless to somebody checking
    one — a claim about a person's history is only reviewable next to the
    words it came from. The writing is still in the conversation the run
    processed, so this walks node to run to conversation to reach it.
    """

    def test_the_writing_behind_an_episode_comes_back(
        self, api_client, a_processed_entry
    ):
        from lumen.tests.conftest import EPISODE_TEXT

        episode_id = a_processed_entry.episodes[0].episode_id

        body = api_client.get(f"/debug/episodes/{episode_id}/source").json()

        assert [message["content"] for message in body["messages"]] == [EPISODE_TEXT]

    def test_it_says_which_conversation_and_run_it_came_from(
        self, api_client, a_processed_entry
    ):
        episode_id = a_processed_entry.episodes[0].episode_id

        body = api_client.get(f"/debug/episodes/{episode_id}/source").json()

        assert body["episode_id"] == episode_id
        assert body["session_id"] == a_processed_entry.session_id
        assert body["trace_id"] == a_processed_entry.trace_id

    def test_each_message_says_who_said_it_and_when(
        self, api_client, a_processed_entry
    ):
        """Half of a conversation read back without its speakers is not one."""
        episode_id = a_processed_entry.episodes[0].episode_id

        message = api_client.get(f"/debug/episodes/{episode_id}/source").json()[
            "messages"
        ][0]

        assert message["role"] == "USER"
        assert message["seq"] == 0
        assert message["timestamp"]

    def test_the_day_it_was_filed_under_is_included(
        self, api_client, a_processed_entry
    ):
        episode_id = a_processed_entry.episodes[0].episode_id

        body = api_client.get(f"/debug/episodes/{episode_id}/source").json()

        assert body["event_date"] == "2026-06-11"

    def test_an_episode_no_run_ever_produced_is_a_404(self, api_client):
        response = api_client.get("/debug/episodes/ep_never_read/source")

        assert response.status_code == 404
        assert response.json()["kind"] == "the run behind episode"

    def test_a_run_whose_conversation_is_gone_is_a_404(
        self, api_client, ops_store, a_processed_entry, monkeypatch
    ):
        """
        Erasure takes the writing away and leaves the run log behind. Asking
        for the words afterwards should say they are not there, not fail.
        """
        episode_id = a_processed_entry.episodes[0].episode_id
        monkeypatch.setattr(ops_store.buffers, "get_buffer", lambda _id: None)

        response = api_client.get(f"/debug/episodes/{episode_id}/source")

        assert response.status_code == 404
        assert response.json()["kind"] == "the conversation behind episode"
