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
            location: ":memory:" for testing, or a path like "./lumen_vectors" for persistence.
            collection_name: Name of the Qdrant collection.
            vector_size: Dimensionality of dense vectors (768 for text-embedding-004).
        """
        self.location = location
        self.collection_name = collection_name
        self.vector_size = vector_size
        self.client = qdrant_client.QdrantClient(location=location)
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
            logger.debug("Collection '%s' already exists, skipping creation", self.collection_name)

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
