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

  > **Sparse search is not yet enabled.** The vector collection is created without a sparse
  > vector configuration, so "hybrid" search is dense-only today and `hybrid_search` logs a
  > warning if a sparse vector is passed to it. Retrieval quality is correspondingly weaker
  > for rare proper nouns and exact phrases, which is what BM25 is good at. Stated here
  > rather than left implicit, because "hybrid" otherwise reads as a capability that ships.

  > **One HyDE call per episode, not per node.** The hypothetical match for every extracted
  > node in an episode is generated in a single call and embedded in a single batch. A rich
  > episode can produce twenty-plus nodes, and a call each would make this stage the most
  > expensive thing in the pipeline by a wide margin.

  > **Embedding task type:** the synthetic match is embedded as a **document**
  > (`EmbeddingTaskType.DOCUMENT`), not a query. Turning the query into a document is precisely
  > what HyDE is for; labelling it `QUERY` would apply the query/document asymmetry correction
  > a second time. See `hld/LLM_Abstraction_Architecture.md` §2B.

- **Pass B — Structural Retrieval:** A deterministic, graph-keyed lookup that bypasses embedding entirely. It runs whenever any of the following anchors are present in the current episode:
  1. **Named persons** from the coreference map — retrieves all active `BeliefNode`, `PatternNode`, and `ObservationNode` instances linked to that `PersonEntityNode`.
  2. **`historical_era` tags** — retrieves all nodes tagged with that era (e.g., `a major entrance exam_PREP`).
  3. **High-sensitivity open nodes** — retrieves any `INAUTHENTICITY_STATE`, `IDENTITY_FUSION_STATE`, `EXISTENTIAL_REFLECTION`, or `SUPPRESSED_EMOTION_SURFACING` observations **belonging to an episode whose `reconciliation_status` is `PENDING_RERECONCILIATION`**.

     > These four are observation types, and `ObservationNode` has no `reconciliation_status` —
     > only `EpisodeNode` does. An earlier version of this line asked for a field that does not
     > exist on the nodes it names. The implementable reading is the one above: the observation
     > is reached through the episode that contains it, a two-hop lookup along `contains_obs`.

  Pass B guarantees that emotionally significant history (heartbreak, identity-defining relationships, historical trauma) is always surfaced during Reconciliation even when embedding distance is high due to semantic drift — i.e., when the user is describing *resolution* using vocabulary entirely different from the original *wound*.

**Merge rule:** Pass A and Pass B results are combined. Duplicates are deduplicated by `node_id`. The combined candidate set (max 8 nodes) is passed to Stage 3. Pass B nodes are tagged `retrieval_source: STRUCTURAL` in the candidate metadata so the Reconciliation LLM knows they were surfaced via anchor, not semantic similarity.

**Stage 3 summary:** A second LLM call receives both the new extraction and the historical candidates. It outputs one of eight structured decisions per node: `MERGE`, `REINFORCE`, `EVOLVE`, `BRANCH`, `CONTRADICT`, `DIALECTIC`, `REGULATE`, or `AMBIGUOUS`. Each action has a per-action confidence threshold; sub-threshold decisions route to HITL. See [`Reconciliation.md`](Reconciliation.md).

> **One batched call, plus one for anything consequential.** The whole episode is
> decided in a single `LIGHTWEIGHT` call; any item returning `EVOLVE`, `CONTRADICT`
> or `DIALECTIC` is re-asked in a single `THINKING` call that may confirm, lower
> confidence, or overrule to a safer action — never to a heavier one. See
> `Reconciliation.md` "How the roles are actually spent".

> **Stage 3 reads but does not write.** It takes `GraphProvider` as an injected
> parameter — to read candidate records in full, to check what already exists, and
> to count what has been decided about a node before. It returns a `GraphWritePlan`:
> the exact nodes, edges and bookkeeping operations its decisions imply, fully built
> and validated, executed later by the orchestrator without interpretation. This
> keeps every judgement about the user's history in one place and leaves the code
> that writes to the databases making no judgements at all.

> **What `BRANCH` creates depends on the observation type.** Only claim-like types
> (beliefs, patterns, core wounds, breakthroughs…) become standing `BeliefNode` /
> `PatternNode` records; the rest are recorded with their episode and never become
> permanent claims — while still being free to `MERGE`, `REINFORCE` or `REGULATE`
> against existing nodes. The full table is in `Reconciliation.md` "What BRANCH
> Creates".

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
- Re-extraction is attempted for the failing items only, with a correction prompt that names the violated rule and the offending field. Items that already validated are never re-asked, so a good observation from the first attempt cannot be re-rolled into a worse one on the second.
- **An observation gets at most 3 attempts in total** — the first reading plus two corrections. On the third failure it is written with `status: EXTRACTION_FAILED`, linked to its episode with a `failed_extraction` edge, and surfaced in the next HITL queue session. Matches [`Reconciliation.md`](Reconciliation.md).

