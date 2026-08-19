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
    QualityGateDecision,
    ReconciliationAction,
    RelationshipToUser,
    ReportType,
    SentimentTrend,
    SignalStrength,
    SourceModality,
    StructuralAnchorType,
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


@pytest.fixture(scope="session")
def test_log_dir(tmp_path_factory):
    """One throwaway directory for anything the suite logs to a file."""
    return tmp_path_factory.mktemp("lumen-test-logs")


@pytest.fixture(autouse=True)
def the_real_databases_are_out_of_reach(tmp_path_factory, monkeypatch):
    """
    Point every default at somewhere disposable.

    A test that builds an application without naming a configuration gets the
    shipped defaults, and the shipped defaults are the databases a running
    Lumen uses. One such test was quietly creating a Kuzu database in the
    repository root on every run and opening the real operational store — and
    the day the schema moved on, that leftover database failed the suite in a
    test that had nothing to do with it.

    Anything that genuinely cares about a path still passes its own; this
    only decides where "unspecified" points.
    """
    root = tmp_path_factory.mktemp("lumen-test-stores")
    monkeypatch.setenv("LUMEN_GRAPH_DB_PATH", str(root / "graph.db"))
    monkeypatch.setenv("LUMEN_OPS_DB_URL", f"sqlite:///{root / 'ops.db'}")
    monkeypatch.setenv("LUMEN_VECTOR_LOCATION", ":memory:")


@pytest.fixture(autouse=True)
def logging_stays_inside_the_test(test_log_dir, monkeypatch):
    """
    Keep the suite out of the log file the real service writes.

    Two things leak without this. A test that builds an AppConfig without
    naming a log file gets the shipped default — ./logs/lumen.jsonl, the same
    file a running Lumen appends to — so scripted failures land in the
    production log looking exactly like real ones. And configure_logging
    attaches its handler to the *root* logger, which nothing detaches at
    shutdown, so one test entering an application's lifespan redirects every
    test that runs after it for the rest of the session.

    So the default is pointed somewhere disposable, and the root logger is put
    back the way it was found.
    """
    monkeypatch.setenv("LUMEN_LOG_FILE", str(test_log_dir / "lumen.jsonl"))

    root = logging.getLogger()
    handlers_before = list(root.handlers)
    level_before = root.level
    try:
        yield
    finally:
        for handler in list(root.handlers):
            if handler not in handlers_before:
                root.removeHandler(handler)
                handler.close()
        for handler in handlers_before:
            if handler not in root.handlers:
                root.addHandler(handler)
        root.setLevel(level_before)


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
    # A conversation is split by naming turns and a monologue by returning
    # text, and both prompts say SPLITTING. The dialogue one is listed first
    # and keyed on a phrase only it contains, so scripting one never answers
    # the other with a reply of the wrong shape.
    "structure_by_turn": "Give each episode the numbers of the turns",
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
    "correction": "Some of what you returned could not be used",
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


# ---------------------------------------------------------------------------
# Retrieval fixtures
# ---------------------------------------------------------------------------

# Retrieval is tested against real embedded stores rather than stand-ins.
# Every question it asks is a query against typed edge tables or a vector
# index, and a stand-in answering from a dict would pass whether or not the
# query was right — which is the only thing worth testing here. Both run
# in-process, so nothing has to be installed or running.

RETRIEVAL_PROMPT_KEYS = {"hyde": "ITEMS:"}


@pytest.fixture
def graph_store(tmp_path):
    """A real, empty Kuzu database."""
    from lumen.graph.kuzu_impl import KuzuGraphProvider

    provider = KuzuGraphProvider(str(tmp_path / "retrieval_db"))
    provider.init_schema()
    yield provider
    provider.close()


@pytest.fixture
def vector_store():
    """A real, empty Qdrant collection held in memory."""
    from lumen.vector.qdrant_impl import QdrantVectorProvider

    provider = QdrantVectorProvider(location=":memory:", vector_size=768)
    provider.init_collection()
    yield provider
    provider.close()


@pytest.fixture
def embedder():
    """A stand-in embedder that gives the same text the same vector."""
    from lumen.providers.fake import FakeEmbeddingProvider

    return FakeEmbeddingProvider(dimensions=768)


@pytest.fixture
def seed_observation(graph_store, vector_store, embedder):
    """
    Put one historical observation into both stores.

    Written to the graph and the vector index together, since that is how
    the orchestrator will write it and retrieval needs both halves: the
    index to find it, the graph to say what it is.
    """

    def _seed(
        node_id: str,
        content: str,
        *,
        observation_type: str = "PATTERN",
        signal: str = "STANDARD",
        status: str = "ACTIVE",
        episode_id: str = "ep_old",
        indexed: bool = True,
        vector: list[float] | None = None,
    ) -> str:
        graph_store.write_node(
            "ObservationNode",
            {
                "node_id": node_id,
                "episode_id": episode_id,
                "occurred_at": "2026-01-01T00:00:00Z",
                "created_at": "2026-01-01T00:00:00Z",
                "valid_from": "2026-01-01T00:00:00Z",
                "type": observation_type,
                "content": content,
                "signal_strength": signal,
                "provenance": "USER_GENERATED",
                "verification_status": "IMPLICIT",
                "extraction_confidence": "STANDARD",
                "status": status,
                "extraction_model": "fake",
                "extraction_attempt": 1,
            },
        )
        if indexed:
            vector_store.upsert(
                node_id,
                vector if vector is not None else embedder.embed_text(content),
                {"node_type": "ObservationNode", "status": status},
            )
        return node_id

    return _seed


@pytest.fixture
def vector_at_angle():
    """
    Build a vector a chosen distance from the reference one.

    The stand-in embedder turns text into a hash, so two sentences that
    mean nearly the same thing land nowhere near each other. Tests about
    ranking need distances they can choose, so they place vectors by angle
    instead: the cosine similarity to `vector_at_angle(0)` is exactly the
    cosine of the angle given.
    """
    import math

    def _build(radians: float, dimensions: int = 768) -> list[float]:
        return [math.cos(radians), math.sin(radians)] + [0.0] * (dimensions - 2)

    return _build


