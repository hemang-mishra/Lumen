"""
GraphProvider Protocol — abstract interface for all graph database operations.

Every graph database implementation (Kuzu, Neo4j, FalkorDB) must satisfy this
protocol. Business logic NEVER imports vendor SDKs directly (HLD Rule 1).

See: docs/hld/Technical_HLD.md Section 2.2
"""

from __future__ import annotations

from typing import Any, Protocol


class GraphProvider(Protocol):
    """
    Abstract protocol defining the interface for the Graph Database.
    This ensures we can swap Kuzu for Neo4j seamlessly as the project scales.
    """

    def init_schema(self) -> None:
        """Initialize the graph database schema if it doesn't exist.
        
        Must be idempotent — calling twice on an already-initialized
        database must not raise.
        """
        ...

    def write_node(self, node_type: str, properties: dict[str, Any]) -> str:
        """
        Write a node to the graph and return its node_id.
        The properties dictionary must contain a 'node_id' key.
        """
        ...

    def write_edge(
        self,
        edge_type: str,
        from_id: str,
        to_id: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """
        Write a directed edge between two existing nodes.
        
        The implementation must resolve the correct FROM/TO node table types
        internally from the edge_type, avoiding Cartesian product scans.
        """
        ...

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Retrieve a node's properties by its ID. Returns None if not found."""
        ...

    def get_nodes_by_ids(self, node_ids: list[str]) -> list[dict[str, Any]]:
        """
        Retrieve multiple nodes by their IDs.
        Required by the HLD read path (Section 4.2):
            candidate_ids = vector_store.hybrid_search(...)
            candidates = graph_store.get_nodes_by_ids(candidate_ids)
        """
        ...

    def close(self) -> None:
        """Release database resources (file locks, connections)."""
        ...
