"""
Tests for writing the text the search is actually run with.

Two things are guarded here.

Cost: one call and one batch for a whole entry, however many findings it
produced. A call each would make this the most expensive step in the
pipeline, and the test that catches a regression is a call count.

Alignment: answers are matched to findings by the number they came back
with. A short or scrambled answer must not slide the rest up, because a
search run with another finding's text does not fail — it returns
confident, wrong matches, and nothing downstream can tell.
"""

from __future__ import annotations

import json
import logging

import pytest

from lumen.pipeline.retrieval import hyde
from lumen.pipeline.retrieval.contracts import SearchTarget
from lumen.providers.errors import ProviderTimeoutError
from lumen.providers.fake import FakeEmbeddingProvider, FakeLLMProvider
from lumen.schemas.enums import EmbeddingTaskType, ModelRole


def target(text: str, node_id: str = "obs_1", episode_id: str = "ep_1") -> SearchTarget:
    return SearchTarget(
        node_id=node_id, node_type="ObservationNode", text=text, episode_id=episode_id
    )


def targets(*texts: str) -> tuple[SearchTarget, ...]:
    return tuple(target(text, f"obs_{i}") for i, text in enumerate(texts, start=1))


def replying(*hypotheticals: str) -> FakeLLMProvider:
    reply = json.dumps(
        {
            "hypotheticals": [
                {"index": index, "text": text}
                for index, text in enumerate(hypotheticals, start=1)
            ]
        }
    )
    return FakeLLMProvider([reply], role=ModelRole.LIGHTWEIGHT)


class FailingProvider(FakeLLMProvider):
    """A model whose calls never get through."""

    def _request_structured(self, **kwargs):
        raise ProviderTimeoutError(
            "took too long", provider="fake", model="fake", role=self.model_role
        )


class FailingEmbedder(FakeEmbeddingProvider):
    """An embedder that cannot produce vectors."""

    def embed_batch(self, texts, *, task_type=EmbeddingTaskType.DOCUMENT):
        raise ProviderTimeoutError(
            "took too long", provider="fake", model="fake", role=ModelRole.EMBEDDING
        )


class TestOneCallForTheWholeEntry:
    def test_many_findings_cost_one_call(self):
        model = replying("first record", "second record", "third record")

        hyde.write_search_text(targets("a", "b", "c"), provider=model)

        assert len(model.calls) == 1

    def test_every_finding_is_in_that_call(self):
        model = replying("x", "y")

        hyde.write_search_text(targets("the comparing hurts", "felt small"), provider=model)

        prompt = model.calls[0].prompt
        assert "the comparing hurts" in prompt
        assert "felt small" in prompt

    def test_nothing_to_search_for_costs_nothing(self):
        model = replying()

        result = hyde.write_search_text((), provider=model)

        assert result.texts == ()
        assert model.calls == []


class TestAnswersLineUpWithFindings:
    def test_each_answer_lands_on_its_own_finding(self):
        model = replying("record one", "record two")

        result = hyde.write_search_text(targets("a", "b"), provider=model)

        assert result.texts == ("record one", "record two")

    def test_answers_are_placed_by_number_not_by_order(self):
        # A model that returns them out of order is still answerable, as
        # long as the numbers are trusted over the sequence.
        reply = json.dumps(
            {
                "hypotheticals": [
                    {"index": 2, "text": "second"},
                    {"index": 1, "text": "first"},
                ]
            }
        )
        model = FakeLLMProvider([reply], role=ModelRole.LIGHTWEIGHT)

        result = hyde.write_search_text(targets("a", "b"), provider=model)

        assert result.texts == ("first", "second")

    def test_a_missing_answer_keeps_its_place(self):
        # The failure this prevents is invisible: sliding the rest up would
        # search for every later finding using the wrong text, and the
        # matches would come back looking perfectly reasonable.
        reply = json.dumps({"hypotheticals": [{"index": 2, "text": "second only"}]})
        model = FakeLLMProvider([reply], role=ModelRole.LIGHTWEIGHT)

        result = hyde.write_search_text(targets("first finding", "b"), provider=model)

        assert result.texts == ("first finding", "second only")

    def test_a_blank_answer_falls_back_to_the_findings_own_words(self):
        model = replying("", "second")

        result = hyde.write_search_text(targets("first finding", "b"), provider=model)

        assert result.texts[0] == "first finding"

    def test_extra_answers_are_ignored(self):
        model = replying("one", "two", "three")

        result = hyde.write_search_text(targets("a"), provider=model)

        assert result.texts == ("one",)


