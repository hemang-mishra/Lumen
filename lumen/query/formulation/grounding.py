"""
Checking that what the model claimed is actually in the graph.

A model reading a turn can name anyone and refer to any period of a life. It
is answering from the sentence in front of it, and it has no way of knowing
what was ever written down. So a reading arrives holding reasons to search
that may point at nothing at all.

Left alone, those cost more than they look. The next stage has about three
seconds to find something before the conversation moves on, and it would
spend that time looking for a person who has no record. Worse, it would find
nothing and report it the same way it reports a person who genuinely has no
history — and nothing anywhere would show the difference.

So every reason is checked against the graph before it leaves, and one that
names something the graph has never heard of is dropped. Each kind of reason
has its own check, kept in a table rather than a chain of conditions, so
adding a kind means adding a row.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from lumen.config import QueryConfig
from lumen.graph.provider import ReadOnlyGraph
from lumen.graph.queries import era_key
from lumen.query.formulation.contracts import RawTrigger, precedence_of
from lumen.query.session import ChatSession
from lumen.schemas.enums import Domain, TriggerType
from lumen.schemas.ids import person_node_id
from lumen.schemas.query import RetrievalTrigger

logger = logging.getLogger(__name__)

OPEN_LOOP_TABLE = "OpenLoopNode"


@dataclass(frozen=True)
class GroundingContext:
    """
    Everything a check needs, gathered once for the whole turn.

    The era names in particular are fetched once per day rather than per
    reason: they only change when the pipeline writes new history, which
    cannot happen while somebody is mid-conversation.
    """

    graph: ReadOnlyGraph
    eras: tuple[str, ...]
    keyword_limit: int


def era_vocabulary(
    graph: ReadOnlyGraph, session: ChatSession, *, config: QueryConfig
) -> tuple[str, ...]:
    """
    The era names this person's history actually uses, fetched once a day.

    A graph that cannot answer gives an empty list, which makes every era
    reason fail its check. That is the right way round: an era nobody can
    confirm is an era no lookup will match, so acting on one buys nothing and
    costs the turn its time.
    """
    if session.era_vocabulary is not None:
        return session.era_vocabulary

    try:
        names = graph.list_era_tags(limit=config.era_vocabulary_limit)
    except Exception:
        logger.warning(
            "could not read the known era names, so era reasons will not ground",
            exc_info=True,
            extra={"session_id": session.session_id},
        )
        names = []

    vocabulary = tuple(name for name in names if name.strip())
    session.remember_era_vocabulary(vocabulary)
    return vocabulary


def ground_triggers(
    raw_triggers: Iterable[RawTrigger], *, context: GroundingContext
) -> tuple[RetrievalTrigger, ...]:
    """
    Keep the reasons that survive their own check, best first.

    Duplicates of the same kind are collapsed, because two reasons of one
    kind describe one search and running it twice finds the same records
    twice.
    """
    kept: dict[TriggerType, RetrievalTrigger] = {}
    for raw in raw_triggers:
        trigger_type = _parse_trigger_type(raw.trigger_type)
        if trigger_type is None or trigger_type is TriggerType.NO_TRIGGER:
            continue
        if trigger_type in kept:
            continue

        grounded = CHECKS[trigger_type](raw, trigger_type, context)
        if grounded is None:
            logger.debug(
                "a reason to search was dropped because the graph has no such thing",
                extra={"trigger_type": trigger_type.value},
            )
            continue
        kept[trigger_type] = grounded

    return tuple(
        sorted(kept.values(), key=lambda item: precedence_of(item.trigger_type))
    )


def parse_domain(value: str | None) -> Domain | None:
    """
    The area of life a word names, or nothing when it names none of them.

    Matched loosely on purpose — a model that answers "self concept" for
    SELF_CONCEPT has understood the question, and refusing that answer over
    an underscore would throw away a correct reading.
    """
    if not value:
        return None
    wanted = era_key(value)
    return next(
        (domain for domain in Domain if era_key(domain.value) == wanted), None
    )


# ---------------------------------------------------------------------------
# The checks, one per kind of reason
# ---------------------------------------------------------------------------


def _check_person(
    raw: RawTrigger, trigger_type: TriggerType, context: GroundingContext
) -> RetrievalTrigger | None:
    """
    Keep only the names that have a record, and drop the reason if none do.

    A person's record has an identifier worked out from their name, so this
    is one direct lookup per name rather than a search. Nicknames are
    therefore not found: "my sister" has no record of its own even when the
    person behind it does. That limit is inherited rather than introduced —
    the pipeline does not join two spellings of one person either.
    """
    found: list[str] = []
    for name in raw.people:
        node_id = _person_record(name, context.graph)
        if node_id is not None:
            found.append(node_id)

    if not found:
        return None
    return RetrievalTrigger(
        trigger_type=trigger_type,
        domain=parse_domain(raw.domain),
        person_node_ids=tuple(dict.fromkeys(found)),
        keywords=_clean_keywords(raw.keywords, context.keyword_limit),
    )


def _check_era(
    raw: RawTrigger, trigger_type: TriggerType, context: GroundingContext
) -> RetrievalTrigger | None:
    """
    Keep the reason only when the period named is one the graph really holds.

    The spelling that goes on the trigger is the graph's own, not the one the
    model returned. Only that one will match anything when the lookup runs.
    """
    if not raw.era:
        return None

    wanted = era_key(raw.era)
    stored = next((era for era in context.eras if era_key(era) == wanted), None)
    if stored is None:
        logger.debug(
            "an era was named that this history does not use",
            extra={"known_eras": len(context.eras)},
        )
        return None

    return RetrievalTrigger(
        trigger_type=trigger_type,
        domain=parse_domain(raw.domain),
        era=stored,
        keywords=_clean_keywords(raw.keywords, context.keyword_limit),
    )


def _check_open_loop(
    raw: RawTrigger, trigger_type: TriggerType, context: GroundingContext
) -> RetrievalTrigger | None:
    """
    Keep the reason only when there is at least one unfinished question to
    match against.

    Existence is the whole check. Which of them this turn resembles is a
    question about meaning, and answering it here would mean doing the
    search twice.
    """
    if not _any_open_loops(context.graph):
        return None
    return _keep(raw, trigger_type, context)


def _keep(
    raw: RawTrigger, trigger_type: TriggerType, context: GroundingContext
) -> RetrievalTrigger | None:
    """
    Keep the reason as it stands.

    Used by the kinds that name nothing in particular — a physical
    sensation, a claim about who somebody is, a claim that something has
    improved. There is nothing to confirm, because these narrow a search by
    the kind of record wanted rather than by pointing at one.

    An area of life that does not parse is cleared rather than fatal: it only
    narrows the search, and a search that is merely wider still works.
    """
    return RetrievalTrigger(
        trigger_type=trigger_type,
        domain=parse_domain(raw.domain),
        keywords=_clean_keywords(raw.keywords, context.keyword_limit),
    )


# Which check each kind of reason gets.
#
# A table rather than a chain of conditions, so that a new kind of reason is
# a new row and an unchecked one is visibly missing rather than quietly
# falling through to whatever the last branch happened to be.
Check = Callable[[RawTrigger, TriggerType, GroundingContext], RetrievalTrigger | None]

CHECKS: dict[TriggerType, Check] = {
    TriggerType.NAMED_PERSON: _check_person,
    TriggerType.HISTORICAL_ERA: _check_era,
    TriggerType.OPEN_LOOP_MATCH: _check_open_loop,
    TriggerType.PATTERN_MENTION: _keep,
    TriggerType.BELIEF_CHALLENGE: _keep,
    TriggerType.SOMATIC_MARKER: _keep,
    TriggerType.IDENTITY_STATEMENT: _keep,
    TriggerType.PROGRESS_CLAIM: _keep,
}


# ---------------------------------------------------------------------------
# Talking to the graph
# ---------------------------------------------------------------------------


def _person_record(name: str, graph: ReadOnlyGraph) -> str | None:
    """
    The identifier of this person's record, if they have one.

    A graph that cannot answer is treated as not knowing them, which loses a
    lookup. The other way round would send the next stage searching for a
    record that may not exist, and it has no way to tell that apart from a
    person with nothing written about them.
    """
    if not name.strip():
        return None
    try:
        node_id = person_node_id(name)
    except ValueError:
        # A name made only of punctuation cannot produce an identifier.
        return None

    try:
        return node_id if graph.get_node(node_id) is not None else None
    except Exception:
        logger.warning(
            "could not check whether a named person has a record", exc_info=True
        )
        return None


def _any_open_loops(graph: ReadOnlyGraph) -> bool:
    """Whether any unfinished question is still open."""
    try:
        return bool(graph.find_nodes([OPEN_LOOP_TABLE], limit=1))
    except Exception:
        logger.warning("could not check for unfinished questions", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Tidying what the model returned
# ---------------------------------------------------------------------------


def _parse_trigger_type(value: str) -> TriggerType | None:
    """The kind of reason a word names, or nothing when it names none."""
    wanted = era_key(value)
    return next(
        (kind for kind in TriggerType if era_key(kind.value) == wanted), None
    )


def _clean_keywords(keywords: Iterable[str], limit: int) -> tuple[str, ...]:
    """Trimmed, deduplicated, and cut to the number one search may use."""
    cleaned = [word.strip() for word in keywords if word and word.strip()]
    unique = list(dict.fromkeys(cleaned))
    return tuple(unique[: max(int(limit), 0)])


def clean_names(names: Iterable[str]) -> tuple[str, ...]:
    """
    Names as the turn said them, tidied but not checked.

    Kept even when no record matches, because a name the graph has never
    seen is worth being able to look at — it is either somebody new or a
    sign the model is hearing names that were never said.
    """
    cleaned = [name.strip() for name in names if name and name.strip()]
    return tuple(dict.fromkeys(cleaned))


__all__ = [
    "GroundingContext",
    "CHECKS",
    "era_vocabulary",
    "ground_triggers",
    "parse_domain",
    "clean_names",
]