@pytest.fixture
def make_extraction():
    """Build an ExtractionResult out of plain text, for retrieval to work on."""

    def _build(
        *contents: str,
        episode_id: str = "ep_new",
        status: ObservationStatus = ObservationStatus.ACTIVE,
        events: list[str] | None = None,
        sessions: list[str] | None = None,
        person_refs: list[str] | None = None,
    ):
        from lumen.schemas.nodes import EventNode, ObservationNode, SessionNode
        from lumen.schemas.pipeline import ExtractionResult

        moment = datetime(2026, 6, 11, 20, 0, tzinfo=UTC)
        return ExtractionResult(
            episode_id=episode_id,
            observations=[
                ObservationNode(
                    node_id=f"obs_new_{index}",
                    created_at=moment,
                    valid_from=moment,
                    episode_id=episode_id,
                    occurred_at=moment,
                    type=ObservationType.PATTERN,
                    content=text,
                    signal_strength=SignalStrength.STANDARD,
                    provenance=Provenance.USER_GENERATED,
                    status=status,
                    extraction_model="fake",
                    person_refs=person_refs or [],
                )
                for index, text in enumerate(contents, start=1)
            ],
            events=[
                EventNode(
                    node_id=f"evt_new_{index}",
                    created_at=moment,
                    valid_from=moment,
                    episode_id=episode_id,
                    occurred_at=moment,
                    event_summary=text,
                    signal_strength=SignalStrength.STANDARD,
                )
                for index, text in enumerate(events or [], start=1)
            ],
            sessions=[
                SessionNode(
                    node_id=f"sess_new_{index}",
                    created_at=moment,
                    valid_from=moment,
                    episode_id=episode_id,
                    occurred_at=moment,
                    event_date=TODAY,
                    session_label="A",
                    session_summary=text,
                    signal_strength=SignalStrength.STANDARD,
                )
                for index, text in enumerate(sessions or [], start=1)
            ],
            extraction_model="fake-thinker",
            validation_passed=True,
        )

    return _build


@pytest.fixture
def hyde_provider():
    """A stand-in model that echoes each finding back as its search text."""
    from lumen.providers.fake import FakeLLMProvider

    def _build(texts: list[str] | None = None, *, reply: str | None = None):
        if reply is None:
            reply = json.dumps(
                {
                    "hypotheticals": [
                        {"index": index, "text": text}
                        for index, text in enumerate(texts or [], start=1)
                    ]
                }
            )
        return FakeLLMProvider([reply], role=ModelRole.LIGHTWEIGHT, model="fake-light")

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


# ---------------------------------------------------------------------------
# Reconciliation fixtures
# ---------------------------------------------------------------------------

# Phrases unique to each reconciliation prompt, used the same way as the
# preprocessing and extraction keys above.
RECONCILIATION_PROMPT_KEYS = {
    "decision": "Below are things someone noticed in one journal entry",
    "escalation": "A faster model read these items",
}

MOMENT = datetime(2026, 6, 11, 20, 0, tzinfo=UTC)


@pytest.fixture
def make_candidate():
    """Build one candidate match, found either by meaning or by an anchor."""

    def _build(
        node_id: str = "pat_old",
        *,
        node_type: str = "PatternNode",
        preview: str = "an existing pattern",
        score: float | None = 0.9,
        anchor: str | None = None,
    ):
        from lumen.schemas.pipeline import CandidateNode

        if anchor is not None:
            return CandidateNode(
                node_id=node_id,
                node_type=node_type,
                content_preview=preview,
                retrieval_source=CandidateRetrievalSource.STRUCTURAL,
                structural_anchor_type=StructuralAnchorType(anchor),
                structural_anchor_value="Alex",
            )
        return CandidateNode(
            node_id=node_id,
            node_type=node_type,
            content_preview=preview,
            similarity_score=score,
            retrieval_source=CandidateRetrievalSource.SEMANTIC,
        )

    return _build


@pytest.fixture
def make_item(make_candidate):
    """
    Build one thing to be decided about, with whatever the search found.

    Defaults to a finding of a kind that can become a lasting record, since
    that is the interesting case; the other kinds are asked for by name.
    """

    def _build(
        text: str = "I compare myself to people ahead of me",
        *,
        node_id: str = "obs_new_1",
        node_type: str = "ObservationNode",
        observation_type: ObservationType = ObservationType.PATTERN,
        signal: SignalStrength = SignalStrength.STANDARD,
        provenance: Provenance = Provenance.USER_GENERATED,
        candidates=None,
        person_refs: tuple[str, ...] = (),
        search_failed: bool = False,
        episode_id: str = "ep_new",
    ):
        from lumen.pipeline.reconciliation.contracts import DecisionItem
        from lumen.schemas.nodes import EventNode, ObservationNode, SessionNode

        if node_type == "ObservationNode":
            source = ObservationNode(
                node_id=node_id,
                created_at=MOMENT,
                valid_from=MOMENT,
                episode_id=episode_id,
                occurred_at=MOMENT,
                type=observation_type,
                content=text,
                signal_strength=signal,
                provenance=provenance,
                status=ObservationStatus.ACTIVE,
                extraction_model="fake",
                person_refs=list(person_refs),
            )
        elif node_type == "EventNode":
            source = EventNode(
                node_id=node_id,
                created_at=MOMENT,
                valid_from=MOMENT,
                episode_id=episode_id,
                occurred_at=MOMENT,
                event_summary=text,
                signal_strength=signal,
                person_refs=list(person_refs),
            )
        else:
            source = SessionNode(
                node_id=node_id,
                created_at=MOMENT,
                valid_from=MOMENT,
                episode_id=episode_id,
                occurred_at=MOMENT,
                event_date=TODAY,
                session_label="A",
                session_summary=text,
                signal_strength=signal,
            )

        return DecisionItem(
            source=source,
            node_type=node_type,
            text=text,
            observation_type=(
                observation_type if node_type == "ObservationNode" else None
            ),
            signal_strength=signal,
            provenance=provenance,
            episode_id=episode_id,
            person_refs=person_refs,
            candidates=tuple(candidates or ()),
            search_failed=search_failed,
        )

    return _build


