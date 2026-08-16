"""
The shapes the live-conversation side of Lumen hands around.

The extraction pipeline turns what somebody wrote into a graph. This is the
other direction: somebody is talking right now, and the question on every
turn is whether anything already in that graph is worth putting in front of
the AI answering them.

Three models cover it. A turn is one message. A trigger is one reason to go
looking. A signal is everything decided about a single turn, and it is the
only thing that leaves this layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lumen.schemas.enums import Domain, EmotionalRegister, FormulationPath, TriggerType


class ChatTurn(BaseModel):
    """
    One message in a live conversation.

    Ordering comes from turn_index rather than from the timestamp, for the
    same reason the stored conversations order by sequence: two messages can
    share a clock reading, and the order they were said in still has to
    survive that.

    Attributes:
        turn_index: Position in the day's conversation, counting from zero.
        role: Who said it.
        content: What was said.
        timestamp: When it was said.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    turn_index: int = Field(ge=0)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)
    timestamp: datetime


class RetrievalTrigger(BaseModel):
    """
    One reason this turn is worth searching the graph for.

    Everything on it that names something — the domain, the era, the people —
    has already been checked against the graph by the time a caller sees it.
    A trigger naming a person who was never recorded, or a period of life the
    graph has no notion of, is dropped rather than passed on: the search it
    would cause has no possible answer, and it would spend the turn's whole
    time budget discovering that.

    Attributes:
        trigger_type: Which kind of reason this is.
        domain: The area of life it concerns, when the reason has one.
        era: The named period of the past, spelled the way the graph spells
            it rather than the way the turn or the model did.
        person_node_ids: Records of the people involved, already resolved.
        keywords: A few words from the turn, for the search to expand on.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger_type: TriggerType
    domain: Domain | None = None
    era: str | None = None
    person_node_ids: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()


class RetrievalSignal(BaseModel):
    """
    Everything decided about one turn.

    An empty trigger list is how "nothing to look up" is said. NO_TRIGGER
    exists as a word the model can answer with and as something to write in a
    log, but it never appears in the list itself — a list holding a reason
    not to search would be a shape two parts of the system could disagree
    about.

    Attributes:
        session_id: The day's conversation this turn belongs to.
        turn_index: Which turn was read.
        retrieval_triggers: Every surviving reason to search, best first.
        named_entities_mentioned: People the turn named, as the turn named
            them. Kept even when they matched no record, because "the model
            heard a name the graph has never seen" is worth being able to
            see.
        emotional_register: How the person sounds right now.
        query_formulation_confidence: How sure the reading is, from 0 to 1.
        critical_domain_opened: An area of life the person opened themselves
            on this turn, if any. Opening one is what makes the most
            sensitive records in it available for the rest of the day.
        unlocked_domains: Every area opened so far today, this turn included.
        formulation_path: How this reading was arrived at.
        latency_ms: How long producing it actually took.
        suppressed_by_crisis: True when reasons to search were found and then
            deliberately thrown away because of how the person sounds. The
            difference between this and finding nothing matters: one means
            the graph has nothing, the other means now is not the time.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1)
    turn_index: int = Field(ge=0)
    retrieval_triggers: tuple[RetrievalTrigger, ...] = ()
    named_entities_mentioned: tuple[str, ...] = ()
    emotional_register: EmotionalRegister = EmotionalRegister.STABLE
    query_formulation_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    critical_domain_opened: Domain | None = None
    unlocked_domains: tuple[Domain, ...] = ()
    formulation_path: FormulationPath = FormulationPath.CLASSIFIED
    latency_ms: int = Field(default=0, ge=0)
    suppressed_by_crisis: bool = False

    @property
    def should_retrieve(self) -> bool:
        """
        Whether anything should be searched for.

        Worked out from the trigger list rather than stored alongside it, so
        the two can never say different things.
        """
        return bool(self.retrieval_triggers)

    @property
    def trigger_types(self) -> tuple[TriggerType, ...]:
        """The kinds of reason found, for logging and for tests."""
        return tuple(trigger.trigger_type for trigger in self.retrieval_triggers)


__all__ = ["ChatTurn", "RetrievalTrigger", "RetrievalSignal"]
