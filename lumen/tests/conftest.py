"""
Shared fixtures for the Lumen test suite.

Provides one valid, fully-populated instance of each node model so that
this goal's tests — and Goals 5-10's tests later — can build on known-good
data instead of re-authoring construction boilerplate.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime

import pytest

from lumen.config import OperationalConfig, ProviderConfig
from lumen.providers.factory import reset_provider_cache
from lumen.providers.fake import fake_scripts
from lumen.observability.logging import JsonFormatter, TraceIdFilter
from lumen.observability.trace import bind_trace
from lumen.operational.engine import create_ops_engine
from lumen.operational.migrator import upgrade_to_head
from lumen.operational.schemas import BufferMessageRecord
from lumen.operational.sqlalchemy_impl import SQLAlchemyOperationalStore

from lumen.schemas.enums import (
    CandidateRetrievalSource,
    ContradictionResolutionStatus,
    DecisionStatus,
    Domain,
    EntryClass,
    LifecycleState,
    LoopCategory,
    ModelRole,
    NodeStatus,
    ObservationStatus,
    ObservationType,
    PrincipleDomain,
    Provenance,
    ReconciliationAction,
    RelationshipToUser,
    ReportType,
    SentimentTrend,
    SignalStrength,
    SourceModality,
)
from lumen.schemas.nodes import (
    AdoptedPrincipleNode,
    BeliefNode,
    CausalChainNode,
    CausalStepNode,
    ContradictionNode,
    DecisionAuditNode,
    EpisodeNode,
    EventNode,
    LessonNode,
    LifecycleHistoryEntry,
    MacroextractionReportNode,
    ObservationNode,
    OpenLoopNode,
    PatternNode,
    PersonEntityNode,
    RollbackPointer,
    SessionNode,
)

NOW = datetime(2026, 6, 11, 10, 30, 0)
TODAY = date(2026, 6, 11)


@pytest.fixture
def sample_episode() -> EpisodeNode:
    return EpisodeNode(
        node_id="ep_2026_06_11_001",
        entry_id="entry_2026_06_11_raw",
        occurred_at=NOW,
        created_at=NOW,
        valid_from=NOW,
        event_date=TODAY,
        session_label="A",
        source_modality=SourceModality.VOICE_NOTE,
        entry_class=EntryClass.REFLECTION,
        episode_summary="User reflects on a slow, deliberate decision-making approach.",
        overarching_themes=["career_decision"],
        episode_index=1,
        total_episodes_in_entry=2,
        coreference_map_id="coref_2026_06_11_001",
        reconciliation_status="COMPLETE",
        raw_text_hash="sha256:a3f",
    )


@pytest.fixture
def sample_observation() -> ObservationNode:
    return ObservationNode(
        node_id="obs_2026_06_11_004",
        episode_id="ep_2026_06_11_001",
        occurred_at=NOW,
        created_at=NOW,
        valid_from=NOW,
        type=ObservationType.PATTERN,
        content="User consistently defers major decisions until fully informed.",
        raw_evidence=["I just can't pull the trigger until I feel ready"],
        signal_strength=SignalStrength.HIGH,
        provenance=Provenance.USER_GENERATED,
        status=ObservationStatus.ACTIVE,
        extraction_model="gemini-2.0-flash",
    )


@pytest.fixture
def sample_event() -> EventNode:
    return EventNode(
        node_id="evt_example_001",
        episode_id="ep_example_001",
        occurred_at=NOW,
        created_at=NOW,
        valid_from=NOW,
        event_summary="Went to a local cafe alone to eat, breaking a pattern of avoidance.",
        signal_strength=SignalStrength.HIGH,
        raw_evidence=["I just went out to a local cafe alone"],
    )


@pytest.fixture
def sample_session() -> SessionNode:
    return SessionNode(
        node_id="sess_example_001",
        episode_id="ep_example_002",
        occurred_at=NOW,
        created_at=NOW,
        valid_from=NOW,
        event_date=TODAY,
        session_label="A",
        session_summary="Deep conversational breakthrough resolving an identity conflict.",
        signal_strength=SignalStrength.HIGH,
        participant_entities=["user", "ai_facilitator"],
    )


@pytest.fixture
def sample_causal_chain() -> CausalChainNode:
    return CausalChainNode(
        node_id="chain_2026_06_11_001",
        episode_id="ep_2026_06_11_001",
        created_at=NOW,
        valid_from=NOW,
        chain_summary="Headache-triggered slowdown leading to energy restoration.",
        is_anticipatory=False,
        step_count=6,
    )


@pytest.fixture
def sample_causal_step() -> CausalStepNode:
    return CausalStepNode(
        node_id="step_2026_06_11_001_s3",
        chain_id="chain_2026_06_11_001",
        step_index=3,
        step_type="ACTION",
        content="Relieved all expectations, went at very slow pace",
        created_at=NOW,
    )


@pytest.fixture
def sample_pattern() -> PatternNode:
    return PatternNode(
        node_id="pat_decision_saturation",
        version=1,
        created_at=NOW,
        valid_from=NOW,
        last_reinforced_at=NOW,
        pattern_name="Deliberate Information Saturation Before Decision",
        pattern_description="User systematically over-collects information before committing.",
        domain=Domain.COGNITIVE_STYLE,
        signal_strength=SignalStrength.HIGH,
        provenance=Provenance.USER_GENERATED,
        evidence_count=7,
        archetype_tags=["high_conscientiousness"],
    )


@pytest.fixture
def sample_belief() -> BeliefNode:
    return BeliefNode(
        node_id="bel_introvert_001",
        version=1,
        created_at=NOW,
        valid_from=NOW,
        last_reinforced_at=NOW,
        belief_statement="I am an introvert who needs solitude to recharge.",
        belief_source_summary="Expressed explicitly and reinforced in 4 entries.",
        domain=Domain.SELF_CONCEPT,
        signal_strength=SignalStrength.HIGH,
        provenance=Provenance.USER_GENERATED,
        evidence_count=5,
    )


@pytest.fixture
def sample_lesson() -> LessonNode:
    return LessonNode(
        node_id="les_example_001",
        created_at=NOW,
        valid_from=NOW,
        lesson_statement="Volunteering before feeling ready accelerates growth.",
        domain=Domain.CAREER,
        signal_strength=SignalStrength.HIGH,
        lesson_confidence=0.84,
        evidence_episodes=["ep_example_006"],
    )


@pytest.fixture
def sample_adopted_principle() -> AdoptedPrincipleNode:
    return AdoptedPrincipleNode(
        node_id="prin_work_relationship_001",
        created_at=NOW,
        valid_from=NOW,
        adopted_at=NOW,
        principle_statement="Before every work session, perform an autotelic shift.",
        principle_name="Autotelic Shift + Relationship Check",
        domain=PrincipleDomain.PRODUCTIVITY,
        lifecycle_state=LifecycleState.TRYING,
        lifecycle_updated_at=NOW,
        source_session_id="session-id-example",
        provenance=Provenance.CO_CREATED,
        last_referenced_at=NOW,
        evidence_count=1,
        lifecycle_history=[
            LifecycleHistoryEntry(state=LifecycleState.TRYING, at=NOW, reason="User committed to this.")
        ],
    )


@pytest.fixture
def sample_person() -> PersonEntityNode:
    return PersonEntityNode(
        node_id="person_jordan_001",
        canonical_name="Jordan",
        aliases=["J", "my colleague Jordan"],
        first_mentioned_at=NOW,
        last_mentioned_at=NOW,
        mention_count=12,
        relationship_to_user=RelationshipToUser.COLLEAGUE,
        relationship_sentiment_trend=SentimentTrend.NEUTRAL_TO_NEGATIVE,
        linked_observation_types=[ObservationType.RELATIONAL_DYNAMIC],
    )


@pytest.fixture
def sample_decision_audit() -> DecisionAuditNode:
    return DecisionAuditNode(
        node_id="d_2026_06_11_001",
        created_at=NOW,
        action=ReconciliationAction.MERGE,
        source_node_id="obs_2026_06_11_004",
        target_node_id="pat_decision_saturation",
        edge_type_created="same_as",
        edge_id="edge_2026_06_11_009",
        confidence=0.91,
        confidence_runner_up=0.83,
        runner_up_action=ReconciliationAction.REINFORCE,
        model_used="gemini-2.0-flash",
        model_role=ModelRole.LIGHTWEIGHT,
        candidate_retrieval_source=CandidateRetrievalSource.SEMANTIC,
        status=DecisionStatus.ACTIVE,
        rollback_pointer=RollbackPointer(
            edge_to_invalidate="edge_2026_06_11_009",
            nodes_to_requeue=["obs_2026_06_11_004"],
        ),
    )


@pytest.fixture
def sample_contradiction() -> ContradictionNode:
    return ContradictionNode(
        node_id="con_example_001",
        created_at=NOW,
        valid_from=NOW,
        belief_a_id="bel_introvert_001",
        belief_b_id="bel_2026_06_11_expressive_social",
        contradiction_summary="User holds simultaneous, incompatible beliefs.",
        decision_id="d_2026_06_11_003",
        resolution_status=ContradictionResolutionStatus.UNRESOLVED,
    )


@pytest.fixture
def sample_macro_report() -> MacroextractionReportNode:
    return MacroextractionReportNode(
        node_id="macro_2026_06_01_weekly",
        created_at=NOW,
        report_type=ReportType.WEEKLY,
        period_start=datetime(2026, 5, 25),
        period_end=datetime(2026, 6, 1),
        episodes_analyzed=14,
        model_used="gemini-2.0-pro",
    )


@pytest.fixture
def sample_open_loop() -> OpenLoopNode:
    return OpenLoopNode(
        node_id="loop_2026_04_15_001",
        created_at=NOW,
        valid_from=NOW,
        loop_description="Am I staying because I find meaning, or avoiding uncertainty?",
        loop_category=LoopCategory.CAREER_IDENTITY,
        provenance=Provenance.AI_GENERATED,
        source_episode_id="ep_2026_04_15_002",
        last_referenced_at=NOW,
        linked_patterns=["pat_decision_saturation"],
    )


# ---------------------------------------------------------------------------
# Operational store and observability fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ops_config(tmp_path) -> OperationalConfig:
    """Point the operational store at a database file of this test's own."""
    return OperationalConfig(db_url=f"sqlite:///{tmp_path / 'ops.db'}")


