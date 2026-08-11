"""
Stage 0 of the pipeline — preparing raw input for extraction.

From outside, this is one function. `preprocess` takes a finished session
and returns clean, topic-split episodes with a decision about how much
attention each has earned.

Inside it splits into parts that are easier to reason about separately:
plain text handling that needs no model, the pattern-based filler removal,
the shapes passed between steps, the prompts, the model-backed steps, and
the sequence that runs them.
"""

from lumen.pipeline.preprocessing.stage import preprocess

__all__ = ["preprocess"]
