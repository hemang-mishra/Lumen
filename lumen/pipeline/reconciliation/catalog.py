"""
The fixed tables reconciliation decides by.

Everything here is a rule rather than a tuning knob: how confident the
system has to be before it will take each action, which actions are even
possible from which kind of finding, and which findings are allowed to
become a permanent part of someone's history. They live in one file so they
can be read side by side and quoted directly by a test — a threshold buried
in the branch that uses it is a threshold nobody ever checks.

The legality table is *derived* from the links the database actually
supports rather than written out by hand. A hand-written copy would drift
the first time a link was added, and the failure would only show up as a
save that stops halfway.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from lumen.schemas.edges import LOGICAL_TO_PHYSICAL, LogicalEdgeType
from lumen.schemas.enums import ModelRole, ObservationType, ReconciliationAction

# ---------------------------------------------------------------------------
# How sure the system has to be
# ---------------------------------------------------------------------------

# The bar each action has to clear. They are not arbitrary: the two that
# permanently change a long-held belief sit highest, and the one that simply
# records something new sits lowest, because being wrong about novelty costs
# a duplicate while being wrong about change costs the truth.
THRESHOLDS: dict[ReconciliationAction, float] = {
    ReconciliationAction.MERGE: 0.88,
    ReconciliationAction.REINFORCE: 0.80,
    ReconciliationAction.EVOLVE: 0.93,
    ReconciliationAction.BRANCH: 0.75,
    ReconciliationAction.CONTRADICT: 0.85,
    ReconciliationAction.DIALECTIC: 0.88,
    ReconciliationAction.REGULATE: 0.82,
}

# The actions a fast model is not trusted to take on its own. Each of these
# permanently alters something the person has held for a long time, so a
# second, more careful model is asked to confirm before any of them counts.
ESCALATED_ACTIONS: frozenset[ReconciliationAction] = frozenset(
    {
        ReconciliationAction.EVOLVE,
        ReconciliationAction.CONTRADICT,
        ReconciliationAction.DIALECTIC,
    }
)

# Two readings this close together are not a preference, they are a coin
# toss. Whatever the absolute numbers, the item goes to a person.
TIE_WINDOW = 0.05

# How old a belief has to be before a single contrary moment stops being
# enough to change it, and the bar that moment then has to clear. The bar is
# deliberately out of reach: the point is not to make it hard, it is to make
# the system record the moment separately and wait for it to happen again.
TRAIT_AGE_DAYS = 180
TRIAL_PENALTY_THRESHOLD = 0.98


def threshold_for(action: ReconciliationAction) -> float:
    """How confident the system has to be to take this action by itself."""
    return THRESHOLDS.get(action, 1.0)


def role_for(action: ReconciliationAction) -> ModelRole:
    """
    Which kind of model has the final say on an action.

    The careful model decides the three that change a long-held belief; the
    fast one handles the rest.
    """
    return (
        ModelRole.THINKING if action in ESCALATED_ACTIONS else ModelRole.LIGHTWEIGHT
    )


# ---------------------------------------------------------------------------
# Which actions are possible at all
# ---------------------------------------------------------------------------

# The three kinds of thing reconciliation makes decisions about.
SOURCE_TYPES: frozenset[str] = frozenset(
    {"ObservationNode", "EventNode", "SessionNode"}
)

# The two kinds of long-lived record a decision can point at.
STANDING_TYPES: frozenset[str] = frozenset({"PatternNode", "BeliefNode"})


@dataclass(frozen=True)
class ActionShape:
    """
    What has to be true for one action to be possible.

    Attributes:
        sources: The kinds of finding this action can start from.
        targets: The kinds of existing record it can point at. Empty when
            the action does not point at one.
        needs_target: Whether an existing record has to be named.
        needs_standing_node: Whether the finding has to be the sort of thing
            that can become a belief or a pattern. Two actions link one
            long-lived record to another, so a finding that cannot become
            one has nothing to link with.
    """

    sources: frozenset[str]
    targets: frozenset[str]
    needs_target: bool
    needs_standing_node: bool = False


def _pairs_for(logical: LogicalEdgeType) -> tuple[frozenset[str], frozenset[str]]:
    """Read the record types a link type can join, straight from the database's own list."""
    sources = {source for (kind, source, _) in LOGICAL_TO_PHYSICAL if kind is logical}
    targets = {target for (kind, _, target) in LOGICAL_TO_PHYSICAL if kind is logical}
    return frozenset(sources), frozenset(targets)


