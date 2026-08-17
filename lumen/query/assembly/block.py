"""
How the briefing appears inside the assistant's instructions.

A few sentences of somebody's history, wrapped so the assistant can tell
where they came from and what to do with them. The wrapper does three jobs
and each of them is load-bearing.

It says this is background, not something the person just said. Without
that, an assistant can quote a pattern back as though it had been mentioned
this turn — which is the exact experience of being handed your own file.

It says not to read it out. Most of what goes in here should never be
visible; it should show up as the assistant simply understanding them.

And it says how old it is, when it is old. A briefing that arrived a turn
late is still worth having and is not about what was just said, and the
difference has to be legible or it will be read as current.
"""

from __future__ import annotations

from lumen.query.assembly.contracts import AssembledContext

OPENING = "[WHAT YOU ALREADY KNOW ABOUT THEM — from your notes, not from this chat]"
CLOSING = "[END OF NOTES]"

GUIDANCE = (
    "Let this shape how you listen. Do not read it out, do not list it, and "
    "do not treat it as something they just told you."
)

DEFERRED_NOTE = (
    "These notes were gathered a moment ago, about what they said before this "
    "message. Treat them as slightly behind the conversation."
)


def render(context: AssembledContext) -> str:
    """
    The briefing as a block of text, or nothing at all.

    An empty briefing renders as an empty string rather than as a heading
    with nothing under it. An assistant shown "here is what you know about
    them" followed by silence reads it as the person having no history,
    which is a much stronger claim than "nothing came up this turn".
    """
    if context.is_empty:
        return ""

    lines = [OPENING, GUIDANCE]
    if context.deferred:
        lines.append(DEFERRED_NOTE)
    lines.append("")
    lines.extend(f"- {item.text}" for item in context.items)
    lines.append(CLOSING)
    return "\n".join(lines)


__all__ = ["render", "OPENING", "CLOSING", "GUIDANCE", "DEFERRED_NOTE"]
