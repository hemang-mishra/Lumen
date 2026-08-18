"""
The one search stack a running process holds open.

Two things here are worth pinning down. The index is opened once and shared,
because a file-backed one takes a lock and a second handle on the same folder
is simply refused — so a service that both imports and answers conversations
has to borrow rather than open. And nothing is built until something asks,
because a deployment with no model configured must still start and still
serve every route that needs none.
"""

from __future__ import annotations

import pytest

from lumen.api.resources import LazySearchStack
from lumen.config import AppConfig, ProviderConfig
from lumen.ingest.worker import IngestResources
from lumen.providers.errors import ProviderError
from lumen.providers.fake import FakeEmbeddingProvider, FakeLLMProvider


class StubWorker:
    """An importer that hands over resources it already opened."""

    def __init__(self, resources: IngestResources) -> None:
        self._resources = resources
        self.asked = 0

    def ensure_ready(self) -> IngestResources:
        self.asked += 1
        return self._resources


@pytest.fixture
def borrowed(graph_store, vector_store):
    """What an importer would already have open."""
    return IngestResources(
        graph=graph_store,
        vectors=vector_store,
        embedder=FakeEmbeddingProvider(dimensions=768),
        lightweight=FakeLLMProvider([]),
        thinking=FakeLLMProvider([]),
    )


@pytest.fixture
def stack(graph_store, borrowed, monkeypatch):
    """A stack over a stubbed importer and a model that always resolves."""

    # "Nothing passed" has to mean "give me an importer", while an explicit
    # None means "this deployment has none" — the case where the stack opens
    # its own index.
    unset = object()

    def _build(worker=unset, config=None):
        monkeypatch.setattr(
            "lumen.api.resources.get_llm_provider",
            lambda role, settings: FakeLLMProvider([]),
        )
        return LazySearchStack(
            config=config or AppConfig(),
            graph=graph_store,
            reader=graph_store,
            worker=StubWorker(borrowed) if worker is unset else worker,
        )

    return _build


class TestBuildingOnDemand:
    def test_nothing_is_opened_until_something_asks(self, stack, borrowed):
        worker = StubWorker(borrowed)

        stack(worker)

        assert worker.asked == 0

    def test_the_first_ask_builds_it(self, stack, borrowed):
        worker = StubWorker(borrowed)

        retriever = stack(worker).get()

        assert retriever is not None
        assert worker.asked == 1

    def test_the_second_ask_gets_the_same_one(self, stack, borrowed):
        # Building one per request would pay for a thread pool and a model
        # connection on every call, which is the opposite of what a
        # component with a three-second budget wants.
        worker = StubWorker(borrowed)
        held = stack(worker)

        assert held.get() is held.get()
        assert worker.asked == 1


class TestSharingTheIndex:
    def test_the_importer_s_index_is_borrowed_rather_than_opened_again(
        self, stack, borrowed, vector_store
    ):
        # Not an optimisation. A file-backed index takes a lock, so a second
        # handle on the same folder in one process is refused outright.
        retriever = stack(StubWorker(borrowed)).get()

        assert retriever._vectors is vector_store

    def test_a_deployment_with_no_importer_opens_its_own(
        self, stack, graph_store, monkeypatch, borrowed
    ):
        opened = []

        def _open(config, graph):
            opened.append(graph)
            return borrowed

        monkeypatch.setattr("lumen.api.resources.build_resources", _open)
        held = stack(worker=None)

        held.get()

        assert opened == [graph_store]

    def test_it_opens_its_own_only_once(self, stack, monkeypatch, borrowed):
        calls = []
        monkeypatch.setattr(
            "lumen.api.resources.build_resources",
            lambda config, graph: (calls.append(1), borrowed)[1],
        )
        held = stack(worker=None)

        held.get()
        held.get()

        assert len(calls) == 1


class TestClosing:
    def test_what_it_opened_itself_is_closed(self, stack, monkeypatch, borrowed):
        closed = []
        monkeypatch.setattr(borrowed, "close", lambda: closed.append(True))
        monkeypatch.setattr(
            "lumen.api.resources.build_resources", lambda config, graph: borrowed
        )
        held = stack(worker=None)
        held.get()

        held.close()

        assert closed == [True]

    def test_what_it_borrowed_is_left_alone(self, stack, borrowed, monkeypatch):
        # The importer is still using it and is responsible for shutting it
        # down; closing it here would pull it out from under a running
        # import.
        closed = []
        monkeypatch.setattr(borrowed, "close", lambda: closed.append(True))
        held = stack(StubWorker(borrowed))
        held.get()

        held.close()

        assert closed == []

    def test_closing_twice_is_harmless(self, stack, borrowed):
        held = stack(StubWorker(borrowed))
        held.get()

        held.close()
        held.close()

    def test_closing_one_that_was_never_used_is_harmless(self, stack, borrowed):
        stack(StubWorker(borrowed)).close()


class TestWhenNothingIsConfigured:
    def test_a_missing_model_is_raised_rather_than_swallowed(
        self, graph_store, borrowed, monkeypatch
    ):
        # An empty list of records would read as "this person has no
        # history", which is the one answer that must never be given by
        # mistake.
        def refuse(role, settings):
            raise ProviderError("no model configured")

        monkeypatch.setattr("lumen.api.resources.get_llm_provider", refuse)
        held = LazySearchStack(
            config=AppConfig(),
            graph=graph_store,
            reader=graph_store,
            worker=StubWorker(borrowed),
        )

        with pytest.raises(ProviderError):
            held.get()

    def test_the_model_it_builds_does_not_retry(
        self, graph_store, borrowed, monkeypatch
    ):
        # Every other call in Lumen retries with backoff, which is right for
        # work nobody is waiting on. A call inside a three-second budget that
        # failed has already missed it.
        seen: list[ProviderConfig] = []

        def record(role, settings):
            seen.append(settings.providers)
            return FakeLLMProvider([])

        monkeypatch.setattr("lumen.api.resources.get_llm_provider", record)
        LazySearchStack(
            config=AppConfig(),
            graph=graph_store,
            reader=graph_store,
            worker=StubWorker(borrowed),
        ).get()

        assert seen[0].max_attempts == 1
