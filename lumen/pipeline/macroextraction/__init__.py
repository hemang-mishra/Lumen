"""
The periodic layer: what keeps happening, rather than what happened today.

Every other stage of the pipeline reads one entry at a time. That is the right
shape for extraction and the wrong shape for a whole class of true things
about a person, because a pattern that fires six times in a month is not
visible on any of those six days.

This package steps back. On a schedule it reads a stretch of history — a week,
a month, a quarter — and writes one immutable report saying what recurred,
what appeared for the first time, what stopped, what changed shape, and what
is still unresolved.

The package is built around one split, and it is worth knowing before reading
any of it. Every number is counted in plain code from the graph; every
sentence is written by a model that is shown those counts and asked for
phrasing only. So the figures in a report can be checked by hand, two runs
over the same month always agree, and a model that cannot be reached costs the
report its prose rather than the whole period.

Only two modules touch anything outside themselves — one reads, one writes —
and everything between them is a pure function over Pydantic models.
"""

from lumen.pipeline.macroextraction.contracts import (
    ComputedFacts,
    MacroWindow,
    NarrativeDraft,
    NarrativeResult,
    ReportOutcome,
    ShadowFinding,
    WindowCorpus,
)
from lumen.pipeline.macroextraction.runner import (
    due_now,
    run_due,
    run_report,
    run_shadow,
)
from lumen.pipeline.macroextraction.windows import (
    reports_due,
    shadow_window,
    window_for,
)

__all__ = [
    "ComputedFacts",
    "MacroWindow",
    "NarrativeDraft",
    "NarrativeResult",
    "ReportOutcome",
    "ShadowFinding",
    "WindowCorpus",
    "due_now",
    "run_due",
    "run_report",
    "run_shadow",
    "reports_due",
    "shadow_window",
    "window_for",
]
