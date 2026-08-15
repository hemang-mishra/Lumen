"""
The shapes retrieval hands around internally.

Retrieval works on three kinds of node at once — the findings, the things
that happened, and the session in which the thinking happened — and after
the first step it stops mattering which is which. What matters is the text
to search with and the id the result belongs to. `SearchTarget` is that
reduction, and it is why the rest of the stage has no branching on node
type in it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from lumen.schemas.enums import SignalStrength


class SearchTarget(BaseModel):
    """
    One extracted node, reduced to what searching for it needs.

    Attributes:
        node_id: The node these results will belong to.
        node_type: Which kind it is, kept for the log line rather than for
            any decision.
        text: What the node says, used to write the hypothetical match.
        episode_id: The episode it came from, so a node is never offered
            itself as a candidate.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    text: str = Field(min_length=1)
    episode_id: str = Field(min_length=1)


class Hypothetical(BaseModel):
    """
    A made-up historical record that would be a perfect match for one
    extracted node.

    Never stored, never shown to anyone, and never treated as something the
    person said. It exists to be turned into a vector, because the thing
    being searched for and the thing that matches it are usually written
    completely differently — and a fabricated version of the answer sits
    much closer in that space than the question does.

    Attributes:
        index: Which target it belongs to, by position.
        text: The fabricated record.
    """

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1)
    text: str = ""


class HydeResponse(BaseModel):
    """What the model returns when asked for one hypothetical per node."""

    model_config = ConfigDict(extra="ignore")

    hypotheticals: list[Hypothetical] = Field(default_factory=list)


class HydeResult(BaseModel):
    """
    The search text for every target, in the order the targets arrived.

    Attributes:
        texts: One search text per target. Never shorter than the target
            list — a missing hypothetical is replaced with the node's own
            words rather than dropped, because a shifted list would search
            for each node using another node's text.
        used_fallback: True when the model call failed and every search
            text is the node's own content.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    texts: tuple[str, ...] = ()
    used_fallback: bool = False


class HydratedNode(BaseModel):
    """
    An existing graph node, read back as a possible match.

    Attributes:
        node_id: Its identifier.
        node_type: Which table it came from.
        preview: A short piece of its content, for display and for the
            reconciliation prompt.
        signal_strength: How much weight it carries, used for ranking.
        episode_id: Which episode produced it, where it has one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    preview: str = Field(min_length=1)
    signal_strength: SignalStrength = SignalStrength.STANDARD
    episode_id: str | None = None


__all__ = [
    "SearchTarget",
    "Hypothetical",
    "HydeResponse",
    "HydeResult",
    "HydratedNode",
]
