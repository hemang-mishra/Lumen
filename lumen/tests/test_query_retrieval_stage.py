"""
Fetching a turn's history, end to end.

The pieces have their own suites. This one is about how they fit: that the
two searches really run together under one budget, that one failing costs
only itself, that today's thread is updated with what survived, and that the
four ways of producing an empty answer stay distinguishable.

Real Kuzu, real Qdrant, a scripted model.
"""

from __future__ import annotations

import time

import pytest

from lumen.config import QueryConfig
from lumen.providers.fake import FakeLLMProvider
from lumen.query.buffer import SessionContextBuffer
from lumen.schemas.enums import (
    Domain,
    RetrievalOutcome,
    RetrievalPass,
    SignalStrength,
    TriggerType,
)


@pytest.fixture
def retriever(make_retriever, hyde_replies):
    """A retriever whose model answers the invented-record request."""

    def _build(texts=None, **settings):
        return make_retriever(
            llm=hyde_replies(texts), config=QueryConfig(**settings)
        )

    return _build


class TestATurnWithNothingToLookFor:
    def test_a_turn_with_no_reasons_searches_nothing(
        self, retriever, chat_session, make_signal, seed_pattern
    ):
        seed_pattern("pat_1")

        bundle = retriever().retrieve(make_signal(), chat_session)

        assert bundle.outcome is RetrievalOutcome.NOT_NEEDED
        assert bundle.candidates == ()
        assert bundle.passes == ()

    def test_a_turn_in_crisis_is_reported_as_suppressed_not_as_empty(
        self, retriever, chat_session, make_signal
    ):
        # "The graph had nothing" and "now is not the moment" are different
        # facts about the conversation, and only one of them is about the
        # graph.
        bundle = retriever().retrieve(
            make_signal(suppressed=True), chat_session
        )

        assert bundle.outcome is RetrievalOutcome.SUPPRESSED

    def test_a_turn_that_finds_nothing_says_so_plainly(
        self, retriever, chat_session, make_signal, make_trigger
    ):
        bundle = retriever(["nothing like anything stored"]).retrieve(
            make_signal(make_trigger(TriggerType.PATTERN_MENTION)), chat_session
        )

        assert bundle.outcome is RetrievalOutcome.NOTHING
        assert bundle.search_failed is False


class TestBothSearchesRunning:
    def test_the_meaning_based_search_contributes(
        self, retriever, chat_session, make_signal, make_trigger, seed_observation
    ):
        seed_observation("obs_1", "the invented record")

        bundle = retriever(["the invented record"]).retrieve(
            make_signal(make_trigger(TriggerType.PATTERN_MENTION)), chat_session
        )

        assert "obs_1" in {node.node_id for node in bundle.candidates}
        assert bundle.outcome is RetrievalOutcome.RETRIEVED

    def test_the_anchors_contribute(
        self, retriever, chat_session, make_signal, make_trigger, seed_pattern
    ):
        # The Master Plan's named test, through the whole component: a turn
        # about a period of life surfaces what is filed under it.
        seed_pattern("pat_school", era_tag="high school years")

        bundle = retriever(["unrelated wording entirely"]).retrieve(
            make_signal(
                make_trigger(TriggerType.HISTORICAL_ERA, era="high school years")
            ),
            chat_session,
        )

        found = {node.node_id: node for node in bundle.candidates}
        assert "pat_school" in found
        assert found["pat_school"].found_by is RetrievalPass.STRUCTURAL

    def test_both_are_reported_on_separately(
        self, retriever, chat_session, make_signal, make_trigger, seed_pattern
    ):
        seed_pattern("pat_school", era_tag="high school years")

        bundle = retriever(["anything"]).retrieve(
            make_signal(
                make_trigger(TriggerType.HISTORICAL_ERA, era="high school years")
            ),
            chat_session,
        )

        reported = {report.which for report in bundle.passes}
        assert reported == {
            RetrievalPass.SEMANTIC,
            RetrievalPass.STRUCTURAL,
            RetrievalPass.CONTINUITY,
        }


