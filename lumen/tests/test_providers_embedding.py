"""
Tests for how a batch of text gets embedded.

The promise being checked is that vectors come back in the order the texts went
in, and that a batch either fully succeeds or fully fails. Callers line these up
against their own ids, so a vector in the wrong position would attach a piece of
the graph to the wrong entry — worse than an error, because nothing would look
broken.
"""

from __future__ import annotations

import pytest

from lumen.config import ProviderConfig
from lumen.providers.base import BaseEmbeddingProvider
from lumen.providers.errors import (
    ProviderError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from lumen.schemas.enums import EmbeddingTaskType


class CountingProvider(BaseEmbeddingProvider):
    """
    Records every chunk it was given and returns vectors it can be identified by.

    Each vector's first number is the position of its text in the original list,
    which is what lets a test check the ordering directly.
    """

    provider_name = "counting"

    def __init__(self, config: ProviderConfig, *, dimensions: int = 4, fail_on: str | None = None):
        super().__init__("counting-model", config, dimensions)
        self.chunks: list[list[str]] = []
        self.fail_on = fail_on
        self.prepared: list[str] = []

    def _embed_chunk(self, texts, task_type):
        self.chunks.append(list(texts))
        if self.fail_on is not None and self.fail_on in texts:
            raise ProviderTimeoutError("this chunk always fails")
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        marker = float(int(text.split("-")[-1])) if "-" in text else 0.0
        return [marker] + [0.0] * (self.dimensions - 1)


class ShortReplyProvider(BaseEmbeddingProvider):
    """Returns fewer vectors than it was asked for, to check that is caught."""

    provider_name = "short"

    def __init__(self, config: ProviderConfig):
        super().__init__("short-model", config, 4)

    def _embed_chunk(self, texts, task_type):
        return [[0.0] * 4 for _ in texts[:-1]]


class PrefixingProvider(CountingProvider):
    """Adds something to each text, the way a prefix-using model needs."""

    provider_name = "prefixing"

    def _prepare_text(self, text, task_type):
        self.prepared.append(text)
        return f"{task_type.value.lower()}: {text}"


def texts(count: int) -> list[str]:
    return [f"text-{index}" for index in range(count)]


class TestSplittingWork:
    def test_a_small_batch_goes_in_one_chunk(self, provider_config):
        provider = CountingProvider(ProviderConfig(embed_batch_size=32))
        provider.embed_batch(texts(5))
        assert len(provider.chunks) == 1

    def test_a_large_batch_is_split(self, provider_config):
        provider = CountingProvider(ProviderConfig(embed_batch_size=2))
        provider.embed_batch(texts(5))
        assert [len(chunk) for chunk in provider.chunks] == [2, 2, 1]

    def test_the_configured_size_is_respected(self):
        provider = CountingProvider(ProviderConfig(embed_batch_size=3))
        provider.embed_batch(texts(9))
        assert all(len(chunk) <= 3 for chunk in provider.chunks)

    def test_an_empty_batch_does_no_work(self, provider_config):
        provider = CountingProvider(provider_config)
        assert provider.embed_batch([]) == []
        assert provider.chunks == []

    def test_one_text_gives_one_vector(self, provider_config):
        provider = CountingProvider(provider_config)
        assert len(provider.embed_text("text-0")) == 4


class TestOrdering:
    def test_order_is_kept_within_one_chunk(self, provider_config):
        provider = CountingProvider(ProviderConfig(embed_batch_size=32))
        vectors = provider.embed_batch(texts(5))
        assert [vector[0] for vector in vectors] == [0.0, 1.0, 2.0, 3.0, 4.0]

    def test_order_is_kept_across_chunks(self):
        """The join back together must not shuffle anything."""
        provider = CountingProvider(ProviderConfig(embed_batch_size=2))
        vectors = provider.embed_batch(texts(7))
        assert [vector[0] for vector in vectors] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]

    def test_order_is_kept_when_chunks_run_side_by_side(self):
        provider = CountingProvider(
            ProviderConfig(embed_batch_size=1, embed_max_workers=4)
        )
        vectors = provider.embed_batch(texts(10))
        assert [vector[0] for vector in vectors] == [float(index) for index in range(10)]

    def test_the_count_coming_back_matches_the_count_going_in(self, provider_config):
        provider = CountingProvider(ProviderConfig(embed_batch_size=3))
        assert len(provider.embed_batch(texts(10))) == 10


class TestAllOrNothing:
    def test_one_bad_chunk_fails_the_whole_batch(self):
        """
        Rather than returning a short list, which would leave the caller lining
        vectors up against the wrong ids.
        """
        provider = CountingProvider(
            ProviderConfig(embed_batch_size=2, max_attempts=1, backoff_base_seconds=0.0),
            fail_on="text-4",
        )
        with pytest.raises(ProviderTimeoutError):
            provider.embed_batch(texts(7))

    def test_a_failing_chunk_is_retried_first(self):
        provider = CountingProvider(
            ProviderConfig(embed_batch_size=10, max_attempts=3, backoff_base_seconds=0.0),
            fail_on="text-0",
        )
        with pytest.raises(ProviderTimeoutError):
            provider.embed_batch(texts(3))
        assert len(provider.chunks) == 3

    def test_a_provider_returning_too_few_vectors_is_caught(self, provider_config):
        provider = ShortReplyProvider(
            ProviderConfig(max_attempts=1, backoff_base_seconds=0.0)
        )
        with pytest.raises(ProviderError, match="expected 4 vectors"):
            provider.embed_batch(texts(4))


class TestPreparingText:
    def test_by_default_the_text_is_sent_unchanged(self, provider_config):
        provider = CountingProvider(provider_config)
        provider.embed_batch(["text-0"])
        assert provider.chunks[0] == ["text-0"]

    def test_a_provider_can_change_the_text_before_sending(self, provider_config):
        """Which is how a model that expects a task prefix gets one."""
        provider = PrefixingProvider(provider_config)
        provider.embed_batch(["text-0"], task_type=EmbeddingTaskType.QUERY)
        assert provider.chunks[0] == ["query: text-0"]

    def test_every_text_in_a_batch_is_prepared(self, provider_config):
        provider = PrefixingProvider(ProviderConfig(embed_batch_size=32))
        provider.embed_batch(texts(3))
        assert all(chunk.startswith("document: ") for chunk in provider.chunks[0])

    def test_the_task_reaches_the_provider(self, provider_config):
        provider = PrefixingProvider(provider_config)
        provider.embed_batch(["text-0"], task_type=EmbeddingTaskType.CLASSIFICATION)
        assert provider.chunks[0] == ["classification: text-0"]

    def test_storing_is_the_default_task(self, provider_config):
        """Most embedding is done to store something, so that is the default."""
        provider = PrefixingProvider(provider_config)
        provider.embed_batch(["text-0"])
        assert provider.chunks[0] == ["document: text-0"]


class TestDeclaredWidth:
    def test_a_provider_reports_its_width(self, provider_config):
        assert CountingProvider(provider_config, dimensions=1024).dimensions == 1024

    def test_the_vectors_are_that_width(self, provider_config):
        provider = CountingProvider(provider_config, dimensions=16)
        assert len(provider.embed_text("text-0")) == 16


class TestClosing:
    def test_closing_is_safe(self, provider_config):
        CountingProvider(provider_config).close()
