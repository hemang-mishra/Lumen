"""
Building the questions the graph gets asked, and tidying up its answers.

Three jobs live here, all of them fiddly and none of them interesting enough
to read past while looking for something else in the store implementation.

Composing filters is the first. The same question — "beliefs about work from
last spring" — has to be asked slightly differently of every kind of record,
because they do not all keep the same columns. A pattern records the part of
life it belongs to under one name and an episode under another; an
observation records neither. Rather than refuse a filter a record cannot
answer, it is left out for that record and the omission is logged.

Deciding what still counts is the second. A record has a date it became
true, and a link has one plus a date it was withdrawn. Together those answer
"what did this look like in March", which is a filter rather than a feature.

Tidying is the third. The store returns every kind of record in one wide
shape, so reading one back gives back dozens of empty columns and any list
it held as a run of text. What comes out of here is what the record actually
has.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Which column each table keeps a filterable quality in.
#
# Written out per table rather than assumed, because the same idea genuinely
# lives under different names — and asking a table for a column it does not
# have is an error, not an empty result. A filter absent from a table's entry
# here is one that table cannot answer.
FILTER_COLUMNS: dict[str, dict[str, str]] = {
    "EpisodeNode": {"era": "historical_era", "occurred": "occurred_at"},
    "ObservationNode": {
        "signal": "signal_strength",
        "status": "status",
        "occurred": "occurred_at",
    },
    "EventNode": {
        "signal": "signal_strength",
        "status": "status",
        "occurred": "occurred_at",
    },
    "SessionNode": {
        "signal": "signal_strength",
        "status": "status",
        "occurred": "occurred_at",
    },
    "CausalChainNode": {"status": "status"},
    "CausalStepNode": {},
    "PatternNode": {
        "domain": "domain",
        "signal": "signal_strength",
        "status": "status",
        "era": "era_tag",
    },
    "BeliefNode": {
        "domain": "domain",
        "signal": "signal_strength",
        "status": "status",
        "era": "era_tag",
    },
    "LessonNode": {"domain": "domain", "signal": "signal_strength", "status": "status"},
    "AdoptedPrincipleNode": {"domain": "domain", "status": "status"},
    "PersonEntityNode": {"status": "status"},
    "DecisionAuditNode": {"status": "status"},
    "ContradictionNode": {},
    "MacroextractionReportNode": {"status": "status"},
    # An open question records how far it got rather than whether it is
    # live, so there is no plain status column to narrow on.
    "OpenLoopNode": {},
}

# Columns holding a list, stored as a run of text because the store has no
# list column. Read back as lists so a caller never has to know that.
JSON_COLUMNS: frozenset[str] = frozenset(
    {
        "raw_evidence",
        "person_refs",
        "language_tags",
        "overarching_themes",
        "participant_entities",
        "archetype_tags",
        "evidence_episodes",
        "lifecycle_history",
        "parent_belief_ids",
        "aliases",
        "linked_observation_types",
        "merged_from",
        "episodes_analyzed",
        "linked_patterns",
        "linked_beliefs",
        "rollback_pointer",
    }
)

# The date a record became true. Every table that has one calls it this, so
# unlike the rest it needs no per-table lookup.
VALID_FROM = "valid_from"

# The date a record was written down. Every table has one, but it only means
# the same thing as VALID_FROM for records that were never backdated.
CREATED_AT = "created_at"

# The kinds of record that have no date they became true, but can still be
# asked about by date because when they were written *is* when they happened.
# A note of a decision is made at the moment the decision is taken, and a
# report is written at the moment it covers up to.
#
# Deliberately a short opt-in list rather than "everything without a start
# date". A step in a sequence and a person both lack one because they have no
# moment of their own at all, and letting a date filter reach them would
# answer a question they cannot honestly answer.
DATED_BY_CREATION: frozenset[str] = frozenset(
    {"DecisionAuditNode", "MacroextractionReportNode"}
)


def _tables_without_a_start_date() -> frozenset[str]:
    """
    Which kinds of record have no date they became true.

    Read off the schema rather than written out by hand. Asking a table for
    a column it does not have is an error, not an empty result, so a list
    that drifted out of date would turn an ordinary query into a crash — and
    a hand-kept list of five exceptions is exactly the sort of thing that
    drifts.
    """
    from lumen.graph.kuzu_impl import NODE_TABLES

    return frozenset(
        table for table, ddl in NODE_TABLES.items() if f"{VALID_FROM} " not in ddl
    )


class Filters:
    """
    One set of conditions, ready to be asked of a particular table.

    Holds the SQL-ish fragment and the values it refers to together, because
    they are only ever correct as a pair. Building the text without the
    values is how a query ends up asking for a parameter nobody supplied.
    """

    def __init__(self, clause: str, params: dict[str, Any]) -> None:
        self.clause = clause
        self.params = params

    def where(self, *, prefix: str = "WHERE") -> str:
        """The fragment to paste into a query, or nothing if unconditional."""
        return f"{prefix} {self.clause}" if self.clause else ""

    def and_with(self, extra: str) -> str:
        """This set of conditions, plus one more the caller already has."""
        if not self.clause:
            return extra
        return f"{self.clause} AND {extra}"


def build_filters(
    table: str,
    *,
    since: datetime | date | None = None,
    until: datetime | date | None = None,
    domain: str | None = None,
    signal_strength: str | None = None,
    era_tag: str | None = None,
    active_only: bool = True,
    alias: str = "n",
) -> Filters:
    """
    Turn a set of wanted qualities into conditions this table can answer.

    A filter the table has no column for is dropped and logged, rather than
    making the whole request fail. Asked for "beliefs and observations about
    work", the honest answer is the beliefs — an observation does not record
    a part of life, so there is no such thing as an observation about work.
    Failing the request instead would mean the caller had to know the shape
    of every table to ask a simple question.
    """
    available = FILTER_COLUMNS.get(table, {})
    conditions: list[str] = []
    params: dict[str, Any] = {}

    def add(name: str, column: str | None, value: Any, operator: str = "=") -> None:
        if value is None:
            return
        if column is None:
            logger.debug(
                "%s records no %s; that part of the filter does not apply", table, name
            )
            return
        key = f"f_{name}"
        conditions.append(f"{alias}.{column} {operator} ${key}")
        params[key] = _as_text(value)

    dated = date_column(table)
    add("since", dated, since, ">=")
    add("until", dated, until, "<=")
    add("domain", available.get("domain"), domain)
    add("signal", available.get("signal"), signal_strength)
    add("era", available.get("era"), era_tag)

    if active_only:
        conditions.append(active_clause(table, alias=alias))

    return Filters(" AND ".join(conditions), params)


def has_start_date(table: str) -> bool:
    """
    Whether this kind of record carries a date it became true.

    Worked out once from the schema. A step in a sequence belongs to
    whenever its sequence was, a person is not something that starts on a
    date, and the note of a decision records only when it was made.
    """
    return table not in _tables_without_a_start_date()


def date_column(table: str) -> str | None:
    """
    Which column to use when asking this kind of record about time.

    Most records answer with the date they became true. A couple have no such
    date but were written at the moment they describe, so they answer with the
    date they were written instead. The rest have no honest answer and are
    left out of date-bounded questions entirely.
    """
    if has_start_date(table):
        return VALID_FROM
    if table in DATED_BY_CREATION:
        return CREATED_AT
    return None


def active_clause(table: str, *, alias: str = "n") -> str:
    """
    The condition meaning a record of this table is still in play.

    Most answer with a plain status column. An episode has none — it is a
    piece of writing, and writing does not stop having happened — so it is
    always current.
    """
    if "status" not in FILTER_COLUMNS.get(table, {}):
        return "true"
    return f"{alias}.status = 'ACTIVE'"


def as_of_clause(as_of: datetime | date | None, *, alias: str = "n") -> str | None:
    """
    The condition meaning a record already existed on a given date.

    This is the whole of the graph's time travel. Every record carries the
    date it became true, so "what did I believe in March" needs no separate
    history — it is this one comparison.
    """
    return None if as_of is None else f"{alias}.{VALID_FROM} <= $as_of"


def edge_liveness_clause(
    *,
    include_invalidated: bool = False,
    as_of: datetime | date | None = None,
    alias: str = "r",
) -> str:
    """
    The condition meaning a link should be followed.

    A withdrawn link is never deleted, only stamped with the moment it
    stopped applying. Left out of ordinary reads, because a rolled-back
    decision should not still be shaping what the graph appears to say — and
    kept reachable, because "what did this look like before the rollback" is
    a real question once something has gone wrong.

    Asked about a past date, a link withdrawn after that date was still live
    then, and is followed.
    """
    if include_invalidated:
        return "true"
    if as_of is not None:
        return (
            f"({alias}.invalidated_at IS NULL OR {alias}.invalidated_at > $as_of)"
        )
    return f"{alias}.invalidated_at IS NULL"


def tidy_row(row: dict[str, Any]) -> dict[str, Any]:
    """
    Turn one stored row into what the record actually holds.

    Three things happen. The kind of record moves from the store's own
    private column to a plain name. Empty columns are dropped, because every
    table is stored in the same wide shape and most of any row is columns
    belonging to other kinds of record. Lists come back as lists.
    """
    tidied: dict[str, Any] = {}

    for key, value in row.items():
        if key.startswith("_"):
            continue
        if value is None or value == "":
            continue
        tidied[key] = _decode(key, value)

    return tidied


def node_type_of(row: dict[str, Any]) -> str:
    """The kind of record a stored row belongs to."""
    return str(row.get("_label") or "Unknown")


def tidy_edge(
    table: str, from_id: str, to_id: str, properties: dict[str, Any]
) -> dict[str, Any]:
    """
    Turn one stored link into a plain description of it.

    Links have no identifier of their own, so the two ends and the table are
    carried explicitly — that triple is the only way to name a particular
    link, and it is what a rollback has to be told.
    """
    return {
        "edge_type": table,
        "from_node_id": from_id,
        "to_node_id": to_id,
        **{
            key: _decode(key, value)
            for key, value in properties.items()
            if value is not None and value != ""
        },
    }


def _decode(key: str, value: Any) -> Any:
    """
    Read one stored value back into its real shape.

    Only the columns known to hold a list are parsed. Trying it on every
    text column would turn a note that happens to begin with a bracket into
    a list, which is a strange bug to chase.
    """
    if key not in JSON_COLUMNS or not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        logger.debug("column %s was expected to hold a list and did not", key)
        return value


def _as_text(value: Any) -> Any:
    """
    A comparable form of a filter value.

    Dates are stored as text, so a date has to be compared as the same text
    it was written in. The stored form sorts correctly as text, which is why
    comparing dates this way works at all.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def era_key(name: str) -> str:
    """
    A comparable form of an era name.

    Nothing constrains how a period of somebody's past gets written down, so
    the same era arrives as "HIGH_SCHOOL", "high school" and "High-School"
    depending on who wrote it. Capitalisation and punctuation are dropped so
    that all three answer to each other.

    Only ever used for comparing. Whatever is shown or looked up afterwards
    is the spelling the graph really holds, because that is the only one a
    lookup will match.
    """
    collapsed = "".join(
        char.casefold() if char.isalnum() else " " for char in name
    ).split()
    return "_".join(collapsed)


__all__ = [
    "FILTER_COLUMNS",
    "JSON_COLUMNS",
    "VALID_FROM",
    "CREATED_AT",
    "DATED_BY_CREATION",
    "date_column",
    "era_key",
    "has_start_date",
    "Filters",
    "build_filters",
    "active_clause",
    "as_of_clause",
    "edge_liveness_clause",
    "tidy_row",
    "tidy_edge",
    "node_type_of",
]
