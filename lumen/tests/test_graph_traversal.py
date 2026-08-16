"""
Tests for the named questions the graph can answer.

Run against a real Kuzu database. Every one of these is a query, and a
stand-in answering from a dictionary would agree with whatever it was told —
which is the only thing worth checking here.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

JANUARY = datetime(2026, 1, 1, tzinfo=UTC)
MARCH = datetime(2026, 3, 1, tzinfo=UTC)
JUNE = datetime(2026, 6, 1, tzinfo=UTC)


@pytest.fixture
def seed_versions(graph_store):
    """A belief in three versions, each superseding the last."""

    def _seed(prefix: str = "bel_growth") -> list[str]:
        moments = [JANUARY, MARCH, JUNE]
        ids = []
        for index, moment in enumerate(moments, start=1):
            node_id = f"{prefix}_v{index}"
            graph_store.write_node(
                "BeliefNode",
                {
                    "node_id": node_id,
                    "version": index,
                    "previous_version_id": f"{prefix}_v{index - 1}" if index > 1 else None,
                    "created_at": moment.isoformat(),
                    "valid_from": moment.isoformat(),
                    "last_reinforced_at": moment.isoformat(),
                    "belief_statement": f"version {index} of the belief",
                    "belief_source_summary": "said so",
                    "domain": "SELF_CONCEPT",
                    "signal_strength": "HIGH",
                    "provenance": "USER_GENERATED",
                    "verification_status": "IMPLICIT",
                    "evidence_count": index,
                    "query_frequency": 0,
                    "is_contradicted": False,
                    "status": "ACTIVE" if index == 3 else "SUPERSEDED",
                },
            )
            if index > 1:
                graph_store.write_edge(
                    "evolved_from_bel",
                    node_id,
                    f"{prefix}_v{index - 1}",
                    {"valid_from": moment.isoformat(), "decision_id": f"d_{index}"},
                )
            ids.append(node_id)
        return ids

    return _seed


@pytest.fixture
def seed_episode(graph_store):
    """An episode with a finding, an event and a reflection hanging off it."""

    def _seed(episode_id: str = "ep_2026_06_11_001") -> dict[str, str]:
        moment = JUNE.isoformat()
        graph_store.write_node(
            "EpisodeNode",
            {
                "node_id": episode_id,
                "entry_id": "entry_1",
                "occurred_at": moment,
                "created_at": moment,
                "valid_from": moment,
                "event_date": "2026-06-01",
                "session_label": "A",
                "source_modality": "TEXT_ENTRY",
                "entry_class": "REFLECTION",
                "episode_summary": "a day of comparing",
                "episode_index": 1,
                "total_episodes_in_entry": 1,
                "coreference_map_id": "coref_entry_1",
                "reconciliation_status": "COMPLETE",
                "raw_text_hash": "hash",
                "language_tags": ["en"],
            },
        )
        children = {
            "observation": ("ObservationNode", "contains_obs"),
            "event": ("EventNode", "contains_evt"),
            "session": ("SessionNode", "contains_sess"),
        }
        made: dict[str, str] = {"episode": episode_id}
        for name, (table, edge) in children.items():
            node_id = f"{name[:3]}_{episode_id}"
            graph_store.write_node(table, _child_row(table, node_id, episode_id, moment))
            graph_store.write_edge(edge, episode_id, node_id, {"valid_from": moment})
            made[name] = node_id
        return made

    return _seed


def _child_row(table: str, node_id: str, episode_id: str, moment: str) -> dict:
    common = {
        "node_id": node_id,
        "episode_id": episode_id,
        "occurred_at": moment,
        "created_at": moment,
        "valid_from": moment,
        "signal_strength": "STANDARD",
        "status": "ACTIVE",
    }
    if table == "ObservationNode":
        return {
            **common,
            "type": "PATTERN",
            "content": "the comparing is what hurts",
            "provenance": "USER_GENERATED",
            "verification_status": "IMPLICIT",
            "extraction_confidence": "STANDARD",
            "extraction_model": "fake",
            "extraction_attempt": 1,
            "raw_evidence": ["the comparing is the thing"],
        }
    if table == "EventNode":
        return {**common, "event_summary": "ate at the cafe alone"}
    return {
        **common,
        "event_date": "2026-06-01",
        "session_label": "A",
        "session_summary": "thought it through",
    }


class TestListingRecords:
    def test_records_come_back_newest_first(self, graph_store, seed_versions):
        seed_versions()

        found = graph_store.find_nodes(["BeliefNode"], active_only=False)

        assert [row["node_id"] for row in found] == [
            "bel_growth_v3",
            "bel_growth_v2",
            "bel_growth_v1",
        ]

    def test_only_live_records_by_default(self, graph_store, seed_versions):
        seed_versions()

        found = graph_store.find_nodes(["BeliefNode"])

        assert [row["node_id"] for row in found] == ["bel_growth_v3"]

    def test_a_date_range_narrows_the_answer(self, graph_store, seed_versions):
        seed_versions()

        found = graph_store.find_nodes(
            ["BeliefNode"], since=MARCH, until=MARCH, active_only=False
        )

        assert [row["node_id"] for row in found] == ["bel_growth_v2"]

    def test_a_part_of_life_narrows_it_too(self, graph_store, seed_versions):
        seed_versions()

        assert graph_store.find_nodes(["BeliefNode"], domain="CAREER") == []
        assert graph_store.find_nodes(["BeliefNode"], domain="SELF_CONCEPT")

    def test_several_kinds_are_merged_into_one_ordered_list(
        self, graph_store, seed_versions, seed_episode
    ):
        seed_versions()
        seed_episode()

        found = graph_store.find_nodes(
            ["BeliefNode", "EpisodeNode"], active_only=False
        )

        kinds = {row["_label"] for row in found}
        assert kinds == {"BeliefNode", "EpisodeNode"}

    def test_asking_for_no_kind_in_particular_searches_everything(
        self, graph_store, seed_episode
    ):
        seed_episode()

        assert len(graph_store.find_nodes([], limit=100)) >= 4

    def test_an_unknown_kind_is_ignored_rather_than_failing(
        self, graph_store, seed_episode
    ):
        seed_episode()

        assert graph_store.find_nodes(["NotARealTable"], limit=100)

    def test_paging_walks_through_without_repeating(self, graph_store, seed_versions):
        seed_versions()

        first = graph_store.find_nodes(["BeliefNode"], active_only=False, limit=2)
        second = graph_store.find_nodes(
            ["BeliefNode"], active_only=False, limit=2, offset=2
        )

        assert len(first) == 2
        assert len(second) == 1
        assert {r["node_id"] for r in first}.isdisjoint({r["node_id"] for r in second})

    def test_an_empty_graph_answers_with_nothing(self, graph_store):
        assert graph_store.find_nodes(["BeliefNode"]) == []


class TestCounting:
    def test_every_kind_is_reported_even_at_zero(self, graph_store):
        from lumen.graph.kuzu_impl import NODE_TABLES

        counts = graph_store.count_by_type()

        assert set(counts) == set(NODE_TABLES)
        assert all(value == 0 for value in counts.values())

    def test_retired_records_are_counted_too(self, graph_store, seed_versions):
        # "How much is in here" is a different question from "how much of it
        # still applies", and this one is the first.
        seed_versions()

        assert graph_store.count_by_type()["BeliefNode"] == 3


class TestWalkingOutFromARecord:
    def test_the_starting_record_is_always_included(self, graph_store, seed_episode):
        # A record with nothing attached should come back as itself, not as
        # nothing at all.
        made = seed_episode()

        slice_ = graph_store.get_neighborhood(made["observation"], depth=1)

        assert made["observation"] in {row["node_id"] for row in slice_.nodes}

    def test_one_step_reaches_what_is_directly_attached(
        self, graph_store, seed_episode
    ):
        made = seed_episode()

        slice_ = graph_store.get_neighborhood(made["episode"], depth=1)

        assert {row["node_id"] for row in slice_.nodes} == set(made.values())

    def test_two_steps_reach_what_is_attached_to_that(
        self, graph_store, seed_versions
    ):
        seed_versions()

        slice_ = graph_store.get_neighborhood("bel_growth_v1", depth=2)

        assert {row["node_id"] for row in slice_.nodes} == {
            "bel_growth_v1",
            "bel_growth_v2",
            "bel_growth_v3",
        }

    def test_one_step_stops_at_one_step(self, graph_store, seed_versions):
        seed_versions()

        slice_ = graph_store.get_neighborhood("bel_growth_v1", depth=1)

        assert "bel_growth_v3" not in {row["node_id"] for row in slice_.nodes}

    def test_the_links_come_back_with_the_records(self, graph_store, seed_episode):
        # Records without their links are a list, and links without their
        # records are identifiers nobody can read.
        made = seed_episode()

        slice_ = graph_store.get_neighborhood(made["episode"], depth=1)

        assert {edge.edge_type for edge in slice_.edges} == {
            "contains_obs",
            "contains_evt",
            "contains_sess",
        }

    def test_a_link_carries_its_own_details(self, graph_store, seed_versions):
        seed_versions()

        slice_ = graph_store.get_neighborhood("bel_growth_v2", depth=1)
        evolved = next(e for e in slice_.edges if e.edge_type == "evolved_from_bel")

        assert evolved.properties["decision_id"].startswith("d_")

    def test_direction_can_be_narrowed(self, graph_store, seed_episode):
        made = seed_episode()

        outward = graph_store.get_neighborhood(made["episode"], direction="out")
        inward = graph_store.get_neighborhood(made["episode"], direction="in")

        assert len(outward.edges) == 3
        assert inward.edges == []

    def test_only_the_asked_for_kinds_of_link_are_followed(
        self, graph_store, seed_episode
    ):
        made = seed_episode()

        slice_ = graph_store.get_neighborhood(
            made["episode"], edge_types=["contains_obs"]
        )

        assert {row["node_id"] for row in slice_.nodes} == {
            made["episode"],
            made["observation"],
        }

    def test_a_record_that_does_not_exist_gives_nothing(self, graph_store):
        assert graph_store.get_neighborhood("nope") == ([], [], False)

    def test_a_cut_short_answer_says_so(self, graph_store, seed_episode):
        # A piece of the graph that was cut and one that was genuinely that
        # size look identical otherwise, and a partial graph drawn as a whole
        # one is a wrong answer that looks right.
        made = seed_episode()

        slice_ = graph_store.get_neighborhood(made["episode"], depth=2, limit=2)

        assert slice_.truncated is True

    def test_a_complete_answer_says_that_too(self, graph_store, seed_episode):
        made = seed_episode()

        assert graph_store.get_neighborhood(made["episode"]).truncated is False


class TestWithdrawnLinks:
    @pytest.fixture
    def with_a_withdrawn_link(self, graph_store, seed_episode):
        made = seed_episode()
        graph_store.conn.execute(
            "MATCH (:EpisodeNode)-[r:contains_obs]->(:ObservationNode) "
            "SET r.invalidated_at = '2026-06-15T00:00:00+00:00'"
        )
        return made

    def test_they_are_not_followed_by_default(
        self, graph_store, with_a_withdrawn_link
    ):
        # A rolled-back decision should not still be shaping what the graph
        # appears to say.
        slice_ = graph_store.get_neighborhood(with_a_withdrawn_link["episode"])

        assert "contains_obs" not in {edge.edge_type for edge in slice_.edges}

    def test_they_can_be_asked_for(self, graph_store, with_a_withdrawn_link):
        slice_ = graph_store.get_neighborhood(
            with_a_withdrawn_link["episode"], include_invalidated=True
        )

        assert "contains_obs" in {edge.edge_type for edge in slice_.edges}

    def test_one_withdrawn_later_was_still_live_on_an_earlier_date(
        self, graph_store, with_a_withdrawn_link
    ):
        slice_ = graph_store.get_neighborhood(
            with_a_withdrawn_link["episode"], as_of=JUNE
        )

        assert "contains_obs" in {edge.edge_type for edge in slice_.edges}


class TestAskingAboutAPastDate:
    def test_records_made_later_are_left_out(self, graph_store, seed_versions):
        seed_versions()

        slice_ = graph_store.get_neighborhood("bel_growth_v1", depth=2, as_of=MARCH)

        assert {row["node_id"] for row in slice_.nodes} == {
            "bel_growth_v1",
            "bel_growth_v2",
        }

    def test_links_to_records_that_were_left_out_go_too(
        self, graph_store, seed_versions
    ):
        seed_versions()

        slice_ = graph_store.get_neighborhood("bel_growth_v1", depth=2, as_of=MARCH)

        reachable = {row["node_id"] for row in slice_.nodes}
        for edge in slice_.edges:
            assert edge.from_node_id in reachable
            assert edge.to_node_id in reachable

    def test_the_record_asked_about_is_kept_even_if_it_is_newer(
        self, graph_store, seed_versions
    ):
        # Someone asking what was around a record in March is asking about
        # its surroundings; answering with nothing would be a strange reading.
        seed_versions()

        slice_ = graph_store.get_neighborhood("bel_growth_v3", as_of=JANUARY)

        assert "bel_growth_v3" in {row["node_id"] for row in slice_.nodes}


class TestVersionChains:
    def test_the_whole_history_comes_back_oldest_first(
        self, graph_store, seed_versions
    ):
        seed_versions()

        chain = graph_store.get_version_chain("bel_growth_v3")

        assert [row["node_id"] for row in chain] == [
            "bel_growth_v1",
            "bel_growth_v2",
            "bel_growth_v3",
        ]

    def test_it_reads_the_same_from_anywhere_in_the_chain(
        self, graph_store, seed_versions
    ):
        # Someone who reached a record through a search has no idea whether
        # they are looking at the first version or the fifth.
        seed_versions()

        assert graph_store.get_version_chain("bel_growth_v1") == (
            graph_store.get_version_chain("bel_growth_v2")
        )

    def test_a_record_with_no_other_versions_is_a_chain_of_one(
        self, graph_store, seed_belief
    ):
        seed_belief("bel_alone")

        assert len(graph_store.get_version_chain("bel_alone")) == 1

    def test_a_kind_that_is_never_versioned_has_no_chain(
        self, graph_store, seed_episode, caplog
    ):
        made = seed_episode()

        assert graph_store.get_version_chain(made["observation"]) == []

    def test_a_record_that_does_not_exist_has_no_chain(self, graph_store):
        assert graph_store.get_version_chain("nope") == []

    def test_every_version_comes_back_in_the_same_shape(
        self, graph_store, seed_versions
    ):
        # Asking for one record by itself and asking for it as one of a kind
        # come back with different sets of columns. A chain assembled as it
        # was walked would describe the same history differently depending
        # on where the walk started.
        seed_versions()

        chain = graph_store.get_version_chain("bel_growth_v2")

        assert len({tuple(sorted(row)) for row in chain}) == 1

    def test_a_chain_that_points_at_itself_does_not_loop_forever(
        self, graph_store, seed_versions, caplog
    ):
        # Not reachable through the pipeline, but a graph written to by hand
        # can hold one, and an endless walk is a much worse failure than a
        # short chain.
        seed_versions()
        graph_store.conn.execute(
            "MATCH (n:BeliefNode) WHERE n.node_id = 'bel_growth_v1' "
            "SET n.previous_version_id = 'bel_growth_v3'"
        )

        chain = graph_store.get_version_chain("bel_growth_v2")

        assert len(chain) <= 3
        assert "loops back" in caplog.text

    def test_a_version_naming_one_that_is_gone_stops_there(
        self, graph_store, seed_belief, caplog
    ):
        seed_belief("bel_orphan", version=2)
        graph_store.conn.execute(
            "MATCH (n:BeliefNode) WHERE n.node_id = 'bel_orphan' "
            "SET n.previous_version_id = 'bel_never_written'"
        )

        assert [r["node_id"] for r in graph_store.get_version_chain("bel_orphan")] == [
            "bel_orphan"
        ]


class TestDecisionHistory:
    def test_every_decision_about_a_record_comes_back(
        self, graph_store, seed_episode, sample_decision_audit
    ):
        made = seed_episode()
        for index in (1, 2):
            audit = sample_decision_audit.model_copy(
                update={"node_id": f"d_test_{index}"}
            )
            graph_store.write_node("DecisionAuditNode", audit)
            graph_store.write_edge(
                "decided_by_obs",
                made["observation"],
                f"d_test_{index}",
                {"valid_from": JUNE.isoformat()},
            )

        history = graph_store.get_decision_history(made["observation"])

        assert {row["node_id"] for row in history} == {"d_test_1", "d_test_2"}

    def test_a_record_nobody_decided_about_has_no_history(
        self, graph_store, seed_episode
    ):
        made = seed_episode()

        assert graph_store.get_decision_history(made["event"]) == []


class TestEpisodeContents:
    def test_everything_the_entry_produced_comes_back(
        self, graph_store, seed_episode
    ):
        made = seed_episode()

        slice_ = graph_store.get_episode_contents(made["episode"])

        assert {row["node_id"] for row in slice_.nodes} == set(made.values())

    def test_the_episode_itself_is_first(self, graph_store, seed_episode):
        made = seed_episode()

        slice_ = graph_store.get_episode_contents(made["episode"])

        assert slice_.nodes[0]["node_id"] == made["episode"]

    def test_the_previous_episode_is_not_dragged_in(self, graph_store, seed_episode):
        # An episode also points at the one before it, and following that
        # would answer a wider question than the one asked.
        first = seed_episode("ep_2026_06_10_001")
        second = seed_episode("ep_2026_06_11_001")
        graph_store.write_edge(
            "follows_from",
            second["episode"],
            first["episode"],
            {"valid_from": JUNE.isoformat()},
        )

        slice_ = graph_store.get_episode_contents(second["episode"])

        assert first["episode"] not in {row["node_id"] for row in slice_.nodes}

    def test_an_episode_that_does_not_exist_gives_nothing(self, graph_store):
        assert graph_store.get_episode_contents("nope") == ([], [], False)


class TestFindingWhatIsAboutSomeone:
    @pytest.fixture
    def a_person_with_history(self, graph_store, seed_episode, seed_pattern):
        """Someone named in a finding, which in turn became a pattern."""
        made = seed_episode()
        moment = JUNE.isoformat()
        graph_store.write_node(
            "PersonEntityNode",
            {
                "node_id": "person_alex",
                "canonical_name": "Alex",
                "first_mentioned_at": moment,
                "last_mentioned_at": moment,
                "mention_count": 1,
                "relationship_to_user": "COLLEAGUE",
                "relationship_sentiment_trend": "MIXED",
                "is_canonical": True,
                "status": "ACTIVE",
            },
        )
        graph_store.write_edge(
            "mentions_obs", made["observation"], "person_alex", {"valid_from": moment}
        )
        seed_pattern("pat_comparison")
        graph_store.write_edge(
            "branches_to_obs_pat",
            made["observation"],
            "pat_comparison",
            {"valid_from": moment, "decision_id": "d_1", "confidence": 0.9},
        )
        return made

    def test_findings_that_name_the_person_come_back(
        self, graph_store, a_person_with_history
    ):
        found = graph_store.find_linked_to_person(
            "Alex", node_types=["ObservationNode"]
        )

        assert [row["node_id"] for row in found] == [
            a_person_with_history["observation"]
        ]

    def test_a_pattern_that_grew_out_of_one_comes_back_too(
        self, graph_store, a_person_with_history
    ):
        # A pattern never names a person itself. It is about them because a
        # finding about them turned into it, and "what do I know about Alex"
        # means the same thing either way.
        found = graph_store.find_linked_to_person("Alex", node_types=["PatternNode"])

        assert [row["node_id"] for row in found] == ["pat_comparison"]

    def test_a_pattern_reached_by_two_routes_is_offered_once(
        self, graph_store, a_person_with_history
    ):
        # Offering the same record twice wastes one of very few places in a
        # short list of candidates.
        graph_store.write_edge(
            "reinforces_obs_pat",
            a_person_with_history["observation"],
            "pat_comparison",
            {"valid_from": JUNE.isoformat(), "decision_id": "d_2", "confidence": 0.8},
        )

        found = graph_store.find_linked_to_person("Alex", node_types=["PatternNode"])

        assert len(found) == 1

    def test_a_withdrawn_link_does_not_carry_a_pattern_across(
        self, graph_store, a_person_with_history
    ):
        graph_store.conn.execute(
            "MATCH ()-[r:branches_to_obs_pat]->() "
            "SET r.invalidated_at = '2026-07-01T00:00:00+00:00'"
        )

        assert graph_store.find_linked_to_person("Alex", node_types=["PatternNode"]) == []

    def test_a_kind_with_no_route_to_a_person_is_skipped(
        self, graph_store, a_person_with_history, caplog
    ):
        import logging

        caplog.set_level(logging.DEBUG, logger="lumen.graph.kuzu_impl")

        assert (
            graph_store.find_linked_to_person("Alex", node_types=["LessonNode"]) == []
        )
        assert "no route" in caplog.text

    def test_somebody_nobody_knows_yields_nothing(
        self, graph_store, a_person_with_history
    ):
        assert (
            graph_store.find_linked_to_person(
                "Nobody", node_types=["ObservationNode", "PatternNode"]
            )
            == []
        )


class TestCausalChains:
    @pytest.fixture
    def seed_chain(self, graph_store, seed_episode):
        made = seed_episode()
        graph_store.write_node(
            "CausalChainNode",
            {
                "node_id": "chain_1",
                "episode_id": made["episode"],
                "created_at": JUNE.isoformat(),
                "valid_from": JUNE.isoformat(),
                "chain_summary": "trigger to lesson",
                "is_anticipatory": False,
                "step_count": 3,
                "status": "ACTIVE",
            },
        )
        graph_store.write_edge(
            "contains_chain", made["episode"], "chain_1", {"valid_from": JUNE.isoformat()}
        )
        for index in (3, 1, 2):
            graph_store.write_node(
                "CausalStepNode",
                {
                    "node_id": f"step_{index}",
                    "chain_id": "chain_1",
                    "step_index": index,
                    "step_type": "ACTION",
                    "content": f"step {index}",
                    "created_at": JUNE.isoformat(),
                },
            )
            graph_store.write_edge(
                "chain_contains", "chain_1", f"step_{index}", {"valid_from": JUNE.isoformat()}
            )
        return made

    def test_the_steps_come_back_in_the_order_they_happened(
        self, graph_store, seed_chain
    ):
        # They were written out of order on purpose. A sequence read in the
        # wrong order describes a different sequence.
        steps = graph_store.get_causal_chain("chain_1")

        assert [row["step_index"] for row in steps] == [1, 2, 3]

    def test_a_chain_that_does_not_exist_has_no_steps(self, graph_store):
        assert graph_store.get_causal_chain("nope") == []

    def test_the_steps_reach_the_episode_contents(self, graph_store, seed_chain):
        # They hang off the sequence rather than the episode, so an episode
        # would otherwise report a chain with nothing in it.
        slice_ = graph_store.get_episode_contents(seed_chain["episode"])

        assert {"step_1", "step_2", "step_3"} <= {row["node_id"] for row in slice_.nodes}
