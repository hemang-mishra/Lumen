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

import itertools
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
      LUMEN_MAX_DECISION_ATTEMPTS — tries at getting a readable decision reply
      LUMEN_PASS_A_KEEP           — how many search matches survive ranking
      LUMEN_PASS_A_OVERFETCH      — how many are fetched before ranking
      LUMEN_PASS_B_KEEP           — how many anchor matches survive per anchor
      LUMEN_CANDIDATE_CAP         — most candidates handed to reconciliation

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

    # Reading an entry again can recover a finding that came back malformed.
    # A decision reply has nothing to correct — either it arrived readable or
    # it did not — so it gets one repeat rather than two, and a run that
    # still cannot be read hands the whole episode to a person.
    max_decision_attempts: int = _env_int("LUMEN_MAX_DECISION_ATTEMPTS", 2)

    # More matches are fetched than are kept, because ranking happens after
    # the search: a rare and weighty node can sit just below the cut on raw
    # distance and belong above it once its weight is taken into account.
    # Fetching only what is kept would throw it away before that.
    pass_a_keep: int = _env_int("LUMEN_PASS_A_KEEP", 5)
    pass_a_overfetch: int = _env_int("LUMEN_PASS_A_OVERFETCH", 20)
    pass_b_keep: int = _env_int("LUMEN_PASS_B_KEEP", 5)
    merged_candidate_cap: int = _env_int("LUMEN_CANDIDATE_CAP", 8)


@dataclass(frozen=True)
class IngestConfig:
    """
    Configuration for taking in conversations exported from elsewhere.

    Environment variables:
      LUMEN_ENABLE_INGEST    — "false" to refuse uploads entirely
      LUMEN_IMPORT_TIMEZONE  — the zone the person's days are measured in

    The time zone is the one that matters. An imported conversation is filed
    under the day it started, and which day that is depends on where the
    person was standing: nine in the evening in India is half past three in
    the afternoon in UTC, and the day either has or has not turned over
    depending on which of those you measure. Exports record an instant;
    only the reader knows the calendar it belongs to.

    Turning ingestion off is for a deployment that should be able to read
    the graph and nothing more. The upload routes are then not mounted at
    all, rather than mounted and refusing.
    """

    enabled: bool = _env_bool("LUMEN_ENABLE_INGEST", True)
    timezone: str = _env("LUMEN_IMPORT_TIMEZONE", "UTC")

    def tzinfo(self) -> Any:
        """
        The configured zone, or UTC if it cannot be resolved.

        A misspelled zone name falls back rather than refusing to start.
        Getting the day wrong by a few hours is a small, visible problem;
        a service that will not boot over a typo in an optional setting is
        a larger one.
        """
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError, ModuleNotFoundError):
            import logging

            logging.getLogger(__name__).warning(
                "unknown time zone %r, so imported days will be measured in UTC",
                self.timezone,
            )
            from datetime import UTC

            return UTC


@dataclass(frozen=True)
class QueryConfig:
    """
    Tuning knobs for reading a live conversation.

    Everything here trades recall against the rhythm of a conversation.
    Somebody talking will forgive an answer that misses a connection; they
    will not forgive a pause before every reply.

    Environment variables:
      LUMEN_FORMULATION_TIMEOUT_SECONDS — how long a turn may wait on the model
      LUMEN_FORMULATION_CONTEXT_TURNS   — how much of the conversation it sees
      LUMEN_MAX_TRIGGERS_PER_TURN       — most reasons to search kept from one turn
      LUMEN_MAX_TRIGGER_KEYWORDS        — most keywords kept per reason
      LUMEN_ERA_VOCABULARY_LIMIT        — most past periods offered to the model
      LUMEN_SESSION_MAX_TURNS           — most turns a day's session holds in memory
      LUMEN_FORMULATION_MAX_WORKERS     — threads available for timing out a call

    The timeout is the number that matters most. It is deliberately shorter
    than any other model call in the system: this one runs on every single
    turn, and the person is waiting on it. Six hundred milliseconds is about
    as long as a real call to a fast model takes, so the deadline catches
    calls that have gone wrong rather than calls that are merely working.

    The turn window exists because some turns cannot be read alone. "I don't
    feel that anymore" says nothing without the sentence before it.
    """

    formulation_timeout_seconds: float = _env_float(
        "LUMEN_FORMULATION_TIMEOUT_SECONDS", 0.6
    )
    formulation_context_turns: int = _env_int("LUMEN_FORMULATION_CONTEXT_TURNS", 4)
    max_triggers_per_turn: int = _env_int("LUMEN_MAX_TRIGGERS_PER_TURN", 3)
    max_keywords_per_trigger: int = _env_int("LUMEN_MAX_TRIGGER_KEYWORDS", 6)
    era_vocabulary_limit: int = _env_int("LUMEN_ERA_VOCABULARY_LIMIT", 50)
    session_max_turns: int = _env_int("LUMEN_SESSION_MAX_TURNS", 200)
    formulation_max_workers: int = _env_int("LUMEN_FORMULATION_MAX_WORKERS", 4)


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

    # How a request picks between several configured credentials. "random"
    # holds no state and so stays even across threads and processes;
    # "round_robin" is strictly even but only within one process. The keys
    # themselves are not here — see gemini_api_keys.
    key_rotation_strategy: str = _env("LUMEN_KEY_ROTATION_STRATEGY", "random")

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

        Where several keys are configured this is the first of them, which
        keeps every caller that only ever wanted "a credential" working
        unchanged. Anything that spreads load across keys reads
        gemini_api_keys instead.
        """
        keys = self.gemini_api_keys
        return keys[0] if keys else None

    @property
    def gemini_api_keys(self) -> tuple[str, ...]:
        """
        Every Gemini credential this deployment has, in configured order.

        Quotas are metered per key, so a deployment with several keys can do
        several times the work in a minute — provided requests are actually
        spread across them. This reads the whole set; lumen.providers.keyring
        decides which one a given request uses.

        Three ways of saying it, checked in this order and then merged:

          GEMINI_API_KEYS=key-one,key-two,key-three   one line, comma separated
          GEMINI_API_KEY_1=... GEMINI_API_KEY_2=...   numbered, one per line
          GEMINI_API_KEY=... / GOOGLE_API_KEY=...     the single-key form

        The numbered form is read from 1 upwards and stops at the first gap,
        so a commented-out GEMINI_API_KEY_3 truncates the list rather than
        leaving a hole — silently skipping a gap would make a typo'd variable
        name look like it was working.

        A property, not a field, for the reason described on gemini_api_key:
        nothing that walks the dataclass can carry a plaintext key into a
        config snapshot.
        """
        found: list[str] = []

        for raw in (os.environ.get("GEMINI_API_KEYS") or "").split(","):
            if raw.strip():
                found.append(raw.strip())

        for index in itertools.count(1):
            value = (os.environ.get(f"GEMINI_API_KEY_{index}") or "").strip()
            if not value:
                break
            found.append(value)

        single = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if single and single.strip():
            found.append(single.strip())

        # Dropping repeats keeps the same key pasted under two names from
        # looking like two meters when it is one.
        return tuple(dict.fromkeys(found))


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
    query: QueryConfig = field(default_factory=QueryConfig)
    ingest: IngestConfig = field(default_factory=IngestConfig)

    # The personal build has one user. Multi-user deployments set this per request.
    user_id: str = _env("LUMEN_USER_ID", "local")
