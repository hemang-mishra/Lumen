# Smriti

Smriti is a personal wisdom system that converts unstructured voice and text journal entries into a richly connected, versioned knowledge graph. It is designed to solve "Personal Knowledge Decay" by extracting structured observations from messy human experiences.

## Core Architectural Principles
1. **Late Binding:** Observations are extracted blindly (zero history context) to prevent anchoring bias. History is only introduced during Reconciliation.
2. **Append-Only:** Content nodes are immutable. Shifts in beliefs produce new nodes; errors in linking produce reversible edges.
3. **Privacy-First (Tiered Routing):** Standard content uses cloud APIs; sensitive content (`CRITICAL`) uses local LLMs and local embeddings exclusively.

---

## Document Map

The system architecture is fully defined in the following documents. Start with `HLDv2.md`.

### 1. High-Level Architecture
- **[hld/HLDv2.md](hld/HLDv2.md):** The master entry-point. Covers the 7-step data journey, glossary, and model routing.
- **[ROADMAP.md](ROADMAP.md):** The 5-phase build sequence and known technical risks.

### 2. The Extraction Pipeline
- **[Extraction/Architecture.md](Extraction/Architecture.md):** Overview of the pipeline, async write safety, and quarterly re-embedding.
- **[Extraction/Preprocessing.md](Extraction/Preprocessing.md):** Stage 0. Voice ASR cleanup, code-mixing normalization, and quality gates.
- **[Extraction/Microextraction.md](Extraction/Microextraction.md):** Stage 1. Blind extraction schema, strict enum taxonomy, and entry-type routing.
- **[Extraction/Reconciliation.md](Extraction/Reconciliation.md):** Stage 3. The 6 reconciliation actions (`MERGE`, `REINFORCE`, `EVOLVE`, `BRANCH`, `CONTRADICT`, `AMBIGUOUS`), same-as edges, and the HITL Review Queue.
- **[Extraction/Macroextraction.md](Extraction/Macroextraction.md):** Stage 6. Periodic intelligence (weekly/monthly/quarterly), Temporal Decay, Archetype Shifts, and Proof Chains.

### 3. Graph & Query Layers
- **[Graph/Schema.md](Graph/Schema.md):** The immutable knowledge graph schema, node types, edge types, and temporal retrieval scoring.
- **[Query/RAGArchitecture.md](Query/RAGArchitecture.md):** Multi-hop GraphRAG, counterfactual retrieval, and the Reflection Prompt Engine.

### 4. Privacy & Safety
- **[Privacy/Architecture.md](Privacy/Architecture.md):** The 3-tier LLM routing model, local model requirements for `CRITICAL` data, output scrubbing, and DPDP compliance.