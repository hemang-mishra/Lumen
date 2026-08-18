"""
The shape of what the assistant remembers about the conversation itself.

Distinct from what it remembers about the *person* — that is the graph, and
it is years deep. This is the much shorter thing: what has been said today,
and what the earlier part of today was about.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from lumen.schemas.query import ChatTurn


class DaySummary(BaseModel):
    """
    What one earlier day's conversation was about.

    Journalling is not a series of unrelated days — somebody picks a thread
    back up on Thursday that they dropped on Monday. Today's conversation
    opens holding a few of these so the assistant is not meeting them fresh
    every morning.

    Attributes:
        on: The day this covers.
        summary: What was talked about, in a few sentences.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    on: date
    summary: str = Field(min_length=1)


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
        previous_days: What the last few days were about, oldest first. Only
            days that actually hold a conversation appear, so a week with two
            entries in it gives two.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str | None = None
    turns: tuple[ChatTurn, ...] = ()
    summarised_through: int = Field(default=0, ge=0)
    total_turns: int = Field(default=0, ge=0)
    previous_days: tuple[DaySummary, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Whether this conversation has anything in it yet."""
        return not self.turns and not self.summary and not self.previous_days


__all__ = ["Recollection", "DaySummary"]
