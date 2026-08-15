"""
Tests for finding history that reads like the thing just written.

These run against a real vector index and a real graph. The interesting
behaviour is in what happens to the search result afterwards — what gets
read back, what gets filtered out, and what order the survivors end up in —
and each of those is a decision that a stand-in would simply agree with.

The one to look at first is the weighting test. Raw closeness is not the
same as importance, and a short candidate list built on closeness alone
will quietly leave out the entries that matter most.
"""

from __future__ import annotations

import logging

import pytest

from lumen.config import PipelineConfig
from lumen.pipeline.retrieval import semantic
from lumen.schemas.enums import CandidateRetrievalSource

NOTHING_EXCLUDED = "ep_not_a_real_episode"


@pytest.fixture
def search(graph_store, vector_store, embedder):
    """Search the seeded stores with a piece of text."""

    def _search(text: str, *, exclude_episode: str = NOTHING_EXCLUDED, **limits):
        return semantic.find_by_resemblance(
            embedder.embed_text(text),
            exclude_episode=exclude_episode,
            graph=graph_store,
            vectors=vector_store,
            config=PipelineConfig(**limits),
        )

    return _search


class TestFindingWhatResembles:
    def test_a_matching_record_comes_back(self, seed_observation, search):
        seed_observation("obs_old", "Comparing myself to others after seeing their work")

        found = search("Comparing myself to others after seeing their work")

        assert [c.node_id for c in found] == ["obs_old"]

    def test_an_empty_store_returns_nothing(self, search):
        # The first entry a person ever writes runs against exactly this,
        # and it must look like a clean empty answer rather than a fault.
        assert search("anything at all") == []

    def test_a_candidate_carries_what_the_node_says(self, seed_observation, search):
        seed_observation("obs_old", "Comparing myself to others")

        found = search("Comparing myself to others")

        assert found[0].content_preview == "Comparing myself to others"
        assert found[0].node_type == "ObservationNode"

    def test_a_candidate_is_marked_as_found_by_resemblance(
        self, seed_observation, search
    ):
        seed_observation("obs_old", "Comparing myself to others")

        found = search("Comparing myself to others")

        assert found[0].retrieval_source is CandidateRetrievalSource.SEMANTIC
        assert found[0].structural_anchor_type is None

    def test_the_stored_score_is_the_plain_closeness(self, seed_observation, search):
        # Weighting can reach twice what this field allows, so what is kept
        # here is the measurement rather than a ranking decision baked in.
        seed_observation("obs_old", "Comparing myself to others", signal="CRITICAL")

        found = search("Comparing myself to others")

        assert 0.0 <= found[0].similarity_score <= 1.0
        assert found[0].similarity_score == pytest.approx(1.0, abs=1e-3)


class TestWhatIsLeftOut:
    def test_a_node_never_matches_itself(self, seed_observation, search):
        # A replayed run finds its own nodes already stored, and a node
        # offered as a candidate for itself reconciles as a perfect match
        # with total confidence — merging something with itself.
        seed_observation("obs_self", "the comparing hurts", episode_id="ep_today")

        found = search("the comparing hurts", exclude_episode="ep_today")

        assert found == []

    def test_a_superseded_node_is_left_out(self, seed_observation, search):
        seed_observation("obs_gone", "the comparing hurts", status="SUSPENDED")

        assert search("the comparing hurts") == []

    def test_a_failed_extraction_is_never_offered(self, seed_observation, search):
        seed_observation("obs_bad", "the comparing hurts", status="EXTRACTION_FAILED")

        assert search("the comparing hurts") == []

    def test_a_node_missing_from_the_graph_is_skipped(
        self, seed_observation, vector_store, embedder, search
    ):
        # The two stores can disagree — a vector written and a graph write
        # that failed. The search should lose that hit, not raise.
        vector_store.upsert(
            "obs_orphan", embedder.embed_text("only in the index"), {"node_type": "x"}
        )

        assert search("only in the index") == []

    def test_machinery_is_not_history(self, graph_store, vector_store, embedder, search):
        # Audit records and decisions live in the same graph and can be
        # indexed, but they are not things a person said.
        graph_store.write_node(
            "MacroextractionReportNode",
            {
                "node_id": "macro_1",
                "created_at": "2026-01-01T00:00:00Z",
                "report_type": "WEEKLY",
                "period_start": "2026-01-01T00:00:00Z",
                "period_end": "2026-01-07T00:00:00Z",
                "episodes_analyzed": 3,
                "model_used": "fake",
                "status": "IMMUTABLE",
            },
        )
        vector_store.upsert(
            "macro_1", embedder.embed_text("a weekly summary"), {"node_type": "report"}
        )

        assert search("a weekly summary") == []


