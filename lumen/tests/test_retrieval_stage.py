"""
End-to-end tests for the stage that brings back what was said before.

Run against a real graph and a real vector index, so what is tested is the
thing that ships rather than an agreeable stand-in.

The two tests that matter most sit in the same class and look almost
identical: a cold graph and a broken search both return nothing, and this
stage has to say which is which. The next stage answers "nothing" by
writing a new node — correct for the first, and for the second it records
a long-standing pattern as a fresh discovery, permanently, with nothing
raised anywhere.
"""

from __future__ import annotations

import json

import pytest

from lumen.config import AppConfig, PipelineConfig
from lumen.pipeline import retrieve
from lumen.providers.errors import ProviderTimeoutError
from lumen.providers.fake import FakeEmbeddingProvider
from lumen.schemas.enums import EmbeddingTaskType, ModelRole, ObservationStatus


class FailingEmbedder(FakeEmbeddingProvider):
    """An embedder that cannot produce vectors."""

    def embed_batch(self, texts, *, task_type=EmbeddingTaskType.DOCUMENT):
        raise ProviderTimeoutError(
            "took too long", provider="fake", model="fake", role=ModelRole.EMBEDDING
        )


@pytest.fixture
def run(graph_store, vector_store, embedder, hyde_provider):
    """Run the whole stage against the seeded stores."""

    def _run(extraction, *, model=None, embedding=None, episode=None, **limits):
        texts = [node.content for node in extraction.observations]
        texts += [node.event_summary for node in extraction.events]
        texts += [node.session_summary for node in extraction.sessions]
        return retrieve(
            extraction,
            graph=graph_store,
            vectors=vector_store,
            embedder=embedding or embedder,
            lightweight=model or hyde_provider(texts),
            episode=episode,
            config=AppConfig(pipeline=PipelineConfig(**limits)) if limits else None,
        )

    return _run


class TestWhatGetsSearchedFor:
    def test_findings_events_and_the_session_all_get_results(
        self, make_extraction, run
    ):
        extraction = make_extraction(
            "the comparing hurts", events=["ate at the cafe"], sessions=["worked it out"]
        )

        results = run(extraction)

        assert [r.source_node_id for r in results] == [
            "obs_new_1",
            "evt_new_1",
            "sess_new_1",
        ]

    def test_findings_from_a_thin_entry_are_skipped(self, make_extraction, run):
        # They are written straight to the graph without ever being
        # compared against history, so searching for them is work nobody
        # reads.
        extraction = make_extraction(
            "a short note", status=ObservationStatus.RAW_CAPTURE
        )

        assert run(extraction) == []

    def test_a_failed_extraction_yields_nothing_to_search(self, make_extraction, run):
        assert run(make_extraction()) == []

    def test_causal_sequences_are_not_searched(self, make_extraction, run):
        # A sequence belongs to its episode and there is no way to relate
        # one to another, so candidates for it would have nowhere to go.
        extraction = make_extraction("the comparing hurts")

        results = run(extraction)

        assert all(not r.source_node_id.startswith("chain") for r in results)


class TestCost:
    def test_a_whole_entry_costs_one_call(self, make_extraction, run, hyde_provider):
        model = hyde_provider(["a", "b", "c"])

        run(make_extraction("first", "second", "third"), model=model)

        assert len(model.calls) == 1

    def test_a_whole_entry_costs_one_batch_of_vectors(
        self, make_extraction, run, embedder
    ):
        batches = []
        original = embedder.embed_batch
        embedder.embed_batch = lambda texts, **kw: (
            batches.append(len(texts)) or original(texts, **kw)
        )

        run(make_extraction("first", "second", "third"), embedding=embedder)

        assert batches == [3]


class TestNothingFoundVersusCouldNotLook:
    def test_a_cold_graph_finds_nothing_and_says_so_calmly(
        self, make_extraction, run
    ):
        # The very first entry a person ever writes. Zero candidates is the
        # correct answer and must not look like a fault.
        results = run(make_extraction("something entirely new"))

        assert results[0].pass_a_candidates == []
        assert results[0].pass_b_candidates == []
        assert results[0].search_failed is False

    def test_a_broken_embedder_says_it_could_not_look(self, make_extraction, run):
        # Identical from the outside, opposite in meaning. Without this
        # flag the next stage would record a long-standing pattern as a
        # brand new discovery and nothing would ever say otherwise.
        results = run(
            make_extraction("the comparing hurts"),
            embedding=FailingEmbedder(dimensions=768),
        )

        assert results[0].pass_a_candidates == []
        assert results[0].search_failed is True

    def test_the_anchors_still_run_when_the_search_cannot(
        self, graph_store, seed_observation, make_extraction, run
    ):
        graph_store.write_node(
            "PersonEntityNode",
            {
                "node_id": "person_alex",
                "canonical_name": "Alex",
                "first_mentioned_at": "2026-01-01T00:00:00Z",
                "last_mentioned_at": "2026-01-01T00:00:00Z",
                "relationship_to_user": "MENTOR",
                "relationship_sentiment_trend": "STABLE",
                "status": "ACTIVE",
            },
        )
        seed_observation("obs_alex", "about Alex", indexed=False)
        graph_store.write_edge("mentions_obs", "obs_alex", "person_alex")

        results = run(
            make_extraction("the comparing hurts", person_refs=["Alex"]),
            embedding=FailingEmbedder(dimensions=768),
        )

        assert [c.node_id for c in results[0].pass_b_candidates] == ["obs_alex"]
        assert results[0].search_failed is True