class TestWhenTheModelCannotHelp:
    @pytest.mark.parametrize(
        "model_factory",
        [
            lambda: FailingProvider([], role=ModelRole.LIGHTWEIGHT),
            lambda: FakeLLMProvider(["not json"], role=ModelRole.LIGHTWEIGHT),
            lambda: FakeLLMProvider(
                [json.dumps({"hypotheticals": "not a list"})], role=ModelRole.LIGHTWEIGHT
            ),
        ],
        ids=["call_failed", "not_json", "wrong_shape"],
    )
    def test_the_findings_own_words_are_used_instead(self, model_factory):
        # A worse search, but a real one. Returning nothing would look
        # exactly like a person with no history on the subject, which is
        # the one mistake this stage must not make.
        result = hyde.write_search_text(
            targets("the comparing hurts"), provider=model_factory()
        )

        assert result.texts == ("the comparing hurts",)
        assert result.used_fallback is True

    def test_the_fallback_is_warned_about(self, caplog):
        with caplog.at_level(logging.WARNING):
            hyde.write_search_text(
                targets("a"), provider=FakeLLMProvider(["nope"], role=ModelRole.LIGHTWEIGHT)
            )

        assert any("falling back" in record.getMessage() for record in caplog.records)


class TestTurningTextIntoVectors:
    def test_every_text_becomes_a_vector(self, embedder):
        result = hyde.write_search_text(targets("a", "b"), provider=replying("x", "y"))

        vectors, failed = hyde.to_vectors(result, embedder=embedder)

        assert len(vectors) == 2
        assert failed is False

    def test_they_are_embedded_as_records_not_as_questions(self, embedder):
        # Turning the question into a record is the whole point of writing
        # one; labelling it a question would apply that correction twice.
        seen = {}
        original = embedder.embed_batch

        def watching(texts, *, task_type=EmbeddingTaskType.DOCUMENT):
            seen["task_type"] = task_type
            return original(texts, task_type=task_type)

        embedder.embed_batch = watching
        result = hyde.write_search_text(targets("a"), provider=replying("x"))

        hyde.to_vectors(result, embedder=embedder)

        assert seen["task_type"] is EmbeddingTaskType.DOCUMENT

    def test_nothing_to_embed_is_not_a_failure(self, embedder):
        vectors, failed = hyde.to_vectors(hyde.write_search_text((), provider=replying()), embedder=embedder)

        assert vectors == []
        assert failed is False

    def test_a_failed_embedding_is_reported_as_a_failure(self):
        # Without vectors there is no search at all, and that has to be
        # distinguishable from a search that found nothing.
        result = hyde.write_search_text(targets("a"), provider=replying("x"))

        vectors, failed = hyde.to_vectors(
            result, embedder=FailingEmbedder(dimensions=768)
        )

        assert vectors == []
        assert failed is True

    def test_a_short_batch_is_refused_rather_than_misaligned(self, embedder):
        # Order is the only thing tying a vector to its finding.
        embedder.embed_batch = lambda texts, *, task_type=None: [[0.1] * 768]
        result = hyde.write_search_text(targets("a", "b"), provider=replying("x", "y"))

        vectors, failed = hyde.to_vectors(result, embedder=embedder)

        assert vectors == []
        assert failed is True
