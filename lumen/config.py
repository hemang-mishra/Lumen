"""
Lumen Application Configuration.

Central configuration for all provider injection. This is the single place
where infrastructure choices (Kuzu vs Neo4j, local vs cloud Qdrant, etc.)
are made. Business logic never references vendor libraries directly.

See: docs/hld/Technical_HLD.md Section 3.3 — "The only thing that changes
between local and production is config.py"
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GraphConfig:
    """Configuration for the Graph database provider."""
    db_path: str = os.environ.get("LUMEN_GRAPH_DB_PATH", "./lumen_graph.db")


@dataclass(frozen=True)
class VectorConfig:
    """Configuration for the Vector database provider."""
    location: str = os.environ.get("LUMEN_VECTOR_LOCATION", ":memory:")
    collection_name: str = "lumen_nodes"
    vector_size: int = 768  # text-embedding-004 default


@dataclass(frozen=True)
class AppConfig:
    """
    Top-level application config. All provider constructors read from this.
    
    Environment variables override defaults:
      LUMEN_GRAPH_DB_PATH   — path for Kuzu database
      LUMEN_VECTOR_LOCATION — ":memory:" or path for Qdrant
    """
    graph: GraphConfig = field(default_factory=GraphConfig)
    vector: VectorConfig = field(default_factory=VectorConfig)
