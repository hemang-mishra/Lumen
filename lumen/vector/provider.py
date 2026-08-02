"""
VectorProvider Protocol — abstract interface for all vector database operations.

Every vector database implementation (Qdrant, ChromaDB, Weaviate) must satisfy
this protocol. Business logic NEVER imports vendor SDKs directly (HLD Rule 1).

See: docs/hld/Technical_HLD.md Section 2.3
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class VectorProvider(Protocol):
    """
    Abstract protocol defining the interface for the Vector Database.
    This encapsulates all vector storage and retrieval operations.
    """

    def init_collection(self) -> None:
        """Initialize the vector collection and payload indexes.
        
        Must be idempotent — calling twice must not raise.
        """
        ...

    def upsert(self, node_id: str, vector: list[float], payload: dict[str, Any]) -> None:
        """
        Upsert a dense vector with its associated payload.
        If node_id already exists, the vector and payload are replaced.
        """
        ...

    def hybrid_search(
        self,
        dense_vector: list[float],
        sparse_vector: dict[str, float] | None = None,
        limit: int = 10,
    ) -> list[str]:
        """
        Perform a hybrid search (dense + sparse BM25) and return node IDs.
        
        If sparse_vector is None or not supported by the implementation,
        falls back to dense-only search with a logged warning.
        """
        ...

    def close(self) -> None:
        """Release database resources."""
        ...
