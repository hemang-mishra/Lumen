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


def _split(raw: str) -> tuple[str, ...]:
    """
    A comma-separated setting as the list it means.

    Empty entries are dropped and whitespace trimmed, so a trailing comma or
    a line wrapped for readability does not become an empty allowed origin —
    which would be an origin nothing matches, or worse, one that something
    does.
    """
    return tuple(part.strip() for part in (raw or "").split(",") if part.strip())


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

    # Where everybody's graphs live. One directory per person underneath it,
    # named from their identifier — which is what makes isolation structural
    # rather than a condition every query has to remember.
    db_root: str = _env("LUMEN_GRAPH_DB_ROOT", "./data/graphs")

    # How many people's graphs may be open at once. A real ceiling: the graph
    # is embedded and every open one costs file handles and memory. Past it,
    # the least recently used idle graph is closed, and reopening costs
    # milliseconds next time somebody needs it.
    max_open_graphs: int = _env_int("LUMEN_MAX_OPEN_GRAPHS", 32)


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
      LUMEN_HITL_SNOOZE_HOURS    — how long a deferred item stays out of sight
      LUMEN_HITL_AUTO_RESOLVE_DAYS — days after deferring before an item
                                     settles itself

    The two review-queue timings are settings rather than constants because
    they are the pace at which the system asks somebody for their attention,
    and the right pace is a matter of how they use it.
    """

    db_url: str = _env("LUMEN_OPS_DB_URL", "sqlite:///./lumen_ops.db")
    echo_sql: bool = _env_bool("LUMEN_OPS_DB_ECHO", False)
    session_decay_minutes: int = _env_int("LUMEN_SESSION_DECAY_MINUTES", 120)
    hitl_queue_cap: int = _env_int("LUMEN_HITL_QUEUE_CAP", 40)
    hitl_snooze_hours: int = _env_int("LUMEN_HITL_SNOOZE_HOURS", 24)
    hitl_auto_resolve_days: int = _env_int("LUMEN_HITL_AUTO_RESOLVE_DAYS", 7)


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

    And for fetching what a turn points at:

      LUMEN_RETRIEVAL_BUDGET_SECONDS    — the whole wait a turn may spend searching
      LUMEN_PASS_A_TIMEOUT_SECONDS      — how long the meaning-based search may take
      LUMEN_PASS_B_TIMEOUT_SECONDS      — how long the anchor lookups may take
      LUMEN_CONV_PASS_A_KEEP            — matches kept from the meaning-based search
      LUMEN_CONV_PASS_A_OVERFETCH       — matches fetched before ranking
      LUMEN_CONV_PASS_B_KEEP            — records kept per anchor
      LUMEN_CONV_CANDIDATE_CAP          — most records handed on from one turn
      LUMEN_SESSION_BUFFER_SIZE         — how much of today's thread is remembered
      LUMEN_SESSION_BUFFER_IDLE_TURNS   — turns of silence before it is forgotten
      LUMEN_SESSION_BOOST               — how much being part of today's thread counts
      LUMEN_SESSION_BOOST_THRESHOLD     — how close it must be to still count
      LUMEN_ANCHOR_BASE_SCORE           — what an exact anchor match is worth
      LUMEN_RETRIEVAL_MAX_WORKERS       — threads available for the parallel searches

    Every deadline here is a **safety net, not a pace-setter.** That is the
    one thing to understand before changing any of them. Each one exists to
    catch a call that has gone wrong — a hung connection, a provider that
    will never answer — and not to hurry a call that is merely working. A
    deadline set close to how long the work actually takes does not make
    anything faster; it just converts the slow tail of a working system into
    turns that silently retrieved nothing.

    That is why the numbers are generous. The failure they guard against is
    a felt pause before a reply; the failure they cause when set too tight is
    an assistant that has forgotten somebody, which is worse and is invisible
    from the outside. A pause before a considered answer is normal in this
    kind of conversation. Being answered by something that does not remember
    you is not.

    The turn window exists because some turns cannot be read alone. "I don't
    feel that anymore" says nothing without the sentence before it.

    The search budget is a limit on the whole search rather than on each part
    of it, because one wall clock is what the person waiting actually
    experiences. The per-pass limits sit under it so that one stuck pass
    cannot spend the shared budget and leave the other with nothing.
    """

    # Three seconds, not six hundred milliseconds. A real call to a hosted
    # fast model takes 300–800ms on a good day and several times that on a
    # bad one, so a sub-second deadline was firing on calls that were working
    # — and every one of those cost the turn its retrieval. This catches a
    # call that has genuinely hung and leaves a slow one alone.
    formulation_timeout_seconds: float = _env_float(
        "LUMEN_FORMULATION_TIMEOUT_SECONDS", 3.0
    )
    formulation_context_turns: int = _env_int("LUMEN_FORMULATION_CONTEXT_TURNS", 4)
    max_triggers_per_turn: int = _env_int("LUMEN_MAX_TRIGGERS_PER_TURN", 3)
    max_keywords_per_trigger: int = _env_int("LUMEN_MAX_TRIGGER_KEYWORDS", 6)
    era_vocabulary_limit: int = _env_int("LUMEN_ERA_VOCABULARY_LIMIT", 50)
    session_max_turns: int = _env_int("LUMEN_SESSION_MAX_TURNS", 200)

    # Wider than it was, because a longer deadline means each turn holds its
    # worker for longer. An abandoned call cannot be stopped and finishes on
    # its own, so the pool has to have room for the one being waited on plus
    # the ones nobody is waiting on any more.
    formulation_max_workers: int = _env_int("LUMEN_FORMULATION_MAX_WORKERS", 8)

    # Twenty seconds, and the history of this number is the argument for it.
    # It began at three, to stay inside the time somebody spends reading the
    # previous reply. It went to eight once it was clear that a pause before
    # a considered answer is normal here and a missed connection is not. It
    # is now twenty, for the same reason carried to its end: the search
    # contains a model call, an embedding call and an index lookup, and any
    # of the three can have a slow minute without being broken. Waiting is
    # cheap and recoverable. Answering somebody as though their history were
    # not there is neither.
    retrieval_budget_seconds: float = _env_float("LUMEN_RETRIEVAL_BUDGET_SECONDS", 20.0)
    semantic_pass_timeout_seconds: float = _env_float(
        "LUMEN_PASS_A_TIMEOUT_SECONDS", 15.0
    )

    # The anchor lookups read the graph and call no model, so this used to be
    # half a second. That stopped being safe when the single-writer lock
    # landed: these reads now serialise against an import's write
    # transaction and can wait for as long as one runs. Five seconds is not
    # how long the lookup takes — it is how long it may sit behind somebody
    # else's write before giving up.
    structural_pass_timeout_seconds: float = _env_float(
        "LUMEN_PASS_B_TIMEOUT_SECONDS", 5.0
    )

    # More matches are fetched than kept, for the reason the pipeline fetches
    # extra: ranking happens after the search, and a weighty record can sit
    # just below the cut on raw distance and belong above it once its weight
    # counts.
    conversational_pass_a_keep: int = _env_int("LUMEN_CONV_PASS_A_KEEP", 5)
    conversational_pass_a_overfetch: int = _env_int("LUMEN_CONV_PASS_A_OVERFETCH", 20)
    conversational_pass_b_keep: int = _env_int("LUMEN_CONV_PASS_B_KEEP", 5)
    conversational_candidate_cap: int = _env_int("LUMEN_CONV_CANDIDATE_CAP", 12)

    # Today's thread. Five records, forgotten after five turns of nobody
    # coming back to them — long enough to carry a subject across a
    # digression, short enough that this morning does not colour tonight.
    session_buffer_size: int = _env_int("LUMEN_SESSION_BUFFER_SIZE", 5)
    session_buffer_max_idle_turns: int = _env_int("LUMEN_SESSION_BUFFER_IDLE_TURNS", 5)
    session_boost_multiplier: float = _env_float("LUMEN_SESSION_BOOST", 1.3)
    session_boost_threshold: float = _env_float("LUMEN_SESSION_BOOST_THRESHOLD", 0.35)

    # The same question asked of the stand-in measurement, and deliberately a
    # harder bar. When a record has no position in the index, relevance falls
    # back to counting how many of the turn's words appear in its text — and
    # a third of them appearing is something that happens by accident, where
    # a third of the way between two positions in the index does not. One
    # number for both scales makes the fallback wave through everything held,
    # on exactly the turns where the search was already in trouble.
    session_boost_keyword_threshold: float = _env_float(
        "LUMEN_SESSION_BOOST_KEYWORD_THRESHOLD", 0.6
    )

    # What an exact anchor match counts as when ordering a mixed list. It is
    # a policy number, not a measurement — a record found because a name
    # matched has no similarity score, and never gets given one.
    anchor_base_score: float = _env_float("LUMEN_ANCHOR_BASE_SCORE", 0.6)

    # What happens to a search that missed its deadline and finished anyway.
    # It cannot be stopped, so its answer is either kept for the next turn or
    # thrown away — and the next turn is the only one where it is still about
    # roughly what is being talked about. Ranked below anything fresh,
    # because it answers the question before this one.
    carry_forward_turns: int = _env_int("LUMEN_CARRY_FORWARD_TURNS", 1)
    deferred_penalty: float = _env_float("LUMEN_DEFERRED_PENALTY", 0.9)
    retrieval_max_workers: int = _env_int("LUMEN_RETRIEVAL_MAX_WORKERS", 8)


