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
from lumen.ingest.worker import IngestModels, IngestWorker, build_models
from lumen.stores import StoreRegistry
from lumen.providers.factory import get_llm_provider
from lumen.providers.protocols import EmbeddingProvider
from lumen.query import ConversationalRetriever
from lumen.schemas.enums import ModelRole
from lumen.vector.provider import VectorProvider

logger = logging.getLogger(__name__)


class LazySearchStack:
    """
    Builds the conversational retriever the first time somebody asks for it.

    Built late because it needs models, and every other route in this service
    reads two local databases and needs none — a deployment with nothing
    configured still starts and still serves the graph.

    It used to exist mostly to stop two handles being opened on one search
    index. That is the store registry's problem now, and what is left here is
    a retriever and the models it holds.
    """

    def __init__(
        self,
        *,
        config: AppConfig,
        stores: StoreRegistry,
        worker: IngestWorker | None = None,
    ) -> None:
        """
        Args:
            config: Settings for this deployment.
            stores: Where a person's graph and search index come from. The
                retriever is given the registry rather than a store, because
                which store a turn is about depends on who is talking.
            worker: The importer, when this deployment has one. Its models are
                borrowed rather than built twice.
        """
        self._config = config
        self._stores = stores
        self._worker = worker
        self._lock = threading.Lock()
        self._retriever: ConversationalRetriever | None = None
        self._owned: IngestModels | None = None

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

    def embedder(self) -> EmbeddingProvider:
        """The embedding model, borrowed from the importer where there is one."""
        with self._lock:
            return self._models().embedder

    def close(self) -> None:
        """Release the thread pool. The stores are the registry's to close."""
        with self._lock:
            if self._retriever is not None:
                self._retriever.close()
                self._retriever = None

    def _build(self) -> ConversationalRetriever:
        """Wire the retriever to the registry and this deployment's models."""
        return ConversationalRetriever(
            stores=self._stores,
            embedder=self._models().embedder,
            llm=self._model_without_retries(),
            config=self._config.query,
            scoring=self._config.scoring,
        )

    def _models(self) -> IngestModels:
        """
        The models, borrowed from the importer where there is one.

        Sharing them is only an economy — they are stateless clients. The
        thing that genuinely could not be opened twice was the search index,
        and that is the store registry's problem now.
        """
        if self._worker is not None:
            return self._worker.ensure_ready()
        if self._owned is None:
            self._owned = build_models(self._config)
        return self._owned

    def _model_without_retries(self):
        """The fast model, told to answer once or not at all."""
        no_retries = replace(self._config.providers, max_attempts=1)
        return get_llm_provider(
            ModelRole.LIGHTWEIGHT, replace(self._config, providers=no_retries)
        )



class LazyEraser:
    """
    Holds the thing that forgets.

    Nothing lazy is left in it. It was built late because erasing needed the
    search index and opening the index needed a model configured; stores now
    come from the registry, which needs no model at all, so this exists only
    to keep the shape the routes already expect.
    """

    def __init__(self, *, config: AppConfig, stores, ops) -> None:
        self._config = config
        self._stores = stores
        self._ops = ops
        self._lock = threading.Lock()
        self._service = None

    def get(self):
        """The erasure service."""
        from lumen.erasure import ErasureService

        with self._lock:
            if self._service is None:
                self._service = ErasureService(
                    config=self._config, stores=self._stores, ops=self._ops
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
        stores=None,
    ) -> None:
        self._config = config
        self._search = search
        self._formulator = formulator
        self._composer = composer
        self._memory = memory
        self._sessions = sessions
        self._personas = personas
        self._stores = stores
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
                    alerts=self._alert_reader(),
                    config=self._config.chat,
                )
            return self._engine

    def _hit_recorder(self):
        """
        The thing that counts which records a turn used, where there is one.

        Nothing when no registry was handed in. Counting is a convenience,
        and a deployment that cannot write should still hold a conversation.
        """
        from lumen.query.frequency import QueryHitRecorder

        if self._stores is None:
            return None
        return QueryHitRecorder(self._stores, config=self._config.scoring)

    def _alert_reader(self):
        """
        The thing that notices somebody's beliefs moving, where there is one.

        Nothing without a registry, for the same reason as the counter: it
        reads a history, and which history depends on who is talking.
        """
        from lumen.query.alerts import ShadowAlertReader

        if self._stores is None:
            return None
        return ShadowAlertReader(self._stores, config=self._config.macro)

    def _alert_reader(self):
        """
        The thing that notices somebody's beliefs moving, where there is one.

        Nothing without a registry, for the same reason as the counter: it
        reads a history, and which history depends on who is talking.
        """
        from lumen.query.alerts import ShadowAlertReader

        if self._stores is None:
            return None
        return ShadowAlertReader(self._stores, config=self._config.macro)

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

