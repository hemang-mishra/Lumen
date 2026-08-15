"""
GraphProvider Protocol — abstract interface for all graph database operations.

Every graph database implementation (Kuzu, Neo4j, FalkorDB) must satisfy this
protocol. Business logic NEVER imports vendor SDKs directly (HLD Rule 1).

See: docs/hld/Technical_HLD.md Section 2.2
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from lumen.schemas.base import GraphNode


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

    def write_node(self, node_type: str, properties: "GraphNode | dict[str, Any]") -> str:
        """
        Write a node to the graph and return its node_id.

        `properties` may be a raw dict (must contain a 'node_id' key) or a
        Pydantic node model from lumen.schemas.nodes (serialized via its
        to_graph_dict() method). See implementation/Goal_2_Plan.md Section B8.
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

    # ------------------------------------------------------------------
    # Anchor lookups
    #
    # Three narrow, named reads rather than one general query method. A
    # general one would push query building out to callers and start
    # letting graph-shaped thinking leak into business logic, which is the
    # thing this Protocol exists to prevent. Each of these answers one
    # question that candidate retrieval actually asks.
    # ------------------------------------------------------------------

    def find_linked_to_person(
        self, canonical_name: str, *, node_types: list[str], limit: int = 10
    ) -> list[dict[str, Any]]:
        """
        Find active nodes that mention a particular person.

        Someone described across a year is described differently every
        time, so their name is a far more reliable way back to what was
        said about them than any measure of similarity.

        Returns an empty list when nobody by that name is known.
        """
        ...

    def find_by_era(
        self, era_tag: str, *, node_types: list[str], limit: int = 10
    ) -> list[dict[str, Any]]:
        """
        Find active nodes anchored to a named period of the person's past.

        When someone says "back during exam prep", everything already filed
        under that period is relevant regardless of what words they used
        this time.
        """
        ...

    def find_unresolved_high_signal(
        self, observation_types: list[str], *, limit: int = 10
    ) -> list[dict[str, Any]]:
        """
        Find weighty observations whose episode is still awaiting
        reconciliation.

        Reached through the episode rather than the observation, because it
        is the episode that records whether reconciliation is outstanding.

        This exists for the case similarity search handles worst: someone
        describing recovery uses none of the words they used describing the
        injury, so the two look unrelated by any measure of distance. This
        lookup does not care what either one says.
        """
        ...

    def close(self) -> None:
        """Release database resources (file locks, connections)."""
        ...