@dataclass(frozen=True)
class ChatConfig:
    """
    Tuning knobs for what the assistant is actually sent.

    Two separate things live here. The briefing allowance decides how much of
    somebody's own history goes in front of the assistant, and it varies with
    how they sound — a wall of history in front of a light question makes the
    answer worse, and somebody thinking out loud can use everything there is.
    The memory settings decide how much of the conversation itself it can
    still see after an hour of talking.

    Environment variables:
      LUMEN_CONTEXT_TOKENS_VULNERABLE / _STABLE / _REFLECTIVE
      LUMEN_CONTEXT_RECORDS_VULNERABLE / _STABLE / _REFLECTIVE
      LUMEN_CONTEXT_DUPLICATE_THRESHOLD — how alike two briefings may be
      LUMEN_CONTEXT_PER_KIND_CAP        — most records of any one kind
      LUMEN_CHARS_PER_TOKEN             — how token counts are estimated
      LUMEN_CHAT_RECENT_TURNS           — turns kept word for word
      LUMEN_CHAT_SUMMARY_EVERY          — turns between summary refreshes
      LUMEN_CHAT_SUMMARY_WORDS          — how long the summary may run
      LUMEN_CHAT_PREVIOUS_DAYS          — earlier days carried into today
      LUMEN_CHAT_PREVIOUS_DAY_LOOKBACK  — how far back to reach to find them
      LUMEN_CHAT_PREVIOUS_DAY_TOKENS    — the allowance those days share
      LUMEN_VOICE_ENABLED               — whether replies are spoken
      LUMEN_MAX_AUDIO_BYTES             — the largest recording accepted

    There is no crisis setting, and that is deliberate. Nothing is injected
    when somebody is in acute distress, and making that a number somebody
    could raise would turn a clinical decision into a configuration mistake
    waiting to happen.
    """

    vulnerable_tokens: int = _env_int("LUMEN_CONTEXT_TOKENS_VULNERABLE", 400)
    stable_tokens: int = _env_int("LUMEN_CONTEXT_TOKENS_STABLE", 800)
    reflective_tokens: int = _env_int("LUMEN_CONTEXT_TOKENS_REFLECTIVE", 1500)

    vulnerable_records: int = _env_int("LUMEN_CONTEXT_RECORDS_VULNERABLE", 2)
    stable_records: int = _env_int("LUMEN_CONTEXT_RECORDS_STABLE", 4)
    reflective_records: int = _env_int("LUMEN_CONTEXT_RECORDS_REFLECTIVE", 6)

    # Two briefings that read almost alike are one briefing taking up two
    # slots. A strong theme otherwise fills the whole allowance with
    # variations on itself.
    duplicate_threshold: float = _env_float("LUMEN_CONTEXT_DUPLICATE_THRESHOLD", 0.8)
    per_kind_cap: int = _env_int("LUMEN_CONTEXT_PER_KIND_CAP", 3)

    chars_per_token: float = _env_float("LUMEN_CHARS_PER_TOKEN", 4.0)

    # How much of the conversation is sent word for word, and how often
    # everything older is folded into a few sentences. Twelve turns is about
    # the span somebody refers back to without re-explaining themselves.
    recent_turns: int = _env_int("LUMEN_CHAT_RECENT_TURNS", 12)
    summary_every: int = _env_int("LUMEN_CHAT_SUMMARY_EVERY", 8)
    summary_words: int = _env_int("LUMEN_CHAT_SUMMARY_WORDS", 200)

    # How much of the last few days today's conversation opens with. Life
    # runs on longer than one night, and a thread dropped on Monday is often
    # picked up on Thursday — so this counts days that hold a conversation
    # rather than squares on the calendar, and reaches back a fortnight to
    # find them. Free: every day already writes a summary of itself.
    # Whether replies are spoken. Off by default because it needs a model
    # that many deployments will not have configured, and a chat that refuses
    # to start over a missing voice would be a poor trade.
    voice_enabled: bool = _env_bool("LUMEN_VOICE_ENABLED", False)
    max_audio_bytes: int = _env_int("LUMEN_MAX_AUDIO_BYTES", 25 * 1024 * 1024)

    previous_days: int = _env_int("LUMEN_CHAT_PREVIOUS_DAYS", 3)
    previous_day_lookback: int = _env_int("LUMEN_CHAT_PREVIOUS_DAY_LOOKBACK", 14)
    previous_day_tokens: int = _env_int("LUMEN_CHAT_PREVIOUS_DAY_TOKENS", 700)


