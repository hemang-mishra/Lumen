"""
Pass A — finding history that reads like what was just said.

Run against a real search index and a real graph. The stand-in embedder
turns text into a hash, so two sentences that mean nearly the same thing
land nowhere near each other — which is why the tests that care about
distance place their vectors by angle instead of hoping the words are close.
"""

from __future__ import annotations

import json

import pytest

from lumen.config import QueryConfig
from lumen.providers.errors import ProviderError
from lumen.providers.fake import FakeLLMProvider
from lumen.query.retrieval import semantic
from lumen.query.retrieval.semantic import SearchUnavailable
from lumen.schemas.enums import Domain, RetrievalPass, TriggerType


@pytest.fixture
def search(graph_store, vector_store, embedder, hyde_replies):
    """Run the meaning-based search against the seeded stores."""

    def _search(
        *triggers,
        turn="I keep avoiding it",
        llm=None,
        texts=None,
        embed=None,
        **settings,
    ):
        return semantic.find_by_resemblance(
            turn,
            tuple(triggers),
            graph=graph_store,
            vectors=vector_store,
            embedder=embed or embedder,
            llm=llm or hyde_replies(texts),
            config=QueryConfig(**settings),
        )

    return _search


class FixedEmbedder:
    """
    An embedder that always answers with the same chosen position.

    Used where a test needs to control distance exactly. The shipped
    stand-in hashes text, so two sentences that mean the same thing land
    nowhere near each other — fine for checking that a search runs, useless
    for checking what it ranks first.
    """

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    def embed_batch(self, texts, task_type=None):
        return [list(self._vector) for _ in texts]


class TestFindingWhatResembles:
    def test_a_matching_record_comes_back(
        self, search, seed_observation, make_trigger
    ):
        seed_observation("obs_1", "the invented record", episode_id="ep_old")

        found = search(
            make_trigger(TriggerType.PATTERN_MENTION),
            texts=["the invented record"],
        )

        assert [node.node_id for node in found.candidates] == ["obs_1"]
        assert found.candidates[0].found_by is RetrievalPass.SEMANTIC

    def test_the_measured_closeness_is_reported_honestly(
        self, search, seed_observation, make_trigger
    ):
        # Not the weighted number used for ordering — that can reach twice
        # what this field allows, and somebody reading it should get the
        # measurement rather than a ranking decision baked in.
        seed_observation("obs_1", "the invented record", signal="CRITICAL")

        found = search(
            make_trigger(TriggerType.PATTERN_MENTION), texts=["the invented record"]
        )

        found_node = found.candidates[0]

        assert found_node.similarity == pytest.approx(1.0, abs=1e-6)
        # The weighted number is the measurement times everything the record
        # is worth, which for a weighty one can reach twice what the
        # similarity field allows.
        assert found_node.rank_score == pytest.approx(
            1.0 * 2.0 * found_node.recency_weight * found_node.trust_weight,
            abs=1e-6,
        )

    def test_the_reason_that_led_there_is_recorded(
        self, search, seed_observation, make_trigger
    ):
        seed_observation("obs_1", "the invented record")

        found = search(
            make_trigger(TriggerType.PATTERN_MENTION), texts=["the invented record"]
        )

        assert found.candidates[0].trigger_type is TriggerType.PATTERN_MENTION

    def test_no_reasons_means_no_call_and_no_search(self, search):
        found = search()

        assert found.candidates == ()
        assert found.query_vector is None


class TestWhatIsWorthOffering:
    def test_machinery_is_not_history(
        self, search, graph_store, vector_store, embedder, make_trigger
    ):
        # A decision record is how the system works, not something that
        # happened to the person.
        graph_store.write_node(
            "DecisionAuditNode",
            {
                "node_id": "d_1",
                "created_at": "2026-06-01T00:00:00+00:00",
                "action": "MERGE",
                "confidence": 0.9,
                "model_role": "LIGHTWEIGHT",
                "model_used": "fake",
                "delta_description": "the invented record",
                "status": "ACTIVE",
            },
        )
        vector_store.upsert(
            "d_1", embedder.embed_text("the invented record"), {"node_type": "x"}
        )

        found = search(
            make_trigger(TriggerType.PATTERN_MENTION), texts=["the invented record"]
        )

        assert found.candidates == ()

    def test_a_superseded_record_is_not_offered(
        self, search, seed_observation, make_trigger
    ):
        seed_observation("obs_1", "the invented record", status="SUPERSEDED")

        found = search(
            make_trigger(TriggerType.PATTERN_MENTION), texts=["the invented record"]
        )

        assert found.candidates == ()

    def test_a_match_the_graph_has_lost_is_skipped(
        self, search, vector_store, embedder, make_trigger
    ):
        # Indexed but never written, which is what a half-failed run leaves.
        vector_store.upsert(
            "obs_ghost",
            embedder.embed_text("the invented record"),
            {"node_type": "ObservationNode"},
        )

        found = search(
            make_trigger(TriggerType.PATTERN_MENTION), texts=["the invented record"]
        )

        assert found.candidates == ()


