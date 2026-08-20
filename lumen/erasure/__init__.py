"""
Forgetting, in a system built never to forget.

The graph is append-only. Nothing is deleted from it, which is exactly what
makes it worth trusting — a history that can quietly lose entries is not a
history. It also means that when somebody asks to be forgotten, there is no
delete to run.

The answer is to overwrite instead of remove. Every field holding something
a person said becomes a marker saying it was erased; every identifier, link,
date and version chain stays as it was. What is left proves that a history
existed and says nothing about what was in it.

Four things happen, in an order that matters: the record of the erasure is
opened first so a crash still leaves a trace, the graph is rewritten, the
search index is emptied of those records, and the working database — which
holds the person's actual sentences, not just what was read out of them — is
cleared too.

None of it can be undone.
"""

from lumen.erasure.service import ErasureService

__all__ = ["ErasureService"]