@dataclass(frozen=True)
class MacroConfig:
    """
    Settings for the periodic reports that ask "what keeps happening?".

    Everything here is a threshold in a judgement that would otherwise be
    buried in code: how many times something has to recur before it is worth
    naming, how long something has to go unmentioned before it counts as
    ignored, how many separate things have to move together before that is a
    shift rather than a coincidence.

    They are gathered in one place because they are the report's opinions,
    and opinions belong somewhere a person can read and change them.
    """

    enabled: bool = _env_bool("LUMEN_MACRO_ENABLED", True)

    # How long after a period ends before it is reported on. Reports cover
    # when things happened rather than when they were written, and a report
    # is never rewritten, so running the instant a period ends would freeze
    # it before the last few entries about it had been made.
    weekly_grace_days: int = _env_int("LUMEN_MACRO_WEEKLY_GRACE_DAYS", 1)
    monthly_grace_days: int = _env_int("LUMEN_MACRO_MONTHLY_GRACE_DAYS", 3)
    quarterly_grace_days: int = _env_int("LUMEN_MACRO_QUARTERLY_GRACE_DAYS", 3)

    # How far back to look for periods that were never reported on, and how
    # many may be caught up in one go. The cap matters: a system switched off
    # for a year would otherwise wake up and start dozens of model calls at
    # once.
    catchup_periods: int = _env_int("LUMEN_MACRO_CATCHUP_PERIODS", 6)
    max_runs_per_invocation: int = _env_int("LUMEN_MACRO_MAX_RUNS", 4)

    # The near-real-time scan. A burst of beliefs branching or contradicting
    # inside two days is the shape of something shifting while it happens.
    shadow_window_hours: int = _env_int("LUMEN_MACRO_SHADOW_WINDOW_HOURS", 48)
    shadow_min_decisions: int = _env_int("LUMEN_MACRO_SHADOW_MIN_DECISIONS", 3)
    shadow_min_targets: int = _env_int("LUMEN_MACRO_SHADOW_MIN_TARGETS", 2)
    shadow_repeat_hours: int = _env_int("LUMEN_MACRO_SHADOW_REPEAT_HOURS", 24)

    # How much of a window one report will read. A cap rather than a
    # suggestion, and hitting it is recorded in the report rather than
    # hidden, because a partial summary presented as a whole one is a wrong
    # answer that looks right.
    max_episodes_per_window: int = _env_int("LUMEN_MACRO_MAX_EPISODES", 200)
    max_nodes_per_kind: int = _env_int("LUMEN_MACRO_MAX_NODES_PER_KIND", 500)

    # How much of the arithmetic makes it into the report.
    top_patterns_limit: int = _env_int("LUMEN_MACRO_TOP_PATTERNS", 10)
    high_signal_limit: int = _env_int("LUMEN_MACRO_HIGH_SIGNAL_LIMIT", 25)
    open_loop_limit: int = _env_int("LUMEN_MACRO_OPEN_LOOP_LIMIT", 25)
    ignored_lesson_limit: int = _env_int("LUMEN_MACRO_IGNORED_LESSON_LIMIT", 10)
    aging_limit: int = _env_int("LUMEN_MACRO_AGING_LIMIT", 25)

    # How often something has to happen before the report says it recurs.
    repeated_lesson_min_episodes: int = _env_int("LUMEN_MACRO_REPEATED_LESSON_MIN", 3)
    relational_min_observations: int = _env_int("LUMEN_MACRO_RELATIONAL_MIN", 2)
    arc_min_episodes: int = _env_int("LUMEN_MACRO_ARC_MIN_EPISODES", 3)

    # How long a lesson can go unmentioned before it counts as ignored, and
    # how far back to look for lessons that might qualify.
    ignored_lesson_days: int = _env_int("LUMEN_MACRO_IGNORED_LESSON_DAYS", 14)
    ignored_lesson_lookback_days: int = _env_int("LUMEN_MACRO_IGNORED_LOOKBACK_DAYS", 180)

    # How quiet a pattern has to have gone before a report mentions it at all.
    # A pattern nobody has written about for five weeks is not news; one
    # nobody has written about for half a year is.
    aging_report_days: int = _env_int("LUMEN_MACRO_AGING_REPORT_DAYS", 180)

    # How long a quiet pattern is worth less for is not settled here. It is
    # the same question search ranking answers, and two answers to one
    # question means a report stating a number the system does not use, so
    # both read ScoringConfig.

    # What counts as an identity-level shift rather than a few patterns
    # moving independently, and how far back the comparison reaches.
    archetype_min_patterns: int = _env_int("LUMEN_MACRO_ARCHETYPE_MIN_PATTERNS", 5)
    archetype_window_days: int = _env_int("LUMEN_MACRO_ARCHETYPE_WINDOW_DAYS", 90)

    # The single call that writes the report's prose. Capped by length
    # because a quarter of somebody's history does not fit in a prompt, and
    # retried twice because it is nobody's live request.
    narrative_max_chars: int = _env_int("LUMEN_MACRO_NARRATIVE_MAX_CHARS", 20000)
    narrative_attempts: int = _env_int("LUMEN_MACRO_NARRATIVE_ATTEMPTS", 2)
    narrative_excerpt_chars: int = _env_int("LUMEN_MACRO_EXCERPT_CHARS", 220)


