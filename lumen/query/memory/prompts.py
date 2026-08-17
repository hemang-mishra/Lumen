"""
What the model is told when summarising a conversation so far.

The summary is read by the assistant, not by the person, and it exists for
one purpose: so that a sentence forty turns in still makes sense. That
shapes what it has to contain and what it must not.

It has to keep what a later turn might refer back to — what they came in
with, what they realised, what they decided, what is still open. It must not
turn into a clinical write-up: this is a note for continuing a conversation,
not an assessment of a person.

The previous summary is folded in rather than the whole conversation being
re-read. That is what keeps a long chat costing the same as a short one, and
it is the reason the instruction asks for the earlier summary and the new
turns to be merged rather than the new ones described.
"""

from __future__ import annotations

from lumen.prompt_rules import AUTHOR_NAMING

SYSTEM_INSTRUCTION = (
    "You keep the thread of an ongoing personal conversation so it can be "
    "continued later. You write short, plain notes for whoever picks the "
    "conversation up. You do not give advice, do not analyse the person, and "
    "do not use clinical language. " + AUTHOR_NAMING + " Return only the "
    "requested structure."
)


SUMMARY_PROMPT = """\
Below is what a conversation had been about, and what has been said since.

Merge them into one short account of the conversation so far, in at most \
{word_limit} words. Write plainly, in the third person, as notes for \
somebody about to continue talking with this person.

Keep:
  - what they came in with, and what they are actually working on
  - anything they realised, decided, or changed their mind about
  - anything left unfinished or unanswered
  - the emotional shape of it, in ordinary words

Leave out:
  - anything the assistant said, unless the person responded to it
  - pleasantries, and any part that went nowhere
  - judgements about them, diagnoses, or advice

WHAT IT HAD BEEN ABOUT:
{previous}

WHAT HAS BEEN SAID SINCE:
{recent}
"""

NOTHING_YET = "(nothing yet — this is the start of the conversation)"


def build_prompt(previous: str | None, recent: str, *, word_limit: int) -> str:
    """Fill the instruction in for one refresh."""
    return SUMMARY_PROMPT.format(
        word_limit=max(int(word_limit), 20),
        previous=(previous or "").strip() or NOTHING_YET,
        recent=recent.strip(),
    )


def render_turns(turns: list[tuple[str, str]]) -> str:
    """
    The turns to be folded in, as the instruction shows them.

    Speakers are labelled by their part in the conversation rather than by
    name, so a name inside the transcript cannot be mistaken for a label.
    """
    return "\n".join(f"{speaker}: {content}" for speaker, content in turns)


__all__ = [
    "SYSTEM_INSTRUCTION",
    "SUMMARY_PROMPT",
    "NOTHING_YET",
    "build_prompt",
    "render_turns",
]
