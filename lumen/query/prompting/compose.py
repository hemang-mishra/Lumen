"""
Everything the assistant gets for one turn, in one place.

Four things arrive from four different directions — what was fetched from
the person's history, how they sound, what the conversation has been about,
and what they just said — and exactly one thing leaves: the prompt.

Having a single object for that is the point of this goal. It means the
question "what would the assistant actually see?" has an answer that can be
printed, checked, and argued with before any chat surface exists, rather
than being spread across the code that eventually sends it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from lumen.config import ChatConfig
from lumen.query.assembly import ContextAssembler
from lumen.query.assembly.budget import estimate_tokens
from lumen.query.memory import earlier
from lumen.query.memory.contracts import Recollection
from lumen.query.prompting.contracts import ChatPrompt
from lumen.query.prompting.persona import DEFAULT_PERSONA, Persona
from lumen.query.prompting.system import build_system_prompt
from lumen.query.retrieval.contracts import RetrievalBundle
from lumen.schemas.enums import EmotionalRegister
from lumen.schemas.query import RetrievalSignal

logger = logging.getLogger(__name__)


class PromptComposer:
    """
    Builds the whole prompt for one turn.

    Owns the assembler and nothing else. No store, no model, no clock of its
    own — everything that varies is passed in, which is what makes the output
    a pure function of its inputs and therefore checkable line by line.
    """

    def __init__(self, *, config: ChatConfig | None = None) -> None:
        self._config = config or ChatConfig()
        self._assembler = ContextAssembler(config=self._config)

    def compose(
        self,
        *,
        bundle: RetrievalBundle,
        signal: RetrievalSignal,
        recollection: Recollection,
        now: datetime | None = None,
        deferred: bool = False,
        persona: Persona | None = None,
        alert: str | None = None,
    ) -> ChatPrompt:
        """
        Put the instructions, the briefing and the conversation together.

        In crisis the briefing is not merely empty — the instructions change
        as well, and the summary is left out. Somebody in the middle of a bad
        ten minutes does not need the last hour reflected back at them, and
        the ordinary instructions ask for exactly the kind of stepping-back
        that would produce that.

        The persona is passed in per call rather than held on the composer,
        because there is one composer for the whole process and the wording
        belongs to whoever is talking. Left out, everybody gets the defaults —
        which is what a caller with no notion of who is speaking should get.

        The alert is passed in for a different reason: finding one means
        reading the graph, and nothing on this side of the system touches a
        store.
        """
        context = self._assembler.assemble(
            bundle, signal, now=now, deferred=deferred, alert=alert
        )
        in_crisis = signal.emotional_register is EmotionalRegister.CRISIS
        moment = now or datetime.now(UTC)

        recent_days = (
            ""
            if in_crisis
            else earlier.render(
                recollection.previous_days,
                now=moment,
                max_tokens=self._config.previous_day_tokens,
                chars_per_token=self._config.chars_per_token,
            )
        )
        system = build_system_prompt(
            context,
            summary=None if in_crisis else recollection.summary,
            earlier_days=recent_days,
            in_crisis=in_crisis,
            persona=persona or DEFAULT_PERSONA,
        )
        prompt = ChatPrompt(
            system=system,
            messages=recollection.turns,
            context=context,
            summary=None if in_crisis else recollection.summary,
            estimated_tokens=self._size(system, recollection),
        )
        _log(prompt, signal, recollection)
        return prompt

    def _size(self, system: str, recollection: Recollection) -> int:
        """
        Roughly what the whole prompt costs.

        Counted over the instructions and the turns together, because that is
        the number that matters to whoever pays for the call — and because a
        briefing that fits its own allowance can still make an enormous
        prompt if the conversation behind it is long.
        """
        per_token = self._config.chars_per_token
        return estimate_tokens(system, chars_per_token=per_token) + sum(
            estimate_tokens(turn.content, chars_per_token=per_token)
            for turn in recollection.turns
        )


def _log(
    prompt: ChatPrompt, signal: RetrievalSignal, recollection: Recollection
) -> None:
    """
    One line about what the assistant was sent.

    Worth watching because this is where every earlier stage's work either
    arrives or quietly does not. A run of turns with a briefing of nothing
    and a growing token count is a system doing a great deal of retrieval and
    putting none of it in front of anybody.
    """
    logger.info(
        "a prompt was composed",
        extra={
            "session_id": signal.session_id,
            "turn_index": signal.turn_index,
            "register": signal.emotional_register.value,
            "briefing_lines": len(prompt.context.items),
            "briefing_tokens": prompt.context.estimated_tokens,
            "turns_sent": len(prompt.messages),
            "turns_held": recollection.total_turns,
            "summarised": prompt.summary is not None,
            "total_tokens": prompt.estimated_tokens,
        },
    )


__all__ = ["PromptComposer"]
