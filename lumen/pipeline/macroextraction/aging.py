"""
Noticing which patterns have gone quiet, and how quiet.

A pattern is never deleted for going unmentioned. People do not stop having
been a certain way because they stopped writing about it, and a system that
forgot on a timer would keep losing exactly the long-running things it exists
to hold on to.

What happens instead is that a quiet pattern counts for less when history is
searched, and crossing either threshold is reported. The second threshold is
the interesting one: past a year, the record genuinely cannot say whether
something resolved or simply stopped being written down, and the honest
response is to ask rather than to assume either.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from lumen.config import MacroConfig
from lumen.pipeline.macroextraction.contracts import AgingPattern, WindowCorpus
from lumen.schemas.enums import PatternAgeBand

logger = logging.getLogger(__name__)

# What to ask about a pattern nobody has mentioned in over a year. Written
# once here rather than asked of a model: it is the same question every time,
# and a model given the job would reword it differently in every report.
RE_INTERROGATION_PROMPT = (
    "This pattern hasn't appeared in over a year. Has it resolved, or has it "
    "gone unlogged?"
)


def age_patterns(corpus: WindowCorpus, *, config: MacroConfig) -> list[AgingPattern]:
    """
    Every live pattern that has been quiet long enough to be worth less.

    Measured against the end of the window rather than against today, so a
    report produced late says the same thing as one produced on time. A
    report is a statement about a period, and re-running last March's report
    next year should not age everything in it by a year.

    Deliberately not limited to what appeared in the window. These patterns
    are here precisely because they did not.
    """
    ends_at = _utc(corpus.window.period_end)
    cooling_after = max(config.cooling_days, 0)
    dormant_after = max(config.dormant_days, cooling_after + 1)

    aged: list[AgingPattern] = []

    for record in corpus.all_patterns:
        last_seen = _moment(
            record.get("last_reinforced_at")
            or record.get("valid_from")
            or record.get("created_at")
        )
        if last_seen is None:
            continue

        quiet_days = (ends_at - _utc(last_seen)).days
        if quiet_days <= cooling_after:
            continue

        dormant = quiet_days > dormant_after
        aged.append(
            AgingPattern(
                pattern_id=str(record.get("node_id")),
                label=str(record.get("pattern_name") or record.get("node_id")),
                band=PatternAgeBand.DORMANT if dormant else PatternAgeBand.COOLING,
                last_reinforced=last_seen,
                days_since_last_seen=quiet_days,
                weight_multiplier=(
                    config.dormant_multiplier if dormant else config.cooling_multiplier
                ),
                re_interrogation_prompt=RE_INTERROGATION_PROMPT if dormant else None,
            )
        )

    # Quietest first. The ones nobody has thought about in longest are the
    # ones a person is least likely to raise on their own.
    aged.sort(key=lambda item: (-item.days_since_last_seen, item.pattern_id))
    return aged[: max(config.aging_limit, 1)]


def _moment(value: Any) -> datetime | None:
    """Read a stored timestamp back, or nothing if it cannot be read."""
    if isinstance(value, datetime):
        return value
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        logger.debug("could not read %r as a moment", value)
        return None


def _utc(moment: datetime) -> datetime:
    """A moment with a timezone, reading a bare one as UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


__all__ = ["RE_INTERROGATION_PROMPT", "age_patterns"]