class TestNarrowingByReason:
    def test_a_physical_sensation_asks_only_about_the_body(
        self, search, seed_observation, make_trigger
    ):
        seed_observation(
            "obs_body",
            "the invented record",
            observation_type="PHYSIOLOGICAL_CAPACITY_STATE",
        )
        seed_observation(
            "obs_career", "the invented record", observation_type="PATTERN"
        )

        found = search(
            make_trigger(TriggerType.SOMATIC_MARKER), texts=["the invented record"]
        )

        assert [node.node_id for node in found.candidates] == ["obs_body"]

    def test_a_statement_about_who_they_are_asks_about_beliefs(
        self, search, seed_observation, seed_belief, index_node, make_trigger
    ):
        seed_observation("obs_belief", "the invented record", observation_type="BELIEF")
        seed_observation("obs_other", "the invented record", observation_type="EMOTION")
        seed_belief("bel_1", statement="the invented record")
        index_node("bel_1", "the invented record", node_type="BeliefNode")

        found = search(
            make_trigger(TriggerType.IDENTITY_STATEMENT), texts=["the invented record"]
        )

        assert {node.node_id for node in found.candidates} == {"obs_belief", "bel_1"}

    def test_a_narrowed_reason_refuses_a_whole_kind_it_did_not_ask_for(
        self, search, seed_pattern, index_node, make_trigger
    ):
        # Somebody describing a tight chest should not be answered with a
        # standing pattern, however closely the words happen to line up.
        seed_pattern("pat_1", name="the invented record")
        index_node("pat_1", "the invented record", node_type="PatternNode")

        found = search(
            make_trigger(TriggerType.SOMATIC_MARKER), texts=["the invented record"]
        )

        assert found.candidates == ()

    def test_a_narrowed_reason_still_accepts_the_other_kinds_it_asked_for(
        self, search, seed_belief, index_node, make_trigger
    ):
        # A belief has no observation type at all, so the deeper check must
        # not be applied to it — only observations are sorted that finely.
        seed_belief("bel_1", statement="the invented record")
        index_node("bel_1", "the invented record", node_type="BeliefNode")

        found = search(
            make_trigger(TriggerType.IDENTITY_STATEMENT), texts=["the invented record"]
        )

        assert [node.node_id for node in found.candidates] == ["bel_1"]

    def test_an_ordinary_reason_narrows_nothing(
        self, search, seed_observation, make_trigger
    ):
        seed_observation("obs_1", "the invented record", observation_type="EMOTION")

        found = search(
            make_trigger(TriggerType.PATTERN_MENTION), texts=["the invented record"]
        )

        assert [node.node_id for node in found.candidates] == ["obs_1"]


class TestRankingAndCutting:
    def test_a_weighty_record_outranks_a_closer_ordinary_one(
        self, search, seed_observation, vector_at_angle, make_trigger
    ):
        # The whole reason more is fetched than kept: raw closeness is not
        # importance, and a realisation that reorganised somebody's life
        # earns its place over a routine note worded alike.
        import math

        seed_observation(
            "obs_near", "near", vector=vector_at_angle(math.radians(30))
        )
        seed_observation(
            "obs_weighty",
            "weighty",
            signal="CRITICAL",
            vector=vector_at_angle(math.radians(50)),
        )

        found = search(
            make_trigger(TriggerType.PATTERN_MENTION),
            texts=["anything"],
            embed=FixedEmbedder(vector_at_angle(0.0)),
        )

        assert [node.node_id for node in found.candidates][0] == "obs_weighty"

    def test_only_the_agreed_number_survive(
        self, search, seed_observation, make_trigger
    ):
        for index in range(4):
            seed_observation(f"obs_{index}", "the invented record")

        found = search(
            make_trigger(TriggerType.PATTERN_MENTION),
            texts=["the invented record"],
            conversational_pass_a_keep=2,
        )

        assert len(found.candidates) == 2
        # What was found before cutting is still reported, so a limit doing
        # more work than expected is visible.
        assert found.found == 4

    def test_one_record_answering_two_reasons_is_offered_once(
        self, search, seed_observation, make_trigger
    ):
        seed_observation("obs_1", "the invented record")

        found = search(
            make_trigger(TriggerType.PATTERN_MENTION),
            make_trigger(TriggerType.BELIEF_CHALLENGE, domain=Domain.SELF_CONCEPT),
            texts=["the invented record", "the invented record"],
        )

        assert [node.node_id for node in found.candidates] == ["obs_1"]


