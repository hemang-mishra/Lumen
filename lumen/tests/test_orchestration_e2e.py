"""
One real journal entry, all the way from a text file to a saved graph.

Everything here reads the databases back rather than trusting the run
report. The report says what the code believes it did; these tests check
what is actually there, which is the only claim that matters after five
goals of stages that had never met.
"""

from __future__ import annotations

import json

import pytest

from lumen.config import AppConfig
from lumen.operational.enums import JobStatus, WriteTarget
from lumen.pipeline.orchestration import run_pipeline
from lumen.schemas.enums import EpisodeRunStatus

ENTRY = """\
I went to the cafe alone today and ate there without the usual dread.
Then I saw what Alex had shipped this week and felt small and behind.
I sat with it for a while and the pressure lifted on its own.
I think the comparing is the thing that hurts, not the gap itself.
"""


@pytest.fixture
def entry_file(tmp_path):
    """The entry as a file on disk, the way a person would import one."""
    path = tmp_path / "2026-06-11.txt"
    path.write_text(ENTRY, encoding="utf-8")
    return path


@pytest.fixture
def report(
    entry_file, ops_store, graph_store, vector_store, embedder, full_run_providers,
    decayed_session,
):
    """Run the whole pipeline once, over the contents of that file."""
    text = entry_file.read_text(encoding="utf-8").strip()
    light, deep = full_run_providers(
        {
            "normalize_text": json.dumps(
                {
                    "cleaned_text": text.replace("\n", " "),
                    "detected_languages": ["en"],
                    "translated": False,
                }
            )
        }
    )
    return run_pipeline(
        decayed_session(text),
        graph=graph_store,
        vectors=vector_store,
        embedder=embedder,
        lightweight=light,
        thinking=deep,
        ops=ops_store,
        config=AppConfig(),
    )


@pytest.fixture
def episode_id(report):
    return report.episodes[0].episode_id


class TestTheRunItself:
    def test_it_finishes(self, report):
        assert report.job_status == JobStatus.COMPLETE.value
        assert len(report.episodes) == 1

    def test_the_episode_was_saved(self, report):
        assert report.episodes[0].status in (
            EpisodeRunStatus.COMPLETE,
            EpisodeRunStatus.SUSPENDED,
        )


class TestWhatIsInTheGraph:
    def test_the_episode_record_exists(self, graph_store, episode_id, report):
        # Nothing before this goal had ever created one, and everything
        # extracted hangs off it.
        stored = graph_store.get_node(episode_id)

        assert stored is not None
        assert stored["episode_summary"]
        assert stored["entry_id"] == report.session_id

    def test_the_episode_points_at_a_coreference_map_that_exists(
        self, graph_store, ops_store, episode_id
    ):
        # The pointer used to lead nowhere. Now it resolves.
        map_id = graph_store.get_node(episode_id)["coreference_map_id"]

        assert ops_store.coref.get(map_id) is not None

    def test_the_findings_are_there(self, graph_store, ops_store, report):
        written = _written_nodes(ops_store, report)

        assert any(node_id.startswith("obs_") for node_id in written)
        assert all(graph_store.get_node(node_id) is not None for node_id in written)

    def test_the_findings_are_linked_to_their_episode(self, ops_store, report):
        links = _written_edges(ops_store, report)

        assert any(table == "contains_obs" for table, _, _ in links)

    def test_the_reflection_that_anchors_a_change_is_there(self, ops_store, report):
        written = _written_nodes(ops_store, report)

        assert any(node_id.startswith("sess_") for node_id in written)

    def test_every_decision_left_a_note(self, graph_store, ops_store, report):
        # A decision with no note is a change to somebody's history that
        # nobody can explain or undo.
        notes = [n for n in _written_nodes(ops_store, report) if n.startswith("d_")]

        assert notes
        for note in notes:
            assert graph_store.get_node(note) is not None


class TestWhatIsInTheSearchIndex:
    def test_the_findings_can_be_found_by_meaning(
        self, vector_store, embedder, report
    ):
        hits = vector_store.hybrid_search(
            embedder.embed_text("comparing myself to other people"), limit=10
        )

        assert hits

    def test_every_indexed_record_is_a_real_record(
        self, graph_store, vector_store, embedder, report
    ):
        # A search result that cannot be read back is worse than no result.
        hits = vector_store.hybrid_search(embedder.embed_text("anything"), limit=20)

        for hit in hits:
            assert graph_store.get_node(hit.node_id) is not None

    def test_machinery_is_not_in_the_index(
        self, vector_store, embedder, episode_id, report
    ):
        hits = vector_store.hybrid_search(embedder.embed_text("anything"), limit=20)
        indexed = {hit.node_id for hit in hits}

        assert episode_id not in indexed
        assert not any(node_id.startswith("d_") for node_id in indexed)


class TestTheRunIsExplainable:
    def test_every_record_written_is_in_the_log(self, ops_store, report):
        trace = ops_store.jobs.get_trace(report.trace_id)
        logged = sum(1 for w in trace.writes if w.target is WriteTarget.GRAPH_NODE)

        assert logged == report.nodes_written

    def test_any_record_can_be_traced_back_to_this_run(self, ops_store, report):
        # The deliverable that makes a wrong graph fixable: every node leads
        # back to the conversation that produced it.
        node_id = _written_nodes(ops_store, report)[0]

        assert ops_store.jobs.find_job_for_node(node_id).job_id == report.job_id

    def test_every_write_names_the_episode_it_came_from(self, ops_store, report):
        trace = ops_store.jobs.get_trace(report.trace_id)

        assert all(write.episode_id for write in trace.writes)

    def test_each_stage_recorded_how_long_it_took(self, ops_store, report):
        runs = ops_store.jobs.get_stage_runs(report.job_id)

        assert len(runs) == 5
        assert all(run.duration_ms is not None for run in runs)


def _written_nodes(ops_store, report) -> list[str]:
    trace = ops_store.jobs.get_trace(report.trace_id)
    return [w.node_id for w in trace.writes if w.target is WriteTarget.GRAPH_NODE]


def _written_edges(ops_store, report) -> list[tuple[str, str, str]]:
    trace = ops_store.jobs.get_trace(report.trace_id)
    return [
        (w.edge_type, w.from_id, w.to_id)
        for w in trace.writes
        if w.target is WriteTarget.GRAPH_EDGE
    ]
