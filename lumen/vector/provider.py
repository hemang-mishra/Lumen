"""
VectorProvider Protocol — abstract interface for all vector database operations.

Every vector database implementation (Qdrant, ChromaDB, Weaviate) must satisfy
this protocol. Business logic NEVER imports vendor SDKs directly (HLD Rule 1).

See: docs/hld/Technical_HLD.md Section 2.3
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple, Protocol

logger = logging.getLogger(__name__)


class ScoredHit(NamedTuple):
    """
    One search result: which node matched, and how closely.

    The score travels with the id because it is most of what a caller
    needs. Deciding whether a new observation is the same thing as an old
    one, or merely adjacent to it, is a question about distance — an
    ordered list of ids alone cannot answer it.

    Attributes:
        node_id: The matching node.
        score: Cosine similarity, from 0.0 to 1.0. Higher is closer.
    """

    node_id: str
    score: float


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
    ) -> list[ScoredHit]:
        """
        Search for the closest stored vectors, best match first.

        Returns each match with its similarity score. Callers rank and
        filter on those scores, so an implementation must not discard them.

        If sparse_vector is None or not supported by the implementation,
        falls back to dense-only search with a logged warning.
        """
        ...

    def close(self) -> None:
        """Release database resources."""
        ...
