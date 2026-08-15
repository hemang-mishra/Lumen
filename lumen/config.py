"""
Lumen Application Configuration.

Central configuration for all provider injection. This is the single place
where infrastructure choices (Kuzu vs Neo4j, local vs cloud Qdrant, etc.)
are made. Business logic never references vendor libraries directly.

Every environment variable is read when a config object is *constructed*, not
when this module is imported. That distinction matters: a process that loads a
.env file after importing lumen.config would otherwise be stuck with whatever
the environment held at import time, silently ignoring its own settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from lumen.schemas.enums import ModelRole


def _env(name: str, default: str) -> Any:
    """
    A dataclass default that reads an environment variable on construction.

    Field defaults are evaluated once, when the class is created. Wrapping the
    read in a default_factory defers it to each instantiation, which is what
    makes `LUMEN_X=... python -m lumen` and monkeypatched environments in tests
    behave the way everyone expects.
    """
    return field(default_factory=lambda: os.environ.get(name, default))


def _env_int(name: str, default: int) -> Any:
    """As _env, for a whole number."""
    return field(default_factory=lambda: int(os.environ.get(name, str(default))))


def _env_float(name: str, default: float) -> Any:
    """As _env, for a decimal number."""
    return field(default_factory=lambda: float(os.environ.get(name, str(default))))


def _env_optional_int(name: str) -> Any:
    """
    As _env, for a whole number that is normally not set at all.

    Left unset it stays None, which lets code tell "nobody said" apart from
    "somebody said this number" — a distinction that matters when the fallback
    would otherwise hide a mistake.
    """

    def read() -> int | None:
        raw = os.environ.get(name)
        return int(raw) if raw else None

    return field(default_factory=read)


def _env_bool(name: str, default: bool) -> Any:
    """
    As _env, for a true/false switch.

    Anything other than "true"/"false" (case-insensitive) leaves the default in
    place rather than guessing at intent.
    """

    def read() -> bool:
        raw = os.environ.get(name)
        if raw is None:
            return default
        lowered = raw.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
        return default

    return field(default_factory=read)


@dataclass(frozen=True)
class GraphConfig:
    """Configuration for the Graph database provider."""

    db_path: str = _env("LUMEN_GRAPH_DB_PATH", "./lumen_graph.db")


@dataclass(frozen=True)
class VectorConfig:
    """
    Configuration for the Vector database provider.

    vector_size must match the width of whatever the EMBEDDING role produces.
    The embedding provider checks this at startup rather than letting a
    mismatch surface as a failed write much later.
    """

    location: str = _env("LUMEN_VECTOR_LOCATION", ":memory:")
    collection_name: str = _env("LUMEN_VECTOR_COLLECTION", "lumen_nodes")
    vector_size: int = _env_int("LUMEN_VECTOR_SIZE", 768)


@dataclass(frozen=True)
class OperationalConfig:
    """
    Configuration for the operational database — the store that holds session
    buffers, pipeline job state, the review queue, and settings.

    Swapping SQLite for PostgreSQL is a change to db_url and nothing else.

    Environment variables:
      LUMEN_OPS_DB_URL           — SQLAlchemy connection URL
      LUMEN_OPS_DB_ECHO          — "true" to log every SQL statement
      LUMEN_SESSION_DECAY_MINUTES — idle minutes before a session is processed
      LUMEN_HITL_QUEUE_CAP       — maximum items allowed in the review queue
    """

    db_url: str = _env("LUMEN_OPS_DB_URL", "sqlite:///./lumen_ops.db")
    echo_sql: bool = _env_bool("LUMEN_OPS_DB_ECHO", False)
    session_decay_minutes: int = _env_int("LUMEN_SESSION_DECAY_MINUTES", 120)
    hitl_queue_cap: int = _env_int("LUMEN_HITL_QUEUE_CAP", 40)


@dataclass(frozen=True)
class PipelineConfig:
    """
    Tuning knobs for the extraction pipeline stages.

    These are thresholds that decide how much attention a piece of writing
    earns. They live here rather than as constants in the code because the
    right values are only discoverable by running real entries through the
    pipeline and seeing what gets waved through or held back.

    Environment variables:
      LUMEN_MIN_REFLECTION_WORDS  — below this word count, skip deep analysis
      LUMEN_COHERENCE_THRESHOLD   — score at or above this counts as a reflection
      LUMEN_REFLECTION_PROMPT_COUNT — follow-up questions offered on thin entries
      LUMEN_MAX_EPISODES          — ceiling on how many pieces one entry can split into
      LUMEN_MAX_OBSERVATIONS      — ceiling on findings taken from one episode
      LUMEN_MAX_CAUSAL_CHAINS     — ceiling on cause-and-effect sequences per episode
      LUMEN_MAX_CAUSAL_STEPS      — ceiling on steps within one sequence
      LUMEN_MAX_EXTRACTION_ATTEMPTS — tries at reading one episode before giving up

    The three ceilings are limits, not targets. They exist so that one runaway
    reply cannot turn a single paragraph into two hundred nodes; a normal
    entry never comes close to them.

    The attempt count covers the first reading plus any corrections asked for
    afterwards, so the default of three means one reading and at most two
    goes at fixing what it got wrong. Setting it to one turns correction off
    entirely.
    """

    min_reflection_words: int = _env_int("LUMEN_MIN_REFLECTION_WORDS", 30)
    coherence_threshold: float = _env_float("LUMEN_COHERENCE_THRESHOLD", 0.4)
    reflection_prompt_count: int = _env_int("LUMEN_REFLECTION_PROMPT_COUNT", 3)
    max_episodes_per_session: int = _env_int("LUMEN_MAX_EPISODES", 12)
    max_observations_per_episode: int = _env_int("LUMEN_MAX_OBSERVATIONS", 25)
    max_causal_chains_per_episode: int = _env_int("LUMEN_MAX_CAUSAL_CHAINS", 5)
    max_causal_steps_per_chain: int = _env_int("LUMEN_MAX_CAUSAL_STEPS", 12)
    max_extraction_attempts: int = _env_int("LUMEN_MAX_EXTRACTION_ATTEMPTS", 3)


@dataclass(frozen=True)
class ObservabilityConfig:
    """
    Configuration for logging.

    Logs are written as one JSON object per line, which makes them easy to
    grep, parse, and filter by trace id.

    Environment variables:
      LUMEN_LOG_LEVEL   — DEBUG / INFO / WARNING / ERROR
      LUMEN_LOG_FILE    — where the JSON log file is written
      LUMEN_LOG_CONSOLE — "false" to silence console output
    """

    log_level: str = _env("LUMEN_LOG_LEVEL", "INFO")
    log_file: str = _env("LUMEN_LOG_FILE", "./logs/lumen.jsonl")
    log_to_console: bool = _env_bool("LUMEN_LOG_CONSOLE", True)
    console_json: bool = False
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5


@dataclass(frozen=True)
class ProviderConfig:
    """
    Single point of configuration for every AI provider role in Lumen.

    Each role (see lumen.schemas.enums.ModelRole) independently maps to a
    (provider, model) pair. This is the ONLY place a role resolves to an
    actual vendor + model — the abstraction layers read from here and never
    hardcode a vendor or assume a deployment locality.

    Deliberately excludes any privacy/security-tier concept. A maintainer
    who wants guaranteed-local processing configures every *_provider
    field to a local provider (e.g. "ollama", "whisper_cpp") — that is a
    deployment choice made once, here, not a runtime decision the pipeline
    makes per piece of content.

    Two rules this class exists to enforce:

      - Provider selection belongs to whoever deploys Lumen, not to whoever
        writes the journal entries. Values come from the environment and never
        from the user_settings table; there is no runtime switcher and no UI.
      - Credentials live in the environment and are never persisted. Lumen has
        no api_keys table and no secrets store. Credentials are exposed here as
        properties rather than fields, so they cannot be captured by asdict(),
        a repr, an equality check, or anything else that walks the fields —
        see the note on gemini_api_key.

    Environment variables override every field independently:
      LUMEN_LIGHTWEIGHT_PROVIDER / LUMEN_LIGHTWEIGHT_MODEL
      LUMEN_THINKING_PROVIDER / LUMEN_THINKING_MODEL
      LUMEN_EMBEDDING_PROVIDER / LUMEN_EMBEDDING_MODEL
      LUMEN_TRANSCRIPTION_PROVIDER / LUMEN_TRANSCRIPTION_MODEL
      LUMEN_TTS_PROVIDER / LUMEN_TTS_MODEL
    """

    lightweight_provider: str = _env("LUMEN_LIGHTWEIGHT_PROVIDER", "gemini")
    lightweight_model: str = _env("LUMEN_LIGHTWEIGHT_MODEL", "gemini-2.5-flash")

    thinking_provider: str = _env("LUMEN_THINKING_PROVIDER", "gemini")
    thinking_model: str = _env("LUMEN_THINKING_MODEL", "gemini-2.5-pro")

    embedding_provider: str = _env("LUMEN_EMBEDDING_PROVIDER", "gemini")
    embedding_model: str = _env("LUMEN_EMBEDDING_MODEL", "text-embedding-004")

    transcription_provider: str = _env("LUMEN_TRANSCRIPTION_PROVIDER", "whisper_cpp")
    transcription_model: str = _env("LUMEN_TRANSCRIPTION_MODEL", "base.en")

    tts_provider: str = _env("LUMEN_TTS_PROVIDER", "macos")
    tts_model: str = _env("LUMEN_TTS_MODEL", "default")

    # Where a local Ollama daemon is listening.
    ollama_host: str = _env("LUMEN_OLLAMA_HOST", "http://localhost:11434")

    # How long to wait for a model, and how hard to try again when a call fails
    # for reasons that have nothing to do with the answer (a dropped
    # connection, a busy server, a hit rate limit).
    timeout_seconds: float = _env_float("LUMEN_LLM_TIMEOUT_SECONDS", 60.0)
    thinking_timeout_seconds: float = _env_float("LUMEN_THINKING_TIMEOUT_SECONDS", 180.0)
    max_attempts: int = _env_int("LUMEN_LLM_MAX_ATTEMPTS", 3)
    backoff_base_seconds: float = _env_float("LUMEN_LLM_BACKOFF_BASE", 0.5)
    backoff_max_seconds: float = _env_float("LUMEN_LLM_BACKOFF_MAX", 8.0)

    # Rate limits get a much longer ceiling than other failures. Cloud quotas
    # are usually counted per minute, so three quick retries all land inside
    # the same exhausted minute and fail together. One longer wait that
    # crosses into the next minute is worth more than several short ones.
    rate_limit_backoff_max_seconds: float = _env_float("LUMEN_LLM_RATE_LIMIT_BACKOFF_MAX", 65.0)

    # How many texts go into one embedding request, and how many requests run
    # at the same time. Concurrency is off by default: firing several requests
    # at a metered cloud API is the quickest way to trip its rate limit.
    embed_batch_size: int = _env_int("LUMEN_EMBED_BATCH_SIZE", 32)
    embed_max_workers: int = _env_int("LUMEN_EMBED_MAX_WORKERS", 1)

    # How wide the vectors from the embedding model are, for a model Lumen has
    # not been told about. Normally unset, because the widths of the models we
    # know are already recorded. Setting it is how somebody says "this is a new
    # model and I know its width", instead of being blocked.
    embedding_dimensions: int | None = _env_optional_int("LUMEN_EMBEDDING_DIMENSIONS")

    # Low temperature because extraction should give the same answer twice.
    # Kept here rather than in each provider so switching providers cannot
    # quietly change how repeatable the pipeline is.
    temperature: float = _env_float("LUMEN_LLM_TEMPERATURE", 0.2)

    # Prompts are journal text. Turning this on writes them to the log file,
    # which is useful when debugging and a privacy problem otherwise.
    log_prompts: bool = _env_bool("LUMEN_LOG_PROMPTS", False)

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

    def resolve_timeout(self, role: ModelRole) -> float:
        """
        How long to wait for a given role before giving up.

        Deep-reasoning models take much longer than fast ones, so they get a
        larger budget rather than every call being held to the slowest.
        """
        if role is ModelRole.THINKING:
            return self.thinking_timeout_seconds
        return self.timeout_seconds

    @property
    def gemini_api_key(self) -> str | None:
        """
        The Gemini credential, read from the environment on every access.

        This is a property, not a field, and that is the whole point. Config
        objects get snapshotted — pipeline_jobs.config_snapshot stores one on
        every run — and anything that walks the dataclass fields would carry a
        plaintext key into the database with it. A property is invisible to
        asdict(), replace(), repr(), and ==, so the key has no path into any
        store, log line, or error message unless someone asks for it by name.

        Reading it fresh each time also means a rotated key takes effect
        without a restart.
        """
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


@dataclass(frozen=True)
class AppConfig:
    """
    Top-level application config. All provider constructors read from this.

    Environment variables override defaults:
      LUMEN_GRAPH_DB_PATH   — path for Kuzu database
      LUMEN_VECTOR_LOCATION — ":memory:" or path for Qdrant
      LUMEN_USER_ID         — identifier for the single local user
      See ProviderConfig, OperationalConfig and ObservabilityConfig for the rest.
    """

    graph: GraphConfig = field(default_factory=GraphConfig)
    vector: VectorConfig = field(default_factory=VectorConfig)
    providers: ProviderConfig = field(default_factory=ProviderConfig)
    operational: OperationalConfig = field(default_factory=OperationalConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)

    # The personal build has one user. Multi-user deployments set this per request.
    user_id: str = _env("LUMEN_USER_ID", "local")
