"""
Asking a model to write a report's sentences, and checking what comes back.

One call per report. The model is handed the finished counts and asked for
phrasing only, which is why this module can be read in one sitting: build a
brief, make the call, and then throw away anything in the answer that refers
to something that does not exist.

That last step is the reason this is not three lines. A model writing about
somebody's history will occasionally attach a sentence to an identifier it
invented, and an invented identifier fails silently — the sentence reads
perfectly and belongs to nothing. So every reference is checked against the
material the model was given, unknown ones are dropped, and the report records
that it happened.

A failed call is not a failed report. The counts are already finished by the
time this runs, so the report is written either way and simply says that its
prose is missing.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from lumen.config import MacroConfig
from lumen.pipeline.macroextraction.contracts import (
    ComputedFacts,
    NarrativeDraft,
    NarrativeResult,
    ShadowFinding,
    ShadowNarrative,
)
from lumen.pipeline.macroextraction.prompts import (
    NARRATIVE_PROMPT,
    SHADOW_PROMPT,
    SHADOW_SYSTEM_INSTRUCTION,
    SYSTEM_INSTRUCTION,
    render_section,
)
from lumen.providers.errors import ProviderError
from lumen.providers.protocols import LLMProvider
from lumen.schemas.enums import NarrativeStatus

logger = logging.getLogger(__name__)


def write(
    facts: ComputedFacts, *, provider: LLMProvider, config: MacroConfig
) -> NarrativeResult:
    """
    The prose for one report, or an honest account of why there is none.

    Never raises. A report that lost its wording is still a report — every
    figure in it was worked out before this was called — and letting a model
    outage take the whole period with it would mean the period could never be
    covered again, because a period is only ever reported on once.
    """
    brief = build_brief(facts, config=config)
    reply = _request(facts, brief, provider=provider, config=config)

    if reply is None:
        return NarrativeResult(
            status=NarrativeStatus.UNAVAILABLE,
            model_used=getattr(provider, "model_name", ""),
            reason="the model could not be reached or could not be read",
        )

    draft, dropped = keep_known_references(reply, facts)
    if dropped:
        logger.warning(
            "the report's wording referred to things that do not exist",
            extra={
                "dropped": dropped,
                "period_start": facts.window.period_start.isoformat(),
                "report_type": facts.window.report_type.value,
            },
        )

    return NarrativeResult(
        draft=draft,
        status=NarrativeStatus.DEGRADED if dropped else NarrativeStatus.OK,
        model_used=getattr(provider, "model_name", ""),
        dropped_references=dropped,
    )


def write_shadow(
    finding: ShadowFinding,
    decisions: list[dict[str, Any]],
    *,
    provider: LLMProvider,
    config: MacroConfig,
) -> ShadowNarrative:
    """
    The sentence describing a two-day burst.

    Falls back to a plain statement of what was seen rather than to nothing.
    An alert that says "several beliefs shifted" is worth surfacing even when
    the model that would have phrased it better was unavailable.
    """
    rendered = "\n".join(
        f"  - {str(row.get('action') or '')}: "
        f"{str(row.get('delta_description') or row.get('target_node_id') or '')}"
        for row in decisions
    )
    prompt = SHADOW_PROMPT.format(
        hours=config.shadow_window_hours, decisions=rendered or "  - (none recorded)"
    )

    try:
        result = provider.generate_structured(
            prompt, ShadowNarrative, system_instruction=SHADOW_SYSTEM_INSTRUCTION
        )
    except ProviderError as exc:
        logger.warning(
            "could not describe the recent shift, reporting it plainly instead",
            extra={"reason": type(exc).__name__},
        )
        return plain_shadow(finding)

    if result.data is None:
        return plain_shadow(finding)

    try:
        return ShadowNarrative.model_validate(result.data)
    except ValidationError:
        return plain_shadow(finding)


def build_brief(facts: ComputedFacts, *, config: MacroConfig) -> str:
    """
    The material the model is shown, in the order it should read it.

    Ordered by how much a section carries rather than by the order the report
    prints it in. What sits at the top is what the wording will be about, and
    the sections most likely to be trimmed for length should be the ones the
    report can most afford to lose.
    """
    excerpt = max(config.narrative_excerpt_chars, 40)
    blocks = [
        render_section(
            "PATTERNS THAT RECURRED",
            [
                f"- {item.label} [{item.pattern_id}]"
                for item in facts.top_patterns
            ],
        ),
        render_section(
            "PATTERNS SEEN FOR THE FIRST TIME",
            [f"- {item.label} [{item.pattern_id}]" for item in facts.emerging_patterns],
        ),
        render_section(
            "PATTERNS THAT DID NOT APPEAR THIS TIME",
            [
                f"- {item.label} [{item.pattern_id}]"
                for item in facts.disappearing_patterns
            ],
        ),
        render_section(
            "GROWTH CANDIDATE",
            _growth_lines(facts),
        ),
        render_section(
            "MOST FREQUENT PATTERN",
            (
                [f"- {facts.struggle_candidate.name} [{facts.struggle_candidate.pattern_id}]"]
                if facts.struggle_candidate
                else []
            ),
        ),
        render_section(
            "BELIEFS THAT CHANGED SHAPE",
            [
                f"- was: {_clip(item.old_content, excerpt)} | now: "
                f"{_clip(item.new_content, excerpt)}"
                for item in facts.belief_changes
            ],
        ),
        render_section(
            "LESSONS ARRIVED AT REPEATEDLY",
            [f"- {_clip(item.content, excerpt)}" for item in facts.repeated_lessons],
        ),
        render_section(
            "PEOPLE (person_ref — what was noticed)",
            [
                f"- {item.person_ref}: " + " / ".join(
                    _clip(text, excerpt) for text in item.excerpts
                )
                for item in facts.relational_dynamics
            ],
        ),
        render_section(
            "RELATIONSHIPS TO DESCRIBE (person_id, name)",
            [
                f"- [{item.person_id}] {item.canonical_name}: "
                + " / ".join(_clip(text, excerpt) for text in item.excerpts)
                for item in facts.relationship_arcs
            ],
        ),
        render_section(
            "ENVIRONMENT NOTES TO GROUP (observation_id)",
            [
                f"- [{item.observation_id}] {_clip(item.content, excerpt)}"
                for item in facts.environment_observations
            ],
        ),
        render_section(
            "GAPS IN THE STORY (observation_id)",
            [
                f"- [{item.observation_id}] {_clip(item.content, excerpt)}"
                for item in facts.biographical_gaps
            ],
        ),
        render_section(
            "TENSIONS HELD (contradiction_id)",
            [
                f"- [{item.contradiction_id}] {_clip(item.belief_a, excerpt)} "
                f"AGAINST {_clip(item.belief_b, excerpt)}"
                for item in facts.active_contradictions
            ],
        ),
        render_section(
            "QUESTIONS STILL OPEN",
            [
                _clip(f"- {item.content}", excerpt)
                for item in facts.unresolved_open_loops
            ],
        ),
        render_section(
            "WEIGHTY MOMENTS",
            [
                f"- {item.type}: {_clip(item.content, excerpt)}"
                for item in facts.high_signal_observations
            ],
        ),
        render_section("SHIFT DETECTED", _shift_lines(facts)),
    ]

    return _fit(blocks, limit=max(config.narrative_max_chars, 500))


def keep_known_references(
    draft: NarrativeDraft, facts: ComputedFacts
) -> tuple[NarrativeDraft, int]:
    """
    Strip anything the model wrote about something that does not exist.

    Checked against the material the model was actually shown, not against
    the whole graph. A real identifier the model reached for from somewhere
    else is still a sentence about something this report never looked at.

    Returns the cleaned draft and how many references were thrown away, so
    the report can record that its wording is incomplete rather than quietly
    presenting a shortened version as a full one.
    """
    known_people = {item.person_ref for item in facts.relational_dynamics}
    known_person_ids = {item.person_id for item in facts.relationship_arcs}
    known_observations = {
        item.observation_id for item in facts.environment_observations
    } | {item.observation_id for item in facts.biographical_gaps}
    known_contradictions = {
        item.contradiction_id for item in facts.active_contradictions
    }

    dropped = 0

    summaries = [
        item for item in draft.relational_summaries if item.person_ref in known_people
    ]
    dropped += len(draft.relational_summaries) - len(summaries)

    arcs = [item for item in draft.relationship_arcs if item.person_id in known_person_ids]
    dropped += len(draft.relationship_arcs) - len(arcs)

    gaps = [
        item for item in draft.biographical_gaps if item.observation_id in known_observations
    ]
    dropped += len(draft.biographical_gaps) - len(gaps)

    prompts = [
        item
        for item in draft.contradiction_prompts
        if item.contradiction_id in known_contradictions
    ]
    dropped += len(draft.contradiction_prompts) - len(prompts)

    groups = []
    for group in draft.environment_groups:
        kept_ids = [
            node_id for node_id in group.observation_ids if node_id in known_observations
        ]
        dropped += len(group.observation_ids) - len(kept_ids)
        if kept_ids:
            groups.append(group.model_copy(update={"observation_ids": kept_ids}))
        else:
            dropped += 1

    shift = draft.archetype_shift
    if shift is not None and not facts.archetype_shift.detected:
        # A model that names a shift the arithmetic did not find has written
        # the single most consequential line in the report out of nothing.
        shift = None
        dropped += 1

    cleaned = draft.model_copy(
        update={
            "relational_summaries": summaries,
            "relationship_arcs": arcs,
            "biographical_gaps": gaps,
            "contradiction_prompts": prompts,
            "environment_groups": groups,
            "archetype_shift": shift,
        }
    )
    return cleaned, dropped


def _request(
    facts: ComputedFacts,
    brief: str,
    *,
    provider: LLMProvider,
    config: MacroConfig,
) -> NarrativeDraft | None:
    """
    Ask the model, and hand back nothing rather than raising.

    Tried more than once because nothing is waiting on this — the report is
    produced by a schedule, and a second attempt a moment later costs a pause
    nobody is sitting through.
    """
    window = facts.window
    prompt = NARRATIVE_PROMPT.format(
        report_type=window.report_type.value.lower(),
        period_start=window.start_date.isoformat(),
        period_end=window.end_date.isoformat(),
        brief=brief,
    )

    attempts = max(config.narrative_attempts, 1)
    for attempt in range(1, attempts + 1):
        try:
            result = provider.generate_structured(
                prompt, NarrativeDraft, system_instruction=SYSTEM_INSTRUCTION
            )
        except ProviderError as exc:
            _log_attempt(attempt, attempts, "provider_error", type(exc).__name__)
            continue

        if result.data is None:
            _log_attempt(attempt, attempts, "unreadable_response", result.parse_error)
            continue

        try:
            return NarrativeDraft.model_validate(result.data)
        except ValidationError as exc:
            _log_attempt(
                attempt, attempts, "unexpected_shape", f"{exc.error_count()} field errors"
            )

    return None


def _log_attempt(attempt: int, of: int, reason: str, detail: str | None) -> None:
    """Record one failed try at writing a report's wording."""
    logger.warning(
        "could not get the wording for a report",
        extra={"attempt": attempt, "of": of, "reason": reason, "detail": detail},
    )


