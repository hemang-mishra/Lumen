"""
Tests for finding history by what it is attached to.

This half of retrieval exists for one specific failure, and the test worth
reading first is the unresolved-material one. Someone describing recovery
uses none of the words they used describing the injury — no measure of
distance between those two sentences will ever connect them. These lookups
do not read either sentence.

The other property tested throughout is containment: three anchors are
asked independently, and one failing must cost that anchor alone.
"""

from __future__ import annotations

import logging

import pytest

from lumen.config import PipelineConfig
from lumen.pipeline.retrieval import structural
from lumen.schemas.enums import CandidateRetrievalSource, StructuralAnchorType


@pytest.fixture
def anchors(graph_store):
    """Run the anchor lookups against the seeded graph."""

    def _find(*, people=(), era=None, **limits):
        return structural.find_by_anchors(
            people=tuple(people),
            era=era,
            graph=graph_store,
            config=PipelineConfig(**limits),
        )

    return _find


@pytest.fixture
def person(graph_store):
    def _write(name: str) -> str:
        node_id = f"person_{name.lower()}"
        graph_store.write_node(
            "PersonEntityNode",
            {
                "node_id": node_id,
                "canonical_name": name,
                "first_mentioned_at": "2026-01-01T00:00:00Z",
                "last_mentioned_at": "2026-06-11T00:00:00Z",
                "relationship_to_user": "MENTOR",
                "relationship_sentiment_trend": "STABLE",
                "status": "ACTIVE",
            },
        )
        return node_id

    return _write


@pytest.fixture
def episode(graph_store):
    def _write(node_id: str, *, status: str = "COMPLETE", era: str | None = None) -> str:
        props = {
            "node_id": node_id,
            "entry_id": "sess_1",
            "occurred_at": "2026-01-01T00:00:00Z",
            "created_at": "2026-01-01T00:00:00Z",
            "valid_from": "2026-01-01T00:00:00Z",
            "event_date": "2026-01-01",
            "session_label": "A",
            "source_modality": "TEXT_ENTRY",
            "entry_class": "REFLECTION",
            "episode_summary": "an earlier entry",
            "episode_index": 1,
            "total_episodes_in_entry": 1,
            "coreference_map_id": "coref_1",
            "reconciliation_status": status,
            "raw_text_hash": "hash",
        }
        if era:
            props["historical_era"] = era
        graph_store.write_node("EpisodeNode", props)
        return node_id

    return _write


@pytest.fixture
def belief(graph_store):
    def _write(node_id: str, *, era: str | None = None, status: str = "ACTIVE") -> str:
        props = {
            "node_id": node_id,
            "created_at": "2026-01-01T00:00:00Z",
            "valid_from": "2026-01-01T00:00:00Z",
            "belief_statement": "Effort is only worth it if nobody sees it fail",
            "belief_source_summary": "from an earlier entry",
            "domain": "SELF_CONCEPT",
            "signal_strength": "HIGH",
            "provenance": "USER_GENERATED",
            "verification_status": "IMPLICIT",
            "is_contradicted": False,
            "status": status,
            "version": 1,
            "last_reinforced_at": "2026-01-01T00:00:00Z",
            "evidence_count": 1,
            "query_frequency": 0,
        }
        if era:
            props["era_tag"] = era
        graph_store.write_node("BeliefNode", props)
        return node_id

    return _write


class TestFollowingAName:
    def test_what_was_said_about_someone_comes_back(
        self, graph_store, seed_observation, person, anchors
    ):
        person("Alex")
        seed_observation("obs_alex", "He gives feedback as correction", indexed=False)
        graph_store.write_edge("mentions_obs", "obs_alex", "person_alex")

        found = anchors(people=["Alex"])

        assert [c.node_id for c in found] == ["obs_alex"]

    def test_the_candidate_says_which_name_led_to_it(
        self, graph_store, seed_observation, person, anchors
    ):
        # Reconciliation is told how a candidate surfaced, because "a name
        # matched" and "it reads similarly" are different claims and the
        # second is far easier to over-trust.
        person("Alex")
        seed_observation("obs_alex", "He gives feedback as correction", indexed=False)
        graph_store.write_edge("mentions_obs", "obs_alex", "person_alex")

        found = anchors(people=["Alex"])

        assert found[0].retrieval_source is CandidateRetrievalSource.STRUCTURAL
        assert found[0].structural_anchor_type is StructuralAnchorType.NAMED_PERSON
        assert found[0].structural_anchor_value == "Alex"

    def test_a_name_the_graph_has_never_seen_finds_nothing(self, anchors):
        # Expected on a young graph: person nodes and the edges pointing at
        # them are created during reconciliation, which has not run yet.
        assert anchors(people=["Alex"]) == []

    def test_several_names_are_all_followed(
        self, graph_store, seed_observation, person, anchors
    ):
        person("Alex")
        person("Priya")
        seed_observation("obs_a", "about Alex", indexed=False)
        seed_observation("obs_p", "about Priya", indexed=False)
        graph_store.write_edge("mentions_obs", "obs_a", "person_alex")
        graph_store.write_edge("mentions_obs", "obs_p", "person_priya")

        found = anchors(people=["Alex", "Priya"])

        assert {c.node_id for c in found} == {"obs_a", "obs_p"}