@dataclass(frozen=True)
class ScoringConfig:
    """
    How much a stored record is worth when history is searched.

    Four things change what a record counts for: how strong a signal it was
    when it was written, how long ago it was last true of the person, whether
    they confirmed it themselves or an assistant suggested it, and how often
    it has turned out to be worth showing.

    They live together in one place because more than one part of Lumen ranks
    records, and if two of them disagreed about what a record is worth, the
    same record would come out in a different order depending on who asked.

    Every number here can be turned off with one switch, so today's ranking
    can be compared against the old one without changing any code.
    """

    decay_enabled: bool = _env_bool("LUMEN_DECAY_ENABLED", True)

    # How many days of quiet it takes to move a record into each band. A
    # record younger than the first is worth its full value.
    fresh_days: int = _env_int("LUMEN_DECAY_FRESH_DAYS", 30)
    cooling_days: int = _env_int("LUMEN_DECAY_COOLING_DAYS", 180)
    dormant_days: int = _env_int("LUMEN_DECAY_DORMANT_DAYS", 365)

    # What a record in each band is worth. Nothing ever reaches zero: an old
    # record ranks lower, and is still reachable.
    cooling_weight: float = _env_float("LUMEN_DECAY_COOLING_WEIGHT", 0.85)
    stale_weight: float = _env_float("LUMEN_DECAY_STALE_WEIGHT", 0.70)
    dormant_weight: float = _env_float("LUMEN_DECAY_DORMANT_WEIGHT", 0.50)

    # What something the assistant suggested and the person never confirmed
    # is worth beside something they said themselves.
    unverified_weight: float = _env_float("LUMEN_TRUST_UNVERIFIED", 0.5)

    # How much each time a record proved useful lifts it, and the ceiling on
    # that lift. The ceiling is the point: being shown makes a record more
    # likely to be shown again, and without a cap that loop runs away.
    frequency_enabled: bool = _env_bool("LUMEN_FREQUENCY_ENABLED", True)
    frequency_step: float = _env_float("LUMEN_FREQUENCY_STEP", 0.1)
    frequency_cap: float = _env_float("LUMEN_FREQUENCY_CAP", 1.5)