def _growth_lines(facts: ComputedFacts) -> list[str]:
    """What the model is told about the pattern that improved."""
    candidate = facts.growth_candidate
    if candidate is None:
        return []
    worked_on = []
    if candidate.was_evolved:
        worked_on.append("the belief behind it was revised")
    if candidate.was_regulated:
        worked_on.append("it was interrupted deliberately")
    trailer = f" ({'; '.join(worked_on)})" if worked_on else ""
    return [f"- {candidate.name} [{candidate.node_id}], firing less often{trailer}"]


def _shift_lines(facts: ComputedFacts) -> list[str]:
    """What the model is told about an identity-level shift, if there was one."""
    shift = facts.archetype_shift
    if not shift.detected:
        return []
    return [
        f"- {item.label} [{item.pattern_id}] — {item.trend.value.lower().replace('_', ' ')}"
        for item in shift.contributing_patterns
    ]


def _fit(blocks: list[str], *, limit: int) -> str:
    """
    As much of the brief as will fit, keeping the most important first.

    Trimmed from the end because the blocks are already ordered by weight.
    Dropping a whole section is preferable to cutting one off mid-line: a
    half-written list looks to the model like a complete short one.
    """
    kept: list[str] = []
    used = 0

    for block in blocks:
        if not block:
            continue
        if used + len(block) > limit:
            logger.info(
                "the report's material was trimmed to fit one model call",
                extra={"limit": limit, "kept_sections": len(kept)},
            )
            break
        kept.append(block)
        used += len(block)

    return "\n".join(kept) if kept else "(this period held almost nothing)"


def _clip(text: str, limit: int) -> str:
    """One quoted line, shortened so a long entry cannot crowd out the rest."""
    cleaned = " ".join(str(text).split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def plain_shadow(finding: ShadowFinding) -> ShadowNarrative:
    """
    A description of a burst written without a model.

    Used both when the model call fails and when no model is configured at
    all. An alert saying plainly that several things shifted is worth raising;
    silence because nothing could phrase it nicely is not.
    """
    if finding.contradict_count and finding.branch_count:
        kind = "New directions alongside unresolved tension"
    elif finding.contradict_count:
        kind = "Tension surfacing between things held"
    else:
        kind = "Sudden movement away from established patterns"
    return ShadowNarrative(
        shift_type=kind,
        summary="Several things shifted in this person's recent entries.",
    )


__all__ = [
    "write",
    "write_shadow",
    "plain_shadow",
    "build_brief",
    "keep_known_references",
]
