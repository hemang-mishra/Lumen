"""
Running imported conversations through the pipeline, one at a time.

This is the only thing in the ingest package that can change the graph, and
it is deliberately the narrowest part of it. A route hands it an identifier
and learns nothing else; everything that can write lives behind this object.

**One thread, on purpose.** The graph is an embedded database reached through
a single connection, so two imports running side by side would be two
transactions on one connection. It is also not a throughput problem worth
solving: importing is measured in model calls, and a person watching their
own history load would rather it arrive in order than in parallel.

**A request never waits for a run.** One conversation is several model calls
and takes minutes. The upload is answered as soon as the messages are
stored, and what happens afterwards is followed through the import record.

**Nothing here is allowed to kill the thread.** A conversation that fails is
recorded as failed and the next one starts. A worker that died on the first
bad episode would leave every later upload queued forever with nothing
saying why.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass

from lumen.config import AppConfig
from lumen.graph.provider import GraphProvider
from lumen.operational.enums import ImportStatus
from lumen.operational.repositories import OperationalStore
from lumen.pipeline.orchestration import run_pipeline
from lumen.providers.factory import get_embedding_provider, get_llm_provider
from lumen.providers.protocols import EmbeddingProvider, LLMProvider
from lumen.schemas.enums import ModelRole
from lumen.schemas.pipeline import RunReport
from lumen.vector.provider import VectorProvider
from lumen.vector.qdrant_impl import QdrantVectorProvider

logger = logging.getLogger(__name__)

# Told to stop.
_STOP = object()


@dataclass
class IngestResources:
    """
    Everything one pipeline run needs that is not the conversation itself.

    Gathered into one object because they share a lifetime — they are built
    together on the first import and closed together when the service stops
    — and because handing a test one of these is how the whole worker gets
    exercised without a model or a vector store anywhere in sight.

    Attributes:
        graph: The knowledge graph, writable. This is the same object the
            read routes hold; see the note in `build_resources`.
        vectors: The search index.
        embedder: Turns text into vectors.
        lightweight: The fast model, for cleaning and reading.
        thinking: The careful model, for deciding.
    """

    graph: GraphProvider
    vectors: VectorProvider
    embedder: EmbeddingProvider
    lightweight: LLMProvider
    thinking: LLMProvider

    def close(self) -> None:
        """
        Release what this object opened.

        The graph is not closed here. It was borrowed rather than opened —
        whoever passed it in is still using it and is responsible for
        shutting it down.
        """
        self.vectors.close()


def build_resources(config: AppConfig, graph: GraphProvider) -> IngestResources:
    """
    Open everything a run needs, except the graph, which is borrowed.

    The graph is passed in rather than opened because it cannot be opened
    twice. Kuzu is embedded and takes a file lock, so a second provider on
    the same path inside the same process would be refused. The service
    opens exactly one and shares it; the provider serialises access
    internally, so the routes reading from it and this thread writing to it
    do not collide.

    Raises:
        ProviderError: No model is configured, or the configured one cannot
            be reached. Left to propagate, because an import that cannot
            reach a model has not failed halfway — it has not started, and
            the caller should be told that plainly.
    """
    vectors = QdrantVectorProvider(
        location=config.vector.location,
        collection_name=config.vector.collection_name,
        vector_size=config.vector.vector_size,
    )
    vectors.init_collection()

    return IngestResources(
        graph=graph,
        vectors=vectors,
        embedder=get_embedding_provider(config),
        lightweight=get_llm_provider(ModelRole.LIGHTWEIGHT, config),
        thinking=get_llm_provider(ModelRole.THINKING, config),
    )


class IngestWorker:
    """
    A queue of imports and one thread working through them.

    Built but not started, so a test can drive it a job at a time with
    `run_once` and never touch a thread at all.
    """

    def __init__(
        self,
        *,
        config: AppConfig,
        ops: OperationalStore,
        graph: GraphProvider,
        resources: IngestResources | None = None,
    ) -> None:
        """
        Args:
            config: Settings for the run.
            ops: Where imports and job history are recorded.
            graph: The shared, writable graph.
            resources: Everything else a run needs. Supplied directly by
                tests; built on first use otherwise, so that a service with
                no model configured still starts and still serves every
                route that does not need one.
        """
        self._config = config
        self._ops = ops
        self._graph = graph
        self._resources = resources
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Readiness
    # ------------------------------------------------------------------

    def ensure_ready(self) -> IngestResources:
        """
        Make sure a run could actually happen, and say so if it could not.

        Called before anything is staged, so that "no model is configured"
        is answered as a refusal to accept the upload rather than discovered
        four minutes later as a failed import. Building the providers is the
        check — there is no cheaper question that means the same thing.

        Raises:
            ProviderError: No usable model or embedder is configured.
        """
        with self._lock:
            if self._resources is None:
                self._resources = build_resources(self._config, self._graph)
                logger.info("ingest resources opened")
            return self._resources

    # ------------------------------------------------------------------
    # The queue
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Begin working through the queue. Safe to call twice."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._drain, name="lumen-ingest", daemon=True
        )
        self._thread.start()
        logger.info("ingest worker started")

    def submit(self, import_id: str) -> None:
        """Queue one import. Returns immediately."""
        self._queue.put(import_id)
        logger.info("import queued", extra={"import_id": import_id})

    def stop(self, timeout: float = 30.0) -> None:
        """
        Finish the current import, abandon the rest, and close what was
        opened.

        The one in flight is allowed to finish because it is partway through
        writing somebody's history, and the transaction protecting it is
        worth more than a fast shutdown.
        """
        if self._thread is not None and self._thread.is_alive():
            self._queue.put(_STOP)
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("ingest worker did not stop in time")
        self._thread = None

        with self._lock:
            if self._resources is not None:
                self._resources.close()
                self._resources = None
        logger.info("ingest worker stopped")

    @property
    def pending(self) -> int:
        """How many imports are waiting. Approximate, and only for display."""
        return self._queue.qsize()

    def _drain(self) -> None:
        """Take imports off the queue until told to stop."""
        while True:
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                self.run_once(str(item))
            finally:
                self._queue.task_done()

    # ------------------------------------------------------------------
    # One import
    # ------------------------------------------------------------------

    def run_once(self, import_id: str) -> None:
        """
        Take one staged conversation all the way through the pipeline.

        Never raises. Every outcome — success, a failed run, a conversation
        that vanished — ends as a status on the import record, because that
        record is the only thing anybody is watching.
        """
        record = self._ops.imports.get(import_id)
        if record is None:
            logger.warning("asked to run an import that does not exist", extra={"import_id": import_id})
            return
        if not record.session_id:
            self._fail(import_id, "this import has no stored conversation to run")
            return

        self._ops.imports.update_status(import_id, ImportStatus.RUNNING)

        try:
            resources = self.ensure_ready()
            event = self._ops.buffers.build_decay_event(record.session_id)
            report = run_pipeline(
                event,
                graph=resources.graph,
                vectors=resources.vectors,
                embedder=resources.embedder,
                lightweight=resources.lightweight,
                thinking=resources.thinking,
                ops=self._ops,
                config=self._config,
            )
        except Exception as exc:
            # Deliberately broad. Anything at all that goes wrong belongs on
            # the import record where somebody will see it, and nothing is
            # worth taking the worker down for.
            logger.exception("import failed", extra={"import_id": import_id})
            self._fail(import_id, f"{type(exc).__name__}: {exc}")
            return

        job = self._ops.jobs.get_job(report.job_id)
        finished = (
            ImportStatus.COMPLETE if report.job_status == "COMPLETE" else ImportStatus.FAILED
        )
        self._ops.imports.update_status(
            import_id,
            finished,
            job_id=report.job_id,
            trace_id=job.trace_id if job else None,
            error=None if finished is ImportStatus.COMPLETE else _why(report),
        )
        logger.info(
            "import finished",
            extra={
                "import_id": import_id,
                "job_status": report.job_status,
                "episodes": len(report.episodes),
                "nodes_written": report.nodes_written,
            },
        )

    def _fail(self, import_id: str, reason: str) -> None:
        """Record that an import will not be finishing, and why."""
        self._ops.imports.update_status(import_id, ImportStatus.FAILED, error=reason)

    def __enter__(self) -> IngestWorker:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


def _why(report: RunReport) -> str:
    """
    A short account of what went wrong in a run that did not complete.

    Built from the episodes rather than from the job's own status, because
    "FAILED" tells whoever uploaded the file nothing they can act on and the
    episode outcomes usually name the actual problem.
    """
    reasons = [episode.error for episode in report.episodes if episode.error]
    if not reasons:
        return f"the run ended as {report.job_status}"
    return "; ".join(reasons[:3])


__all__ = ["IngestWorker", "IngestResources", "build_resources"]
