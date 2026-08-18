"""
GraphProvider Protocol — abstract interface for all graph database operations.

Every graph database implementation (Kuzu, Neo4j, FalkorDB) must satisfy this
protocol. Business logic NEVER imports vendor SDKs directly (HLD Rule 1).

See: docs/hld/Technical_HLD.md Section 2.2
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol

if TYPE_CHECKING:
    from lumen.schemas.base import GraphNode


class EdgeRow(NamedTuple):
    """
    One link, as it comes back from a read.

    Links have no identifier of their own in the store, so the table and the
    two ends together are the only way to name a particular one. That triple
    is what a rollback has to be told, and it is why this is three fields
    rather than an id.

    Attributes:
        edge_type: The table the link lives in, which is also what it means.
        from_node_id: Where it starts.
        to_node_id: Where it ends.
        properties: Its own columns — when it was made, which decision made
            it, and how sure that decision was.
    """

    edge_type: str
    from_node_id: str
    to_node_id: str
    properties: dict[str, Any]


class GraphSlice(NamedTuple):
    """
    A piece of the graph: some records and the links among them.

    Both halves are needed together or neither is useful. Records without
    their links are a list, and links without their records are a set of
    identifiers nobody can read.

    Attributes:
        nodes: The records in this piece.
        edges: The links between them.
        truncated: True when a limit cut the answer short. A piece of the
            graph that was cut and a piece that was genuinely that size look
            identical otherwise, and a partial graph presented as a whole one
            is a wrong answer that looks right.
    """

    nodes: list[dict[str, Any]]
    edges: list[EdgeRow]
    truncated: bool = False


class ReadOnlyGraph(Protocol):
    """
    Everything that can be asked of the graph, and nothing that changes it.

    Exists so that a component with no business writing can be handed
    something that structurally cannot. The web layer takes one of these, so
    a write is not merely discouraged there — the method is not on the type
    it was given.

    Every read is a named question rather than a way to run any query at
    all. A general one would let graph-shaped thinking spread into whatever
    called it, and would quietly end the promise that the store behind this
    can be replaced.
    """

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Retrieve a node's properties by its ID. Returns None if not found."""
        ...

    def get_nodes_by_ids(self, node_ids: list[str]) -> list[dict[str, Any]]:
        """Retrieve several nodes at once, skipping any that do not exist."""
        ...

    def find_nodes(
        self,
        node_types: list[str],
        *,
        since: datetime | date | None = None,
        until: datetime | date | None = None,
        domain: str | None = None,
        signal_strength: str | None = None,
        era_tag: str | None = None,
        active_only: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        List records of the given kinds, narrowed by whatever was asked for.

        A filter a kind of record cannot answer is skipped for that kind
        rather than failing the whole request — an observation records no
        part of life, so there is no such thing as an observation about
        work, and a caller should not have to know that to ask.
        """
        ...

    def count_by_type(self) -> dict[str, int]:
        """How many records of each kind exist. Counts everything, live or not."""
        ...

    def get_neighborhood(
        self,
        node_id: str,
        *,
        depth: int = 1,
        edge_types: list[str] | None = None,
        direction: str = "both",
        as_of: datetime | date | None = None,
        include_invalidated: bool = False,
        limit: int = 200,
    ) -> GraphSlice:
        """
        Everything within a few steps of one record, and the links between.

        The starting record is always included, so a record with nothing
        attached comes back as itself rather than as nothing at all.

        Withdrawn links are not followed unless asked for. A decision that
        was rolled back should not still be shaping what the graph appears
        to say.
        """
        ...

    def get_version_chain(self, node_id: str) -> list[dict[str, Any]]:
        """
        Every version of a belief or pattern, oldest first.

        Reached from any version, not only the newest or the first: someone
        looking at a record found by search has no idea where in its own
        history they have landed.

        A record with no versions before or after it comes back as a chain
        of one. Anything that is not versioned at all comes back empty.
        """
        ...

    def get_decision_history(self, node_id: str) -> list[dict[str, Any]]:
        """
        Every decision ever recorded about one record, newest first.

        This is the answer to "where did this come from" — what was compared,
        what was decided, how sure the model was, and what it would take to
        undo it.
        """
        ...

    def get_episode_contents(self, episode_id: str) -> GraphSlice:
        """
        Everything one piece of writing produced, and the links between.

        Includes the findings that could not be read, since an episode that
        yielded nothing and one whose reading failed look the same otherwise.
        """
        ...

    def get_causal_chain(self, chain_id: str) -> list[dict[str, Any]]:
        """One cause-and-effect sequence's steps, in the order they happened."""
        ...

    def find_linked_to_person(
        self, canonical_name: str, *, node_types: list[str], limit: int = 10
    ) -> list[dict[str, Any]]:
        """Find active nodes that mention a particular person."""
        ...

    def find_by_era(
        self, era_tag: str, *, node_types: list[str], limit: int = 10
    ) -> list[dict[str, Any]]:
        """Find active nodes anchored to a named period of the person's past."""
        ...

    def list_era_tags(self, *, limit: int = 50) -> list[str]:
        """
        Every named period of the past that some record is anchored to.

        The stored spelling is what comes back, most used first. Nothing
        anywhere constrains what these names look like — they are whatever
        was written when the record was made — so anything asking about an
        era has to be told the real ones rather than allowed to guess.
        """
        ...

    def find_unresolved_high_signal(
        self, observation_types: list[str], *, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Find weighty observations whose episode is still awaiting reconciliation."""
        ...

    def count_prior_decisions(
        self, target_node_id: str, *, actions: list[str]
    ) -> int:
        """Count how many past decisions of the given kinds were made about a node."""
        ...

    def find_episodes_by_event_date(
        self, start: date, end: date, *, limit: int = 500, offset: int = 0
    ) -> list[dict[str, Any]]:
        """
        Every piece of writing about a stretch of time, oldest first.

        Selected by the day the writing is *about*, not the day it was
        written. An entry made on the third of June describing the
        twenty-eighth of May belongs to May, and anything summarising a month
        of somebody's life has to agree with that or it is summarising their
        typing habits instead.

        The end of the range is not included, so consecutive periods share no
        episode and none falls between two of them.
        """
        ...

    def find_standing_edges(
        self,
        node_ids: list[str],
        *,
        edge_names: list[str] | None = None,
        include_invalidated: bool = False,
    ) -> list["EdgeRow"]:
        """
        Every link leading out of a batch of records at once.

        The batched form exists because the alternative is one walk per
        record. Asking what a month's worth of findings turned into means
        several hundred of them, and several hundred round trips to answer
        one question is the difference between a report that runs and a
        report that times out.

        Withdrawn links are left out unless asked for, on the same reasoning
        as everywhere else: a decision that was rolled back should not still
        be shaping what the graph appears to say.
        """
        ...

    def find_reports(
        self,
        *,
        report_type: str | None = None,
        period_start: datetime | date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Periodic reports already written, newest first.

        Narrowing by both kind and period start is how anything checks
        whether a given week or month has been covered already. That check is
        what makes running the same period twice cost nothing, which in turn
        is what makes a schedule safe to fire more than once.
        """
        ...


class GraphProvider(ReadOnlyGraph, Protocol):
    """
    Abstract protocol defining the interface for the Graph Database.
    This ensures we can swap Kuzu for Neo4j seamlessly as the project scales.

    Everything a reader can do, plus the writes. Anything that only reads
    should ask for `ReadOnlyGraph` instead, so that what it cannot do is
    visible in its signature rather than left to discipline.
    """

    def init_schema(self) -> None:
        """Initialize the graph database schema if it doesn't exist.

        Must be idempotent — calling twice on an already-initialized
        database must not raise.
        """
        ...

    def transaction(self) -> AbstractContextManager[None]:
        """
        Group several writes so that they all land or none of them do.

        Saving one journal entry means writing many nodes and links that
        only make sense together. Without this, a failure partway through
        leaves an entry that looks complete to anything reading the graph
        but is missing half of what it said.

        Not reusable inside itself. Nesting one of these inside another
        would quietly widen the group of writes being protected to
        something the caller never chose, so a nested call raises instead.
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

    # ------------------------------------------------------------------
    # Bookkeeping writes
    #
    # The three operations below are the only writes in the whole system
    # that touch a record which already exists. Each one changes a fixed,
    # hard-coded set of columns that hold counts, timestamps and status —
    # never a word the person wrote. There is no way to pass a column name
    # in, so there is no way for a caller to reach content through them.
    # ------------------------------------------------------------------

    def mark_superseded(self, node_id: str, *, at: datetime) -> None:
        """
        Mark a belief or pattern as no longer the current version.

        Its text is untouched and it stays in the graph forever. All this
        says is that a newer version of the same idea now exists.
        """
        ...

    def record_reinforcement(self, node_id: str, *, at: datetime) -> None:
        """
        Note that a belief or pattern has been evidenced once more.

        Bumps the evidence count and the date it was last seen, which is
        what lets a well-supported pattern outrank a passing thought.
        """
        ...

    def touch_person(self, node_id: str, *, at: datetime) -> None:
        """Note that a person was mentioned again, and when."""
        ...

    def close(self) -> None:
        """Release database resources (file locks, connections)."""
        ...