@dataclass(frozen=True)
class MaintenanceConfig:
    """
    Settings for the jobs that run over the whole history rather than a window.

    Erasing somebody's data and scanning every year of it for a long-running
    pattern have nothing in common except that both walk everything, both take
    a while, and both need to be told how much to do at once so a live
    conversation is not left waiting behind them.
    """

    # How many records are rewritten in one go. Each batch is its own
    # transaction, so the graph is locked for a moment at a time rather than
    # for the whole sweep.
    erasure_batch_size: int = _env_int("LUMEN_ERASURE_BATCH", 200)

    # What a request has to say back before anything is erased. Erasure
    # cannot be undone, so it cannot be reached by a request that merely
    # arrives at the right address.
    erasure_confirm_phrase: str = _env("LUMEN_ERASURE_CONFIRM", "ERASE")

    # How many separate occasions a pattern needs before its history is worth
    # laying out, and how many of those occasions to show.
    proof_min_instances: int = _env_int("LUMEN_PROOF_MIN_INSTANCES", 10)
    proof_key_instances: int = _env_int("LUMEN_PROOF_KEY_INSTANCES", 5)


@dataclass(frozen=True)
class AuthConfig:
    """
    Who is allowed in, and how they prove it.

    Two values are missing from the fields below and that is deliberate. The
    signing key and the Google secret are properties rather than settings,
    for the reason spelled out on `gemini_api_key`: config objects get
    snapshotted onto every pipeline run, and anything that walks the fields
    of one would carry a credential into the database with it. A property is
    invisible to asdict(), replace(), repr() and ==, so neither has a path
    into a stored record, a log line or an error body unless somebody asks
    for it by name.

    Off by default, so a single-user deployment and the test suite behave
    exactly as they did before any of this existed.
    """

    enabled: bool = _env_bool("LUMEN_AUTH_ENABLED", False)

    # Who the tokens say they are from and who they are for. Both are
    # verified rather than merely read, so a token minted for something else
    # is refused even when the signature is good.
    issuer: str = _env("LUMEN_JWT_ISSUER", "lumen")
    audience: str = _env("LUMEN_JWT_AUDIENCE", "lumen-api")

    # Short enough that ending a session takes effect quickly, long enough
    # that a conversation is not interrupted to renew one.
    access_ttl_seconds: int = _env_int("LUMEN_ACCESS_TOKEN_TTL_SECONDS", 900)
    refresh_ttl_seconds: int = _env_int("LUMEN_REFRESH_TOKEN_TTL_SECONDS", 2_592_000)

    # The Google credential. The id is public by construction — the browser
    # needs it — and the secret is not, which is why only one of them is here.
    google_client_id: str = _env("GOOGLE_OAUTH_CLIENT_ID", "")
    google_redirect_uri: str = _env("GOOGLE_OAUTH_REDIRECT_URI", "")

    # Who may sign up at all. "allowlist" rather than "open" on purpose: an
    # open sign-in on a reachable host hands a database, a search index and a
    # model budget to whoever finds the port.
    signup_mode: str = _env("LUMEN_SIGNUP_MODE", "allowlist")
    allowed_emails: str = _env("LUMEN_ALLOWED_EMAILS", "")

    # Exact origins, because a wildcard with credentials is not merely lax —
    # browsers refuse the combination outright.
    allowed_origins: str = _env("LUMEN_ALLOWED_ORIGINS", "")

    # How many sign-in attempts one caller or one address may make before
    # being asked to wait. Sign-in is the only door open to somebody who has
    # not proved anything yet.
    signin_attempts: int = _env_int("LUMEN_SIGNIN_ATTEMPTS", 10)
    signin_window_seconds: int = _env_int("LUMEN_SIGNIN_WINDOW_SECONDS", 300)

    # Whether the session cookie may only travel over HTTPS. True everywhere
    # it should ever be false is a mistake — but a deployment running on
    # plain http for local development cannot sign in at all otherwise,
    # because a cookie marked Secure is simply not sent. The two settings
    # move together for the same reason: browsers refuse SameSite=None
    # without Secure, so an insecure deployment gets the only combination
    # that works rather than one that silently drops the cookie.
    cookie_secure: bool = _env_bool("LUMEN_COOKIE_SECURE", True)

    @property
    def cookie_samesite(self) -> str:
        """
        How freely the session cookie travels.

        "none" when it is Secure, because the browser and the API are
        different origins and anything stricter would not send it at all.
        "lax" otherwise, since that is the strongest setting an insecure
        deployment can actually use.
        """
        return "none" if self.cookie_secure else "lax"

    @property
    def jwt_private_key(self) -> str | None:
        """
        The Ed25519 signing key, read from the environment on every access.

        A property, not a field, for the reason described on
        `gemini_api_key`: this is the one value in the system that can mint a
        session for anybody, and it must have no path into a snapshot, a log
        or a repr.

        Absent in a deployment that only verifies tokens, which is the whole
        point of signing them asymmetrically.
        """
        return os.environ.get("LUMEN_JWT_PRIVATE_KEY") or None

    @property
    def jwt_public_keys(self) -> str | None:
        """
        The public half, or halves — several so a rotation can verify tokens
        minted by the old key and the new one at the same time.

        Not a secret, and kept beside its private half rather than as a field
        so the two are read the same way and cannot drift.
        """
        return os.environ.get("LUMEN_JWT_PUBLIC_KEYS") or None

    @property
    def google_client_secret(self) -> str | None:
        """The Google secret. Server-side only, and never a field."""
        return os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET") or None

    @property
    def origins(self) -> tuple[str, ...]:
        """The exact origins allowed to send credentials."""
        return _split(self.allowed_origins)

    @property
    def allowlist(self) -> frozenset[str]:
        """Who may sign up, lowercased so a capital letter is not a refusal."""
        return frozenset(address.lower() for address in _split(self.allowed_emails))