class TestWhenOneSearchFails:
    def test_a_failed_index_does_not_cost_the_anchors(
        self,
        make_retriever,
        hyde_replies,
        chat_session,
        make_signal,
        make_trigger,
        seed_pattern,
        vector_store,
        monkeypatch,
    ):
        seed_pattern("pat_school", era_tag="high school years")

        def broken(*args, **kwargs):
            raise RuntimeError("the index is gone")

        monkeypatch.setattr(vector_store, "hybrid_search", broken)

        bundle = make_retriever(llm=hyde_replies()).retrieve(
            make_signal(
                make_trigger(TriggerType.HISTORICAL_ERA, era="high school years")
            ),
            chat_session,
        )

        assert [node.node_id for node in bundle.candidates] == ["pat_school"]

    def test_a_search_that_could_not_run_is_not_a_search_that_found_nothing(
        self,
        make_retriever,
        chat_session,
        make_signal,
        make_trigger,
        graph_store,
        vector_store,
        hyde_replies,
    ):
        # Both halves broken. The turn has to be able to say that nobody
        # knows, rather than reporting the person has no history.
        class BrokenEmbedder:
            def embed_batch(self, texts, task_type=None):
                from lumen.providers.errors import ProviderError

                raise ProviderError("no embedder")

        retriever = make_retriever(llm=hyde_replies(), embed=BrokenEmbedder())
        signal = make_signal(make_trigger(TriggerType.PATTERN_MENTION))

        bundle = retriever.retrieve(signal, chat_session)

        assert bundle.outcome is RetrievalOutcome.UNAVAILABLE
        assert bundle.search_failed is True

    def test_the_reason_a_search_failed_is_recorded(
        self, make_retriever, chat_session, make_signal, make_trigger, graph_store,
        hyde_replies, monkeypatch,
    ):
        def broken(*args, **kwargs):
            raise RuntimeError("the store said no")

        monkeypatch.setattr(graph_store, "find_nodes", broken)
        # The anchor half contains each failure where it happens, and counts
        # them. When every lookup it made was refused, that is a pass that
        # could not look — not a pass that looked and found nothing.
        bundle = make_retriever(llm=hyde_replies()).retrieve(
            make_signal(make_trigger(TriggerType.OPEN_LOOP_MATCH)), chat_session
        )

        structural = next(
            report
            for report in bundle.passes
            if report.which is RetrievalPass.STRUCTURAL
        )
        assert structural.failure == "SearchUnavailable"
        assert structural.kept == 0


class TestTheBudget:
    def test_a_search_that_runs_over_is_abandoned_and_said_to_be(
        self,
        make_retriever,
        chat_session,
        make_signal,
        make_trigger,
        vector_store,
        monkeypatch,
    ):
        class Slow(FakeLLMProvider):
            def generate_structured(self, *args, **kwargs):
                time.sleep(0.5)
                raise AssertionError("should have been abandoned")

        retriever = make_retriever(
            llm=Slow([]), config=QueryConfig(retrieval_budget_seconds=0.05)
        )

        bundle = retriever.retrieve(
            make_signal(make_trigger(TriggerType.PATTERN_MENTION)), chat_session
        )

        assert bundle.within_budget is False
        semantic = next(
            report for report in bundle.passes if report.which is RetrievalPass.SEMANTIC
        )
        assert semantic.failure == "timed_out"

    def test_a_turn_that_finished_in_time_says_so(
        self, retriever, chat_session, make_signal, make_trigger
    ):
        bundle = retriever(["anything"]).retrieve(
            make_signal(make_trigger(TriggerType.PATTERN_MENTION)), chat_session
        )

        assert bundle.within_budget is True
        assert bundle.latency_ms >= 0


