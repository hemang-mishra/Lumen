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

from lumen.schemas.enums import ModelRole


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
class ProviderConfig:
    """
    Single point of configuration for every AI provider role in Lumen.

    Each role (see lumen.schemas.enums.ModelRole) independently maps to a
    (provider, model) pair. This is the ONLY place a role resolves to an
    actual vendor + model — the abstraction layers built in Goal 4 read
    from here and never hardcode a vendor or assume a deployment locality.

    Deliberately excludes any privacy/security-tier concept. An operator
    who wants guaranteed-local processing configures every *_provider
    field to a local provider (e.g. "ollama", "whisper_cpp") — that is a
    deployment choice made once, here, not a runtime decision the pipeline
    makes per piece of content. See docs/hld/LLM_Abstraction_Architecture.md.

    Environment variables override every field independently:
      LUMEN_LIGHTWEIGHT_PROVIDER / LUMEN_LIGHTWEIGHT_MODEL
      LUMEN_THINKING_PROVIDER / LUMEN_THINKING_MODEL
      LUMEN_EMBEDDING_PROVIDER / LUMEN_EMBEDDING_MODEL
      LUMEN_TRANSCRIPTION_PROVIDER / LUMEN_TRANSCRIPTION_MODEL
      LUMEN_TTS_PROVIDER / LUMEN_TTS_MODEL
    """

    lightweight_provider: str = os.environ.get("LUMEN_LIGHTWEIGHT_PROVIDER", "gemini")
    lightweight_model: str = os.environ.get("LUMEN_LIGHTWEIGHT_MODEL", "gemini-2.5-flash")

    thinking_provider: str = os.environ.get("LUMEN_THINKING_PROVIDER", "gemini")
    thinking_model: str = os.environ.get("LUMEN_THINKING_MODEL", "gemini-2.5-pro")

    embedding_provider: str = os.environ.get("LUMEN_EMBEDDING_PROVIDER", "gemini")
    embedding_model: str = os.environ.get("LUMEN_EMBEDDING_MODEL", "text-embedding-004")

    transcription_provider: str = os.environ.get("LUMEN_TRANSCRIPTION_PROVIDER", "whisper_cpp")
    transcription_model: str = os.environ.get("LUMEN_TRANSCRIPTION_MODEL", "base.en")

    tts_provider: str = os.environ.get("LUMEN_TTS_PROVIDER", "macos")
    tts_model: str = os.environ.get("LUMEN_TTS_MODEL", "default")

    def resolve(self, role: ModelRole) -> tuple[str, str]:
        """Return the (provider, model) pair configured for a given role."""
        mapping: dict[ModelRole, tuple[str, str]] = {
            ModelRole.LIGHTWEIGHT: (self.lightweight_provider, self.lightweight_model),
            ModelRole.THINKING: (self.thinking_provider, self.thinking_model),
            ModelRole.EMBEDDING: (self.embedding_provider, self.embedding_model),
            ModelRole.TRANSCRIPTION: (self.transcription_provider, self.transcription_model),
            ModelRole.TTS: (self.tts_provider, self.tts_model),
        }
        return mapping[role]


@dataclass(frozen=True)
class AppConfig:
    """
    Top-level application config. All provider constructors read from this.

    Environment variables override defaults:
      LUMEN_GRAPH_DB_PATH   — path for Kuzu database
      LUMEN_VECTOR_LOCATION — ":memory:" or path for Qdrant
      See ProviderConfig for the full set of AI-provider env vars.
    """
    graph: GraphConfig = field(default_factory=GraphConfig)
    vector: VectorConfig = field(default_factory=VectorConfig)
    providers: ProviderConfig = field(default_factory=ProviderConfig)
