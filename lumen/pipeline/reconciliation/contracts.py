"""
The shapes reconciliation hands around internally.

Three kinds of model live here, and keeping them apart is what makes the
stage readable.

The *ask* models describe what a model is invited to return. Their fields
are loose — plain text where a fixed category will eventually go, defaults
everywhere — for the same reason as the earlier stages: one invented action
name should cost one decision, not the whole entry.

The *item* models describe what is being decided about, gathered from the
extraction and the search into one object so nothing further down has to
know which of the three kinds of finding it is looking at.

The *settled* model is what a decision looks like once the checks have run.
By then the action is real, the confidence has been judged against the bar
for that action, and any reason it was refused is recorded by name.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from lumen.schemas.enums import (
    CandidateRetrievalSource,
    ModelRole,
    ObservationType,
    Provenance,
    ReconciliationAction,
    SignalStrength,
    StructuralAnchorType,
)
from lumen.schemas.nodes import EventNode, ObservationNode, SessionNode
from lumen.schemas.pipeline import CandidateNode

# ---------------------------------------------------------------------------
# What a model is asked to return
# ---------------------------------------------------------------------------


class ProposedAction(BaseModel):
    """
    One reading of how a finding relates to the past.

    Attributes:
        action: What the model thinks should happen. Checked afterwards
            against the list of real actions.
        target_node_id: Which existing record it relates to, if any.
        confidence: How sure the model is, from 0 to 1.
        reason: One line saying why, kept for the decision note and for
            anyone reviewing the item later.
    """

    model_config = ConfigDict(extra="ignore")

    action: str = ""
    target_node_id: str | None = None
    confidence: float = 0.0
    reason: str = ""


class NewNodeContent(BaseModel):
    """
    The wording for a record that does not exist yet.

    Only asked for when the finding is the sort of thing that can become a
    lasting record and nothing in the past matched it.

    Attributes:
        kind: Whether this should be a belief or a pattern.
        name: A short name for it, which also becomes its identifier.
        statement: The record itself, in the person's own terms.
        domain: Which part of life it belongs to.
    """

    model_config = ConfigDict(extra="ignore")

    kind: str = ""
    name: str = ""
    statement: str = ""
    domain: str = ""


class PersonSketch(BaseModel):
    """
    The little that is known about someone the first time they are recorded.

    Attributes:
        name: What the person is called in this entry.
        relationship: How they relate to the writer, if the entry says.
        sentiment: How the writer seems to feel about them, if the entry
            says.
    """

    model_config = ConfigDict(extra="ignore")

    name: str = ""
    relationship: str = "UNKNOWN"
    sentiment: str = "UNKNOWN"


class ItemDecision(BaseModel):
    """
    Everything a model has to say about one finding.

    Attributes:
        item_index: Which finding this answers, counting from 1. Answers are
            matched back by this number, so a reply that loses one item
            cannot silently shift every decision after it onto the wrong
            finding.
        primary: The best reading.
        runner_up: The next best. Always asked for, because two readings
            that score alike mean the model is guessing, and that is only
            visible if the second one was recorded.
        is_local_extremum: True when the finding describes a short, intense
            spike inside a longer stretch — a crunch week, not a changed
            person. Asked for because only a reader of the text can tell.
        new_node: Wording for a record that would have to be created.
        delta_description: What changed, when something is said to have
            changed.
        contradiction_summary: What the clash is, when two beliefs are held
            at once.
        tension_summary: What the tension is, when two opposing things are
            both true.
        regulation_summary: What was interrupted, when the person caught
            themselves.
    """

    model_config = ConfigDict(extra="ignore")

    item_index: int = 0
    primary: ProposedAction = Field(default_factory=ProposedAction)
    runner_up: ProposedAction | None = None
    is_local_extremum: bool = False
    new_node: NewNodeContent | None = None
    delta_description: str | None = None
    contradiction_summary: str | None = None
    tension_summary: str | None = None
    regulation_summary: str | None = None


class DecisionResponse(BaseModel):
    """What the first, fast pass returns for a whole entry."""

    model_config = ConfigDict(extra="ignore")

    decisions: list[ItemDecision] = Field(default_factory=list)
    people: list[PersonSketch] = Field(default_factory=list)


class ConfirmedDecision(ItemDecision):
    """
    A second opinion on one high-consequence reading.

    Attributes:
        confirmed: True to stand by the fast model's reading, false to
            replace it with the one in `primary`.
    """

    confirmed: bool = True


class EscalationResponse(BaseModel):
    """What the careful second pass returns."""

    model_config = ConfigDict(extra="ignore")

    verdicts: list[ConfirmedDecision] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# What is being decided about
# ---------------------------------------------------------------------------


class DecisionItem(BaseModel):
    """
    One extracted finding together with everything known about it.

    Built once at the start so the rest of the stage never has to ask which
    of the three kinds of finding it is holding.

    Attributes:
        source: The finding itself, as it was extracted.
        node_type: Which kind of record the finding is.
        text: What it says, in one piece of text.
        observation_type: The category of finding, for the ones that have
            one. Events and sessions do not.
        signal_strength: How much weight it carries.
        provenance: Whose idea it was.
        episode_id: The piece of writing it came from.
        person_refs: Anyone it names.
        candidates: What the search brought back for it.
        search_failed: True when the search could not run. Nothing found and
            nothing searched look identical from here, and only one of them
            means the thought is new.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    source: ObservationNode | EventNode | SessionNode
    node_type: str = Field(min_length=1)
    text: str = Field(min_length=1)
    observation_type: ObservationType | None = None
    signal_strength: SignalStrength = SignalStrength.STANDARD
    provenance: Provenance = Provenance.USER_GENERATED
    episode_id: str = Field(min_length=1)
    person_refs: tuple[str, ...] = ()
    candidates: tuple[CandidateNode, ...] = ()
    search_failed: bool = False

    @property
    def node_id(self) -> str:
        """The identifier of the finding being decided about."""
        return self.source.node_id


