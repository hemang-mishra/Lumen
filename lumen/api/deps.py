"""
What a request is given to work with.

Two things, and both are narrower than they could be. The graph arrives as a
reader — every question, none of the writes — so a route that tried to
change something would be reaching for a method its own type does not have.
The operational store arrives whole, because reading a run's history is all
anything here does with it and there is no read-only half to hand over.

Both are resolved per request from what the application opened at startup,
rather than reached for directly. That is what lets a test point the whole
API at temporary databases by replacing two functions.
"""

from __future__ import annotations

from fastapi import Request

from lumen.graph.provider import ReadOnlyGraph
from lumen.operational.repositories import OperationalStore


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


__all__ = ["get_graph", "get_ops"]
