"""
The shapes the review queue passes around.

Two vocabularies live here and they are deliberately not the same one. What
somebody taps on a card is not what gets written to the graph: "approve" and
"take the first reading" are one tap on two different card layouts, and
folding them together would lose the difference between a recommendation
somebody accepted and a tie somebody broke.

Everything in this file is data. The decisions about what to do with it live
in the modules beside it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from lumen.operational.enums import HitlItemStatus
from lumen.schemas.enums import (
    HitlEntryType,
    HitlResolutionChoice,
    ReconciliationAction,
    SignalStrength,
)
from lumen.schemas.nodes import DecisionAuditNode
from lumen.schemas.pipeline import GraphWritePlan


class ReviewError(Exception):
    """Something about answering a card could not be done."""


class ChoiceNotOffered(ReviewError):
    """
    The answer given is not one this card offers.

    A tie has a second reading to take and an ordinary low-confidence item
    does not, so the same word means something on one card and nothing on
    the other. Refused rather than quietly mapped onto the nearest available
    answer, because guessing here writes a permanent change to somebody's
    history that they did not ask for.
    """

    def __init__(self, choice: str, offered: list[str]) -> None:
        self.choice = choice
        self.offered = offered
        super().__init__(
            f"{choice} is not one of this item's answers ({', '.join(offered)})"
        )


class StaleProposal(ReviewError):
    """
    The record this answer would act on is no longer the current one.

    The proposal was worked out against the graph as it stood on the day the
    question was raised. If a later entry has since moved the same belief on
    a version, taking the answer now would attach today's decision to
    yesterday's wording. Refusing says so plainly; the answer that records
    the finding as its own separate thing is unaffected and stays available.
    """

    def __init__(self, node_id: str, reason: str) -> None:
        self.node_id = node_id
        self.reason = reason
        super().__init__(f"{node_id} can no longer be acted on: {reason}")


class ResolutionChoice(StrEnum):
    """
    What somebody tapped on a card.

    APPROVE     — do the thing that was recommended.
    REJECT      — do not. What that comes to depends on what was proposed:
        against a recommendation to join something existing it means "record
        it on its own", and against a recommendation to record it on its own
        it means "leave it with the entry and record nothing".
    SNOOZE      — not now.
    ACTION_A    — on a tie, take the first reading.
    ACTION_B    — on a tie, take the second.
    CREATE_NEW  — on a tie, take neither and record it as its own thing.

    APPROVE and ACTION_A do the same work, as do REJECT and CREATE_NEW. They
    are separate words because they appear on different card layouts, and the
    graph is told which one was actually offered.
    """

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    SNOOZE = "SNOOZE"
    ACTION_A = "ACTION_A"
    ACTION_B = "ACTION_B"
    CREATE_NEW = "CREATE_NEW"


class CandidatePreview(BaseModel):
    """
    An existing record shown on a card, in its own words.

    Enough to recognise what is being proposed without opening anything
    else. A person cannot judge "is this the same belief" from an identifier.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str
    node_type: str
    text: str = ""
    valid_from: datetime | None = None
    evidence_count: int | None = None
    is_current: bool = True


class CardOption(BaseModel):
    """
    One answer a card offers, with everything needed to weigh it.

    Attributes:
        choice: The answer to send back.
        label: What the button says, in plain words.
        action: What the graph would do.
        target: The existing record it would act on, where there is one.
        confidence: How sure the model was about this reading.
        difference: What separates this reading from the other one, on a
            card that offers two.
        writes_nothing: True when taking this changes nothing in the graph.
            Surfaced rather than hidden, because a button that does nothing
            should say so.
        declines: True when this answer is a refusal rather than an action.
            It writes nothing and is not a smaller version of the
            recommendation — it is the person saying the finding should stay
            part of its entry and become nothing more.
    """

    model_config = ConfigDict(extra="forbid")

    choice: ResolutionChoice
    label: str
    action: ReconciliationAction
    target: CandidatePreview | None = None
    confidence: float | None = None
    difference: str | None = None
    writes_nothing: bool = False
    declines: bool = False


