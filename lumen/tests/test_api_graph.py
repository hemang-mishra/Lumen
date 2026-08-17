"""
Tests for the endpoints that read the knowledge graph.

The graph these run against is built by actually running the pipeline over a
journal entry, not by seeding nodes by hand. A hand-seeded fixture agrees
with whatever shape the test author imagined; a graph the pipeline produced
is the one the API has to serve, and it is the first time anything has
checked that those two agree.
"""

from __future__ import annotations

import json

import pytest

from lumen.config import AppConfig
from lumen.pipeline.orchestration import run_pipeline


@pytest.fixture
def a_processed_entry(
    ops_store, graph_store, vector_store, embedder, full_run_providers, decayed_session
):
    """Run one real journal entry all the way through, and report what it made."""
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
def episode_id(a_processed_entry) -> str:
    return a_processed_entry.episodes[0].episode_id


@pytest.fixture
def merged_into_a_pattern(
    ops_store, graph_store, vector_store, embedder, full_run_providers,
    decayed_session, seed_pattern
):
    """
    A run where a finding joins a pattern the person already had.

    Built by running the pipeline rather than by writing the decision into
    the graph, because what is being checked is that the reported outcome
    matches what reconciliation actually did. The pattern is seeded with the
    finding's own words behind it so the search genuinely finds it — the
    stand-in embedder gives identical text identical vectors.
    """
    from lumen.tests.conftest import EPISODE_TEXT

    finding = "Comparing himself to Alex is what causes the pain"
    pattern_id = seed_pattern("pat_old", name="Comparison spiral")
    vector_store.upsert(
        pattern_id,
        embedder.embed_text(finding),
        {"node_type": "PatternNode", "status": "ACTIVE"},
    )

    light, deep = full_run_providers(
        {
            "decision": json.dumps(
                {
                    "decisions": [
                        {
                            "item_index": 1,
                            "primary": {
                                "action": "MERGE",
                                "target_node_id": pattern_id,
                                "confidence": 0.95,
                                "reason": "the same thing said before",
                            },
                        }
                    ],
                    "people": [],
                }
            ),
            "hyde": json.dumps(
                {"hypotheticals": [{"index": 1, "text": finding}]}
            ),
        }
    )
    report = run_pipeline(
        decayed_session(EPISODE_TEXT),
        graph=graph_store,
        vectors=vector_store,
        embedder=embedder,
        lightweight=light,
        thinking=deep,
        ops=ops_store,
        config=AppConfig(),
    )
    return report.episodes[0].episode_id, pattern_id


@pytest.fixture
def written_ids(ops_store, a_processed_entry) -> list[str]:
    """Every record the run put in the graph."""
    trace = ops_store.jobs.get_trace(a_processed_entry.trace_id)
    return [
        write.node_id
        for write in trace.writes
        if write.target.value == "GRAPH_NODE" and write.node_id
    ]


class TestOverview:
    def test_the_counts_reflect_what_was_written(self, api_client, written_ids):
        body = api_client.get("/graph/stats").json()

        assert body["total"] == len(written_ids)
        assert body["counts"]["EpisodeNode"] == 1

    def test_an_empty_graph_reports_zero_rather_than_nothing(self, api_client):
        body = api_client.get("/graph/stats").json()

        assert body["total"] == 0
        assert body["counts"]["BeliefNode"] == 0


class TestListingRecords:
    def test_everything_written_can_be_listed(self, api_client, written_ids):
        body = api_client.get("/graph/nodes?limit=200&active_only=false").json()

        assert body["count"] == len(written_ids)

    def test_one_kind_can_be_asked_for(self, api_client, episode_id):
        body = api_client.get("/graph/nodes?types=EpisodeNode").json()

        assert [node["node_id"] for node in body["nodes"]] == [episode_id]

    def test_several_kinds_can_be_asked_for_at_once(self, api_client, written_ids):
        body = api_client.get(
            "/graph/nodes?types=EpisodeNode&types=ObservationNode&active_only=false"
        ).json()

        assert {node["node_type"] for node in body["nodes"]} <= {
            "EpisodeNode",
            "ObservationNode",
        }

    def test_the_page_says_what_was_asked_for(self, api_client, a_processed_entry):
        body = api_client.get("/graph/nodes?limit=2&offset=1").json()

        assert body["limit"] == 2
        assert body["offset"] == 1
        assert body["count"] <= 2

    def test_paging_does_not_repeat_records(self, api_client, a_processed_entry):
        first = api_client.get("/graph/nodes?limit=3&active_only=false").json()
        second = api_client.get("/graph/nodes?limit=3&offset=3&active_only=false").json()

        assert {n["node_id"] for n in first["nodes"]}.isdisjoint(
            {n["node_id"] for n in second["nodes"]}
        )

    def test_an_impossible_page_size_is_refused(self, api_client):
        assert api_client.get("/graph/nodes?limit=0").status_code == 422
        assert api_client.get("/graph/nodes?limit=5000").status_code == 422