@pytest.fixture
def make_settled(make_item):
    """
    Build a decision as it looks straight after being read, before checking.

    Tests for the checks use this so each one can start from exactly the
    reading it cares about, rather than scripting a model to produce it.
    """

    def _build(
        action: ReconciliationAction = ReconciliationAction.MERGE,
        *,
        item=None,
        target_node_id: str | None = "pat_old",
        target_type: str | None = "PatternNode",
        confidence: float = 0.95,
        runner_up: ReconciliationAction | None = None,
        runner_up_confidence: float | None = None,
        **extra,
    ):
        from lumen.pipeline.reconciliation.contracts import SettledDecision

        return SettledDecision(
            item=item if item is not None else make_item(),
            action=action,
            target_node_id=target_node_id,
            target_type=target_type,
            confidence=confidence,
            runner_up_action=runner_up,
            runner_up_confidence=runner_up_confidence,
            model_used="fake-light",
            **extra,
        )

    return _build


@pytest.fixture
def reconciliation_providers():
    """
    Build the pair of stand-in models reconciliation uses.

    Keyed by step — "decision" or "escalation" — so a test writes only the
    replies for the steps it exercises.
    """
    from lumen.providers.fake import FakeLLMProvider

    def _build(replies: dict[str, str]):
        script = {
            RECONCILIATION_PROMPT_KEYS[step]: reply for step, reply in replies.items()
        }
        return (
            FakeLLMProvider(dict(script), role=ModelRole.LIGHTWEIGHT, model="fake-light"),
            FakeLLMProvider(dict(script), role=ModelRole.THINKING, model="fake-thinker"),
        )

    return _build


@pytest.fixture
def seed_pattern(graph_store):
    """Put one existing pattern into the graph, with a chosen age."""

    def _seed(
        node_id: str = "pat_old",
        *,
        name: str = "Comparison spiral",
        description: str = "Comparing himself to peers and feeling behind",
        valid_from: str = "2026-06-01T00:00:00+00:00",
        status: str = "ACTIVE",
        evidence_count: int = 3,
        era_tag: str | None = None,
        domain: str = "EMOTIONAL",
        signal: str = "STANDARD",
    ) -> str:
        graph_store.write_node(
            "PatternNode",
            {
                "node_id": node_id,
                "version": 1,
                "created_at": valid_from,
                "valid_from": valid_from,
                "last_reinforced_at": valid_from,
                "pattern_name": name,
                "pattern_description": description,
                "domain": domain,
                "signal_strength": signal,
                "provenance": "USER_GENERATED",
                "verification_status": "IMPLICIT",
                "evidence_count": evidence_count,
                "query_frequency": 0,
                "is_canonical": True,
                "status": status,
                **({"era_tag": era_tag} if era_tag else {}),
            },
        )
        return node_id

    return _seed


@pytest.fixture
def seed_belief(graph_store):
    """Put one existing belief into the graph, with a chosen age."""

    def _seed(
        node_id: str = "bel_old",
        *,
        statement: str = "I need solitude to recharge",
        valid_from: str = "2026-06-01T00:00:00+00:00",
        provenance: str = "USER_GENERATED",
        version: int = 1,
    ) -> str:
        graph_store.write_node(
            "BeliefNode",
            {
                "node_id": node_id,
                "version": version,
                "created_at": valid_from,
                "valid_from": valid_from,
                "last_reinforced_at": valid_from,
                "belief_statement": statement,
                "belief_source_summary": "said so in an earlier entry",
                "domain": "SELF_CONCEPT",
                "signal_strength": "STANDARD",
                "provenance": provenance,
                "verification_status": "IMPLICIT",
                "evidence_count": 2,
                "query_frequency": 0,
                "is_contradicted": False,
                "status": "ACTIVE",
            },
        )
        return node_id

    return _seed


# ---------------------------------------------------------------------------
# Orchestration fixtures
#
# Running a whole entry touches every stage, so these build one script that
# answers all of them. Each stage's fake looks up its reply by a phrase
# unique to its own prompt, which means one dictionary can serve the lot and
# a test only has to override the step it cares about.
# ---------------------------------------------------------------------------

FULL_RUN_PROMPT_KEYS = {
    **PROMPT_KEYS,
    **{f"extract_{k}": v for k, v in EXTRACTION_PROMPT_KEYS.items()},
    **RETRIEVAL_PROMPT_KEYS,
    **RECONCILIATION_PROMPT_KEYS,
    # "ITEMS:" is enough to spot the search prompt when it is the only one
    # being scripted, but a whole run also sends the decision prompt, which
    # contains the same heading. A phrase unique to the search prompt keeps
    # the decision call from being answered with search text.
    "hyde": "write a single sentence as it might appear",
}