class TestWeightingDecidesWhoSurvives:
    """
    Distances here are set by hand rather than left to the stand-in
    embedder, which turns text into a hash and so puts two sentences that
    mean the same thing nowhere near each other. Placing vectors by angle
    makes the closeness exact and the ranking arithmetic checkable.
    """

    @pytest.fixture
    def by_distance(
        self, seed_observation, graph_store, vector_store, embedder, vector_at_angle
    ):
        """Seed a node at a chosen closeness to the search."""

        def _seed(node_id: str, radians: float, *, signal: str = "STANDARD") -> str:
            return seed_observation(
                node_id, f"content of {node_id}", signal=signal,
                vector=vector_at_angle(radians),
            )

        return _seed

    @pytest.fixture
    def search_at_origin(self, graph_store, vector_store, vector_at_angle):
        """Search from the reference direction, so closeness is the cosine."""

        def _search(**limits):
            return semantic.find_by_resemblance(
                vector_at_angle(0.0),
                exclude_episode=NOTHING_EXCLUDED,
                graph=graph_store,
                vectors=vector_store,
                config=PipelineConfig(**limits),
            )

        return _search

    def test_a_weighty_node_is_rescued_from_below_the_cut(
        self, by_distance, search_at_origin
    ):
        # This is what fetching more than is kept is for. Three ordinary
        # nodes are closer on plain distance; the weighty one belongs above
        # them once its weight counts, and would have been thrown away
        # before anything could notice if only the top matches were taken.
        for index in range(3):
            by_distance(f"obs_close_{index}", 0.45)          # cosine ≈ 0.90
        by_distance("obs_weighty", 0.93, signal="CRITICAL")  # ≈ 0.60 × 2.0

        found = search_at_origin(pass_a_keep=1)

        assert [c.node_id for c in found] == ["obs_weighty"]

    def test_between_equals_the_closer_one_wins(self, by_distance, search_at_origin):
        by_distance("obs_near", 0.2)
        by_distance("obs_far", 1.0)

        found = search_at_origin(pass_a_keep=1)

        assert [c.node_id for c in found] == ["obs_near"]

    def test_weight_alone_does_not_beat_a_much_closer_match(
        self, by_distance, search_at_origin
    ):
        # Weighting tilts the ranking; it does not overturn it. A weighty
        # node that is barely related still loses to a near-exact match.
        by_distance("obs_exact", 0.0)                       # cosine 1.0
        by_distance("obs_distant", 1.35, signal="CRITICAL")  # ≈ 0.22 × 2.0

        found = search_at_origin(pass_a_keep=1)

        assert [c.node_id for c in found] == ["obs_exact"]

    def test_only_the_number_asked_for_is_kept(self, by_distance, search_at_origin):
        for index in range(5):
            by_distance(f"obs_{index}", 0.1 * index)

        assert len(search_at_origin(pass_a_keep=2)) == 2

    def test_the_kept_ones_come_back_best_first(self, by_distance, search_at_origin):
        by_distance("obs_far", 1.0)
        by_distance("obs_near", 0.1)
        by_distance("obs_middle", 0.5)

        found = search_at_origin(pass_a_keep=3)

        assert [c.node_id for c in found] == ["obs_near", "obs_middle", "obs_far"]

    def test_fetching_too_few_loses_the_weighty_one(
        self, by_distance, search_at_origin
    ):
        # The mirror of the rescue test, and the reason the over-fetch is
        # not just a tuning knob: cut the fetch down and the node that
        # weighting would have saved never reaches the ranking at all.
        for index in range(3):
            by_distance(f"obs_close_{index}", 0.45)
        by_distance("obs_weighty", 0.93, signal="CRITICAL")

        found = search_at_origin(pass_a_keep=1, pass_a_overfetch=3)

        assert [c.node_id for c in found] != ["obs_weighty"]


class TestWhenTheSearchBreaks:
    def test_a_failing_index_costs_the_search_and_nothing_else(
        self, graph_store, embedder, caplog
    ):
        class BrokenIndex:
            def hybrid_search(self, *args, **kwargs):
                raise RuntimeError("index is unavailable")

        with caplog.at_level(logging.WARNING):
            found = semantic.find_by_resemblance(
                embedder.embed_text("anything"),
                exclude_episode=NOTHING_EXCLUDED,
                graph=graph_store,
                vectors=BrokenIndex(),
                config=PipelineConfig(),
            )

        assert found == []
        assert any("search failed" in r.getMessage() for r in caplog.records)
