"""
Putting the counts and the sentences together into one finished report.

The two halves have been kept apart for the whole of the package, and this is
where they meet — every number produced by the arithmetic, every sentence
produced by the model, joined by identifier so that no sentence can end up
attached to the wrong figure.

The output is a plain dictionary rather than a typed object, because that is
what a report is: a document, stored whole, read back years later by something
that has not been written yet. A schema version is stamped on it so a future
reader can tell which shape it is looking at without guessing from the keys.

Nothing here writes anything. It builds the record and hands it back.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from lumen.pipeline.macroextraction.contracts import (
    ComputedFacts,
    MacroWindow,
    NarrativeResult,
    ShadowFinding,
    ShadowNarrative,
)
from lumen.schemas.enums import (
    NarrativeStatus,
    ReportStatus,
    ReportType,
    SignalStrength,
)
from lumen.schemas.ids import make_slug_node_id
from lumen.schemas.nodes import MacroextractionReportNode

logger = logging.getLogger(__name__)

# Which shape of report this is. Stamped on every document so that a reader
# years from now can tell what it is looking at rather than inferring it from
# which keys happen to be present.
REPORT_SCHEMA_VERSION = 1

# Sections of the report that are deliberately not produced yet, recorded on
# the document itself. A reader finding no mood chart should be able to tell
# "this was not built" from "this period had no feeling in it".
DEFERRED_SECTIONS: tuple[str, ...] = (
    "emotional_valence",
    "proof_chains",
    "prospective_memory",
)


def report_id_for(window: MacroWindow, *, existing: int = 0) -> str:
    """
    What one period's report is called.

    Derived from the period rather than from the moment of writing, so the
    name says which stretch of life it covers and two runs of the same period
    are obviously the same thing.

    A deliberate re-run gets a suffix instead of replacing what is there.
    Nothing in the graph is ever overwritten, so both survive and whichever
    was written last is the one a reader is shown.
    """
    base = make_slug_node_id(
        "macro",
        f"{window.report_type.value.lower()}_{window.period_start:%Y_%m_%d}",
    )
    if existing <= 0:
        return base
    return f"{base}_r{existing + 1}"


def build(
    facts: ComputedFacts,
    narrative: NarrativeResult,
    *,
    model_used: str,
    existing: int = 0,
) -> tuple[MacroextractionReportNode, tuple[str, ...]]:
    """
    One finished report, and the writing it drew on.

    The episode list comes back separately because it becomes links rather
    than content. A report that says it covered fourteen pieces of writing
    and cannot point at them is an assertion; one that is joined to them is
    a record.
    """
    draft = narrative.draft
    window = facts.window

    content: dict[str, Any] = {
        "meta": _meta(facts, narrative),
        "window": {
            "window_start": window.start_date.isoformat(),
            "window_end": window.end_date.isoformat(),
            "window_type": window.report_type.value.lower(),
            "episodes_analyzed": facts.episodes_analyzed,
        },
        "headline": draft.headline,
        "top_patterns": [
            {
                "pattern_id": item.pattern_id,
                "label": item.label,
                "episode_count": item.episode_count,
                "first_seen": _day(item.first_seen),
                "last_seen": _day(item.last_seen),
            }
            for item in facts.top_patterns
        ],
        "pattern_frequency": [
            {"pattern_id": item.pattern_id, "frequency_pct": item.frequency_pct}
            for item in facts.pattern_frequency
        ],
        "emerging_patterns": [
            {
                "pattern_id": item.pattern_id,
                "label": item.label,
                "first_episode": item.first_episode,
            }
            for item in facts.emerging_patterns
        ],
        "disappearing_patterns": [
            {
                "pattern_id": item.pattern_id,
                "label": item.label,
                "previous_frequency_pct": item.previous_frequency_pct,
                "last_seen": _moment(item.last_reinforced),
            }
            for item in facts.disappearing_patterns
        ],
        "belief_changes": [
            {
                "belief_id": item.belief_id,
                "old_version": item.old_version,
                "old_content": item.old_content,
                "new_version": item.new_version,
                "new_content": item.new_content,
                "delta_description": item.delta_description,
                "evolved_on": _moment(item.evolved_on),
            }
            for item in facts.belief_changes
        ],
        "repeated_lessons": [
            {
                "lesson_id": item.lesson_id,
                "content": item.content,
                "appearance_count": item.appearance_count,
            }
            for item in facts.repeated_lessons
        ],
        "ignored_lessons": [
            {
                "lesson_id": item.lesson_id,
                "content": item.content,
                "last_seen": _day(item.last_seen),
                "days_since_last_seen": item.days_since_last_seen,
            }
            for item in facts.ignored_lessons
        ],
        "biggest_growth_area": _growth(facts, narrative),
        "biggest_struggle": _struggle(facts, narrative),
        "key_relational_dynamics": _relational(facts, narrative),
        "key_environmental_dependencies": _environments(facts, narrative),
        "unresolved_open_loops": [
            {
                "open_loop_id": item.open_loop_id,
                "content": item.content,
                "first_raised": _moment(item.first_raised),
                "days_open": item.days_open,
            }
            for item in facts.unresolved_open_loops
        ],
        "pending_hitl_decisions": {
            "count": facts.pending_review_count,
            "oldest_item_days": facts.pending_review_oldest_days,
        },
        "high_signal_observations": [
            {
                "observation_id": item.observation_id,
                "type": item.type,
                "signal_strength": item.signal_strength,
                "summary": item.content,
                "episode_id": item.episode_id,
            }
            for item in facts.high_signal_observations
        ],
        "motif_of_unprocessed_depth": [
            {
                "pattern_id": item.pattern_id,
                "label": item.label,
                "suppressed_surfacing_count": item.suppressed_surfacing_count,
            }
            for item in facts.unprocessed_motifs
        ],
        "relationship_arcs": _arcs(facts, narrative),
        "biographical_gaps_raised": _gaps(facts, narrative),
        "pattern_aging": _aging(facts),
        "archetype_shift": _shift(facts, narrative),
        "active_contradictions": _contradictions(facts, narrative),
    }

    node = MacroextractionReportNode(
        node_id=report_id_for(window, existing=existing),
        created_at=datetime.now(timezone.utc),
        report_type=window.report_type,
        period_start=window.period_start,
        period_end=window.period_end,
        episodes_analyzed=facts.episodes_analyzed,
        archetype_shift_detected=facts.archetype_shift.detected,
        model_used=model_used or "none",
        status=ReportStatus.IMMUTABLE,
        report_content=content,
    )

    return node, tuple(facts.episode_ids)


def build_shadow(
    window: MacroWindow,
    finding: ShadowFinding,
    narrative: ShadowNarrative,
    *,
    model_used: str,
) -> tuple[MacroextractionReportNode, tuple[str, ...]]:
    """
    One alert about the last couple of days.

    Named by the moment it was raised rather than by a period, because there
    is no period — two of these can be raised in the same week and neither
    supersedes the other.
    """
    content = {
        "meta": {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "narrative_status": NarrativeStatus.OK.value,
        },
        "shadow_micro_shift": {
            "detected": finding.detected,
            "shift_type": narrative.shift_type,
            "trigger_nodes": list(finding.trigger_nodes),
            "summary": narrative.summary,
            "branch_count": finding.branch_count,
            "contradict_count": finding.contradict_count,
            "target_count": finding.target_count,
        },
    }

    node = MacroextractionReportNode(
        node_id=make_slug_node_id(
            "macro", f"shadow_{window.period_end:%Y_%m_%d_%H%M%S}"
        ),
        created_at=datetime.now(timezone.utc),
        report_type=ReportType.SHADOW,
        period_start=window.period_start,
        period_end=window.period_end,
        episodes_analyzed=len(finding.episode_ids),
        archetype_shift_detected=False,
        model_used=model_used or "none",
        status=ReportStatus.IMMUTABLE,
        report_content=content,
    )

    return node, finding.episode_ids


def _meta(facts: ComputedFacts, narrative: NarrativeResult) -> dict[str, Any]:
    """What the reader needs to know about the report itself."""
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "narrative_status": narrative.status.value,
        "dropped_references": narrative.dropped_references,
        "truncated": facts.truncated,
        "deferred_sections": list(DEFERRED_SECTIONS),
    }


def _growth(facts: ComputedFacts, narrative: NarrativeResult) -> dict[str, Any] | None:
    """The improving pattern, with the sentence explaining it."""
    candidate = facts.growth_candidate
    if candidate is None:
        return None
    return {
        "pattern_or_belief_id": candidate.node_id,
        "label": narrative.draft.growth_area_label or candidate.name,
        "evidence": narrative.draft.growth_area_evidence or "",
        "episode_count": candidate.episode_count,
        "previous_episode_count": candidate.previous_episode_count,
    }


def _struggle(facts: ComputedFacts, narrative: NarrativeResult) -> dict[str, Any] | None:
    """
    The pattern that took up most of the period.

    The count of difficult noticings is named for what it is. Nothing in
    Lumen scores how strongly anything was felt, so nothing here reports a
    score — a number that looks measured and was not is worse than no number.
    """
    candidate = facts.struggle_candidate
    if candidate is None:
        return None
    return {
        "pattern_id": candidate.pattern_id,
        "label": narrative.draft.struggle_label or candidate.name,
        "episode_count": candidate.episode_count,
        "negative_observation_count": candidate.negative_observation_count,
    }


def _relational(facts: ComputedFacts, narrative: NarrativeResult) -> list[dict[str, Any]]:
    """Each person the period involved, with a sentence where one was written."""
    written = {
        item.person_ref: item.dynamic_summary
        for item in narrative.draft.relational_summaries
    }
    return [
        {
            "person_ref": item.person_ref,
            "dynamic_summary": written.get(item.person_ref, ""),
            "observation_count": item.observation_count,
        }
        for item in facts.relational_dynamics
    ]


def _environments(
    facts: ComputedFacts, narrative: NarrativeResult
) -> list[dict[str, Any]]:
    """
    The places the person leans on, as the model grouped them.

    The confidence on each group is worked out here from how weighty the
    noticings underneath it were, rather than being asked of the model. A
    model's own estimate of its confidence is a sentence about itself, not a
    measurement of the evidence.
    """
    weight = {
        item.observation_id: item.signal_strength
        for item in facts.environment_observations
    }
    groups = []
    for group in narrative.draft.environment_groups:
        strengths = [weight.get(node_id, "") for node_id in group.observation_ids]
        strong = sum(
            1
            for value in strengths
            if value in (SignalStrength.HIGH.value, SignalStrength.CRITICAL.value)
        )
        groups.append(
            {
                "environment": group.environment,
                "dependency": group.dependency,
                "observation_ids": list(group.observation_ids),
                "confidence": "high" if strong * 2 >= len(strengths) and strengths else "medium",
            }
        )
    return groups


def _arcs(facts: ComputedFacts, narrative: NarrativeResult) -> list[dict[str, Any]]:
    """
    Each relationship that ran through the period.

    The direction is taken from what the person's own record already says
    where it says anything, and only otherwise from the model. A stored trend
    was built from every mention across all time; a model reading one period
    is guessing at the same thing with less to go on.
    """
    written = {item.person_id: item for item in narrative.draft.relationship_arcs}
    arcs = []
    for item in facts.relationship_arcs:
        sentence = written.get(item.person_id)
        direction = item.stored_direction or (
            sentence.arc_direction if sentence else None
        )
        arcs.append(
            {
                "person_id": item.person_id,
                "canonical_name": item.canonical_name,
                "episodes_in_window": item.episodes_in_window,
                "arc_summary": sentence.arc_summary if sentence else "",
                "dominant_observation_types": list(item.dominant_observation_types),
                "arc_direction": direction.value if direction else None,
            }
        )
    return arcs


def _gaps(facts: ComputedFacts, narrative: NarrativeResult) -> list[dict[str, Any]]:
    """Each missing piece of the story, with where the model judges it stands."""
    judged = {item.observation_id: item for item in narrative.draft.biographical_gaps}
    gaps = []
    for item in facts.biographical_gaps:
        judgement = judged.get(item.observation_id)
        gaps.append(
            {
                "gap_id": item.observation_id,
                "content": item.content,
                "first_raised": _moment(item.first_raised),
                "status": (judgement.status.value if judgement else "PRESENT").lower(),
                "closing_evidence": judgement.closing_evidence if judgement else None,
            }
        )
    return gaps


def _aging(facts: ComputedFacts) -> dict[str, list[dict[str, Any]]]:
    """Quiet patterns, split by how quiet."""
    def entry(item) -> dict[str, Any]:
        row = {
            "pattern_id": item.pattern_id,
            "label": item.label,
            "last_reinforced": _moment(item.last_reinforced),
            "days_since_last_seen": item.days_since_last_seen,
            "current_weight_multiplier": item.weight_multiplier,
        }
        if item.re_interrogation_prompt:
            row["re_interrogation_prompt"] = item.re_interrogation_prompt
        return row

    return {
        "cooling_patterns": [
            entry(item) for item in facts.pattern_aging if item.band.value == "COOLING"
        ],
        "dormant_patterns": [
            entry(item) for item in facts.pattern_aging if item.band.value == "DORMANT"
        ],
    }


def _shift(facts: ComputedFacts, narrative: NarrativeResult) -> dict[str, Any]:
    """
    Whether the period held an identity-level shift, and what to call it.

    The name only appears when the arithmetic found something. A shift with a
    label and no patterns behind it is the single most consequential sentence
    a report can contain, and it must never be reachable from prose alone.
    """
    shift = facts.archetype_shift
    written = narrative.draft.archetype_shift

    return {
        "detected": shift.detected,
        "shift_label": written.shift_label if (shift.detected and written) else None,
        "evidence_summary": (
            written.evidence_summary if (shift.detected and written) else None
        ),
        "window": {
            "start": _moment(shift.comparison_start),
            "end": _moment(shift.comparison_end),
        },
        "contributing_patterns": [
            {
                "pattern_id": item.pattern_id,
                "label": item.label,
                "trend": item.trend.value.lower(),
                "recent_count": item.recent_count,
                "earlier_count": item.earlier_count,
            }
            for item in shift.contributing_patterns
        ],
    }


def _contradictions(
    facts: ComputedFacts, narrative: NarrativeResult
) -> list[dict[str, Any]]:
    """Each unresolved tension, with a question to sit with."""
    asked = {
        item.contradiction_id: item.reflection_prompt
        for item in narrative.draft.contradiction_prompts
    }
    return [
        {
            "contradiction_id": item.contradiction_id,
            "belief_a": item.belief_a,
            "belief_b": item.belief_b,
            "summary": item.summary,
            "first_detected": _moment(item.first_detected),
            "still_active": True,
            "days_open": item.days_open,
            "reflection_prompt": asked.get(item.contradiction_id, ""),
        }
        for item in facts.active_contradictions
    ]


def _day(value) -> str | None:
    """A day as text, or nothing."""
    return value.isoformat() if value is not None else None


def _moment(value: datetime | None) -> str | None:
    """A timestamp as text, or nothing."""
    return value.isoformat() if value is not None else None


__all__ = [
    "REPORT_SCHEMA_VERSION",
    "DEFERRED_SECTIONS",
    "report_id_for",
    "build",
    "build_shadow",
]
