"""
Reading a stored row back into something usable.

The graph returns every kind of record in one wide shape: the union of all
columns across all tables, so most of any row is empty columns belonging to
other kinds of record. Which column holds the readable text depends on the
kind, and nothing in the row says so directly.

Two parts of Lumen need exactly the same answers to that. The extraction
pipeline reads rows to decide whether a new finding is something the person
has said before; the live conversation layer reads rows to decide what is
worth putting in front of the AI mid-sentence. They ask different questions
and share this one fact — how to read a row — so it lives here rather than
in either of them.

The weighting table is here for the same reason. How much a record counts
follows from the signal strength recorded on it, and two layers disagreeing
about that would mean the same record ranked differently depending on who
was asking.
"""

from __future__ import annotations

from typing import Any

from lumen.schemas.enums import SignalStrength

# Where each kind of record keeps the text worth showing, in the order to
# try. A pattern says what it is in its name, a belief in its statement, an
# observation in its content — the same idea under three column names.
PREVIEW_COLUMNS: tuple[str, ...] = (
    "content",
    "pattern_name",
    "belief_statement",
    "lesson_statement",
    "event_summary",
    "session_summary",
    "chain_summary",
    "episode_summary",
    "principle_statement",
    "loop_description",
    "contradiction_summary",
    "canonical_name",
)

# How much of a record's text to carry. Enough to recognise it by; not so
# much that a handful of candidates crowd out whatever they are being
# compared against.
PREVIEW_LENGTH = 240

# How much more a weighty record is worth when ranking. A realisation that
# changed how someone sees themselves earns its place in a short list ahead
# of a routine note that happens to be worded alike.
SIGNAL_WEIGHT: dict[SignalStrength, float] = {
    SignalStrength.STANDARD: 1.0,
    SignalStrength.HIGH: 1.5,
    SignalStrength.CRITICAL: 2.0,
}

# Kinds of record that are history. Everything else in the graph — audit
# records, decisions, reports — is machinery.
CONTENT_TABLES: frozenset[str] = frozenset(
    {
        "ObservationNode",
        "EventNode",
        "SessionNode",
        "PatternNode",
        "BeliefNode",
        "LessonNode",
        "AdoptedPrincipleNode",
        "OpenLoopNode",
    }
)

# Statuses that mean a record is no longer part of the live picture.
RETIRED_STATUSES: frozenset[str] = frozenset(
    {"SUPERSEDED", "SUSPENDED", "EXTRACTION_FAILED"}
)


def preview_of(row: dict[str, Any]) -> str:
    """
    Find the readable part of a record, whatever kind it is.

    Falls back to the record's own id, so one with no recognised content
    column still produces something usable rather than being silently
    dropped. Losing a real historical match because its table names things
    unusually would be a worse outcome than an ugly preview.
    """
    for column in PREVIEW_COLUMNS:
        text = (row.get(column) or "").strip()
        if text:
            return text[:PREVIEW_LENGTH]
    return str(row.get("node_id", "unknown node"))


def signal_of(row: dict[str, Any]) -> SignalStrength:
    """
    Read how much weight a record carries, defaulting to ordinary.

    Not every kind of record records this. Treating a missing value as
    ordinary is the safe direction: it can only fail to promote something,
    never promote something that did not earn it.
    """
    try:
        return SignalStrength(row.get("signal_strength") or "STANDARD")
    except ValueError:
        return SignalStrength.STANDARD


def is_live_content(row: dict[str, Any]) -> bool:
    """
    Whether a record is history that is still in play.

    Two conditions, and both matter. Machinery is not history, and a record
    that has been superseded or suspended should not be offered as though
    it were still what the person thinks.
    """
    if row.get("_label") not in CONTENT_TABLES:
        return False
    return (row.get("status") or "") not in RETIRED_STATUSES


__all__ = [
    "PREVIEW_COLUMNS",
    "PREVIEW_LENGTH",
    "SIGNAL_WEIGHT",
    "CONTENT_TABLES",
    "RETIRED_STATUSES",
    "preview_of",
    "signal_of",
    "is_live_content",
]