class TestFindingHistory:
    def test_a_matching_record_is_returned(
        self, seed_observation, make_extraction, run
    ):
        seed_observation("obs_old", "the comparing is the thing that hurts")

        results = run(make_extraction("the comparing is the thing that hurts"))

        assert [c.node_id for c in results[0].pass_a_candidates] == ["obs_old"]

    def test_a_node_is_never_offered_itself(
        self, seed_observation, make_extraction, run
    ):
        seed_observation(
            "obs_already_there", "the comparing hurts", episode_id="ep_new"
        )

        results = run(make_extraction("the comparing hurts", episode_id="ep_new"))

        assert results[0].pass_a_candidates == []

    def test_anchors_are_shared_by_every_finding_in_the_entry(
        self, graph_store, seed_observation, make_extraction, run
    ):
        # The people and the period belong to the entry, so they are looked
        # up once and offered to everything in it.
        graph_store.write_node(
            "EpisodeNode",
            {
                "node_id": "ep_open",
                "entry_id": "s1",
                "occurred_at": "2026-01-01T00:00:00Z",
                "created_at": "2026-01-01T00:00:00Z",
                "valid_from": "2026-01-01T00:00:00Z",
                "event_date": "2026-01-01",
                "session_label": "A",
                "source_modality": "TEXT_ENTRY",
                "entry_class": "REFLECTION",
                "episode_summary": "earlier",
                "episode_index": 1,
                "total_episodes_in_entry": 1,
                "coreference_map_id": "c1",
                "reconciliation_status": "PENDING_RERECONCILIATION",
                "raw_text_hash": "h",
            },
        )
        seed_observation(
            "obs_open",
            "something long unresolved",
            observation_type="CORE_WOUND",
            signal="HIGH",
            episode_id="ep_open",
            indexed=False,
        )
        graph_store.write_node(
            "ObservationNode",
            {
                "node_id": "obs_fusion",
                "episode_id": "ep_open",
                "occurred_at": "2026-01-01T00:00:00Z",
                "created_at": "2026-01-01T00:00:00Z",
                "valid_from": "2026-01-01T00:00:00Z",
                "type": "IDENTITY_FUSION_STATE",
                "content": "if this fails I am nothing",
                "signal_strength": "CRITICAL",
                "provenance": "USER_GENERATED",
                "verification_status": "IMPLICIT",
                "extraction_confidence": "STANDARD",
                "status": "ACTIVE",
                "extraction_model": "fake",
                "extraction_attempt": 1,
            },
        )
        graph_store.write_edge("contains_obs", "ep_open", "obs_fusion")

        results = run(make_extraction("first thing", "second thing"))

        assert len(results) == 2
        for result in results:
            assert [c.node_id for c in result.pass_b_candidates] == ["obs_fusion"]


