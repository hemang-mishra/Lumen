"""
Turning a stored record into a sentence somebody can use.

A record in the graph is a row of columns. What the assistant needs is one
or two plain sentences it can absorb mid-conversation without stopping to
parse anything — the difference between

    PatternNode(pattern_name="Avoidance of solo exploration", evidence_count=4,
                era_tag="childhood", signal_strength="HIGH", ...)

and

    Pattern: avoiding going places alone. Seen 4 times. Goes back to childhood.

The second is what a person would say to a colleague before a session. That
is the standard every template here is written to.

Three rules run through all of them.

Dates are said the way people say them — "last Tuesday", "three weeks ago",
"in June". A timestamp in a therapeutic briefing is noise the assistant has
to translate before it can use it.

Quotes are optional, and off when the person is raw. Hearing your own
sentence repeated back during a bad moment lands as being studied rather
than heard, so those templates have a second form that describes instead of
quoting.

An unfamiliar record still becomes a sentence. A kind nobody wrote a
template for falls back to a plain one rather than being dropped, because
losing a genuinely relevant piece of somebody's history over a missing entry
in a table would be the worse failure.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from lumen.query.retrieval.contracts import RetrievedNode

logger = logging.getLogger(__name__)

# How many days count as "recent" before a date is given as a month instead.
RECENT_DAYS = 60


def render(node: RetrievedNode, *, now: datetime, allow_quotes: bool = True) -> str:
    """
    Say what one record is, in a sentence or two.

    The kind of record chooses the wording; an observation additionally
    chooses by what sort of observation it is, because a reframe and a
    self-model are different things wearing the same table.
    """
    template = TEMPLATES.get(node.node_type, _generic)
    text = template(node, now, allow_quotes)
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# One per kind of record
# ---------------------------------------------------------------------------


def _pattern(node: RetrievedNode, now: datetime, allow_quotes: bool) -> str:
    """
    A recurring behaviour, and what usually surrounds it.

    How many times it has been seen is the most useful number in the whole
    briefing: it is the difference between something the person does and
    something they did once.
    """
    parts = [f"Pattern: {_plain(node.preview)}."]

    seen = _count(node, "evidence_count")
    if seen > 1:
        parts.append(f"Seen {seen} times.")

    trigger = _text(node, "typical_trigger")
    outcome = _text(node, "typical_outcome")
    if trigger:
        parts.append(f"Usually starts with {_plain(trigger)}.")
    if outcome:
        parts.append(f"Usually ends with {_plain(outcome)}.")

    if node.era_tag:
        parts.append(f"Goes back to {node.era_tag}.")
    return " ".join(parts)


def _belief(node: RetrievedNode, now: datetime, allow_quotes: bool) -> str:
    """
    Something the person holds to be true, and how long they have.

    Whether it is still current matters as much as the wording: a belief
    they have moved past is useful to know about precisely because they have
    moved past it.
    """
    statement = _quoted(node.preview, allow_quotes)
    held = "Believes" if _is_current(node) else "Used to believe"
    parts = [f"{held}: {statement}."]

    when = _when(node, now)
    if when:
        parts.append(f"Held since {when}.")
    if node.era_tag:
        parts.append(f"Formed around {node.era_tag}.")
    if not _is_current(node):
        parts.append("Since replaced by a later version.")
    return " ".join(parts)


def _open_loop(node: RetrievedNode, now: datetime, allow_quotes: bool) -> str:
    """A question they left open, and when they left it."""
    when = _when(node, now)
    opened = f" from {when}" if when else ""
    return f"Unfinished question{opened}: {_quoted(node.preview, allow_quotes)}."


def _lesson(node: RetrievedNode, now: datetime, allow_quotes: bool) -> str:
    """Something they worked out for themselves."""
    return f"Lesson they drew: {_quoted(node.preview, allow_quotes)}."


def _principle(node: RetrievedNode, now: datetime, allow_quotes: bool) -> str:
    """A rule they have chosen to live by."""
    return f"Principle they have adopted: {_quoted(node.preview, allow_quotes)}."


def _event(node: RetrievedNode, now: datetime, allow_quotes: bool) -> str:
    """Something that happened, and when."""
    when = _when(node, now)
    lead = f"{when.capitalize()}: " if when else "Earlier: "
    return f"{lead}{_plain(node.preview)}."


def _session(node: RetrievedNode, now: datetime, allow_quotes: bool) -> str:
    """A stretch of thinking they did, and roughly when."""
    when = _when(node, now)
    lead = f"Worked through {when}: " if when else "Worked through: "
    return f"{lead}{_plain(node.preview)}."


def _observation(node: RetrievedNode, now: datetime, allow_quotes: bool) -> str:
    """
    One noticed thing.

    Which sentence it becomes depends on what sort of thing was noticed — a
    reframe, a statement about who they are, and a passing feeling are three
    different things stored in one table, and reading them all out the same
    way wastes the distinction the extraction worked to make.
    """
    kind = _text(node, "type")
    shape = OBSERVATION_SHAPES.get(kind, _noticed)
    return shape(node, now, allow_quotes)


def _reframe(node: RetrievedNode, now: datetime, allow_quotes: bool) -> str:
    """A way of seeing something that they arrived at themselves."""
    when = _when(node, now)
    arrived = f" ({when})" if when else ""
    return f"Reframe they reached{arrived}: {_quoted(node.preview, allow_quotes)}."


def _self_model(node: RetrievedNode, now: datetime, allow_quotes: bool) -> str:
    """A belief about the kind of person they are."""
    return f"How they see themselves: {_quoted(node.preview, allow_quotes)}."


def _body(node: RetrievedNode, now: datetime, allow_quotes: bool) -> str:
    """Something their body was doing."""
    return f"In the body: {_plain(node.preview)}."


def _noticed(node: RetrievedNode, now: datetime, allow_quotes: bool) -> str:
    """The plain form, for everything else somebody noticed."""
    when = _when(node, now)
    lead = f"Noted {when}: " if when else "Noted: "
    return f"{lead}{_plain(node.preview)}"


def _generic(node: RetrievedNode, now: datetime, allow_quotes: bool) -> str:
    """
    The fallback, for a kind of record nobody wrote a template for.

    Deliberately dull and deliberately present. A missing row in a table
    should cost a good sentence, never a relevant piece of somebody's
    history.
    """
    logger.debug(
        "a kind of record has no briefing of its own",
        extra={"node_type": node.node_type},
    )
    when = _when(node, now)
    lead = f"From {when}: " if when else "From their history: "
    return f"{lead}{_plain(node.preview)}."


Template = Callable[[RetrievedNode, datetime, bool], str]

# Which wording each kind of record gets. A table rather than a chain of
# conditions, so a new kind is a new row and a missing one is visible.
TEMPLATES: dict[str, Template] = {
    "PatternNode": _pattern,
    "BeliefNode": _belief,
    "OpenLoopNode": _open_loop,
    "LessonNode": _lesson,
    "AdoptedPrincipleNode": _principle,
    "EventNode": _event,
    "SessionNode": _session,
    "ObservationNode": _observation,
}

# And within observations, by what was noticed.
OBSERVATION_SHAPES: dict[str, Template] = {
    "CONCEPTUAL_REFRAME": _reframe,
    "META_BELIEF": _self_model,
    "IDENTITY_FUSION_STATE": _self_model,
    "BELIEF": _self_model,
    "PHYSIOLOGICAL_CAPACITY_STATE": _body,
    "SUPPRESSED_EMOTION_SURFACING": _body,
}


# ---------------------------------------------------------------------------
# Saying things the way people say them
# ---------------------------------------------------------------------------


def humanise_date(when: datetime | None, now: datetime) -> str:
    """
    A date as somebody would actually say it.

    Recent things get a relative form, because that is how they are held in
    mind — "last week" is immediately placed, while a date has to be worked
    out. Anything older gets a month and, once it is not this year, a year:
    at that distance the exact day has stopped meaning anything.
    """
    if when is None:
        return ""

    moment = comparable(when, now)
    days = (now - moment).days

    if days < 0:
        return "just now"
    if days == 0:
        return "earlier today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    if days < 14:
        return "last week"
    if days < RECENT_DAYS:
        return f"{days // 7} weeks ago"
    if moment.year == now.year:
        return f"in {moment:%B}"
    return f"in {moment:%B %Y}"


def _when(node: RetrievedNode, now: datetime) -> str:
    """When this record is from, said plainly, or nothing if it does not say."""
    return humanise_date(node.occurred_at, now)


def comparable(when: datetime, now: datetime) -> datetime:
    """
    Two moments that can be subtracted from each other.

    Records written before time zones were carried consistently come back
    without one. Treating those as being in the same zone as now is a guess,
    and it is the harmless kind: the worst case is a briefing that says
    "yesterday" about something from late the night before.
    """
    if when.tzinfo is None and now.tzinfo is not None:
        return when.replace(tzinfo=now.tzinfo)
    if when.tzinfo is not None and now.tzinfo is None:
        return when.replace(tzinfo=None)
    return when


def _quoted(text: str, allow_quotes: bool) -> str:
    """The person's own words, or a plain reference to them."""
    return f'"{_plain(text)}"' if allow_quotes else _plain(text)


