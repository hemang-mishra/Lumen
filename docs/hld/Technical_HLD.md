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

| Role | Used By | Default Provider / Model |
|---|---|---|
| `LIGHTWEIGHT` | Quality-gate scoring, low-risk Reconciliation actions (MERGE/REINFORCE/BRANCH/REGULATE), Query Formulation turn classification, HyDE expansion | `gemini` / `gemini-2.5-flash` |
| `THINKING` | High-consequence Reconciliation actions (EVOLVE/CONTRADICT/DIALECTIC), Macroextraction synthesis | `gemini` / `gemini-2.5-pro` |
| `EMBEDDING` | Dense vector generation for the Vector Store | `gemini` / `text-embedding-004` |
| `TRANSCRIPTION` | Voice-note speech-to-text | `whisper_cpp` / `base.en` |
| `TTS` | Text-to-speech | `macos` / `default` |

An operator who wants every AI call to run locally (for privacy or offline use)
reconfigures all five roles to local providers (e.g. `ollama` for `LIGHTWEIGHT`/`THINKING`,
`ollama` for `EMBEDDING`) — a one-time deployment choice, not a runtime routing decision
the pipeline makes per piece of content.

### 2.8 Audio (STT / TTS)

| Direction | Personal | Production |
|---|---|---|
| Speech → Text | `whisper.cpp` (local, fast, free) | `Deepgram` or `Assembly AI` (cloud) |
| Text → Speech | macOS system neural voices | `ElevenLabs` or `OpenAI TTS` |

Both behind `AudioTranscriptionProvider` and `TTSProvider` Protocols (already defined),
configured via the same `ProviderConfig` `TRANSCRIPTION`/`TTS` roles described in §2.7.

---

## 3. Service Decomposition

The personal version runs all services as Python modules in a single process. The production version extracts each into its own Docker container + replica set. The service boundaries are drawn today so extraction is mechanical, not architectural.

### 3.1 Service Registry

| Service | Responsibility | Personal | Production |
|---|---|---|---|
| **BFF / API Gateway** | Single entry point for all client requests, auth, rate limiting | FastAPI process on port 8000 | FastAPI + Nginx + TLS |
| **Ingestion Service** | Receive messages, voice uploads, external log imports → write to Session Buffer | Module in BFF | Separate FastAPI service |
| **Pipeline Orchestrator** | Watch Session Buffer for decayed sessions → dispatch pipeline jobs | Background thread in BFF | Dedicated Celery beat scheduler |
| **Extraction Worker** | Steps 0 + 1 (Preprocessing + Microextraction) | Python-RQ worker | Celery worker, N replicas |
| **Retrieval Service** | Step 2 (HyDE + Hybrid Search) | Function call in Extraction Worker | Separate FastAPI service (CPU-bound, scale independently) |
| **Reconciliation Worker** | Step 3 (Reconciliation decisions + HITL escalation) | Python-RQ worker | Celery worker, separate queue from Extraction |
| **Graph Service** | Step 4 + all graph reads | Module in BFF | Separate FastAPI service with connection pool |
| **Query Service** | Step 5 (GraphRAG + Conversational RAG Mode) | Module in BFF | Separate FastAPI service |
| **HITL Service** | Review queue management, one-tap decisions | Module in BFF | Separate FastAPI service |
| **Scheduler** | Trigger Macroextraction jobs on schedule | APScheduler in BFF | Kubernetes CronJob |
| **Formulation Service** | Query Formulation Layer (Conversational RAG) — classifies turn, emits RetrievalSignal | Async function in Query Service | Sidecar in Query Service |

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
│  session_buffer: raw messages pending extraction        │
│  pipeline_jobs: task state, retries, errors             │
│  hitl_queue: AMBIGUOUS decisions pending human review   │
│  user_settings: provider config, sensitivity prefs      │
│  api_keys: encrypted provider credentials               │
│  Access pattern: standard CRUD, status polling          │
└─────────────────────────────────────────────────────────┘
```

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
    message_count: int
    raw_buffer: list[BufferMessage]
    triggered_at: datetime

class PreprocessingResult(BaseModel):
    session_id: str
    episodes: list[PreprocessedEpisode]
    coreference_map: dict
    quality_gate_decision: Literal["REFLECTION", "RAW_CAPTURE", "DISCARD"]
    processing_time_ms: int

class ExtractionResult(BaseModel):
    episode_id: str
    observations: list[ObservationNode]
    extraction_model: str
    validation_passed: bool
    retry_count: int

class RetrievalResult(BaseModel):
    source_node_id: str  # ObservationNode | EventNode | SessionNode
    pass_a_candidates: list[CandidateNode]  # semantic
    pass_b_candidates: list[CandidateNode]  # structural
    retrieval_time_ms: int

class ReconciliationResult(BaseModel):
    source_node_id: str  # ObservationNode | EventNode | SessionNode
    action: ReconciliationAction
    target_node_id: str | None
    confidence: float
    delta_description: str | None          # mandatory for EVOLVE
    decision_model: str
    escalated_to_hitl: bool
    audit_node_id: str
```

