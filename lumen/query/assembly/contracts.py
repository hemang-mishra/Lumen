"""
The shapes the briefing hands around.

One line of history is a `ContextItem`: the record it came from, the
sentence it became, and what that sentence costs. The whole briefing is an
`AssembledContext`, which carries what was left out as well as what got in —
because the interesting question when a briefing disappoints is almost
always what did not make it and why.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from lumen.schemas.enums import EmotionalRegister, RetrievalPass


class ContextItem(BaseModel):
    """
    One piece of history, as the assistant will read it.

    Attributes:
        node_id: The record this came from, so a line in the briefing can
            always be traced back to something in the graph.
        node_type: What kind of record it is.
        text: The sentence it became.
        tokens: Roughly what that sentence costs.
        score: Where it ranked.
        found_by: Which search surfaced it.
        boosted: True when this was already part of today's conversation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    text: str = Field(min_length=1)
    tokens: int = Field(ge=0)
    score: float = Field(default=0.0, ge=0.0)
    found_by: RetrievalPass
    boosted: bool = False


class DroppedItem(BaseModel):
    """
    One piece of history that did not make it, and why.

    Kept because a briefing that disappoints is usually explained by what is
    missing rather than by what is there, and "it was fetched and then cut"
    is invisible otherwise.

    Attributes:
        node_id: The record left out.
        reason: Which rule left it out — the budget, the count, a duplicate,
            too many of one kind, or the register's own limits.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class AssembledContext(BaseModel):
    """
    Everything of the person's own history that this turn will carry.

    Attributes:
        items: The lines, best first.
        dropped: What was fetched and did not make it, with the rule that
            cut it.
        emotional_register: How the person sounds, which is what set the
            allowance.
        token_budget: What was allowed.
        estimated_tokens: What was used.
        suppressed: True when nothing is being offered because the person is
            in acute distress — as opposed to there being nothing to offer.
        deferred: True when this arrived too late for the turn it was fetched
            for and is being carried into the next one.
        search_failed: True when the history could not be looked up at all —
            as opposed to being looked up and coming back empty. Carried this
            far because those two produce the same empty briefing, and an
            assistant handed one with no way to tell them apart behaves as
            though the person has no history, which is the failure this whole
            layer exists to avoid.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ContextItem, ...] = ()
    dropped: tuple[DroppedItem, ...] = ()
    emotional_register: EmotionalRegister = EmotionalRegister.STABLE
    token_budget: int = Field(default=0, ge=0)
    estimated_tokens: int = Field(default=0, ge=0)
    suppressed: bool = False
    deferred: bool = False
    search_failed: bool = False

    @property
    def is_empty(self) -> bool:
        """Whether there is anything to put in front of the assistant."""
        return not self.items


__all__ = ["ContextItem", "DroppedItem", "AssembledContext"]
