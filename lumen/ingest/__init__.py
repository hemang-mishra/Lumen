"""
Getting somebody's existing chat history into Lumen.

Until now the only writing the pipeline had ever seen came from a fixture.
This package is the other door: a file that somebody exported from a chat
application, read into the same session buffers a live conversation would
have filled, and run through the same pipeline.

Three pieces, deliberately separate.

`parse_export` reads a file and understands it. It touches nothing — no
database, no clock, no configuration — so what a file means can be checked
without standing anything up.

`stage_conversations` writes what was understood into the waiting room,
using only the repository methods a live conversation already uses. There is
no second way into a session buffer.

`IngestWorker` runs the pipeline over what was staged, on one background
thread. It is the only thing here that can change the graph.
"""

from lumen.ingest.chatgpt_json import ExportFormatError, parse_export
from lumen.ingest.contracts import (
    ImportPlan,
    ParsedConversation,
    ParsedMessage,
    RejectedConversation,
    StagedConversation,
)
from lumen.ingest.loader import stage_conversations
from lumen.ingest.worker import IngestResources, IngestWorker, build_resources

__all__ = [
    "ExportFormatError",
    "parse_export",
    "ImportPlan",
    "ParsedConversation",
    "ParsedMessage",
    "RejectedConversation",
    "StagedConversation",
    "stage_conversations",
    "IngestWorker",
    "IngestResources",
    "build_resources",
]
