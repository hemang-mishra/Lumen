"""
The extraction pipeline: the chain of steps that turns raw journal input
into structured knowledge.

Each stage is a plain function. It takes one typed object in, hands another
one back, and touches no database of its own. Whatever a stage needs from
the outside world — a language model, configuration — is passed to it, so
any stage can be run on its own with nothing installed.
"""

from lumen.pipeline.extraction import extract
from lumen.pipeline.preprocessing import preprocess

__all__ = ["preprocess", "extract"]
