"""
What the model is told when reading a live turn.

One instruction, and it asks for a judgement rather than for content: does
this sentence touch anything worth going and finding, and how does the person
sound saying it.

Two things about the instruction do real work.

It is given the person's own era names, because nothing constrains how those
were written down. A model left to answer freely says HIGH_SCHOOL against a
history that recorded "school years", which matches nothing — and matches
nothing *silently*, which is the worst way for a lookup to fail.

It is told which turn to read. The turns before are there so a sentence with
no subject of its own can be understood at all, and it would otherwise be
easy to classify the wrong one — "I don't feel that anymore" is a claim about
change only if you can see what "that" was.
"""

from __future__ import annotations

from collections.abc import Iterable

from lumen.schemas.enums import Domain
from lumen.schemas.query import ChatTurn
from lumen.prompt_rules import AUTHOR_NAMING

SYSTEM_INSTRUCTION = (
    "You are a router for a personal-history system. You read one message "
    "from a conversation and decide whether anything in that person's "
    "recorded history is worth looking up before replying to them. You do "
    "not reply to the person and you do not give advice. You never invent "
    "names, periods or events that are not in what you were given. "
    + AUTHOR_NAMING + " Return only the requested structure."
)


CLASSIFY_PROMPT = """\
Read the conversation below and classify ONLY the final message, the one \
marked CLASSIFY. Earlier messages are there for context and must not be \
classified themselves.

Decide two things.

FIRST — every reason, if any, that this person's recorded history is worth \
searching. Use only these reasons:

{trigger_help}

Return no reasons at all if the message is small talk, an acknowledgement, a \
logistics question, or a question about what was just said. That is the \
common case and it is a correct answer, not a failure.

For each reason give:
  trigger_type — one of the names above
  domain       — one of: {domains}, or null if none applies
  era          — only for HISTORICAL_ERA, and ONLY one of these exact \
names, or null if none of them is what was referred to: {eras}
  people       — only for NAMED_PERSON: the names as the message says them
  keywords     — up to {keyword_limit} words or short phrases from the \
message itself, to search with

SECOND — how the person sounds in this message. Choose exactly one:
  STABLE     — ordinary conversation, steady
  REFLECTIVE — thinking something through, working it out
  VULNERABLE — raw, exposed, close to tears
  CRISIS     — in acute distress, or talking about harming themselves

If it is CRISIS, say so even if the message also gives reasons to search. \
Getting this wrong in the cautious direction costs nothing.

Also return:
  confidence — how sure you are of this reading, 0 to 1
  named_entities — every person named anywhere in the message
  critical_domain_opened — if the person has themselves raised a painful or \
private area of life in this message, name it as one of the domains above; \
otherwise null

CONVERSATION:
{conversation}
"""


# What each reason means, in the words the model is asked to apply.
#
# Written out here rather than taken from the enum's own docstrings: these
# are instructions to a model and are tuned by what it gets wrong, which is a
# different job from explaining the vocabulary to a reader.
TRIGGER_HELP: tuple[tuple[str, str], ...] = (
    (
        "PATTERN_MENTION",
        "they describe a recurring behaviour, feeling or situation — "
        "something that happens to them repeatedly",
    ),
    (
        "BELIEF_CHALLENGE",
        "they question, doubt or contradict something they have held about "
        "themselves or the world",
    ),
    (
        "HISTORICAL_ERA",
        "they refer to a period of their past — school, a job, a city, an "
        "exam, a relationship that ended",
    ),
    ("NAMED_PERSON", "they name a specific person in their life"),
    (
        "SOMATIC_MARKER",
        "they describe a physical sensation — tightness, weight, tears, "
        "heat, a knot",
    ),
    (
        "IDENTITY_STATEMENT",
        "they make a claim about who they are or are not, or what kind of "
        "person they are",
    ),
    (
        "PROGRESS_CLAIM",
        "they claim something has changed for the better, or that something "
        "no longer affects them",
    ),
    (
        "OPEN_LOOP_MATCH",
        "they return to a question they left unfinished, or wonder aloud "
        "about something unresolved",
    ),
)


def render_trigger_help() -> str:
    """The list of reasons, one per line, as the instruction shows them."""
    return "\n".join(f"  {name} — {meaning}" for name, meaning in TRIGGER_HELP)


def render_domains() -> str:
    """Every area of life the system knows, as a comma-separated list."""
    return ", ".join(domain.value for domain in Domain)


def render_eras(eras: Iterable[str]) -> str:
    """
    The person's own era names, or a plain statement that there are none.

    Saying "none recorded" outright matters more than it looks. An empty list
    reads as an open invitation to make one up, and a made-up era is a lookup
    that can never match.
    """
    names = [era for era in eras if era.strip()]
    if not names:
        return "(none recorded — never return an era)"
    return ", ".join(names)


def render_conversation(turns: list[ChatTurn], *, classify_index: int) -> str:
    """
    The conversation as the model sees it, with one turn marked for reading.

    Speakers are labelled by their part in the conversation rather than by
    name, because a name in the transcript is something the model might then
    return as a person who was mentioned.
    """
    lines = []
    for turn in turns:
        speaker = "USER" if turn.role == "user" else "ASSISTANT"
        marker = " [CLASSIFY]" if turn.turn_index == classify_index else ""
        lines.append(f"{speaker}{marker}: {turn.content}")
    return "\n".join(lines)


def build_prompt(
    turns: list[ChatTurn],
    *,
    classify_index: int,
    eras: Iterable[str],
    keyword_limit: int,
) -> str:
    """Fill the instruction in for one turn."""
    return CLASSIFY_PROMPT.format(
        trigger_help=render_trigger_help(),
        domains=render_domains(),
        eras=render_eras(eras),
        keyword_limit=keyword_limit,
        conversation=render_conversation(turns, classify_index=classify_index),
    )


__all__ = [
    "SYSTEM_INSTRUCTION",
    "CLASSIFY_PROMPT",
    "TRIGGER_HELP",
    "build_prompt",
    "render_trigger_help",
    "render_domains",
    "render_eras",
    "render_conversation",
]
