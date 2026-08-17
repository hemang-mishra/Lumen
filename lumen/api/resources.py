"""
The one search stack this process holds open.

Fetching history during a conversation needs three things the read routes
never needed: a search index, something to turn text into vectors, and a
model to write the text being searched with. Opening those at startup would
be wrong twice over — a deployment with no model configured would stop
booting, and the index would be opened a second time by a process that
already has it open for imports, which a local Qdrant simply refuses.

So they are built on first use, and shared. When this deployment accepts
uploads, the importer already owns an index and an embedder and those are
borrowed rather than duplicated. When it does not, this opens its own and
closes them when the process stops.

The model is the exception: it is built here even when one already exists,
because this one has retries switched off. Every other call in Lumen retries
a few times with a growing pause, which is right for work nobody is waiting
on. A call inside a three-second conversational budget that failed has
already missed it, and retrying only guarantees the wait is spent twice.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace

from lumen.config import AppConfig
from lumen.graph.provider import GraphProvider, ReadOnlyGraph
from lumen.ingest.worker import IngestResources, IngestWorker, build_resources
from lumen.providers.factory import get_llm_provider
from lumen.query import ConversationalRetriever
from lumen.schemas.enums import ModelRole

logger = logging.getLogger(__name__)


class LazySearchStack:
    """
    Builds the conversational retriever the first time somebody asks for it.

    Holds a lock because two requests can arrive together on the first call,
    and building two index handles for one folder is exactly the thing this
    class exists to prevent.
    """

    def __init__(
        self,
        *,
        config: AppConfig,
        graph: GraphProvider,
        reader: ReadOnlyGraph,
        worker: IngestWorker | None = None,
    ) -> None:
        """
        Args:
            config: Settings for this deployment.
            graph: The writable graph, needed only because opening the
                shared resources borrows it. Nothing here ever writes.
            reader: What the retriever is actually given — every question,
                none of the writes.
            worker: The importer, when this deployment has one. Its index
                and embedder are borrowed rather than opened again.
        """
        self._config = config
        self._graph = graph
        self._reader = reader
        self._worker = worker
        self._lock = threading.Lock()
        self._retriever: ConversationalRetriever | None = None
        self._owned: IngestResources | None = None

    def get(self) -> ConversationalRetriever:
        """
        The retriever, built if this is the first time.

        Raises:
            ProviderError: No model or embedder is configured. Left to
                propagate, because the honest answer to "fetch this
                person's history" with nothing to search with is a refusal,
                not an empty list that reads as "they have no history".
        """
        with self._lock:
            if self._retriever is None:
                self._retriever = self._build()
                logger.info("the conversational search stack is open")
            return self._retriever

    def close(self) -> None:
        """Release the thread pool, and the index if this opened its own."""
        with self._lock:
            if self._retriever is not None:
                self._retriever.close()
                self._retriever = None
            if self._owned is not None:
                self._owned.close()
                self._owned = None

    def _build(self) -> ConversationalRetriever:
        """Open what is missing, borrow what is not, and wire it together."""
        shared = self._shared_resources()
        return ConversationalRetriever(
            graph=self._reader,
            vectors=shared.vectors,
            embedder=shared.embedder,
            llm=self._model_without_retries(),
            config=self._config.query,
        )

    def _shared_resources(self) -> IngestResources:
        """
        The index and embedder, borrowed from the importer where there is one.

        Sharing is not an optimisation. A file-backed index takes a lock, so
        a second handle on the same folder inside one process is refused —
        and two handles on the same collection would be two views of one
        thing with no reason to agree.
        """
        if self._worker is not None:
            return self._worker.ensure_ready()
        if self._owned is None:
            self._owned = build_resources(self._config, self._graph)
        return self._owned

    def _model_without_retries(self):
        """The fast model, told to answer once or not at all."""
        no_retries = replace(self._config.providers, max_attempts=1)
        return get_llm_provider(
            ModelRole.LIGHTWEIGHT, replace(self._config, providers=no_retries)
        )


__all__ = ["LazySearchStack"]