Each worker accepts an input model and emits an output model. The orchestrator is the only component that chains them. This means any stage can be tested in complete isolation by constructing its input model and asserting its output model — no real DB, no real LLM required.

---

## 6. Conversational RAG Integration

The Query Service contains the Conversational RAG Mode (from `Query/Conversational_RAG_Mode.md`). Here is how it maps to code:

```
User turn arrives (WebSocket message)
        │
        ▼
  FormulationService.classify(turn)        ← gemini-2.5-flash, <100ms
        │
        ├─ NO_TRIGGER → pass to AI immediately (no wait)
        │
        └─ TRIGGER → dispatch retrieval (async, 3s budget)
               │
               ├─ PassA: qdrant.hybrid_search(hyde_expansion)
               ├─ PassB: graph.anchor_lookup(named_entities, historical_era)
               └─ PassC: session_buffer.get_relevant(session_context_buffer)
                         │
                         ▼
                  ContextAssembler.rank_and_compress(candidates, max_tokens=400)
                         │
                         ▼
                  SystemPromptPatcher.inject(ai_context, compressed_context)
                         │
                         ▼
                  AI generates response (streaming)
```

The `SessionContextBuffer` lives in memory per-session (Zustand on frontend, Python dict in Query Service). It is NOT persisted to the graph — it is ephemeral per calendar day.

---

## 7. Frontend Architecture

### 7.1 Page Structure (Next.js App Router)

```
app/
├── (auth)/
│   └── login/
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

### 7.3 Real-time Updates

WebSocket connection maintained for the session. The FastAPI backend pushes:
- AI response tokens (streaming)
- Pipeline stage completion events (`extraction_complete`, `reconciliation_complete`)
- HITL queue count changes
- End-of-day nudge

---

## 8. Modularity Rules (Non-Negotiable)

These rules are the architectural contract that keeps the codebase debuggable and scalable:

### Rule 1: Providers are always Protocols
No direct imports of `google.generativeai`, `openai`, `kuzu`, or `qdrant_client` outside their `providers/` module. Every call goes through a Protocol. Business logic has zero knowledge of vendor SDKs.

### Rule 2: Pipeline stages are pure functions
Each stage function accepts a Pydantic input model and returns a Pydantic output model. No global state. No direct DB calls from within a stage — the stage returns its result and the orchestrator handles persistence. This means any stage is unit-testable with no infrastructure.

### Rule 3: Graph is append-only; queue is the write path
No component writes directly to the graph from outside the Graph Service. All graph writes go through the Graph Service API (or its module equivalent in the personal version). This creates one place to audit all writes.

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
  Extract Graph Service (FastAPI) — swap Kuzu → Neo4j
  Extract Query Service (FastAPI)
  Add auth layer (Clerk or custom JWT)
  PostgreSQL replaces SQLite
  Qdrant Cloud replaces local Qdrant
  Each user gets isolated graph namespace (user_id prefix on node_ids)

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
- Every Pydantic model flowing through the pipeline
- Every graph node and edge written as a result
- Every Qdrant write
- Every LLM call (as a metadata header)

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

---

## 11. Open Technical Decisions

| # | Decision | Recommendation |
|---|---|---|
| 1 | **Kuzu vs. Neo4j from day 1?** | Start with Kuzu. Migrate when the first second user appears. The `GraphProvider` Protocol makes this zero-code-change. |
| 2 | **RQ vs. Celery for personal?** | RQ. It's 10× simpler. The `OrchestratorProvider` Protocol abstracts the queue. Celery is a 2-hour migration when needed. |
| 3 | **Voice-first or text-first UI?** | Text-first MVP. Voice input as progressive enhancement (Whisper.cpp local, browser MediaRecorder API). |
| 4 | **Offline-first frontend?** | Not for MVP. PWA with service worker caching is a Phase 1 addition. |
| 5 | **Multi-user auth strategy?** | Clerk (turnkey, supports social login, good DX) for Phase 2. Current personal build has no auth — local-only access. |
