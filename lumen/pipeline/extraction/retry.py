"""
Asking again for the parts of a reading that came back unusable.

A first reading is often nearly right: eight good findings and one labelled
with a category that does not exist. Throwing the ninth away is a real
loss, and a second, narrower question usually recovers it.

The danger sits in exactly the same place as the value. **Asking again is
asking for output, and a model asked twice will produce something.** Ask it
to support a finding the entry does not support and it will find support.
So which problems are worth re-asking is one fixed table in this file
rather than a judgement made at each call site, and one entry is missing
from that table for a safety reason rather than an efficiency one: a
feeling with no supporting quote is never re-asked, because the correction
would be a direct instruction to produce the missing quote, and the
produced one would pass the check it was meant to fail.

Only the refused items are re-asked. Everything that already passed is left
exactly as it was, so a good finding from the first reading can never come
back worse from the second.

What is given up on is not quietly lost either. A finding that spends every
attempt is kept and marked as failed, so a person can be shown what could
not be read instead of being left with a gap nobody mentions.
"""

from __future__ import annotations

import logging

from lumen.config import PipelineConfig
from lumen.pipeline.extraction import passes
from lumen.pipeline.extraction.assembly import NodeFactory
from lumen.pipeline.extraction.catalog import render_type_dictionary
from lumen.pipeline.extraction.contracts import (
    DropRule,
    ExtractionOutcome,
    RejectedItem,
    ReflectionExtractionResponse,
)
from lumen.pipeline.extraction.prompts import (
    CORRECTION_PROMPT,
    needs_type_dictionary,
    render_correction_items,
)
from lumen.pipeline.extraction.validation import validate_corrections
from lumen.providers.protocols import LLMProvider
from lumen.schemas.pipeline import MicroextractionInput

logger = logging.getLogger(__name__)


# The problems worth asking about again.
#
# This table is the whole safety argument of the file, so it is one frozen
# set rather than a rule spread across call sites. Adding a member is a
# visible change with a test attached, which is the point.
#
# What is deliberately absent, and why:
#
#   EXCLUDED_TYPE          the category needs audio, and no number of
#                          attempts conjures a recording nobody made.
#   TYPE_NOT_ALLOWED_HERE  the wrong reading was run over a thin entry.
#                          Asking again repeats the mistake.
#   CHAIN_TOO_SHORT        a one-step sequence is a finding, not a chain.
#                          Asking again invites padding it into one.
#   OVER_LIMIT             nothing was wrong with the item; there were
#                          simply too many of them.
#   QUOTE_NOT_FOUND        the one that matters. It fires when a thin entry
#                          produced a feeling the person never put into
#                          words. A correction asking for the missing quote
#                          is an instruction to write one, and the written
#                          one would pass. Retrying this rule would turn the
#                          strongest guard in the pipeline into the way
#                          around it.
RETRYABLE_RULES: frozenset[DropRule] = frozenset(
    {
        DropRule.UNKNOWN_TYPE,
        DropRule.UNKNOWN_ENUM_VALUE,
        DropRule.SIGNAL_FLOOR,
        DropRule.UNKNOWN_STEP_TYPE,
        DropRule.EMPTY_CONTENT,
    }
)


def read_with_corrections(
    payload: MicroextractionInput,
    *,
    provider: LLMProvider,
    limits: PipelineConfig,
) -> ExtractionOutcome:
    """
    Read an episode, then ask again about whatever came back unusable.

    Stops as soon as any of four things is true: everything was accepted,
    nothing left is worth asking about, an attempt changed nothing, or the
    allowed attempts are spent. The third of those matters as much as the
    others — a model that returned the same unusable answer once will
    return it again, and spending a third call to watch that happen helps
    nobody.

    One naming factory is shared across every attempt. Each attempt would
    otherwise start counting from one and hand out names the previous
    attempt had already used.
    """
    allowed = max(1, limits.max_extraction_attempts)
    factory = NodeFactory(payload, extraction_model=provider.model_name)
    outcome = passes.read_reflection(
        payload, provider=provider, limits=limits, factory=factory
    )

    while outcome.attempts < allowed:
        if outcome.used_fallback:
            outcome = _read_again(
                outcome, payload, provider=provider, limits=limits, factory=factory
            )
            continue

        outstanding = _worth_asking_again(outcome.rejected)
        if not outstanding:
            break

        corrected = _correct(
            outcome,
            payload,
            outstanding,
            provider=provider,
            limits=limits,
            factory=factory,
        )
        recovered_something = _made_progress(outcome, corrected)
        outcome = corrected
        if not recovered_something:
            break

    return _finish(outcome, payload, factory=factory)