class TestTodaysThread:
    def test_what_survives_is_remembered(
        self, retriever, chat_session, make_signal, make_trigger, seed_observation
    ):
        seed_observation("obs_1", "the invented record")

        retriever(["the invented record"]).retrieve(
            make_signal(make_trigger(TriggerType.PATTERN_MENTION)), chat_session
        )

        assert "obs_1" in chat_session.context_buffer

    def test_a_record_seen_earlier_today_is_carried_into_a_later_turn(
        self, retriever, chat_session, make_signal, make_trigger, seed_observation
    ):
        # The connection this whole pass exists to make: the earlier record
        # comes back even though this turn's search found nothing.
        seed_observation("obs_1", "the invented record")
        engine = retriever(["the invented record"])
        engine.retrieve(
            make_signal(make_trigger(TriggerType.PATTERN_MENTION), turn_index=0),
            chat_session,
        )

        later = engine.retrieve(
            make_signal(make_trigger(TriggerType.PATTERN_MENTION), turn_index=1),
            chat_session,
        )

        carried = {node.node_id: node for node in later.candidates}
        assert "obs_1" in carried
        assert carried["obs_1"].boosted is True

    def test_a_record_nobody_returns_to_drops_out_of_the_thread(
        self, retriever, chat_session, make_signal, make_trigger, seed_observation
    ):
        seed_observation("obs_1", "the invented record")
        engine = retriever(["the invented record"], session_buffer_max_idle_turns=2)
        engine.retrieve(
            make_signal(make_trigger(TriggerType.PATTERN_MENTION), turn_index=0),
            chat_session,
        )

        engine.retrieve(
            make_signal(make_trigger(TriggerType.PATTERN_MENTION), turn_index=9),
            chat_session,
        )

        # Found again this turn, so it is back — but the eviction ran, which
        # is what keeps a day-long conversation from accumulating forever.
        assert len(chat_session.context_buffer) <= 5

    def test_a_thread_that_cannot_read_positions_still_works(
        self,
        make_retriever,
        chat_session,
        make_signal,
        make_trigger,
        seed_observation,
        vector_store,
        hyde_replies,
        monkeypatch,
    ):
        seed_observation("obs_1", "the invented record")
        monkeypatch.setattr(
            vector_store,
            "get_vectors",
            lambda node_ids: (_ for _ in ()).throw(RuntimeError("no")),
        )

        retriever = make_retriever(llm=hyde_replies(["the invented record"]))
        retriever.retrieve(
            make_signal(make_trigger(TriggerType.PATTERN_MENTION)), chat_session
        )

        assert chat_session.context_buffer.entries[0].vector is None


class TestTheSensitivityGate:
    def test_the_heaviest_records_are_held_back_until_invited(
        self, retriever, chat_session, make_signal, make_trigger, seed_pattern,
        index_node,
    ):
        _seed_critical(seed_pattern, index_node)

        bundle = retriever(["the invented record"]).retrieve(
            make_signal(make_trigger(TriggerType.PATTERN_MENTION)), chat_session
        )

        assert "pat_critical" in bundle.gated
        assert "pat_critical" not in {node.node_id for node in bundle.candidates}

    def test_opening_the_subject_releases_them(
        self, retriever, chat_session, make_signal, make_trigger, seed_pattern,
        index_node,
    ):
        _seed_critical(seed_pattern, index_node)

        bundle = retriever(["the invented record"]).retrieve(
            make_signal(
                make_trigger(TriggerType.PATTERN_MENTION),
                unlocked=(Domain.SELF_CONCEPT,),
            ),
            chat_session,
        )

        assert "pat_critical" in {node.node_id for node in bundle.candidates}
        assert bundle.gated == ()


class TestHowMuchLeaves:
    def test_the_cap_is_respected(
        self, retriever, chat_session, make_signal, make_trigger, seed_observation
    ):
        for index in range(6):
            seed_observation(f"obs_{index}", "the invented record")

        bundle = retriever(
            ["the invented record"], conversational_candidate_cap=2
        ).retrieve(
            make_signal(make_trigger(TriggerType.PATTERN_MENTION)), chat_session
        )

        assert len(bundle.candidates) == 2

    def test_the_best_comes_first(
        self, retriever, chat_session, make_signal, make_trigger, seed_observation
    ):
        seed_observation("obs_ordinary", "the invented record")
        seed_observation("obs_weighty", "the invented record", signal="HIGH")

        bundle = retriever(["the invented record"]).retrieve(
            make_signal(make_trigger(TriggerType.PATTERN_MENTION)), chat_session
        )

        assert bundle.candidates[0].node_id == "obs_weighty"