class TestOneRecord:
    def test_a_record_comes_back_with_what_it_holds(self, api_client, episode_id):
        body = api_client.get(f"/graph/nodes/{episode_id}").json()

        assert body["node_id"] == episode_id
        assert body["node_type"] == "EpisodeNode"
        assert body["properties"]["episode_summary"]

    def test_empty_columns_do_not_come_back(self, api_client, episode_id):
        # Every kind of record is stored in one wide shape, so an untidied
        # answer would be a hundred-odd mostly empty fields.
        body = api_client.get(f"/graph/nodes/{episode_id}").json()

        assert None not in body["properties"].values()
        assert len(body["properties"]) < 30

    def test_lists_come_back_as_lists(self, api_client, episode_id):
        body = api_client.get(f"/graph/nodes/{episode_id}").json()

        assert body["properties"]["language_tags"] == ["en"]

    def test_a_record_that_does_not_exist_is_a_clean_404(self, api_client):
        response = api_client.get("/graph/nodes/nope")

        assert response.status_code == 404
        assert response.json()["kind"] == "node"


class TestNeighbours:
    def test_what_the_entry_produced_is_one_step_away(self, api_client, episode_id):
        body = api_client.get(f"/graph/nodes/{episode_id}/neighbors").json()

        assert len(body["nodes"]) > 1
        assert episode_id in {node["node_id"] for node in body["nodes"]}

    def test_the_links_come_back_with_the_records(self, api_client, episode_id):
        body = api_client.get(f"/graph/nodes/{episode_id}/neighbors").json()

        assert body["edges"]
        assert all(edge["edge_type"] for edge in body["edges"])
        assert all(edge["from_node_id"] and edge["to_node_id"] for edge in body["edges"])

    def test_a_complete_answer_says_it_was_not_cut(self, api_client, episode_id):
        body = api_client.get(f"/graph/nodes/{episode_id}/neighbors").json()

        assert body["truncated"] is False

    def test_a_cut_answer_says_so(self, api_client, episode_id):
        # A piece that was cut and one that was genuinely that size look
        # identical otherwise, and a partial graph drawn as a whole one is a
        # wrong answer that looks right.
        body = api_client.get(
            f"/graph/nodes/{episode_id}/neighbors?depth=3&limit=2"
        ).json()

        assert body["truncated"] is True

    def test_walking_further_than_three_steps_is_refused(self, api_client, episode_id):
        # Beyond three steps a well-connected graph is mostly reachable from
        # anywhere in it, so a deeper walk is the whole history by accident.
        assert (
            api_client.get(f"/graph/nodes/{episode_id}/neighbors?depth=4").status_code
            == 422
        )

    def test_only_the_asked_for_kinds_of_link_are_followed(
        self, api_client, episode_id
    ):
        body = api_client.get(
            f"/graph/nodes/{episode_id}/neighbors?edge_types=contains_obs"
        ).json()

        assert {edge["edge_type"] for edge in body["edges"]} == {"contains_obs"}

    def test_direction_can_be_narrowed(self, api_client, episode_id):
        inward = api_client.get(
            f"/graph/nodes/{episode_id}/neighbors?direction=in"
        ).json()

        assert inward["edges"] == []

    def test_a_direction_that_is_not_a_direction_is_refused(
        self, api_client, episode_id
    ):
        assert (
            api_client.get(
                f"/graph/nodes/{episode_id}/neighbors?direction=sideways"
            ).status_code
            == 422
        )

    def test_neighbours_of_nothing_is_a_404(self, api_client):
        assert api_client.get("/graph/nodes/nope/neighbors").status_code == 404


