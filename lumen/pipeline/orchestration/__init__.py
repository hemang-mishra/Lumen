"""
Running a whole session through the pipeline and saving what it produced.

The four thinking stages each do one job and know nothing about each other.
This package is the only thing that chains them together, and the only thing
in the system that writes to the graph and the search index.

Two names are public. `run_pipeline` takes one finished conversation and
leaves behind a graph that has grown. `repair_index` fixes the single kind of
damage a transaction cannot prevent — a record that was saved correctly but
never made findable, because the two stores cannot be written to as one.
"""

from lumen.pipeline.orchestration.contracts import (
    EmbeddingFailed,
    GraphWriteFailed,
    IndexWriteFailed,
    OrchestrationError,
)
from lumen.pipeline.orchestration.embed import repair_index
from lumen.pipeline.orchestration.runner import run_pipeline

__all__ = [
    "run_pipeline",
    "repair_index",
    "OrchestrationError",
    "EmbeddingFailed",
    "GraphWriteFailed",
    "IndexWriteFailed",
]
