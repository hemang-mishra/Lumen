"""
Stage 2 of the pipeline: bringing back what the person has said before.

The package is split by how a candidate is found — by resemblance, or by
what it is attached to — plus the writing of the text that resemblance is
measured against, and the settling of the two into one short list. Only one
name is meant to be used from outside it.
"""

from lumen.pipeline.retrieval.stage import retrieve

__all__ = ["retrieve"]
