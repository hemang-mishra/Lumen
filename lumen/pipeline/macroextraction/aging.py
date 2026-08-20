"""
Noticing which patterns have gone quiet, and how quiet.

A pattern is never deleted for going unmentioned. People do not stop having
been a certain way because they stopped writing about it, and a system that
forgot on a timer would keep losing exactly the long-running things it exists
to hold on to.

What happens instead is that a quiet pattern counts for less when history is
searched, and crossing a threshold is reported. The last one is the
interesting one: past a year, the record genuinely cannot say whether
something resolved or simply stopped being written down, and the honest
response is to ask rather than to assume either.

How much a quiet pattern is worth is not decided here. It is the same
question search ranking answers, and it is answered in one place so a report
can never state a number that nothing actually uses.
"""

from __future__ import annotations

import logging

from lumen.config import MacroConfig, ScoringConfig
from lumen.graph import scoring
from lumen.graph.rows import as_utc, last_seen_at
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


def age_patterns(
    corpus: WindowCorpus,
    *,
    config: MacroConfig,
    scoring_config: ScoringConfig | None = None,
) -> list[AgingPattern]:
    """
    Every live pattern that has been quiet long enough to be worth reporting.

    Measured against the end of the window rather than against today, so a
    report produced late says the same thing as one produced on time. A
    report is a statement about a period, and re-running last March's report
    next year should not age everything in it by a year.

    Deliberately not limited to what appeared in the window. These patterns
    are here precisely because they did not.

    How long counts as quiet enough to mention is this report's own opinion —
    a pattern nobody has written about for five weeks is not news. What that
    quiet costs the pattern is not: that comes from the shared curve.
    """
    weights = scoring_config or ScoringConfig()
    ends_at = as_utc(corpus.window.period_end)
    report_after = max(config.aging_report_days, 0)

    aged: list[AgingPattern] = []

    for record in corpus.all_patterns:
        last_seen = last_seen_at(record)
        if last_seen is None:
            continue

        quiet_days = scoring.quiet_days(last_seen, ends_at)
        if quiet_days <= report_after:
            continue

        band = scoring.age_band(last_seen, ends_at, config=weights)
        aged.append(
            AgingPattern(
                pattern_id=str(record.get("node_id")),
                label=str(record.get("pattern_name") or record.get("node_id")),
                band=band,
                last_reinforced=last_seen,
                days_since_last_seen=quiet_days,
                weight_multiplier=scoring.recency_weight(
                    last_seen, ends_at, config=weights
                ),
                re_interrogation_prompt=(
                    RE_INTERROGATION_PROMPT if band is PatternAgeBand.DORMANT else None
                ),
            )
        )

    # Quietest first. The ones nobody has thought about in longest are the
    # ones a person is least likely to raise on their own.
    aged.sort(key=lambda item: (-item.days_since_last_seen, item.pattern_id))
    return aged[: max(config.aging_limit, 1)]


__all__ = ["RE_INTERROGATION_PROMPT", "age_patterns"]