class TestWhoTheEntryRefersTo:
    def person(self, graph_store, name: str) -> None:
        graph_store.write_node(
            "PersonEntityNode",
            {
                "node_id": f"person_{name.lower()}",
                "canonical_name": name,
                "first_mentioned_at": "2026-01-01T00:00:00Z",
                "last_mentioned_at": "2026-01-01T00:00:00Z",
                "relationship_to_user": "MENTOR",
                "relationship_sentiment_trend": "STABLE",
                "status": "ACTIVE",
            },
        )

    def test_a_name_on_a_finding_is_followed(
        self, graph_store, seed_observation, make_extraction, run
    ):
        self.person(graph_store, "Alex")
        seed_observation("obs_alex", "about Alex", indexed=False)
        graph_store.write_edge("mentions_obs", "obs_alex", "person_alex")

        results = run(make_extraction("about him", person_refs=["Alex"]))

        assert [c.node_id for c in results[0].pass_b_candidates] == ["obs_alex"]

    def test_someone_only_ever_referred_to_by_a_pronoun_is_still_followed(
        self, graph_store, seed_observation, make_extraction, run, make_extraction_input
    ):
        # The reference map knows people the findings never named, because
        # the entry only ever said "he".
        self.person(graph_store, "Alex")
        seed_observation("obs_alex", "about Alex", indexed=False)
        graph_store.write_edge("mentions_obs", "obs_alex", "person_alex")

        results = run(
            make_extraction("about him"),
            episode=make_extraction_input(people=["Alex"]),
        )

        assert [c.node_id for c in results[0].pass_b_candidates] == ["obs_alex"]

    def test_a_period_the_entry_named_is_followed(
        self, graph_store, make_extraction, run, make_extraction_input
    ):
        graph_store.write_node(
            "BeliefNode",
            {
                "node_id": "bel_old",
                "created_at": "2026-01-01T00:00:00Z",
                "valid_from": "2026-01-01T00:00:00Z",
                "belief_statement": "Effort only counts if it succeeds",
                "belief_source_summary": "earlier",
                "domain": "SELF_CONCEPT",
                "signal_strength": "HIGH",
                "provenance": "USER_GENERATED",
                "verification_status": "IMPLICIT",
                "is_contradicted": False,
                "status": "ACTIVE",
                "era_tag": "EXAM_PREP",
                "version": 1,
                "last_reinforced_at": "2026-01-01T00:00:00Z",
                "evidence_count": 1,
                "query_frequency": 0,
            },
        )
        episode = make_extraction_input()
        episode = episode.model_copy(
            update={
                "episode": episode.episode.model_copy(
                    update={"historical_era": "EXAM_PREP"}
                )
            }
        )

        results = run(make_extraction("back then it felt different"), episode=episode)

        assert [c.node_id for c in results[0].pass_b_candidates] == ["bel_old"]

    def test_an_unsettled_reference_is_not_resolved_here(
        self, graph_store, seed_observation, make_extraction, run, make_extraction_input
    ):
        # The earlier stage refused to say which of two people was meant.
        # Quietly picking one and pulling in their history would undo that.
        self.person(graph_store, "Alex")
        seed_observation("obs_alex", "about Alex", indexed=False)
        graph_store.write_edge("mentions_obs", "obs_alex", "person_alex")

        results = run(
            make_extraction("about this guy"),
            episode=make_extraction_input(ambiguous=[("this guy", ["Alex", "Rohan"])]),
        )

        assert results[0].pass_b_candidates == []

    def test_the_same_name_from_both_places_is_followed_once(
        self, graph_store, seed_observation, make_extraction, run, make_extraction_input
    ):
        self.person(graph_store, "Alex")
        seed_observation("obs_alex", "about Alex", indexed=False)
        graph_store.write_edge("mentions_obs", "obs_alex", "person_alex")

        results = run(
            make_extraction("about him", person_refs=["Alex"]),
            episode=make_extraction_input(people=["Alex"]),
        )

        assert [c.node_id for c in results[0].pass_b_candidates] == ["obs_alex"]


class TestTheResult:
    def test_each_result_names_the_node_it_belongs_to(self, make_extraction, run):
        results = run(make_extraction("first", "second"))

        assert [r.source_node_id for r in results] == ["obs_new_1", "obs_new_2"]

    def test_time_taken_is_recorded(self, make_extraction, run):
        results = run(make_extraction("something"))

        assert results[0].retrieval_time_ms >= 0

    def test_results_carry_the_running_trace(self, make_extraction, run, bound_trace):
        results = run(make_extraction("something"))

        assert results[0].trace_id == bound_trace

    def test_a_result_never_exceeds_the_candidate_limit(
        self, seed_observation, make_extraction, run
    ):
        for index in range(12):
            seed_observation(f"obs_{index}", "the comparing hurts")

        results = run(make_extraction("the comparing hurts"))

        total = len(results[0].pass_a_candidates) + len(results[0].pass_b_candidates)
        assert total <= 8


class TestNothingIsWritten:
    def test_the_stores_are_unchanged_by_a_run(
        self, graph_store, seed_observation, make_extraction, run
    ):
        # Reading is allowed here; writing is the orchestrator's alone, so
        # replaying this stage can never change what is stored.
        seed_observation("obs_old", "the comparing hurts")
        before = graph_store.get_nodes_by_ids(["obs_old"])[0]

        run(make_extraction("the comparing hurts"))

        assert graph_store.get_nodes_by_ids(["obs_old"])[0]["content"] == before["content"]
        assert graph_store.get_node("obs_new_1") is None


class TestWhatGetsLogged:
    def test_the_closing_line_counts_what_came_back(
        self, seed_observation, make_extraction, run, captured_logs
    ):
        seed_observation("obs_old", "the comparing hurts")

        run(make_extraction("the comparing hurts"))

        line = next(e for e in captured_logs if e["msg"] == "retrieval complete")
        assert line["searched"] == 1
        assert line["resembling"] == 1
        assert line["search_failed"] is False

    def test_empty_answers_are_counted(self, make_extraction, run, captured_logs):
        # The only warning there is for a search that has quietly stopped
        # working: every entry starts looking like a new beginning.
        run(make_extraction("nothing like this exists yet"))

        line = next(e for e in captured_logs if e["msg"] == "retrieval complete")
        assert line["results_with_nothing"] == 1

    def test_the_entry_never_reaches_the_log(
        self, make_extraction, run, captured_logs
    ):
        private = "the thing about my father I never said out loud"

        run(make_extraction(private))

        assert private not in json.dumps(captured_logs)
