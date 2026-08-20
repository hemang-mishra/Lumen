"""
Tests for age, confirmation and usefulness changing what a search returns.

Run against real stores, because every question here is about ordering and a
stand-in would agree with whatever it was told.

The case this file exists for is the first one: two records that say the same
thing and match a question equally well, one reaffirmed last week and one
last touched over a year ago. Before this, they ranked identically. The
system had no sense of time at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lumen.config import QueryConfig, ScoringConfig
from lumen.query.retrieval import continuity, semantic, structural
from lumen.query.retrieval.hydrate import Weighting
from lumen.schemas.enums import StructuralAnchorType, TriggerType

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


def quiet_for(days: int) -> str:
    """A stored date that many days before this test's fixed moment."""
    return (NOW - timedelta(days=days)).isoformat()


@pytest.fixture
def weighing():
    """The clock and settings this file measures everything against."""
    return Weighting.at(NOW, config=ScoringConfig())


@pytest.fixture
def search(graph_store, vector_store, embedder, hyde_replies, weighing):
    """Run the meaning-based search against the seeded stores."""

    def _search(*triggers, turn="Am I still like that?", texts=None, **settings):
        return semantic.find_by_resemblance(
            turn,
            tuple(triggers),
            graph=graph_store,
            vectors=vector_store,
            embedder=embedder,
            llm=hyde_replies(texts),
            config=QueryConfig(**settings),
            weighting=weighing,
        )

    return _search


class TestTheFourHundredDayGap:
    def test_the_older_of_two_identical_records_scores_half(
        self, seed_belief, index_node, search, make_trigger, embedder
    ):
        # The named case for this goal. Both beliefs say the same thing and
        # sit at the same distance from the question; one was reaffirmed
        # yesterday and one has not been touched in over a year.
        same_words = "I only make good decisions alone"
        seed_belief("bel_fresh", statement=same_words, valid_from=quiet_for(1))
        seed_belief("bel_old", statement=same_words, valid_from=quiet_for(400))
        vector = embedder.embed_text(same_words)
        index_node("bel_fresh", vector=vector, node_type="BeliefNode")
        index_node("bel_old", vector=vector, node_type="BeliefNode")

        found = search(make_trigger(TriggerType.BELIEF_CHALLENGE), texts=[same_words])
        scores = {node.node_id: node.rank_score for node in found.candidates}

        assert scores["bel_old"] == pytest.approx(scores["bel_fresh"] * 0.5)

    def test_the_older_record_is_still_returned(
        self, seed_belief, index_node, search, make_trigger, embedder
    ):
        # Age costs a record its place in the order. It never removes it —
        # people do not stop having been a certain way because they stopped
        # writing about it.
        same_words = "I only make good decisions alone"
        seed_belief("bel_fresh", statement=same_words, valid_from=quiet_for(1))
        seed_belief("bel_old", statement=same_words, valid_from=quiet_for(400))
        vector = embedder.embed_text(same_words)
        index_node("bel_fresh", vector=vector, node_type="BeliefNode")
        index_node("bel_old", vector=vector, node_type="BeliefNode")

        found = search(make_trigger(TriggerType.BELIEF_CHALLENGE), texts=[same_words])

        assert {node.node_id for node in found.candidates} == {"bel_fresh", "bel_old"}

    def test_the_bands_a_record_passed_through_are_reported(
        self, seed_belief, index_node, search, make_trigger, embedder
    ):
        # "0.5" says nothing on its own. "Dormant" says the whole thing.
        seed_belief("bel_old", statement="I am behind", valid_from=quiet_for(400))
        index_node("bel_old", vector=embedder.embed_text("I am behind"), node_type="BeliefNode")

        found = search(make_trigger(TriggerType.BELIEF_CHALLENGE), texts=["I am behind"])

        assert found.candidates[0].age_band.value == "DORMANT"
        assert found.candidates[0].recency_weight == 0.5


class TestWhatAnAnchorSurvives:
    def test_an_old_record_found_by_name_still_comes_back(
        self, graph_store, seed_person, weighing, make_trigger
    ):
        # The anchor searches exist to reach material that resemblance never
        # would, and half of that material is old on purpose. Decay must not
        # quietly undo the reason they exist.
        person_id = seed_person("Alex")
        graph_store.write_node(
            "ObservationNode",
            {
                "node_id": "obs_ancient",
                "episode_id": "ep_ancient",
                "occurred_at": quiet_for(1200),
                "created_at": quiet_for(1200),
                "valid_from": quiet_for(1200),
                "type": "PATTERN",
                "content": "Alex said something that stayed with me",
                "signal_strength": "STANDARD",
                "provenance": "USER_GENERATED",
                "verification_status": "IMPLICIT",
                "extraction_confidence": "STANDARD",
                "status": "ACTIVE",
                "person_refs": [person_id],
            },
        )
        graph_store.write_edge("mentions_obs", "obs_ancient", person_id)

        found = structural.find_by_anchors(
            (make_trigger(TriggerType.NAMED_PERSON, person_node_ids=(person_id,)),),
            graph=graph_store,
            config=QueryConfig(),
            weighting=weighing,
        )

        assert [node.node_id for node in found] == ["obs_ancient"]
        assert found[0].anchor_type is StructuralAnchorType.NAMED_PERSON
        assert found[0].rank_score > 0


