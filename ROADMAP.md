# Implementation Roadmap

Smriti is currently an architecture-stage project. This roadmap defines the build sequence, ordered by dependency and technical risk.

## Phase 0 — Foundation (Before Any ML/LLM Work)
1. Choose graph database and write `Schema.md` nodes as actual DB schema.
   - *Recommendation:* SQLite + Kuzu for local-first simplicity, or Neo4j for richer graph queries.
2. Implement schema validation layer as code (the 5 hard validation rules from `Reconciliation.md`).
3. Write test harness: given a raw journal entry, does Microextraction output match expected observations? 
   - *Test cases:* Use 11th June and 12th June entries.
4. Design the entry storage model (how are raw entries stored before processing?).

## Phase 1 — MVP Extraction Pipeline
1. Build **Stage 0 (Preprocessing)**: ASR normalization, filler removal, entry completeness scoring, quality gate routing.
2. Implement **Microextraction** with Gemini Flash + JSON Schema enforcement.
3. Implement validation layer post-extraction.
4. Implement ChromaDB vector store + embedding pipeline (`STANDARD`/`ELEVATED` via `text-embedding-004`).
5. Implement HyDE + Hybrid BM25+Vector candidate retrieval (Step 2).
6. Implement **Reconciliation** with the first 4 actions (`MERGE` as same-as edge, `REINFORCE`, `EVOLVE`, `BRANCH`).
7. Implement HITL Review Queue UI (mobile-first, one-tap).
8. Integrate `CONTRADICT` and `AMBIGUOUS` actions.

## Phase 2 — Privacy & Safety
1. Set up Ollama local LLM for `CRITICAL`-tier extraction.
2. Set up local embedding model for `CRITICAL`-tier embeddings.
3. Implement output-layer scrubbing pass.
4. Implement AES-256 encryption for local database files.
5. Implement input sanitization / prompt injection protection.
6. Implement soft-delete anonymization for DPDP/GDPR compliance.

## Phase 3 — Query Layer
1. Implement basic vector RAG query interface.
2. Add multi-hop GraphRAG traversal.
3. Implement counterfactual retrieval.
4. Build Reflection Prompt Engine.
5. Add query feedback loop logging.

## Phase 4 — Macroextraction & Periodic Intelligence
1. Implement weekly Macroextraction pass.
2. Implement monthly Macroextraction pass.
3. Add Archetype Shift detection.
4. Add Contradiction tracking in reports.
5. Add Emotional Valence time-series.
6. Add Proof Chain generation.
7. Implement Prospective Memory signal generation.

## Phase 5 — Advanced Features
1. Quarterly re-embedding with atomic migration.
2. Temporal decay model in retrieval.
3. Adaptive Enum Extension (quarantine + graduate process).
4. `PROSODY_SIGNAL` extraction from voice audio (paralinguistic model).
5. Cross-user anonymized pattern library (differential privacy, opt-in).

## Known Technical Risks

| Risk | Severity | Mitigation |
|---|---|---|
| LLM inconsistency in enum type assignment | High | Validation layer + re-extraction with correction prompt |
| Write race conditions in async BRANCH | High | Write-serialization queue + daily dedup pass |
| Vocabulary drift over years | Medium | Quarterly re-embedding with atomic migration |
| HITL queue abandonment | Medium | Hard cap of 20 items + suspension instead of auto-BRANCH |
| Cross-version embedding incompatibility | High | Freeze active_version during migration |
| CRITICAL content in output layer | High | Output scrubbing pass on all generated responses |