class TestWritingTheSearchText:
    def test_one_call_covers_every_reason(self, search, make_trigger, hyde_replies):
        llm = hyde_replies(["first", "second"])

        search(
            make_trigger(TriggerType.PATTERN_MENTION),
            make_trigger(TriggerType.SOMATIC_MARKER),
            llm=llm,
        )

        assert len(llm.calls) == 1

    def test_a_model_that_will_not_answer_costs_quality_and_not_the_search(
        self, search, seed_observation, make_trigger
    ):
        # A worse search, and a real one. Returning nothing would look
        # exactly like a person with no history on the subject.
        seed_observation("obs_1", "I keep avoiding it resistance")

        found = search(
            make_trigger(TriggerType.PATTERN_MENTION, keywords=("resistance",)),
            turn="I keep avoiding it",
            llm=FakeLLMProvider([]),
        )

        assert found.used_fallback is True
        assert [node.node_id for node in found.candidates] == ["obs_1"]

    def test_a_short_reply_pads_rather_than_shifting_the_rest_up(
        self, search, seed_observation, make_trigger, hyde_replies
    ):
        # Searching one reason with another reason's text does not fail —
        # it returns confident, wrong records, which is worse than none.
        seed_observation("obs_second", "I keep avoiding it hostel")
        llm = hyde_replies(
            reply=json.dumps({"hypotheticals": [{"index": 1, "text": "only the first"}]})
        )

        found = search(
            make_trigger(TriggerType.PATTERN_MENTION),
            make_trigger(TriggerType.HISTORICAL_ERA, era="hostel", keywords=()),
            turn="I keep avoiding it",
            llm=llm,
        )

        assert [node.node_id for node in found.candidates] == ["obs_second"]

    def test_an_unreadable_reply_falls_back(self, search, make_trigger):
        found = search(
            make_trigger(TriggerType.PATTERN_MENTION),
            llm=FakeLLMProvider({"ITEMS:": "not json at all"}),
        )

        assert found.used_fallback is True

    def test_a_reply_of_the_wrong_shape_falls_back(self, search, make_trigger):
        found = search(
            make_trigger(TriggerType.PATTERN_MENTION),
            llm=FakeLLMProvider({"ITEMS:": json.dumps({"hypotheticals": "not a list"})}),
        )

        assert found.used_fallback is True


class TestWhenNothingCanBeSearched:
    def test_an_embedder_that_fails_says_so_rather_than_finding_nothing(
        self, graph_store, vector_store, embedder, hyde_replies, make_trigger
    ):
        # The distinction this whole layer insists on. Finding nothing means
        # the person has no history on the subject; failing to look means
        # nobody knows, and answering the second as the first is how a
        # long-standing pattern gets recorded as a fresh discovery.
        class Broken:
            def embed_batch(self, texts, task_type=None):
                raise ProviderError("no embedder")

        with pytest.raises(SearchUnavailable):
            semantic.find_by_resemblance(
                "anything",
                (make_trigger(TriggerType.PATTERN_MENTION),),
                graph=graph_store,
                vectors=vector_store,
                embedder=Broken(),
                llm=hyde_replies(),
                config=QueryConfig(),
            )

    def test_one_failing_search_costs_that_reason_and_not_the_others(
        self, search, seed_observation, vector_store, make_trigger, monkeypatch
    ):
        seed_observation("obs_second", "the second invented record")
        real = vector_store.hybrid_search
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("the index said no")
            return real(*args, **kwargs)

        monkeypatch.setattr(vector_store, "hybrid_search", flaky)

        found = search(
            make_trigger(TriggerType.PATTERN_MENTION),
            make_trigger(TriggerType.BELIEF_CHALLENGE),
            texts=["the first invented record", "the second invented record"],
        )

        # The second reason still answered, which is the whole point of
        # containing a failure where it happens rather than raising.
        assert [node.node_id for node in found.candidates] == ["obs_second"]

    def test_an_index_that_refuses_everything_is_not_an_empty_history(
        self, search, vector_store, make_trigger, monkeypatch
    ):
        """
        Every search failing must raise, not return nothing.

        Containing each failure where it happens is right, and on its own it
        turns a dead index into a pass that reports an empty answer. The
        layer above reads that as "this person has no such history" and the
        assistant behaves as though it has never met them — which is the one
        failure this whole layer exists to prevent.
        """

        def broken(*args, **kwargs):
            raise RuntimeError("the index said no")

        monkeypatch.setattr(vector_store, "hybrid_search", broken)

        with pytest.raises(SearchUnavailable):
            search(make_trigger(TriggerType.PATTERN_MENTION), texts=["anything"])

    def test_a_graph_that_refuses_every_read_back_is_reported_the_same_way(
        self, search, seed_observation, graph_store, make_trigger, monkeypatch
    ):
        """The index answering is no use if the graph will not say what it found."""
        seed_observation("obs_1", "the invented record")

        def broken(*args, **kwargs):
            raise RuntimeError("the graph said no")

        monkeypatch.setattr(graph_store, "get_nodes_by_ids", broken)

        with pytest.raises(SearchUnavailable):
            search(
                make_trigger(TriggerType.PATTERN_MENTION),
                texts=["the invented record"],
            )


class TestTheVectorItHandsBack:
    def test_the_turn_s_position_comes_back_with_the_result(
        self, search, make_trigger
    ):
        # Handed over so the continuity check can compare today's earlier
        # records against the same measurement, instead of paying a model to
        # make it twice.
        found = search(make_trigger(TriggerType.PATTERN_MENTION), texts=["anything"])

        assert found.query_vector is not None
        assert len(found.query_vector) == 768
