"""
The shape the model answers in, before anything has been checked.

Nothing here leaves this package. It is deliberately loose — every field
optional, unknown fields ignored, names taken as plain strings — because it
describes what a model *said*, not what is true. A reply naming a person who
does not exist or an era nobody ever wrote down is a perfectly valid reply of
this shape; it simply will not survive the checks that come next.

Keeping the loose shape and the checked shape as two separate models is what
makes those checks a visible step rather than a pile of validators that
silently reject half a reply and leave no trace of having done so.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from lumen.schemas.enums import TriggerType

# The order reasons to search are kept in when a turn produces more of them
# than one turn is allowed to act on.
#
# Structural reasons come first — a person or a period or an unfinished
# question is an exact lookup, cheap and certain, while the rest are
# similarity searches that may or may not land on anything. Fixing the order
# here rather than at the point of cutting means the same turn always keeps
# the same reasons.
TRIGGER_PRECEDENCE: tuple[TriggerType, ...] = (
    TriggerType.NAMED_PERSON,
    TriggerType.HISTORICAL_ERA,
    TriggerType.OPEN_LOOP_MATCH,
    TriggerType.BELIEF_CHALLENGE,
    TriggerType.PATTERN_MENTION,
    TriggerType.IDENTITY_STATEMENT,
    TriggerType.PROGRESS_CLAIM,
    TriggerType.SOMATIC_MARKER,
)


def precedence_of(trigger_type: TriggerType) -> int:
    """
    Where a kind of reason sorts. Anything unlisted goes last.

    NO_TRIGGER is the only unlisted one, and it should never reach here — but
    sorting it to the end is a harmless answer, whereas raising would turn a
    stray value from the model into a failed turn.
    """
    try:
        return TRIGGER_PRECEDENCE.index(trigger_type)
    except ValueError:
        return len(TRIGGER_PRECEDENCE)


class RawTrigger(BaseModel):
    """
    One reason to search, exactly as the model gave it.

    Attributes:
        trigger_type: What kind of reason. Anything outside the known list
            makes the whole reason unusable and it is dropped.
        domain: The area of life, as a word the model chose.
        era: The period of the past, as the model spelled it.
        people: Who the turn named.
        keywords: A few words to search on.
    """

    model_config = ConfigDict(extra="ignore")

    trigger_type: str = ""
    domain: str | None = None
    era: str | None = None
    people: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class ClassifierReply(BaseModel):
    """
    A whole reading of one turn, exactly as the model gave it.

    Attributes:
        triggers: Every reason it found to search.
        named_entities: People it heard named anywhere in the turn, whether
            or not they became a reason on their own.
        emotional_register: How it thinks the person sounds.
        confidence: How sure it is, from 0 to 1.
        critical_domain_opened: An area of life it believes the person
            raised themselves on this turn.
    """

    model_config = ConfigDict(extra="ignore")

    triggers: list[RawTrigger] = Field(default_factory=list)
    named_entities: list[str] = Field(default_factory=list)
    emotional_register: str = ""
    confidence: float = 0.0
    critical_domain_opened: str | None = None


__all__ = [
    "TRIGGER_PRECEDENCE",
    "precedence_of",
    "RawTrigger",
    "ClassifierReply",
]
