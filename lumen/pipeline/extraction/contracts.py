"""
The shapes extraction hands around internally.

Two kinds of model live here, and the difference between them is the point
of the file.

The *response* models describe what the language model is asked to return.
Every field on them that will eventually become a fixed category is typed
as plain text, and every field has a default. That looks careless and is
deliberate: if the type of a finding were declared as the real category
here, one invented category name would fail the whole reply and take eight
good findings down with the one bad one. Parsing stays forgiving so that
judgement can happen one item at a time, further along.

The *outcome* model is what a step hands back once that judgement has been
made. By then everything in it is a real, fully checked node, and the
items that did not survive are recorded separately with the reason they
were dropped.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from lumen.schemas.nodes import (
    CausalChainNode,
    CausalStepNode,
    EventNode,
    ObservationNode,
    SessionNode,
)

# ---------------------------------------------------------------------------
# What the language model is asked to return
# ---------------------------------------------------------------------------


class ExtractedObservation(BaseModel):
    """
    One finding, as the model reported it and before anything is checked.

    Attributes:
        type: Which category of finding this is. Checked later against the
            fixed list; an unrecognised name loses this finding only.
        content: The finding itself, in one or two sentences.
        provenance: Whose idea this was — the person's, the assistant's, or
            both together.
        extraction_signal_strength: How much weight this deserves when the
            history is searched later.
        extraction_confidence: Whether this was described fresh or
            reconstructed from a distant memory.
        person_ref: The name of the person involved, if any.
        raw_evidence: Quotes from the entry that support the finding. This
            is what makes a finding checkable against the text instead of
            something taken on trust.
    """

    model_config = ConfigDict(extra="ignore")

    type: str = ""
    content: str = ""
    provenance: str = "USER_GENERATED"
    extraction_signal_strength: str = "STANDARD"
    extraction_confidence: str = "STANDARD"
    person_ref: str | None = None
    raw_evidence: list[str] = Field(default_factory=list)


class ExtractedEvent(BaseModel):
    """
    Something that actually happened, as the model reported it.

    Attributes:
        event_summary: What occurred, in one sentence.
        signal_strength: How much weight this deserves later.
        person_refs: Names of anyone involved.
        raw_evidence: Quotes from the entry describing it.
    """

    model_config = ConfigDict(extra="ignore")

    event_summary: str = ""
    signal_strength: str = "STANDARD"
    person_refs: list[str] = Field(default_factory=list)
    raw_evidence: list[str] = Field(default_factory=list)


class ExtractedCausalStep(BaseModel):
    """
    One link in a chain of cause and effect.

    Attributes:
        step: Where this sits in the sequence, counting from 1.
        type: What kind of link it is — the trigger, an inner state, an
            action, an outcome, or what was learned.
        content: What happened at this point.
        branch_id: Set when one action led to two different outcomes, so
            the parallel paths can be told apart.
    """

    model_config = ConfigDict(extra="ignore")

    step: int = 0
    type: str = ""
    content: str = ""
    branch_id: str | None = None


class ExtractedCausalChain(BaseModel):
    """
    A sequence of cause and effect running through an episode.

    Attributes:
        chain_summary: What the sequence describes, in one line.
        is_anticipatory: True when the sequence is feared or imagined
            rather than something that actually happened.
        causal_chain: The steps, in order.
    """

    model_config = ConfigDict(extra="ignore")

    chain_summary: str = ""
    is_anticipatory: bool = False
    causal_chain: list[ExtractedCausalStep] = Field(default_factory=list)


class ReflectionExtractionResponse(BaseModel):
    """Everything found in an entry worth reading closely."""

    model_config = ConfigDict(extra="ignore")

    observations: list[ExtractedObservation] = Field(default_factory=list)
    events: list[ExtractedEvent] = Field(default_factory=list)
    causal_mechanisms: list[ExtractedCausalChain] = Field(default_factory=list)


class RawCaptureResponse(BaseModel):
    """
    The little that is taken from a thin entry.

    Attributes:
        context: One sentence saying what the entry is about on the
            surface. No interpretation.
        emotion: A feeling, but only one the person named themselves.
        emotion_quote: Their own words naming that feeling. Required with
            the feeling, and checked against the entry afterwards. Asking
            for the quote is what turns "do not infer feelings" from an
            instruction the model may ignore into something that can be
            verified.
    """

    model_config = ConfigDict(extra="ignore")

    context: str = ""
    emotion: str | None = None
    emotion_quote: str | None = None


# ---------------------------------------------------------------------------
# What a step hands back
# ---------------------------------------------------------------------------


class DropRule(StrEnum):
    """
    Why an item was thrown away.

    Named rather than free text so that a run's problems can be counted and
    compared. "Forty-one findings lost to unknown types this week" is a
    prompt that needs fixing; the same information as forty-one different
    sentences is noise.
    """

    UNKNOWN_TYPE = "UNKNOWN_TYPE"
    UNKNOWN_ENUM_VALUE = "UNKNOWN_ENUM_VALUE"
    SIGNAL_FLOOR = "SIGNAL_FLOOR"
    EMPTY_CONTENT = "EMPTY_CONTENT"
    EXCLUDED_TYPE = "EXCLUDED_TYPE"
    UNKNOWN_PERSON = "UNKNOWN_PERSON"
    UNKNOWN_STEP_TYPE = "UNKNOWN_STEP_TYPE"
    CHAIN_TOO_SHORT = "CHAIN_TOO_SHORT"
    TYPE_NOT_ALLOWED_HERE = "TYPE_NOT_ALLOWED_HERE"
    QUOTE_NOT_FOUND = "QUOTE_NOT_FOUND"
    OVER_LIMIT = "OVER_LIMIT"
    REJECTED_BY_SCHEMA = "REJECTED_BY_SCHEMA"


class DropRecord(BaseModel):
    """
    A note that one item did not survive checking.

    Carries where the item was and which rule it broke, and never the item
    itself. A log that quoted the content would slowly become a second copy
    of somebody's private writing.

    Attributes:
        item_kind: What sort of item this was — a finding, an event, or a
            chain.
        index: Its position in the reply, so it can be located when
            debugging against a captured response.
        rule: The rule it broke.
        detail: A short, non-identifying note, such as the unrecognised
            name that was used.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    item_kind: str
    index: int = Field(ge=0)
    rule: DropRule
    detail: str = ""