def _shape_from_links(
    logical: LogicalEdgeType, *, needs_target: bool = True
) -> ActionShape:
    """Build an action's rules from the links that back it."""
    sources, targets = _pairs_for(logical)
    return ActionShape(
        sources=sources & SOURCE_TYPES,
        targets=targets,
        needs_target=needs_target,
    )


# Four of the actions write a link that runs straight from the finding to
# the record it relates to, so their rules are exactly the links that exist.
# The other three do not: an evolved belief is linked from its own new
# version, a contradiction is linked from a record joining both sides, and a
# tension runs between two long-lived records. For those three the rule is
# written out, because the link's ends are not the finding at all.
ACTION_SHAPES: dict[ReconciliationAction, ActionShape] = {
    ReconciliationAction.MERGE: _shape_from_links(LogicalEdgeType.SAME_AS),
    ReconciliationAction.REINFORCE: _shape_from_links(LogicalEdgeType.REINFORCES),
    ReconciliationAction.REGULATE: _shape_from_links(LogicalEdgeType.REGULATES),
    ReconciliationAction.BRANCH: _shape_from_links(
        LogicalEdgeType.BRANCHES_TO, needs_target=False
    ),
    ReconciliationAction.EVOLVE: ActionShape(
        sources=SOURCE_TYPES,
        targets=STANDING_TYPES,
        needs_target=True,
    ),
    ReconciliationAction.CONTRADICT: ActionShape(
        sources=frozenset({"ObservationNode"}),
        targets=frozenset({"BeliefNode"}),
        needs_target=True,
        needs_standing_node=True,
    ),
    ReconciliationAction.DIALECTIC: ActionShape(
        sources=frozenset({"ObservationNode"}),
        targets=STANDING_TYPES,
        needs_target=True,
        needs_standing_node=True,
    ),
}


# ---------------------------------------------------------------------------
# Which findings can become a permanent record
# ---------------------------------------------------------------------------


class PromotionTarget(StrEnum):
    """What a finding can grow into, when it is the sort of thing that can."""

    BELIEF = "BELIEF"
    PATTERN = "PATTERN"
    OPEN_LOOP = "OPEN_LOOP"