### Which Rejections Are Re-Asked

A retry is a request for output, and a model asked twice will produce something. So the retryable set is fixed and deliberately small.

| Rejection | Re-asked | Reason |
|---|---|---|
| Unrecognised observation type | ✅ | The commonest failure and the most recoverable — the model can be shown the dictionary again. |
| Unrecognised enum value (provenance, signal strength, confidence) | ✅ | The same mistake in a smaller field. |
| Mandatory signal floor violated | ✅ | The model contradicted itself: it chose a type that marks unusual weight, then called it ordinary. |
| Unrecognised causal step type | ✅ | One unreadable step costs the whole sequence, so recovery is worth a call. |
| Empty content | ✅ | An item that arrived blank may simply have been truncated. |
| Type requires audio (`PROSODY_SIGNAL`) | ❌ | The pipeline has a transcript. No number of attempts changes that. |
| Type not permitted on this path | ❌ | The wrong reading was run over a thin entry; re-asking repeats the mistake. |
| Causal chain shorter than two steps | ❌ | A one-step sequence is a finding, not a chain. Asking again invites the model to pad it into one. |
| Over the per-episode ceiling | ❌ | Nothing was wrong with the item; there were simply too many. |
| **Stated-feeling quote not found in the entry** | ❌ **never** | This fires when a thin entry produced a feeling the person never put into words. A correction prompt asking for the missing quote is a direct instruction to produce one, and the fabricated quote would then pass the check. Retrying this rule would convert the strongest guard in the pipeline into the mechanism for defeating it. |

Rejections in the ❌ rows are discarded outright and never become `EXTRACTION_FAILED` records.

**When the reading itself fails** — a provider error, an unparseable reply, or a reply of the wrong shape — the request is re-issued rather than corrected, since there is nothing to correct. After the last attempt the episode is marked unreadable so its `EpisodeNode` is written with `reconciliation_status: SUSPENDED` rather than being stored as an episode that merely looks empty. Nothing is ever invented to fill the gap.

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

```
final_score = cosine_similarity × signal_weight_multiplier × recency_weight × trust_weight
```

**Not every layer applies every factor, and the split is deliberate.**

| Factor | Applied by | Why there |
|---|---|---|
| `cosine_similarity` | Stage 2 and the query layer | Comes straight from the vector store. |
| `signal_weight_multiplier` | Stage 2 and the query layer | Available on the node itself, and it decides which candidates Reconciliation ever sees. A `CRITICAL` node ranked just below the cut on raw distance has to be able to climb back above it. |
| `recency_weight` | Query layer (temporal decay, Goal 19) | Needs aged data to be meaningful, and Stage 2's candidate set is small enough that decay would mostly reorder things Reconciliation will judge on content anyway. |
| `trust_weight` | Query layer | Same. |

**What is stored versus what is ranked on.** `CandidateNode.similarity_score` is bounded
`0.0–1.0` and holds the **raw cosine**. The weighted score is a ranking step, not a
recorded value — a `CRITICAL` node's weighted score reaches `2.0` and would not fit the
field. Any layer that needs the weighted number recomputes it from the node's own signal
strength.

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

**Creation:** Person Entity nodes are created during Reconciliation (Stage 3), which
is the first stage that can see whether the person is already known. The node id is
derived from the canonical name (`person_alex`), so checking is a single lookup with
no matching involved. A person already known is not rewritten — only `mention_count`
and `last_mentioned_at` move. Every observation, event or session naming them gets a
`mentions` edge.

> Until Stage 3 shipped, nothing created these nodes, so Stage 2's named-person
> retrieval anchor had nothing to find. That is what closes the loop.

**Cross-entry alias resolution:** Within a single entry, coreference is handled in Stage 0. Across entries, alias resolution (e.g., *"my mentor"* in one entry → *"Alex"* in another) is handled by the Reconciliation layer using the same `MERGE`-style logic as pattern merging — specifically by creating `same-as` edges between the alias node and the canonical Person Entity node.

> **Not yet implemented.** Alias resolution is the same fuzzy-matching problem as
> pattern merging and deserves the same care rather than being smuggled into person
> creation. Today two spellings of one person produce two records. Stated here so
> the gap is a known limitation rather than a surprise. See [`Microextraction.md`](Microextraction.md) for the within-document coreference schema, and [`Reconciliation.md`](Reconciliation.md) for the cross-entry person resolution mechanism.

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
