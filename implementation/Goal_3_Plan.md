# Goal 3 + 3b: Operational DB & Trace Infrastructure

**Branch:** `goal3`
**Status:** ✅ Complete
**Depends on:** Goal 1 (DB Init) ✅, Goal 2 (Pydantic Contracts) ✅
**Blocks:** Goal 10 (E2E harness), Goal 18 (HITL queue), Goal 19 (erasure), Goal 20 (BFF)

---

## Objective

Give Lumen the third of its three data stores — the one that holds everything that
*isn't* knowledge. The graph stores what the user believes; the operational DB stores
what the system is *doing about it*: which sessions are waiting, which pipeline runs
failed, which decisions need a human, and what got erased.

Goal 3b threads a `trace_id` through all of it, so any single journal entry can be
followed from raw message to graph write and back.

---

# SECTION A — LOGIC (please verify)

*Short by design. This is what the goal means; Section B is how it's coded.*

## A1. What Gets Built

| Deliverable | What it is |
|---|---|
| **8 operational tables** | The 5 named in `Master_Plan.md`, with `session_buffer` split in two and `pipeline_jobs` split into three (see A3). |
| **5 repository Protocols + one SQLAlchemy implementation** | Business logic talks to `SessionBufferRepository`, `PipelineJobRepository`, etc. — never to a SQLAlchemy `Session`. Goals 10/18 become testable against fakes with no database at all. |
| **Alembic migrations** | The schema is code-controlled from day one. SQLite → PostgreSQL is one env var (`LUMEN_OPS_DB_URL`). |
| **`trace_id` infrastructure** | A UUID minted at each pipeline entry, carried ambiently so every log line, Pydantic model, and DB row picks it up without being handed it explicitly. |
| **Structured JSON logging** | One `.jsonl` file, one line per event, `trace_id` on every line — including the logs Goal 1's `kuzu_impl` and `qdrant_impl` already emit. |

## A2. The Decisions You Made

1. **Sync SQLAlchemy 2.0.** Same code path in RQ workers, FastAPI (via threadpool), and pytest. SQLite serializes writes regardless, so async buys nothing at personal scale.
2. **Repositories behind Protocols**, matching `graph/` and `vector/`. Callers never see an ORM object.
3. **`trace_id` does not go into the graph.** `Technical_HLD.md` §10 says it's attached to every node and edge written — but no node or edge table has such a column, and adding one means altering 59 Kuzu tables and every Goal 2 model. Instead the ops DB records *which* node and edge IDs each pipeline run wrote. Same reconstruction power, one join, zero graph churn.
4. **stdlib `logging` + a custom JSON formatter.** No new dependency, and Goal 1's existing `getLogger(__name__)` calls start emitting traced JSON with no code change.
5. **Master_Plan's 5 tables; `api_keys` deferred to Goal 4**, where encrypted credentials first have a reader. Building a secrets table now would mean designing an encryption scheme with no consumer.
6. **`user_settings` is generic key/value.** Precedence: **DB override > env var > code default**. This is what lets the Settings UI change a `ModelRole`'s provider at runtime without a migration.
7. **HITL: table + queries only.** The queue cap, snooze flow, and 7-day auto-resolve are Goal 18's — they can't do anything real until there's a graph write-back to execute.
8. **`session_label` gets added to `SessionDecayEvent`.** The buffer is keyed by `(event_date, session_label)` per `Interface_Architecture.md`, and `SessionNode` carries the label — but Goal 2's decay DTO doesn't, so Stage 0 would have nothing to stamp onto the node.

## A3. Why `pipeline_jobs` Becomes Three Tables

`Master_Plan.md` names one table. Splitting it is the only structural liberty this plan
takes, and it exists because three separate HLD requirements need three different shapes:

| Table | Answers the question | Required by |
|---|---|---|
| `pipeline_jobs` | "Is this session's run done, and did it fail?" | Master_Plan Goal 3 |
| `pipeline_stage_runs` | "How long did Stage 1 take, which model ran it, did validation pass, what exactly went in and came out?" | HLD §10 Stage-Level Health Metrics; §7.2 Pipeline Debug View; §10 Re-run Policy (`rerun_from_stage` needs the stored input to replay) |
| `pipeline_write_log` | "Which run created node `pat_decision_saturation`?" | Decision A2-3 — this *is* the trace→graph link |

Folding the last two into JSON columns on `pipeline_jobs` would work, but the reverse
lookup (node → which run wrote it) is needed by both Goal 11's debug APIs and Goal 19's
erasure pass, and a JSON scan is the wrong shape for it.

