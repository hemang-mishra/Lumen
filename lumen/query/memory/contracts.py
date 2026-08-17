"""
The shape of what the assistant remembers about the conversation itself.

Distinct from what it remembers about the *person* — that is the graph, and
it is years deep. This is the much shorter thing: what has been said today,
and what the earlier part of today was about.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from lumen.schemas.query import ChatTurn


class Recollection(BaseModel):
    """
    The conversation as the assistant will see it.

    Two parts, because they are read differently. The recent turns are the
    person's actual words and are worth every token they cost. The summary is
    a compression of everything before those, and exists so that a long
    conversation costs the same as a short one while still hanging together.

    Attributes:
        summary: What the earlier part of the conversation was about, if
            there is an earlier part that has been summarised.
        turns: The recent turns, oldest first, exactly as they were said.
        summarised_through: How far into the conversation the summary
            reaches, as an arrival number. What comes after it is in `turns`.
        total_turns: How many turns the conversation holds in total, so the
            gap between that and what is being sent is visible.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str | None = None
    turns: tuple[ChatTurn, ...] = ()
    summarised_through: int = Field(default=0, ge=0)
    total_turns: int = Field(default=0, ge=0)

    @property
    def is_empty(self) -> bool:
        """Whether this conversation has anything in it yet."""
        return not self.turns and not self.summary


__all__ = ["Recollection"]