# One episode's worth of replies, enough to carry an entry all the way from
# raw text to saved records: it is cleaned, split into one episode, judged
# worth analysing, read, searched for, and decided about.
FULL_RUN_SCRIPT: dict[str, str] = {
    "normalize_text": json.dumps(
        {
            "cleaned_text": EPISODE_TEXT,
            "detected_languages": ["en"],
            "translated": False,
        }
    ),
    "structure": json.dumps(
        {
            "episodes": [
                {
                    "episode_summary": "Comparing himself to Alex after a good morning",
                    "text": EPISODE_TEXT,
                    "overarching_themes": ["comparison"],
                }
            ],
            "coreference": {
                "resolved_entities": [
                    {
                        "span": "he",
                        "resolved_to": "Alex",
                        "confidence": 0.9,
                        "resolution_basis": "named earlier in the entry",
                    }
                ],
                "ambiguous_refs": [],
            },
        }
    ),
    "triage": json.dumps(
        {
            "scores": [
                {
                    "episode_index": 1,
                    "coherence_score": 0.85,
                    "reason": "a clear thought with a feeling and a context",
                }
            ]
        }
    ),
    "extract_reflection": json.dumps(
        {
            "observations": [
                {
                    "type": "PATTERN",
                    "content": "Comparing himself to Alex is what causes the pain",
                    "raw_evidence": ["I think the comparing is the thing that hurts"],
                    "extraction_signal_strength": "HIGH",
                    "person_ref": "Alex",
                }
            ],
            "events": [
                {
                    "event_summary": "Ate at the cafe alone without dread",
                    "raw_evidence": ["I went to the cafe alone today"],
                    "person_refs": [],
                }
            ],
            "causal_mechanisms": [],
        }
    ),
    "extract_raw_capture": json.dumps(
        {"context": "A short note about the day", "emotion": None}
    ),
    "hyde": json.dumps(
        {
            "hypotheticals": [
                {"index": 1, "text": "He compares himself to other people"},
                {"index": 2, "text": "He ate somewhere on his own"},
                {"index": 3, "text": "He reflected on comparison"},
            ]
        }
    ),
    "decision": json.dumps(
        {
            "decisions": [
                {
                    "item_index": 1,
                    "primary": {
                        "action": "BRANCH",
                        "confidence": 0.9,
                        "reason": "nothing like this has been said before",
                    },
                    "runner_up": {"action": "AMBIGUOUS", "confidence": 0.1},
                    "new_node": {
                        "kind": "PATTERN",
                        "name": "comparison_spiral",
                        "statement": "Comparing himself to others is what hurts",
                        "domain": "SELF_CONCEPT",
                    },
                },
                {
                    "item_index": 2,
                    "primary": {
                        "action": "AMBIGUOUS",
                        "confidence": 0.2,
                        "reason": "an ordinary meal",
                    },
                },
                {
                    "item_index": 3,
                    "primary": {
                        "action": "AMBIGUOUS",
                        "confidence": 0.2,
                        "reason": "the session itself",
                    },
                },
            ],
            "people": [
                {"name": "Alex", "relationship": "COLLEAGUE", "sentiment": "MIXED"}
            ],
        }
    ),
}


@pytest.fixture
def full_run_providers():
    """
    Build a pair of stand-in models that can answer every stage of a run.

    Both models share one script, so a test never has to know which of the
    two a given step happens to use. Overrides are merged on top, which is
    how a test makes one step fail or answer differently.
    """
    from lumen.providers.fake import FakeLLMProvider

    def _build(overrides: dict[str, str] | None = None):
        replies = {**FULL_RUN_SCRIPT, **(overrides or {})}
        script = {
            FULL_RUN_PROMPT_KEYS[step]: reply for step, reply in replies.items()
        }
        return (
            FakeLLMProvider(dict(script), role=ModelRole.LIGHTWEIGHT, model="fake-light"),
            FakeLLMProvider(dict(script), role=ModelRole.THINKING, model="fake-thinker"),
        )

    return _build


@pytest.fixture
def decayed_session(ops_store, make_event):
    """
    A conversation sitting in the operational store, ready to be processed.

    A run creates a job against a real buffer row, so the buffer has to
    exist before the pipeline is pointed at it.
    """

    def _build(
        text: str = EPISODE_TEXT,
        *,
        session_label: str = "A",
        source_modality: SourceModality = SourceModality.TEXT_ENTRY,
    ):
        buffer = ops_store.buffers.find_or_create(
            user_id="local", event_date=TODAY, session_label=session_label
        )
        ops_store.buffers.append_message(
            buffer.session_id,
            BufferMessageRecord(
                message_id="msg_0",
                session_id=buffer.session_id,
                seq=0,
                role="USER",
                content=text,
                timestamp=datetime(2026, 6, 11, 21, 0, tzinfo=UTC),
                event_date=TODAY,
            ),
        )
        return make_event(
            [("USER", text)],
            session_id=buffer.session_id,
            source_modality=source_modality,
        )

    return _build


@pytest.fixture
def make_preprocessing(make_episode):
    """
    Build the result of cleaning one session, for tests downstream of it.

    Only the parts the orchestrator reads are worth setting here: the
    episodes it loops over, who the pronouns meant, and which languages the
    writing was in.
    """

    def _build(
        *,
        episodes: list | None = None,
        decision: QualityGateDecision = QualityGateDecision.REFLECTION,
        languages: list[str] | None = None,
        session_id: str = "sess_test_001",
    ):
        from lumen.schemas.pipeline import CoreferenceMap, PreprocessingResult, ResolvedEntity

        return PreprocessingResult(
            session_id=session_id,
            episodes=episodes if episodes is not None else [make_episode()],
            coreference_map=CoreferenceMap(
                entry_id=session_id,
                resolved_entities=[
                    ResolvedEntity(
                        span="he",
                        resolved_to="Alex",
                        confidence=0.9,
                        resolution_basis="named earlier in the entry",
                    )
                ],
            ),
            quality_gate_decision=decision,
            processing_time_ms=5,
            detected_languages=languages if languages is not None else ["en"],
        )

    return _build


