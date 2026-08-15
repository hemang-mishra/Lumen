"""
The instruction given to the language model when writing search text.

There is only one prompt here, and it asks for something unusual: an
invented record. The point is that a question and its answer are almost
never written alike. "Why do I keep checking how everyone else is doing?"
and "seeking external validation through social comparison" share barely a
word, and searching for the first will not find the second. Writing a
plausible version of the answer and searching with *that* closes most of
the gap.

The obvious danger is that an invented record can invent details. It is
never stored and nobody ever reads it — it exists for a moment, becomes a
vector, and is thrown away. But an invented specific still steers the
search toward history the person does not have, so the instruction is
built to keep the fabrication general: same shape, same vocabulary, no new
facts.
"""

from __future__ import annotations

SYSTEM_INSTRUCTION = (
    "You help search a personal history. You write short, plausible "
    "versions of what an earlier record might have said, purely so they can "
    "be compared against real ones. You never add facts, names, dates or "
    "events that were not given to you. Return only the requested structure."
)


HYDE_PROMPT = """\
Below are things noticed in one journal entry, numbered.

For each one, write a single sentence as it might appear in this person's \
earlier history if they had noticed the same thing before. Write it as a \
settled record of theirs, in plain past-tense statement form — not as a \
question, and not as a note about today.

Keep to what you are given. Use the same vocabulary and the same level of \
detail. Do not add a cause, a name, a date, a place, or an outcome that is \
not there. If an item is thin, write a thin sentence; padding it out with \
invented specifics sends the search looking for a history this person does \
not have.

Return exactly one sentence per item, numbered to match. If you cannot write \
one for an item, return an empty string for it and keep its number.

ITEMS:
{items}
"""


def render_targets(texts: list[str]) -> str:
    """
    Number the extracted nodes for the prompt.

    The numbers are how each answer is matched back to the node it belongs
    to afterwards, so they start at 1 and match the positions exactly.
    """
    return "\n\n".join(
        f"{index}. {text}" for index, text in enumerate(texts, start=1)
    )


__all__ = ["SYSTEM_INSTRUCTION", "HYDE_PROMPT", "render_targets"]
