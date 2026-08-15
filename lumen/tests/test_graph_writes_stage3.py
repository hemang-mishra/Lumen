"""
Tests for the graph operations reconciliation needed and did not have.

All of them run against a real embedded database rather than a stand-in,
because every one is a query. A stand-in answering from a dictionary would
agree with whatever it was told, including a query that names a column the
database does not have — which is exactly the failure these are here to
catch.

Two of the four are worth their weight on their own. The links carrying a
sentence had no column to hold it, so writing one would have failed the
moment reconciliation shipped. And the small changes to existing records
are the only writes in the system that touch something already saved, so
each is checked for what it did *not* change as well as what it did.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lumen.graph.kuzu_impl import EDGE_EXTRA_COLUMNS

LATER = datetime(2026, 6, 11, 20, 0, tzinfo=UTC)


@pytest.fixture
def store(graph_store):
    """The same real database the seeding fixtures write into."""
    return graph_store


def _audit(store, node_id: str, *, target: str, action: str, status: str = "ACTIVE"):
    store.write_node(
        "DecisionAuditNode",
        {
            "node_id": node_id,
            "created_at": "2026-06-11T20:00:00+00:00",
            "action": action,
            "source_node_id": "obs_1",
            "target_node_id": target,
            "confidence": 0.9,
            "model_used": "fake",
            "model_role": "LIGHTWEIGHT",
            "candidate_retrieval_source": "SEMANTIC",
            "status": status,
            "rollback_pointer": "{}",
        },
    )


class TestLinksThatCarryASentence:
    def test_a_tension_link_keeps_its_summary(self, store, seed_belief):
        seed_belief("bel_a", statement="Criticism helps me")
        seed_belief("bel_b", statement="I need to feel appreciated")

        store.write_edge(
            "dialectic_bel_bel",
            "bel_a",
            "bel_b",
            {
                "valid_from": "2026-06-11T20:00:00+00:00",
                "decision_id": "d_1",
                "confidence": 0.9,
                "tension_summary": "both true at once",
            },
        )

        res = store.conn.execute(
            "MATCH (a:BeliefNode)-[r:dialectic_bel_bel]->(b:BeliefNode) "
            "RETURN r.tension_summary"
        )
        assert res.get_next()[0] == "both true at once"

    def test_a_regulation_link_keeps_its_summary(
        self, store, seed_pattern, sample_observation
    ):
        seed_pattern("pat_spiral")
        store.write_node("ObservationNode", sample_observation)

        store.write_edge(
            "regulates_obs",
            sample_observation.node_id,
            "pat_spiral",
            {
                "valid_from": "2026-06-11T20:00:00+00:00",
                "decision_id": "d_1",
                "confidence": 0.85,
                "regulation_summary": "caught the spiral and stopped",
            },
        )

        res = store.conn.execute(
            "MATCH (o:ObservationNode)-[r:regulates_obs]->(p:PatternNode) "
            "RETURN r.regulation_summary"
        )
        assert res.get_next()[0] == "caught the spiral and stopped"

    def test_only_those_two_kinds_get_an_extra_column(self):
        # Every other link is the same four columns. Adding a column to all
        # of them would cost nothing today and mean nobody could tell which
        # links genuinely carry a sentence.
        assert set(EDGE_EXTRA_COLUMNS) == {"dialectic", "regulates"}


class TestDecidingAboutASession:
    def test_a_session_can_point_at_its_decision(self, store, sample_session):
        # A session is one of the three things reconciliation decides about,
        # and until now it was the only one that could not record the
        # decision made about it.
        store.write_node("SessionNode", sample_session)
        _audit(store, "d_1", target="pat_x", action="BRANCH")

        store.write_edge(
            "decided_by_sess",
            sample_session.node_id,
            "d_1",
            {"valid_from": "2026-06-11T20:00:00+00:00"},
        )

        res = store.conn.execute(
            "MATCH (s:SessionNode)-[:decided_by_sess]->(d:DecisionAuditNode) "
            "RETURN d.node_id"
        )
        assert res.get_next()[0] == "d_1"


class TestCountingWhatWasDecidedBefore:
    def test_nothing_decided_yet_counts_as_none(self, store):
        assert store.count_prior_decisions("pat_old", actions=["BRANCH"]) == 0

    def test_it_counts_only_the_asked_for_actions(self, store):
        _audit(store, "d_1", target="pat_old", action="BRANCH")
        _audit(store, "d_2", target="pat_old", action="REINFORCE")

        assert store.count_prior_decisions("pat_old", actions=["BRANCH"]) == 1
        assert (
            store.count_prior_decisions("pat_old", actions=["BRANCH", "REINFORCE"]) == 2
        )

    def test_it_counts_only_that_record(self, store):
        _audit(store, "d_1", target="pat_old", action="BRANCH")
        _audit(store, "d_2", target="pat_other", action="BRANCH")

        assert store.count_prior_decisions("pat_old", actions=["BRANCH"]) == 1

    def test_a_reversed_decision_stops_counting(self, store):
        # Something that was undone is not evidence that it happened. If it
        # still counted, one rolled-back decision would permanently lower
        # the bar for changing that belief.
        _audit(store, "d_1", target="pat_old", action="BRANCH", status="ROLLED_BACK")

        assert store.count_prior_decisions("pat_old", actions=["BRANCH"]) == 0

    def test_asking_about_no_actions_counts_nothing(self, store):
        assert store.count_prior_decisions("pat_old", actions=[]) == 0


class TestTheThreeSmallChanges:
    def test_superseding_changes_the_status_and_nothing_else(
        self, store, seed_pattern
    ):
        seed_pattern("pat_old", name="Comparison spiral")
        before = store.get_node("pat_old")

        store.mark_superseded("pat_old", at=LATER)

        after = store.get_node("pat_old")
        assert after["status"] == "SUPERSEDED"
        assert after["pattern_description"] == before["pattern_description"]
        assert after["pattern_name"] == before["pattern_name"]
        assert after["valid_from"] == before["valid_from"]

    def test_reinforcing_moves_the_count_and_the_date(self, store, seed_pattern):
        seed_pattern("pat_old", evidence_count=3)

        store.record_reinforcement("pat_old", at=LATER)

        after = store.get_node("pat_old")
        assert after["evidence_count"] == 4
        assert after["last_reinforced_at"] == LATER.isoformat()

    def test_reinforcing_leaves_the_words_alone(self, store, seed_pattern):
        seed_pattern("pat_old", description="Comparing himself to peers")
        before = store.get_node("pat_old")

        store.record_reinforcement("pat_old", at=LATER)

        after = store.get_node("pat_old")
        untouched = [
            "pattern_name",
            "pattern_description",
            "domain",
            "provenance",
            "signal_strength",
            "status",
            "valid_from",
            "created_at",
        ]
        assert all(after[column] == before[column] for column in untouched)

    def test_touching_a_person_moves_the_count_and_the_date(
        self, store, sample_person
    ):
        store.write_node("PersonEntityNode", sample_person)

        store.touch_person(sample_person.node_id, at=LATER)

        after = store.get_node(sample_person.node_id)
        assert after["mention_count"] == sample_person.mention_count + 1
        assert after["last_mentioned_at"] == LATER.isoformat()
        assert after["canonical_name"] == sample_person.canonical_name

    def test_a_belief_can_be_superseded_too(self, store, seed_belief):
        seed_belief("bel_old")

        store.mark_superseded("bel_old", at=LATER)

        assert store.get_node("bel_old")["status"] == "SUPERSEDED"


class TestTheSmallChangesStayNarrow:
    def test_they_refuse_a_record_that_does_not_exist(self, store):
        with pytest.raises(ValueError, match="No node with id"):
            store.mark_superseded("pat_missing", at=LATER)

    def test_superseding_refuses_a_kind_it_has_no_business_touching(
        self, store, sample_observation
    ):
        # The point of naming the allowed tables is that a mistyped
        # identifier cannot quietly point one of these at something else.
        store.write_node("ObservationNode", sample_observation)

        with pytest.raises(ValueError, match="cannot be applied"):
            store.mark_superseded(sample_observation.node_id, at=LATER)

    def test_touching_a_person_refuses_anything_that_is_not_one(
        self, store, seed_pattern
    ):
        seed_pattern("pat_old")

        with pytest.raises(ValueError, match="cannot be applied"):
            store.touch_person("pat_old", at=LATER)


class TestFindingWhatIsStillUnsettled:
    def _episode(self, store, node_id: str, status: str) -> None:
        store.write_node(
            "EpisodeNode",
            {
                "node_id": node_id,
                "entry_id": "entry_1",
                "occurred_at": "2026-06-01T00:00:00+00:00",
                "created_at": "2026-06-01T00:00:00+00:00",
                "valid_from": "2026-06-01T00:00:00+00:00",
                "event_date": "2026-06-01",
                "session_label": "A",
                "source_modality": "TEXT_ENTRY",
                "entry_class": "REFLECTION",
                "episode_summary": "an earlier entry",
                "episode_index": 1,
                "total_episodes_in_entry": 1,
                "coreference_map_id": "cm_1",
                "reconciliation_status": status,
                "raw_text_hash": "hash",
            },
        )

    def _weighty_observation(self, store, node_id: str, episode_id: str) -> None:
        store.write_node(
            "ObservationNode",
            {
                "node_id": node_id,
                "episode_id": episode_id,
                "occurred_at": "2026-06-01T00:00:00+00:00",
                "created_at": "2026-06-01T00:00:00+00:00",
                "valid_from": "2026-06-01T00:00:00+00:00",
                "type": "IDENTITY_FUSION_STATE",
                "content": "I do not know who I am without them",
                "signal_strength": "HIGH",
                "provenance": "USER_GENERATED",
                "verification_status": "IMPLICIT",
                "extraction_confidence": "STANDARD",
                "status": "ACTIVE",
                "extraction_model": "fake",
                "extraction_attempt": 1,
            },
        )
        store.write_edge(
            "contains_obs",
            episode_id,
            node_id,
            {"valid_from": "2026-06-01T00:00:00+00:00"},
        )

    def test_an_entry_with_something_waiting_is_still_surfaced(self, store):
        # Reconciliation now writes the decisions it was sure about and
        # leaves one item waiting, marking the entry suspended. If this
        # lookup ignored that state it would stop finding precisely the
        # items that were set aside.
        self._episode(store, "ep_suspended", "SUSPENDED")
        self._weighty_observation(store, "obs_waiting", "ep_suspended")

        found = store.find_unresolved_high_signal(["IDENTITY_FUSION_STATE"])

        assert [row["node_id"] for row in found] == ["obs_waiting"]

    def test_an_entry_awaiting_a_second_look_is_still_surfaced(self, store):
        self._episode(store, "ep_redo", "PENDING_RERECONCILIATION")
        self._weighty_observation(store, "obs_redo", "ep_redo")

        found = store.find_unresolved_high_signal(["IDENTITY_FUSION_STATE"])

        assert [row["node_id"] for row in found] == ["obs_redo"]

    def test_a_settled_entry_is_left_alone(self, store):
        self._episode(store, "ep_done", "COMPLETE")
        self._weighty_observation(store, "obs_done", "ep_done")

        assert store.find_unresolved_high_signal(["IDENTITY_FUSION_STATE"]) == []
