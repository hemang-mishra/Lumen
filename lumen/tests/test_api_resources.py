"""
The one search stack a running process holds open.

What is worth pinning down here is that nothing is built until something
asks, because a deployment with no model configured must still start and
still serve every route that needs none.

It used to be about sharing the search index as well. That is the store
registry's job now — there is a collection per person and one connection
between them — so what is left here is a retriever and the models it holds.
"""

from __future__ import annotations

import pytest

from lumen.api.resources import LazySearchStack
from lumen.config import AppConfig, ProviderConfig
from lumen.ingest.worker import IngestModels
from lumen.providers.errors import ProviderError
from lumen.providers.fake import FakeEmbeddingProvider, FakeLLMProvider


class StubWorker:
    """An importer that hands over the models it already opened."""

    def __init__(self, resources: IngestModels) -> None:
        self._resources = resources
        self.asked = 0

    def ensure_ready(self) -> IngestModels:
        self.asked += 1
        return self._resources


@pytest.fixture
def borrowed():
    """What an importer would already have open."""
    return IngestModels(
        embedder=FakeEmbeddingProvider(dimensions=768),
        lightweight=FakeLLMProvider([]),
        thinking=FakeLLMProvider([]),
    )


@pytest.fixture
def stack(store_registry, borrowed, monkeypatch):
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
            stores=store_registry,
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


class TestSharingTheModels:
    def test_the_importer_s_models_are_borrowed_rather_than_built_again(
        self, stack, borrowed
    ):
        # Only an economy now — they are stateless clients. The thing that
        # genuinely could not be opened twice was the search index, and that
        # is the store registry's problem.
        worker = StubWorker(borrowed)

        stack(worker).get()

        assert worker.asked == 1

    def test_a_deployment_with_no_importer_builds_its_own(
        self, stack, monkeypatch, borrowed
    ):
        built = []

        def _build(config):
            built.append(1)
            return borrowed

        monkeypatch.setattr("lumen.api.resources.build_models", _build)
        held = stack(worker=None)

        held.get()

        assert built == [1]

    def test_it_builds_its_own_only_once(self, stack, monkeypatch, borrowed):
        calls = []
        monkeypatch.setattr(
            "lumen.api.resources.build_models",
            lambda config: (calls.append(1), borrowed)[1],
        )
        held = stack(worker=None)

        held.get()
        held.get()

        assert len(calls) == 1

    def test_the_retriever_is_given_the_registry_rather_than_a_store(
        self, stack, borrowed, store_registry
    ):
        # The whole of how one turn is about one person: which store it reads
        # is decided per turn, from who is talking.
        retriever = stack(StubWorker(borrowed)).get()

        assert retriever._stores is store_registry


class TestClosing:
    def test_the_stores_are_not_this_object_s_to_close(
        self, stack, borrowed, store_registry
    ):
        # They belong to the registry, which closes them when the process
        # stops. Closing them here would pull a graph out from under whoever
        # else is holding it.
        held = stack(StubWorker(borrowed))
        held.get()

        held.close()

        assert store_registry.open_count == 0

    def test_closing_twice_is_harmless(self, stack, borrowed):
        held = stack(StubWorker(borrowed))
        held.get()

        held.close()
        held.close()

    def test_closing_one_that_was_never_used_is_harmless(self, stack, borrowed):
        stack(StubWorker(borrowed)).close()


class TestWhenNothingIsConfigured:
    def test_a_missing_model_is_raised_rather_than_swallowed(
        self, store_registry, borrowed, monkeypatch
    ):
        # An empty list of records would read as "this person has no
        # history", which is the one answer that must never be given by
        # mistake.
        def refuse(role, settings):
            raise ProviderError("no model configured")

        monkeypatch.setattr("lumen.api.resources.get_llm_provider", refuse)
        held = LazySearchStack(
            config=AppConfig(),
            stores=store_registry,
            worker=StubWorker(borrowed),
        )

        with pytest.raises(ProviderError):
            held.get()

    def test_the_model_it_builds_does_not_retry(
        self, store_registry, borrowed, monkeypatch
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
            stores=store_registry,
            worker=StubWorker(borrowed),
        ).get()

        assert seen[0].max_attempts == 1
