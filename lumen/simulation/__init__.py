"""
A written week, and a way to run it.

Everything before this processed one entry at a time. What the system is
actually for is what happens across many: someone writes about the same
struggle on Monday, Wednesday and Friday in different words, and the graph
should end up holding one thing with three pieces of evidence behind it
rather than three things with one each.

This package holds five consecutive days written for that purpose, a
stand-in embedder that can recognise a theme returning, and one call that
feeds the days through the real pipeline in order. It ships alongside the
code rather than living with the tests because it is also the only way to
fill a graph with anything worth looking at by hand.
"""

from lumen.simulation.corpus import (
    BELIEF_PACE,
    CORPUS,
    PATTERN_COMPARISON,
    THEMES,
    DayExpectation,
    SimulatedDay,
)
from lumen.simulation.runner import build_embedder, build_models, simulate_days
from lumen.simulation.themes import Theme, ThemedEmbeddingProvider

__all__ = [
    "CORPUS",
    "THEMES",
    "SimulatedDay",
    "DayExpectation",
    "PATTERN_COMPARISON",
    "BELIEF_PACE",
    "simulate_days",
    "build_models",
    "build_embedder",
    "Theme",
    "ThemedEmbeddingProvider",
]