# ---------------------------------------------------------------------------
# One attempt at a time
# ---------------------------------------------------------------------------


def _read_again(
    outcome: ExtractionOutcome,
    payload: MicroextractionInput,
    *,
    provider: LLMProvider,
    limits: PipelineConfig,
    factory: NodeFactory,
) -> ExtractionOutcome:
    """
    Ask for the whole reading again after one that never arrived.

    There is nothing to correct when a call fails, a reply is unreadable,
    or the answer is the wrong shape — so the original question is simply
    put again. Nothing else re-asks these: the model layer only tries again
    for problems that were never about the answer, like a timeout or a busy
    server.
    """
    logger.info(
        "extraction re-reading after an unusable reply",
        extra={"episode_id": payload.episode.episode_id, "attempt": outcome.attempts + 1},
    )
    fresh = passes.read_reflection(
        payload, provider=provider, limits=limits, factory=factory
    )
    return fresh.model_copy(update={"attempts": outcome.attempts + 1})


def _correct(
    outcome: ExtractionOutcome,
    payload: MicroextractionInput,
    outstanding: tuple[RejectedItem, ...],
    *,
    provider: LLMProvider,
    limits: PipelineConfig,
    factory: NodeFactory,
) -> ExtractionOutcome:
    """Ask about the refused items and fold whatever comes back into the reading."""
    response = passes.request(
        provider=provider,
        prompt=_correction_prompt(payload, outstanding),
        response_model=ReflectionExtractionResponse,
        pass_name="correction",
    )
    attempt = outcome.attempts + 1

    if response is None:
        # Nothing came back at all, so every item asked about has simply
        # spent an attempt.
        _log_attempt(payload, attempt=attempt, asked_about=outstanding, recovered=0)
        return outcome.model_copy(
            update={
                "attempts": attempt,
                "rejected": tuple(
                    item.again(rule=DropRule.NOT_CORRECTED) for item in outstanding
                ),
            }
        )

    report = validate_corrections(
        response,
        passes.reflection_context(payload, limits),
        outstanding=outstanding,
    )
    recovered = passes.assemble(
        report, factory=factory, with_anchor=False, attempt=attempt
    )
    _log_attempt(
        payload,
        attempt=attempt,
        asked_about=outstanding,
        recovered=len(recovered.observations) + len(recovered.events) + len(recovered.chains),
    )
    return _merge(outcome, recovered, still_rejected=report.rejected, attempt=attempt)


def _merge(
    outcome: ExtractionOutcome,
    recovered: ExtractionOutcome,
    *,
    still_rejected: tuple[RejectedItem, ...],
    attempt: int,
) -> ExtractionOutcome:
    """
    Add what a correction recovered to what the reading already had.

    Only the refused items were asked about, so nothing here can replace an
    existing finding — recovered items are added beside them. The notes
    from both attempts are kept together, because the record of what went
    wrong is as much a part of the reading as what came out of it.
    """
    return outcome.model_copy(
        update={
            "observations": outcome.observations + recovered.observations,
            "events": outcome.events + recovered.events,
            "chains": outcome.chains + recovered.chains,
            "steps": outcome.steps + recovered.steps,
            "drops": outcome.drops + recovered.drops,
            "rejected": still_rejected,
            "ungrounded": outcome.ungrounded + recovered.ungrounded,
            "attempts": attempt,
        }
    )