## A4. What the Tables Mean

- **`session_buffers` + `buffer_messages`** — the pre-pipeline waiting room. A buffer is unique on `(user_id, event_date, session_label)`; multiple same-day sessions stay separate, exactly as `Interface_Architecture.md` requires. This goal ships the *query* for finding decayed sessions (2hr inactivity by default, configurable); the background watcher that calls it is Goal 10's.
- **`hitl_queue`** — **workflow state only**. The decision itself is a `DecisionAuditNode` in the graph; this table holds the queue mechanics around it (status, priority, snooze counters), joined by `audit_node_id`. Two stores, one owner each, no duplicated truth.
- **`data_erasure_audit`** — table and repository only; the anonymization pass is Goal 19. Per `Schema.md`, this record **contains no user content**: the repository hashes `user_id` on the way in and plaintext is never stored. A test enforces that.

## A5. How `trace_id` Actually Propagates

A context variable, set once at the pipeline entry point. Everything downstream reads it
ambiently:

- **Log lines** — a filter on the handler injects it, so `kuzu_impl`, `qdrant_impl`, and SQLAlchemy's own loggers are covered without touching them.
- **Pydantic models** — `PipelineDTO.trace_id` (which Goal 2 already defined and left optional) gets a default that reads the context. Stages stop having to pass it.
- **DB rows** — repositories stamp it on job, stage-run, write-log, and HITL rows.

Outside a bound trace it stays `None`, so Goal 2's existing tests are unaffected.
The test that matters: two concurrent traces on separate threads must not leak into
each other.

## A6. Doc Discrepancies This Goal Surfaces

Three, all in `Technical_HLD.md`. I'll fix the wording as part of this goal — **flagging
rather than silently picking**, per CLAUDE.md:

1. **§10** says `trace_id` is attached to every graph node and edge. No such column exists anywhere in `Schema.md` or the Kuzu DDL. → Reword to describe the ops-DB write log (decision A2-3).
2. **§4.1** lists `api_keys` as an operational table; `Master_Plan.md` Goal 3 lists `data_erasure_audit` instead. Neither mentions the other. → Document both; `api_keys` marked as Goal 4.
3. **§4.1** describes `user_settings` as holding "provider config, sensitivity prefs". Goal 2 **deleted** the sensitivity-tier concept entirely. → Reword to key/value config overrides.

## A7. What This Goal Deliberately Leaves Undone

| Deferred | To | Why |
|---|---|---|
| The session-decay watcher loop | Goal 10 | Goal 3 ships the query; the orchestrator owns the schedule. |
| HITL cap / snooze / auto-resolve logic | Goal 18 | Needs a graph write-back to be meaningful. |
| The erasure anonymization pass | Goal 19 | Goal 3 ships the audit record it writes. |
| `api_keys` + encryption | Goal 4 | No consumer until providers exist. |
| Any FastAPI route | Goals 11+ | This goal has no HTTP surface. |
| OpenTelemetry export | Goal 20 | HLD §10 scopes OTel to production. |

## A8. Definition of Done

- 8 tables created via Alembic; `alembic upgrade head` and `downgrade base` both clean.
- A drift test proves `models.py` and the migration cannot silently diverge.
- Round-trip: write a buffer + messages → read back a valid `SessionDecayEvent`.
- Illegal pipeline job state transitions raise, rather than corrupting state.
- HITL priority ordering matches `Reconciliation.md`'s three-level rule exactly.
- A mock 3-stage run proves one `trace_id` reaches logs, DTOs, and DB rows — and that concurrent traces stay isolated.
- ≥90% coverage on `lumen/operational/` and `lumen/observability/`.

---

# SECTION B — LOW-LEVEL DESIGN

## B1. Files

