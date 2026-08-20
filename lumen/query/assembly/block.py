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

UNAVAILABLE_HEADING = "[YOUR NOTES ARE UNAVAILABLE THIS TURN]"

UNAVAILABLE_NOTE = (
    "Your record of this person could not be reached for this message. This "
    "does not mean there is nothing in it. Carry on normally from what has "
    "been said in this conversation, do not assume you are meeting them for "
    "the first time, and do not mention that anything is unavailable."
)


# What an alert is introduced as. Worded so the assistant treats it as
# something to hold in mind rather than something to announce — being told
# "you are shifting" by software is not a conversation anybody asked for.
ALERT_HEADING = (
    "Recent change worth being aware of, to hold in mind rather than to raise:"
)


def render(context: AssembledContext) -> str:
    """
    The briefing as a block of text, or nothing at all.

    An empty briefing renders as an empty string rather than as a heading
    with nothing under it. An assistant shown "here is what you know about
    them" followed by silence reads it as the person having no history,
    which is a much stronger claim than "nothing came up this turn".

    Unless the reason it is empty is that the history could not be reached
    at all — and then saying so is the whole point. Silence and "the store
    refused every query" produce an identical empty briefing, and the
    assistant's honest reading of silence is that this person is a stranger.
    """
    if context.is_empty:
        if context.search_failed:
            return _unavailable()
        # An alert on its own is still worth saying. It is a fact about the
        # last two days rather than about what this turn asked for, so a turn
        # that found nothing does not make it untrue.
        return _alert(context.alert) if context.alert else ""

    lines = [OPENING, GUIDANCE]
    if context.deferred:
        lines.append(DEFERRED_NOTE)
    lines.append("")
    lines.extend(f"- {item.text}" for item in context.items)
    if context.alert:
        lines.append("")
        lines.append(ALERT_HEADING)
        lines.append(context.alert)
    lines.append(CLOSING)
    return "\n".join(lines)


def _alert(alert: str) -> str:
    """The alert on its own, for a turn with no history to go with it."""
    return f"{ALERT_HEADING}\n{alert}"


def _unavailable() -> str:
    """
    What the assistant is told when the history could not be reached.

    Written to change how it *reasons* rather than what it says. It must not
    conclude that a person with years of history is somebody it has never
    met, and it must not tell them their file is down — that is an operator's
    problem and repeating it mid-conversation would be alarming and useless.
    """
    return f"{UNAVAILABLE_HEADING}\n{UNAVAILABLE_NOTE}"


__all__ = [
    "render",
    "ALERT_HEADING",
    "OPENING",
    "CLOSING",
    "GUIDANCE",
    "DEFERRED_NOTE",
    "UNAVAILABLE_HEADING",
    "UNAVAILABLE_NOTE",
]
