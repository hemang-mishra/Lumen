"""
The side of Lumen that reads the graph back during a live conversation.

The pipeline packages turn what somebody wrote into recorded history. This
one runs while they are still talking: it decides, turn by turn, whether any
of that history is worth putting in front of the AI answering them.

It is kept apart from the pipeline for two reasons. It never writes to the
graph, so none of the rules that protect the write path apply to it. And it
holds state for as long as a conversation lasts, which nothing in the
pipeline is allowed to do.

It does write one thing, and the distinction is worth stating rather than
glossing. Conversations are saved — the turns themselves, and a running note
of what they have been about — into the same store the extraction pipeline
already reads from. That is not a graph write and does not weaken the
guarantee that matters: nothing on this side can create, change or retire a
record of somebody's history. What it can do is keep the conversation that
will later become one.
"""

from lumen.query.assembly import ContextAssembler
from lumen.query.buffer import SessionContextBuffer
from lumen.query.conversation import ConversationStore
from lumen.query.formulation import QueryFormulator
from lumen.query.memory import ConversationMemory
from lumen.query.prompting import ChatPrompt, PromptComposer
from lumen.query.retrieval import ConversationalRetriever
from lumen.query.session import ChatSession, SessionRegistry, make_session_id

__all__ = [
    "QueryFormulator",
    "ConversationalRetriever",
    "ContextAssembler",
    "PromptComposer",
    "ChatPrompt",
    "ConversationMemory",
    "ConversationStore",
    "ChatSession",
    "SessionContextBuffer",
    "SessionRegistry",
    "make_session_id",
]