```text
lumen/
├── config.py                          # +OperationalConfig, +ObservabilityConfig, +user_id
├── operational/
│   ├── __init__.py                    # re-exports Protocols + SQLAlchemyOperationalStore
│   ├── enums.py                       # ops-only enums (job/stage/HITL/erasure states)
│   ├── models.py                      # SQLAlchemy DeclarativeBase — 7 tables
│   ├── schemas.py                     # Pydantic DTOs at the repository boundary
│   ├── engine.py                      # engine factory, SQLite pragmas, session scope
│   ├── repositories.py                # 5 repo Protocols + OperationalStore Protocol
│   ├── sqlalchemy_impl.py             # SQLAlchemyOperationalStore
│   └── migrations/
│       ├── env.py
│       ├── script.py.mako
│       └── versions/0001_initial_ops_schema.py
├── observability/
│   ├── __init__.py
│   ├── trace.py                       # ContextVar, new_trace_id, bind_trace, span
│   └── logging.py                     # JsonFormatter, TraceIdFilter, configure_logging
└── tests/
    ├── conftest.py                    # +ops_store, +bound_trace, +captured_logs
    ├── test_operational_models.py
    ├── test_operational_migrations.py
    ├── test_operational_session_buffer.py
    ├── test_operational_pipeline_jobs.py
    ├── test_operational_hitl.py
    ├── test_operational_settings.py
    ├── test_operational_erasure.py
    ├── test_observability_trace.py
    ├── test_observability_logging.py
    └── test_trace_propagation.py
alembic.ini                            # repo root
```

New dependencies (`uv add`): `sqlalchemy>=2.0`, `alembic>=1.13`. No logging dependency.

## B2. `config.py` Additions

```python
@dataclass(frozen=True)
class OperationalConfig:
    db_url: str = os.environ.get("LUMEN_OPS_DB_URL", "sqlite:///./lumen_ops.db")
    echo_sql: bool = os.environ.get("LUMEN_OPS_DB_ECHO", "").lower() == "true"
    session_decay_minutes: int = int(os.environ.get("LUMEN_SESSION_DECAY_MINUTES", "60"))
    hitl_queue_cap: int = int(os.environ.get("LUMEN_HITL_QUEUE_CAP", "20"))

@dataclass(frozen=True)
class ObservabilityConfig:
    log_level: str = os.environ.get("LUMEN_LOG_LEVEL", "INFO")
    log_file: str = os.environ.get("LUMEN_LOG_FILE", "./logs/lumen.jsonl")
    log_to_console: bool = os.environ.get("LUMEN_LOG_CONSOLE", "true").lower() == "true"
    console_json: bool = False          # human-readable console in dev
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5
```

`AppConfig` gains `operational`, `observability`, and `user_id: str = os.environ.get("LUMEN_USER_ID", "local")`.
`session_decay_minutes` = 60 and `hitl_queue_cap` = 20 come straight from
`Interface_Architecture.md` and `Reconciliation.md` — configurable, but defaulted to spec.

## B3. `operational/enums.py`

New `StrEnum`s (ops-only; anything already in `lumen/schemas/enums.py` is imported, not
redefined — `SignalStrength`, `ReconciliationAction`, `HitlResolutionChoice`, `DialogueAct`):

| Enum | Values | Source |
|---|---|---|
| `BufferStatus` | `OPEN`, `DECAYED`, `DISPATCHED`, `PROCESSED`, `DISCARDED` | Interface_Architecture.md §Session Decay Trigger |
| `BufferSource` | `NATIVE_CHAT`, `IMPORT_MARKDOWN`, `IMPORT_JSON`, `VOICE_NOTE` | Interface_Architecture.md §Ingestion |
| `JobStatus` | `PENDING`, `RUNNING`, `COMPLETE`, `FAILED`, `CANCELLED` | HLD §10 Re-run Policy |
| `PipelineStage` | `STAGE_0_PREPROCESSING` … `STAGE_4_GRAPH_WRITE` | HLDv2 7-step journey |
| `StageStatus` | `PENDING`, `RUNNING`, `COMPLETE`, `FAILED`, `SKIPPED` | — |
| `WriteTarget` | `GRAPH_NODE`, `GRAPH_EDGE`, `VECTOR` | Decision A2-3 |
| `HitlEntryType` | `AMBIGUOUS_TIE`, `BELOW_THRESHOLD`, `EXTRACTION_FAILED` | Reconciliation.md §Entry Conditions |
| `HitlItemStatus` | `PENDING_HITL`, `SUSPENDED_QUEUE_FULL`, `RESOLVED`, `AUTO_RESOLVED` | Reconciliation.md §HITL Review Queue |
| `ErasureInitiator` | `USER_REQUEST`, `ADMIN_REQUEST`, `AUTOMATED_RETENTION_POLICY` | Schema.md §DataErasureAuditRecord |
| `ErasureStatus` | `IN_PROGRESS`, `COMPLETE`, `FAILED` | Schema.md §DataErasureAuditRecord |

## B4. `operational/models.py` — Table Definitions

