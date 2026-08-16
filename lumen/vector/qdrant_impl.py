"""
QdrantVectorProvider — Qdrant implementation of the VectorProvider Protocol.

Qdrant's killer feature for Lumen: native sparse + dense hybrid search in
a single query. The in-process mode means zero server for the personal version.

See: docs/hld/Technical_HLD.md Section 2.3
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import qdrant_client
from qdrant_client.models import Distance, PointStruct, VectorParams

from lumen.vector.provider import ScoredHit, VectorProvider

logger = logging.getLogger(__name__)

# Held in memory and gone when the process exits. Right for tests, and the
# wrong default for anything that also keeps a graph on disk.
IN_MEMORY = ":memory:"


def _connection_for(location: str) -> dict[str, str]:
    """
    Work out what a location string was meant to say.

    This exists because the underlying client's `location` argument is a
    *host*, not a path: handed "./lumen_vectors" it tries to resolve that as
    a server name and fails with a DNS error, which is a spectacularly
    unhelpful way to be told you wanted a folder. A path is the most likely
    thing somebody configuring a personal, file-backed deployment means, so
    it is recognised and passed as one.

    Args:
        location: ":memory:", a URL, or a folder path.

    Returns:
        The keyword argument the client actually wants.
    """
    if location == IN_MEMORY:
        return {"location": IN_MEMORY}
    if location.startswith(("http://", "https://")):
        return {"url": location}
    return {"path": location}


class QdrantVectorProvider(VectorProvider):
    """
    Qdrant implementation of the VectorProvider Protocol.

    Usage:
        provider = QdrantVectorProvider(location=":memory:", collection_name="lumen_nodes")
        provider.init_collection()
        provider.upsert("obs_001", [0.1, 0.2, ...], {"node_type": "ObservationNode"})
        provider.close()

    Or as a context manager:
        with QdrantVectorProvider() as provider:
            provider.init_collection()
            ...
    """

    def __init__(
        self,
        location: str = ":memory:",
        collection_name: str = "lumen_nodes",
        vector_size: int = 768,
    ) -> None:
        """
        Initialize the Qdrant client.

        Args:
            location: Where the index lives. Three forms are understood, and
                which one was meant is worked out from the value itself:

                  ":memory:"                — held in memory, lost on exit
                  "http://host:6333"        — a running Qdrant server
                  anything else             — a folder on disk

            collection_name: Name of the Qdrant collection.
            vector_size: Dimensionality of dense vectors (768 for text-embedding-004).
        """
        self.location = location
        self.collection_name = collection_name
        self.vector_size = vector_size
        self.client = qdrant_client.QdrantClient(**_connection_for(location))
        logger.info(
            "QdrantVectorProvider initialized (location=%s, collection=%s, dim=%d)",
            location, collection_name, vector_size,
        )

    def __enter__(self) -> QdrantVectorProvider:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        """Release Qdrant client resources."""
        if hasattr(self, "client") and self.client is not None:
            self.client.close()
            self.client = None  # type: ignore[assignment]
        logger.info("QdrantVectorProvider closed for %s", self.location)

    # ------------------------------------------------------------------
    # Collection Initialization
    # ------------------------------------------------------------------

    def init_collection(self) -> None:
        """Initialize the vector collection if it does not exist. Idempotent."""
        collections = self.client.get_collections().collections
        exists = any(c.name == self.collection_name for c in collections)

        if not exists:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=self.vector_size,
                    distance=Distance.COSINE,
                ),
                # Sparse vectors for BM25 are deferred to a future goal.
                # When enabled, add sparse_vectors_config here.
            )
            logger.info(
                "Created collection '%s' (dim=%d, distance=Cosine)",
                self.collection_name, self.vector_size,
            )

            # Payload Indexes — no-op in local/memory mode but required for
            # production Qdrant to enable fast filtered retrieval.
            for field_name in ("node_type", "status", "signal_strength"):
                try:
                    self.client.create_payload_index(
                        collection_name=self.collection_name,
                        field_name=field_name,
                        field_schema="keyword",
                    )
                except Exception:
                    # Local Qdrant warns but doesn't support payload indexes
                    pass
        else:
            self._refuse_a_collection_of_the_wrong_width()
            logger.debug("Collection '%s' already exists, skipping creation", self.collection_name)

    def _refuse_a_collection_of_the_wrong_width(self) -> None:
        """
        Stop if the existing collection was built for a different model.

        Changing the embedding model changes how wide its vectors are, and a
        collection's width is fixed when it is created. Without this check the
        mismatch surfaces once per record, deep inside a run, as an array
        error that names neither the model nor the setting — while the graph
        keeps saving records that nothing will ever find. Better to refuse
        here, where the message can say which two numbers disagree.
        """
        existing = self.client.get_collection(self.collection_name)
        params = existing.config.params.vectors
        size = getattr(params, "size", None)
        if size is None or size == self.vector_size:
            return

        raise ValueError(
            f"the collection {self.collection_name!r} holds vectors of width "
            f"{size} and this deployment is configured for {self.vector_size}. "
            "That happens when the embedding model changes. Either put "
            f"LUMEN_VECTOR_SIZE back to {size} with the model that produced "
            "it, or delete the collection and let it be rebuilt — which means "
            "re-embedding everything already saved."
        )

    # ------------------------------------------------------------------
    # Write Operations
    # ------------------------------------------------------------------

    def upsert(self, node_id: str, vector: list[float], payload: dict[str, Any]) -> None:
        """
        Upsert a dense vector with its associated payload.
        Uses a deterministic UUID5 derived from node_id to ensure idempotent upserts.
        """
        qdrant_id = str(uuid.uuid5(uuid.NAMESPACE_OID, node_id))

        # Always store node_id in payload for reverse lookup
        payload["node_id"] = node_id

        point = PointStruct(
            id=qdrant_id,
            vector=vector,
            payload=payload,
        )

        self.client.upsert(
            collection_name=self.collection_name,
            points=[point],
        )
        logger.debug("Upserted vector for node_id=%s (qdrant_id=%s)", node_id, qdrant_id)

    # ------------------------------------------------------------------
    # Search Operations
    # ------------------------------------------------------------------

    def hybrid_search(
        self,
        dense_vector: list[float],
        sparse_vector: dict[str, float] | None = None,
        limit: int = 10,
    ) -> list[ScoredHit]:
        """
        Search for the closest stored vectors, best match first.

        Each match carries its similarity score, which is what callers rank
        on — a node that is nearly identical to the search text and one that
        is merely in the same territory are different answers, and only the
        score tells them apart.

        Sparse/BM25 search is deferred — when sparse_vector is provided,
        a warning is logged and only dense search is performed.
        """
        if sparse_vector:
            logger.warning(
                "sparse_vector provided but sparse search is not yet implemented. "
                "Falling back to dense-only search."
            )

        search_result = self.client.query_points(
            collection_name=self.collection_name,
            query=dense_vector,
            limit=limit,
        ).points

        return [
            ScoredHit(node_id=hit.payload["node_id"], score=hit.score)
            for hit in search_result
            if hit.payload and "node_id" in hit.payload
        ]
