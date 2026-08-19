"""
Turning a saved question into something a person can answer in seconds.

A card carries everything needed to judge one thing: what was found, what it
was matched against in the person's own earlier words, what the system
proposed, how sure it was, and how long the question has been waiting. If
answering means opening another screen first, the card has failed.

Reads only. Nothing here changes anything, which is why the queue can be
looked at as often as anybody likes.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from lumen.graph.provider import ReadOnlyGraph
from lumen.operational.schemas import HitlQueueItemRecord
from lumen.review.contracts import (
    CandidatePreview,
    CardOption,
    QueueCard,
    ResolutionChoice,
)
from lumen.schemas.enums import HitlEntryType, LifecycleNodeStatus
from lumen.schemas.pipeline import FrozenProposal, ProposalVariant

logger = logging.getLogger(__name__)

# Where each kind of record keeps the words worth showing a person, in the
# order to try them. Preferring the shortest meaningful field: a card is read
# standing up, and a paragraph where a sentence would do is a card that gets
# skipped rather than answered.
_PREVIEW_FIELDS: dict[str, tuple[str, ...]] = {
    "ObservationNode": ("content",),
    "EventNode": ("event_summary",),
    "SessionNode": ("session_summary",),
    "PatternNode": ("pattern_name", "pattern_description"),
    "BeliefNode": ("belief_statement",),
    "LessonNode": ("lesson_statement",),
    "AdoptedPrincipleNode": ("principle_statement",),
    "OpenLoopNode": ("loop_description",),
    "ContradictionNode": ("contradiction_summary",),
}

# The answers that mean "no". Which of the two appears depends on the
# layout, and both can come to mean "leave it alone" rather than "do the
# other thing".
_REFUSALS: frozenset[ResolutionChoice] = frozenset(
    {ResolutionChoice.REJECT, ResolutionChoice.CREATE_NEW}
)

# What a refusal says when there is no other action behind it.
_REFUSAL_LABELS: dict[ResolutionChoice, str] = {
    ResolutionChoice.REJECT: "No — leave it with the entry",
    ResolutionChoice.CREATE_NEW: "Neither — leave it with the entry",
}

# What each layout's buttons say. Plain verbs, because the words are the only
# explanation a card has room for.
_LABELS: dict[ResolutionChoice, str] = {
    ResolutionChoice.APPROVE: "Yes, do that",
    ResolutionChoice.REJECT: "No — it's something else",
    ResolutionChoice.ACTION_A: "Take the first",
    ResolutionChoice.ACTION_B: "Take the second",
    ResolutionChoice.CREATE_NEW: "Neither — it's new",
}


def wanted_node_ids(proposals: Iterable[FrozenProposal]) -> list[str]:
    """
    Every record a page of cards will need to show, gathered up.

    Collected for the whole page so the graph is asked once rather than once
    per card. Twenty cards reading three records each is sixty round trips to
    draw one screen.
    """
    wanted: set[str] = set()
    for proposal in proposals:
        wanted.add(proposal.source_node_id)
        for variant in _variants(proposal):
            if variant.target_node_id:
                wanted.add(variant.target_node_id)
    return sorted(wanted)


def read_rows(node_ids: list[str], *, graph: ReadOnlyGraph) -> dict[str, dict[str, Any]]:
    """
    Read back the records a page of cards needs, in one go.

    A failure here is not fatal. A card missing the earlier wording is worse
    than one with it and far better than a queue that will not open, so the
    failure is logged and the cards are drawn from what was saved with them.
    """
    if not node_ids:
        return {}
    try:
        rows = graph.get_nodes_by_ids(node_ids)
    except Exception:
        logger.warning("could not read the records behind the review queue")
        return {}
    return {str(row["node_id"]): dict(row) for row in rows if row.get("node_id")}


def read_episode_summaries(
    episode_ids: Iterable[str], *, graph: ReadOnlyGraph
) -> dict[str, str]:
    """The one-paragraph summary of each entry a page of cards came from."""
    wanted = sorted({episode_id for episode_id in episode_ids if episode_id})
    rows = read_rows(wanted, graph=graph)
    return {
        node_id: str(row.get("episode_summary") or "")
        for node_id, row in rows.items()
        if row.get("episode_summary")
    }


def wanted_for_items(items: Iterable[HitlQueueItemRecord]) -> list[str]:
    """
    Every record the queue rows themselves point at.

    Needed for the questions that have nothing saved behind them: the row
    still names the finding and what it was weighed against, and those two
    are the whole of what makes the question mean anything to a person.
    """
    wanted: set[str] = set()
    for item in items:
        for node_id in (item.observation_id, item.candidate_a_node_id):
            if node_id:
                wanted.add(node_id)
    return sorted(wanted)


def build_unanswerable_card(
    item: HitlQueueItemRecord,
    *,
    rows: Mapping[str, dict[str, Any]],
    episode_summaries: Mapping[str, str],
    now: datetime,
    auto_resolve_days: int,
    reason: str,
) -> QueueCard:
    """
    A question that can be shown and explained, but not carried out.

    There is no saved working to build answers from, so it offers none. What
    it does show is everything needed to understand the question: the finding
    in the person's own words, the entry it came from, what it was weighed
    against, and what the system was leaning towards. Without those a card is
    a row of identifiers, and nobody can judge a row of identifiers.

    The queue's own one-line summary is deliberately not used as the finding.
    It reads "BRANCH against obs_… held back: BELOW_THRESHOLD", which is a
    description of the machinery rather than of anything the person said.
    """
    asked_at = item.created_at or now
    source_id = item.observation_id or ""

    return QueueCard(
        item_id=item.id,
        entry_type=item.entry_type,
        signal_strength=item.signal_strength,
        status=item.status,
        asked_at=asked_at,
        age_days=max((now - asked_at).days, 0),
        snooze_count=item.snooze_count,
        snoozed_until=item.snoozed_until,
        auto_resolves_at=_auto_resolves_at(item, days=auto_resolve_days),
        episode_id=item.episode_id,
        episode_summary=episode_summaries.get(item.episode_id or "") or None,
        source_node_id=source_id or item.audit_node_id,
        source_text=_preview_text(rows.get(source_id, {})),
        recommended_action=item.recommended_action,
        recommended_confidence=item.confidence_a,
        compared_against=_preview_of(item.candidate_a_node_id, rows=rows),
        question=_question_for(item.entry_type),
        options=[],
        answerable=False,
        unanswerable_reason=reason,
    )


def build_card(
    item: HitlQueueItemRecord,
    proposal: FrozenProposal,
    *,
    rows: Mapping[str, dict[str, Any]],
    episode_summaries: Mapping[str, str],
    now: datetime,
    auto_resolve_days: int,
) -> QueueCard:
    """
    Assemble one card.

    The layout follows from why the item is waiting. A tie is a choice
    between two readings and gets three buttons; anything else is one
    recommendation to accept or turn down and gets two.
    """
    asked_at = item.created_at or proposal.frozen_at
    stale_reason = _stale_reason(proposal, rows=rows)

    return QueueCard(
        item_id=item.id,
        entry_type=item.entry_type,
        signal_strength=item.signal_strength,
        status=item.status,
        asked_at=asked_at,
        age_days=max((now - asked_at).days, 0),
        snooze_count=item.snooze_count,
        snoozed_until=item.snoozed_until,
        auto_resolves_at=_auto_resolves_at(item, days=auto_resolve_days),
        episode_id=item.episode_id,
        episode_summary=episode_summaries.get(item.episode_id or "") or None,
        source_node_id=proposal.source_node_id,
        source_text=proposal.source_text or _preview_text(
            rows.get(proposal.source_node_id, {})
        ),
        recommended_action=item.recommended_action,
        recommended_confidence=proposal.primary.confidence,
        compared_against=_preview_of(proposal.primary.target_node_id, rows=rows),
        question=_question_for(item.entry_type),
        options=_options_for(proposal, rows=rows),
        stale=stale_reason is not None,
        stale_reason=stale_reason,
    )


def offered_choices(proposal: FrozenProposal) -> list[ResolutionChoice]:
    """
    Which answers this item's layout allows, snoozing aside.

    Kept here beside the layout rather than in the code that carries an
    answer out, so a card and the check on what it accepts can never disagree
    about what it offered.
    """
    if proposal.entry_type is HitlEntryType.AMBIGUOUS_TIE:
        choices = [ResolutionChoice.ACTION_A]
        if proposal.runner_up is not None:
            choices.append(ResolutionChoice.ACTION_B)
        choices.append(ResolutionChoice.CREATE_NEW)
        return choices
    return [ResolutionChoice.APPROVE, ResolutionChoice.REJECT]


def standing_alone_choice(proposal: FrozenProposal) -> ResolutionChoice:
    """
    The answer that records the finding as its own separate thing.

    Every layout offers it and each one calls it something different: a
    recommendation is turned down, a tie is answered with neither. Asked for
    by meaning rather than by name so nothing has to hard-code one layout's
    wording — which is how the clock ends up unable to settle a tie.
    """
    if proposal.entry_type is HitlEntryType.AMBIGUOUS_TIE:
        return ResolutionChoice.CREATE_NEW
    return ResolutionChoice.REJECT


def _options_for(
    proposal: FrozenProposal, *, rows: Mapping[str, dict[str, Any]]
) -> list[CardOption]:
    """Build a button for each answer this card offers."""
    by_choice: dict[ResolutionChoice, ProposalVariant] = {
        ResolutionChoice.APPROVE: proposal.primary,
        ResolutionChoice.ACTION_A: proposal.primary,
        ResolutionChoice.REJECT: proposal.fallback,
        ResolutionChoice.CREATE_NEW: proposal.fallback,
    }
    if proposal.runner_up is not None:
        by_choice[ResolutionChoice.ACTION_B] = proposal.runner_up

    # Turning down "record this on its own" cannot mean "record this on its
    # own". Where the two would write the same thing, saying no means the
    # finding stays part of its entry and becomes nothing more.
    declining = proposal.saying_no_means_doing_nothing

    return [
        _option(
            choice,
            by_choice[choice],
            rows=rows,
            declines=declining and choice in _REFUSALS,
        )
        for choice in offered_choices(proposal)
        if choice in by_choice
    ]


def _option(
    choice: ResolutionChoice,
    variant: ProposalVariant,
    *,
    rows: Mapping[str, dict[str, Any]],
    declines: bool = False,
) -> CardOption:
    """One button, with the record it would act on shown in its own words."""
    if declines:
        return CardOption(
            choice=choice,
            label=_REFUSAL_LABELS[choice],
            action=variant.action,
            confidence=variant.confidence,
            difference="leave it with the entry it came from",
            writes_nothing=True,
            declines=True,
        )
    return CardOption(
        choice=choice,
        label=_LABELS.get(choice, choice.value),
        action=variant.action,
        target=_preview_of(variant.target_node_id, rows=rows),
        confidence=variant.confidence,
        difference=variant.delta_description or variant.summary or None,
        writes_nothing=variant.writes_nothing,
    )


def _preview_of(
    node_id: str | None, *, rows: Mapping[str, dict[str, Any]]
) -> CandidatePreview | None:
    """The shown form of one existing record, or nothing where there is none."""
    if not node_id:
        return None
    row = rows.get(node_id)
    if row is None:
        # Shown by identifier rather than dropped. "This points at something
        # I cannot read" is information; a silently missing candidate is not.
        return CandidatePreview(node_id=node_id, node_type="unknown", is_current=True)
    return CandidatePreview(
        node_id=node_id,
        node_type=str(row.get("_label", "unknown")),
        text=_preview_text(row),
        valid_from=_as_moment(row.get("valid_from")),
        evidence_count=_as_count(row.get("evidence_count")),
        is_current=_is_current(row),
    )


def _stale_reason(
    proposal: FrozenProposal, *, rows: Mapping[str, dict[str, Any]]
) -> str | None:
    """
    Why the recommended answer can no longer be taken, if it cannot.

    The proposal was worked out against the graph on the day the question
    was raised. A later entry may have moved the same belief on a version
    since, and attaching today's answer to the older wording would quietly
    put the decision in the wrong place.
    """
    target_id = proposal.primary.target_node_id
    if not target_id:
        return None

    row = rows.get(target_id)
    if row is None:
        return f"{target_id} is no longer in the graph"
    if not _is_current(row):
        return f"{target_id} has been replaced by a newer version since this was asked"
    return None


def _auto_resolves_at(
    item: HitlQueueItemRecord, *, days: int
) -> datetime | None:
    """
    When an item settles itself, for the items that ever do.

    Only something already deferred once has a date. An item nobody has
    touched waits indefinitely, which is the point: deferring it is a signal
    of intent, and never opening it is not.
    """
    if item.snooze_count < 1 or item.last_snoozed_at is None:
        return None
    return item.last_snoozed_at + timedelta(days=days)


def _question_for(entry_type: HitlEntryType) -> str:
    """The one line at the top of the card saying what is being asked."""
    if entry_type is HitlEntryType.AMBIGUOUS_TIE:
        return "Two readings of this scored too closely to separate. Which is right?"
    if entry_type is HitlEntryType.BELOW_THRESHOLD:
        return "This is what the system thinks, but it is not sure enough to act."
    return "This finding could not be read properly."


def _preview_text(row: Mapping[str, Any]) -> str:
    """The words to show for a record, whatever kind of record it is."""
    label = str(row.get("_label", ""))
    for field in _PREVIEW_FIELDS.get(label, ()):
        value = row.get(field)
        if value and str(value).strip():
            return str(value).strip()
    for field in ("content", "pattern_name", "belief_statement", "event_summary"):
        value = row.get(field)
        if value and str(value).strip():
            return str(value).strip()
    return ""


def _is_current(row: Mapping[str, Any]) -> bool:
    """
    Whether a record is still the live version of its idea.

    A record with no status at all counts as current. Not everything in the
    graph is versioned, and treating an observation as superseded because it
    has no lifecycle would make every card look stale.
    """
    status = row.get("status")
    if status is None:
        return True
    return str(status) != LifecycleNodeStatus.SUPERSEDED.value


def _as_moment(raw: Any) -> datetime | None:
    """Read a stored timestamp, treating anything unreadable as absent."""
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
    return None


def _as_count(raw: Any) -> int | None:
    """Read a stored counter, treating anything unreadable as absent."""
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _variants(proposal: FrozenProposal) -> tuple[ProposalVariant, ...]:
    """Every answer a proposal holds, however many that is."""
    if proposal.runner_up is None:
        return (proposal.primary, proposal.fallback)
    return (proposal.primary, proposal.runner_up, proposal.fallback)


__all__ = [
    "standing_alone_choice",
    "wanted_for_items",
    "wanted_node_ids",
    "read_rows",
    "read_episode_summaries",
    "build_card",
    "build_unanswerable_card",
    "offered_choices",
]