def _plain(text: str) -> str:
    """
    A record's own text, tidied but not rewritten.

    Capitalisation is left exactly as the record has it, and that is a
    decision rather than an omission. Lowering the first letter makes a
    briefing read slightly more like a sentence — and turns "Alex called
    about it" into "alex called about it". Names are the thing a briefing
    about somebody's relationships must not mangle, and there is no reliable
    way to tell a name from an ordinary word at the start of a line. Every
    line here already begins with its own label, so nothing is lost by
    leaving the text alone.
    """
    return text.strip().rstrip(".")


def _text(node: RetrievedNode, column: str) -> str:
    """One of a record's own columns, as text, or empty if it has none."""
    value: Any = node.properties.get(column)
    return str(value).strip() if isinstance(value, str) else ""


def _count(node: RetrievedNode, column: str) -> int:
    """One of a record's own columns, as a whole number."""
    value: Any = node.properties.get(column)
    return value if isinstance(value, int) else 0


def _is_current(node: RetrievedNode) -> bool:
    """Whether this record is still what the person holds."""
    status = _text(node, "status")
    return status in ("", "ACTIVE")


__all__ = [
    "render",
    "humanise_date",
    "comparable",
    "TEMPLATES",
    "OBSERVATION_SHAPES",
    "RECENT_DAYS",
]
