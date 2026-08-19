"""
The web surface of the review queue.

Six routes, and none of them decides anything. Each one checks what was
asked, hands it to the thing that owns reviewing, and shapes the answer. What
these routes hold is not a graph handle: it is an object whose whole
vocabulary is "list what is waiting", "answer this one", "put this one off"
and "do the housekeeping". A route cannot reach anybody's history through it,
so no amount of growth here can turn into an unaudited write.

Answering is the only thing in this file that changes the graph, and even
then the only thing a caller supplies is which of the answers on the card
they picked. Everything that gets written was worked out and saved when the
question was raised.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from lumen.api.deps import get_config, get_reviewer
from lumen.api.errors import BadRequest, Conflict, NotFound
from lumen.api.schemas import ReviewCountView, ReviewResolveRequest
from lumen.config import AppConfig
from lumen.operational.repositories import (
    IllegalStateTransitionError,
    RecordNotFoundError,
)
from lumen.review.contracts import (
    ChoiceNotOffered,
    QueueCard,
    QueueView,
    ResolutionOutcome,
    StaleProposal,
    SweepReport,
)
from lumen.review.service import MissingProposal, ReviewService

router = APIRouter(prefix="/hitl", tags=["review"])

# The most cards one request will build. A cap rather than a suggestion: each
# card costs graph reads, and nothing useful asks for four hundred questions
# at once.
MAX_LIMIT = 100


@router.get("", response_model=QueueView)
def list_queue(
    reviewer: ReviewService = Depends(get_reviewer),
    config: AppConfig = Depends(get_config),
    limit: int = Query(default=20, ge=1, le=MAX_LIMIT),
) -> QueueView:
    """
    Everything waiting for an answer, in the order to ask.

    Ties first, then the findings that carry the most weight, then whatever
    has waited longest. Opening the queue also runs its housekeeping, so the
    list is correct rather than merely recent.
    """
    return reviewer.list_queue(config.user_id, limit=limit)


@router.get("/count", response_model=ReviewCountView)
def queue_count(
    reviewer: ReviewService = Depends(get_reviewer),
    config: AppConfig = Depends(get_config),
) -> ReviewCountView:
    """
    How much is waiting, and how long the oldest has waited.

    Separate from the queue itself because this is polled from everywhere
    and the queue is not. It runs no housekeeping: a number displayed in a
    corner should not settle anything on somebody's behalf.
    """
    return ReviewCountView.of(reviewer.counts(config.user_id))


@router.get("/{item_id}", response_model=QueueCard)
def get_card(
    item_id: str,
    reviewer: ReviewService = Depends(get_reviewer),
    config: AppConfig = Depends(get_config),
) -> QueueCard:
    """One question in full, with every answer it offers."""
    try:
        return reviewer.get_card(config.user_id, item_id)
    except RecordNotFoundError as exc:
        raise NotFound("review item", item_id) from exc


@router.post("/{item_id}/resolve", response_model=ResolutionOutcome)
def resolve_item(
    item_id: str,
    request: ReviewResolveRequest,
    reviewer: ReviewService = Depends(get_reviewer),
    config: AppConfig = Depends(get_config),
) -> ResolutionOutcome:
    """
    Answer one question, and write what was held back.

    An answer the card did not offer is refused rather than mapped onto the
    nearest one. A question whose record has been superseded since it was
    raised is refused too — that is not a bad request, it is a request that
    was valid when it was drawn and has been overtaken, which is what a
    conflict means.
    """
    try:
        return reviewer.resolve(config.user_id, item_id, request.choice)
    except RecordNotFoundError as exc:
        raise NotFound("review item", item_id) from exc
    except MissingProposal as exc:
        raise Conflict(str(exc)) from exc
    except ChoiceNotOffered as exc:
        raise BadRequest(str(exc)) from exc
    except StaleProposal as exc:
        raise Conflict(str(exc)) from exc
    except IllegalStateTransitionError as exc:
        raise Conflict(str(exc)) from exc


@router.post("/{item_id}/dismiss", response_model=ResolutionOutcome)
def dismiss_item(
    item_id: str,
    reviewer: ReviewService = Depends(get_reviewer),
    config: AppConfig = Depends(get_config),
) -> ResolutionOutcome:
    """
    Withdraw a question that can no longer be answered.

    Only for an item with nothing recorded to carry out. Nothing is written
    to the history; the question stops being asked, and the note says it was
    dropped rather than settled. Refused for anything answerable — deferring
    is what "not now" means, and a question with a real answer behind it
    should never be quietly dropped.
    """
    try:
        return reviewer.dismiss(config.user_id, item_id)
    except RecordNotFoundError as exc:
        raise NotFound("review item", item_id) from exc
    except ChoiceNotOffered as exc:
        raise BadRequest(str(exc)) from exc
    except IllegalStateTransitionError as exc:
        raise Conflict(str(exc)) from exc


@router.post("/{item_id}/snooze", response_model=QueueCard)
def snooze_item(
    item_id: str,
    reviewer: ReviewService = Depends(get_reviewer),
    config: AppConfig = Depends(get_config),
) -> QueueCard:
    """
    Put one question off, and hide it while it waits.

    Comes back with the card so the answer says what deferring actually did:
    when it will return, and the date it would settle itself.
    """
    try:
        return reviewer.snooze(config.user_id, item_id)
    except RecordNotFoundError as exc:
        raise NotFound("review item", item_id) from exc
    except IllegalStateTransitionError as exc:
        raise Conflict(str(exc)) from exc


@router.post("/sweep", response_model=SweepReport)
def sweep(
    reviewer: ReviewService = Depends(get_reviewer),
    config: AppConfig = Depends(get_config),
) -> SweepReport:
    """
    Run the housekeeping now.

    The same pass that runs whenever the queue is opened or answered, offered
    on its own so a scheduler has one thing to call. Reports what it settled
    and what it let in, because a pass that changes somebody's history
    without being asked has to be able to say what it did.
    """
    return reviewer.sweep(config.user_id)


__all__ = ["router"]