`class Base(DeclarativeBase)`. All timestamps stored **UTC-aware**, normalized by a
`utcnow()` helper at the boundary (SQLite ignores `timezone=True`, so Python owns this).
Lists/dicts go into `JSON` columns — SQLAlchemy's `JSON` type maps to SQLite `TEXT` and
PostgreSQL `JSONB` with no code change.

**`session_buffers`**
`session_id` PK (str) · `user_id` · `event_date` (Date) · `session_label` · `status`
(`BufferStatus`) · `source` (`BufferSource`) · `message_count` · `created_at` ·
`last_activity_at` (indexed) · `decayed_at` · `ingested_at`
→ `UniqueConstraint(user_id, event_date, session_label)`; `Index(status, last_activity_at)` for the decay scan.

**`buffer_messages`**
`message_id` PK · `session_id` FK→`session_buffers` (CASCADE) · `seq` (int, ordering) ·
`role` (`USER|AI`) · `content` (Text) · `timestamp` · `event_date` · `dialogue_act`
(nullable) · `co_created_marker` (bool)
→ mirrors `BufferMessage` field-for-field; `UniqueConstraint(session_id, seq)`.

**`pipeline_jobs`**
`job_id` PK · `trace_id` (indexed) · `session_id` FK · `user_id` · `status` (`JobStatus`) ·
`current_stage` · `created_at` · `started_at` · `finished_at` · `retry_count` ·
`error_type` · `error_message` (Text) · `config_snapshot` (JSON — the `ProviderConfig` in
force, so a re-run can reproduce or deliberately differ)

**`pipeline_stage_runs`**
`id` PK · `job_id` FK (CASCADE) · `trace_id` · `stage` · `attempt` (int) · `status` ·
`started_at` · `finished_at` · `duration_ms` · `model_used` · `validation_passed` ·
`retry_count` · `input_payload` (JSON) · `output_payload` (JSON) · `error_message`
→ the four metrics named in HLD §10 are first-class columns, not JSON.
→ `UniqueConstraint(job_id, stage, attempt)`.

**`pipeline_write_log`**
`id` PK · `job_id` FK (CASCADE) · `trace_id` (indexed) · `stage` · `target`
(`WriteTarget`) · `node_id` (indexed, nullable) · `edge_type` · `from_id` · `to_id` ·
`written_at`

**`hitl_queue`**
`id` PK · `user_id` · `trace_id` · `job_id` FK (nullable) · `audit_node_id` (unique —
the graph join key) · `observation_id` · `episode_id` · `entry_type` (`HitlEntryType`) ·
`status` (`HitlItemStatus`) · `priority_rank` (int 1–3) · `signal_rank` (int 1–3) ·
`recommended_action` (`ReconciliationAction`) · `candidate_a_node_id` ·
`candidate_b_node_id` · `confidence_a` · `confidence_b` · `context_summary` (Text) ·
`created_at` · `snooze_count` · `last_snoozed_at` · `resolved_at` · `resolution_choice`
(`HitlResolutionChoice`, nullable)

> `priority_rank` and `signal_rank` are **derived integer columns** written at insert
> (`AMBIGUOUS_TIE`→1, `BELOW_THRESHOLD`→2, `EXTRACTION_FAILED`→3;
> `CRITICAL`→3, `HIGH`→2, `STANDARD`→1). SQLite can't sort an enum by semantic rank,
> and a `CASE` expression in the query is unindexable. The repository computes them —
> callers pass the enum.

**`user_settings`**
`user_id` + `key` composite PK · `value_json` (JSON) · `updated_at`

**`data_erasure_audit`**
`id` PK (`era_2026_07_01_001` format) · `user_id_hash` (sha256 hex) · `erased_at` ·
`nodes_anonymized` · `embeddings_deleted` · `entry_ids_affected` (JSON list) ·
`initiated_by` (`ErasureInitiator`) · `status` (`ErasureStatus`)
→ **no plaintext user identifier, no content field** (Schema.md).

## B5. `operational/engine.py`

```python
def create_ops_engine(config: OperationalConfig) -> Engine
def session_scope(engine) -> contextmanager[Session]   # commit / rollback / close
```

SQLite needs explicit setup that SQLAlchemy does **not** apply by default:

```python
@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _):
    if not _is_sqlite(dbapi_conn):
        return
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")     # OFF by default — FKs are silently ignored
    cur.execute("PRAGMA journal_mode=WAL")    # reader (API) + writer (worker) concurrently
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()
```

The dialect guard keeps this a no-op on PostgreSQL.

