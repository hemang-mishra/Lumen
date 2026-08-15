# Extraction Architecture: The Late Binding Model

*This document explains the full extraction pipeline. Start with [`hld/HLDv2.md`](../hld/HLDv2.md) for system overview. For step-by-step sub-schemas, see [`Preprocessing.md`](Preprocessing.md), [`Microextraction.md`](Microextraction.md), [`Reconciliation.md`](Reconciliation.md), [`Macroextraction.md`](Macroextraction.md).*

---

## The Core Dilemma

When converting a raw journal entry into structured insights, the pipeline faces two opposing failure modes:

1. **Anchoring Bias (too much context up front):** If the LLM sees a master list of all historical patterns and beliefs before reading today's entry, it stops reading faithfully. It force-fits nuanced new reflection into pre-existing boxes. A genuinely novel experience is collapsed into the nearest historical pattern, and real change is made invisible.

2. **Fragmentation (zero context):** If the LLM extracts in a complete vacuum every time, the same underlying truth accumulates as duplicate noise. Over a month: *"Comparison hurts"*, *"Comparing myself to others is bad"*, and *"Social comparison causes sadness"* become three separate entities. The graph grows noisy; the pattern is never identified.

**The solution is Late Binding** — waiting as long as possible before introducing historical context. Extract first, in isolation. Then reconcile, with full history available. The boundary between these two phases is the key architectural decision in Lumen.

---

## Full Pipeline Overview

