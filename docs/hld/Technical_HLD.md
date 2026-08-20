# Lumen — Technical High Level Design

*Author: System Design Session, June 27 2026*
*Status: Living Document — Personal → SaaS scaling path*

---

## 0. Design Mandate

Three constraints shape every decision in this document:

1. **Today it's a personal project.** Every piece must be runnable on a single MacBook with zero cloud spend. No Docker orchestrators. No managed services.
2. **Tomorrow it must scale.** Every architectural boundary chosen today must map cleanly to an independently deployable service later. No rewrites — only extractions.
3. **Always debuggable.** Every pipeline decision must be inspectable, replayable, and reversible. The data is deeply personal — a bug that silently corrupts someone's knowledge graph is unacceptable.

The fundamental insight: **the pipeline is the product**. The value of Lumen is not in storing data — it is in the chain of transformations from raw voice to structured knowledge. The architecture must treat each transformation stage as a first-class, independently observable unit.

---

## 1. System Map

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           CLIENT LAYER (Next.js)                             │
│   Daily Chat  │  Past Days  │  Graph Explorer  │  HITL Queue  │  Reports     │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │ HTTPS / WebSocket
┌───────────────────────────────▼──────────────────────────────────────────────┐
│                        BFF / API GATEWAY (FastAPI)                           │
│  /chat  /ingest  /query  /graph  /hitl  /reports  /sessions  /providers      │
└──┬──────────────┬──────────────┬──────────────────┬──────────────────────────┘
   │              │              │                  │
   ▼              ▼              ▼                  ▼
[Ingestion    [Query         [HITL           [Scheduler
 Service]      Service]       Service]        Service]
   │              │                            │
   ▼              ▼                            ▼
[Pipeline                              [Macroextraction
 Orchestrator]                          Runner]
   │
   ▼   (task queue)
┌──────────────────────────────────────────┐
│              WORKER POOL                 │
│  [Extraction Worker]  [Retrieval Worker] │
│  [Reconciliation Worker]                 │
└──────────────────┬───────────────────────┘
                   │ reads/writes
         ┌─────────┴──────────┐
         ▼                    ▼
   [Graph Store]       [Vector Store]
   (Kuzu / Neo4j)      (Qdrant)
         │
         ▼
   [Operational DB]
   (SQLite / PostgreSQL)
```

---

## 2. Tech Stack Decisions

Every choice below includes a **personal project option** and a **production option**. The interface between them is always identical — swapping is config-only, not a code change.

### 2.1 Backend Runtime

| Layer | Choice | Rationale |
|---|---|---|
| Language | **Python 3.13** | Already decided (Protocol-based LLM abstraction in LLM_Abstraction.md). Ecosystem dominance for LLM tooling. |
| API Framework | **FastAPI** | Async-native, automatic OpenAPI docs, Pydantic-native (same library used for stage schemas), streaming support for chat. |
| Schema / Validation | **Pydantic v2** | Already used in pipeline stage contracts. All inter-stage data transfer objects are Pydantic models. Schema validation between stages is free. |
| Async Runtime | **asyncio + anyio** | Native to FastAPI. Allows concurrent LLM calls without threads. |

### 2.2 Graph Database

The most critical database decision in the system.

| Option | Personal | Production |
|---|---|---|
| **Kuzu** | ✅ Embedded (no server), file-based, Python API, Cypher-compatible | ❌ Single-node only |
| **Neo4j Community** | ✅ Free, local Docker, Cypher, excellent tooling | ✅ Neo4j Aura (managed cloud) |
| **FalkorDB** | ✅ Redis-based, extremely fast | ✅ Scales with Redis Cluster |

**Decision: Kuzu for personal, Neo4j for production.**

Kuzu is the SQLite of graph databases — embedded, zero-config, file-based. The Cypher query syntax it uses is identical to Neo4j. When the project scales, the graph query layer is untouched — only the connection string changes. This is the cleanest migration path possible.

```python
# Personal (Kuzu)
db = kuzu.Database("./lumen_graph.db")

# Production (Neo4j)
driver = GraphDatabase.driver("bolt://neo4j-host:7687", auth=("neo4j", "password"))
```

Both are hidden behind a `GraphProvider` Protocol, identical to the LLM abstraction pattern.

### 2.3 Vector Database

| Option | Personal | Production |
|---|---|---|
| **Qdrant** | ✅ In-process mode (`:memory:` or local file), Python client, built-in sparse + dense (BM25 + vectors = hybrid search out of the box) | ✅ Qdrant Cloud or self-hosted cluster |
| **ChromaDB** | ✅ Simplest setup | ❌ No sparse/hybrid search, performance degrades at scale |
| **Weaviate** | ❌ Heavy for personal | ✅ Strong managed offering |

**Decision: Qdrant exclusively.**

Qdrant's killer feature for Lumen: **native sparse + dense hybrid search** in a single query. The BM25 + vector fusion (Step 2, HyDE retrieval) is built into Qdrant with no extra infrastructure. No need for Elasticsearch for BM25 — one DB does both. The in-process mode means zero server for the personal version.

```python
# Personal (in-process)
client = QdrantClient(":memory:")  # or path="./lumen_vectors"