class TestWhatItReadsTheTurnFrom:
    def test_the_turn_s_own_words_are_taken_from_the_day_s_memory(
        self, make_retriever, chat_session, make_signal, make_trigger, make_turn
    ):
        # Rather than passed alongside the signal, because the reader has
        # already recorded it and two copies of one sentence is two chances
        # for them to disagree.
        llm = FakeLLMProvider({"ITEMS:": '{"hypotheticals": []}'})
        chat_session.record_turn(make_turn("the resistance to going out alone"))

        make_retriever(llm=llm).retrieve(
            make_signal(make_trigger(TriggerType.PATTERN_MENTION), turn_index=0),
            chat_session,
        )

        assert "the resistance to going out alone" in llm.calls[0].prompt


def _seed_critical(seed_pattern, index_node):
    """One heavy record about a sensitive area, written to both stores."""
    seed_pattern(
        "pat_critical",
        name="the invented record",
        signal="CRITICAL",
        domain="SELF_CONCEPT",
    )
    index_node("pat_critical", "the invented record", node_type="PatternNode")


class TestADeadStoreIsNotAnEmptyHistory:
    """
    The failure this whole layer exists to prevent, asserted from the outside.

    Each pass contains its own failures so one broken lookup does not cost
    the others. Containment without counting turns a store that refuses every
    query into a pass that reports an empty answer — and the layer above
    reads an empty answer as "this person has no such history", so a system
    built to remember behaves as though it had never met anybody, one turn at
    a time, with nothing failing and no test going red.
    """

    def test_an_index_that_refuses_everything_says_so(
        self,
        make_retriever,
        chat_session,
        make_signal,
        make_trigger,
        vector_store,
        hyde_replies,
        monkeypatch,
    ):
        def broken(*args, **kwargs):
            raise RuntimeError("the index said no")

        monkeypatch.setattr(vector_store, "hybrid_search", broken)

        bundle = make_retriever(llm=hyde_replies()).retrieve(
            make_signal(make_trigger(TriggerType.PATTERN_MENTION)), chat_session
        )

        assert bundle.outcome is RetrievalOutcome.UNAVAILABLE
        assert bundle.search_failed is True

    def test_both_stores_refusing_says_so(
        self,
        make_retriever,
        chat_session,
        make_signal,
        make_trigger,
        graph_store,
        vector_store,
        hyde_replies,
        monkeypatch,
    ):
        def broken(*args, **kwargs):
            raise RuntimeError("the store said no")

        monkeypatch.setattr(graph_store, "find_nodes", broken)
        monkeypatch.setattr(vector_store, "hybrid_search", broken)

        bundle = make_retriever(llm=hyde_replies()).retrieve(
            make_signal(make_trigger(TriggerType.OPEN_LOOP_MATCH)), chat_session
        )

        assert bundle.outcome is RetrievalOutcome.UNAVAILABLE
        assert bundle.search_failed is True

    def test_one_half_refusing_is_recorded_without_condemning_the_turn(
        self,
        make_retriever,
        chat_session,
        make_signal,
        make_trigger,
        graph_store,
        hyde_replies,
        monkeypatch,
    ):
        """
        The anchors were refused; the meaning-based search genuinely ran.

        The bundle still says NOTHING, because one search that actually
        consulted a store is enough to say the graph was asked — that rule is
        deliberate. What changed is that the refusal is now visible on the
        pass instead of being indistinguishable from an empty answer, which
        is what any aggregate judgement has to be built on.
        """

        def broken(*args, **kwargs):
            raise RuntimeError("the store said no")

        monkeypatch.setattr(graph_store, "find_nodes", broken)

        bundle = make_retriever(llm=hyde_replies()).retrieve(
            make_signal(make_trigger(TriggerType.OPEN_LOOP_MATCH)), chat_session
        )

        anchors = next(
            report
            for report in bundle.passes
            if report.which is RetrievalPass.STRUCTURAL
        )
        assert anchors.failure == "SearchUnavailable"
        assert bundle.outcome is RetrievalOutcome.NOTHING

    def test_a_working_search_that_finds_nothing_still_says_nothing(
        self, make_retriever, chat_session, make_signal, make_trigger, hyde_replies
    ):
        # The other half of the distinction. Both stores answered; this
        # person genuinely has nothing filed that matches.
        bundle = make_retriever(llm=hyde_replies()).retrieve(
            make_signal(make_trigger(TriggerType.PATTERN_MENTION)), chat_session
        )

        assert bundle.outcome is RetrievalOutcome.NOTHING
        assert bundle.search_failed is False
