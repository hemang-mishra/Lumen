"""
Fetching a live conversation's history.

One name comes out of here. The three searches, the deadline, the
sensitivity gate and the day's own thread are how the fetching is done, not
things a caller picks between.
"""

from lumen.query.retrieval.stage import ConversationalRetriever

__all__ = ["ConversationalRetriever"]