```
Stage 0: Preprocessing
  └── ASR normalization, filler removal, code-mix handling,
      entry completeness scoring, quality gate, entry type classification,
      coreference map, episode segmentation
        │
        ▼
Stage 0.5: Session-Level Rollups (Conversational only)
  └── Summarizes final settled conclusions from multi-turn dialogue,
      discards exploratory scaffolding, outputs Session Summary
        │
        ▼
Stage 1: Microextraction  (blind — no history)
  └── Observations array, causal mechanisms array, signal_strength,
      provenance, extraction_confidence
        │
        ▼
Stage 2: Candidate Retrieval  (two parallel passes — results merged before Stage 3)
  ├── Pass A — Semantic Retrieval:
  │     HyDE expansion → Hybrid BM25 + Vector search →
  │     Top 3–5 closest historical candidates per extracted node
  └── Pass B — Structural Retrieval:
        Named-entity anchors + node-type filters → Graph-keyed lookup →
        All active high-sensitivity nodes linked to mentioned persons/eras
        │
        ▼
Stage 3: Reconciliation
  └── LLM decision per node: MERGE | REINFORCE | EVOLVE | BRANCH | CONTRADICT | DIALECTIC | REGULATE | AMBIGUOUS
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
| Stage 0.5 — Session-Level Rollups | ✅ Yes | [`Preprocessing.md`](Preprocessing.md) |
| Stage 1 — Microextraction | ✅ Yes | [`Microextraction.md`](Microextraction.md) |
| Stage 2 — Candidate Retrieval | ✅ Yes | (see below) |
| Stage 3 — Reconciliation | ⚠️ No — see serialization rules | [`Reconciliation.md`](Reconciliation.md) |
| Stage 4 — Graph Write | ⚠️ No — see serialization rules | [`Reconciliation.md`](Reconciliation.md) |

**Stage 0 summary:** Normalizes raw voice or text input. Runs ASR correction, removes fillers, translates non-English spans, scores completeness, segments the entry into conceptual episodes, and builds the coreference map. Applies a quality gate that classifies each episode as `REFLECTION` or `RAW_CAPTURE`; sub-threshold episodes are routed to `RAW_CAPTURE` (minimal capture plus reflection prompts), **not** held for human review. The only input that is thrown away is input with nothing extractable left in it — see `DISCARD` in [`Preprocessing.md`](Preprocessing.md).

**Stage 0.5 summary:** (For conversational data only). Intercepts raw multi-turn dialogue to extract settled conclusions (`REALIZATION`s) while discarding intermediate hypotheses and exploratory scaffolding. Outputs a clean Session Summary to prevent intra-session fragmentation in the graph. See [`Preprocessing.md`](Preprocessing.md).

**Stage 1 summary:** Produces a structured extraction of one preprocessed episode in complete isolation from historical context. Outputs an `observations` array and a `causal_mechanisms` array. The coreference map, the adopted-framing spans, and the episode boundaries arrive from Stage 0 as inputs; Stage 1 consumes them and does not re-derive them. See [`Microextraction.md`](Microextraction.md).

**Causal anchor:** Stage 1 also mints exactly one `SessionNode` per `REFLECTION` episode, in code rather than by asking the model. The Bipartite Causal Graph rule forbids a `BeliefNode` or `PatternNode` from evolving without an intervening `EventNode` or `SessionNode`, so Reconciliation must always have something to anchor against; making that anchor's existence depend on a model's judgement about what counts as an event would turn a structural guarantee into a probabilistic one. `EventNode`s are still extracted from content wherever the person describes something that actually happened — an event that anchors a shift and the session in which the shift occurred are different claims, and Reconciliation chooses between them. `RAW_CAPTURE` episodes get no anchor, since they never reach Reconciliation.

**Stage 2 summary:** Takes each extracted node and runs **two parallel retrieval passes** whose results are merged into a single candidate set before Reconciliation.

- **Pass A — Semantic Retrieval:** Uses HyDE (Hypothetical Document Embedding) to generate a synthetic "ideal historical match" for embedding, then runs Hybrid Search (BM25 + cosine similarity) to retrieve the top 3–5 closest historical nodes. This handles most cases where the current and historical descriptions share semantic proximity.

  > **Embedding task type:** the synthetic match is embedded as a **document**
  > (`EmbeddingTaskType.DOCUMENT`), not a query. Turning the query into a document is precisely
  > what HyDE is for; labelling it `QUERY` would apply the query/document asymmetry correction
  > a second time. See `hld/LLM_Abstraction_Architecture.md` §2B.

- **Pass B — Structural Retrieval:** A deterministic, graph-keyed lookup that bypasses embedding entirely. It runs whenever any of the following anchors are present in the current episode:
  1. **Named persons** from the coreference map — retrieves all active `BeliefNode`, `PatternNode`, and `ObservationNode` instances linked to that `PersonEntityNode`.
  2. **`historical_era` tags** — retrieves all nodes tagged with that era (e.g., `a major entrance exam_PREP`).
  3. **High-sensitivity open nodes** — retrieves any `INAUTHENTICITY_STATE`, `IDENTITY_FUSION_STATE`, `EXISTENTIAL_REFLECTION`, or `SUPPRESSED_EMOTION_SURFACING` nodes with `reconciliation_status: PENDING_RERECONCILIATION` linked to referenced entities.

  Pass B guarantees that emotionally significant history (heartbreak, identity-defining relationships, historical trauma) is always surfaced during Reconciliation even when embedding distance is high due to semantic drift — i.e., when the user is describing *resolution* using vocabulary entirely different from the original *wound*.

**Merge rule:** Pass A and Pass B results are combined. Duplicates are deduplicated by `node_id`. The combined candidate set (max 8 nodes) is passed to Stage 3. Pass B nodes are tagged `retrieval_source: STRUCTURAL` in the candidate metadata so the Reconciliation LLM knows they were surfaced via anchor, not semantic similarity.

**Stage 3 summary:** A second LLM call receives both the new extraction and the historical candidates. It outputs one of eight structured decisions per node: `MERGE`, `REINFORCE`, `EVOLVE`, `BRANCH`, `CONTRADICT`, `DIALECTIC`, `REGULATE`, or `AMBIGUOUS`. Each action has a per-action confidence threshold; sub-threshold decisions route to HITL. See [`Reconciliation.md`](Reconciliation.md).

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
  id: pat_example_001
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

1. Every observation must have a `type` from the fixed Enum Dictionary (unknown types are rejected). Newly added enums include `COGNITIVE_DISTORTION_STATE`, `PHYSIOLOGICAL_CAPACITY_STATE`, and `EXISTENTIAL_REFLECTION`.
2. Every observation must have an `extraction_signal_strength` (`STANDARD` | `HIGH` | `CRITICAL`). A missing value is a hard failure.
3. Types with mandatory signal floors: `SUPPRESSED_EMOTION_SURFACING`, `METACOGNITIVE_INTERRUPT`, `METACOGNITIVE_BREAKTHROUGH`, `PROSODY_SIGNAL`, `IDENTITY_FUSION_STATE`, and `EXISTENTIAL_REFLECTION` must have `extraction_signal_strength: HIGH` or `CRITICAL`. (Each type's own definition in [`Microextraction.md`](Microextraction.md) states its floor; this is the consolidated list.)
4. Causal chain `type` values must be from the set `{TRIGGER, INTERNAL_STATE, ACTION, OUTCOME, LESSON}`. Unknown step types are rejected.

**On validation failure:**
- Validation is **per item**, not per response. A single bad observation is dropped on its own; its valid siblings in the same response survive. Rejecting a whole extraction over one malformed entry would discard good work and pay for a second call to recover it.
- Re-extraction is attempted for the failing items, with a correction prompt that names the violated rule and the offending field.
- **An observation may be re-extracted at most 3 times.** On the third failure it is written with `status: EXTRACTION_FAILED`, linked to its episode with a `failed_extraction` edge, and surfaced in the next HITL queue session. This matches the re-extraction limit in [`Reconciliation.md`](Reconciliation.md); an earlier version of this document said one attempt, which was wrong.

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
final_score = cosine_similarity × signal_weight_multiplier × recency_weight × trust_weight
```