class HistoricalNode(BaseModel):
    """
    An existing record read back in full, so a decision can be made about it.

    A candidate from the search carries only enough to recognise a record
    by. Deciding needs more: how old it is, whether it has been argued with
    before, and every field of it if a new version has to be built from it.

    Attributes:
        node_id: Its identifier.
        node_type: Which kind of record it is.
        preview: Its readable content.
        valid_from: When it was first true, used to tell a long-held belief
            from a recent one.
        era_tag: The stretch of life it belongs to, where it has one.
        row: The whole record as it was stored.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    preview: str = ""
    valid_from: datetime | None = None
    era_tag: str | None = None
    row: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# What a decision looks like once it has been checked
# ---------------------------------------------------------------------------


class GateRule(StrEnum):
    """
    Why a decision was held back or changed.

    Named rather than written out in prose so that a run's refusals can be
    counted. "Nineteen decisions held back for low confidence this week" is
    something to act on; the same thing as nineteen different sentences is
    noise.

    IMPOSSIBLE_ACTION — the proposed link does not exist between those two
                        kinds of record.
    UNKNOWN_ACTION    — the model named an action that is not one of the
                        eight.
    TIE               — the top two readings were too close to separate.
    TRIAL_NOT_TRAIT   — a first deviation from a long-held belief, which is
                        a moment rather than a change.
    LOCAL_EXTREMUM    — a short spike inside a longer stretch, recorded on
                        its own instead of rewriting the baseline.
    BELOW_THRESHOLD   — a clear reading, but not confident enough to act on.
    SEARCH_FAILED     — the past could not be searched, so novelty cannot be
                        claimed.
    NO_CAUSAL_ANCHOR  — a change with nothing that could have caused it.
    MISSING_WORDING   — an action that has to say what changed, or what the
                        clash is, arrived without saying it.
    UNREADABLE        — no usable answer came back at all.
    """

    IMPOSSIBLE_ACTION = "IMPOSSIBLE_ACTION"
    UNKNOWN_ACTION = "UNKNOWN_ACTION"
    TIE = "TIE"
    TRIAL_NOT_TRAIT = "TRIAL_NOT_TRAIT"
    LOCAL_EXTREMUM = "LOCAL_EXTREMUM"
    BELOW_THRESHOLD = "BELOW_THRESHOLD"
    SEARCH_FAILED = "SEARCH_FAILED"
    NO_CAUSAL_ANCHOR = "NO_CAUSAL_ANCHOR"
    MISSING_WORDING = "MISSING_WORDING"
    UNREADABLE = "UNREADABLE"


class SettledDecision(BaseModel):
    """
    One decision after every check has run.

    This is the single thing both the permanent note and the records to be
    written are built from, so the two can never describe different
    decisions.

    Attributes:
        item: The finding this is about.
        action: What will happen. Still meaningful when the decision was
            refused: it says what would have happened.
        target_node_id: The existing record involved, if any.
        target_type: Which kind of record that is.
        confidence: How sure the deciding model was.
        runner_up_action: The second-best reading.
        runner_up_confidence: How sure the model was about that one.
        runner_up_target_node_id: The existing record the second reading
            pointed at. Kept because a tie is a question with two answers,
            and an answer nobody recorded the target of cannot be taken.
        tied_action: The reading that was leading when a tie was declared,
            and the confidence that went with it. `action` becomes AMBIGUOUS
            at that moment, which is right — the system has no preference —
            but it would otherwise erase the first of the two readings, and
            those two readings are the entire question being asked.
        tied_confidence: How sure the model was about that first reading.
        reason: The model's one-line justification.
        refusal: Set when the decision will not be carried out, saying why.
            A refused decision writes nothing and waits for a person.
        altered_by: Set when a check changed the action rather than stopping
            it — a claimed change turned into a separately recorded moment,
            say. Kept so the note of the decision shows what the model
            proposed and what the system did with it.
        escalated: True when the careful model had the final say.
        model_used: The model that decided.
        model_role: Which kind of model that was.
        retrieval_source: Whether the record involved was found by meaning
            or by an anchor. Worth keeping, because "a name matched" and "it
            reads similarly" are different claims.
        anchor_type: Which anchor found it, when one did.
        anchor_value: The value of that anchor.
        delta_description: What changed, for a decision that changes
            something.
        contradiction_summary: What the clash is.
        tension_summary: What the tension is.
        regulation_summary: What was interrupted.
        new_node: Wording for a record that has to be created.
        is_local_extremum: True when the finding describes a short, intense
            stretch rather than how the person usually is.
        co_created_origin: True when the finding refines something the
            assistant first suggested and the person has now made their own.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    item: DecisionItem
    action: ReconciliationAction
    target_node_id: str | None = None
    target_type: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    runner_up_action: ReconciliationAction | None = None
    runner_up_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    runner_up_target_node_id: str | None = None
    tied_action: ReconciliationAction | None = None
    tied_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reason: str = ""
    refusal: GateRule | None = None
    altered_by: GateRule | None = None
    escalated: bool = False
    model_used: str = "unknown"
    model_role: ModelRole = ModelRole.LIGHTWEIGHT
    retrieval_source: CandidateRetrievalSource = CandidateRetrievalSource.SEMANTIC
    anchor_type: StructuralAnchorType | None = None
    anchor_value: str | None = None
    delta_description: str | None = None
    contradiction_summary: str | None = None
    tension_summary: str | None = None
    regulation_summary: str | None = None
    new_node: NewNodeContent | None = None
    is_local_extremum: bool = False
    co_created_origin: bool = False

    @property
    def is_refused(self) -> bool:
        """True when this decision will not be carried out on its own."""
        return self.refusal is not None

    def refuse(self, rule: GateRule) -> "SettledDecision":
        """Hold this decision back, recording which rule stopped it."""
        return self.model_copy(update={"refusal": rule})


__all__ = [
    "ProposedAction",
    "NewNodeContent",
    "PersonSketch",
    "ItemDecision",
    "DecisionResponse",
    "ConfirmedDecision",
    "EscalationResponse",
    "DecisionItem",
    "HistoricalNode",
    "GateRule",
    "SettledDecision",
]