@pytest.fixture
def ops_engine(ops_config):
    """
    A database engine with the schema built by the real migrations.

    Tests run the migrations rather than creating tables directly, so every
    test run also checks that the migrations actually work.
    """
    engine = create_ops_engine(ops_config)
    upgrade_to_head(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def ops_store(ops_config, ops_engine) -> SQLAlchemyOperationalStore:
    """A ready-to-use operational store backed by a migrated database."""
    store = SQLAlchemyOperationalStore(ops_config, engine=ops_engine)
    yield store
    store.close()


@pytest.fixture
def bound_trace():
    """Run a test inside a known trace id, so assertions can name it."""
    with bind_trace("test-trace-0001") as trace_id:
        yield trace_id


@pytest.fixture
def captured_logs():
    """
    Collect log lines as parsed JSON.

    Attaches a handler that formats records exactly the way the real file
    handler does, so tests check the actual output rather than a stand-in.
    """
    records: list[dict] = []

    class _Collector(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.setFormatter(JsonFormatter())
            self.addFilter(TraceIdFilter())

        def emit(self, record: logging.LogRecord) -> None:
            records.append(json.loads(self.format(record)))

    handler = _Collector()
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)


# ---------------------------------------------------------------------------
# Preprocessing fixtures
# ---------------------------------------------------------------------------

# Phrases unique to each preprocessing prompt. A scripted fake provider is
# keyed on these, so one script can answer every step of the stage and each
# reply is matched to the step that asked for it.
PROMPT_KEYS = {
    "conversation": "CONVERSATION:",
    "normalize_voice": "TRANSCRIPT:",
    "normalize_text": "Below is a journal entry someone typed",
    "structure": "SPLITTING",
    "triage": "EPISODES:",
    "reflection": "too short to analyse properly",
}


@pytest.fixture
def make_event():
    """
    Build a decayed session out of plain strings.

    Messages are given as (role, text) pairs so a test can describe the
    conversation it needs on one line and ignore ids and timestamps.
    """

    def _build(
        messages,
        *,
        session_id: str = "sess_test_001",
        source_modality: SourceModality = SourceModality.TEXT_ENTRY,
        event_date: date = TODAY,
        message_dates: list[date] | None = None,
    ):
        from lumen.schemas.pipeline import BufferMessage, SessionDecayEvent

        buffer = []
        for index, (role, content) in enumerate(messages):
            buffer.append(
                BufferMessage(
                    message_id=f"m{index}",
                    role=role,
                    content=content,
                    timestamp=datetime(2026, 6, 11, 21, index, tzinfo=UTC),
                    event_date=(
                        message_dates[index] if message_dates else event_date
                    ),
                )
            )
        return SessionDecayEvent(
            session_id=session_id,
            user_id="local",
            event_date=event_date,
            source_modality=source_modality,
            message_count=len(buffer),
            raw_buffer=buffer,
            triggered_at=datetime(2026, 6, 11, 23, 0, tzinfo=UTC),
        )

    return _build


@pytest.fixture
def scripted_providers():
    """
    Build a pair of fake models that answer preprocessing from a script.

    The script is keyed by step name; each reply is the JSON that step's
    response shape expects. Both models share the script, so a test does not
    have to care which of the two a given step happens to use.
    """
    from lumen.providers.fake import FakeLLMProvider

    def _build(replies: dict[str, str]):
        script = {PROMPT_KEYS[step]: reply for step, reply in replies.items()}
        return (
            FakeLLMProvider(dict(script), role=ModelRole.LIGHTWEIGHT),
            FakeLLMProvider(dict(script), role=ModelRole.THINKING),
        )

    return _build


# ---------------------------------------------------------------------------
# Extraction fixtures
# ---------------------------------------------------------------------------

# Phrases unique to each extraction prompt, used the same way as the
# preprocessing keys above.
EXTRACTION_PROMPT_KEYS = {
    "reflection": "FINDINGS (observations)",
    "raw_capture": "Below is a short or unclear journal entry",
}

# A worked example with enough in it to produce several kinds of finding: an
# event that happened, a feeling, a named person, and a sequence running from
# a trigger to a lesson.
EPISODE_TEXT = (
    "I went to the cafe alone today and ate there without the usual dread. "
    "Then I saw what Alex had shipped this week and felt small and behind. "
    "I sat with it for a while and the pressure lifted on its own. "
    "I think the comparing is the thing that hurts, not the gap itself."
)


@pytest.fixture
def make_episode():
    """Build one preprocessed episode, with sensible defaults for everything."""

    def _build(
        text: str = EPISODE_TEXT,
        *,
        entry_class: EntryClass = EntryClass.REFLECTION,
        episode_index: int = 1,
        total: int = 1,
        summary: str = "Comparing himself to Alex after a good morning",
    ):
        from lumen.schemas.pipeline import PreprocessedEpisode

        return PreprocessedEpisode(
            episode_id=f"ep_2026_06_11_{episode_index:03d}",
            episode_summary=summary,
            episode_index=episode_index,
            total_episodes_in_entry=total,
            cleaned_text=text,
            entry_class=entry_class,
            coherence_score=0.8 if entry_class is EntryClass.REFLECTION else 0.1,
            raw_text_hash="hash_of_the_text",
        )

    return _build


@pytest.fixture
def make_extraction_input(make_episode):
    """
    Build the object the extraction stage takes in.

    People named in the entry are given as plain strings, since almost every
    test cares only about whether a name was known, not how it was resolved.
    """

    def _build(
        text: str = EPISODE_TEXT,
        *,
        entry_class: EntryClass = EntryClass.REFLECTION,
        episode_index: int = 1,
        total: int = 1,
        people: list[str] | None = None,
        ambiguous: list[tuple[str, list[str]]] | None = None,
        co_created_spans: list[str] | None = None,
        session_label: str = "A",
    ):
        from lumen.schemas.pipeline import (
            AmbiguousRef,
            CoreferenceMap,
            MicroextractionInput,
            ResolvedEntity,
        )

        coreference = CoreferenceMap(
            entry_id="sess_test_001",
            resolved_entities=[
                ResolvedEntity(
                    span="he",
                    resolved_to=name,
                    confidence=0.9,
                    resolution_basis="named earlier in the entry",
                )
                for name in (people or [])
            ],
            ambiguous_refs=[
                AmbiguousRef(span=span, candidates=names, reason="two people nearby")
                for span, names in (ambiguous or [])
            ],
        )
        return MicroextractionInput(
            episode=make_episode(
                text,
                entry_class=entry_class,
                episode_index=episode_index,
                total=total,
            ),
            coreference_map=coreference,
            entry_id="sess_test_001",
            event_date=TODAY,
            occurred_at=datetime(2026, 6, 11, 20, 0, tzinfo=UTC),
            source_modality=SourceModality.TEXT_ENTRY,
            session_label=session_label,
            co_created_spans=co_created_spans or [],
        )

    return _build


@pytest.fixture
def extraction_providers():
    """
    Build a pair of fake models that answer extraction from a script.

    Keyed by path name — "reflection" or "raw_capture" — so a test writes
    only the reply for the path it is exercising. Both models share the
    script, so a test does not have to track which of the two the stage
    picks.
    """
    from lumen.providers.fake import FakeLLMProvider

    def _build(replies: dict[str, str]):
        script = {EXTRACTION_PROMPT_KEYS[path]: reply for path, reply in replies.items()}
        return (
            FakeLLMProvider(dict(script), role=ModelRole.LIGHTWEIGHT, model="fake-light"),
            FakeLLMProvider(dict(script), role=ModelRole.THINKING, model="fake-thinker"),
        )

    return _build


@pytest.fixture
def buffer_with_messages(ops_store):
    """A buffer holding three messages, ready for tests that need real data."""
    buffer = ops_store.buffers.find_or_create(
        user_id="local", event_date=TODAY, session_label="A"
    )
    for index, (role, content) in enumerate(
        [
            ("USER", "Rough day. I kept second-guessing the architecture call."),
            ("AI", "What made it feel unresolved?"),
            ("USER", "I think I was avoiding the tradeoff rather than making it."),
        ]
    ):
        ops_store.buffers.append_message(
            buffer.session_id,
            BufferMessageRecord(
                message_id=f"msg_{index}",
                session_id=buffer.session_id,
                seq=index,
                role=role,
                content=content,
                timestamp=datetime(2026, 6, 11, 21, index, tzinfo=UTC),
                event_date=TODAY,
            ),
        )
    return ops_store.buffers.get_buffer(buffer.session_id)


# ---------------------------------------------------------------------------
# Model provider fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_providers():
    """
    Give every test a clean provider setup.

    Providers are cached for the life of the process, and scripted replies are
    left in a shared place for the factory to find. Both would otherwise leak
    from one test into the next.
    """
    reset_provider_cache()
    fake_scripts.clear()
    yield
    reset_provider_cache()
    fake_scripts.clear()


@pytest.fixture
def provider_config() -> ProviderConfig:
    """
    Provider settings with the waiting taken out.

    Retry delays are set to zero so tests that exercise failure paths finish
    immediately instead of actually sleeping.
    """
    return ProviderConfig(
        max_attempts=3,
        backoff_base_seconds=0.0,
        backoff_max_seconds=0.0,
        rate_limit_backoff_max_seconds=0.0,
        embed_batch_size=32,
        embed_max_workers=1,
    )


@pytest.fixture
def recording_sleeper():
    """
    A stand-in for sleeping that just remembers what it was asked to wait.

    Lets a test check the backoff pattern without spending the time.
    """
    waits: list[float] = []
    return waits, waits.append
