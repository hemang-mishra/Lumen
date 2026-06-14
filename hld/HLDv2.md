# High-Level Architecture v2: The Late Binding Model

Smriti is a personal wisdom system that converts unstructured voice and text journal entries into a richly connected, versioned knowledge graph representing the user's evolving beliefs, behavioral patterns, emotional observations, and open psychological investigations. Unlike retrieval-augmented diary apps, Smriti's core design principle is **Late Binding**: raw observations are extracted blindly (without history context), and only then are they reconciled against the existing graph — preventing anchoring bias during extraction while still enabling high-fidelity integration. The system is privacy-first, schema-enforced, and append-only at the content layer.

---

## Table of Contents

1. [Glossary](#glossary)
2. [Data Journey](#data-journey)
   - [Step 0: Preprocessing & Quality Gate](#step-0-preprocessing--quality-gate)
   - [Step 1: Blind Microextraction](#step-1-blind-microextraction)
   - [Step 2: Semantic Candidate Retrieval](#step-2-semantic-candidate-retrieval-hyde--hybrid-search)
   - [Step 3: Reconciliation & Decision Audit](#step-3-reconciliation--decision-audit)
   - [Step 4: Graph Write](#step-4-graph-write-append-only--temporal-model)
   - [Step 5: Query Layer](#step-5-query-layer-multi-hop-graphrag--counterfactual-retrieval)
   - [Step 6: Periodic Intelligence](#step-6-periodic-intelligence-macroextraction)
3. [Document Map](#document-map)
4. [Cost & Model Routing](#cost--model-routing)

---

## Glossary

| Term | Definition |
|---|---|
| **Episode** | A single conceptual unit extracted from a journal entry. An entry may segment into multiple episodes (e.g., a work conflict and a family interaction are two distinct episodes). |
| **Node** | A first-class vertex in the knowledge graph. Nodes are immutable once written; all changes produce new nodes or new edges. |
| **Knowledge Graph** | The persistent, versioned memory store of Smriti. A directed graph of typed nodes and edges, where edges represent reconciliation decisions. |
| **Late Binding** | The architectural principle that extraction (what did the user say?) is performed with zero history context. History is introduced only at the Reconciliation step. This prevents the LLM from shaping new observations to fit existing patterns. |
| **Anchoring Bias** | The cognitive and model-level failure mode where new evidence is systematically interpreted through the lens of what already exists, suppressing genuine novelty. Late Binding is the primary defense. |
| **Fragmentation** | The opposite failure: each entry creates isolated nodes that are never connected, producing a flat list instead of a knowledge graph. Reconciliation prevents fragmentation. |
| **Embeddings** | Dense vector representations of node content, used in semantic candidate retrieval. STANDARD/ELEVATED tiers use `text-embedding-004`; CRITICAL tier uses local models only. |
| **Causal Chain** | A sequence of linked observations or episodes in the graph that together explain how the user arrived at a current belief or pattern. Used in multi-hop GraphRAG queries. |
| **Sensitivity Tier** | A three-level classification (`STANDARD`, `ELEVATED`, `CRITICAL`) assigned to every extracted observation. CRITICAL tier mandates local-only LLM and embedding processing. |
| **Decision Audit Node** | A first-class graph node that records every Reconciliation action: which nodes were linked, the confidence score, the action type, the model used, and a rollback pointer. |
| **Temporal Decay** | The scoring mechanism by which older, unreinforced patterns receive lower retrieval weights (not deletion). Nodes with `last_reinforced_at > 365 days` receive a 0.5× weight multiplier. |
| **Same-As Edge** | The graph edge produced by a MERGE reconciliation action. It links a newly extracted node to a canonical historical node without deleting either. Provenance is fully preserved. |
| **Archetype Shift** | A detected change in a user's fundamental behavioral or cognitive archetype, typically surfaced during Macroextraction (Quarterly). Requires EVOLVE or BRANCH on a BeliefNode. |

---

## Data Journey

The full data journey for a single journal entry is a seven-step pipeline. Steps 0–4 are sequential per entry. Step 5 is on-demand (query). Step 6 is scheduled.

```
 Raw Input (Voice / Pasted Text)
        │
        ▼
 ┌─────────────────┐
 │  Step 0         │  Preprocessing & Quality Gate
 │  Preprocessing  │  ASR cleanup, completeness scoring, coreference pre-pass
 └────────┬────────┘
          │  REFLECTION or RAW_CAPTURE decision
          ▼
 ┌─────────────────┐
 │  Step 1         │  Blind Microextraction
 │  Microextraction│  Structured JSON extraction with NO history context
 └────────┬────────┘
          │  EpisodeNode + ObservationNodes (unlinked)
          ▼
 ┌─────────────────┐
 │  Step 2         │  Semantic Candidate Retrieval
 │  HyDE + Hybrid  │  Generates hypothetical doc, runs BM25 + vector search
 └────────┬────────┘
          │  Top-K candidate nodes from graph
          ▼
 ┌─────────────────┐
 │  Step 3         │  Reconciliation & Decision Audit
 │  Reconciliation │  6 actions: MERGE / REINFORCE / EVOLVE / BRANCH / CONTRADICT / AMBIGUOUS
 └────────┬────────┘
          │  Typed edges + DecisionAuditNode
          ▼
 ┌─────────────────┐
 │  Step 4         │  Graph Write
 │  Graph Write    │  Append-only node commit + edge writes
 └─────────────────┘
```

---

### Step 0: Preprocessing & Quality Gate

**What it does:** Cleans raw voice or pasted text input before any LLM extraction. Handles filler removal, self-correction detection, code-mixed language normalization, and entry completeness scoring. Routes entries to either the full extraction pipeline (`REFLECTION`) or minimal metadata capture (`RAW_CAPTURE`).

**Why it exists:** Real voice transcripts and pasted transcripts can contain fillers (`uh`, `um`, `like`), self-corrections (`wait no, I meant`), and mixed-language segments. Feeding these raw to an extraction LLM produces noisy, sometimes contradictory observations. Preprocessing ensures the extraction sees clean, coherent input.

**Key technical detail:** A coreference pre-pass runs on the full document before episode segmentation, producing a `coreference_map` JSON object that resolves intra-document pronoun and alias references. Cross-entry coreference is handled in Reconciliation.

**Detailed doc:** [Extraction/Preprocessing.md](../Extraction/Preprocessing.md)

---

### Step 1: Blind Microextraction

**What it does:** Given a preprocessed episode, calls the extraction LLM with **zero history context** to produce a structured JSON payload containing typed observations (from the strict enum taxonomy), entity references, emotional signals, and sensitivity tier assignments.

**Why it exists:** If the extraction model sees the user's existing graph, it will shape new observations to fit old patterns — suppressing genuine novelty and manufacturing false continuity. Blind extraction ensures raw observations are uncontaminated.

**Key technical detail:** Every extracted `ObservationNode` carries:
- `type` — from a closed enum (e.g., `SUPPRESSED_EMOTION_SURFACING`, `BEHAVIORAL_PATTERN_OBSERVATION`, `METACOGNITIVE_INTERRUPT`)
- `signal_strength` — `STANDARD | HIGH | CRITICAL`
- `sensitivity_tier` — `STANDARD | ELEVATED | CRITICAL`
- `content` — the extracted observation text
- `raw_evidence` — verbatim quote(s) from the episode

Schema validation runs post-extraction to enforce hard rules (e.g., `SUPPRESSED_EMOTION_SURFACING` must have `signal_strength = HIGH`). Violations trigger re-extraction, not silent override.

**Detailed doc:** [Extraction/Architecture.md](../Extraction/Architecture.md)

---

### Step 2: Semantic Candidate Retrieval (HyDE + Hybrid Search)

**What it does:** For each extracted observation, retrieves the top-K most semantically similar existing nodes from the knowledge graph. Uses **HyDE** (Hypothetical Document Embeddings) combined with **Hybrid Search** (BM25 sparse + vector dense retrieval).

**Why it exists:** Reconciliation needs a relevant comparison set. Naively embedding the raw observation and running nearest-neighbor retrieval misses exact-keyword matches and produces poor recall for short observations. HyDE generates a hypothetical full-form document describing what an existing node representing this observation *might* look like, improving embedding quality. BM25 adds lexical recall for named entities and specific terminology.

**Key technical detail:**
```
HyDE step:  observation_text → LLM → hypothetical_node_description
Embed step: hypothetical_node_description → embedding_model → query_vector
BM25 step:  observation_text → sparse retrieval over node text index
Fusion:     RRF(bm25_results, vector_results) → top-K candidates
```

Candidates are returned to the Reconciliation layer with similarity scores. The retrieval layer does **not** make decisions — it only surfaces options.

**Detailed doc:** [Extraction/Architecture.md](../Extraction/Architecture.md)

---

### Step 3: Reconciliation & Decision Audit

**What it does:** Given the newly extracted observation(s) and the top-K candidate nodes, the Reconciliation model selects one of six typed actions: **MERGE**, **REINFORCE**, **EVOLVE**, **BRANCH**, **CONTRADICT**, or **AMBIGUOUS**. Every action creates a `DecisionAuditNode` in the graph.

**Why it exists:** This is where fragmentation is prevented (MERGE/REINFORCE connects related nodes) and anchoring bias is prevented (BRANCH creates new nodes when the signal is genuinely novel). The decision is always auditable and reversible at the edge level.

**Key technical detail:** MERGE uses `same-as` edges — it does **not** collapse nodes. AMBIGUOUS is not a deferral of choice: it is a typed state that immediately escalates to the HITL review queue. EVOLVE requires a mandatory `delta_description` field or the response is rejected. CONTRADICT creates a `ContradictionNode` linking both beliefs without resolving either.

**Detailed doc:** [Extraction/Reconciliation.md](../Extraction/Reconciliation.md)

---

### Step 4: Graph Write (Append-Only + Temporal Model)

**What it does:** Commits the new nodes and edges produced by Steps 1–3 to the persistent knowledge graph store. All content nodes are immutable post-write. Edges may be invalidated (not deleted) via the Decision Audit Trail.

**Why it exists:** Append-only writes give the system complete provenance — you can always trace how any node was created and how any connection was made. This also makes rollback well-defined: invalidate the edge and re-queue affected nodes for re-evaluation.

**Key technical detail:**
- All nodes carry `created_at` and `valid_from` timestamps.
- `PatternNode` and `BeliefNode` carry `version` and `previous_version_id` for EVOLVE chains.
- All edges carry `valid_from`, `invalidated_at` (null if active), and `decision_id`.
- `last_reinforced_at` on PatternNode/BeliefNode drives temporal decay scoring.

**Detailed doc:** [Graph/Schema.md](../Graph/Schema.md)

---

### Step 5: Query Layer (Multi-Hop GraphRAG + Counterfactual Retrieval)

**What it does:** Serves natural-language queries over the knowledge graph using a two-phase retrieval strategy: (1) seed node retrieval via Hybrid Search, then (2) multi-hop graph traversal to surface causal chains, pattern clusters, and belief lineages. Supports counterfactual queries ("What would my patterns look like if I had resolved that open loop earlier?").

**Why it exists:** Flat vector search over a journal corpus returns isolated passages. Multi-hop traversal allows the system to answer questions like "Why do I keep avoiding direct confrontation?" by tracing a belief → pattern → episode → episode chain rather than returning a single matching quote.

**Key technical detail:** Counterfactual retrieval works by temporarily marking a target node as `invalidated` in a query-scoped graph snapshot, re-running the traversal, and comparing the result set. The original graph is never modified.

---

### Step 6: Periodic Intelligence (Macroextraction)

**What it does:** Scheduled synthesis jobs (Weekly / Monthly / Quarterly) that run across the full knowledge graph for the period, looking for emergent patterns, archetype shifts, unresolved contradictions, and closure signals on open loops.

**Why it exists:** Microextraction is per-episode; it cannot see trends across time. Macroextraction is the system's "zoomed out" perspective. It produces `MacroextractionReportNode` entries in the graph, which become queryable artifacts.

**Key technical detail:**

| Schedule | Scope | Output |
|---|---|---|
| Weekly | Last 7 days of episodes | Behavioral pattern delta, open loop status |
| Monthly | Last 30 days, cross-ref prior weekly reports | Belief drift, emotional trend summary |
| Quarterly | Full graph + all monthly reports | Archetype shift detection, long-term causal chains |

Macroextraction runs on **Gemini Pro** (or equivalent reasoning model). It is never run with CRITICAL-tier content unless a local model is used.

**Detailed doc:** [Extraction/Architecture.md](../Extraction/Architecture.md)

---

## Document Map

| File | What It Covers |
|---|---|
| `hld/HLDv2.md` *(this file)* | Master architecture overview, data journey, glossary, model routing |
| `Extraction/Architecture.md` | Microextraction schema, observation type enum, signal strength/sensitivity tier rules, HITL integration |
| `Extraction/Preprocessing.md` | Stage 0: ASR cleanup, completeness scoring, coreference pre-pass, quality gate routing |
| `Extraction/Reconciliation.md` | All 6 reconciliation actions, same-as edge model, CONTRADICT action, AMBIGUOUS escalation, HITL queue design, Decision Audit Trail, schema validation rules |
| `Graph/Schema.md` | All node and edge types with full YAML schemas, temporal model, retrieval score formula, version chain example, soft delete/erasure |

---

## Cost & Model Routing

Model selection is **not a configuration preference** — it is enforced at the schema layer based on `sensitivity_tier`. CRITICAL-tier content must never leave the local machine.

### Extraction (Microextraction)

| Sensitivity Tier | Model | Notes |
|---|---|---|
| `STANDARD` | Gemini Flash (structured JSON output mode) | Default for most observations |
| `ELEVATED` | Gemini Flash (structured JSON output mode) | Same model, stricter validation pass |
| `CRITICAL` | **Local only**: Llama 3.3 or Gemma 3 via Ollama | Non-negotiable. Code-enforced routing. |

### Reconciliation

| Action | Model | Rationale |
|---|---|---|
| `BRANCH`, `REINFORCE` | Gemini Flash | Low-risk actions; speed matters |
| `MERGE`, `EVOLVE`, `CONTRADICT` | Gemini Pro or reasoning model | High-consequence actions requiring nuanced judgment |
| Any action on `CRITICAL`-tier content | **Local only**: Llama 3.3 or Gemma 3 via Ollama | Tier overrides action-level routing |

### Embeddings

| Sensitivity Tier | Embedding Model |
|---|---|
| `STANDARD` | `text-embedding-004` |
| `ELEVATED` | `text-embedding-004` |
| `CRITICAL` | **Local only**: `nomic-embed-text` or `mxbai-embed` |

### Macroextraction

| Job | Model |
|---|---|
| Weekly / Monthly / Quarterly synthesis | Gemini Pro |
| Synthesis over any `CRITICAL`-tier nodes | **Local only**: Llama 3.3 or Gemma 3 via Ollama |

> ⚠️ **Implementation Rule:** The routing decision is made at the `sensitivity_tier` field of the highest-sensitivity ObservationNode in the episode. If any single observation in an episode is `CRITICAL`, the entire episode's processing pipeline (extraction, reconciliation, embeddings) routes to local models. This is enforced in code, not in prompts.
