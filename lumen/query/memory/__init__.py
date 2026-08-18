"""
What the assistant can still see of the conversation it is in.

Distinct from what it knows about the person, which is the graph and is
years deep. This is today: the recent turns as they were said, and a few
sentences about everything before them.
"""

from lumen.query.memory.contracts import Recollection
from lumen.query.memory.stage import ConversationMemory

__all__ = ["ConversationMemory", "Recollection"]