## B6. `operational/schemas.py` — Boundary DTOs

Pydantic models (`extra="forbid"`) that repositories accept and return, so no ORM
instance escapes the module (Rule 4). One `*Record` per table, plus:

- `SessionBufferRecord`, `BufferMessageRecord`
- `PipelineJobRecord`, `StageRunRecord`, `WriteLogEntry`
- `HitlQueueItem`, `UserSetting`, `ErasureAuditRecord`
- `StageMetrics` — the HLD §10 quartet (`duration_ms`, `model_used`, `validation_passed`, `retry_count`), used to close out a stage run in one call.

The bridge to the pipeline: `SessionBufferRepository.build_decay_event(session_id)`
returns Goal 2's `SessionDecayEvent`, assembling `BufferMessage` objects in `seq` order.
This is the single point where the ops DB hands off to the pipeline.

## B7. `operational/repositories.py` — Protocols

```python
class SessionBufferRepository(Protocol):
    def create_buffer(self, rec: SessionBufferRecord) -> str: ...
    def append_message(self, session_id: str, msg: BufferMessageRecord) -> None: ...
    def get_buffer(self, session_id: str) -> SessionBufferRecord | None: ...
    def find_or_create(self, user_id, event_date, session_label, source) -> SessionBufferRecord: ...
    def find_decayed(self, cutoff: datetime, limit: int = 50) -> list[SessionBufferRecord]: ...
    def build_decay_event(self, session_id: str) -> SessionDecayEvent: ...
    def mark_status(self, session_id: str, status: BufferStatus) -> None: ...

class PipelineJobRepository(Protocol):
    def create_job(self, session_id, user_id, config_snapshot) -> PipelineJobRecord: ...
    def transition(self, job_id: str, to: JobStatus) -> PipelineJobRecord: ...   # validates
    def start_stage(self, job_id, stage, attempt, input_payload) -> StageRunRecord: ...
    def finish_stage(self, run_id, status, metrics: StageMetrics, output_payload) -> None: ...
    def record_write(self, job_id, stage, target, **ids) -> None: ...
    def get_trace(self, trace_id: str) -> PipelineTrace: ...   # job + stages + writes
    def find_job_for_node(self, node_id: str) -> PipelineJobRecord | None: ...

class HitlQueueRepository(Protocol):
    def enqueue(self, item: HitlQueueItem) -> str: ...        # computes ranks
    def list_pending(self, user_id: str, limit: int = 20) -> list[HitlQueueItem]: ...
    def count_pending(self, user_id: str) -> int: ...         # Goal 18's cap check
    def get_by_audit_node(self, audit_node_id: str) -> HitlQueueItem | None: ...
    def update_status(self, item_id, status, resolution_choice=None) -> None: ...

class UserSettingsRepository(Protocol):
    def get(self, user_id: str, key: str) -> Any | None: ...
    def get_all(self, user_id: str) -> dict[str, Any]: ...
    def set(self, user_id: str, key: str, value: Any) -> None: ...   # validates key
    def delete(self, user_id: str, key: str) -> None: ...

class DataErasureAuditRepository(Protocol):
    def record(self, rec: ErasureAuditRecord) -> str: ...      # hashes user_id internally
    def get(self, record_id: str) -> ErasureAuditRecord | None: ...
    def list_for_user(self, user_id: str) -> list[ErasureAuditRecord]: ...

class OperationalStore(Protocol):
    buffers: SessionBufferRepository
    jobs: PipelineJobRepository
    hitl: HitlQueueRepository
    settings: UserSettingsRepository
    erasure: DataErasureAuditRepository
    def init_schema(self) -> None: ...
    def transaction(self) -> ContextManager[None]: ...
    def close(self) -> None: ...
```

Two behaviors worth naming:

**Job state machine** (`transition` raises `IllegalStateTransition` otherwise):
```
PENDING → RUNNING → COMPLETE
                  → FAILED → RUNNING     (re-run, retry_count += 1)
PENDING/RUNNING   → CANCELLED
```
Terminal `COMPLETE` and `CANCELLED` accept no outgoing transition.

**HITL priority query** (`Reconciliation.md` §Queue Priority Order):
```sql
ORDER BY priority_rank ASC, signal_rank DESC, created_at ASC
```