class TestFollowingAPeriod:
    def test_a_belief_from_that_period_comes_back(self, belief, anchors):
        belief("bel_old", era="EXAM_PREP")

        found = anchors(era="EXAM_PREP")

        assert [c.node_id for c in found] == ["bel_old"]

    def test_the_candidate_says_which_period_led_to_it(self, belief, anchors):
        belief("bel_old", era="EXAM_PREP")

        found = anchors(era="EXAM_PREP")

        assert found[0].structural_anchor_type is StructuralAnchorType.HISTORICAL_ERA
        assert found[0].structural_anchor_value == "EXAM_PREP"

    def test_an_episode_from_that_period_comes_back_too(self, episode, anchors):
        episode("ep_old", era="EXAM_PREP")

        found = anchors(era="EXAM_PREP")

        assert [c.node_id for c in found] == ["ep_old"]

    def test_no_period_named_means_no_lookup(self, belief, anchors):
        belief("bel_old", era="EXAM_PREP")

        assert anchors(era=None) == []

    def test_a_superseded_belief_is_left_out(self, belief, anchors):
        belief("bel_gone", era="EXAM_PREP", status="SUPERSEDED")

        assert anchors(era="EXAM_PREP") == []


class TestUnresolvedWeightyMaterial:
    def test_it_surfaces_whatever_today_is_about(
        self, graph_store, seed_observation, episode, anchors
    ):
        # The whole reason this half of retrieval exists. The entry that
        # finally resolves something painful is written in the vocabulary
        # of the resolution, not of the wound, so nothing that reads either
        # of them will ever put the two together.
        episode("ep_open", status="PENDING_RERECONCILIATION")
        seed_observation(
            "obs_wound",
            "I cannot be in that house without my chest going tight",
            observation_type="IDENTITY_FUSION_STATE",
            signal="HIGH",
            episode_id="ep_open",
            indexed=False,
        )
        graph_store.write_edge("contains_obs", "ep_open", "obs_wound")

        found = anchors()

        assert [c.node_id for c in found] == ["obs_wound"]

    def test_it_runs_with_no_anchors_at_all(
        self, graph_store, seed_observation, episode, anchors
    ):
        # No names, no period — and it still surfaces. Nothing in today's
        # entry has to point at it.
        episode("ep_open", status="PENDING_RERECONCILIATION")
        seed_observation(
            "obs_wound",
            "something long unresolved",
            observation_type="EXISTENTIAL_REFLECTION",
            signal="HIGH",
            episode_id="ep_open",
            indexed=False,
        )
        graph_store.write_edge("contains_obs", "ep_open", "obs_wound")

        assert len(anchors(people=[], era=None)) == 1

    def test_the_candidate_says_why_it_surfaced(
        self, graph_store, seed_observation, episode, anchors
    ):
        episode("ep_open", status="PENDING_RERECONCILIATION")
        seed_observation(
            "obs_wound",
            "something long unresolved",
            observation_type="INAUTHENTICITY_STATE",
            signal="HIGH",
            episode_id="ep_open",
            indexed=False,
        )
        graph_store.write_edge("contains_obs", "ep_open", "obs_wound")

        found = anchors()

        assert (
            found[0].structural_anchor_type
            is StructuralAnchorType.HIGH_SENSITIVITY_OPEN
        )

    def test_a_settled_episode_surfaces_nothing(
        self, graph_store, seed_observation, episode, anchors
    ):
        episode("ep_done", status="COMPLETE")
        seed_observation(
            "obs_settled",
            "something already reconciled",
            observation_type="IDENTITY_FUSION_STATE",
            signal="HIGH",
            episode_id="ep_done",
            indexed=False,
        )
        graph_store.write_edge("contains_obs", "ep_done", "obs_settled")

        assert anchors() == []


class TestFailuresAreContained:
    class BrokenPersonLookup:
        def find_linked_to_person(self, *args, **kwargs):
            raise RuntimeError("that query failed")

        def find_by_era(self, *args, **kwargs):
            return [{"node_id": "bel_era", "_label": "BeliefNode", "belief_statement": "x"}]

        def find_unresolved_high_signal(self, *args, **kwargs):
            return [{"node_id": "obs_open", "_label": "ObservationNode", "content": "y"}]

    def test_one_broken_anchor_does_not_take_the_others(self, caplog):
        with caplog.at_level(logging.WARNING):
            found = structural.find_by_anchors(
                people=("Alex",),
                era="EXAM_PREP",
                graph=self.BrokenPersonLookup(),
                config=PipelineConfig(),
            )

        assert {c.node_id for c in found} == {"bel_era", "obs_open"}
        assert any("anchor lookup failed" in r.getMessage() for r in caplog.records)


class TestNothingToFind:
    def test_an_empty_graph_gives_an_empty_answer(self, anchors):
        assert anchors(people=["Alex"], era="EXAM_PREP") == []

    def test_the_limit_is_honoured_per_anchor(self, belief, anchors):
        for index in range(4):
            belief(f"bel_{index}", era="EXAM_PREP")

        assert len(anchors(era="EXAM_PREP", pass_b_keep=2)) == 2
