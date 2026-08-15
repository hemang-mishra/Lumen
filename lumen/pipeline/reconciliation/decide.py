"""
Asking the models how today relates to the past.

Two calls at most, whatever the size of the entry.

The first covers everything at once with a fast model. A rich entry can hold
twenty findings, and a call each would make this the most expensive step in
the pipeline by a wide margin — for a question that is mostly "is this the
same as that?", which does not need deep reasoning to answer.

The second only happens if the first proposed something that permanently
alters a long-held belief. Those three answers are the hardest to undo, so
they are put to a careful model before they count. That model can confirm,
lower its confidence, or replace the answer with a safer one — but it is
only ever shown the risky items, so it has no way to make anything riskier.

Answers are matched back to findings by their number, never by order of
arrival, and a missing answer is left missing. A decision applied to the
wrong finding does not fail: it confidently merges two unrelated things.
"""

from __future__ import annotations

import logging
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from lumen.pipeline.reconciliation.catalog import ESCALATED_ACTIONS
from lumen.pipeline.reconciliation.contracts import (
    ConfirmedDecision,
    DecisionItem,
    DecisionResponse,
    EscalationResponse,
    ItemDecision,
)
from lumen.pipeline.reconciliation.prompts import (
    ACTION_GUIDE,
    DECISION_PROMPT,
    ESCALATION_PROMPT,
    SYSTEM_INSTRUCTION,
    render_domains,
    render_items,
)
from lumen.providers.errors import ProviderError
from lumen.providers.protocols import LLMProvider
from lumen.schemas.enums import Domain, ReconciliationAction

logger = logging.getLogger(__name__)

_ResponseT = TypeVar("_ResponseT", bound=BaseModel)


def propose(
    items: list[DecisionItem],
    *,
    provider: LLMProvider,
    attempts: int = 2,
) -> DecisionResponse | None:
    """
    Ask once for a reading of every finding in the entry.

    Returns nothing if no readable answer came back after the attempts
    allowed. Nothing is invented to fill the gap — an entry nobody could
    decide about is handed to a person, which is slower but true.
    """
    if not items:
        return DecisionResponse()

    prompt = DECISION_PROMPT.format(
        action_guide=ACTION_GUIDE,
        domains=render_domains([domain.value for domain in Domain]),
        items=render_items(items),
    )
    return _ask(
        provider=provider,
        prompt=prompt,
        response_model=DecisionResponse,
        step="decision",
        attempts=attempts,
    )


def confirm(
    items: list[DecisionItem],
    proposals: list[ItemDecision],
    *,
    provider: LLMProvider,
    attempts: int = 2,
) -> dict[int, ConfirmedDecision]:
    """
    Put the high-consequence readings to a more careful model.

    Returns a verdict per item number. An item with no verdict keeps the
    first reading — which the confidence bar for these actions is high
    enough to hold back on its own, so a failed second opinion errs toward
    asking a person rather than toward acting.
    """
    if not items or not proposals:
        return {}

    prompt = ESCALATION_PROMPT.format(
        action_guide=ACTION_GUIDE,
        items=_render_escalated(items, proposals),
    )
    response = _ask(
        provider=provider,
        prompt=prompt,
        response_model=EscalationResponse,
        step="escalation",
        attempts=attempts,
    )
    if response is None:
        return {}

    return {verdict.item_index: verdict for verdict in response.verdicts}


def needs_confirming(decision: ItemDecision) -> bool:
    """True when a reading is one of the three a fast model cannot settle."""
    try:
        action = ReconciliationAction(decision.primary.action.strip().upper())
    except ValueError:
        return False
    return action in ESCALATED_ACTIONS


def align(
    response: DecisionResponse, item_count: int
) -> dict[int, ItemDecision]:
    """
    Match every answer to the finding it belongs to, by number.

    Answers with a number nobody asked about are dropped, and findings with
    no answer simply have none — they are settled as unreadable further on.
    Filling a gap by shifting the next answer up would attach a confident
    decision to the wrong finding, which is worse than having no decision at
    all, because nothing about it looks wrong afterwards.
    """
    matched: dict[int, ItemDecision] = {}
    for decision in response.decisions:
        if 1 <= decision.item_index <= item_count:
            matched.setdefault(decision.item_index, decision)
        else:
            logger.debug(
                "decision answer ignored: no item numbered %s", decision.item_index
            )
    return matched


def _render_escalated(
    items: list[DecisionItem], proposals: list[ItemDecision]
) -> str:
    """Lay out the risky readings with what the first model said about each."""
    by_index = {decision.item_index: decision for decision in proposals}
    blocks: list[str] = []
    for position, item in enumerate(items, start=1):
        proposed = by_index.get(position)
        if proposed is None:
            continue
        blocks.append(
            f"{position}. [{item.node_type}] {item.text}\n"
            f"   proposed: {proposed.primary.action} "
            f"on {proposed.primary.target_node_id} "
            f"(confidence {proposed.primary.confidence:.2f})\n"
            f"   reasoning given: {proposed.primary.reason}\n"
            f"   the existing record:\n"
            + "\n".join(
                f"     - id={c.node_id} [{c.node_type}] {c.content_preview}"
                for c in item.candidates
                if c.node_id == proposed.primary.target_node_id
            )
        )
    return "\n\n".join(blocks)


def _ask(
    *,
    provider: LLMProvider,
    prompt: str,
    response_model: type[_ResponseT],
    step: str,
    attempts: int,
) -> _ResponseT | None:
    """
    Ask for a readable answer, repeating the same request if none comes.

    The request is repeated rather than corrected, because there is nothing
    to correct: the failure is a dropped call or a reply that was not the
    shape asked for, not a wrong judgement. Nothing about the entry is
    logged — only which step failed and why.
    """
    for attempt in range(1, max(attempts, 1) + 1):
        try:
            result = provider.generate_structured(
                prompt, response_model, system_instruction=SYSTEM_INSTRUCTION
            )
        except ProviderError as exc:
            _log_failure(step, "provider_error", type(exc).__name__, attempt)
            continue

        if result.data is None:
            _log_failure(step, "unparseable_response", result.parse_error, attempt)
            continue

        try:
            return response_model.model_validate(result.data)
        except ValidationError as exc:
            _log_failure(
                step, "unexpected_shape", f"{exc.error_count()} field errors", attempt
            )

    return None


def _log_failure(step: str, reason: str, detail: str | None, attempt: int) -> None:
    """Record that a call failed, without recording what was being decided."""
    logger.warning(
        "reconciliation could not read the model's answer",
        extra={
            "reconciliation_step": step,
            "reason": reason,
            "detail": detail,
            "attempt": attempt,
        },
    )


__all__ = ["propose", "confirm", "needs_confirming", "align"]
