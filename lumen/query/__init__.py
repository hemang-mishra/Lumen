"""
The side of Lumen that reads the graph back during a live conversation.

The pipeline packages turn what somebody wrote into recorded history. This
one runs while they are still talking: it decides, turn by turn, whether any
of that history is worth putting in front of the AI answering them.

It is kept apart from the pipeline for two reasons. It reads and never
writes, so none of the rules that protect the write path apply to it. And it
holds state for as long as a conversation lasts, which nothing in the
pipeline is allowed to do.
"""

from lumen.query.buffer import SessionContextBuffer
from lumen.query.formulation import QueryFormulator
from lumen.query.retrieval import ConversationalRetriever
from lumen.query.session import ChatSession, SessionRegistry, make_session_id

__all__ = [
    "QueryFormulator",
    "ConversationalRetriever",
    "ChatSession",
    "SessionContextBuffer",
    "SessionRegistry",
    "make_session_id",
]