@dataclass(frozen=True)
class SchedulerConfig:
    """
    How often the product does the things nobody presses a button for.

    Everything here is an interval, and the intervals are generous on
    purpose. None of these jobs is urgent — a report written an hour late is
    the same report — and the cost of asking too often is a laptop that never
    settles.
    """

    enabled: bool = _env_bool("LUMEN_SCHEDULER_ENABLED", True)

    # How often the clock wakes at all. Every job's own interval is rounded
    # up to this, so it is the shortest anything can happen.
    poll_seconds: float = _env_float("LUMEN_SCHEDULER_POLL_SECONDS", 60.0)

    # How long between each job. Looking for finished conversations is the
    # frequent one, because it is the only one somebody might be waiting on.
    watch_every_seconds: int = _env_int("LUMEN_WATCH_EVERY_SECONDS", 300)
    reports_every_seconds: int = _env_int("LUMEN_REPORTS_EVERY_SECONDS", 3600)
    shadow_every_seconds: int = _env_int("LUMEN_SHADOW_EVERY_SECONDS", 3600)
    sweep_every_seconds: int = _env_int("LUMEN_SWEEP_EVERY_SECONDS", 21600)

    # How many finished conversations to hand over in one go. A cap rather
    # than a suggestion: somebody importing a year of history at once should
    # not have every day of it dispatched in the same minute.
    max_dispatch_per_tick: int = _env_int("LUMEN_MAX_DISPATCH_PER_TICK", 5)

    # How many recent events to keep for a page that has just connected.
    # Nothing here is a record — it is what somebody missed while opening a
    # tab, and everything worth keeping is readable from its own endpoint.
    event_history: int = _env_int("LUMEN_EVENT_HISTORY", 50)


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

    # The model that talks to the person. Configured on its own because
    # writing a warm reply in under a second and doing the overnight
    # extraction reasoning are different jobs with different needs — tying
    # them together means every improvement to one hurts the other.
    conversation_provider: str = _env("LUMEN_CONVERSATION_PROVIDER", "gemini")
    conversation_model: str = _env("LUMEN_CONVERSATION_MODEL", "gemini-2.5-flash")

    embedding_provider: str = _env("LUMEN_EMBEDDING_PROVIDER", "gemini")
    embedding_model: str = _env("LUMEN_EMBEDDING_MODEL", "text-embedding-004")

    transcription_provider: str = _env("LUMEN_TRANSCRIPTION_PROVIDER", "gemini")
    transcription_model: str = _env("LUMEN_TRANSCRIPTION_MODEL", "gemini-2.5-flash")

    tts_provider: str = _env("LUMEN_TTS_PROVIDER", "gemini")
    tts_model: str = _env("LUMEN_TTS_MODEL", "gemini-2.5-flash-preview-tts")

    # Where a local Ollama daemon is listening.
    ollama_host: str = _env("LUMEN_OLLAMA_HOST", "http://localhost:11434")

    # How long to wait for a model, and how hard to try again when a call fails
    # for reasons that have nothing to do with the answer (a dropped
    # connection, a busy server, a hit rate limit).
    #
    # Both are deliberately longer than any call should need. Nobody is
    # waiting on these — the live conversation bounds itself from outside, in
    # QueryConfig — so the only thing a short timeout achieves here is
    # throwing away work that was about to succeed and making the pipeline
    # re-run it. A long entry given to a reasoning model is genuinely slow,
    # and slow is not the same as stuck.
    timeout_seconds: float = _env_float("LUMEN_LLM_TIMEOUT_SECONDS", 120.0)
    thinking_timeout_seconds: float = _env_float("LUMEN_THINKING_TIMEOUT_SECONDS", 300.0)
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
            ModelRole.CONVERSATION: (
                self.conversation_provider,
                self.conversation_model,
            ),
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
      LUMEN_GRAPH_DB_ROOT   — directory holding one Kuzu database per person
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
    chat: ChatConfig = field(default_factory=ChatConfig)
    ingest: IngestConfig = field(default_factory=IngestConfig)
    macro: MacroConfig = field(default_factory=MacroConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    maintenance: MaintenanceConfig = field(default_factory=MaintenanceConfig)

    # Who a request belongs to when there is no request to ask.
    #
    # Renamed from `user_id` when identity became a real thing. The rename is
    # the point: identity now arrives per request, and this is only the
    # fallback for the callers that have no request to carry one — the
    # command line, the simulation runner, the background jobs. Nothing under
    # `lumen/api/` may read it, and a test says so.
    default_user_id: str = _env("LUMEN_USER_ID", "local")
