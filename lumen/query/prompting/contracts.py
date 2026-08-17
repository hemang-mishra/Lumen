"""
What the assistant is actually sent.

One model, and it is the whole point of this goal: given a turn, this is
exactly what would go to the model — nothing hidden, nothing added later.
That is what makes the thing inspectable before any chat surface exists, and
what makes it testable without one.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from lumen.query.assembly.contracts import AssembledContext
from lumen.schemas.query import ChatTurn


class ChatPrompt(BaseModel):
    """
    Everything that goes to the model for one turn.

    Attributes:
        system: The instructions — who the assistant is, how to be, the
            briefing from the person's history, and where the conversation
            has got to.
        messages: The recent turns, oldest first, as they were said.
        context: The briefing that went into the instructions, with what was
            left out and why.
        summary: What the earlier part of the conversation was about, when
            there is an earlier part.
        estimated_tokens: Roughly what the whole thing costs.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    system: str = Field(min_length=1)
    messages: tuple[ChatTurn, ...] = ()
    context: AssembledContext = Field(default_factory=AssembledContext)
    summary: str | None = None
    estimated_tokens: int = Field(default=0, ge=0)


__all__ = ["ChatPrompt"]
