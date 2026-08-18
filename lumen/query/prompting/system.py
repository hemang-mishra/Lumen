"""
Putting the instructions together.

Five parts, always in the same order, and the order is doing work. The
assistant reads who it is before how to behave, how to behave before what it
knows, and what it knows before where the conversation has got to — so that
by the time it reaches the person's own words it has already been told how
to hold them.

Anything empty is left out rather than included as an empty heading. A
heading with nothing under it is not neutral: "what you know about them"
followed by silence reads as a claim that there is nothing to know.

There is one fork. When somebody is in acute distress the whole thing is
replaced by a shorter instruction, because the ordinary one asks for
curiosity and pattern-noticing, and that is the wrong thing to ask for at
that moment even with no notes attached.
"""

from __future__ import annotations

from lumen.query.assembly import block
from lumen.query.assembly.contracts import AssembledContext
from lumen.query.prompting import persona

SUMMARY_HEADING = "[WHERE THIS CONVERSATION HAS GOT TO]"


def build_system_prompt(
    context: AssembledContext,
    *,
    summary: str | None = None,
    in_crisis: bool = False,
) -> str:
    """
    The instructions the assistant gets for this turn.

    The crisis form takes no notes and no summary. Both would invite exactly
    the sort of stepping-back that the moment does not want, and a summary of
    the last hour is not what somebody in the middle of a bad ten minutes
    needs reflected at them.
    """
    if in_crisis:
        return persona.CRISIS_INSTRUCTION

    sections = [
        persona.IDENTITY,
        persona.HOW_TO_BE,
        _notes(context),
        _summary(summary),
        persona.SAFETY,
    ]
    return "\n\n".join(section for section in sections if section)


def _notes(context: AssembledContext) -> str:
    """
    The briefing, with the instruction for using it.

    Both or neither. Telling the assistant how to handle notes it does not
    have wastes its attention on a rule with nothing to apply to, and — worse
    — implies there were notes and they were withheld.

    The one exception is a briefing that is empty because the history could
    not be reached. That renders on its own, without the instruction for
    using notes: there are none to use, and the thing being corrected is a
    conclusion the assistant would otherwise draw from the silence.
    """
    rendered = block.render(context)
    if not rendered:
        return ""
    if context.is_empty:
        return rendered
    return f"{persona.HOW_TO_USE_THE_NOTES}\n\n{rendered}"


def _summary(summary: str | None) -> str:
    """Where the conversation has got to, when it has got anywhere."""
    text = (summary or "").strip()
    if not text:
        return ""
    return f"{SUMMARY_HEADING}\n{text}"


__all__ = ["build_system_prompt", "SUMMARY_HEADING"]