class QueueCard(BaseModel):
    """
    One question, assembled for somebody to answer in a few seconds.

    Everything needed to judge it is here. A card that requires opening
    another screen to make sense of is a card that does not get answered.

    A card that cannot be answered at all still appears, and says why. The
    alternative — leaving it out — produces a screen where the count says
    forty are waiting and the list below shows none, which is worse than an
    awkward card: it is a screen that contradicts itself.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str
    entry_type: HitlEntryType
    signal_strength: SignalStrength
    status: HitlItemStatus
    asked_at: datetime
    age_days: int = Field(ge=0)
    snooze_count: int = Field(ge=0)
    snoozed_until: datetime | None = None
    auto_resolves_at: datetime | None = None
    episode_id: str | None = None
    episode_summary: str | None = None
    source_node_id: str
    source_text: str = ""
    recommended_action: ReconciliationAction | None = None
    recommended_confidence: float | None = None
    compared_against: CandidatePreview | None = None
    question: str = ""
    options: list[CardOption] = Field(default_factory=list)
    stale: bool = False
    stale_reason: str | None = None
    answerable: bool = True
    unanswerable_reason: str | None = None


class QueueCounts(BaseModel):
    """
    How much is waiting, in the form a badge needs.

    The oldest date is here as well as the count because they answer
    different questions. Three items raised this morning and three raised
    five weeks ago are the same number and completely different situations.
    """

    model_config = ConfigDict(extra="forbid")

    pending: int = Field(ge=0)
    visible: int = Field(ge=0)
    parked: int = Field(ge=0)
    cap: int = Field(ge=1)
    at_capacity: bool = False
    oldest_asked_at: datetime | None = None


class QueueView(BaseModel):
    """A page of cards, with the counts they sit inside."""

    model_config = ConfigDict(extra="forbid")

    cards: list[QueueCard] = Field(default_factory=list)
    counts: QueueCounts


class ResolutionPlan(BaseModel):
    """
    What answering one card comes to, worked out and not yet saved.

    Built without touching a database so that every rule about which answer
    does what can be checked on its own.
    """

    model_config = ConfigDict(extra="forbid")

    write_plan: GraphWritePlan
    new_audit: DecisionAuditNode
    action_taken: ReconciliationAction
    recorded_choice: HitlResolutionChoice
    writes_nothing: bool = False


class ResolutionOutcome(BaseModel):
    """
    What actually happened when a card was answered.

    Reports the records and links that landed rather than only success, so
    the caller can say what changed instead of asking the graph again.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str
    choice: ResolutionChoice
    recorded_choice: HitlResolutionChoice
    action_taken: ReconciliationAction
    original_audit_node_id: str
    new_audit_node_id: str
    nodes_written: list[str] = Field(default_factory=list)
    edges_written: list[tuple[str, str, str]] = Field(default_factory=list)
    vectors_written: list[str] = Field(default_factory=list)
    unindexed_node_ids: list[str] = Field(default_factory=list)
    admitted: list[str] = Field(default_factory=list)
    writes_nothing: bool = False


class SweepReport(BaseModel):
    """
    What the housekeeping pass did.

    A system that settles things on somebody's behalf has to be able to say
    what it settled, so this is returned and logged rather than kept
    internal.

    "Closed" is the one that changes nothing: questions whose every answer
    would have written the same nothing, taken off the list because there was
    never a decision in them.
    """

    model_config = ConfigDict(extra="forbid")

    ran_at: datetime
    auto_resolved: list[str] = Field(default_factory=list)
    closed: list[str] = Field(default_factory=list)
    admitted: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    still_pending: int = Field(default=0, ge=0)
    oldest_pending_at: datetime | None = None


__all__ = [
    "ReviewError",
    "ChoiceNotOffered",
    "StaleProposal",
    "ResolutionChoice",
    "CandidatePreview",
    "CardOption",
    "QueueCard",
    "QueueCounts",
    "QueueView",
    "ResolutionPlan",
    "ResolutionOutcome",
    "SweepReport",
]