### Trust Weight (Verification Status)

Nodes carry a `verification_status` field that tracks whether AI-assisted insights have been explicitly confirmed by the user. This prevents hallucinated or loosely-adopted AI reframes from dominating retrieval.

| `verification_status` | `trust_weight` | Set When |
|---|---|---|
| `IMPLICIT` | 1.0 | Default for `USER_GENERATED` provenance — user articulated this directly |
| `VERIFIED` | 1.0 | User explicitly confirmed via HITL review, or re-articulated in a later session (EVOLVE from CO_CREATED → USER_GENERATED) |
| `UNVERIFIED` | 0.5 | Default for `CO_CREATED` provenance — AI generated, user may have agreed but hasn't independently confirmed |

**Promotion rules:** `UNVERIFIED` → `VERIFIED` occurs ONLY through explicit user action:
1. User confirms accuracy in HITL review queue
2. User independently re-articulates the concept in a later session, triggering EVOLVE (Rule R3 ownership transfer also sets `verification_status = VERIFIED`)

There is no automatic promotion based on reinforcement count. An unverified insight remains unverified until the user actively confirms it.

### Recency Weight Decay

| Age of observation | `recency_weight` |
|---|---|
| < 30 days | 1.00 |
| 30–179 days | 0.85 |
| 180–364 days | 0.70 |
| ≥ 365 days | 0.50 |

Recency decay ensures that the retrieval layer reflects how the user currently is, not just what has the most historical volume. Older patterns are not deleted — they are reachable, but ranked lower unless explicitly queried.

---

## Conversational & AI-Assisted Extraction

Because Stage 0 preserves the raw multi-turn dialogue (both User and AI turns), the Stage 1 Microextraction LLM is explicitly instructed to capture AI-assisted psychological work:

1. **CO_CREATED Observations**: Using the adoption markers flagged by Stage 0, when the user agrees with and adopts an AI's framework or reframing, the Microextraction LLM extracts this as an observation and explicitly sets `provenance: CO_CREATED`. This ensures the graph ingests the AI's wisdom as part of the user's cognitive record, properly attributed.
2. **AI-Initiated Open Loops**: If the dialogue session ends with a profound, unanswered question or framing presented by the AI, the Microextraction LLM extracts this as an `OPEN_LOOP` **observation** with `provenance: AI_GENERATED`. Promotion to a first-class `OpenLoopNode` happens in Reconciliation, not here: deciding that a question is a standing investigation rather than a passing one requires knowing whether it has come up before, and Microextraction is blind to history by design. Once promoted, the system can queue the open question for the user's next interface interaction.

---

## Person Entity Lifecycle

Person Entity nodes are a first-class node type in the knowledge graph. Every named person mentioned across any entry resolves to a canonical Person Entity node, enabling cross-episode relationship tracking and protecting against alias fragmentation.

**Discovery:** Person entities are first discovered during the Preprocessing coreference pass (Stage 0), which produces a within-document alias map. The Microextraction LLM consumes this pre-computed map rather than re-deriving it.

**Cross-entry alias resolution:** Within a single entry, coreference is handled in Stage 0. Across entries, alias resolution (e.g., *"my mentor"* in one entry → *"Alex"* in another) is handled by the Reconciliation layer using the same `MERGE`-style logic as pattern merging — specifically by creating `same-as` edges between the alias node and the canonical Person Entity node. See [`Microextraction.md`](Microextraction.md) for the within-document coreference schema, and [`Reconciliation.md`](Reconciliation.md) for the cross-entry person resolution mechanism.

```yaml
person_entity:
  id: person_alex_001
  canonical_name: "Alex"
  aliases: ["Alex", "my mentor"]
  relationship_role: "Corporate mentor"
  first_mentioned: "2025-01-20"
  linked_episodes: [ep_example_003]
  linked_observation_types: [RELATIONAL_DYNAMIC, GRATITUDE_APPRECIATION, SOCIAL_PERFORMANCE_STATE]
```
