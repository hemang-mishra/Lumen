"""
Tests for saving one episode, all of it or none of it.

Run against a real graph and a real search index. The central claim here —
that a failure partway through leaves nothing behind — cannot be checked
against a stand-in, because a stand-in agrees it rolled back whether or not
anything did.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lumen.pipeline.orchestration import commit, embed
from lumen.pipeline.orchestration.contracts import (
    GraphWriteFailed,
    IndexEntry,
    IndexWriteFailed,
)
from lumen.schemas.edges import LogicalEdgeType, LumenEdge
from lumen.schemas.enums import BookkeepingOperation
from lumen.schemas.pipeline import (
    GraphWritePlan,
    PlannedBookkeeping,
    PlannedEdge,
    PlannedNode,
)

MOMENT = datetime(2026, 6, 11, 20, 0, tzinfo=UTC)


@pytest.fixture
def episode_plan(sample_episode, sample_observation):
    """One episode and one finding, with the link between them."""
    return GraphWritePlan(
        nodes=[
            PlannedNode(node_type="EpisodeNode", node=sample_episode),
            PlannedNode(node_type="ObservationNode", node=sample_observation),
        ],
        edges=[
            PlannedEdge(
                logical_type=LogicalEdgeType.CONTAINS,
                table="contains_obs",
                from_node_id=sample_episode.node_id,
                to_node_id=sample_observation.node_id,
                edge=LumenEdge(
                    source_node_id=sample_episode.node_id,
                    target_node_id=sample_observation.node_id,
                    valid_from=MOMENT,
                ),
            )
        ],
    )


class BrokenIndex:
    """A search index that refuses everything."""

    def upsert(self, node_id, vector, payload):
        raise RuntimeError("the index is down")


class PartlyBrokenIndex:
    """A search index that refuses one particular record."""

    def __init__(self, refuse: str) -> None:
        self.refuse = refuse
        self.accepted: list[str] = []

    def upsert(self, node_id, vector, payload):
        if node_id == self.refuse:
            raise RuntimeError("not that one")
        self.accepted.append(node_id)


class TestTheHappyPath:
    def test_records_and_links_both_land(
        self, episode_plan, graph_store, vector_store, sample_episode, sample_observation
    ):
        report = commit.commit(episode_plan, [], graph=graph_store, vectors=vector_store)

        assert graph_store.get_node(sample_episode.node_id) is not None
        assert graph_store.get_node(sample_observation.node_id) is not None
        assert report.nodes_written == [sample_episode.node_id, sample_observation.node_id]
        assert report.edges_written == [
            ("contains_obs", sample_episode.node_id, sample_observation.node_id)
        ]

    def test_the_search_entries_are_written_too(
        self, episode_plan, graph_store, vector_store, embedder, sample_observation
    ):
        entries = embed.prepare_index(episode_plan, embedder=embedder)

        report = commit.commit(
            episode_plan, entries, graph=graph_store, vectors=vector_store
        )

        assert report.vectors_written == [sample_observation.node_id]
        assert report.unindexed_node_ids == []

    def test_a_small_update_to_an_existing_record_is_applied(
        self, graph_store, vector_store, seed_pattern
    ):
        seed_pattern("pat_existing")
        before = graph_store.get_node("pat_existing")["evidence_count"]
        plan = GraphWritePlan(
            bookkeeping=[
                PlannedBookkeeping(
                    operation=BookkeepingOperation.RECORD_REINFORCEMENT,
                    node_id="pat_existing",
                    at=MOMENT,
                )
            ]
        )

        commit.commit(plan, [], graph=graph_store, vectors=vector_store)

        assert graph_store.get_node("pat_existing")["evidence_count"] == before + 1

    def test_every_small_update_has_exactly_one_thing_it_can_do(self):
        # There is no way to pass a field name into any of them, so nothing
        # the person wrote can be reached through this path even by mistake.
        assert set(commit.BOOKKEEPING_OPERATIONS) == set(BookkeepingOperation)


class TestWhenTheGraphFails:
    def test_a_failure_partway_through_leaves_the_graph_untouched(
        self, graph_store, vector_store, sample_episode, sample_observation
    ):
        # The guarantee the whole save step exists to provide. A half-saved
        # entry reads as a complete one, and nothing downstream could tell.
        plan = GraphWritePlan(
            nodes=[
                PlannedNode(node_type="EpisodeNode", node=sample_episode),
                PlannedNode(node_type="ObservationNode", node=sample_observation),
            ],
            edges=[
                PlannedEdge(
                    logical_type=LogicalEdgeType.CONTAINS,
                    table="contains_obs",
                    from_node_id=sample_episode.node_id,
                    to_node_id=sample_observation.node_id,
                    edge=LumenEdge(
                        source_node_id=sample_episode.node_id,
                        target_node_id=sample_observation.node_id,
                        valid_from=MOMENT,
                    ),
                )
            ],
            bookkeeping=[
                PlannedBookkeeping(
                    operation=BookkeepingOperation.RECORD_REINFORCEMENT,
                    node_id="pat_that_does_not_exist",
                    at=MOMENT,
                )
            ],
        )

        with pytest.raises(GraphWriteFailed):
            commit.commit(plan, [], graph=graph_store, vectors=vector_store)

        assert graph_store.get_node(sample_episode.node_id) is None
        assert graph_store.get_node(sample_observation.node_id) is None

    def test_nothing_is_indexed_when_the_graph_write_fails(
        self, graph_store, sample_observation
    ):
        # Index entries for records that do not exist would be worse than
        # useless: every search would return them and every read-back would
        # find nothing.
        index = PartlyBrokenIndex(refuse="never-reached")
        plan = GraphWritePlan(
            nodes=[PlannedNode(node_type="NotARealTable", node=sample_observation)]
        )

        with pytest.raises(GraphWriteFailed):
            commit.commit(
                plan,
                [
                    IndexEntry(
                        node_id="obs_x",
                        node_type="ObservationNode",
                        text="anything",
                        vector=[0.1] * 768,
                    )
                ],
                graph=graph_store,
                vectors=index,
            )

        assert index.accepted == []

    def test_an_unknown_small_update_is_refused(self, graph_store, vector_store):
        plan = GraphWritePlan.model_construct(
            nodes=[],
            edges=[],
            bookkeeping=[
                PlannedBookkeeping.model_construct(
                    operation="NOT_A_REAL_OPERATION", node_id="pat_x", at=MOMENT
                )
            ],
            existing_node_ids=frozenset(),
        )

        with pytest.raises(GraphWriteFailed, match="no such bookkeeping"):
            commit.commit(plan, [], graph=graph_store, vectors=vector_store)


class TestWhenTheIndexFails:
    def test_the_graph_half_is_kept(
        self, episode_plan, graph_store, embedder, sample_observation
    ):
        # Those records are real and correct. They simply cannot be found by
        # meaning yet, and undoing them would be the wrong repair.
        entries = embed.prepare_index(episode_plan, embedder=embedder)

        with pytest.raises(IndexWriteFailed):
            commit.commit(episode_plan, entries, graph=graph_store, vectors=BrokenIndex())

        assert graph_store.get_node(sample_observation.node_id) is not None

    def test_the_records_that_cannot_be_found_are_named(
        self, episode_plan, graph_store, embedder, sample_observation
    ):
        entries = embed.prepare_index(episode_plan, embedder=embedder)

        with pytest.raises(IndexWriteFailed) as failure:
            commit.commit(episode_plan, entries, graph=graph_store, vectors=BrokenIndex())

        assert failure.value.missing == [sample_observation.node_id]

    def test_the_report_survives_the_failure(
        self, episode_plan, graph_store, embedder, sample_episode
    ):
        # Everything that did land still has to reach the run log, which is
        # what a later repair reads to work out what is missing.
        entries = embed.prepare_index(episode_plan, embedder=embedder)

        with pytest.raises(IndexWriteFailed) as failure:
            commit.commit(episode_plan, entries, graph=graph_store, vectors=BrokenIndex())

        assert sample_episode.node_id in failure.value.report.nodes_written
        assert len(failure.value.report.edges_written) == 1

    def test_one_bad_record_does_not_hide_the_good_ones(
        self, graph_store, embedder, sample_observation, sample_pattern
    ):
        # Stopping at the first failure would leave nine healthy records
        # unaccounted for behind one broken one.
        plan = GraphWritePlan(
            nodes=[
                PlannedNode(node_type="ObservationNode", node=sample_observation),
                PlannedNode(node_type="PatternNode", node=sample_pattern),
            ]
        )
        entries = embed.prepare_index(plan, embedder=embedder)
        index = PartlyBrokenIndex(refuse=sample_observation.node_id)

        with pytest.raises(IndexWriteFailed) as failure:
            commit.commit(plan, entries, graph=graph_store, vectors=index)

        assert index.accepted == [sample_pattern.node_id]
        assert failure.value.missing == [sample_observation.node_id]