class ExtractionOutcome(BaseModel):
    """
    Everything one episode produced, after checking.

    Attributes:
        observations: The findings that survived.
        events: The things that happened.
        sessions: The anchor node for this episode, when one was minted.
        chains: Cause-and-effect sequences.
        steps: The individual links of those sequences.
        drops: Items that were thrown away, with reasons.
        ungrounded: How many findings quoted evidence that could not be
            located in the entry. Not fatal — a translated entry is
            legitimately paraphrased — but worth watching, because a rising
            number is what invention looks like from the outside.
        used_fallback: True when the model call itself failed and nothing
            was extracted.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    observations: tuple[ObservationNode, ...] = ()
    events: tuple[EventNode, ...] = ()
    sessions: tuple[SessionNode, ...] = ()
    chains: tuple[CausalChainNode, ...] = ()
    steps: tuple[CausalStepNode, ...] = ()
    drops: tuple[DropRecord, ...] = ()
    ungrounded: int = 0
    used_fallback: bool = False

    @property
    def is_empty(self) -> bool:
        """True when nothing usable came out of the episode."""
        return not (self.observations or self.events or self.chains)


__all__ = [
    "ExtractedObservation",
    "ExtractedEvent",
    "ExtractedCausalStep",
    "ExtractedCausalChain",
    "ReflectionExtractionResponse",
    "RawCaptureResponse",
    "DropRule",
    "DropRecord",
    "ExtractionOutcome",
]
