"""
Turning the last few days into something the assistant can read.

Journalling is not a series of unrelated days. Somebody picks a thread back
up on Thursday that they let go of on Monday, and an assistant that starts
every morning knowing nothing about the week cannot follow that.

So today's instructions open with a few sentences about each of the last few
days the person actually talked. Every day already writes a summary of
itself, so this costs a handful of row reads and no model call at all.

Two rules keep it from taking over. The days share one allowance, and when
they do not all fit the oldest is dropped first — what happened yesterday
matters more to today than what happened last week. And each day is labelled
the way somebody would say it, because a bare date in front of a
conversation is noise.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from lumen.query.assembly.budget import estimate_tokens
from lumen.query.assembly.templates import humanise_date
from lumen.query.memory.contracts import DaySummary

HEADING = "[THE LAST FEW DAYS — from your notes, not from this chat]"

GUIDANCE = (
    "This is where they have been recently. Use it to follow a thread they "
    "pick back up, not as something to bring up on its own."
)


def render(
    days: tuple[DaySummary, ...],
    *,
    now: datetime,
    max_tokens: int,
    chars_per_token: float = 4.0,
) -> str:
    """
    The recent days as a block of text, or nothing when there are none.

    Nothing renders as an empty string rather than as a heading with nothing
    under it — an assistant shown "the last few days" followed by silence
    reads it as a claim that there were none.
    """
    lines = _within_budget(
        days, now=now, max_tokens=max_tokens, chars_per_token=chars_per_token
    )
    if not lines:
        return ""
    return "\n".join([HEADING, GUIDANCE, "", *lines])


def _within_budget(
    days: tuple[DaySummary, ...],
    *,
    now: datetime,
    max_tokens: int,
    chars_per_token: float,
) -> list[str]:
    """
    As many days as the allowance holds, oldest dropped first.

    Read newest-first while deciding and turned back the right way round
    afterwards, so that dropping happens at the far end of the week rather
    than at yesterday.
    """
    budget = max(int(max_tokens), 0)
    if not days or not budget:
        return []

    kept: list[str] = []
    spent = 0
    for day in reversed(days):
        line = _one_day(day, now)
        cost = estimate_tokens(line, chars_per_token=chars_per_token)
        if spent + cost > budget:
            break
        kept.append(line)
        spent += cost

    kept.reverse()
    return kept


def _one_day(day: DaySummary, now: datetime) -> str:
    """One day, labelled the way somebody would say it out loud."""
    return f"- {_said_as(day.on, now)}: {day.summary.strip()}"


def _said_as(on: date, now: datetime) -> str:
    """
    How far back a day was, in words.

    Falls back to naming the date only when the relative form would be empty,
    which happens for a day that is somehow in the future.
    """
    moment = datetime(on.year, on.month, on.day, tzinfo=UTC)
    spoken = humanise_date(moment, now)
    return spoken.capitalize() if spoken else f"{on:%d %B}"


__all__ = ["render", "HEADING", "GUIDANCE"]