# Most of what gets noticed in a day is context: where someone was, what
# they did, how tired they felt. Those belong to the day they happened and
# nowhere else. A much smaller set are claims about how the person works or
# what they believe, and only those earn a record of their own that will
# still be retrieved years later.
#
# Every finding type appears here, including the ones that map to nothing. A
# type added later with no entry fails the test that checks this table is
# complete, which is the point: deciding a new kind of finding is not worth
# keeping should be a decision somebody made, not a default nobody noticed.
PROMOTION: dict[ObservationType, PromotionTarget | None] = {
    # Claims about what is true, or about the self
    ObservationType.BELIEF: PromotionTarget.BELIEF,
    ObservationType.META_BELIEF: PromotionTarget.BELIEF,
    ObservationType.EPISTEMIC_SHIFT: PromotionTarget.BELIEF,
    ObservationType.CONCEPTUAL_REFRAME: PromotionTarget.BELIEF,
    ObservationType.PERSPECTIVE_SHIFT: PromotionTarget.BELIEF,
    ObservationType.CORE_WOUND: PromotionTarget.BELIEF,
    ObservationType.CORE_CONFLICT: PromotionTarget.BELIEF,
    ObservationType.IDENTITY_AFFINITY: PromotionTarget.BELIEF,
    ObservationType.IDENTITY_FUSION_STATE: PromotionTarget.BELIEF,
    ObservationType.EXISTENTIAL_REFLECTION: PromotionTarget.BELIEF,
    ObservationType.ACCEPTANCE_ACKNOWLEDGEMENT: PromotionTarget.BELIEF,
    ObservationType.METACOGNITIVE_BREAKTHROUGH: PromotionTarget.BELIEF,
    ObservationType.LESSON: PromotionTarget.BELIEF,
    # Ways of behaving that repeat
    ObservationType.PATTERN: PromotionTarget.PATTERN,
    ObservationType.RUMINATION_LOOP: PromotionTarget.PATTERN,
    ObservationType.COGNITIVE_DISTORTION: PromotionTarget.PATTERN,
    ObservationType.COGNITIVE_DISTORTION_STATE: PromotionTarget.PATTERN,
    ObservationType.COGNITIVE_DEFENSE_MECHANISM: PromotionTarget.PATTERN,
    ObservationType.SELF_NARRATION_PATTERN: PromotionTarget.PATTERN,
    ObservationType.SOCIAL_PERFORMANCE_STATE: PromotionTarget.PATTERN,
    ObservationType.SUBPERSONALITY_ACTION: PromotionTarget.PATTERN,
    ObservationType.RELATIONAL_DYNAMIC: PromotionTarget.PATTERN,
    ObservationType.ENVIRONMENTAL_DEPENDENCY: PromotionTarget.PATTERN,
    ObservationType.INAUTHENTICITY_STATE: PromotionTarget.PATTERN,
    ObservationType.OTHER_PERSON_MODEL: PromotionTarget.PATTERN,
    ObservationType.SYSTEM_DESIGN_ITERATION: PromotionTarget.PATTERN,
    # A question that keeps coming back
    ObservationType.OPEN_LOOP: PromotionTarget.OPEN_LOOP,
    # Everything below belongs to the day it happened. Each can still
    # reinforce or interrupt something already known — it simply never
    # becomes a standing record of its own.
    ObservationType.CONTEXT: None,
    ObservationType.CONTEXT_SEVERANCE: None,
    ObservationType.EMOTION: None,
    ObservationType.SOMATIC_STATE: None,
    ObservationType.SOMATIC_CATHARSIS: None,
    ObservationType.ANTICIPATORY_ANXIETY: None,
    ObservationType.COGNITIVE_FRICTION: None,
    ObservationType.TRIGGER_CATALYST: None,
    ObservationType.PROSODY_SIGNAL: None,
    ObservationType.ENVIRONMENTAL_REANCHORING: None,
    ObservationType.INTERVENTION_APPLIED: None,
    ObservationType.ENERGY_SPIKE_EVENT: None,
    ObservationType.SUPPRESSED_EMOTION_SURFACING: None,
    ObservationType.ERA_INTEGRATION_STATE: None,
    ObservationType.PHYSIOLOGICAL_CAPACITY_STATE: None,
    ObservationType.BIOGRAPHICAL_GAP: None,
    ObservationType.LEXICON_UPDATE: None,
    ObservationType.METACOGNITIVE_INTERRUPT: None,
    ObservationType.ENVIRONMENTAL_CONTEXT: None,
    ObservationType.GRATITUDE_APPRECIATION: None,
}


def promotion_for(observation_type: ObservationType | None) -> PromotionTarget | None:
    """What this kind of finding can grow into, if anything."""
    if observation_type is None:
        return None
    return PROMOTION.get(observation_type)


def is_action_possible(
    action: ReconciliationAction,
    *,
    source_type: str,
    target_type: str | None,
    can_become_standing: bool,
) -> bool:
    """
    Say whether an action could actually be carried out.

    A model can propose linking an event to a belief as "the same thing",
    and there is simply no such link — the check is here so that impossible
    answers are caught while deciding, rather than when saving.
    """
    shape = ACTION_SHAPES.get(action)
    if shape is None:
        return False
    if source_type not in shape.sources:
        return False
    if shape.needs_target:
        if target_type is None or target_type not in shape.targets:
            return False
    if shape.needs_standing_node and not can_become_standing:
        return False
    return True


__all__ = [
    "THRESHOLDS",
    "ESCALATED_ACTIONS",
    "TIE_WINDOW",
    "TRAIT_AGE_DAYS",
    "TRIAL_PENALTY_THRESHOLD",
    "SOURCE_TYPES",
    "STANDING_TYPES",
    "ActionShape",
    "ACTION_SHAPES",
    "PromotionTarget",
    "PROMOTION",
    "threshold_for",
    "role_for",
    "promotion_for",
    "is_action_possible",
]