# Production
client = QdrantClient(url="https://qdrant-host:6333", api_key="...")
```

### 2.4 Operational Database

Stores everything that's NOT graph data: session buffers, HITL queue, pipeline job state, provider configs, user settings, audit logs.

| Personal | Production |
|---|---|
| **SQLite** (file-based, zero config, via SQLAlchemy) | **PostgreSQL** (via SQLAlchemy — same ORM, same models, different URL) |

SQLAlchemy ORM + Alembic migrations means the schema is always code-controlled. Moving from SQLite → PostgreSQL is one environment variable change.

### 2.5 Task Queue (Pipeline Orchestration)

The 7-step pipeline runs asynchronously after session decay. Each stage is a task.

| Personal | Production |
|---|---|
| **Python-RQ** backed by **Redis (local)** | **Celery** + **Redis / RabbitMQ** |
| OR: **SQLAlchemy-based job queue** (no Redis dep) | OR: **Apache Kafka** for full event streaming |

**Decision: Python-RQ with Redis for personal. Celery + Kafka for production.**

For personal use, Python-RQ is dramatically simpler than Celery — it's three lines of code to enqueue a task. Redis runs in a single Docker container or via Homebrew. For production, Kafka gives replay, dead letter queues, and independent consumer scaling.

The orchestrator defines task contracts as Pydantic models. The queue is a transport detail behind an `OrchestratorProvider` Protocol.

### 2.6 Frontend

| Layer | Choice | Rationale |
|---|---|---|
| Framework | **Next.js 15 (App Router)** | Full-stack: API routes handle BFF logic, no separate Node server needed for personal. React Server Components for fast load. |
| Language | **TypeScript** | Type-safe API contracts with backend (zod schemas shared or generated from Pydantic). |
| UI Components | **shadcn/ui + Radix** | Accessible, unstyled primitives. Gives full design control without fighting a UI library. |
| Styling | **Tailwind CSS** | Co-located with components, works perfectly with shadcn. |
| Graph Visualization | **react-force-graph** (2D/3D) | WebGL-rendered force-directed graph for the Knowledge Graph Explorer. Handles thousands of nodes. |
| State Management | **Zustand** | Minimal, no boilerplate. Session buffer state, HITL queue state. |
| Real-time | **WebSockets (native Next.js + FastAPI)** | Streaming AI responses. Pipeline progress updates. |
| Charts | **Recharts** | Pattern frequency trends, belief evolution over time. |

### 2.7 LLM & Embedding Providers

Already specified in `LLM_Abstraction_Architecture.md`. Summary:

Provider selection is a **single point of configuration** (`lumen.config.ProviderConfig`),
keyed by model-capability **role**, not by content sensitivity. Each role's provider and
model are independently overridable via env var; the abstraction never assumes or
enforces a deployment locality (cloud vs. local) for any role.

| Role | Used By | Default Provider / Model | Status |
|---|---|---|---|
| `LIGHTWEIGHT` | Quality-gate scoring, low-risk Reconciliation actions (MERGE/REINFORCE/BRANCH/REGULATE), Query Formulation turn classification, HyDE expansion | `gemini` / `gemini-2.5-flash` | Implemented (Goal 4) |
| `THINKING` | High-consequence Reconciliation actions (EVOLVE/CONTRADICT/DIALECTIC), Macroextraction synthesis | `gemini` / `gemini-2.5-pro` | Implemented (Goal 4) |
| `EMBEDDING` | Dense vector generation for the Vector Store | `gemini` / `text-embedding-004` | Implemented (Goal 4) |
| `TRANSCRIPTION` | Voice-note speech-to-text | `whisper_cpp` / `base.en` | Protocol only |
| `TTS` | Text-to-speech | `macos` / `default` | Protocol only |

A maintainer who wants every AI call to run locally (for privacy or offline use)
reconfigures all five roles to local providers (e.g. `ollama` for `LIGHTWEIGHT`/`THINKING`
and `EMBEDDING`) — a one-time deployment choice, not a runtime routing decision the pipeline
makes per piece of content, and not something the end user is offered. Configuration is read
from the environment at process start; there is no runtime or user-facing switcher.

### 2.8 Audio (STT / TTS)

| Direction | Personal | Production |
|---|---|---|
| Speech → Text | `whisper.cpp` (local, fast, free) | `Deepgram` or `Assembly AI` (cloud) |
| Text → Speech | macOS system neural voices | `ElevenLabs` or `OpenAI TTS` |

Both sit behind `AudioTranscriptionProvider` and `TTSProvider` Protocols, configured via the
same `ProviderConfig` `TRANSCRIPTION`/`TTS` roles described in §2.7. Goal 4 **defines** these
two Protocols but implements neither — the extraction pipeline (Goals 5–9) needs no audio, and
whisper.cpp brings a binary and model-file dependency that belongs with the voice-ingestion
work that first consumes it.

---

## 3. Service Decomposition

The personal version runs all services as Python modules in a single process. The production version extracts each into its own Docker container + replica set. The service boundaries are drawn today so extraction is mechanical, not architectural.

### 3.1 Service Registry

| Service | Responsibility | Personal | Production |
|---|---|---|---|
| **BFF / API Gateway** | Single entry point for all client requests, auth, rate limiting | FastAPI process on port 8000 | FastAPI + Nginx + TLS |
| **Identity Service** | Google sign-in, token issue/refresh/revoke, JWKS, user records | `lumen/auth/`, a module in the BFF [Goal 21] | Separate FastAPI service; every other service verifies against its JWKS and none can mint |
| **Ingestion Service** | Receive messages, voice uploads, external log imports → write to Session Buffer | Module in BFF | Separate FastAPI service |
| **Pipeline Orchestrator** | Watch Session Buffer for decayed sessions → dispatch pipeline jobs | `lumen/scheduling/`, a background thread in the BFF [Goal 20] | Dedicated Celery beat scheduler |
| **Extraction Worker** | Steps 0 + 1 (Preprocessing + Microextraction) | Python-RQ worker | Celery worker, N replicas |
| **Retrieval Service** | Step 2 (HyDE + Hybrid Search) | Function call in Extraction Worker | Separate FastAPI service (CPU-bound, scale independently) |
| **Reconciliation Worker** | Step 3 (Reconciliation decisions + HITL escalation) | Python-RQ worker | Celery worker, separate queue from Extraction |
| **Graph Service** | Step 4 + all graph reads | Module in BFF | Separate FastAPI service with connection pool |

| **Query Service** | Step 5 (GraphRAG + Conversational RAG Mode) | Module in BFF | Separate FastAPI service |
| **HITL Service** | Review queue management, one-tap decisions | Module in BFF | Separate FastAPI service |
| **Scheduler** | Trigger every recurring job on a schedule — the decay watcher, reports due, the shadow scan, the review sweep | `lumen/scheduling/scheduler.py`, one background thread in the BFF [Goal 20] | Kubernetes CronJob |
| **Formulation Service** | Query Formulation Layer (Conversational RAG) — classifies turn, emits RetrievalSignal | `lumen/query/formulation/`, a synchronous call in the Query Service | Sidecar in Query Service |
| **Conversational Retrieval** | Passes A/B/C for a live turn under one shared budget, plus the sensitivity gate | `lumen/query/retrieval/`, a synchronous call in the Query Service | Sidecar in Query Service |
| **Context Assembly & Prompting** | Compresses retrieved nodes into a briefing, builds the system prompt, keeps the conversation's own memory | `lumen/query/{assembly,prompting,memory}/`, `lumen/query/conversation.py` | Module in Query Service |

**Note on `lumen/query/`.** The query layer is a top-level package, not a member of `lumen/pipeline/`. Two rules that protect the pipeline do not apply to it and would read as violations if it lived there: it never writes to the graph, and it holds per-conversation state for the length of a session. The pipeline's stages may do neither.

Since Goal 15 it does write *conversations* — the turns themselves and a running summary — into the operational store. That is not a graph write and does not weaken the guarantee that matters: nothing on this side can create, change or retire a record of somebody's history.

### 3.2 Personal Project Topology

```
lumen/
├── api/              ← FastAPI BFF (all services co-located as modules)
│   ├── routes/
│   │   ├── chat.py
│   │   ├── ingest.py
│   │   ├── query.py
│   │   ├── graph.py
│   │   ├── hitl.py
│   │   └── reports.py
│   └── main.py
├── pipeline/         ← Pipeline workers (run as RQ tasks)
│   ├── orchestrator.py
│   ├── preprocessing.py
│   ├── extraction.py
│   ├── retrieval.py
│   ├── reconciliation.py
│   └── macroextraction.py
├── graph/            ← Graph store abstraction
│   ├── provider.py   ← GraphProvider Protocol
│   ├── kuzu_impl.py
│   └── neo4j_impl.py
├── vector/           ← Vector store abstraction
│   ├── provider.py
│   └── qdrant_impl.py
├── providers/        ← LLM/STT/TTS providers
│   ├── gemini.py
│   ├── ollama.py
│   └── whisper.py
├── schemas/          ← All Pydantic models (shared between API + workers)
│   ├── nodes.py
│   ├── edges.py
│   ├── pipeline.py   ← Inter-stage data transfer objects
│   └── api.py        ← API request/response contracts
├── config.py         ← AppConfig (provider injection)
├── workers.py        ← RQ worker entrypoint
└── frontend/         ← Next.js app
```

### 3.3 Production Topology

Each directory above becomes a Docker image. `docker-compose.yml` for staging. Kubernetes manifests for production. The only thing that changes between local and production is `config.py` — which providers are injected.

---

## 4. Database Architecture

### 4.1 Three-Database Strategy

Lumen uses **three separate data stores**, each optimized for its access pattern:

```
┌─────────────────────────────────────────────────────────┐
│                    GRAPH STORE (Kuzu/Neo4j)             │
│  PatternNode, BeliefNode, EpisodeNode, ObservationNode  │
│  EventNode, SessionNode, DecisionAuditNode              │
│  All edges (same-as, evolved-from, reinforces, etc.)    │
│  Access pattern: Cypher traversal, multi-hop queries    │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                   VECTOR STORE (Qdrant)                 │
│  node_id → embedding vector (dense)                     │
│  node_id → BM25 sparse vector                           │
│  Payload: node_type, created_at                         │
│  Access pattern: hybrid search, nearest-neighbor        │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│               OPERATIONAL DB (SQLite/PostgreSQL)        │
│  session_buffers + buffer_messages: pending extraction  │
│  pipeline_jobs: run state, retries, errors              │
│  pipeline_stage_runs: per-stage metrics, input/output   │
│  pipeline_write_log: trace → graph/vector write mapping │
│  hitl_queue: decisions pending human review             │
│  user_settings: key/value config overrides              │
│  data_erasure_audit: erasure records (no user content)  │
│  users / user_identities / refresh_tokens  [Goal 21]    │
│  Access pattern: standard CRUD, status polling          │
└─────────────────────────────────────────────────────────┘

