"""
Stage 1 of the pipeline: turning a cleaned episode into graph nodes.

The package is split by concern — the vocabulary of what can be found, the
instructions given to the model, the rules everything is checked against,
and the work of naming and timestamping what survives — but only one name
is meant to be used from outside it.
"""

from lumen.pipeline.extraction.stage import extract

__all__ = ["extract"]