@pytest.fixture
def reconciliation_outcome(sample_pattern, sample_decision_audit):
    """
    One episode's worth of decisions, already made.

    Hand-built rather than produced by running the deciding stage, because
    the tests that use it are about what happens to a set of decisions
    afterwards, not about how those decisions were reached. Holds one of
    each thing a decision can produce: a new record, the link to it, a small
    update to something that already existed, the note of the decision, and
    one item nobody could settle.
    """
    from lumen.schemas.edges import LogicalEdgeType, ReconciliationEdge
    from lumen.schemas.enums import BookkeepingOperation, HitlEntryType
    from lumen.schemas.pipeline import (
        GraphWritePlan,
        HitlEscalation,
        PlannedBookkeeping,
        PlannedEdge,
        PlannedNode,
        ReconciliationOutcome,
        ReconciliationResult,
    )

    moment = datetime(2026, 6, 11, 20, 0, tzinfo=UTC)
    new_pattern = sample_pattern.model_copy(update={"node_id": "pat_new_one"})

    return ReconciliationOutcome(
        episode_id="ep_new",
        results=[
            ReconciliationResult(
                source_node_id="obs_new_1",
                action=ReconciliationAction.BRANCH,
                target_node_id=None,
                confidence=0.9,
                decision_model="fake-light",
                escalated_to_hitl=False,
                audit_node_id=sample_decision_audit.node_id,
            )
        ],
        audit_nodes=[sample_decision_audit],
        write_plan=GraphWritePlan(
            nodes=[
                PlannedNode(node_type="PatternNode", node=new_pattern),
                PlannedNode(node_type="DecisionAuditNode", node=sample_decision_audit),
            ],
            edges=[
                PlannedEdge(
                    logical_type=LogicalEdgeType.BRANCHES_TO,
                    table="branches_to_obs_pat",
                    from_node_id="obs_new_1",
                    to_node_id="pat_new_one",
                    edge=ReconciliationEdge(
                        source_node_id="obs_new_1",
                        target_node_id="pat_new_one",
                        valid_from=moment,
                        decision_id=sample_decision_audit.node_id,
                        confidence=0.9,
                    ),
                )
            ],
            bookkeeping=[
                PlannedBookkeeping(
                    operation=BookkeepingOperation.RECORD_REINFORCEMENT,
                    node_id="pat_existing",
                    at=moment,
                )
            ],
            existing_node_ids=frozenset({"obs_new_1", "pat_existing"}),
        ),
        escalations=[
            HitlEscalation(
                audit_node_id=sample_decision_audit.node_id,
                source_node_id="obs_new_1",
                episode_id="ep_new",
                entry_type=HitlEntryType.AMBIGUOUS_TIE,
                signal_strength=SignalStrength.HIGH,
                summary="BRANCH against no existing record held back: TIE",
            )
        ],
        decision_model="fake-light",
    )


# ---------------------------------------------------------------------------
# Web API fixtures
#
# The application is built by a function, so a test points the whole thing at
# temporary databases by replacing the two dependencies rather than by
# reaching into it. That is also how the real deployment is wired, so what
# these tests exercise is the shipped path.
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client(graph_store, ops_store):
    """A client for the web API, wired to the test databases."""
    from fastapi.testclient import TestClient

    from lumen.api.deps import get_graph, get_ops, get_reporter
    from lumen.api.main import create_app
    from lumen.config import AppConfig
    from lumen.pipeline.macroextraction.service import MacroextractionService

    app = create_app()
    app.dependency_overrides[get_graph] = lambda: graph_store
    app.dependency_overrides[get_ops] = lambda: ops_store

    # The report builder is wired to the same test databases. It is given no
    # model, which is a supported way to run — a report carries its counts
    # without one — so the route can be exercised without a stand-in model
    # having to answer for it.
    reporter = MacroextractionService(
        config=AppConfig(), graph=graph_store, ops=ops_store
    )
    reporter._models = {ModelRole.THINKING: None, ModelRole.LIGHTWEIGHT: None}
    app.dependency_overrides[get_reporter] = lambda: reporter
    app.state.reporter = reporter

    # The stores are supplied directly, so the application's own startup —
    # which would open a second pair against the configured paths — is
    # skipped rather than run and thrown away.
    app.state.graph = graph_store
    app.state.ops = ops_store

    # Server-side failures are answered rather than re-raised, because
    # what the caller receives when something breaks is the thing being
    # tested — and a leaked stack trace is the failure being guarded against.
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Live conversation — reading a turn
# ---------------------------------------------------------------------------

TURN_AT = datetime(2026, 8, 16, 10, 0, tzinfo=UTC)


@pytest.fixture
def make_turn():
    """Build one message of a conversation."""
    from lumen.schemas.query import ChatTurn

    def _make(
        content: str = "I keep putting off the thing that matters most.",
        *,
        turn_index: int = 0,
        role: str = "user",
        at: datetime | None = None,
    ) -> ChatTurn:
        return ChatTurn(
            turn_index=turn_index,
            role=role,
            content=content,
            timestamp=at or TURN_AT,
        )

    return _make


@pytest.fixture
def chat_session():
    """One day's conversation, empty and in memory."""
    from lumen.query.session import ChatSession, make_session_id

    day = TURN_AT.date()
    return ChatSession(
        session_id=make_session_id("tester", day),
        user_id="tester",
        event_date=day,
        created_at=TURN_AT,
        last_activity_at=TURN_AT,
    )


@pytest.fixture
def make_reply():
    """
    Build the JSON a classifier would return.

    Written as text rather than as an object because that is what a provider
    actually hands back, and a test that skipped the parsing step would not
    notice a reply shape the code cannot read.
    """

    def _make(
        *,
        triggers: list[dict] | None = None,
        named_entities: list[str] | None = None,
        register: str = "STABLE",
        confidence: float = 0.8,
        critical_domain_opened: str | None = None,
    ) -> str:
        return json.dumps(
            {
                "triggers": triggers or [],
                "named_entities": named_entities or [],
                "emotional_register": register,
                "confidence": confidence,
                "critical_domain_opened": critical_domain_opened,
            }
        )

    return _make


@pytest.fixture
def make_formulator():
    """
    Build a turn reader over a scripted model and a given graph.

    The deadline runner is shared rather than made per reader, so a test
    that builds several does not leave a thread pool behind for each.
    """
    from lumen.providers.fake import FakeLLMProvider
    from lumen.query.formulation import QueryFormulator
    from lumen.query.deadline import DeadlineRunner

    built = []
    runner = DeadlineRunner(max_workers=2, name="test-formulate")

    def _make(graph, *, script=None, config=None, llm=None):
        formulator = QueryFormulator(
            llm=llm or FakeLLMProvider(script if script is not None else []),
            graph=graph,
            config=config,
            runner=runner,
        )
        built.append(formulator)
        return formulator

    yield _make

    for formulator in built:
        formulator.close()
    runner.close()


# ---------------------------------------------------------------------------
# Live conversation — fetching a turn's history
#
# Tested against the same real Kuzu and real Qdrant the extraction-side
# retrieval uses, for the same reason: every question these passes ask is a
# query, and a stand-in answering from a dictionary would agree with whatever
# it was told.
# ---------------------------------------------------------------------------


