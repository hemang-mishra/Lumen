"""
What a request is given to work with.

Three things, and the first two are narrower than they could be. The graph
arrives as a reader — every question, none of the writes — so a route that
tried to change something would be reaching for a method its own type does
not have. The operational store arrives whole, because reading a run's
history is all anything here does with it and there is no read-only half to
hand over. The turn reader arrives already built, because it holds a model
connection that is not worth opening per request.

All three are resolved per request from what the application opened at startup,
rather than reached for directly. That is what lets a test point the whole
API at temporary databases by replacing two functions.
"""

from __future__ import annotations

from fastapi import Request

from lumen.api.errors import Unavailable
from lumen.graph.provider import ReadOnlyGraph
from lumen.operational.repositories import OperationalStore
from lumen.query import QueryFormulator


def get_graph(request: Request) -> ReadOnlyGraph:
    """
    The graph, to read from.

    Typed as a reader on purpose. Adding a write endpoint would not merely
    be poor judgement — the method is not on what this hands back, so it
    would fail before it ran.
    """
    return request.app.state.graph


def get_ops(request: Request) -> OperationalStore:
    """The record of what past runs did."""
    return request.app.state.ops


def get_formulator(request: Request) -> QueryFormulator:
    """
    The thing that reads a conversational turn.

    Opened once when the application starts, because it holds a model
    connection and a small pool of threads. Building one per request would
    pay for both on every call, which is the opposite of what a component
    with a sub-second budget wants.

    It is the one part of this service that needs a model, so it is also the
    only part that can be missing. Saying so plainly beats a generic failure:
    the fix is to configure a model, and nothing else here is affected.
    """
    formulator = getattr(request.app.state, "formulator", None)
    if formulator is None:
        raise Unavailable(
            "reading conversational turns", "no language model is configured"
        )
    return formulator


__all__ = ["get_graph", "get_ops", "get_formulator"]
