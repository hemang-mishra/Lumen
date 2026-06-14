# Extraction Architecture: The Late Binding Model

*This document explains the full extraction pipeline. Start with [`hld/HLDv2.md`](../hld/HLDv2.md) for system overview. For step-by-step sub-schemas, see [`Preprocessing.md`](Preprocessing.md), [`Microextraction.md`](Microextraction.md), [`Reconciliation.md`](Reconciliation.md), [`Macroextraction.md`](Macroextraction.md).*

---

## The Core Dilemma

When converting a raw journal entry into structured insights, the pipeline faces two opposing failure modes:

1. **Anchoring Bias (too much context up front):** If the LLM sees a master list of all historical patterns and beliefs before reading today's entry, it stops reading faithfully. It force-fits nuanced new reflection into pre-existing boxes. A genuinely novel experience is collapsed into the nearest historical pattern, and real change is made invisible.

2. **Fragmentation (zero context):** If the LLM extracts in a complete vacuum every time, the same underlying truth accumulates as duplicate noise. Over a month: *"Comparison hurts"*, *"Comparing myself to others is bad"*, and *"Social comparison causes sadness"* become three separate entities. The graph grows noisy; the pattern is never identified.

**The solution is Late Binding** — waiting as long as possible before introducing historical context. Extract first, in isolation. Then reconcile, with full history available. The boundary between these two phases is the key architectural decision in Smriti.

---

## Full Pipeline Overview

```
Stage 0: Preprocessing
  └── ASR normalization, filler removal, code-mix handling,
      entry completeness scoring, quality gate, entry type classification
        │
        ▼
Stage 1: Microextraction  (blind — no history)
  └── Coreference map, episode segmentation, observations array,
      causal mechanisms array, sensitivity_tier, signal_strength
        │
        ▼
Stage 2: Candidate Retrieval
  └── HyDE expansion → Hybrid BM25 + Vector search →
      Top 3–5 historical candidates per extracted node
        │
        ▼
Stage 3: Reconciliation
  └── LLM decision per node: MERGE | REINFORCE | EVOLVE | BRANCH | CONTRADICT | AMBIGUOUS
      Confidence thresholds enforced. Sub-threshold → HITL queue.
        │
        ▼
Stage 4: Graph Write
  └── Append-only node/edge writes. Decision Audit Nodes persisted.
      active_embedding_version maintained. Sensitivity routing applied.
```

| Stage | Parallelism-safe? | Primary Doc |
|---|---|---|
| Stage 0 — Preprocessing | ✅ Yes | [`Preprocessing.md`](Preprocessing.md) |
| Stage 1 — Microextraction | ✅ Yes | [`Microextraction.md`](Microextraction.md) |
| Stage 2 — Candidate Retrieval | ✅ Yes | (see below) |
| Stage 3 — Reconciliation | ⚠️ No — see serialization rules | [`Reconciliation.md`](Reconciliation.md) |
| Stage 4 — Graph Write | ⚠️ No — see serialization rules | [`Reconciliation.md`](Reconciliation.md) |

**Stage 0 summary:** Normalizes raw voice or text input. Runs ASR correction, removes fillers, scores entry completeness, applies a quality gate (entries below threshold are held for HITL review rather than processed), and classifies the entry as `REFLECTION` or `RAW_CAPTURE`. See [`Preprocessing.md`](Preprocessing.md).

**Stage 1 summary:** Produces a structured extraction of the entry in complete isolation from historical context. Outputs a coreference map, episode segments, an `observations` array, and a `causal_mechanisms` array. See [`Microextraction.md`](Microextraction.md).

**Stage 2 summary:** Takes each extracted node and queries the vector database. Uses HyDE (Hypothetical Document Embedding) to generate a synthetic "ideal historical match" for embedding, then runs Hybrid Search (BM25 + cosine similarity) to retrieve the top 3–5 closest historical nodes. This is the first moment history enters the pipeline.

**Stage 3 summary:** A second LLM call receives both the new extraction and the historical candidates. It outputs one of six structured decisions per node: `MERGE`, `REINFORCE`, `EVOLVE`, `BRANCH`, `CONTRADICT`, or `AMBIGUOUS`. Each action has a per-action confidence threshold; sub-threshold decisions route to HITL. See [`Reconciliation.md`](Reconciliation.md).

**Stage 4 summary:** Executes the Reconciliation decisions as graph writes. Content nodes are append-only and immutable. Decision Audit Nodes are first-class, reversible graph nodes. See [`Reconciliation.md`](Reconciliation.md).

---

## Async Batching & Write Safety