class TestVersionHistory:
    def test_a_record_with_one_version_reports_one(self, api_client, written_ids):
        pattern = next((i for i in written_ids if i.startswith("pat_")), None)
        assert pattern, "the run should have created a pattern"

        body = api_client.get(f"/graph/nodes/{pattern}/versions").json()

        assert body["length"] == 1
        assert body["current_version_id"] == pattern

    def test_a_kind_that_is_never_versioned_has_an_empty_history(
        self, api_client, episode_id
    ):
        # An empty history rather than an error: it genuinely has none.
        body = api_client.get(f"/graph/nodes/{episode_id}/versions").json()

        assert body["versions"] == []
        assert body["current_version_id"] is None

    def test_the_history_of_nothing_is_a_404(self, api_client):
        assert api_client.get("/graph/nodes/nope/versions").status_code == 404


class TestDecisionHistory:
    def test_a_record_the_system_decided_about_has_a_history(
        self, api_client, written_ids
    ):
        observation = next(i for i in written_ids if i.startswith("obs_"))

        body = api_client.get(f"/graph/nodes/{observation}/decisions").json()

        assert body["node_id"] == observation
        assert body["decisions"]
        assert body["decisions"][0]["node_type"] == "DecisionAuditNode"

    def test_a_decision_says_what_was_chosen_and_how_sure(
        self, api_client, written_ids
    ):
        # This is the answer to "why does the system think this".
        observation = next(i for i in written_ids if i.startswith("obs_"))

        body = api_client.get(f"/graph/nodes/{observation}/decisions").json()
        decision = body["decisions"][0]["properties"]

        assert decision["action"]
        assert "confidence" in decision
        assert decision["model_used"]

    def test_a_record_nobody_decided_about_has_none(self, api_client, episode_id):
        body = api_client.get(f"/graph/nodes/{episode_id}/decisions").json()

        assert body["decisions"] == []

    def test_the_decisions_of_nothing_are_a_404(self, api_client):
        assert api_client.get("/graph/nodes/nope/decisions").status_code == 404


class TestOneEntry:
    def test_everything_the_entry_produced_comes_back(self, api_client, episode_id):
        body = api_client.get(f"/graph/episodes/{episode_id}").json()

        assert body["episode"]["node_id"] == episode_id
        kinds = {node["node_type"] for node in body["contents"]["nodes"]}
        assert "ObservationNode" in kinds

    def test_the_links_between_them_come_too(self, api_client, episode_id):
        body = api_client.get(f"/graph/episodes/{episode_id}").json()

        assert body["contents"]["edges"]

    def test_an_entry_that_does_not_exist_is_a_404(self, api_client):
        response = api_client.get("/graph/episodes/nope")

        assert response.status_code == 404
        assert response.json()["kind"] == "episode"