Notes on the table set (resolved in Goal 3):
- `session_buffer` is two tables — the buffer (keyed `user_id` + `event_date` +
  `session_label`) and its ordered messages.
- `pipeline_jobs` is three tables. `pipeline_stage_runs` stores per-attempt timing,
  model, validation outcome, and the input/output payloads that make
  `rerun_from_stage` possible; `pipeline_write_log` is the trace→graph mapping
  described in Section 10.
- `user_settings` holds generic `(user_id, key, value_json)` overrides, resolved as
  **DB override > env var > code default**. Two things it does *not* hold:
  - "Sensitivity prefs" — the sensitivity/routing-tier concept was removed in Goal 2
    (see Section 2.7).
  - **Provider selection or credentials.** Which model backs a `ModelRole` is a
    deployment property owned by the maintainer, read from the environment once at
    process start; the provider factory has no operational-DB dependency (Goal 4).
- **There is no `api_keys` table.** Provider credentials are read from environment
  variables and are never persisted by the application — no encrypted secrets store,
  no key management, no settings row that can supply a key. Goal 3 listed this table as
  deferred to Goal 4; Goal 4 cancelled it (see `implementation/Goal_4_Plan.md` A2-4).
- **`users`, `user_identities` and `refresh_tokens` arrive in Goal 21** and are the only
  new tables multi-user needs here. Every other table has carried `user_id` since Goal 3,
  so tenancy in this store is already correct and needs no migration. The graph and vector
  stores carry no notion of a user at all, which is why they are split per user rather than
  filtered — see [`Auth_Architecture.md`](file:///Users/hemangmishra/Projects/Lumen/docs/hld/Auth_Architecture.md) §6.
  Note what `refresh_tokens` stores: a hash, never the token, and a hashed IP rather than an
  IP — the same rule the rest of this store already follows.
```

**Multi-user splits two of these three, and not the third.** Each user gets their own Kuzu
database directory and their own Qdrant collection, resolved from the authenticated identity
by a store registry (Goal 22). The operational database stays shared — it holds no graph
content, it is already keyed by `user_id` everywhere, and splitting it would fragment the one
place that can answer a question about the deployment as a whole.

### 4.2 Node ID as the Universal Key

Every node has a stable `node_id` that is the join key across all three stores. When a node is written to the Graph Store, its embedding is simultaneously written to the Vector Store with the same `node_id`. When a query retrieves node_ids from the Vector Store, they are resolved to full nodes via the Graph Store.

```python
# Write path (Step 4)
node_id = graph_store.write_node(observation_node)  # → Kuzu/Neo4j
vector_store.upsert(node_id, embedding, payload)    # → Qdrant

# Read path (Step 2)
candidate_ids = vector_store.hybrid_search(query_vector, bm25_query)  # → Qdrant
candidates = graph_store.get_nodes_by_ids(candidate_ids)              # → Kuzu/Neo4j
```

---

## 5. Pipeline Data Contracts

Every pipeline stage communicates via Pydantic models. These are the "contracts" that make stages independently testable and replaceable.

```python
# schemas/pipeline.py

class SessionDecayEvent(BaseModel):
    session_id: str
    user_id: str
    event_date: date           # logical date of the session
    session_label: str         # separates same-day conversations
    source_modality: SourceModality   # VOICE_NOTE gates the ASR-only cleaning rules
    message_count: int
    raw_buffer: list[BufferMessage]
    triggered_at: datetime

class PreprocessingResult(BaseModel):
    session_id: str
    episodes: list[PreprocessedEpisode]
    coreference_map: CoreferenceMap   # resolved_entities + ambiguous_refs
    quality_gate_decision: Literal["REFLECTION", "RAW_CAPTURE", "DISCARD"]
    processing_time_ms: int
    pending_reflections: list[str]    # RAW_CAPTURE follow-up questions
    co_created_spans: list[str]       # assistant framings the user adopted

class MicroextractionInput(BaseModel):
    """Everything Stage 1 needs about one episode, and nothing about history.

    PreprocessedEpisode carries no date, no coreference map and no modality,
    while every node Stage 1 builds needs occurred_at — so the stage boundary
    is this wrapper rather than the bare episode.
    """
    episode: PreprocessedEpisode
    coreference_map: CoreferenceMap
    entry_id: str                     # the session the episode came from
    event_date: date
    occurred_at: datetime             # logical event time for this episode
    source_modality: SourceModality
    session_label: str
    co_created_spans: list[str]

class ExtractionResult(BaseModel):
    episode_id: str
    observations: list[ObservationNode]
    events: list[EventNode]
    sessions: list[SessionNode]       # the minted causal anchor
    causal_chains: list[CausalChainNode]
    causal_steps: list[CausalStepNode]
    failed_observations: list[ObservationNode]   # spent all 3 attempts; for HITL
    extraction_model: str
    validation_passed: bool           # false if anything was dropped or nothing survived
    retry_count: int                  # corrections spent, 0 when the first reading was clean
    read_failed: bool                 # the episode could not be read at all

class RetrievalResult(BaseModel):
    """One per searchable node — Stage 2 returns a list of these, not one."""
    source_node_id: str  # ObservationNode | EventNode | SessionNode
    pass_a_candidates: list[CandidateNode]  # semantic
    pass_b_candidates: list[CandidateNode]  # structural
    retrieval_time_ms: int
    search_failed: bool  # could not search, as distinct from found nothing

class ReconciliationResult(BaseModel):
    source_node_id: str  # ObservationNode | EventNode | SessionNode
    action: ReconciliationAction
    target_node_id: str | None
    confidence: float
    delta_description: str | None          # mandatory for EVOLVE
    decision_model: str
    escalated_to_hitl: bool
    audit_node_id: str

class ReconciliationOutcome(BaseModel):
    """
    What Stage 3 actually returns for one episode. An episode produces many
    decisions and many audit nodes, plus the writes they imply, and all of it
    has to arrive together or the orchestrator cannot execute it atomically.
    """
    episode_id: str
    results: list[ReconciliationResult]
    audit_nodes: list[DecisionAuditNode]
    write_plan: GraphWritePlan       # nodes + edges + bookkeeping; nothing written
    escalations: list[HitlEscalation]
    episode_status: ReconciliationStatus   # COMPLETE | SUSPENDED
    decision_model: str
    decision_time_ms: int
    decision_failed: bool            # no readable answer, as distinct from few decisions
```

**The write plan is the hand-off.** Stage 3 decides and builds; the orchestrator
executes without interpreting. A `GraphWritePlan` validates its own internal
consistency on construction — every edge endpoint is either created earlier in the
same plan or listed in `existing_node_ids` (records already in the graph, plus the
nodes this same run extracted, which the orchestrator writes immediately before the
plan runs). A dangling reference fails while planning rather than halfway through
saving.

**The orchestrator adds the structural half.** Stage 3 is shown what was extracted, not
the episode it came from, so it cannot create the `EpisodeNode` or link anything to it.
The orchestrator builds that half — the episode record, `contains_*`, `chain_contains`,
`failed_extraction`, `follows_from` — and merges it with Stage 3's plan into one
`GraphWritePlan`, so the plan's own checks cover the whole episode. It returns a
`RunReport` carrying one `EpisodeOutcome` per episode.

```python
def run_pipeline(
    event: SessionDecayEvent, *, graph: GraphProvider, vectors: VectorProvider,
    embedder: EmbeddingProvider, lightweight: LLMProvider, thinking: LLMProvider,
    ops: OperationalStore, config: AppConfig | None = None,
) -> RunReport: ...
```

It is a plain synchronous function today, not a queued task, and it stays one. **Goal 20
shipped the watcher** that starts a run when a session goes idle, and the run itself goes
on the importer's existing queue and thread — the one that already holds the models and
already serialises runs so that two entries are never written at once.

The RQ/Redis topology below is **not** the personal build and was not built. The personal
version is one process by design, this table's own left column says so, and adding a broker
and a second process for a single user builds the production topology to serve one person.
Every job here is minutes long and nobody waits on one. It is a deployment change and it
belongs with a deployment.

Each worker accepts an input model and emits an output model. The orchestrator is the only component that chains them. This means any stage can be tested in complete isolation by constructing its input model and asserting its output model — no real DB, no real LLM required.

---

## 6. Conversational RAG Integration

The Query Service contains the Conversational RAG Mode (from `Query/Conversational_RAG_Mode.md`). Here is how it maps to code:

```
User turn arrives (WebSocket message)
        │
        ▼
  QueryFormulator.formulate(turn, session) ← LIGHTWEIGHT role, 600ms hard deadline
        │   crisis floor and acknowledgement list run first, in code, with no model call
        │   surviving triggers are grounded against the graph before they leave
        │
        ├─ NO_TRIGGER → pass to AI immediately (no wait)
        │
        └─ TRIGGER → ConversationalRetriever.retrieve(signal, session)
               │            one shared 3s wall clock, enforced from outside
               │
               ├─ PassA ─┐ qdrant.hybrid_search(hyde_expansion), ≤2s
               ├─ PassB ─┘ graph anchors, chosen per trigger — run side by side
               │
               └─ PassC: session buffer, after A, on A's query vector
                         │
                         ▼
                  sensitivity gate: CRITICAL nodes in unopened domains withheld
                         │
                         ▼
                  merge: dedupe (anchor copy wins), rank, cut
                         │
                         ▼
                  ContextAssembler.assemble(bundle, signal)
                         │   allowance set by register: 0 / 400 / 800 / 1500 tokens
                         │   repeats collapsed, no more than 3 of any one kind
                         ▼
                  PromptComposer.compose(...) → ChatPrompt
                         │   persona + briefing + rolling summary + recent turns
                         │   a different, shorter instruction entirely in CRISIS
                         │
                         ▼
                  AI generates response (streaming)
```

The `SessionContextBuffer` lives in memory per-session (Zustand on frontend, Python dict in Query Service). It is NOT persisted to the graph — it is ephemeral per calendar day.

That ephemeral day-state is `lumen.query.session.ChatSession`, held by a `SessionRegistry` keyed on `(user_id, session_label)`. Asking for a session on a date the held one does not cover replaces it — that is the entire midnight rule, with no timer and no sweep. It holds the recent turns, the sensitive domains the user has opened themselves, and (since Goal 14) the `SessionContextBuffer` itself. Its identity `(user_id, event_date, session_label)` matches the operational `session_buffers` key, so a live conversation and the buffer that will later be ingested are recognisably the same thing.

**Passes A and B are parallel; Pass C is not, and cannot be.** Pass C measures the buffer against the current turn using the query vector Pass A has just computed. Running it alongside A would mean either measuring against nothing or paying for a second embedding of the same sentence. It runs afterwards, on numbers already in memory, in about a millisecond. Each buffered node caches its own stored vector on admission (`VectorProvider.get_vectors`, added in Goal 14), so no per-turn search is needed; where a vector is missing the comparison falls back to word overlap.

**The retrieval result reports each pass separately**, including whether it ran at all. A pass that failed, a pass that found nothing, and a pass with nothing to look up are three different facts, and the layer above answers all three identically unless it is told which one happened.

### 6.1 What the assistant is actually sent

`ChatPrompt` is the single object every earlier stage feeds: the system instruction, the recent turns, and the briefing that went into the instruction with a record of what was cut. It is a pure function of its inputs, which is what makes `POST /query/prompt` able to print exactly what the model would receive before any chat surface exists.

The instruction has a fixed order — identity, how to be, the briefing and how to use it, where the conversation has got to, safety — and empty sections are omitted rather than left as headings. In `CRISIS` the whole instruction is replaced by a shorter one; withholding the history while still asking for curiosity and pattern-noticing would be half a decision.

### 6.2 Conversation storage and memory

**The query layer now writes, and the distinction matters.** It never writes to the graph — that guarantee is unchanged and is what `ReadOnlyGraph` enforces by type. What it writes is the conversation itself, into the same `session_buffers` the extraction pipeline consumes. A chat held anywhere else would be a chat that never becomes history.

Messages carry `parent_message_id` and the buffer carries `active_message_id`, so a conversation is a tree and the readable thread is one path through it. Editing writes a sibling and moves the pointer; nothing is destroyed. `build_decay_event` follows the active thread, so an abandoned branch never becomes graph history.

Memory is the recent turns verbatim plus a `rolling_summary` stored on the buffer, refreshed every few turns by one cheap call made *after* the reply goes out. Each refresh folds the previous summary plus what has been said since, so a three-hour conversation costs what a ten-minute one does.

The formulation model is resolved through the `LIGHTWEIGHT` role but built with `max_attempts=1`. Every other call in the system retries with backoff, which is correct for work nobody is waiting on; this one has a sub-second deadline that a retry has already missed.

---

## 7. Frontend Architecture

### 7.1 Page Structure (Next.js App Router)

```
app/
├── (auth)/
│   ├── login/              ← Continue with Google. The only public route.
│   └── callback/           ← receives ?code&state, posts it to the API
├── (app)/
│   ├── layout.tsx          ← App shell: sidebar nav, session indicator
│   ├── chat/
│   │   └── page.tsx        ← TODAY's session (Conversational RAG Mode)
│   ├── history/
│   │   ├── page.tsx        ← Past days list
│   │   └── [date]/
│   │       └── page.tsx    ← Past day chat (read-only + query mode)
│   ├── graph/
│   │   └── page.tsx        ← Knowledge Graph Explorer (react-force-graph)
│   ├── review/
│   │   └── page.tsx        ← HITL Review Queue (one-tap decisions)
│   ├── reports/
│   │   ├── page.tsx        ← Report list
│   │   └── [period]/
│   │       └── page.tsx    ← Weekly/Monthly/Quarterly report
│   └── settings/
│       └── page.tsx        ← Provider config, sensitivity settings
└── api/
    ├── chat/route.ts       ← BFF: stream AI response
    ├── ingest/route.ts     ← BFF: receive external log import
    └── graph/route.ts      ← BFF: graph data for explorer
```

**The `(auth)` group is real** as of Goals 21–22. It was drawn here before anything backed
it, and `docs/frontend/Requirements.md` correctly flagged it as a route with no system behind
it. That is now settled the other way: Lumen is multi-user, sign-in is Google, and `(auth)`
is the only route group reachable without a token. What each screen must do is S11 in that
document; what the service must provide is `Auth_Architecture.md`.

Everything under `(app)` requires an identity. Under DEC-2 in the frontend requirements
there is no BFF to enforce that, so the enforcement is the API's own — a router-level default
dependency, not a per-route decorator.

### 7.2 Key UI Surfaces

**Daily Chat (the main surface)**
- Left sidebar: day navigator (calendar), today highlighted
- Center: chat interface — streaming AI responses
- Right sidebar (collapsible): Pipeline status indicator showing extraction state, HITL count
- Bottom: voice input button + text input
- Midnight: soft nudge — "Today's session is winding down. Anything to capture before tomorrow?"

**Knowledge Graph Explorer**
- Force-directed graph (react-force-graph)
- Node types: color-coded (Belief = blue, Pattern = orange, Episode = gray, Event = green)
- Click a node → detail panel with full YAML, evidence, linked nodes
- Timeline scrubber: see the graph at any past date (uses `valid_from` timestamps)
- Filter by domain, sensitivity tier, date range

> **What backs each of these.** The starting view and the filters are
> `GET /graph/nodes`; the counters are `GET /graph/stats`; expand-on-click is
> `GET /graph/nodes/{id}/neighbors`, which returns nodes and edges together and sets
> `truncated` when a limit cut the answer short — a partial graph drawn as a complete one
> is a wrong answer that looks right. The detail panel is `GET /graph/nodes/{id}` plus
> `/versions` and `/decisions`. The timeline scrubber is the `as_of` parameter on
> `neighbors`, which also keeps links a later rollback withdrew.
>
> **Depth is capped at three hops.** Past that, a well-connected graph is mostly reachable
> from anywhere in it, so a deeper walk is not a more detailed answer — it is the whole
> history fetched by accident.

**HITL Review Queue**
- Card-based UI: one card per AMBIGUOUS decision
- Shows: what was extracted, what was retrieved, what the AI proposed
- Actions: ✅ Approve / ❌ Reject (create BRANCH) / ✏️ Edit (manual reconciliation)
- Badge count in sidebar nav shows pending items
- Items older than 7 days auto-resolve (per spec)

**Pipeline Debug View** (accessible from Settings)
- Per-session trace: every stage with timing, model used, input/output schemas
- Expandable: click any stage to see the exact Pydantic model that entered and exited
- Re-run button: re-queue a failed stage without reprocessing the full pipeline
- Decision Audit Trail: visual diff of what EVOLVE changed

> **What backs it.** `GET /debug/traces/{trace_id}` returns the job, every stage attempt in
> order with its timings and model, the payloads that went in and came out, and everything
> the run wrote. `GET /debug/nodes/{node_id}/provenance` answers the other direction — node
> to run to conversation — which is why no trace identifier has to live on graph nodes.
>
> The re-run button has no endpoint yet: `rerun_from_stage` is not implemented (see §10).

### 7.3 Real-time Updates

**Two sockets, not one** (Goal 20). `GET /chat/ws` streams a reply as it is written;
`GET /events/ws` carries everything else. They have different lifetimes and their failures
mean different things — a dropped reply stream loses a sentence somebody is waiting for, a
dropped event stream loses a notification. On one socket the reply stream would have to
stay open between conversations and a notification could arrive mid-sentence.

The reply stream pushes:
- AI response tokens, and what was gathered for the turn

The event stream pushes:
- `run_started` / `run_finished` — a conversation or an import going through the pipeline
- `job_ran` / `job_failed` — what the scheduler did on a pass

**Broadcast, not delivered.** Nothing is stored and nothing is replayed for somebody who
was not connected; a short backlog (`GET /events`) exists only so a page that has just
opened is not blank. The queue count, the runs and the reports are each readable from the
endpoint that owns them, and a system that kept every event would be keeping a second,
worse copy of what the graph and the job records already hold.

**A slow listener drops its own messages and nobody else's.** A browser left open on a
sleeping laptop must not be able to hold up the pipeline.

---

## 8. Modularity Rules (Non-Negotiable)

These rules are the architectural contract that keeps the codebase debuggable and scalable:

### Rule 1: Providers are always Protocols
No direct imports of `google.generativeai`, `openai`, `kuzu`, or `qdrant_client` outside their `providers/` module. Every call goes through a Protocol. Business logic has zero knowledge of vendor SDKs.

**There is no general query method, and there will not be one.** Every graph read is a
named question — "what is within N steps of this", "how has this belief changed" — rather
than a way to run arbitrary Cypher. A general one would push query building out to
callers, spread graph-shaped thinking into the web layer, and quietly end the promise that
Kuzu can be swapped for Neo4j without touching business logic. An `execute_cypher()` was
once planned for the read APIs and was **cancelled** for exactly that reason; anything the
system cannot answer today is a deliberate addition to `ReadOnlyGraph`, visible in review.

**Read-only callers get `ReadOnlyGraph`.** `GraphProvider` extends it with the writes. A
component whose job is reading — the web layer, today — is handed the narrower type, so a
write is not merely discouraged there: the method is not on the object it was given.

### Rule 2: Pipeline stages are pure functions
Each stage function accepts a Pydantic input model and returns a Pydantic output model. No global state. No direct DB calls from within a stage — the stage returns its result and the orchestrator handles persistence. This means any stage is unit-testable with no infrastructure.

**The rule is about writes and hidden state, not about reading.** Stage 2 (Candidate Retrieval) exists to query the graph and the vector store, and it does so through `GraphProvider`, `VectorProvider` and `EmbeddingProvider` handed in as parameters — exactly as Stages 0 and 1 take their language models. What the rule forbids is a stage reaching for a connection of its own, holding state between calls, or writing anything: persistence stays the orchestrator's, so replaying a stage can never change what is stored. A stage that reads through an injected Protocol is still swappable, still testable against a seeded store or a stand-in, and still has no idea which vendor is on the other side.

### Rule 3: Graph is append-only; queue is the write path
No component writes directly to the graph from outside the Graph Service. All graph writes go through the Graph Service API (or its module equivalent in the personal version). This creates one place to audit all writes.

**Precisely: no *content* field is ever modified.** Three bookkeeping operations do
change an existing node — `mark_superseded`, `record_reinforcement` and
`touch_person` — each touching a fixed set of counters, timestamps and version
status, with no caller-supplied field names. Nothing the user wrote is rewritten by
any of them. The exception is named and enumerated in `Graph/Schema.md` rather than
left implicit, because a rule with a hidden exception is worse than one with a
visible exception.

### Rule 4: Every inter-service call is schema-validated
Pydantic models are the contracts. Any call that crosses a service boundary (even within the personal monolith) validates its input against the schema. Schema mismatches are caught at the boundary, not deep inside a stage.

---

## 9. Local → Cloud Scaling Path

The extraction sequence from personal to production:

```
Phase 0 (Now): Local personal
  All services as Python modules in one FastAPI process
  Kuzu (embedded) + Qdrant (local) + SQLite
  RQ workers run in separate terminal
  Next.js dev server

Phase 1: Personal → packaged
  Docker Compose: FastAPI + RQ + Redis + Qdrant + Next.js
  Kuzu still embedded (sufficient for single user)
  Deploy to personal cloud VM (Hetzner CX22 = €4/month)

Phase 2: Multi-user alpha
  Add auth layer — own JWTs (EdDSA + JWKS), Google as identity provider [Goal 21]
  Each user gets their own Kuzu database and Qdrant collection,
    resolved from the authenticated identity through a store registry [Goal 22]
  Extract Graph Service (FastAPI) — swap Kuzu → Neo4j
  Extract Query Service (FastAPI)
  PostgreSQL replaces SQLite
  Qdrant Cloud replaces local Qdrant

Phase 3: Scale
  Pipeline Workers → Celery + Kafka
  Graph Service → Neo4j Aura
  Query Service → horizontal replicas behind load balancer
  Add CDN (Cloudflare) in front of Next.js
  Per-user CRITICAL tier: isolated Qdrant namespace, local processing option
```

At no phase does this require rewriting business logic. The pipeline stages, Pydantic schemas, and Provider Protocols are unchanged. Only the infrastructure configuration (AppConfig) changes.

---

## 10. Observability & Debug Strategy

### Pipeline Trace ID

Every session that enters the pipeline gets a `trace_id` (UUID). This trace_id is attached to:
- Every log line at every stage
- Every Pydantic model flowing through the pipeline (`PipelineDTO.trace_id`)
- Every operational-DB row the run produces (`pipeline_jobs`, `pipeline_stage_runs`, `pipeline_write_log`, `hitl_queue`)
- Every LLM call (as a metadata header)

**Graph and vector writes are traced by reference, not by column.** Node and edge
tables carry no `trace_id` column — the graph schema is purely semantic. Instead the
`pipeline_write_log` table records every `node_id` and every `(edge_type, from_id, to_id)`
a run wrote, keyed by `trace_id`. This gives both directions of the lookup:
`get_trace(trace_id)` returns everything a run produced, and `find_job_for_node(node_id)`
returns the run that created any given node. Storing the id on all 15 node and 44 edge
tables was rejected in Goal 3 as schema churn with no added capability.

Given any bug report, the trace_id lets you reconstruct the complete data flow: raw buffer → preprocessing result → extraction result → retrieval candidates → reconciliation decision → graph write. Every step, with timing and model output.

### Stage-Level Health Metrics

Each worker emits:
- `stage_duration_ms` — how long the stage took
- `model_used` — which provider handled it
- `validation_passed` — did the Pydantic schema validate
- `retry_count` — did it need re-extraction

Personal: structured JSON logs to file. Production: OpenTelemetry → Grafana stack.

### Decision Audit Trail (already in graph schema)

The `DecisionAuditNode` is the most powerful debug tool. It records:
- What was compared (new observation vs. candidate node)
- What was decided (MERGE / EVOLVE / BRANCH / etc.)
- What confidence the model had
- Which model made the decision
- A rollback pointer (how to undo this decision)

The Pipeline Debug View in the UI surfaces this as a visual timeline. Any incorrect reconciliation can be identified and reversed without touching any other part of the graph.

### Re-run Policy

Any failed or incorrect pipeline stage can be re-queued with the original input. The orchestrator supports:
- `rerun_from_stage(session_id, stage)` — re-process from a given stage forward
- `rerun_session(session_id)` — full re-processing with the current model configuration
- Invalidate + re-run reconciliation for specific nodes (without touching extraction)

**What is implemented today is the second one, and it is safe to repeat.** Calling
`run_pipeline` again on a session skips any episode whose record is already in the graph —
the whole episode, including reading and deciding it, not merely its saving. Skipping only
the saving would run Reconciliation against a graph that already holds its own previous
output and record the entry as a repeat of itself.

`rerun_from_stage` is not built yet. Every stage's input and output payload is already
recorded on `pipeline_stage_runs`, so it is a small addition when a caller needs it.

**Repairing an index gap.** The graph and the vector store cannot share a transaction, so
a record can commit to the graph and fail to reach the index. `repair_index(trace_id, …)`
recovers exactly those: `pipeline_write_log` records node writes and vector writes
separately, and the difference between the two lists is the repair set.

---

## 11. Open Technical Decisions

| # | Decision | Recommendation |
|---|---|---|
| 1 | **Kuzu vs. Neo4j from day 1?** | Start with Kuzu. Migrate when the first second user appears. The `GraphProvider` Protocol makes this zero-code-change. |
| 2 | **RQ vs. Celery for personal?** | RQ. It's 10× simpler. The `OrchestratorProvider` Protocol abstracts the queue. Celery is a 2-hour migration when needed. |
| 3 | **Voice-first or text-first UI?** | Text-first MVP. Voice input as progressive enhancement (Whisper.cpp local, browser MediaRecorder API). |
| 4 | **Offline-first frontend?** | Not for MVP. PWA with service worker caching is a Phase 1 addition. |
| 5 | ~~**Multi-user auth strategy?**~~ **Decided.** | ~~Clerk (turnkey, supports social login, good DX) for Phase 2.~~ **Withdrawn.** Lumen issues its own JWTs (EdDSA, published JWKS) and uses Google purely as an identity provider, with one Kuzu database and one Qdrant collection per user. Reasoning — including why not Clerk — is in [`Auth_Architecture.md`](file:///Users/hemangmishra/Projects/Lumen/docs/hld/Auth_Architecture.md); the build is Goals 21–22. The current personal build still has no auth: `AppConfig.user_id` is an env var and every request is the same person. |
