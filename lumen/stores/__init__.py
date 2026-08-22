"""
A graph each.

Goal 21 put a person behind every request and stopped short of the thing that
mattered: everybody who signed in still shared one graph. This is the other
half.

There are two ways to keep histories apart. One is a user column on every
record and a condition on every query — which works until somebody, someday,
writes a query and forgets, and a single missing condition is one person
reading another person's psychological history. The other is a store each:
no shared table, so the mistake cannot be written, and every query already in
the system is correct without knowing anything about it.

This package is the second. The registry is the only place a store handle
comes from, which is what makes "which person is this about" a question with
exactly one answer.
"""

from lumen.stores.contracts import (
    HalfProvisioned,
    StoreError,
    StoresClosed,
    UserStores,
)
from lumen.stores.keys import UnsafeUserKey, collection_name, graph_dir, user_key
from lumen.stores.registry import StoreRegistry

__all__ = [
    "StoreRegistry",
    "UserStores",
    "StoreError",
    "HalfProvisioned",
    "StoresClosed",
    "UnsafeUserKey",
    "user_key",
    "graph_dir",
    "collection_name",
]
