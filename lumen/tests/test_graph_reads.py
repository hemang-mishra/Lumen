"""
Tests for the three anchor lookups candidate retrieval depends on.

These run against a real Kuzu database rather than a stand-in, on purpose.
Every one of them is a Cypher query against typed edge tables, and a
stand-in that answered them from a Python dict would pass whether or not
the query was right — which is the only thing being tested here.

The third lookup is the one that matters most. It exists for the case
similarity search handles worst: someone describing recovery uses none of
the words they used describing the injury, so no measure of distance will
ever connect the two. This lookup does not read either of them.
"""

from __future__ import annotations

import pytest

from lumen.graph.kuzu_impl import KuzuGraphProvider

CONTENT_TYPES = ["ObservationNode", "EventNode", "SessionNode"]


@pytest.fixture
def graph(tmp_path):
    """A real, empty Kuzu database."""
    provider = KuzuGraphProvider(str(tmp_path / "reads_db"))
    provider.init_schema()
    yield provider
    provider.close()


def person(graph, name: str, node_id: str | None = None) -> str:
    node_id = node_id or f"person_{name.lower()}"
    graph.write_node(
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


def observation(graph, node_id: str, **overrides) -> str:
    props = {
        "node_id": node_id,
        "episode_id": "ep_2026_06_11_001",
        "occurred_at": "2026-06-11T20:00:00Z",
        "created_at": "2026-06-11T21:00:00Z",
        "valid_from": "2026-06-11T21:00:00Z",
        "type": "RELATIONAL_DYNAMIC",
        "content": "Something about the relationship",
        "signal_strength": "STANDARD",
        "provenance": "USER_GENERATED",
        "verification_status": "IMPLICIT",
        "extraction_confidence": "STANDARD",
        "status": "ACTIVE",
        "extraction_model": "fake",
        "extraction_attempt": 1,
    }
    props.update(overrides)
    graph.write_node("ObservationNode", props)
    return node_id


def episode(graph, node_id: str, **overrides) -> str:
    props = {
        "node_id": node_id,
        "entry_id": "sess_1",
        "occurred_at": "2026-06-11T20:00:00Z",
        "created_at": "2026-06-11T21:00:00Z",
        "valid_from": "2026-06-11T21:00:00Z",
        "event_date": "2026-06-11",
        "session_label": "A",
        "source_modality": "TEXT_ENTRY",
        "entry_class": "REFLECTION",
        "episode_summary": "a topic",
        "episode_index": 1,
        "total_episodes_in_entry": 1,
        "coreference_map_id": "coref_1",
        "reconciliation_status": "COMPLETE",
        "raw_text_hash": "hash",
    }
    props.update(overrides)
    graph.write_node("EpisodeNode", props)
    return node_id


def pattern(graph, node_id: str, **overrides) -> str:
    props = {
        "node_id": node_id,
        "created_at": "2026-06-11T21:00:00Z",
        "valid_from": "2026-06-11T21:00:00Z",
        "pattern_name": "Comparison spiral",
        "pattern_description": "Comparing after seeing others' work",
        "domain": "EMOTIONAL",
        "signal_strength": "STANDARD",
        "provenance": "USER_GENERATED",
        "verification_status": "IMPLICIT",
        "is_canonical": True,
        "status": "ACTIVE",
        "version": 1,
        "last_reinforced_at": "2026-06-11T21:00:00Z",
        "evidence_count": 1,
        "query_frequency": 0,
    }
    props.update(overrides)
    graph.write_node("PatternNode", props)
    return node_id


class TestFindingWhatMentionsSomeone:
    def test_an_observation_about_a_person_is_found(self, graph):
        person(graph, "Alex")
        observation(graph, "obs_1")
        graph.write_edge("mentions_obs", "obs_1", "person_alex")

        found = graph.find_linked_to_person("Alex", node_types=CONTENT_TYPES)

        assert [row["node_id"] for row in found] == ["obs_1"]

    def test_somebody_nobody_has_mentioned_finds_nothing(self, graph):
        person(graph, "Alex")
        observation(graph, "obs_1")
        graph.write_edge("mentions_obs", "obs_1", "person_alex")

        assert graph.find_linked_to_person("Priya", node_types=CONTENT_TYPES) == []

    def test_an_empty_graph_finds_nothing(self, graph):
        # The first entry a person ever writes runs against exactly this.
        assert graph.find_linked_to_person("Alex", node_types=CONTENT_TYPES) == []

    def test_a_retired_observation_is_left_out(self, graph):
        person(graph, "Alex")
        observation(graph, "obs_live")
        observation(graph, "obs_gone", status="SUSPENDED")
        graph.write_edge("mentions_obs", "obs_live", "person_alex")
        graph.write_edge("mentions_obs", "obs_gone", "person_alex")

        found = graph.find_linked_to_person("Alex", node_types=CONTENT_TYPES)

        assert [row["node_id"] for row in found] == ["obs_live"]

    def test_a_node_type_with_no_route_to_a_person_is_skipped(self, graph):
        # A pattern reaches a person only through the observation that
        # produced it, and that second hop is not made here.
        person(graph, "Alex")
        pattern(graph, "pat_1")

        found = graph.find_linked_to_person("Alex", node_types=["PatternNode"])

        assert found == []

    def test_the_limit_is_honoured(self, graph):
        person(graph, "Alex")
        for index in range(5):
            observation(graph, f"obs_{index}")
            graph.write_edge("mentions_obs", f"obs_{index}", "person_alex")

        assert len(graph.find_linked_to_person("Alex", node_types=CONTENT_TYPES, limit=2)) == 2

    def test_only_the_asked_for_kinds_come_back(self, graph):
        person(graph, "Alex")
        observation(graph, "obs_1")
        graph.write_edge("mentions_obs", "obs_1", "person_alex")

        assert graph.find_linked_to_person("Alex", node_types=["EventNode"]) == []


class TestFindingByPastPeriod:
    def test_a_pattern_from_that_period_is_found(self, graph):
        pattern(graph, "pat_1", era_tag="EXAM_PREP")

        found = graph.find_by_era("EXAM_PREP", node_types=["PatternNode"])

        assert [row["node_id"] for row in found] == ["pat_1"]

    def test_an_episode_records_the_period_under_a_different_name(self, graph):
        # Patterns and beliefs call it era_tag; episodes call it
        # historical_era. The lookup asks each table for its own.
        episode(graph, "ep_1", historical_era="EXAM_PREP")

        found = graph.find_by_era("EXAM_PREP", node_types=["EpisodeNode"])

        assert [row["node_id"] for row in found] == ["ep_1"]

    def test_a_different_period_is_not_returned(self, graph):
        pattern(graph, "pat_1", era_tag="EXAM_PREP")

        assert graph.find_by_era("FIRST_JOB", node_types=["PatternNode"]) == []

    def test_a_superseded_pattern_is_left_out(self, graph):
        pattern(graph, "pat_old", era_tag="EXAM_PREP", status="SUPERSEDED")
        pattern(graph, "pat_now", era_tag="EXAM_PREP")

        found = graph.find_by_era("EXAM_PREP", node_types=["PatternNode"])

        assert [row["node_id"] for row in found] == ["pat_now"]

    def test_a_node_type_that_records_no_period_is_skipped(self, graph):
        observation(graph, "obs_1")

        assert graph.find_by_era("EXAM_PREP", node_types=["ObservationNode"]) == []

    def test_the_limit_is_honoured(self, graph):
        for index in range(4):
            pattern(graph, f"pat_{index}", era_tag="EXAM_PREP")

        assert len(graph.find_by_era("EXAM_PREP", node_types=["PatternNode"], limit=2)) == 2


class TestFindingUnresolvedWeightyMaterial:
    WEIGHTY = [
        "INAUTHENTICITY_STATE",
        "IDENTITY_FUSION_STATE",
        "EXISTENTIAL_REFLECTION",
        "SUPPRESSED_EMOTION_SURFACING",
    ]

    def link(self, graph, episode_id: str, observation_id: str) -> None:
        graph.write_edge("contains_obs", episode_id, observation_id)

    def test_it_is_reached_through_the_episode(self, graph):
        # An observation has no record of whether reconciliation is
        # outstanding; only its episode does.
        episode(graph, "ep_open", reconciliation_status="PENDING_RERECONCILIATION")
        observation(graph, "obs_1", type="IDENTITY_FUSION_STATE", signal_strength="HIGH")
        self.link(graph, "ep_open", "obs_1")

        found = graph.find_unresolved_high_signal(self.WEIGHTY)

        assert [row["node_id"] for row in found] == ["obs_1"]

    def test_a_settled_episode_surfaces_nothing(self, graph):
        episode(graph, "ep_done", reconciliation_status="COMPLETE")
        observation(graph, "obs_1", type="IDENTITY_FUSION_STATE", signal_strength="HIGH")
        self.link(graph, "ep_done", "obs_1")

        assert graph.find_unresolved_high_signal(self.WEIGHTY) == []

    def test_an_ordinary_observation_is_not_weighty(self, graph):
        episode(graph, "ep_open", reconciliation_status="PENDING_RERECONCILIATION")
        observation(graph, "obs_1", type="EMOTION")
        self.link(graph, "ep_open", "obs_1")

        assert graph.find_unresolved_high_signal(self.WEIGHTY) == []

    @pytest.mark.parametrize("weighty_type", WEIGHTY)
    def test_each_weighty_kind_is_reachable(self, graph, weighty_type):
        episode(graph, "ep_open", reconciliation_status="PENDING_RERECONCILIATION")
        observation(graph, "obs_1", type=weighty_type, signal_strength="HIGH")
        self.link(graph, "ep_open", "obs_1")

        assert len(graph.find_unresolved_high_signal(self.WEIGHTY)) == 1

    def test_a_retired_observation_is_left_out(self, graph):
        episode(graph, "ep_open", reconciliation_status="PENDING_RERECONCILIATION")
        observation(
            graph, "obs_1", type="CORE_WOUND", signal_strength="HIGH", status="SUSPENDED"
        )
        self.link(graph, "ep_open", "obs_1")

        assert graph.find_unresolved_high_signal(["CORE_WOUND"]) == []

    def test_asking_for_nothing_returns_nothing(self, graph):
        assert graph.find_unresolved_high_signal([]) == []

    def test_the_limit_is_honoured(self, graph):
        episode(graph, "ep_open", reconciliation_status="PENDING_RERECONCILIATION")
        for index in range(4):
            observation(graph, f"obs_{index}", type="CORE_WOUND", signal_strength="HIGH")
            self.link(graph, "ep_open", f"obs_{index}")

        assert len(graph.find_unresolved_high_signal(["CORE_WOUND"], limit=2)) == 2


class TestWhatComesBack:
    def test_a_row_says_which_kind_of_node_it_is(self, graph):
        # Candidate building reads this rather than guessing from the id.
        person(graph, "Alex")
        observation(graph, "obs_1")
        graph.write_edge("mentions_obs", "obs_1", "person_alex")

        found = graph.find_linked_to_person("Alex", node_types=CONTENT_TYPES)

        assert found[0]["_label"] == "ObservationNode"

    def test_a_row_carries_the_content_it_was_written_with(self, graph):
        pattern(graph, "pat_1", era_tag="EXAM_PREP")

        found = graph.find_by_era("EXAM_PREP", node_types=["PatternNode"])

        assert found[0]["pattern_name"] == "Comparison spiral"