@pytest.fixture
def make_trigger():
    """Build one checked reason to search."""
    from lumen.schemas.query import RetrievalTrigger

    def _make(
        trigger_type,
        *,
        domain=None,
        era=None,
        person_node_ids=(),
        keywords=("resistance",),
    ):
        return RetrievalTrigger(
            trigger_type=trigger_type,
            domain=domain,
            era=era,
            person_node_ids=tuple(person_node_ids),
            keywords=tuple(keywords),
        )

    return _make


@pytest.fixture
def make_signal():
    """Build the reading of a turn, as the searches receive it."""
    from lumen.schemas.query import RetrievalSignal

    def _make(
        *triggers,
        session_id: str = "tester_2026_08_16",
        turn_index: int = 0,
        unlocked=(),
        suppressed: bool = False,
    ) -> RetrievalSignal:
        return RetrievalSignal(
            session_id=session_id,
            turn_index=turn_index,
            retrieval_triggers=tuple(triggers),
            unlocked_domains=tuple(unlocked),
            suppressed_by_crisis=suppressed,
        )

    return _make


@pytest.fixture
def index_node(vector_store, embedder):
    """
    Put a record into the search index under a chosen text or vector.

    Separate from the fixtures that write to the graph, because a record
    that exists but was never indexed is a real state worth testing — it is
    what an old graph looks like, and it is the case the continuity check
    falls back to words for.
    """

    def _index(node_id: str, text: str = "", *, vector=None, node_type="ObservationNode"):
        vector_store.upsert(
            node_id,
            vector if vector is not None else embedder.embed_text(text),
            {"node_type": node_type, "status": "ACTIVE"},
        )
        return node_id

    return _index


@pytest.fixture
def seed_person(graph_store):
    """
    Put a person into the graph under the identifier their name produces.

    Derived rather than chosen, because that derivation is how reading a
    turn recognises a name and how this half finds the record again. A test
    that picked its own identifier would pass while the two halves disagreed.
    """
    from lumen.schemas.ids import person_node_id

    def _seed(name: str = "Alex") -> str:
        node_id = person_node_id(name)
        graph_store.write_node(
            "PersonEntityNode",
            {
                "node_id": node_id,
                "canonical_name": name,
                "first_mentioned_at": "2026-01-01T00:00:00Z",
                "last_mentioned_at": "2026-06-11T00:00:00Z",
                "relationship_to_user": "MENTOR",
                "relationship_sentiment_trend": "STABLE",
                "status": "ACTIVE",
            },
        )
        return node_id

    return _seed


@pytest.fixture
def seed_open_loop(graph_store):
    """Put one unfinished question into the graph."""

    def _seed(
        node_id: str = "loop_old",
        *,
        description: str = "Is the resistance about leaving, or about being alone?",
        status: str = "OPEN",
    ) -> str:
        graph_store.write_node(
            "OpenLoopNode",
            {
                "node_id": node_id,
                "created_at": "2026-06-01T00:00:00+00:00",
                "valid_from": "2026-06-01T00:00:00+00:00",
                "loop_description": description,
                "loop_category": "UNRESOLVED_QUESTION",
                "provenance": "USER_GENERATED",
                "source_episode_id": "ep_old",
                "resolution_status": status,
                "last_referenced_at": "2026-06-01T00:00:00+00:00",
            },
        )
        return node_id

    return _seed


@pytest.fixture
def hyde_replies():
    """
    A scripted model that answers the invented-record request.

    Keyed on the phrase unique to that instruction, so one script can serve
    a test that also reads turns without either answer reaching the wrong
    caller.
    """

    def _build(texts: list[str] | None = None, *, reply: str | None = None):
        from lumen.providers.fake import FakeLLMProvider

        if reply is None:
            written = texts or ["an earlier entry about the same thing"]
            reply = json.dumps(
                {
                    "hypotheticals": [
                        {"index": position, "text": text}
                        for position, text in enumerate(written, start=1)
                    ]
                }
            )
        return FakeLLMProvider({"ITEMS:": reply})

    return _build


@pytest.fixture
def make_retriever(graph_store, vector_store, embedder):
    """
    Build a retriever over the real stores and a scripted model.

    The thread pool is shared between everything one test builds, so a test
    that makes several does not leave one behind for each.
    """
    from lumen.providers.fake import FakeLLMProvider
    from lumen.query.deadline import DeadlineRunner
    from lumen.query.retrieval import ConversationalRetriever

    built = []
    runner = DeadlineRunner(max_workers=3, name="test-retrieve")

    def _make(*, llm=None, config=None, graph=None, vectors=None, embed=None):
        retriever = ConversationalRetriever(
            graph=graph or graph_store,
            vectors=vectors or vector_store,
            embedder=embed or embedder,
            llm=llm or FakeLLMProvider([]),
            config=config,
            runner=runner,
        )
        built.append(retriever)
        return retriever

    yield _make

    for retriever in built:
        retriever.close()
    runner.close()


# ---------------------------------------------------------------------------
# Periodic reports
#
# Two kinds of fixture, because the package has two kinds of code. The
# arithmetic works on a corpus object and is tested with hand-built ones, so
# every count in a test can be checked by eye. The reading and the writing
# work on a real embedded graph, because what they do *is* the queries.
# ---------------------------------------------------------------------------

MACRO_MAY = datetime(2026, 5, 1, tzinfo=UTC)
MACRO_JUNE = datetime(2026, 6, 1, tzinfo=UTC)


