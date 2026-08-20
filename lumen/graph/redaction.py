"""
Which parts of a stored record are somebody's words, and what to put there
instead.

The graph never deletes anything. That is what makes a history trustworthy —
nothing can quietly disappear from it — and it is also why forgetting has to
work some other way. The answer is to overwrite rather than remove: every
field holding what a person said becomes a marker saying it was erased, and
everything that makes the record a record — its identifier, its links, its
dates, its kind, its place in a version chain — stays exactly as it was.

Two things follow from doing it this way. The audit trail still proves what
the system did and when, without saying what any of it was about. And the
erasure cannot be undone, because there is nothing left to undo it from.

Every table is listed below, including the ones that hold no words at all.
An empty entry is a statement that this kind of record says nothing about
anybody, and a table missing entirely is a mistake — a new kind of record
would otherwise keep its text through an erasure and nobody would notice.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# What replaces a field that held words. Dated, so a record can still show
# when it stopped saying anything.
ERASED_TEMPLATE = "[ERASED: {date}]"

# What replaces every one of somebody's other names. Deliberately identical
# for all of them: keeping them apart would leak how many there were.
ERASED_ALIAS = "[ERASED_ALIAS]"

# What replaces a person's name. The hash keeps two different people looking
# like two different people, so the shape of somebody's relationships
# survives, and it cannot be turned back into a name.
ERASED_PERSON_TEMPLATE = "[ERASED_PERSON_{digest}]"
PERSON_DIGEST_LENGTH = 8

# What somebody's relationship to the person becomes. "Manager" and "partner"
# say something about a life even with every name gone.
UNKNOWN_RELATIONSHIP = "UNKNOWN"

# Which columns of each kind of record hold something a person said or wrote.
# Anything not listed is structure: an identifier, a link, a date, a status,
# a counter. Those are what survive an erasure and are the reason the history
# still reads as a history afterwards.
ERASABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "EpisodeNode": ("episode_summary", "historical_era", "overarching_themes"),
    "ObservationNode": ("content", "raw_evidence"),
    "EventNode": ("event_summary", "raw_evidence"),
    "SessionNode": ("session_summary",),
    "CausalChainNode": ("chain_summary",),
    "CausalStepNode": ("content",),
    "PatternNode": (
        "pattern_name",
        "pattern_description",
        "era_tag",
        "archetype_tags",
    ),
    "BeliefNode": (
        "belief_statement",
        "belief_source_summary",
        "version_delta",
        "era_tag",
    ),
    "LessonNode": ("lesson_statement",),
    "AdoptedPrincipleNode": (
        "principle_statement",
        "principle_name",
        "lifecycle_history",
    ),
    # Handled apart from the rest: a name becomes a hash rather than a
    # marker, so two people stay two people.
    "PersonEntityNode": ("canonical_name", "aliases", "relationship_to_user"),
    "DecisionAuditNode": ("delta_description", "hitl_resolution_user_choice"),
    "ContradictionNode": ("contradiction_summary",),
    "MacroextractionReportNode": ("report_content",),
    "OpenLoopNode": ("loop_description", "resolution_summary"),
}

# The one table whose name column becomes a hash rather than a marker.
PERSON_TABLE = "PersonEntityNode"

# Erasable columns that hold a list of separate things rather than one piece
# of text. Overwriting one with a bare marker would leave a column that is
# supposed to hold a list holding a sentence, which every reader of that row
# then has to cope with.
LIST_COLUMNS: frozenset[str] = frozenset(
    {"raw_evidence", "overarching_themes", "archetype_tags", "aliases"}
)

# Erasable columns holding a list of records rather than of words, and which
# part of each record is the words. The dates and states around them are the
# shape of the thing and survive; only what somebody wrote is replaced.
STRUCTURED_COLUMNS: dict[str, tuple[str, ...]] = {
    "lifecycle_history": ("reason",),
}


def erased_marker(at: datetime) -> str:
    """What a field that held words says once it no longer does."""
    return ERASED_TEMPLATE.format(date=at.date().isoformat())


def person_placeholder(name: str) -> str:
    """
    A stand-in for somebody's name that cannot be read back into one.

    The same name always produces the same stand-in, which is what keeps
    twelve mentions of one person looking like one person rather than
    twelve. There is no way back: a hash of a name is not a name.
    """
    digest = hashlib.sha256(name.strip().encode("utf-8")).hexdigest()
    return ERASED_PERSON_TEMPLATE.format(digest=digest[:PERSON_DIGEST_LENGTH])


def needs_the_row(table: str) -> bool:
    """
    Whether erasing this kind of record means reading it first.

    Most kinds do not: every column becomes the same marker, so one
    statement can rewrite a whole batch. Two kinds do — a person, whose new
    name is derived from their old one, and anything holding a list of
    records whose shape has to survive.
    """
    if table == PERSON_TABLE:
        return True
    return any(column in STRUCTURED_COLUMNS for column in columns_for(table))


def replacements_for(
    table: str, row: Mapping[str, Any] | None, *, at: datetime
) -> dict[str, str]:
    """
    What every erasable column of this record should say instead.

    One place decides all of it, so a caller never has to know which columns
    hold a sentence, which hold a list, and which hold a person's name. The
    row is only consulted for the kinds that need it; for everything else it
    can be left out entirely.
    """
    marker = erased_marker(at)
    values: dict[str, str] = {}

    for column in columns_for(table):
        if table == PERSON_TABLE and column == "canonical_name":
            values[column] = person_placeholder(
                str((row or {}).get("canonical_name") or "")
            )
        elif table == PERSON_TABLE and column == "aliases":
            values[column] = json.dumps([ERASED_ALIAS])
        elif table == PERSON_TABLE and column == "relationship_to_user":
            values[column] = UNKNOWN_RELATIONSHIP
        elif column in STRUCTURED_COLUMNS:
            values[column] = _redacted_records(
                (row or {}).get(column), STRUCTURED_COLUMNS[column], marker
            )
        elif column in LIST_COLUMNS:
            values[column] = json.dumps([marker])
        else:
            values[column] = marker

    return values


def _redacted_records(stored: Any, fields: tuple[str, ...], marker: str) -> str:
    """
    Replace the words inside a list of records and keep the records.

    Something unreadable is replaced wholesale with an empty list rather than
    being left as it was. A column nobody can parse is a column nobody can
    prove is empty of words, and this is the one operation where guessing in
    the other direction is not acceptable.
    """
    try:
        entries = json.loads(stored) if isinstance(stored, str) else stored
    except (TypeError, ValueError):
        entries = None

    if not isinstance(entries, list):
        logger.debug("a structured column could not be read, so it is emptied")
        return json.dumps([])

    redacted = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        kept = dict(entry)
        for name in fields:
            if name in kept:
                kept[name] = marker
        redacted.append(kept)
    return json.dumps(redacted)


def columns_for(table: str) -> tuple[str, ...]:
    """
    Which columns of this kind of record hold words.

    An unknown kind answers nothing rather than raising. A sweep that met a
    table nobody had listed should leave it alone and let the check that
    catches the omission be the one that reports it — refusing halfway
    through would erase part of a history and stop.
    """
    return ERASABLE_COLUMNS.get(table, ())


def holds_words(table: str) -> bool:
    """Whether this kind of record has anything to erase at all."""
    return bool(columns_for(table))


__all__ = [
    "ERASED_TEMPLATE",
    "LIST_COLUMNS",
    "STRUCTURED_COLUMNS",
    "UNKNOWN_RELATIONSHIP",
    "needs_the_row",
    "replacements_for",
    "ERASED_ALIAS",
    "ERASED_PERSON_TEMPLATE",
    "ERASABLE_COLUMNS",
    "PERSON_TABLE",
    "erased_marker",
    "person_placeholder",
    "columns_for",
    "holds_words",
]
