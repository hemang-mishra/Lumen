"""
The vector store, behind a Protocol.

Only the Protocol is exported here. The Qdrant implementation is reached
through `lumen.vector.qdrant_impl` and nowhere else, because importing this
package used to pull the vendor's client library in with it — which meant
anything referencing the Protocol, including pipeline stages that must
know nothing about which database is on the other side, ended up importing
Qdrant just to name a type.
"""

from lumen.vector.provider import ScoredHit, VectorProvider

__all__ = ["VectorProvider", "ScoredHit"]