def _finish(
    outcome: ExtractionOutcome,
    payload: MicroextractionInput,
    *,
    factory: NodeFactory,
) -> ExtractionOutcome:
    """
    Settle what is left over once no more attempts will be made.

    Items refused for something a retry could have fixed become failed
    findings: they are kept, marked, and handed on so a person can see what
    could not be read. Items refused for something no retry could ever fix
    are simply discarded — a failure record exists to ask someone for help,
    and there is nothing a person can do about a category that needs audio
    the pipeline never had.

    Only findings can be recorded as failed. There is no place in the graph
    for a failed event or a failed sequence, so those are noted in the log
    and go no further.
    """
    leftover = outcome.rejected
    if not leftover:
        return _ensure_anchor(outcome.model_copy(update={"rejected": ()}), factory)

    failed = tuple(
        factory.failed_observation(item)
        for item in _worth_asking_again(leftover)
        if item.item_kind == "observation"
    )
    _log_abandoned(payload, leftover, recorded=len(failed))
    return _ensure_anchor(
        outcome.model_copy(
            update={"failed": failed, "rejected": (), "abandoned": len(leftover)}
        ),
        factory,
    )


def _ensure_anchor(
    outcome: ExtractionOutcome, factory: NodeFactory
) -> ExtractionOutcome:
    """
    Give the episode something to anchor against if it still has nothing.

    An episode whose first reading came back empty is never anchored, since
    there was nothing there to anchor. If a correction then recovered
    something, the anchor has to be minted late — otherwise an episode
    saved by its second attempt would be the one episode a belief could
    never be recorded as changing in.

    A failed finding does not count as something to anchor. It is a record
    that a reading did not work, not a piece of thinking that happened.
    """
    if outcome.sessions or outcome.is_empty:
        return outcome
    return outcome.model_copy(
        update={"sessions": (factory.session_anchor(list(outcome.observations)),)}
    )


# ---------------------------------------------------------------------------
# The small decisions
# ---------------------------------------------------------------------------


def _worth_asking_again(
    rejections: tuple[RejectedItem, ...],
) -> tuple[RejectedItem, ...]:
    """Pick out the refusals a second look could plausibly fix."""
    return tuple(item for item in rejections if item.rule in RETRYABLE_RULES)


def _made_progress(before: ExtractionOutcome, after: ExtractionOutcome) -> bool:
    """True when an attempt actually recovered something."""
    return len(after.observations) + len(after.events) + len(after.chains) > len(
        before.observations
    ) + len(before.events) + len(before.chains)


def _correction_prompt(
    payload: MicroextractionInput, outstanding: tuple[RejectedItem, ...]
) -> str:
    """
    Build the request to fix the refused items.

    The episode goes with it, because a correction still has to be
    answerable from what the person wrote — without it the model would be
    editing labels in the abstract with nothing to check them against.

    The list of categories is repeated only when a category was the
    problem, which is usually because it was not used the first time.
    Sending it for an unrelated mistake would spend most of the prompt
    restating something that was not what went wrong.
    """
    return CORRECTION_PROMPT.format(
        items=render_correction_items(outstanding),
        type_dictionary=(
            f"\nAVAILABLE TYPES\n\n{render_type_dictionary()}\n"
            if needs_type_dictionary(outstanding)
            else ""
        ),
        text=payload.episode.cleaned_text,
    )


def _log_attempt(
    payload: MicroextractionInput,
    *,
    attempt: int,
    asked_about: tuple[RejectedItem, ...],
    recovered: int,
) -> None:
    """
    Record what one correction asked for and what came back.

    Rules and counts only. The correction prompt is built out of the
    person's own writing, and this line must never become a copy of it.
    """
    logger.info(
        "extraction correction attempted",
        extra={
            "episode_id": payload.episode.episode_id,
            "attempt": attempt,
            "asked_about": len(asked_about),
            "rules": sorted({item.rule.value for item in asked_about}),
            "recovered": recovered,
        },
    )


def _log_abandoned(
    payload: MicroextractionInput,
    leftover: tuple[RejectedItem, ...],
    *,
    recorded: int,
) -> None:
    """Say what was given up on, and how much of it leaves a trace."""
    logger.warning(
        "extraction gave up on items",
        extra={
            "episode_id": payload.episode.episode_id,
            "abandoned": len(leftover),
            "recorded_as_failed": recorded,
            "rules": sorted({item.rule.value for item in leftover}),
            "kinds": sorted({item.item_kind for item in leftover}),
        },
    )


__all__ = ["RETRYABLE_RULES", "read_with_corrections"]