class TestWhatAnEntryChangedInTheHistory:
    """
    The step that matters most and shows least.

    Everything before it is reading. This is where a thing somebody noticed
    on a Tuesday becomes a lasting belief, joins a pattern they have had for
    years, or is held back for them to look at — and read as records and
    links alone, an entry that changed nothing looks exactly like one that
    changed everything.
    """

    def test_each_finding_says_what_was_decided_about_it(
        self, api_client, episode_id
    ):
        body = api_client.get(f"/graph/episodes/{episode_id}").json()

        assert body["outcomes"]
        outcome = body["outcomes"][0]
        assert outcome["source_node_id"]
        assert outcome["action"]
        assert outcome["decision_id"]

    def test_the_outcomes_belong_to_this_entrys_findings(
        self, api_client, episode_id
    ):
        body = api_client.get(f"/graph/episodes/{episode_id}").json()

        findings = {
            node["node_id"]
            for node in body["contents"]["nodes"]
            if node["node_id"] != episode_id
        }
        assert {outcome["source_node_id"] for outcome in body["outcomes"]} <= findings

    def test_what_a_finding_became_is_named_rather_than_numbered(
        self, api_client, merged_into_a_pattern
    ):
        """
        An identifier is not an answer. "It joined the pattern you have been
        calling Comparison spiral" is; resolving that by hand against a
        second table is answering the question badly.
        """
        episode_id, pattern_id = merged_into_a_pattern

        body = api_client.get(f"/graph/episodes/{episode_id}").json()

        joined = next(
            outcome
            for outcome in body["outcomes"]
            if outcome["target_node_id"] == pattern_id
        )
        assert joined["action"] == "MERGE"
        assert joined["target_type"] == "PatternNode"
        assert joined["target_preview"] == "Comparison spiral"
        assert joined["edge_type_created"]

    def test_a_finding_that_became_a_lasting_record_says_what_it_became(
        self, api_client, episode_id
    ):
        """
        The question people actually arrive with: did this become a belief?

        It is not answered by the decision's target, which names what the
        finding was *compared against* — on a BRANCH that is whatever the
        search turned up, or nothing at all. What it became is reached by the
        link the decision drew, and matched to the decision by id, because
        one finding can be decided about more than once.
        """
        body = api_client.get(f"/graph/episodes/{episode_id}").json()

        grew = [o for o in body["outcomes"] if o["became_node_id"]]
        assert grew, "this run promoted nothing, so there is nothing to report"
        assert grew[0]["became_type"]
        assert grew[0]["became_preview"]

    def test_a_finding_that_stayed_with_its_entry_became_nothing(
        self, api_client, episode_id
    ):
        """
        The common and correct outcome. A thing that happened is saved with
        its day and promoted to nothing, and reporting that as a failure is
        how a graph ends up with a permanent record for every sentence.
        """
        body = api_client.get(f"/graph/episodes/{episode_id}").json()

        stayed = [o for o in body["outcomes"] if not o["became_node_id"]]
        assert stayed
        assert all(o["became_type"] is None for o in stayed)

    def test_what_it_became_is_not_confused_with_what_it_was_compared_to(
        self, api_client, merged_into_a_pattern
    ):
        """
        Two different questions that a single field would blur. A merge is
        weighed against the pattern it joins; a branch is weighed against
        whatever the search found and becomes something new.
        """
        episode_id, pattern_id = merged_into_a_pattern

        body = api_client.get(f"/graph/episodes/{episode_id}").json()
        joined = next(
            o for o in body["outcomes"] if o["target_node_id"] == pattern_id
        )

        assert joined["action"] == "MERGE"
        assert joined["became_node_id"] != joined["source_node_id"]

    def test_a_decision_that_was_held_back_says_so(self, api_client, episode_id):
        """
        A note of a decision and a decision that took effect look identical
        otherwise — and the difference is whether it is in the history yet.
        """
        body = api_client.get(f"/graph/episodes/{episode_id}").json()

        assert all("waiting_for_a_person" in o for o in body["outcomes"])
        acted = [o for o in body["outcomes"] if not o["waiting_for_a_person"]]
        assert all(o["status"] in {"ACTIVE", "ROLLED_BACK"} for o in acted)

    def test_an_entry_nobody_decided_about_reports_no_outcomes(
        self, api_client, graph_store, episode_id
    ):
        """Empty rather than absent: the question was asked and had no answer."""
        graph_store.write_node(
            "EpisodeNode",
            {"node_id": "ep_undecided_001", "episode_summary": "never reconciled"},
        )

        body = api_client.get("/graph/episodes/ep_undecided_001").json()

        assert body["outcomes"] == []


class TestCausalSequences:
    def test_a_sequence_that_does_not_exist_is_a_404(self, api_client):
        response = api_client.get("/graph/chains/nope")

        assert response.status_code == 404
        assert response.json()["kind"] == "chain"

    def test_the_steps_come_back_in_order(self, api_client, graph_store, episode_id):
        # Order is the whole content of a sequence: read differently it
        # describes a different sequence.
        moment = "2026-06-11T20:00:00+00:00"
        graph_store.write_node(
            "CausalChainNode",
            {
                "node_id": "chain_api_1",
                "episode_id": episode_id,
                "created_at": moment,
                "valid_from": moment,
                "chain_summary": "trigger to lesson",
                "is_anticipatory": False,
                "step_count": 3,
                "status": "ACTIVE",
            },
        )
        for index in (2, 3, 1):
            graph_store.write_node(
                "CausalStepNode",
                {
                    "node_id": f"step_api_{index}",
                    "chain_id": "chain_api_1",
                    "step_index": index,
                    "step_type": "ACTION",
                    "content": f"step {index}",
                    "created_at": moment,
                },
            )
            graph_store.write_edge(
                "chain_contains", "chain_api_1", f"step_api_{index}", {"valid_from": moment}
            )

        body = api_client.get("/graph/chains/chain_api_1").json()

        assert [n["properties"]["step_index"] for n in body["nodes"]] == [1, 2, 3]
