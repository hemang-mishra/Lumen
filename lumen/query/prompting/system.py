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
from lumen.query.prompting import persona as persona_module
from lumen.query.prompting.persona import DEFAULT_PERSONA, Persona

SUMMARY_HEADING = "[WHERE THIS CONVERSATION HAS GOT TO]"


def build_system_prompt(
    context: AssembledContext,
    *,
    summary: str | None = None,
    earlier_days: str = "",
    in_crisis: bool = False,
    persona: Persona = DEFAULT_PERSONA,
) -> str:
    """
    The instructions the assistant gets for this turn.

    The crisis form takes no notes, no summary and no earlier days. All three
    would invite exactly the sort of stepping-back that the moment does not
    want, and the last week reflected back is not what somebody in the middle
    of a bad ten minutes needs. It also takes nothing from the person's own
    wording — the crisis instruction is the one part of this nobody can
    edit, and a moment somebody is drowning in is not the moment to find out
    what they typed into a settings box last spring.

    The order is doing work. The assistant reads who it is, then how to
    behave, then what it knows about the person, then where they have been
    recently, then where this conversation has got to — so by the time it
    reaches their words it has already been told how to hold them.

    Safety comes last, after anything the person wrote. Position matters
    when part of the instruction is theirs: an edited section that trails
    off, contradicts itself, or tries to talk the assistant out of something
    is still followed by the paragraph about what to do when they are in real
    distress.

    Args:
        context: The briefing from their history.
        summary: Where this conversation has got to, if anywhere.
        earlier_days: The last few days, already rendered.
        in_crisis: Whether this turn gets the crisis instruction instead.
        persona: Who the assistant is and how it behaves — theirs to change,
            defaulting to the wording in `persona.py`.
    """
    if in_crisis:
        return persona_module.CRISIS_INSTRUCTION

    sections = [
        persona.identity,
        persona.how_to_be,
        _notes(context, persona),
        earlier_days,
        _summary(summary),
        persona_module.SAFETY,
    ]
    return "\n\n".join(section for section in sections if section)


def _notes(context: AssembledContext, persona: Persona) -> str:
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
    return f"{persona.how_to_use_the_notes}\n\n{rendered}"


def _summary(summary: str | None) -> str:
    """Where the conversation has got to, when it has got anywhere."""
    text = (summary or "").strip()
    if not text:
        return ""
    return f"{SUMMARY_HEADING}\n{text}"


__all__ = ["build_system_prompt", "SUMMARY_HEADING"]