@pytest.fixture
def make_window():
    """Build one period for a report to cover."""
    from lumen.pipeline.macroextraction.contracts import MacroWindow

    def _make(
        report_type: ReportType = ReportType.MONTHLY,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> MacroWindow:
        return MacroWindow(
            report_type=report_type,
            period_start=start or MACRO_MAY,
            period_end=end or MACRO_JUNE,
        )

    return _make


@pytest.fixture
def make_observation_facts():
    """Build one noticing as a report sees it."""
    from lumen.pipeline.macroextraction.contracts import ObservationFacts

    def _make(
        node_id: str,
        *,
        observation_type: ObservationType | str = ObservationType.EMOTION,
        content: str = "felt behind everyone again",
        signal: SignalStrength | str = SignalStrength.STANDARD,
        people: tuple[str, ...] = (),
        episode_id: str = "ep_1",
    ) -> ObservationFacts:
        return ObservationFacts(
            node_id=node_id,
            type=str(getattr(observation_type, "value", observation_type)),
            content=content,
            signal_strength=str(getattr(signal, "value", signal)),
            person_refs=people,
            episode_id=episode_id,
        )

    return _make


@pytest.fixture
def make_episode_facts():
    """Build one piece of writing as a report sees it, with what it produced."""
    from lumen.pipeline.macroextraction.contracts import EpisodeFacts

    def _make(
        episode_id: str,
        *,
        day: int = 4,
        month: int = 5,
        summary: str = "an ordinary evening",
        observations: tuple = (),
        extra_findings: tuple[str, ...] = (),
        era: str | None = None,
    ) -> EpisodeFacts:
        when = datetime(2026, month, day, 20, 0, tzinfo=UTC)
        return EpisodeFacts(
            episode_id=episode_id,
            event_date=when.date(),
            occurred_at=when,
            episode_summary=summary,
            historical_era=era,
            observations=observations,
            finding_ids=tuple(item.node_id for item in observations) + extra_findings,
        )

    return _make


@pytest.fixture
def make_link():
    """Build one link from something noticed to something the person carries."""
    from lumen.pipeline.macroextraction.contracts import StandingLink

    def _make(
        from_id: str,
        to_id: str,
        *,
        to_type: str = "pattern",
        edge_name: str = "reinforces_obs_pat",
    ) -> StandingLink:
        return StandingLink(
            from_id=from_id, to_id=to_id, to_type=to_type, edge_name=edge_name
        )

    return _make


@pytest.fixture
def make_corpus(make_window):
    """
    Build a whole reading of one period by hand.

    Every field has a sensible empty default, so a test about one section
    names only what that section reads and the rest of the report is
    visibly not involved.
    """
    from lumen.pipeline.macroextraction.contracts import WindowCorpus

    def _make(**overrides) -> WindowCorpus:
        overrides.setdefault("window", make_window())
        return WindowCorpus(**overrides)

    return _make


@pytest.fixture
def pattern_row():
    """A pattern record as the graph hands it back, tidied."""

    def _make(
        node_id: str,
        *,
        name: str = "Comparison with peers",
        version: int = 1,
        valid_from: str = "2026-05-04T20:00:00+00:00",
        last_reinforced: str | None = None,
        status: str = "ACTIVE",
    ) -> dict:
        return {
            "node_id": node_id,
            "pattern_name": name,
            "pattern_description": "measures self against others",
            "domain": "EMOTIONAL",
            "version": version,
            "created_at": valid_from,
            "valid_from": valid_from,
            "last_reinforced_at": last_reinforced or valid_from,
            "status": status,
        }

    return _make


@pytest.fixture
def seed_month(graph_store):
    """
    Write one piece of writing and its findings into a real graph.

    Deliberately writes the links too. What the reading half of a report
    does is follow them, and a fixture that skipped them would let a test
    pass with the query written wrongly.
    """

    def _seed(
        episode_id: str,
        *,
        day: int,
        month: int = 5,
        year: int = 2026,
        written_on: date | None = None,
        observations: tuple[tuple[str, str, str], ...] = (),
        patterns: dict[str, str] | None = None,
        people: dict[str, str] | None = None,
        signal: str = "HIGH",
    ) -> str:
        happened = datetime(year, month, day, 20, 0, tzinfo=UTC)
        recorded = written_on or happened.date()

        graph_store.write_node(
            "EpisodeNode",
            {
                "node_id": episode_id,
                "entry_id": f"entry_{episode_id}",
                "occurred_at": happened.isoformat(),
                "created_at": datetime(
                    recorded.year, recorded.month, recorded.day, 21, 0, tzinfo=UTC
                ).isoformat(),
                "valid_from": happened.isoformat(),
                "event_date": happened.date().isoformat(),
                "session_label": "evening",
                "source_modality": "TEXT_ENTRY",
                "entry_class": "REFLECTION",
                "episode_summary": f"what happened on {happened.date()}",
                "episode_index": 1,
                "total_episodes_in_entry": 1,
                "coreference_map_id": f"cm_{episode_id}",
                "reconciliation_status": "COMPLETE",
                "raw_text_hash": f"hash_{episode_id}",
            },
        )

        for node_id, observation_type, content in observations:
            graph_store.write_node(
                "ObservationNode",
                {
                    "node_id": node_id,
                    "episode_id": episode_id,
                    "occurred_at": happened.isoformat(),
                    "created_at": happened.isoformat(),
                    "valid_from": happened.isoformat(),
                    "type": observation_type,
                    "content": content,
                    "signal_strength": signal,
                    "provenance": "USER_GENERATED",
                    "verification_status": "IMPLICIT",
                    "extraction_confidence": "STANDARD",
                    "status": "ACTIVE",
                    "extraction_model": "fake",
                    "extraction_attempt": 1,
                    **(
                        {"person_refs": json.dumps(list(people[node_id]))}
                        if people and node_id in people
                        else {}
                    ),
                },
            )
            graph_store.write_edge(
                "contains_obs",
                episode_id,
                node_id,
                {"valid_from": happened.isoformat()},
            )
            if patterns and node_id in patterns:
                graph_store.write_edge(
                    "reinforces_obs_pat",
                    node_id,
                    patterns[node_id],
                    {"valid_from": happened.isoformat()},
                )

        return episode_id

    return _seed


@pytest.fixture
def narrative_provider():
    """A stand-in model that answers one report's wording request."""
    from lumen.providers.fake import FakeLLMProvider

    def _build(reply: dict | str) -> FakeLLMProvider:
        return FakeLLMProvider(
            [reply if isinstance(reply, str) else json.dumps(reply)],
            role=ModelRole.THINKING,
            model="fake-thinker",
        )

    return _build


# ---------------------------------------------------------------------------
# The review queue
#
# Two kinds of helper. One builds a saved proposal the way the reconciliation
# stage builds it, so the tests answer the same thing the pipeline would have
# written. The other puts a question into the queue, because most of what is
# worth testing here is about an item that already exists.
# ---------------------------------------------------------------------------


@pytest.fixture
def moment():
    """The one moment the review tests stamp everything with."""
    return MOMENT


@pytest.fixture
def plan_context():
    """The surroundings a decision is translated into writing against."""

    def _build(*, history=None, exists=None, anchor: bool = True):
        from lumen.pipeline.reconciliation.plan import PlanContext

        return PlanContext(
            at=MOMENT,
            event_date=TODAY,
            history=history or {},
            exists=exists or (lambda _node_id: False),
            anchor_node_id="evt_anchor" if anchor else None,
            anchor_node_type="EventNode" if anchor else None,
            episode_index=1,
        )

    return _build


@pytest.fixture
def historical(sample_pattern, sample_belief):
    """
    An existing record read back in full, as reconciliation reads it.

    A next version is built from every field of the record it follows, so a
    preview would not be enough here.
    """

    def _build(node=None, *, node_type: str = "PatternNode"):
        from lumen.pipeline.reconciliation.contracts import HistoricalNode

        node = node or (sample_pattern if node_type == "PatternNode" else sample_belief)
        row = node.to_graph_dict()
        row["_label"] = node_type
        return HistoricalNode(
            node_id=node.node_id,
            node_type=node_type,
            preview=row.get("pattern_name") or row.get("belief_statement") or "",
            valid_from=MOMENT,
            row=row,
        )

    return _build


@pytest.fixture
def make_proposal(make_settled, plan_context, historical, sample_pattern):
    """
    Build a saved proposal the same way the pipeline builds one.

    Deliberately goes through the real freezing rather than assembling a
    proposal by hand: what these tests are checking is that what was saved
    can actually be carried out, and a hand-built one would only prove that
    the test author agreed with themselves.
    """

    def _build(
        action=None,
        *,
        target_node_id: str | None = None,
        runner_up=None,
        runner_up_target: str | None = None,
        entry_type=None,
        audit_id: str = "d_2026_06_11_01_001",
        item=None,
        refusal=None,
        **extra,
    ):
        from lumen.pipeline.reconciliation.contracts import GateRule
        from lumen.pipeline.reconciliation.freeze import freeze
        from lumen.schemas.enums import HitlEntryType, ReconciliationAction

        action = action or ReconciliationAction.REINFORCE
        entry_type = entry_type or (
            HitlEntryType.AMBIGUOUS_TIE if runner_up else HitlEntryType.BELOW_THRESHOLD
        )
        known = historical(sample_pattern)
        history = {known.node_id: known}

        decision = make_settled(
            action,
            item=item,
            target_node_id=(
                known.node_id if target_node_id is None else target_node_id
            ),
            target_type="PatternNode",
            runner_up=runner_up,
            runner_up_confidence=0.5 if runner_up else None,
            runner_up_target_node_id=runner_up_target,
            **extra,
        ).refuse(refusal or (GateRule.TIE if runner_up else GateRule.BELOW_THRESHOLD))

        return freeze(
            decision,
            plan_context(history=history),
            audit_id=audit_id,
            entry_type=entry_type,
        )

    return _build


@pytest.fixture
def queued(ops_store, make_proposal):
    """
    Put one question into the queue, with what it was going to write.

    Returns the queue row, since almost everything worth checking starts from
    an item that is already waiting.
    """

    def _build(
        proposal=None,
        *,
        user_id: str = "tester",
        status=None,
        signal=None,
        snooze_count: int = 0,
        last_snoozed_at=None,
        snoozed_until=None,
        save_proposal: bool = True,
        item_id: str | None = None,
    ):
        from lumen.operational.enums import HitlItemStatus
        from lumen.operational.schemas import HitlQueueItemRecord

        proposal = proposal or make_proposal()
        record = HitlQueueItemRecord(
            id=item_id or f"hitl_{proposal.audit_node_id}",
            user_id=user_id,
            audit_node_id=proposal.audit_node_id,
            entry_type=proposal.entry_type,
            signal_strength=signal or SignalStrength.STANDARD,
            status=status or HitlItemStatus.PENDING_HITL,
            observation_id=proposal.source_node_id,
            episode_id=proposal.episode_id,
            recommended_action=proposal.primary.action,
            candidate_a_node_id=proposal.primary.target_node_id,
            confidence_a=proposal.primary.confidence,
            context_summary=proposal.primary.summary,
            created_at=MOMENT,
            snooze_count=snooze_count,
            last_snoozed_at=last_snoozed_at,
        )
        ops_store.hitl.enqueue(record)
        if save_proposal:
            ops_store.hitl.save_proposal(
                proposal.audit_node_id, proposal.model_dump_json()
            )
        if snoozed_until is not None:
            ops_store.hitl.snooze(
                record.id, until=snoozed_until, at=last_snoozed_at or MOMENT
            )
        return ops_store.hitl.get(record.id)

    return _build


@pytest.fixture
def config_with_cap():
    """Settings with the review queue's ceiling set to a given number."""

    def _build(cap: int):
        from dataclasses import replace

        from lumen.config import AppConfig

        settings = AppConfig()
        return replace(
            settings, operational=replace(settings.operational, hitl_queue_cap=cap)
        )

    return _build


@pytest.fixture
def reviewer(graph_store, ops_store, vector_store, embedder):
    """
    A review service wired to the test databases.

    Given real stores rather than stand-ins, because the thing most worth
    checking is that answering a question actually changes the graph.
    """
    from lumen.config import AppConfig
    from lumen.review.service import ReviewService

    return ReviewService(
        config=AppConfig(),
        graph=graph_store,
        ops=ops_store,
        open_vectors=lambda: vector_store,
        open_embedder=lambda: embedder,
    )
