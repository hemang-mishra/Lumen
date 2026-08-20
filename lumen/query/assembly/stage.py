"""
Turning what was fetched into what the assistant reads.

Retrieval ends with a ranked list of records. This ends with a handful of
plain sentences that fit an allowance — and the allowance is set by how the
person sounds, because the right amount of somebody's history to hold up in
front of them is not a constant.

The steps are few and the order matters:

  1. If they are in acute distress, nothing. Said out loud, not silently.
  2. Newest first among records that scored the same, so a live thing beats
     an equally-relevant old one. Not a decay curve — that is a later goal —
     just a tie-break.
  3. Each record becomes a sentence, quoting their words or not depending on
     how they sound.
  4. Take what fits; record what did not and why.

Nothing here reads a store or calls a model. It is arithmetic and wording,
which is why the whole thing can be checked without a database.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from lumen.config import ChatConfig
from lumen.query.assembly import block, select, templates
from lumen.query.assembly.budget import estimate_tokens, policy_for
from lumen.query.assembly.contracts import AssembledContext
from lumen.query.retrieval.contracts import RetrievalBundle, RetrievedNode
from lumen.schemas.enums import EmotionalRegister
from lumen.schemas.query import RetrievalSignal

logger = logging.getLogger(__name__)


class ContextAssembler:
    """
    Builds the briefing for one turn.

    An object only so the settings and the clock can be handed in — there is
    no state between turns and no connection to anything. The clock is a
    parameter because every date in a briefing is said relative to now, and a
    test that could not choose "now" could not check any of them.
    """

    def __init__(self, *, config: ChatConfig | None = None) -> None:
        self._config = config or ChatConfig()

    def assemble(
        self,
        bundle: RetrievalBundle,
        signal: RetrievalSignal,
        *,
        now: datetime | None = None,
        deferred: bool = False,
        alert: str | None = None,
    ) -> AssembledContext:
        """
        Compress what was fetched into what this turn can carry.

        `deferred` marks a briefing that arrived too late for the turn it was
        fetched for and is being carried into the next one. It changes
        nothing about what is chosen — only how the block introduces itself,
        since context about a moment that has passed should not be read as
        though it were about this one.

        `alert` is passed in rather than looked up. This object is a pure
        function of what it is handed and reaching a store from inside it
        would be the first exception to that.
        """
        moment = now or datetime.now(UTC)
        policy = policy_for(signal.emotional_register, self._config)
        unreachable = bundle.search_failed
        # A briefing built partly from a search that missed the last
        # turn's deadline is slightly behind the conversation, and the
        # block says so rather than reading as though it were current.
        deferred = deferred or bool(bundle.carried_forward)

        # In crisis nothing is offered, and that includes the alert. Being
        # told the system has noticed you changing, in the middle of a bad
        # ten minutes, is the opposite of what this is for.
        if not policy.injects_anything:
            return self._nothing(
                signal,
                policy.max_tokens,
                deferred=deferred,
                search_failed=unreachable,
            )

        ordered = _ordered(bundle.candidates, moment)
        rendered = [
            (
                node,
                templates.render(node, now=moment, allow_quotes=policy.allow_quotes),
            )
            for node in ordered
        ]
        # The alert is charged to the same allowance as everything else, and
        # taken off the top rather than added afterwards. A line that is
        # exempt from the budget is a line that grows the prompt every time
        # the scan fires.
        spent_on_alert = _alert_cost(alert, self._config)
        kept, dropped = select.choose(
            rendered,
            policy=policy.with_less_room(spent_on_alert),
            config=self._config,
        )

        context = AssembledContext(
            items=tuple(kept),
            dropped=tuple(dropped),
            emotional_register=signal.emotional_register,
            token_budget=policy.max_tokens,
            estimated_tokens=sum(item.tokens for item in kept) + spent_on_alert,
            deferred=deferred,
            alert=alert,
            search_failed=unreachable,
        )
        _log(context, signal, offered=len(bundle.candidates))
        return context

    def _nothing(
        self,
        signal: RetrievalSignal,
        budget: int,
        *,
        deferred: bool,
        search_failed: bool,
    ) -> AssembledContext:
        """
        The empty briefing, for somebody who should not be handed one.

        Recorded as suppressed rather than simply empty. "There was nothing
        to say about this" and "there was plenty and this was not the moment"
        are different facts, and only one of them is about the graph.
        """
        suppressed = signal.emotional_register is EmotionalRegister.CRISIS
        logger.info(
            "no history was put in front of the assistant for this turn",
            extra={
                "session_id": signal.session_id,
                "turn_index": signal.turn_index,
                "register": signal.emotional_register.value,
                "suppressed": suppressed,
            },
        )
        return AssembledContext(
            emotional_register=signal.emotional_register,
            token_budget=budget,
            suppressed=suppressed,
            deferred=deferred,
            search_failed=search_failed,
        )


def _alert_cost(alert: str | None, config) -> int:
    """
    What a line about something shifting costs the briefing's allowance.

    Counted the same way every other line is, so the ceiling means the same
    thing whatever the briefing is made of.
    """
    if not alert:
        return 0
    return estimate_tokens(
        f"{block.ALERT_HEADING}\n{alert}", chars_per_token=config.chars_per_token
    )


def _ordered(
    candidates: tuple[RetrievedNode, ...], now: datetime
) -> list[RetrievedNode]:
    """
    The records in the order they should be offered.

    Retrieval has already ranked them, and age is part of that ranking now,
    so this only settles ties — and it settles them towards the more recent,
    because between two equally relevant things the live one is the one
    worth mentioning.
    """
    return sorted(
        candidates,
        key=lambda node: (node.rank_score, _age_key(node, now)),
        reverse=True,
    )


def _age_key(node: RetrievedNode, now: datetime) -> float:
    """
    How recent a record is, as a number that sorts.

    A record with no date sorts oldest. That is the cautious direction: it
    only ever loses a tie, never wins one it should not have.
    """
    if node.occurred_at is None:
        return float("-inf")
    return templates.comparable(node.occurred_at, now).timestamp()


def _log(context: AssembledContext, signal: RetrievalSignal, *, offered: int) -> None:
    """
    One line about what the assistant was given.

    The gap between what was fetched and what was kept is the number worth
    watching. A briefing that is always at its ceiling means the allowance is
    too small; one that is always half-empty means retrieval is not finding
    much, and neither shows up anywhere else.
    """
    logger.info(
        "a briefing was assembled",
        extra={
            "session_id": signal.session_id,
            "turn_index": signal.turn_index,
            "register": context.emotional_register.value,
            "offered": offered,
            "kept": len(context.items),
            "dropped": len(context.dropped),
            "tokens": context.estimated_tokens,
            "budget": context.token_budget,
            "deferred": context.deferred,
            "search_failed": context.search_failed,
        },
    )


__all__ = ["ContextAssembler"]
