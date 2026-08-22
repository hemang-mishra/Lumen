"""
Running one report from end to end, and working out which ones are owed.

Everything above this module either reads, or counts, or writes; none of them
knows about the others. This is where the order is decided: read the period,
count what is in it, ask for the wording, put the two together, save it.

Three of the decisions that shape a report live here rather than in any of the
steps, because they are decisions about whether to run at all.

A period that already has a report is skipped and the existing one handed
back, so a schedule that fires twice costs nothing the second time. A period
with no writing in it produces no report, because a month somebody did not
write in should not leave behind a document saying so. And a model that cannot
be reached does not stop a report — the counting is already finished by then,
and a period is only ever covered once, so losing it to an outage would lose
it permanently.

There is no clock in here. This module answers "run this period" and "which
periods are owed"; something else decides when to ask.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from lumen.config import AppConfig
from lumen.graph.provider import GraphProvider
from lumen.graph.queries import tidy_row
from lumen.operational.repositories import OperationalStore
from lumen.pipeline.macroextraction import (
    analytics,
    assemble,
    commit,
    corpus,
    narrative,
    proof,
    shadow,
    windows,
)
from lumen.pipeline.macroextraction.contracts import (
    ComputedFacts,
    MacroWindow,
    NarrativeResult,
    ReportOutcome,
)
from lumen.providers.protocols import LLMProvider
from lumen.schemas.enums import MacroRunStatus, NarrativeStatus, ReportType

logger = logging.getLogger(__name__)

# The reports that are long enough to be worth a look over the whole history.
# A weekly report is about seven days; five years of evidence would drown it.
PROOF_REPORT_TYPES: frozenset[ReportType] = frozenset(
    {ReportType.MONTHLY, ReportType.QUARTERLY}
)


def run_report(
    window: MacroWindow,
    *,
    graph: GraphProvider,
    thinking: LLMProvider | None = None,
    ops: OperationalStore | None = None,
    config: AppConfig | None = None,
    force: bool = False,
) -> ReportOutcome:
    """
    Produce one period's report, if it is worth producing.

    The stores and the model arrive as parameters rather than being reached
    for, so this can be pointed at temporary databases in a test and at the
    real ones in a run, and so what it touches is readable in its signature.

    Never raises for an ordinary outcome. "Already covered", "nothing to
    cover" and "covered, but without its wording" are all real answers a
    schedule has to be able to tell apart, and each comes back as a status
    rather than as an exception or a None.
    """
    settings = config or AppConfig()
    started = time.perf_counter()

    existing = graph.find_reports(
        report_type=window.report_type.value,
        period_start=window.period_start,
        limit=50,
    )
    if existing and not force:
        logger.info(
            "this period has already been reported on",
            extra={
                "report_type": window.report_type.value,
                "period_start": window.period_start.isoformat(),
            },
        )
        return ReportOutcome(
            status=MacroRunStatus.SKIPPED_EXISTING,
            window=window,
            report_id=str(tidy_row(existing[0]).get("node_id")),
            episodes_analyzed=int(tidy_row(existing[0]).get("episodes_analyzed") or 0),
            duration_ms=_since(started),
        )

    gathered = corpus.gather(
        window,
        graph=graph,
        ops=ops,
        config=settings.macro,
        user_id=settings.default_user_id,
    )
    if gathered.is_empty:
        logger.info(
            "nothing was written about this period, so there is nothing to report",
            extra={
                "report_type": window.report_type.value,
                "period_start": window.period_start.isoformat(),
            },
        )
        return ReportOutcome(
            status=MacroRunStatus.EMPTY_WINDOW,
            window=window,
            duration_ms=_since(started),
        )

    facts = analytics.compute(
        gathered, config=settings.macro, scoring_config=settings.scoring
    )
    facts = _with_proof_chains(facts, window, graph=graph, config=settings)
    written = _narrate(facts, provider=thinking, config=settings)

    node, episode_ids = assemble.build(
        facts,
        written,
        model_used=written.model_used or "none",
        existing=len(existing),
    )
    report_id = commit.write(node, episode_ids, graph=graph)

    outcome = ReportOutcome(
        status=MacroRunStatus.WRITTEN,
        window=window,
        report_id=report_id,
        episodes_analyzed=facts.episodes_analyzed,
        narrative_status=written.status,
        duration_ms=_since(started),
    )
    _log_outcome(outcome, facts)
    return outcome


def run_shadow(
    now: datetime,
    *,
    graph: GraphProvider,
    lightweight: LLMProvider | None = None,
    config: AppConfig | None = None,
) -> ReportOutcome:
    """
    Look at the last couple of days and raise an alert if something moved.

    Writes nothing when nothing moved. A daily note saying "no shift
    detected" would be technically complete and would bury the days when
    something did, which is the whole value of the scan.
    """
    settings = config or AppConfig()
    started = time.perf_counter()
    window = windows.shadow_window(now, config=settings.macro)

    finding, decisions = shadow.scan(window, graph=graph, config=settings.macro)
    if not finding.detected:
        return ReportOutcome(
            status=MacroRunStatus.NOT_DETECTED,
            window=window,
            duration_ms=_since(started),
        )

    described = (
        narrative.write_shadow(
            finding, decisions, provider=lightweight, config=settings.macro
        )
        if lightweight is not None
        else narrative.plain_shadow(finding)
    )

    node, episode_ids = assemble.build_shadow(
        window,
        finding,
        described,
        model_used=getattr(lightweight, "model_name", "") or "none",
    )
    report_id = commit.write(node, episode_ids, graph=graph)

    return ReportOutcome(
        status=MacroRunStatus.WRITTEN,
        window=window,
        report_id=report_id,
        episodes_analyzed=len(finding.episode_ids),
        narrative_status=NarrativeStatus.OK,
        duration_ms=_since(started),
    )


def due_now(
    now: datetime, *, graph: GraphProvider, config: AppConfig | None = None
) -> list[MacroWindow]:
    """
    Which periods should have a report by now and do not, oldest first.

    Reads which periods are already covered straight from the reports
    themselves rather than from any separate record of what has run. There is
    then nothing to keep in step: the reports are the state, and a report
    that exists is proof its period was covered.
    """
    settings = config or AppConfig()
    covered = {
        (
            str(tidy_row(row).get("report_type") or ""),
            str(tidy_row(row).get("period_start") or ""),
        )
        for row in graph.find_reports(limit=200)
    }
    return windows.reports_due(now, covered, config=settings.macro)


def run_due(
    now: datetime,
    *,
    graph: GraphProvider,
    thinking: LLMProvider | None = None,
    lightweight: LLMProvider | None = None,
    ops: OperationalStore | None = None,
    config: AppConfig | None = None,
) -> list[ReportOutcome]:
    """
    Catch up on everything owed, including the two-day scan.

    The periodic reports run oldest first, so each one has the period before
    it already written when it looks for what stopped happening.

    Switched off entirely by configuration, because a deployment that does
    not want scheduled model spend should be able to say so in one place
    rather than by never calling this.
    """
    settings = config or AppConfig()
    if not settings.macro.enabled:
        logger.info("periodic reports are switched off for this deployment")
        return []

    outcomes = [
        run_report(
            window, graph=graph, thinking=thinking, ops=ops, config=settings
        )
        for window in due_now(now, graph=graph, config=settings)
    ]

    if windows.shadow_due(
        now, shadow.last_scan_at(graph), config=settings.macro
    ):
        outcomes.append(
            run_shadow(now, graph=graph, lightweight=lightweight, config=settings)
        )

    return outcomes


def _narrate(
    facts: ComputedFacts, *, provider: LLMProvider | None, config: AppConfig
) -> NarrativeResult:
    """
    The report's wording, or an honest note that there is none.

    A deployment with no model configured is a supported way to run: every
    figure in a report is arrived at without one, and the counts are the part
    that cannot be reconstructed later.
    """
    if provider is None:
        logger.info("no model is configured, so this report will carry only its counts")
        return NarrativeResult(
            status=NarrativeStatus.UNAVAILABLE, reason="no model is configured"
        )
    return narrative.write(facts, provider=provider, config=config.macro)


def _log_outcome(outcome: ReportOutcome, facts: ComputedFacts) -> None:
    """One line recording what a finished report actually contained."""
    logger.info(
        "a periodic report was written",
        extra={
            "report_id": outcome.report_id,
            "report_type": outcome.window.report_type.value,
            "period_start": outcome.window.period_start.isoformat(),
            "episodes_analyzed": facts.episodes_analyzed,
            "patterns": len(facts.pattern_frequency),
            "emerging": len(facts.emerging_patterns),
            "disappearing": len(facts.disappearing_patterns),
            "belief_changes": len(facts.belief_changes),
            "open_loops": len(facts.unresolved_open_loops),
            "archetype_shift_detected": facts.archetype_shift.detected,
            "narrative_status": outcome.narrative_status.value,
            "truncated": facts.truncated,
            "duration_ms": outcome.duration_ms,
        },
    )


def _with_proof_chains(
    facts: ComputedFacts,
    window: MacroWindow,
    *,
    graph: GraphProvider,
    config: AppConfig,
) -> ComputedFacts:
    """
    Add the long-running patterns, for the reports long enough to want them.

    Only the monthly and quarterly ones. This reads the whole history rather
    than a window, and a weekly report is a look at seven days — putting
    five years of evidence in it would drown the thing it is for.

    A failure costs the section and not the report. Everything else in the
    document was already counted before this runs.
    """
    if window.report_type not in PROOF_REPORT_TYPES:
        return facts

    try:
        chains = proof.find_proof_chains(graph, config=config.maintenance)
    except Exception:
        logger.warning(
            "the whole-history scan for long-running patterns did not finish, "
            "so this report goes out without that section",
            exc_info=True,
            extra={"report_type": window.report_type.value},
        )
        return facts

    return facts.model_copy(update={"proof_chains": chains})


def _since(started: float) -> int:
    """How long something took, in whole milliseconds."""
    return int((time.perf_counter() - started) * 1000)


__all__ = [
    "run_report",
    "run_shadow",
    "run_due",
    "due_now",
]
