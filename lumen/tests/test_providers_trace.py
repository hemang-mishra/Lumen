"""
Tests that a model call's log lines stay attached to the run that caused them.

The trace id lives in a context variable, which every log line picks up on its
own. That works because the call happens on the same thread as the run.

Embedding a batch can spread the work across several threads, and context
variables are not shared with new threads. So without deliberately carrying the
context across, every line written by a worker would come out with no trace id
on it — on the busiest path in the whole system, and only when concurrency is
switched on, which is exactly the kind of thing that goes unnoticed.
"""

from __future__ import annotations

import threading

from lumen.config import ProviderConfig
from lumen.observability.trace import bind_trace
from lumen.providers.fake import FakeEmbeddingProvider, FakeLLMProvider
from lumen.providers.results import ChatMessage


def concurrent_config(**overrides) -> ProviderConfig:
    """Settings that force the work onto several threads."""
    settings = {
        "embed_batch_size": 1,
        "embed_max_workers": 4,
        "max_attempts": 1,
        "backoff_base_seconds": 0.0,
    }
    settings.update(overrides)
    return ProviderConfig(**settings)


def provider_lines(logs):
    """The lines written by the provider layer."""
    return [line for line in logs if line.get("logger") == "lumen.providers"]


class TestOnOneThread:
    def test_a_text_call_carries_the_trace_id(self, captured_logs):
        with bind_trace("trace-text-001"):
            FakeLLMProvider(["hello"]).generate_text([ChatMessage(role="user", content="hi")])

        assert provider_lines(captured_logs)[0]["trace_id"] == "trace-text-001"

    def test_an_embedding_call_carries_the_trace_id(self, captured_logs):
        with bind_trace("trace-embed-001"):
            FakeEmbeddingProvider(dimensions=8).embed_batch(["a", "b"])

        assert provider_lines(captured_logs)[0]["trace_id"] == "trace-embed-001"

    def test_outside_a_run_there_is_simply_no_trace_id(self, captured_logs):
        FakeEmbeddingProvider(dimensions=8).embed_text("a")
        assert provider_lines(captured_logs)[0].get("trace_id") is None


class TestAcrossThreads:
    def test_workers_carry_the_trace_id_across(self, captured_logs):
        """
        The point of this test. Splitting a batch across threads must not lose
        the id that ties the work back to the journal entry it came from.
        """
        config = concurrent_config()
        provider = FakeEmbeddingProvider(dimensions=8, config=config)

        with bind_trace("trace-pool-001"):
            vectors = provider.embed_batch(["a", "b", "c", "d", "e", "f"])

        assert len(vectors) == 6
        lines = provider_lines(captured_logs)
        assert lines, "the call should have written a log line"
        assert all(line.get("trace_id") == "trace-pool-001" for line in lines)

    def test_the_work_really_did_use_several_threads(self):
        """
        Otherwise the test above would pass for the wrong reason — a batch that
        quietly ran in order would never exercise the thing being checked.
        """
        seen: set[int] = set()
        barrier = threading.Barrier(4, timeout=5)

        class ThreadWatching(FakeEmbeddingProvider):
            def _embed_chunk(self, texts, task_type):
                seen.add(threading.get_ident())
                barrier.wait()
                return super()._embed_chunk(texts, task_type)

        provider = ThreadWatching(dimensions=8, config=concurrent_config())
        provider.embed_batch(["a", "b", "c", "d"])

        assert len(seen) > 1

    def test_the_vectors_keep_their_order(self):
        """
        Threads finish in whatever order they like. Callers line these up against
        their own ids, so the order coming back has to match the order going in.
        """
        config = concurrent_config()
        provider = FakeEmbeddingProvider(dimensions=8, config=config)
        sequential = FakeEmbeddingProvider(dimensions=8)

        texts = [f"text-{index}" for index in range(10)]
        assert provider.embed_batch(texts) == [sequential.embed_text(text) for text in texts]

    def test_two_runs_on_separate_threads_do_not_mix(self, captured_logs):
        """Each run's lines belong to that run, even when they overlap."""
        provider = FakeEmbeddingProvider(dimensions=8, config=concurrent_config())
        start = threading.Barrier(2, timeout=5)

        def run(trace_id: str) -> None:
            start.wait()
            with bind_trace(trace_id):
                provider.embed_batch(["a", "b", "c", "d"])

        first = threading.Thread(target=run, args=("trace-aaa",))
        second = threading.Thread(target=run, args=("trace-bbb",))
        first.start()
        second.start()
        first.join()
        second.join()

        found = {line.get("trace_id") for line in provider_lines(captured_logs)}
        assert found == {"trace-aaa", "trace-bbb"}


class TestSequentialIsStillTheDefault:
    def test_one_worker_means_no_threads_are_started(self):
        """
        Concurrency is off unless asked for, because several requests at once is
        the quickest way to trip a metered API's rate limit.
        """
        calling_threads: set[int] = set()

        class ThreadWatching(FakeEmbeddingProvider):
            def _embed_chunk(self, texts, task_type):
                calling_threads.add(threading.get_ident())
                return super()._embed_chunk(texts, task_type)

        config = ProviderConfig(embed_batch_size=1, embed_max_workers=1)
        ThreadWatching(dimensions=8, config=config).embed_batch(["a", "b", "c"])

        assert calling_threads == {threading.get_ident()}

    def test_a_single_chunk_does_not_start_a_pool(self):
        """No point spinning threads up for one piece of work."""
        calling_threads: set[int] = set()

        class ThreadWatching(FakeEmbeddingProvider):
            def _embed_chunk(self, texts, task_type):
                calling_threads.add(threading.get_ident())
                return super()._embed_chunk(texts, task_type)

        config = ProviderConfig(embed_batch_size=100, embed_max_workers=4)
        ThreadWatching(dimensions=8, config=config).embed_batch(["a", "b"])

        assert calling_threads == {threading.get_ident()}