**Settings key validation** — `set()` checks against a `KNOWN_SETTING_KEYS` whitelist
(`providers.lightweight.provider`, `providers.thinking.model`, `pipeline.session_decay_minutes`,
`hitl.queue_cap`, …) and raises on an unknown key. Silently accepting a typo would be a
setting that never applies (CLAUDE.md: *log, don't silently degrade*).

**Config resolution helper** (`sqlalchemy_impl.py`, consumed by Goal 4):
```python
def resolve_provider_config(base: ProviderConfig, overrides: dict[str, Any]) -> ProviderConfig
```
Returns a new frozen `ProviderConfig` with DB overrides applied over the env-var/default
baseline. Precedence: **DB > env > code default**.

## B8. `observability/trace.py`

```python
_trace_id: ContextVar[str | None] = ContextVar("lumen_trace_id", default=None)

def new_trace_id() -> str                    # str(uuid.uuid4())
def get_trace_id() -> str | None
def set_trace_id(trace_id: str) -> Token
@contextmanager
def bind_trace(trace_id: str | None = None) -> Iterator[str]   # mints if None, resets on exit
@contextmanager
def span(name: str, **fields) -> Iterator[dict]                # times block, logs duration_ms
```

`ContextVar` is the right primitive here: each thread gets its own value (RQ workers),
and each asyncio task inherits a copy at creation (FastAPI). `bind_trace` resets via the
token so nesting and exceptions don't leak state.

`span()` is how stages get HLD §10's `stage_duration_ms` without hand-rolled timing —
it yields a mutable dict the caller can add fields to, and emits one structured log line
on exit (including on exception).

## B9. `observability/logging.py`

```python
class TraceIdFilter(logging.Filter):        # sets record.trace_id from the ContextVar
class JsonFormatter(logging.Formatter):     # one JSON object per line
def configure_logging(config: ObservabilityConfig) -> None   # idempotent
```

Emitted shape:
```json
{"ts":"2026-08-04T10:30:00.123Z","level":"INFO","logger":"lumen.pipeline.extraction",
 "msg":"stage complete","trace_id":"3f2a...","module":"extraction","line":88,
 "stage":"STAGE_1_MICROEXTRACTION","duration_ms":1420,"model_used":"gemini-2.5-pro"}
```

- Any non-standard `LogRecord` attribute (i.e. anything passed via `extra={...}`) is merged in at the top level — that's how `stage`/`duration_ms`/`model_used` above get there.
- `exc_info` renders to an `"exception"` string field rather than a multi-line traceback, keeping one event per line.
- The filter is attached **to the handlers**, not to individual loggers, so every logger in the process is covered — including `kuzu_impl`, `qdrant_impl`, and `sqlalchemy.engine`, with zero changes to Goal 1's code.
- `RotatingFileHandler` → `.jsonl`; optional console handler uses a plain human-readable formatter in dev (`console_json=False`).
- `sqlalchemy.engine` is pinned to `WARNING` unless `OperationalConfig.echo_sql` is set.
- Idempotent: re-entry clears Lumen's own handlers first, so pytest re-invocation doesn't duplicate lines.

## B10. Wiring `trace_id` Into Existing Models

One change in `lumen/schemas/pipeline.py`:

```python
from lumen.observability.trace import get_trace_id

class PipelineDTO(BaseModel):
    trace_id: str | None = Field(default_factory=get_trace_id)
```

`schemas` → `observability` introduces no cycle (`observability` imports nothing from
`schemas`). Outside a bound trace `get_trace_id()` returns `None`, so Goal 2's
`assert event.trace_id is None` still holds — verified as part of this goal, not assumed.

Repositories read the same context variable when stamping `trace_id` onto job, stage-run,
write-log, and HITL rows. No call site passes it explicitly.

## B11. Alembic Setup

- `alembic.ini` at repo root; `script_location = lumen/operational/migrations`.
- `env.py` reads `AppConfig().operational.db_url` rather than the ini's `sqlalchemy.url`, so env vars drive migrations the same way they drive the app.
- `target_metadata = Base.metadata`; `render_as_batch=True` (SQLite has no `ALTER COLUMN` — batch mode makes future migrations possible at all).
- One migration: `0001_initial_ops_schema`.
- **Alembic is the sole schema-creation path** — `Base.metadata.create_all()` is not used, including in tests. Tests run `alembic upgrade head` against a `tmp_path` SQLite file, so the migration is exercised on every run instead of drifting unnoticed.
- A drift test calls `alembic.autogenerate.compare_metadata()` and asserts an empty diff. This is the guard that makes the previous line safe: edit `models.py` without a migration and the suite fails.

## B12. Test Plan (~110 tests)

| File | Focus | ~n |
|---|---|---|
| `test_operational_models.py` | Table/column shape, FK cascade, unique constraints, `PRAGMA foreign_keys` actually on | 14 |
| `test_operational_migrations.py` | `upgrade head`, `downgrade base`, idempotent re-upgrade, **autogenerate drift == []** | 5 |
| `test_operational_session_buffer.py` | `find_or_create` uniqueness on the composite key, message ordering by `seq`, decay window, `build_decay_event` → valid `SessionDecayEvent` | 18 |
| `test_operational_pipeline_jobs.py` | Legal transitions, **illegal transitions raise**, stage lifecycle + metrics, write-log recording, `get_trace` assembly, `find_job_for_node` reverse lookup | 24 |
| `test_operational_hitl.py` | Rank derivation, three-level priority ordering, `count_pending`, `audit_node_id` uniqueness | 14 |
| `test_operational_settings.py` | Round-trip typed values, unknown key raises, `resolve_provider_config` precedence DB > env > default | 12 |
| `test_operational_erasure.py` | `user_id` hashed on write, **no plaintext identifier or content field present**, JSON list round-trip | 8 |
| `test_observability_trace.py` | Mint/get/reset, nesting, **isolation across threads**, `span` timing + exception path | 12 |
| `test_observability_logging.py` | Valid JSON per line, `trace_id` injected, `extra` merged, exception field, idempotent configure | 12 |
| `test_trace_propagation.py` | **The integration test**: one mock 3-stage run — all log lines share a `trace_id`, DTOs carry it, job/stage/write rows carry it, two concurrent traces don't cross | 6 |

`conftest.py` gains: `ops_engine` / `ops_store` (migrated `tmp_path` SQLite),
`bound_trace`, `captured_logs` (a capturing handler that parses emitted JSON).

## B13. Build Order

1. `uv add sqlalchemy alembic`; extend `config.py`.
2. `operational/enums.py` → `models.py` → `engine.py` (+ pragma tests).
3. Alembic init, `0001_initial_ops_schema`, migration + drift tests.
4. `schemas.py` boundary DTOs.
5. `repositories.py` Protocols.
6. `sqlalchemy_impl.py` one repository at a time, each with its test file.
7. `observability/trace.py` + `logging.py` + their tests.
8. Wire `trace_id` into `PipelineDTO` and the repositories; confirm Goal 2's suite still passes.
9. `test_trace_propagation.py`.
10. Add `session_label` to `SessionDecayEvent` / `BufferMessage`; update Goal 2 tests and note the amendment in `Goal_2_Plan.md`.
11. Coverage sweep to ≥90%; apply the three `Technical_HLD.md` edits from A6; update `Master_Plan.md`.

---

# SECTION C — RESULTS

**Status:** ✅ Complete
**Tests:** 435 passing (222 from Goals 1–2, 213 new). **100% coverage** on
`lumen/operational/` and `lumen/observability/`.

## C1. What Was Built

| Module | Contents |
|---|---|
| `operational/enums.py` | 10 enums + 4 lookup tables, including `ALLOWED_JOB_TRANSITIONS` (the job state machine as data, not branching code) |
| `operational/models.py` | 8 tables |
| `operational/engine.py` | Engine factory, SQLite pragmas, `session_scope`, URL password redaction |
| `operational/schemas.py` | 12 boundary records; `WriteLogEntry` validates that an entry names either a node or a full edge |
| `operational/repositories.py` | 5 repository Protocols + `OperationalStore`, 4 exception types |
| `operational/sqlalchemy_impl.py` | 5 repositories, `_SessionManager` (unit of work), `resolve_provider_config` |
| `operational/migrator.py` | Programmatic `upgrade`/`downgrade`/`detect_schema_drift` |
| `observability/trace.py` | `ContextVar` trace ids, `bind_trace`, `span` |
| `observability/logging.py` | `JsonFormatter`, `ConsoleFormatter`, `TraceIdFilter`, `configure_logging` |

Test files: 9 new, ~211 tests.

## C2. Deviations From the Plan

1. **`init_schema()` was kept on the store.** B11 said Alembic would be the sole schema
   path. It is for tests and for the application — but the method remains for throwaway
   databases where a migration is more ceremony than the situation warrants. The
   `ops_engine` fixture runs real migrations, so every test run still exercises them, and
   the drift test still guards the pair.
2. **A ninth test file, `test_operational_store.py`**, covering store construction,
   protocol conformance, connection ownership, and config defaults. Not in the B12 plan;
   added when coverage showed the store's own lifecycle was untested.
3. **`StoredErasureAudit` is a separate type from `ErasureAuditRecord`.** The plan had one
   record type. Splitting them means the type going in carries a real `user_id` and the
   type coming out can only carry a hash — so nothing downstream can read back something
   that looks like a plain identifier. Enforced by a test.
4. **`get_records()` on the settings repository** — not in the Protocol, added on the
   implementation for reading settings with their timestamps.

## C3. Two Bugs Caught by the Tests

1. **`record_write` validated against the ambient trace id, not the job's.** A write
   recorded outside a bound trace failed validation, and worse, a stage replayed later
   would have been filed under whatever trace happened to be current instead of its own
   run's. Fixed to read the trace id from the job row.
2. **A circular import.** `config` → `schemas.enums` → `schemas/__init__` → `pipeline` →
   `observability` → `config`. Broken by having `logging.py` import `ObservabilityConfig`
   lazily inside `configure_logging()`; observability sits beneath configuration and
   should not need it at import time.

## C4. Doc Changes Made

- **`Technical_HLD.md` §10** — rewritten to describe the `pipeline_write_log` mapping
  instead of a `trace_id` column on graph nodes and edges (which no table has).
- **`Technical_HLD.md` §4.1** — table list corrected to the eight actually built;
  `api_keys` marked as Goal 4; the "sensitivity prefs" description of `user_settings`
  replaced with the key/value override model, since Goal 2 removed that concept.
- **`CLAUDE.md`** — the "modules carry a docstring naming the spec section" convention was
  replaced, per user instruction, with plain-language comments that cite no docs. Spec
  traceability lives in these plan files instead.
- **`Master_Plan.md`** — Goals 3 and 3b checked off with result lines.

### Session decay and queue cap raised (post-implementation, user decision)

`OperationalConfig` defaults were changed to `session_decay_minutes=120` (from 60) and
`hitl_queue_cap=40` (from 20). Both had been written into the specs as fixed numbers, so
the docs were updated to match rather than leaving code and docs in disagreement:

| Doc | Change |
|---|---|
| `Interface_Architecture.md` §Daily Session Buffer | 1 hour → 2 hours of inactivity, noted as configurable |
| `HLDv2.md` (flow diagram + Ingestion Layer) | 1hr → 2hr decay, noted as configurable |
| `Preprocessing.md` §Session Decay | 1 hour → 2 hours; the later "1-hour decay" reference reworded to not restate the number |
| `Reconciliation.md` §Queue Capacity | "Maximum queue size: 20 items" → 40, noted as configurable; the two follow-on rules now say "the cap" instead of restating it |
| `Schema.md` §DecisionStatus | `SUSPENDED_QUEUE_FULL` description no longer hard-codes 20 |
| `ROADMAP.md` risk table | Hard cap 20 → 40 |
| `Master_Plan.md` Goal 18 | "20-item queue cap" → "configurable queue cap (default 40)" |

Where a number appeared more than once in the same document, the secondary mentions were
reworded to refer to "the cap" or "the decay window" rather than repeating the figure —
so the next change to these values has one place to edit per doc, not several.

The corresponding test stopped pinning the exact defaults (they are a deployment choice,
not a guarantee) and now asserts only that they are positive, with two added tests
covering the `LUMEN_SESSION_DECAY_MINUTES` / `LUMEN_HITL_QUEUE_CAP` override path.

## C5. Amendment to Goal 2

`SessionDecayEvent` gained a `session_label` field (defaulting to `""`). The session
buffer is keyed by `(user_id, event_date, session_label)` and `SessionNode` carries the
label, but the decay DTO did not — Stage 0 would have had nothing to stamp onto the node
it creates. `PipelineDTO.trace_id` also changed from a plain `None` default to
`default_factory=get_trace_id`; outside a run it still resolves to `None`, so Goal 2's
existing assertions were unaffected (verified — all 222 earlier tests still pass).

## C6. Still Deferred

Unchanged from A7: the decay watcher loop (Goal 10), HITL cap/snooze/auto-resolve
(Goal 18), the erasure anonymization pass (Goal 19), `api_keys` and encryption (Goal 4),
FastAPI routes (Goals 11+), and OpenTelemetry export (Goal 20).

One item worth naming for Goal 4: `resolve_provider_config()` is written and tested, but
nothing calls it yet — the role-resolution factory that consumes it is Goal 4's.
