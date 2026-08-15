"""
The shapes the web layer hands back.

Nothing raw from the store ever crosses this boundary. A record read out of
the graph arrives as the union of every column across every kind of record —
well over a hundred of them, almost all empty — with any list it held
written out as a run of text. Passing that straight through would make every
reader deal with the storage layer's shape, and would quietly tie the web
surface to the database in use.

What leaves here is what the record actually holds, plus one thing the store
cannot say on its own: whether the answer was cut short.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from lumen.graph.provider import EdgeRow, GraphSlice
from lumen.graph.queries import node_type_of, tidy_edge, tidy_row


class NodeView(BaseModel):
    """
    One record, as a reader sees it.

    Attributes:
        node_id: What it is called. The same identifier names it in the
            search index and in the run log.
        node_type: Which kind of record it is.
        properties: What it actually holds — empty columns dropped, lists
            read back as lists.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    properties: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def of(cls, row: dict[str, Any]) -> "NodeView":
        """Build one from a row as the store returned it."""
        tidied = tidy_row(row)
        return cls(
            node_id=str(tidied.get("node_id", "")),
            node_type=node_type_of(row),
            properties=tidied,
        )


class EdgeView(BaseModel):
    """
    One link between two records.

    Named by its two ends and its kind rather than by an identifier,
    because links do not have one. That triple is the only way to point at a
    particular link, and it is what reversing a decision has to be told.

    Attributes:
        edge_type: What the link means.
        from_node_id: Where it starts.
        to_node_id: Where it ends.
        valid_from: When it was made.
        invalidated_at: When it stopped applying, if it has.
        decision_id: The decision that made it, for links a decision made.
        confidence: How sure that decision was.
    """

    model_config = ConfigDict(extra="forbid")

    edge_type: str = Field(min_length=1)
    from_node_id: str = Field(min_length=1)
    to_node_id: str = Field(min_length=1)
    valid_from: str | None = None
    invalidated_at: str | None = None
    decision_id: str | None = None
    confidence: float | None = None

    @classmethod
    def of(cls, edge: EdgeRow) -> "EdgeView":
        """Build one from a link as the store returned it."""
        tidied = tidy_edge(
            edge.edge_type, edge.from_node_id, edge.to_node_id, edge.properties
        )
        return cls(
            edge_type=tidied["edge_type"],
            from_node_id=tidied["from_node_id"],
            to_node_id=tidied["to_node_id"],
            valid_from=_text(tidied.get("valid_from")),
            invalidated_at=_text(tidied.get("invalidated_at")),
            decision_id=_text(tidied.get("decision_id")),
            confidence=tidied.get("confidence"),
        )


class GraphSliceView(BaseModel):
    """
    A piece of the graph: records and the links among them.

    Attributes:
        nodes: The records in this piece.
        edges: The links between them.
        truncated: True when a limit cut the answer short. Without this, a
            piece that was cut and a piece that was genuinely that size look
            identical — and a partial graph drawn as a whole one is a wrong
            answer that looks right.
    """

    model_config = ConfigDict(extra="forbid")

    nodes: list[NodeView] = Field(default_factory=list)
    edges: list[EdgeView] = Field(default_factory=list)
    truncated: bool = False

    @classmethod
    def of(cls, slice_: GraphSlice) -> "GraphSliceView":
        """Build one from a piece of the graph as the store returned it."""
        return cls(
            nodes=[NodeView.of(row) for row in slice_.nodes],
            edges=[EdgeView.of(edge) for edge in slice_.edges],
            truncated=slice_.truncated,
        )


class NodeListView(BaseModel):
    """
    A page of records.

    Attributes:
        nodes: The records on this page.
        count: How many are on it.
        limit: How many were asked for.
        offset: How many were skipped to reach it.
    """

    model_config = ConfigDict(extra="forbid")

    nodes: list[NodeView] = Field(default_factory=list)
    count: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)

    @classmethod
    def of(
        cls, rows: list[dict[str, Any]], *, limit: int, offset: int
    ) -> "NodeListView":
        views = [NodeView.of(row) for row in rows]
        return cls(nodes=views, count=len(views), limit=limit, offset=offset)


class VersionChainView(BaseModel):
    """
    Every version of one belief or pattern, oldest first.

    Attributes:
        versions: The whole history, in order.
        current_version_id: The version that still applies.
        length: How many versions there have been.
    """

    model_config = ConfigDict(extra="forbid")

    versions: list[NodeView] = Field(default_factory=list)
    current_version_id: str | None = None
    length: int = Field(default=0, ge=0)

    @classmethod
    def of(cls, rows: list[dict[str, Any]]) -> "VersionChainView":
        views = [NodeView.of(row) for row in rows]
        return cls(
            versions=views,
            current_version_id=views[-1].node_id if views else None,
            length=len(views),
        )


class DecisionHistoryView(BaseModel):
    """
    Every decision recorded about one record, newest first.

    Attributes:
        node_id: The record the decisions were about.
        decisions: The notes of those decisions, each carrying what was
            compared, what was chosen, and what it would take to undo it.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    decisions: list[NodeView] = Field(default_factory=list)


class EpisodeDetailView(BaseModel):
    """
    One piece of writing and everything it produced.

    Attributes:
        episode: The piece of writing itself.
        contents: What came out of it, and the links between.
    """

    model_config = ConfigDict(extra="forbid")

    episode: NodeView
    contents: GraphSliceView


class GraphStatsView(BaseModel):
    """
    How much is in the graph.

    Counts everything, retired records included: "how much is in here" is a
    different question from "how much of it still applies", and this is the
    first one.

    Attributes:
        counts: How many of each kind of record.
        total: How many records in all.
    """

    model_config = ConfigDict(extra="forbid")

    counts: dict[str, int] = Field(default_factory=dict)
    total: int = Field(default=0, ge=0)

    @classmethod
    def of(cls, counts: dict[str, int]) -> "GraphStatsView":
        return cls(counts=counts, total=sum(counts.values()))


class ProvenanceView(BaseModel):
    """
    Where one record came from.

    The answer to the question every complaint about the graph starts with:
    the conversation it was written from, the run that wrote it, and the
    piece of writing within that conversation.

    Attributes:
        node_id: The record being explained.
        job_id: The run that wrote it.
        trace_id: That run's identifier in the logs.
        session_id: The conversation it came from.
        episode_id: The piece of writing within that conversation.
        written_at: When it was saved.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    episode_id: str = ""
    written_at: str | None = None


class HealthView(BaseModel):
    """
    Whether the service is up and whether its stores answer.

    Both are reported separately, because a running service that cannot
    reach its databases is a different problem from one that is down, and
    the two need different fixing.
    """

    model_config = ConfigDict(extra="forbid")

    status: str
    graph: bool
    operational: bool


def _text(value: Any) -> str | None:
    """A stored value as text, or nothing when it was never set."""
    return None if value is None else str(value)


__all__ = [
    "NodeView",
    "EdgeView",
    "GraphSliceView",
    "NodeListView",
    "VersionChainView",
    "DecisionHistoryView",
    "EpisodeDetailView",
    "GraphStatsView",
    "ProvenanceView",
    "HealthView",
]
