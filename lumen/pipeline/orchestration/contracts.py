"""
The shapes the orchestrator passes between its own parts.

Only three kinds of thing live here. What the search index is about to be
given, what a completed save actually did, and the ways a save can fail.

Everything the outside world sees — the run report and its per-episode
outcomes — lives with the other pipeline hand-offs instead, because callers
and the eventual web layer read those and should not have to import from
inside the orchestrator to do it.

The failures are separate types rather than one error with a flag, because
each one leaves the databases in a genuinely different state and the caller
has to treat them differently. Losing that distinction would mean treating
"nothing was written" and "everything was written but cannot be found" as
the same event.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IndexEntry(BaseModel):
    """
    One record, ready to be made findable by meaning.

    Built before anything is written. If the model that turns text into
    numbers is unavailable, that failure happens here, while the graph is
    still untouched and the whole episode can simply be tried again.

    Attributes:
        node_id: The record this describes. The same identifier is used in
            the graph and in the search index, which is what lets a search
            result be read back as a real record.
        node_type: What kind of record it is.
        text: The wording that was turned into numbers.
        vector: The numbers themselves.
        payload: A few plain facts stored alongside, so the search index can
            be inspected on its own without joining back to the graph.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    text: str = Field(min_length=1)
    vector: list[float]
    payload: dict[str, Any] = Field(default_factory=dict)


class CommitReport(BaseModel):
    """
    What one save actually did.

    Kept as lists of identifiers rather than counts because this is what
    gets written to the run log, and the log's whole purpose is to answer
    "where did this node come from" a year later. A count cannot answer
    that.

    Attributes:
        nodes_written: Every record created, in the order it was created.
        edges_written: Every link created, as (table, from, to).
        vectors_written: Every record that was made searchable.
        unindexed_node_ids: Records that reached the graph but whose search
            entry failed. Empty on a healthy save.
    """

    model_config = ConfigDict(extra="forbid")

    nodes_written: list[str] = Field(default_factory=list)
    edges_written: list[tuple[str, str, str]] = Field(default_factory=list)
    vectors_written: list[str] = Field(default_factory=list)
    unindexed_node_ids: list[str] = Field(default_factory=list)


class OrchestrationError(Exception):
    """Something went wrong while saving one episode."""


class EmbeddingFailed(OrchestrationError):
    """
    The records could not be turned into searchable numbers.

    Raised before any write happens, so nothing has been saved and the whole
    episode can be run again from the start with nothing to clean up.
    """


class GraphWriteFailed(OrchestrationError):
    """
    A write failed partway through, and everything in that save was undone.

    The graph is exactly as it was before the episode started.
    """


class IndexWriteFailed(OrchestrationError):
    """
    The graph half saved correctly, but some records never reached the
    search index.

    Deliberately not treated as the episode failing. What is in the graph is
    correct and complete; those particular records simply cannot be found by
    meaning yet.

    Carries the full report of what was saved, not just what went wrong.
    Everything that did land still has to be written to the run log — that
    log is what a later repair reads to work out which records are missing,
    so dropping it here would make the damage permanent.
    """

    def __init__(self, report: "CommitReport") -> None:
        self.report = report
        self.missing = list(report.unindexed_node_ids)
        super().__init__(
            f"{len(self.missing)} record(s) were saved but could not be indexed: "
            + ", ".join(self.missing)
        )


__all__ = [
    "IndexEntry",
    "CommitReport",
    "OrchestrationError",
    "EmbeddingFailed",
    "GraphWriteFailed",
    "IndexWriteFailed",
]
