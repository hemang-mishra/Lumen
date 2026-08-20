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
from lumen.ingest.worker import (
    IngestResources,
    IngestWorker,
    build_resources,
    open_index,
)
from lumen.providers.factory import get_llm_provider
from lumen.providers.protocols import EmbeddingProvider
from lumen.query import ConversationalRetriever
from lumen.schemas.enums import ModelRole
from lumen.vector.provider import VectorProvider

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
        # An index opened on its own, for callers that need no model.
        self._index: VectorProvider | None = None

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

    def vectors(self) -> VectorProvider:
        """
        The search index, opened if this is the first thing to want it.

        Handed out so anything else needing to write to the index borrows
        this one rather than opening its own. A file-backed index takes a
        lock, so a second handle on the same folder inside one process is
        refused outright.
        """
        with self._lock:
            return self._shared_resources().vectors

    def index(self) -> VectorProvider:
        """
        The search index alone, without needing a model configured.

        The difference from `vectors` matters for exactly one caller.
        Erasing somebody's data has to delete their positions in the index,
        and deleting needs nothing that writing needs — so a deployment whose
        model credentials have expired must still be able to do it.

        Where an importer exists it is still asked first. It owns the index
        on a deployment that has one, and a second handle on a file-backed
        index inside one process is refused.
        """
        with self._lock:
            if self._worker is not None:
                return self._worker.ensure_ready().vectors
            if self._owned is not None:
                return self._owned.vectors
            if self._index is None:
                self._index = open_index(self._config)
            return self._index

    def embedder(self) -> EmbeddingProvider:
        """The embedding model, borrowed from the same shared resources."""
        with self._lock:
            return self._shared_resources().embedder

    def close(self) -> None:
        """Release the thread pool, and the index if this opened its own."""
        with self._lock:
            if self._retriever is not None:
                self._retriever.close()
                self._retriever = None
            if self._owned is not None:
                self._owned.close()
                self._owned = None
            if self._index is not None:
                self._index.close()
                self._index = None

    def _build(self) -> ConversationalRetriever:
        """Open what is missing, borrow what is not, and wire it together."""
        shared = self._shared_resources()
        return ConversationalRetriever(
            graph=self._reader,
            vectors=shared.vectors,
            embedder=shared.embedder,
            llm=self._model_without_retries(),
            config=self._config.query,
            scoring=self._config.scoring,
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
            # Handing over any index already opened on its own, since a
            # second handle on a file-backed one would be refused.
            self._owned = build_resources(self._config, self._graph, self._index)
        return self._owned

    def _model_without_retries(self):
        """The fast model, told to answer once or not at all."""
        no_retries = replace(self._config.providers, max_attempts=1)
        return get_llm_provider(
            ModelRole.LIGHTWEIGHT, replace(self._config, providers=no_retries)
        )


__all__ = ["LazySearchStack"]


class LazyEraser:
    """
    Builds the thing that forgets, the first time somebody asks it to.

    Built late for one reason: erasing touches the search index, and opening
    the index needs an embedding model configured. Building this at startup
    would mean a deployment with no model refusing to start, when everything
    it can actually do — reading the graph, listing what was erased before —
    needs no model at all.
    """

    def __init__(
        self, *, config: AppConfig, graph, ops, search: LazySearchStack
    ) -> None:
        self._config = config
        self._graph = graph
        self._ops = ops
        self._search = search
        self._lock = threading.Lock()
        self._service = None

    def get(self):
        """The erasure service, opening the index if this is the first ask."""
        from lumen.erasure import ErasureService

        with self._lock:
            if self._service is None:
                self._service = ErasureService(
                    config=self._config,
                    graph=self._graph,
                    vectors=self._search.index(),
                    ops=self._ops,
                )
            return self._service


class LazyChatStack:
    """
    Builds the thing that holds a conversation, the first time one is held.

    Built late for the same reason the search is: it needs models, and every
    other route in this service reads two local databases and needs none. A
    deployment with nothing configured still starts and still serves the
    graph — only talking is refused, and it says why.

    The voice is separate again inside this. Speech is the one job with no
    local option at all, so a deployment can perfectly well have a chat model
    and no voice, and that has to be an ordinary state rather than a failure.
    """

    def __init__(
        self,
        *,
        config: AppConfig,
        search: LazySearchStack,
        formulator,
        composer,
        memory,
        sessions,
        personas=None,
        graph=None,
    ) -> None:
        self._config = config
        self._search = search
        self._formulator = formulator
        self._composer = composer
        self._memory = memory
        self._sessions = sessions
        self._personas = personas
        self._graph = graph
        self._lock = threading.Lock()
        self._engine = None
        self._llm = None
        self._speech = None
        self._listener = None

    def engine(self):
        """
        The chat engine, built if this is the first time.

        Raises:
            ProviderError: No conversation model is configured. Left to
                propagate — a chat with nothing to answer with is a refusal,
                not a silence.
        """
        from lumen.query.chat import ChatEngine

        with self._lock:
            if self._engine is None:
                self._engine = ChatEngine(
                    formulator=self._formulator,
                    retriever=self._search.get(),
                    composer=self._composer,
                    memory=self._memory,
                    sessions=self._sessions,
                    llm=self._reply_model(),
                    speech=self._voice(),
                    personas=self._personas,
                    hits=self._hit_recorder(),
                    config=self._config.chat,
                )
            return self._engine

    def _hit_recorder(self):
        """
        The thing that counts which records a turn used, where there is one.

        Nothing when no writable graph was handed in. Counting is a
        convenience and a deployment reading a graph it cannot write to
        should still be able to hold a conversation.
        """
        from lumen.query.frequency import QueryHitRecorder

        if self._graph is None:
            return None
        return QueryHitRecorder(self._graph, config=self._config.scoring)

    def listener(self):
        """
        The thing that turns a recording into words.

        Built on its own so that a deployment with no speech model still
        chats normally — asking for this is the only thing that fails.
        """
        from lumen.providers.factory import get_transcription_provider

        with self._lock:
            if self._listener is None:
                self._listener = get_transcription_provider(self._config)
            return self._listener

    def close(self) -> None:
        """Release the models this opened, leaving borrowed ones alone."""
        for held in (self._llm, self._speech, self._listener):
            if held is not None:
                _quietly_close(held)
        self._engine = None
        self._llm = self._speech = self._listener = None

    def _reply_model(self):
        """The model that writes the replies."""
        from lumen.providers.factory import get_llm_provider
        from lumen.schemas.enums import ModelRole

        if self._llm is None:
            self._llm = get_llm_provider(ModelRole.CONVERSATION, self._config)
        return self._llm

    def _voice(self):
        """
        The voice, if this deployment has one.

        A missing voice is not a failure. It costs the spoken half of a
        conversation and nothing else, so it is logged and set aside rather
        than stopping anybody from talking.
        """
        from lumen.providers.errors import ProviderError
        from lumen.providers.factory import get_speech_provider

        if not self._config.chat.voice_enabled:
            return None
        if self._speech is None:
            try:
                self._speech = get_speech_provider(self._config)
            except ProviderError:
                logger.warning(
                    "no voice is configured, so replies will not be spoken",
                    exc_info=True,
                )
                return None
        return self._speech


def _quietly_close(held) -> None:
    """Close something without letting a failed cleanup mask a real problem."""
    try:
        held.close()
    except Exception:
        logger.warning("a provider did not close cleanly", exc_info=True)

