"""
Reading a live conversational turn.

One name comes out of here. Everything else — the distress floor, the list
of turns not worth a model call, the checks against the graph, the deadline
— is how the reading is done, not something a caller chooses between.
"""

from lumen.query.formulation.stage import QueryFormulator

__all__ = ["QueryFormulator"]
