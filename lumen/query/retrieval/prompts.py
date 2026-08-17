"""
What the model is told when writing the text a turn is searched with.

Searching somebody's history with the sentence they just said works badly,
and the reason is worth stating plainly: the sentence is spoken, mid-thought,
to another person, while the record that would answer it was written down
afterwards as a settled conclusion. Those two are not written alike, so the
closest stored record to a spoken sentence is often nothing in particular.

The trick is to invent the record first and search with that instead. The
model is asked to write, for each reason the turn gave, the entry this
person would plausibly have written about it in the past — and that
invention is thrown away the moment it has been turned into a vector.

One call covers every reason. A turn produces at most three, and a call each
would put a model round-trip on the critical path three times for one
sentence.
"""

from __future__ import annotations

from collections.abc import Sequence

from lumen.prompt_rules import AUTHOR_NAMING
from lumen.schemas.query import RetrievalTrigger

SYSTEM_INSTRUCTION = (
    "You write short, plausible journal entries in the voice of somebody "
    "reflecting on their own life. What you write is never shown to anyone "
    "and is never treated as true — it is used only to search that person's "
    "own past writing. You do not reply to the person, give advice, or ask "
    "questions. " + AUTHOR_NAMING + " Return only the requested structure."
)


HYDE_PROMPT = """\
Somebody said this in a conversation:

"{turn}"

For each numbered item below, write one or two sentences in the style of a \
private journal entry — the kind of thing this person might have written \
about that subject at some point in the past. Write it as settled \
reflection, not as speech: past tense, first person, no questions and no \
advice.

Use only what the item and the message give you. Do not invent names, \
places, dates or events that are not there.

ITEMS:
{items}

Return one entry per item, numbered to match.
"""


def render_items(triggers: Sequence[RetrievalTrigger]) -> str:
    """
    The reasons to search, as the numbered list the instruction asks about.

    Each line carries the kind of reason and whatever the reason narrowed
    itself to — the area of life, the period, the words from the turn. That
    is what makes two reasons from one sentence produce two different
    searches rather than the same one twice.
    """
    lines: list[str] = []
    for position, trigger in enumerate(triggers, start=1):
        parts = [f"{position}. {_readable(trigger.trigger_type.value)}"]
        if trigger.domain is not None:
            parts.append(f"about {_readable(trigger.domain.value)}")
        if trigger.era:
            parts.append(f"during {trigger.era}")
        if trigger.keywords:
            parts.append(f"— {', '.join(trigger.keywords)}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def build_prompt(turn_text: str, triggers: Sequence[RetrievalTrigger]) -> str:
    """Fill the instruction in for one turn."""
    return HYDE_PROMPT.format(turn=turn_text.strip(), items=render_items(triggers))


def own_words(turn_text: str, trigger: RetrievalTrigger) -> str:
    """
    What to search with when nothing could be invented.

    The turn itself plus whatever the reason narrowed to. A worse search than
    an invented record, and a real one — which is the whole point, because a
    search that does not run looks exactly like a person with no history on
    the subject.
    """
    extras = [word for word in trigger.keywords if word.strip()]
    if trigger.era:
        extras.append(trigger.era)
    joined = " ".join(extras)
    return f"{turn_text.strip()} {joined}".strip() if joined else turn_text.strip()


def _readable(value: str) -> str:
    """An enum's name in words, so the instruction does not read like code."""
    return value.replace("_", " ").lower()


__all__ = [
    "SYSTEM_INSTRUCTION",
    "HYDE_PROMPT",
    "build_prompt",
    "render_items",
    "own_words",
]
