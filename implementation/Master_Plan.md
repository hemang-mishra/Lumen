# Lumen Master Implementation Plan

This document outlines the systematic, stage-by-stage implementation plan for the Lumen project, broken down into 20 distinct, testable goals. The architecture is prioritized to build from the ground up: Foundation → Extraction → Graph Testing → Query → Insights.

## Phase 1: Foundation & Databases (Goals 1-4)
**Objective:** Establish the data layer, schemas, and provider abstractions before touching any LLM logic.

- [ ] **Goal 1: Database Initialization Protocol**
  - Implement connection managers and local instances for Kuzu (Graph Store) and Qdrant (Vector Store).
  - *Test:* Successfully connect, initialize empty collections/tables, and perform basic read/write assertions.
- [ ] **Goal 2: Pydantic Schema Contracts**
  - Translate `Schema.md` and `pipeline.py` contracts into concrete Pydantic v2 models.
  - *Test:* Run unit tests to instantiate every node type and validate field constraints.
- [ ] **Goal 3: Operational DB & Task Queue Setup**
  - Initialize SQLite using SQLAlchemy for the Session Buffer and HITL Queue. Set up a local Python-RQ/Redis or simple async task queue.
  - *Test:* Enqueue a dummy task and successfully write/read a session record to SQLite.
- [ ] **Goal 4: LLM Provider Abstraction Layer**
  - Build the Provider Protocol for LLM and Embedding models (Standard/High-Security routing wrappers).
  - *Test:* Run a simple text prompt and embedding generation through the abstraction layer and receive a valid response.

## Phase 2: Extraction Pipeline (Goals 5-9)
**Objective:** Build the core pipeline that transforms raw conversational input into structured graph actions.

- [ ] **Goal 5: Stage 0 Preprocessing**
  - Implement raw entry ingestion, ASR artifact cleaning, and segmentation into `PreprocessedEpisode`.
  - *Test:* Feed a messy transcript; verify it outputs clean, segment-separated text blocks.
- [ ] **Goal 6: Stage 1 Microextraction Core**
  - Implement the LLM prompt and JSON mode parsing to extract `ObservationNode` instances from an episode.
  - *Test:* Feed a specific paragraph and verify it extracts the correct Belief, Pattern, or Event nodes.
- [ ] **Goal 7: Post-Extraction Validation Layer**
  - Implement the 5 Schema Validation Rules (e.g., Enum enforcement, chronological checks, CoT existence).
  - *Test:* Intentionally feed the validator broken/hallucinated JSON and verify it throws/catches the correct errors.
- [ ] **Goal 8: Stage 2 Retrieval (HyDE & Hybrid)**
  - Build the HyDE generation prompt and Qdrant hybrid search to find `CandidateNodes` for reconciliation.
  - *Test:* Seed Qdrant with 10 dummy nodes, run an observation through Stage 2, and verify the correct semantic matches are returned.
- [ ] **Goal 9: Stage 3 Reconciliation Logic**
  - Build the reconciliation LLM prompt that takes an extracted node and a list of candidates, returning a `ReconciliationResult` action (MERGE, EVOLVE, BRANCH, etc.).
  - *Test:* Provide an extracted node and an identical historical node; verify the LLM outputs a `MERGE` action.

## Phase 3: Graph Construction & E2E Testing (Goals 10-12)
**Objective:** Tie the extraction pipeline to the databases, execute full runs, and manually inspect the graph.

- [ ] **Goal 10: End-to-End Extraction Pipeline Harness**
  - Connect Stage 0 → Stage 1 → Stage 2 → Stage 3 and pipe the results into Kuzu and Qdrant.
  - *Test:* Run a full pipeline script on a single text file and verify the nodes and edges physically appear in Kuzu/Qdrant.
- [ ] **Goal 11: Graph Read/Debug APIs**
  - Implement basic traversal queries (e.g., fetch all nodes of type X, fetch causal chains).
  - *Test:* Programmatically traverse the graph to verify that `same-as` and `evolved-from` edges are correctly linked.
- [ ] **Goal 12: Multi-Session Integrity Test**
  - Feed 3–5 consecutive days of test logs through the system.
  - *Test:* Manually inspect the Kuzu graph to ensure patterns aren't fragmented and that reconciliation worked over time. (Extraction is now fully validated).

## Phase 4: Query Layer (Goals 13-16)
**Objective:** Build the real-time, invisible RAG injection system for conversational therapy.

- [ ] **Goal 13: Query Formulation Layer**
  - Build the lightweight LLM router that classifies user turns and generates a `RetrievalSignal`.
  - *Test:* Send "trivial" and "deep" chat turns; verify it outputs `NO_TRIGGER` and `PATTERN_MENTION` accordingly.
- [ ] **Goal 14: Parallel Retrieval Passes (A, B, C)**
  - Implement Semantic (A), Structural (B), and Continuity (C) passes against Kuzu and Qdrant.
  - *Test:* Trigger a `HISTORICAL_ERA` formulation; verify Pass B accurately retrieves nodes linked to that era.
- [ ] **Goal 15: Context Assembly & Pruning**
  - Merge candidates, apply the ranking formula, and compress them into a <400 token system prompt patch.
  - *Test:* Feed 10 candidate nodes; verify the assembler outputs a strictly truncated, highly-ranked summary block.
- [ ] **Goal 16: Conversational RAG Simulation**
  - Build a simple CLI chat loop integrating the Query Layer with the main AI response generation.
  - *Test:* Chat with the AI about a topic seeded in the graph and verify it invisibly references the graph context without breaking latency rules.

## Phase 5: Insights & Macro Layer (Goals 17-20)
**Objective:** Build the background processes that run asynchronously to summarize and maintain graph health.

- [ ] **Goal 17: Periodic Macroextraction Scripts**
  - Implement the weekly/monthly cron jobs that extract high-signal nodes and run Gemini Pro to generate `MacroextractionReportNode`s.
  - *Test:* Trigger a mock "end of week" event; verify a valid insight report is saved.
- [ ] **Goal 18: HITL Queue System**
  - Build the operational DB tables and simple endpoints for managing `AMBIGUOUS` and pending reflections.
  - *Test:* Force an `AMBIGUOUS` reconciliation, verify it lands in the queue, and resolve it manually.
- [ ] **Goal 19: Temporal Decay & Maintenance**
  - Implement the chronological decay weights on `last_reinforced_at` fields in Qdrant.
  - *Test:* Simulate a 400-day gap on a node and verify its retrieval score drops appropriately.
- [ ] **Goal 20: API Gateway (BFF) Integration**
  - Wrap all functioning subsystems into the unified FastAPI application defined in the HLD.
  - *Test:* Perform a full HTTP lifecycle: Ingest via POST, wait for processing, query the graph via GET, and initiate a WebSocket chat session.
