"""
Stage 3 of the pipeline: deciding what today means for what came before.

The package is split by the job each part does — asking the models, checking
their answers, working out what a decision comes to, and the two kinds of
record only this stage creates. Only one name is meant to be used from
outside it.
"""

from lumen.pipeline.reconciliation.stage import reconcile

__all__ = ["reconcile"]
