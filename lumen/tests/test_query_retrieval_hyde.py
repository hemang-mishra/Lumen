"""
Writing the text a turn is searched with.

Most of this is exercised through Pass A. What is here is the handful of
paths a search never reaches on its own — nothing to write about, and an
embedder that comes back with the wrong number of answers, which is the
quiet failure worth guarding: position is the only thing tying a vector to
the reason it belongs to.
"""

from __future__ import annotations

import json

from lumen.providers.fake import FakeEmbeddingProvider, FakeLLMProvider
from lumen.query.retrieval import hyde
from lumen.query.retrieval.contracts import SearchText
from lumen.schemas.enums import TriggerType
from lumen.schemas.query import RetrievalTrigger


def trigger(**fields) -> RetrievalTrigger:
    return RetrievalTrigger(
        trigger_type=fields.pop("trigger_type", TriggerType.PATTERN_MENTION), **fields
    )


def replies(texts) -> FakeLLMProvider:
    return FakeLLMProvider(
        {
            "ITEMS:": json.dumps(
                {
                    "hypotheticals": [
                        {"index": position, "text": text}
                        for position, text in enumerate(texts, start=1)
                    ]
                }
            )
        }
    )


class TestWritingIt:
    def test_nothing_to_write_about_costs_no_call(self):
        llm = FakeLLMProvider([])

        assert hyde.write_search_text("anything", (), provider=llm) == SearchText()
        assert llm.calls == []

    def test_one_invented_record_per_reason(self):
        written = hyde.write_search_text(
            "I keep avoiding it",
            (trigger(), trigger(trigger_type=TriggerType.SOMATIC_MARKER)),
            provider=replies(["first record", "second record"]),
        )

        assert written.texts == ("first record", "second record")
        assert written.used_fallback is False

    def test_answers_are_placed_by_number_not_by_arrival(self):
        # Sliding them up to fill a gap would search every later reason with
        # the wrong text, and that failure is invisible.
        reply = json.dumps(
            {"hypotheticals": [{"index": 2, "text": "belongs to the second"}]}
        )
        written = hyde.write_search_text(
            "said aloud",
            (trigger(keywords=()), trigger(trigger_type=TriggerType.SOMATIC_MARKER,
                                           keywords=())),
            provider=FakeLLMProvider({"ITEMS:": reply}),
        )

        assert written.texts == ("said aloud", "belongs to the second")

    def test_a_blank_invention_falls_back_to_what_was_said(self):
        written = hyde.write_search_text(
            "said aloud",
            (trigger(keywords=()),),
            provider=replies(["   "]),
        )

        assert written.texts == ("said aloud",)


class TestTurningItIntoVectors:
    def test_nothing_to_embed_is_not_a_failure(self):
        vectors, failed = hyde.to_vectors(
            SearchText(), embedder=FakeEmbeddingProvider(dimensions=8)
        )

        assert (vectors, failed) == ([], False)

    def test_every_text_becomes_a_vector(self):
        vectors, failed = hyde.to_vectors(
            SearchText(texts=("one", "two")),
            embedder=FakeEmbeddingProvider(dimensions=8),
        )

        assert len(vectors) == 2
        assert failed is False

    def test_a_batch_of_the_wrong_length_is_refused_rather_than_lined_up(self):
        # Position is the only thing tying a vector to its reason. A batch
        # that comes back short cannot be trusted to line up, and searching
        # with a mismatched vector returns confident wrong records.
        class ShortEmbedder:
            def embed_batch(self, texts, task_type=None):
                return [[1.0, 0.0]]

        vectors, failed = hyde.to_vectors(
            SearchText(texts=("one", "two")), embedder=ShortEmbedder()
        )

        assert (vectors, failed) == ([], True)
