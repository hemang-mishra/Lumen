"""
Holding back the records that must not arrive uninvited.

A record marked CRITICAL is the heaviest material in somebody's history —
the realisation that reorganised how they see themselves, the thing they
circled for a year before writing down. It is tempting to treat that as
"most important, therefore surface it first". The opposite is correct. The
higher the signal, the more deliberately it has to be gated, because
handing it to an AI mid-conversation means it may be reflected back at
somebody who had not gone anywhere near it today.

So a CRITICAL record about a sensitive area of life is withheld until the
person raises that area themselves. Reading a turn already detects that and
records it on the day's session; this is the first thing that acts on it.
Once opened, the area stays open for the rest of the day and is locked again
tomorrow.

Two rules the specification left open, both settled here:

Which areas count as sensitive. Four of them — how somebody sees themselves,
their relationships, their health, and their spiritual life. Deliberately
not "emotional": in a conversation of this kind nearly everything is
emotional, and gating that would gate the whole graph, which would make the
system useless rather than careful.

What to do with a CRITICAL record belonging to no area at all. Individual
notes record no area of life — only the standing beliefs and patterns do —
so this is common rather than exotic. It is treated as sensitive and stays
locked until the person has opened *some* sensitive area today. The safe
reading of "we do not know what this is about" is caution, because by
definition this is the heaviest material there is.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence

from lumen.query.retrieval.contracts import RetrievedNode
from lumen.schemas.enums import Domain, SignalStrength

logger = logging.getLogger(__name__)

# The areas of life whose heaviest records need an invitation.
SENSITIVE_DOMAINS: frozenset[Domain] = frozenset(
    {
        Domain.SELF_CONCEPT,
        Domain.RELATIONAL,
        Domain.HEALTH,
        Domain.SPIRITUALITY,
    }
)


def apply(
    candidates: Sequence[RetrievedNode],
    *,
    unlocked: Iterable[Domain],
) -> tuple[list[RetrievedNode], tuple[str, ...]]:
    """
    Keep what may be offered, and name what was held back.

    Withheld records are named rather than silently dropped. A system that
    quietly removes things is one nobody can debug — and "why did it not
    mention the obvious thing?" is a question somebody will eventually ask
    of a graph they know contains the answer.
    """
    opened = set(unlocked)
    kept: list[RetrievedNode] = []
    withheld: list[str] = []

    for candidate in candidates:
        if is_withheld(candidate, opened):
            withheld.append(candidate.node_id)
        else:
            kept.append(candidate)

    if withheld:
        logger.info(
            "some of the heaviest records were held back until the person "
            "raises the subject themselves",
            extra={"withheld": len(withheld), "opened": len(opened)},
        )
    return kept, tuple(withheld)


def is_withheld(candidate: RetrievedNode, opened: set[Domain]) -> bool:
    """
    Whether this record needs an invitation it has not been given.

    Only the heaviest records are ever gated. Everything else — however
    private it may read — is ordinary history and is offered normally,
    because gating on how a record sounds rather than on what it is marked
    would be a judgement made in the wrong place by the wrong component.
    """
    if candidate.signal_strength is not SignalStrength.CRITICAL:
        return False

    if candidate.domain is None:
        # Nothing says what area this belongs to, so any opened sensitive
        # subject counts as the person having gone there.
        return not (opened & SENSITIVE_DOMAINS)

    if candidate.domain not in SENSITIVE_DOMAINS:
        return False
    return candidate.domain not in opened


__all__ = ["apply", "is_withheld", "SENSITIVE_DOMAINS"]
