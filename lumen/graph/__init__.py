"""
The graph store, behind a Protocol.

Only the Protocol is exported here. The Kuzu implementation is reached
through `lumen.graph.kuzu_impl` and nowhere else, for the same reason as
the vector package: naming the Protocol should not drag a database driver
into whatever did the naming.
"""

from lumen.graph.provider import GraphProvider

__all__ = ["GraphProvider"]