class TestTodayIsNotAWayPastTheQueue:
    def test_a_remembered_record_is_aged_like_any_other(self, weighing):
        # Being remembered from earlier today is a reason to offer a record
        # again, not a way for an old one to skip the ordering.
        from lumen.query.buffer import SessionContextBuffer
        from lumen.query.retrieval.contracts import RetrievedNode
        from lumen.schemas.enums import RetrievalPass

        old = RetrievedNode(
            node_id="pat_old",
            node_type="PatternNode",
            preview="Comparison spiral",
            found_by=RetrievalPass.SEMANTIC,
            similarity=0.8,
            rank_score=0.8,
            properties={"last_reinforced_at": quiet_for(400)},
        )
        buffer = SessionContextBuffer(max_entries=5)
        buffer.remember(continuity.to_entries([old], vectors={}), turn_index=0)

        revisited, _ = continuity.revisit(
            buffer,
            already_found=set(),
            query_vector=None,
            keywords=["comparison", "spiral"],
            config=QueryConfig(),
            weighting=weighing,
        )

        assert revisited[0].recency_weight == 0.5


class TestConfirmationChangesTheOrder:
    def test_an_unconfirmed_suggestion_ranks_below_the_person_s_own_words(
        self, graph_store, index_node, search, make_trigger, embedder
    ):
        words = "I avoid things that might expose me"
        for node_id, status in (("bel_theirs", "IMPLICIT"), ("bel_ours", "UNVERIFIED")):
            graph_store.write_node(
                "BeliefNode",
                {
                    "node_id": node_id,
                    "version": 1,
                    "created_at": quiet_for(1),
                    "valid_from": quiet_for(1),
                    "last_reinforced_at": quiet_for(1),
                    "belief_statement": words,
                    "domain": "SELF_CONCEPT",
                    "signal_strength": "STANDARD",
                    "provenance": "USER_GENERATED",
                    "verification_status": status,
                    "evidence_count": 1,
                    "query_frequency": 0,
                    "is_contradicted": False,
                    "status": "ACTIVE",
                },
            )
            index_node(node_id, vector=embedder.embed_text(words), node_type="BeliefNode")

        found = search(make_trigger(TriggerType.BELIEF_CHALLENGE), texts=[words])
        scores = {node.node_id: node.rank_score for node in found.candidates}

        assert scores["bel_ours"] == pytest.approx(scores["bel_theirs"] * 0.5)


class TestUsefulnessChangesTheOrder:
    def test_a_record_that_keeps_helping_climbs(
        self, graph_store, seed_pattern, index_node, search, make_trigger, embedder
    ):
        words = "Comparing myself to other people"
        seed_pattern("pat_plain", name=words, valid_from=quiet_for(1))
        seed_pattern("pat_useful", name=words, valid_from=quiet_for(1))
        graph_store.record_query_hits(["pat_useful"], at=NOW)
        graph_store.record_query_hits(["pat_useful"], at=NOW)
        for node_id in ("pat_plain", "pat_useful"):
            index_node(node_id, vector=embedder.embed_text(words), node_type="PatternNode")

        found = search(make_trigger(TriggerType.PATTERN_MENTION), texts=[words])
        scores = {node.node_id: node.rank_score for node in found.candidates}

        assert scores["pat_useful"] == pytest.approx(scores["pat_plain"] * 1.2)


class TestTurningItOff:
    def test_nothing_is_discounted_when_decay_is_switched_off(
        self, graph_store, vector_store, embedder, hyde_replies,
        seed_belief, index_node, make_trigger,
    ):
        # The switch exists so today's ranking can be compared against the
        # old one without changing any code.
        words = "I only make good decisions alone"
        seed_belief("bel_fresh", statement=words, valid_from=quiet_for(1))
        seed_belief("bel_old", statement=words, valid_from=quiet_for(400))
        vector = embedder.embed_text(words)
        index_node("bel_fresh", vector=vector, node_type="BeliefNode")
        index_node("bel_old", vector=vector, node_type="BeliefNode")

        found = semantic.find_by_resemblance(
            "Am I still like that?",
            (make_trigger(TriggerType.BELIEF_CHALLENGE),),
            graph=graph_store,
            vectors=vector_store,
            embedder=embedder,
            llm=hyde_replies([words]),
            config=QueryConfig(),
            weighting=Weighting.at(NOW, config=ScoringConfig(decay_enabled=False)),
        )
        scores = {node.node_id: node.rank_score for node in found.candidates}

        assert scores["bel_old"] == pytest.approx(scores["bel_fresh"])
