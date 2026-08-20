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

    def get_vectors(self, node_ids: list[str]) -> dict[str, list[float]]:
        """
        Read back the stored vector for each of these nodes.

        Ids with nothing stored are simply absent from the answer, so a
        caller can tell "never indexed" from "indexed and far away" — those
        mean opposite things when deciding whether something is worth
        surfacing again.

        This exists so a node can be compared against something *without*
        searching for it. The live conversation layer keeps a short list of
        what it has already surfaced today and has to ask, each turn,
        whether any of it still applies. Embedding those nodes again every
        turn would pay a second time for a measurement the index already
        holds.
        """
        ...

    def delete(self, node_ids: list[str]) -> int:
        """
        Remove these nodes' vectors from the index.

        Needed because a vector is a reconstruction of the words it was made
        from. Rewriting a record's text and leaving its position in the index
        would leave the record still findable by everything it used to say,
        which is not forgetting anything.

        Ids with nothing stored are not an error. A record whose vector was
        never written is already in the state this is asked for. Returns how
        many vectors were actually removed, not how many were asked about —
        the number goes into a record of what an erasure did, and one that
        claims more than happened is worse than none.
        """
        ...

    def close(self) -> None:
        """Release database resources."""
        ...