Stages 0 and 1 carry no dependency on global state. They are safe for parallel async batching — multiple entries can be processed simultaneously without coordination.

Stages 3 and 4 are **not safe for naive parallelism.** Reconciliation reads and writes the same graph nodes. Two concurrent jobs that independently identify the same novel pattern as `BRANCH` will create a duplicate. The second job must re-run Reconciliation against the now-existing node.

### Write-Serialization Queue

For `BRANCH` actions produced by concurrent jobs, a **write-serialization queue** is applied per **semantic cluster**.

**Semantic cluster definition:** A cluster is the set of nodes sharing the same `enum type` AND whose embeddings have at least 2 of 3 top nearest neighbors in common in embedding space. Cluster granularity is intentionally coarse — it is designed to prevent false collisions more than to achieve perfect precision. Occasional over-serialization (jobs that didn't actually conflict being queued together) is acceptable and cheap.

**BRANCH serialization rule:** Before committing a `BRANCH` write, the job checks whether any pending write in the same semantic cluster is already in the queue. If yes, the newer job's Reconciliation decision is paused until the first write commits. After commit, the paused job re-runs Stage 3 (Reconciliation only — no re-extraction required) against the newly committed node.

**Daily deduplication pass:** A background pass runs daily across all nodes, grouping by `enum type` and comparing pairwise cosine similarity. Any pair with similarity ≥ 0.95 and no existing `same-as` edge is surfaced to the HITL Review Queue as a candidate `MERGE`. This catches any duplicates that slipped through concurrent writes.

---

## Quarterly Re-Embedding (with Migration Safety)

HyDE combats semantic drift at query time, but does nothing for frozen historical embeddings. Over years, the same psychological pattern described with completely different vocabulary — *"show off"* at age 22 versus *"seeking external validation"* at age 26 — will become invisible to each other in the vector space, because both descriptions are frozen under the model weights of the year they were written.

**Solution: Quarterly re-embedding.** Every node stores the embedding from each model version alongside the active version pointer:

```yaml
node:
  id: pat_001
  content: "Seeking external validation through social comparison"
  embedding_v1: [...]       # Model: nomic-embed-text, 2026-Q1
  embedding_v2: [...]       # Model: mxbai-embed-large, 2027-Q1 (re-run quarterly)
  active_embedding_version: v2
```

### Migration Safety Protocol

⚠️ A partial migration — where some nodes have been re-embedded under v2 and others remain on v1 — must never be exposed to the retrieval layer. Mixed-version retrieval produces incorrect similarity rankings.

**Migration steps:**
1. **Freeze:** Set `active_embedding_version` to `v1` system-wide (read-only migration lock). The retrieval layer uses `v1` for all nodes during migration.
2. **Canary pass:** Re-embed one week of entries under the new model. Validate retrieval quality metrics (recall@5, MRR) against a held-out query set. If metrics degrade, abort and investigate before proceeding.
3. **Full pass:** Re-embed all remaining nodes sequentially. Store results in `embedding_v2` without touching `active_embedding_version`.
4. **Atomic cutover:** In a single transaction, update `active_embedding_version` to `v2` system-wide. The retrieval layer now uses `v2` for all nodes.
5. **Validation:** Re-run the held-out query set against the v2 retrieval layer. Confirm metrics meet or exceed v1 baseline.

**During migration:** Retrieval falls back to v1 for all nodes until the atomic cutover in step 4 completes. No partially migrated state is ever visible.

---

## Validation Layer (Schema Enforcement)

Post-extraction validation runs on every Microextraction output **before any graph write is attempted.** This is a code-level constraint, not a prompt suggestion.

The full set of validation rules is defined in [`Reconciliation.md`](Reconciliation.md). Key rules enforced at this layer:

1. Every observation must have a `type` from the fixed Enum Dictionary (unknown types are rejected).
2. Every observation must have a `sensitivity_tier` (`STANDARD` | `ELEVATED` | `CRITICAL`). A missing tier is a hard validation failure.
3. Every observation must have an `extraction_signal_strength` (`STANDARD` | `HIGH` | `CRITICAL`). A missing value is a hard failure.
4. Types with mandatory tier floors must not violate them: `INAUTHENTICITY_STATE` and identity-related `ACCEPTANCE_ACKNOWLEDGEMENT` must be `CRITICAL`; `SUPPRESSED_EMOTION_SURFACING` and `METACOGNITIVE_INTERRUPT` must have `extraction_signal_strength: HIGH`.
5. Causal chain `type` values must be from the set `{TRIGGER, INTERNAL_STATE, ACTION, OUTCOME, LESSON}`. Unknown step types are rejected.

**On validation failure:**
- Re-extraction is attempted once, with a correction prompt that names the violated rule and the offending field.
- If re-extraction also fails validation: the entry is flagged, no graph write occurs, and the entry is routed to the HITL Review Queue with the violation attached.

---

## Sensitivity Classification

Every extracted observation carries a `sensitivity_tier` set by the Microextraction LLM. Certain enum types carry mandatory tier floors enforced at the schema level — the LLM output is validated against these floors and rejected if violated.

| Tier | LLM for Extraction | Embedding Model | Storage | Backup | RAG Inclusion | Notifications |
|---|---|---|---|---|---|---|
| `STANDARD` | Any cloud LLM (e.g., Gemini Flash) | Cloud API (e.g., `text-embedding-004`) | Local SQLite + cloud-synced vector store | Encrypted cloud backup allowed | Automatic | Allowed |
| `ELEVATED` | Cloud LLM acceptable; flag for sensitivity | Cloud API, no content logged server-side | Local only; no cloud vector store | Local encrypted backup only | Automatic, no direct quotes | No previews |
| `CRITICAL` | **Local LLM only** (e.g., Llama 3.3 8B via Ollama) | **Local embedding only** (e.g., `nomic-embed-text`) | **Local only; isolated vector partition** | **Disabled by default** | **Only on explicit user request** | **Never** |

**Implementation rule:** The Microextraction LLM must assess and output `sensitivity_tier` for every observation. When in doubt, the LLM must default to the *higher* tier. This cannot be post-hoc defaulted. See [`Privacy/Architecture.md`](../Privacy/Architecture.md) for full privacy routing details.

---

## Extraction Priority Scoring

Not all observations carry equal retrieval weight. The pipeline applies a priority multiplier at the vector store level.

### Signal Weight Multipliers

| `extraction_signal_strength` | Multiplier | Set when |
|---|---|---|
| `STANDARD` | 1.0× | Default for all observations |
| `HIGH` | 1.5× | `SUPPRESSED_EMOTION_SURFACING`, `METACOGNITIVE_INTERRUPT`, any observation with an involuntary somatic marker |
| `CRITICAL` | 2.0× | Explicitly set by LLM for exceptional signal: major identity breakthrough, life-defining realization |

### Final Score Formula

At retrieval time (Stage 2 and query layer), the final similarity score for a candidate node is:

```
final_score = cosine_similarity × signal_weight_multiplier × recency_weight
```

### Recency Weight Decay

| Age of observation | `recency_weight` |
|---|---|
| < 30 days | 1.00 |
| 30–179 days | 0.85 |
| 180–364 days | 0.70 |
| ≥ 365 days | 0.50 |

Recency decay ensures that the retrieval layer reflects how the user currently is, not just what has the most historical volume. Older patterns are not deleted — they are reachable, but ranked lower unless explicitly queried.

---

## Person Entity Lifecycle

Person Entity nodes are a first-class node type in the knowledge graph. Every named person mentioned across any entry resolves to a canonical Person Entity node, enabling cross-episode relationship tracking and protecting against alias fragmentation.

**Discovery:** Person entities are first discovered during the Preprocessing coreference pass (Stage 0), which produces a within-document alias map. The Microextraction LLM consumes this pre-computed map rather than re-deriving it.

**Cross-entry alias resolution:** Within a single entry, coreference is handled in Stage 0. Across entries, alias resolution (e.g., *"my mentor"* in one entry → *"Aditya"* in another) is handled by the Reconciliation layer using the same `MERGE`-style logic as pattern merging — specifically by creating `same-as` edges between the alias node and the canonical Person Entity node. See [`Microextraction.md`](Microextraction.md) for the within-document coreference schema, and [`Reconciliation.md`](Reconciliation.md) for the cross-entry person resolution mechanism.

**Sensitivity inheritance:** A Person Entity node's `sensitivity_tier` is always the maximum tier of any observation it is linked to. If any linked observation is `CRITICAL`, the Person Entity node itself becomes `CRITICAL` and is subject to all CRITICAL-tier routing rules.

```yaml
person_entity:
  id: person_aditya_001
  canonical_name: "Aditya"
  aliases: ["Adit", "my mentor"]
  relationship_role: "Corporate mentor"
  first_mentioned: "2026-06-12"
  sensitivity_tier: ELEVATED       # Inherits max tier of linked observations
  linked_episodes: [ep_2026_06_12_mentor]
  linked_observation_types: [RELATIONAL_DYNAMIC, GRATITUDE_APPRECIATION, SOCIAL_PERFORMANCE_STATE]
```
