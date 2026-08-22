"""
Ways to read the periodic reports, and one way to ask for another.

The reads are like every other read in this service: check what was asked,
ask the store one question, shape the answer. Nothing here decides anything
about somebody's history.

The last route is the exception and the only thing in this file that changes
the graph. It is still narrow: what it holds is not a graph handle but the
thing that builds reports, and what a caller can do with that is name a
period. Everything a report actually does — reading the window, counting it,
asking a model for the wording, writing it down — happens inside, on the other
side of a surface with three methods on it.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Query

from lumen.api.deps import get_config, get_graph, get_reporter, require_identity
from lumen.api.errors import BadRequest, NotFound
from lumen.api.schemas import (
    ReportDetailView,
    ReportDueView,
    ReportEnvelopeView,
    ReportListView,
    ReportRunRequest,
    ReportRunView,
)
from lumen.auth.contracts import Identity
from lumen.config import AppConfig
from lumen.graph.provider import ReadOnlyGraph
from lumen.pipeline.macroextraction import windows
from lumen.pipeline.macroextraction.service import MacroextractionService
from lumen.schemas.enums import ReportType

router = APIRouter(prefix="/reports", tags=["reports"])

# The most reports one request will list. A cap rather than a suggestion,
# since the caller drawing the result is usually a browser.
MAX_LIMIT = 100


@router.get("", response_model=ReportListView)
def list_reports(
    store: ReadOnlyGraph = Depends(get_graph),
    report_type: str | None = Query(default=None, alias="type"),
    limit: int = Query(default=20, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> ReportListView:
    """
    Every report written, newest first.

    Envelopes only. A quarter's report is a long document, and answering
    "which reports exist" with twenty of them in full would send megabytes
    to answer a question about names and dates.
    """
    rows = store.find_reports(
        report_type=_known_type(report_type), limit=limit, offset=offset
    )
    return ReportListView(
        reports=[ReportEnvelopeView.of(row) for row in rows],
        count=len(rows),
        limit=limit,
        offset=offset,
    )


@router.get("/due", response_model=list[ReportDueView])
def due_reports(
    reporter: MacroextractionService = Depends(get_reporter),
    identity: Identity = Depends(require_identity),
) -> list[ReportDueView]:
    """
    Which periods a schedule would build if it woke up now.

    Separate from building them on purpose. This is the decision, and it is
    worth being able to look at the decision before anything is spent acting
    on it.
    """
    return [
        ReportDueView(
            report_type=window.report_type.value,
            period_start=window.period_start,
            period_end=window.period_end,
        )
        for window in reporter.due(identity.user_id, datetime.now(timezone.utc))
    ]


@router.get("/{report_id}", response_model=ReportDetailView)
def get_report(
    report_id: str,
    store: ReadOnlyGraph = Depends(get_graph),
) -> ReportDetailView:
    """
    One report in full, with the writing it drew on.

    The list of episodes is what makes the report checkable. Every figure in
    it came from those and nothing else, so a reader who doubts a claim has
    somewhere to go and look.
    """
    row = store.get_node(report_id)
    if row is None or str(row.get("_label")) != "MacroextractionReportNode":
        raise NotFound("report", report_id)

    covering = store.get_neighborhood(
        report_id, depth=1, edge_types=["analyzed_in"], direction="in"
    )
    episode_ids = sorted(
        edge.from_node_id for edge in covering.edges if edge.edge_type == "analyzed_in"
    )
    return ReportDetailView.of(row, episode_ids=episode_ids)


@router.post("/run", response_model=ReportRunView)
def run_report(
    request: ReportRunRequest,
    reporter: MacroextractionService = Depends(get_reporter),
    config: AppConfig = Depends(get_config),
    identity: Identity = Depends(require_identity),
) -> ReportRunView:
    """
    Build one report now, without waiting for a schedule.

    Runs while the caller waits, because a report over a month of history is
    a handful of database reads and one model call, and a background job for
    that would need a way to report back on itself that nothing yet has.

    A period that already has a report is not rebuilt unless asked. That is
    what makes this safe to press twice.
    """
    now = datetime.now(timezone.utc)
    report_type = ReportType(request.report_type)

    if report_type is ReportType.SHADOW:
        outcome = reporter.run_shadow(identity.user_id, now)
    else:
        outcome = reporter.run(
            identity.user_id,
            _window_for(report_type, request.period_start, config),
            force=request.force,
        )

    return ReportRunView(
        status=outcome.status.value,
        report_id=outcome.report_id,
        report_type=outcome.window.report_type.value,
        period_start=outcome.window.period_start,
        period_end=outcome.window.period_end,
        episodes_analyzed=outcome.episodes_analyzed,
        narrative_status=outcome.narrative_status.value,
        duration_ms=outcome.duration_ms,
        error=outcome.error,
    )


def _window_for(
    report_type: ReportType, period_start: date | None, config: AppConfig
):
    """
    Which period was asked for.

    A day inside the period is enough — the boundaries are worked out from
    the calendar rather than taken as given, so a caller cannot ask for a
    five-week month and get one.

    With no day named, the most recent period that has fully closed is used.
    That is nearly always what somebody pressing the button means, and the
    period still running would produce a report that goes stale by tomorrow.
    """
    if period_start is None:
        current = windows.window_for(report_type, datetime.now(timezone.utc))
        return windows.previous_window(current)

    anchor = datetime(
        period_start.year, period_start.month, period_start.day, tzinfo=timezone.utc
    )
    return windows.window_for(report_type, anchor)


def _known_type(report_type: str | None) -> str | None:
    """The kind of report asked for, refused plainly if there is no such kind."""
    if report_type is None:
        return None
    try:
        return ReportType(report_type.upper()).value
    except ValueError:
        raise BadRequest(
            f"there is no such kind of report as {report_type!r}"
        ) from None


__all__ = ["router"]
