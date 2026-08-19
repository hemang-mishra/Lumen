"""
Turning things going wrong into answers a caller can act on.

Two rules. A caller asking for something that is not there gets told exactly
that, by name, because "which of the four identifiers in my request was
wrong" is otherwise a guessing game. Anything unexpected gets a plain
apology and nothing else — the details go to the log, where the trace id
ties them to the run that produced them.

The second rule matters more than it looks. A stack trace or a database
error returned to a caller leaks the shape of the store and, in a system
holding somebody's private history, occasionally the contents too.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from lumen.observability.trace import get_trace_id

logger = logging.getLogger(__name__)


class NotFound(Exception):
    """
    Something was asked for by name and there is nothing by that name.

    Carries what kind of thing and which name, so the answer can say which
    part of the request was wrong instead of only that something was.
    """

    def __init__(self, kind: str, identifier: str) -> None:
        self.kind = kind
        self.identifier = identifier
        super().__init__(f"no {kind} with id {identifier!r}")


class Unavailable(Exception):
    """
    The request was fine, but something it needs is not running.

    Kept apart from an unexpected failure because a caller can act on it: a
    missing piece of configuration is fixed by configuring it, and answering
    that with a generic apology sends people looking for a bug instead.
    """

    def __init__(self, what: str, reason: str) -> None:
        self.what = what
        self.reason = reason
        super().__init__(f"{what} is unavailable: {reason}")


class BadRequest(Exception):
    """
    What was sent cannot be worked with, and no amount of retrying helps.

    Separate from an unexpected failure because the reason is safe to
    repeat back. A file that is not an export is not a leak of anything —
    it is the one piece of information that lets somebody fix their upload,
    and answering it with a generic apology sends them hunting for a bug in
    the service instead.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class Conflict(Exception):
    """
    The request was fine and the world moved on underneath it.

    Kept apart from a bad request because nothing about what was sent is
    wrong: answering a question that somebody already answered, or one whose
    record has been rewritten since it was asked, is a valid request that
    arrived too late. Telling the two apart is what lets a caller know
    whether to fix the request or simply reload it.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def register_error_handlers(app: FastAPI) -> None:
    """Teach the application how to answer when something goes wrong."""

    @app.exception_handler(BadRequest)
    async def _bad_request(_request: Request, exc: BadRequest) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": "bad_request", "detail": exc.reason},
        )

    @app.exception_handler(Unavailable)
    async def _unavailable(_request: Request, exc: Unavailable) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error": "unavailable",
                "detail": str(exc),
                "what": exc.what,
            },
        )

    @app.exception_handler(Conflict)
    async def _conflict(_request: Request, exc: Conflict) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"error": "conflict", "detail": exc.reason},
        )

    @app.exception_handler(NotFound)
    async def _not_found(_request: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={
                "error": "not_found",
                "detail": str(exc),
                "kind": exc.kind,
                "id": exc.identifier,
            },
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Logged in full, answered in one line. What went wrong is worth
        # keeping; sending it back would hand a caller the shape of the
        # store, and sometimes a piece of what it holds.
        logger.exception(
            "request failed",
            extra={"path": request.url.path, "error": type(exc).__name__},
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "detail": "something went wrong handling this request",
                "trace_id": get_trace_id(),
            },
        )


__all__ = [
    "NotFound",
    "Unavailable",
    "BadRequest",
    "Conflict",
    "register_error_handlers",
]
