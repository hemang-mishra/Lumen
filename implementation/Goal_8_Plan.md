# Goal 8: Stage 2 — Candidate Retrieval (HyDE + Structural)

**Branch:** `goal8`
**Status:** ✅ Complete
**Depends on:** Goal 1 (Kuzu + Qdrant providers) ✅, Goal 4 (embedding providers) ✅, Goal 6/7 (extraction) ✅
**Blocks:** Goal 9 (reconciliation), Goal 10 (orchestrator), Goal 14 (query-time retrieval)

---

## Objective

Stage 1 reads today's entry blind, on purpose. Stage 2 is where the history comes back —
it takes each newly extracted node and finds the handful of existing nodes that might be
the same thing, or the thing this one grew out of.

It is the hinge of the Late Binding design. Give Stage 3 too little and it creates a new
node for something the person has said twenty times; give it the wrong things and it
merges two ideas that were never the same. Everything the graph knows about a person is
downstream of what this stage decides to show.

The failure to watch for is not an error. **A retrieval that quietly returns nothing looks
exactly like a person having a genuinely new thought**, and Stage 3 answers both the same
way: by writing a new node. A broken embedding call therefore does not raise — it
fragments the graph, permanently and invisibly.

---

# SECTION A — LOGIC (please verify)

*Short by design. This is what the goal means; Section B is how it's coded.*

## A1. What Gets Built

| Deliverable | What it is |
|---|---|
| **`retrieve()`** | `ExtractionResult` in, one `RetrievalResult` per searchable node out. Providers injected, no global state. |
| **Pass A — semantic** | One batched HyDE call writes a hypothetical historical match for every extracted node; one batched embedding call turns them into vectors; Qdrant returns scored hits. |
| **Pass B — structural** | Graph lookups by named person, by historical era, and for unresolved high-signal material. No embeddings involved. |
| **Merge** | Deduplicate by `node_id`, rank, and cut to the 8 the contract allows — with a rule about who loses. |
| **3 new graph reads** | `GraphProvider` gains exactly the three lookups Pass B needs. |
| **1 protocol amendment** | `hybrid_search` returns scored hits instead of bare ids. |
| **2 contract additions** | `RetrievalResult.search_failed`, and `PipelineConfig` limits. |

## A2. The Decisions You Made

1. **Providers are injected, exactly like the language models.** `retrieve()` takes a
   `GraphProvider`, a `VectorProvider` and an `EmbeddingProvider` as parameters. The
   purity rule's intent survives: no global state, no hidden reads, and the orchestrator
   still owns every write. Reading is not persisting. `Technical_HLD.md` §8 gets one
   sentence saying so, because as written the rule reads as though this stage cannot exist.

2. **`GraphProvider` gains three narrow reads**, not a general query method: find nodes
   linked to a named person, find nodes carrying an era tag, find unresolved high-signal
   observations. Each is a named method with a typed result, and all the Cypher stays in
   `kuzu_impl.py` beside the edge registry. A generic filtered-query method would move
   graph-shaped thinking into the pipeline, which is the thing the Protocol exists to stop.

3. **`hybrid_search` returns scored hits.** It currently returns bare ids and throws the
   similarity away — while `CandidateNode` requires a score for every semantic candidate.
   That is a bug in a shipped contract, not a constraint to design around: the score is
   most of what Stage 3 uses to tell "the same thing" from "something adjacent".

4. **HyDE runs once per episode, not once per observation.** One LIGHTWEIGHT call takes
   every extracted item and returns one hypothetical match each; one `embed_batch` turns
   them all into vectors. A rich episode costs 2 calls instead of 40, and `embed_batch`
   already promises to return vectors in the order the texts went in.

5. **Observations, events and sessions are searched; causal chains are not.** Those three
   are what Stage 3 acts on and what the edge registry supports. A chain belongs to its
   episode and has no edge type that could reconcile it against another chain, so
   candidates for one would have nowhere to go. `RAW_CAPTURE` observations are excluded
   too — the spec is explicit that they bypass reconciliation.

6. **Rank on a signal-weighted score, store the raw cosine.** Fetch more than needed,
   multiply by the signal-strength weight, keep the top few. `similarity_score` is capped
   at 1.0 by its own contract and a weighted score reaches 2.0, so the field keeps the
   honest cosine. Recency and trust weighting stay deferred to Goal 19, where the Master
   Plan puts them. Over-fetching is what stops a `CRITICAL` node being dropped at rank 6
   before weighting could have rescued it.

7. **Built for this stage; extracted for the query layer later.** Goals 13–16 need much the
   same two passes, but with a 3-second budget, a session buffer and a token cap that this
   goal cannot anticipate. Both passes stay plain functions with narrow inputs so the
   shared shape is obvious when the second caller actually exists.

8. **Tested against real embedded Kuzu and Qdrant.** Both run in-process against a
   `tmp_path`, which is how Goal 1's own tests already work, so "needs no infrastructure"
   still holds. The point is that a Cypher mistake or a filter that only works against a
   forgiving stand-in fails the suite instead of passing it.

## A3. What Pass B Actually Looks For

Worth stating plainly, because this is the half that has no equivalent anywhere else in
the system.

| Anchor | What it retrieves | Why it exists |
|---|---|---|
| **Named person** | Active beliefs, patterns and observations linked to that `PersonEntityNode` | The same person described with different words across a year. |
| **Historical era** | Nodes tagged with the era the episode named | "Back during exam prep" should surface what that period already holds. |
| **Unresolved high-signal** | `INAUTHENTICITY_STATE`, `IDENTITY_FUSION_STATE`, `EXISTENTIAL_REFLECTION`, `SUPPRESSED_EMOTION_SURFACING` observations still awaiting reconciliation | The wound and its resolution are described in completely different vocabulary, so embedding distance is exactly wrong here. |

The third row is the whole reason Pass B is not just an optimisation. A person describing
recovery uses none of the words they used describing the injury; a semantic search compares
the two and finds them unrelated. Pass B does not care what either one says.

## A4. Where the Specs Disagree With Themselves

Found while reading. Each needs a doc fix, listed in B9.

1. **How many factors are in the score.** `Architecture.md` says
   `cosine × signal × recency × trust`; `Schema.md` says `cosine × signal × recency`;
   `Master_Plan.md` puts temporal decay in Goal 19 and the score formula in Goal 15.
   Resolution per A2-6: Stage 2 applies **signal only**, and both docs get a line saying
   which layer applies which factor, so the formula stops looking like something one place
   was supposed to implement whole.

2. **Pass B's third anchor names a field that does not exist.** `Architecture.md` asks for
   observations "with `reconciliation_status: PENDING_RERECONCILIATION`" — but
   `ObservationNode` has no such field. Only `EpisodeNode` does. The four types it names
   are all observation types. The implementable reading is **observations whose episode is
   awaiting reconciliation**, which is a two-hop lookup through `contains_obs`. The doc is
   amended to say that.

3. **`similarity_score` cannot hold what Architecture.md asks it to.** Bounded `0.0–1.0`,
   while a `CRITICAL` weighted score is `2.0`. Resolved by A2-6: raw cosine is stored,
   weighting happens during ranking and is not persisted here.

4. **`hybrid_search` discards the score it was asked for.** A2-3.

5. **The Master Plan's signature is singular.** "Input: `ExtractionResult` → Output:
   `RetrievalResult`" — but `RetrievalResult` is per source node, so a rich episode
   produces a list of them. Recorded so the deviation is not read as an accident.

## A5. What This Goal Deliberately Leaves Undone

| Deferred | To | Why |
|---|---|---|
| BM25 / sparse search | Not scheduled | Qdrant's sparse config was never enabled; `hybrid_search` already logs a warning when a sparse vector is passed. The hybrid stays dense-only and says so out loud. |
| Recency and trust weighting | Goal 19 / 15 | A4-1. Goal 19 owns decay and can test it against aged data; this goal cannot. |
| Writing vectors into Qdrant | Goal 10 | This stage reads a populated store. Nothing populates it yet, which is why the tests seed it. |
| Person anchors bearing fruit | Goal 9 / 10 | See A6 — the nodes and edges Pass B needs do not exist until reconciliation creates them. |
| Query-time retrieval, Pass C | Goals 13–16 | A2-7. |
| Graph traversal and debug APIs | Goal 11 | This goal adds three lookups, not a query surface. |

## A6. The Risk Worth Naming

**Two different things produce an empty candidate list, and only one of them is good news.**

A person can genuinely have a new thought — first entry, new domain, something they have
never said. Retrieval correctly returns nothing, Stage 3 creates a node, and the graph
grows the way it should.

Or the embedding provider is down, or the collection is empty, or the vector dimension
changed under a re-embedding, or a Cypher predicate silently matches nothing. Retrieval
returns nothing, Stage 3 creates a node — and the person's most-repeated pattern is
recorded as a brand new discovery for the twentieth time. Nothing raises. Nothing is
logged as wrong. The graph fragments quietly and the damage is permanent, because Stage 3
writes are append-only.

Three defences:

1. **`search_failed` on the result.** "Could not search" and "searched and found nothing"
   become different states, and Goal 9 is told plainly that it must not branch on the
   first. This is the same shape as Goal 7's `read_failed`, for the same reason.
2. **A cold graph is a tested, first-class case**, not an edge case — the very first entry
   ever must run clean and return zero candidates without any of it looking like a failure.
3. **The closing log line carries per-pass counts**, so a Pass A that has silently stopped
   returning anything is visible as a number that went to zero rather than as a slow drift
   in graph quality nobody can trace.

A second, quieter risk: **HyDE invents.** The hypothetical historical match is a fabricated
document by design, and a bad one steers the search toward history the person does not
have. It is never stored and never shown to anyone — it exists only to be embedded — but a
badly-worded prompt would degrade retrieval in a way no test would notice. So the
hypothetical is generated from the extracted content alone, with no access to the graph,
and the prompt is asserted never to invite specifics it was not given.

## A7. Definition of Done

- [ ] `retrieve()` is a pure function of its inputs and injected providers — no global
      state, no writes; a test asserts `lumen/pipeline/` still imports no `*_impl` module.
- [ ] A rich episode costs exactly one HyDE call and one embedding call, whatever the
      number of extracted nodes.
- [ ] A `REFLECTION` episode's observations, events and session all get results;
      `RAW_CAPTURE` observations and causal chains get none.
- [ ] Pass A returns candidates ranked by signal-weighted score, with the raw cosine stored
      on each and never exceeding 1.0.
- [ ] A `CRITICAL` node ranked below the cut on raw cosine is rescued by weighting, and a
      test proves it — the over-fetch has to earn its cost.
- [ ] Pass B finds a node by named person, by era tag, and by unresolved high-signal
      status, each asserted against real Kuzu.
- [ ] The merged set is deduplicated by `node_id` and never exceeds 8; when it would,
      **Pass A loses first** — Pass B exists precisely to surface what semantic distance
      cannot reach.
- [ ] A structural candidate carries its anchor type and value; a semantic one carries a
      score. Neither carries the other's field.
- [ ] **A cold graph returns zero candidates with `search_failed: False`** and no warning.
- [ ] **An embedding failure returns zero candidates with `search_failed: True`** and a
      warning, and nothing is invented.
- [ ] A failed Pass B does not take Pass A down with it, and the reverse.
- [ ] Journal text never appears in a log line unless `LUMEN_LOG_PROMPTS=true` — the HyDE
      prompt contains the person's writing.
- [ ] Every result carries the ambient `trace_id`; `retrieval_time_ms` is populated.
- [ ] ≥90% coverage on `lumen/pipeline/retrieval/`.

---

# SECTION B — LOW-LEVEL DESIGN

## B1. Files

```
lumen/pipeline/retrieval/
├── __init__.py        — public surface: retrieve()
├── stage.py           — retrieve(): sequences the passes, merges, times, logs
├── hyde.py            — the batched hypothetical-match call and the batched embedding
├── semantic.py        — Pass A: search, hydrate, weight, cut
├── structural.py      — Pass B: the three anchor lookups
├── merge.py           — dedup, rank, cap, and the signal weights
├── contracts.py       — internal shapes: HydeResponse, SearchTarget, PassResult
└── prompts.py         — the HyDE template

lumen/graph/provider.py, kuzu_impl.py   — three read methods
lumen/vector/provider.py, qdrant_impl.py — scored hits

lumen/tests/
├── test_retrieval_hyde.py
├── test_retrieval_semantic.py
├── test_retrieval_structural.py
├── test_retrieval_merge.py
├── test_retrieval_stage.py
└── test_graph_reads.py        — the new provider methods against real Kuzu
```

**Deviation from `Master_Plan.md`**, which names `lumen/pipeline/retrieval.py`. Same call
as Goals 5–7.

## B2. Protocol Amendments

```python
# lumen/vector/provider.py
class ScoredHit(NamedTuple):
    node_id: str
    score: float

def hybrid_search(...) -> list[ScoredHit]:      # was list[str]
```

One caller exists (nothing yet) and one test file, so the blast radius is small. The
`qdrant_impl` change is to stop discarding `hit.score`.

```python
# lumen/graph/provider.py
def find_linked_to_person(self, canonical_name: str, *, node_types: list[str], limit: int) -> list[dict]
def find_by_era(self, era_tag: str, *, node_types: list[str], limit: int) -> list[dict]
def find_unresolved_high_signal(self, observation_types: list[str], *, limit: int) -> list[dict]
```

All three return the same raw node dicts `get_nodes_by_ids` already returns, so hydration
and candidate-building have one shape to handle.

> **Note on hydration cost:** `MATCH (n) WHERE n.node_id IN $ids` returns a row carrying the
> union of every column across all fifteen node tables — roughly 120 keys, nearly all null.
> Harmless at this scale (tens of candidates) and not worth optimising now, but it is the
> reason candidate building reads `_label` for the node type rather than inferring it.

**The purity test changes.** Goal 6's test asserts `lumen/pipeline/` mentions neither
`lumen.graph` nor `lumen.vector`. That was right when no stage read from a store; now it
would forbid the stage this goal exists to build. It becomes: `lumen/pipeline/` may name
the **Protocol modules** but never `kuzu_impl` or `qdrant_impl` — which is the actual rule
(no vendor SDKs in business logic) rather than a proxy for it.

## B3. Contract Additions

```python
# lumen/schemas/pipeline.py
class RetrievalResult(PipelineDTO):
    ...
    search_failed: bool = False     # could not search, as opposed to found nothing

# lumen/config.py — PipelineConfig
pass_a_keep: int          = _env_int("LUMEN_PASS_A_KEEP", 5)
pass_a_overfetch: int     = _env_int("LUMEN_PASS_A_OVERFETCH", 20)
pass_b_keep: int          = _env_int("LUMEN_PASS_B_KEEP", 5)
merged_candidate_cap: int = _env_int("LUMEN_CANDIDATE_CAP", 8)
```

The cap is also enforced by `RetrievalResult`'s own validator; the config value exists so
the merge can cut *before* building a model that would refuse to construct. If the two ever
disagree the model wins, and a test asserts the config default matches it.

## B4. `hyde.py` — One Call For The Whole Episode

```python
def write_hypotheticals(targets, *, provider: LLMProvider, config) -> HydeResult
def embed_hypotheticals(hypotheticals, *, embedder: EmbeddingProvider) -> list[list[float]]
```

The model is given every extracted item at once, numbered, and returns one hypothetical
per item in the same order. Order is the join key, exactly as with `embed_batch`, and a
short reply is padded rather than realigned — a mismatched hypothetical is worse than a
missing one, because it searches confidently for the wrong thing.

**Embedded as `DOCUMENT`, not `QUERY`.** Turning a query into a document is what HyDE is
for; labelling it `QUERY` would apply the asymmetry correction a second time.

**Fallback:** if the HyDE call fails, Pass A falls back to embedding the extracted content
directly. That is a worse search but a real one, and it keeps a single model failure from
producing the empty-candidate outcome A6 is about. `search_failed` is set only when the
*embedding* fails, since without vectors there is no search at all.

## B5. `semantic.py` — Pass A

```
1. search Qdrant with the hypothetical's vector, over-fetching        [vector]
2. hydrate the hits from Kuzu, dropping anything inactive or          [graph]
   not a content node
3. drop the node's own episode — an observation must not match itself
4. weight each hit by its signal strength, rank, keep the top N       [code]
5. build CandidateNode(retrieval_source=SEMANTIC, similarity_score=raw cosine)
```

Filtering happens after hydration rather than inside the vector query. Qdrant's payload
indexes exist for exactly this, but using them means another parameter on a protocol this
goal is already amending, and the over-fetch covers the waste. Recorded as a deliberate
trade: on a graph thick with superseded nodes it would want revisiting.

Step 3 matters more than it looks. The episode being processed may already be in the store
if a run is replayed, and a node retrieved as a candidate for itself would reconcile as a
perfect `MERGE` with total confidence.

## B6. `structural.py` — Pass B

```python
def by_person(names, *, graph, config) -> list[CandidateNode]
def by_era(era, *, graph, config) -> list[CandidateNode]
def unresolved_high_signal(*, graph, config) -> list[CandidateNode]
```

Each result carries `structural_anchor_type` and `structural_anchor_value`, which
`CandidateNode` requires for structural candidates and which tells Stage 3 the node was
surfaced by an anchor rather than by resemblance.

Person names come from the extracted nodes' `person_refs` and from the coreference map.
**None of this bears fruit yet:** `PersonEntityNode`s and `mentions` edges are created by
reconciliation, which does not exist until Goal 9, so on today's graph the person anchor
returns nothing. It degrades to empty rather than to an error, is tested against a graph
where the nodes *do* exist, and is noted here so its silence is not later mistaken for a
bug.

The three lookups run independently; one raising does not stop the others, and each logs
its own failure.

## B7. `merge.py` — Dedup, Rank, Cap

```python
SIGNAL_WEIGHT = {STANDARD: 1.0, HIGH: 1.5, CRITICAL: 2.0}

def merge(pass_a, pass_b, *, cap: int) -> tuple[list[CandidateNode], list[CandidateNode]]
```

- **Deduplicate by `node_id`**, keeping the structural copy when a node came back from
  both. It carries the anchor, which is information the semantic copy does not have, and
  the score is preserved on it anyway.
- **When over the cap, Pass A loses first.** Pass B's whole purpose is surfacing what
  semantic distance cannot reach, so dropping it to make room for closer semantic matches
  would remove the candidates that only Pass B could have found. Within Pass A, the lowest
  weighted score goes first.
- The two lists stay separate in the result, because `RetrievalResult` keeps them apart and
  Stage 3 is told which pass surfaced what.

## B8. `stage.py` — The Sequence

```python
def retrieve(
    extraction: ExtractionResult,
    *,
    graph: GraphProvider,
    vectors: VectorProvider,
    embedder: EmbeddingProvider,
    lightweight: LLMProvider,
    coreference_map: CoreferenceMap | None = None,
    config: AppConfig | None = None,
) -> list[RetrievalResult]
```

```
1. collect the searchable nodes (A2-5)                       [code]
2. if none: return []                                        [code]
3. one HyDE call for all of them                             [LIGHTWEIGHT]
4. one embedding call for all of them                        [EMBEDDING]
5. Pass B once per episode — anchors are episode-level        [graph]
6. per node: Pass A, then merge with Pass B                  [vector + code]
7. one RetrievalResult each, timed; one closing log line     [code]
```

Pass B runs **once per episode**, not once per node: its anchors are the episode's people
and era, which every node in the episode shares. Its results are merged into each node's
candidate set. Running it per node would repeat identical graph queries a dozen times for
one episode.

## B9. Doc Amendments Required

Applied before coding, as Goals 4–7 did.

1. `Architecture.md` — split the score formula by layer: signal at retrieval, recency and
   trust at the query layer (A4-1); fix Pass B's third anchor to the episode-level status
   it can actually read (A4-2); note that hybrid search is dense-only until sparse ships.
2. `Schema.md` — same split, so the two formulas stop disagreeing; note that
   `similarity_score` holds the raw cosine and weighting is a ranking step (A4-3).
3. `Technical_HLD.md` §8 — one sentence: pipeline stages read through injected Protocols;
   the no-database rule is about writes and hidden global state (A2-1). Also
   `RetrievalResult.search_failed`.
4. `Master_Plan.md` — record that Stage 2 returns a list, and tick Goal 8.

## B10. Test Plan (~90 tests)

| File | Covers |
|---|---|
| `test_graph_reads.py` | The three new lookups against real Kuzu: found, not found, limit honoured, type filter honoured, inactive nodes excluded, the two-hop episode-status query. |
| `test_retrieval_hyde.py` | One call for many items; order preserved; a short reply padded rather than realigned; embedded as `DOCUMENT`; the fallback to direct embedding; the prompt carries no graph content. |
| `test_retrieval_semantic.py` | Scores survive the search; hydration drops inactive and non-content nodes; a node never matches itself; **weighting rescues a `CRITICAL` node from below the cut**; the stored score stays the raw cosine. |
| `test_retrieval_structural.py` | Each anchor type produces candidates carrying anchor type and value; a missing person degrades to empty; one failing lookup does not stop the other two. |
| `test_retrieval_merge.py` | Dedup by id; the structural copy wins a tie; the cap holds; **Pass A loses first**; within Pass A the lowest weighted score goes first. |
| `test_retrieval_stage.py` | Which node kinds are searched and which are skipped; call counts are exactly 1 and 1; Pass B runs once per episode; **a cold graph returns zero candidates and `search_failed: False`**; an embedding failure sets `search_failed: True`; `retrieval_time_ms` populated; the purity check. |

Kuzu and Qdrant run embedded against `tmp_path`; the LLM and embedding providers are the
existing fakes. No network, no credentials.

## B11. Build Order

0. Doc amendments (B9).
1. `hybrid_search` → scored hits, with its impl and tests.
2. The three `GraphProvider` reads, with `test_graph_reads.py`.
3. `RetrievalResult.search_failed` + `PipelineConfig` limits.
4. `contracts.py`, `prompts.py`, `hyde.py`.
5. `structural.py` — no embeddings, testable on its own first.
6. `semantic.py`, then `merge.py`.
7. `stage.py`, then the purity test update.
8. `Master_Plan.md` and Section C.

---

# SECTION C — RESULTS

**Status:** ✅ Complete
**Tests:** 1395 passing (1253 before this goal + 142 new), 9 live tests still deselected.
**Coverage:** **100%** on `lumen/pipeline/` (all 25 modules), `lumen/config.py`, and
`lumen/schemas/pipeline.py`.

## C1. What Was Built

| Module | Contents |
|---|---|
| `pipeline/retrieval/hyde.py` | The one batched call that writes search text, the batched embedding, and the alignment rule that keeps answers on their own findings. |
| `pipeline/retrieval/semantic.py` | Pass A: search, hydrate, filter, weight, cut. The weights and the definition of a content node live here. |
| `pipeline/retrieval/structural.py` | Pass B: the three anchors, each asked independently and each failing on its own. |
| `pipeline/retrieval/hydrate.py` | Graph rows into candidates — where each kind of node keeps its readable part. |
| `pipeline/retrieval/merge.py` | Dedup, the cap, and the rule about who loses. |
| `pipeline/retrieval/stage.py` | `retrieve()`, the sequencing, and the closing log line. |
| `graph/provider.py`, `kuzu_impl.py` | Three anchor lookups plus the small tables that say how each node type answers them. |
| `vector/provider.py`, `qdrant_impl.py` | `ScoredHit`; the search stops discarding its scores. |

## C2. Deviations From the Plan

1. **`hydrate.py` was not in B1.** Both passes have to turn graph rows into candidates, and
   the mapping from node type to "where the readable part lives" is real work. Putting it
   in one place is what lets a candidate look the same however it was found.

2. **`StructuralAnchorType` was missing a value.** The retrieval spec has described three
   anchors all along, and the enum had two. A candidate surfaced by the third would have
   been a structural candidate with no anchor type — which `DecisionAuditNode`'s own
   validator refuses, so Goal 9 would have hit it at write time rather than here.
   `HIGH_SENSITIVITY_OPEN` added, with `Schema.md` updated.

3. **Person anchors are one hop, not two.** `Architecture.md` asks for beliefs and patterns
   linked to a person, but the edge registry only routes *observations, events and
   sessions* to a `PersonEntityNode` — a belief reaches a person solely through the
   observation that produced it. The lookup does what the edges support and skips node
   types with no route, logging that it did. The second hop belongs with Goal 11's
   traversal work.

   > **Closed in Goal 11, wired up in Goal 12.** `find_linked_to_person` now takes the
   > second step for `PatternNode` and `BeliefNode`, through whichever link a decision
   > made from the finding — `branches_to`, `reinforces`, or `same_as`. Withdrawn links
   > are not followed, and a record reachable by two routes is offered once, since a
   > duplicate wastes one of very few candidate places.
   >
   > Goal 11 built the hop and nothing called it: retrieval's `PERSON_LINKED_TYPES` still
   > listed only the three kinds that name a person directly. Goal 12 added the other two,
   > which is what makes a standing pattern about someone findable when they are mentioned
   > again in words that match nothing.

4. **The purity test was rewritten, as B2 anticipated — and then a second one was added.**
   See C3-1.

## C3. Things Caught While Implementing

1. **Naming a Protocol imported a database driver.** `lumen/vector/__init__.py` exported
   both the Protocol and the Qdrant implementation, so `from lumen.vector.provider import
   VectorProvider` executed the package `__init__` and pulled in `qdrant_client` — and the
   same for Kuzu. The pipeline was therefore transitively importing both vendor SDKs purely
   to name two types, which is exactly what Rule 1 forbids and what the old text-matching
   purity test could never have seen. Both packages now export only their Protocol, and a
   test spawns a fresh interpreter to assert that importing `lumen.pipeline` leaves `kuzu`
   and `qdrant_client` out of `sys.modules`.

2. **The stand-in embedder cannot express "close but not identical".** It hashes text, so
   two sentences that mean nearly the same thing land nowhere near each other — the
   weighting test built on it failed for reasons that had nothing to do with weighting.
   Ranking tests now place vectors by angle, so the closeness is chosen exactly and the
   arithmetic is checkable. Added `vector_at_angle`, and a mirror test proving that with a
   smaller fetch the weighty node is lost — the over-fetch has to be shown to be load-bearing,
   not merely present.

3. **Cosine similarity overshoots 1.0.** Floating point sums put an exact match at
   `1.0000000852900428`, which the candidate model refuses. Clamped, with the reason
   written down.

## C4. What the Tests Cover

142 new tests across 6 new files plus 3 extended ones. The ones worth knowing about:

- **A cold graph and a broken search are tested side by side**, asserting the same empty
  candidate list and opposite `search_failed` values. That pair is the whole reason the
  field exists.
- **The anchors are asserted to still run when the search cannot**, so a dead embedder
  costs the resembling half and not the half that never needed vectors.
- **Weighting is proved to rescue a node from below the cut, and proved to lose it when the
  fetch is too small** — the second test is what makes the first mean something.
- **A node is proved never to match itself**, which is what a replayed run would otherwise
  reconcile as a perfect merge with total confidence.
- **Every anchor asserts what it recorded about itself**, since "a name matched" and "it
  reads similarly" are different claims and the second is much easier to over-trust.
- **One broken anchor is proved not to take the other two**, against a deliberately
  half-broken graph.
- **The stores are asserted unchanged by a run**, including that no new node appeared.
- **The merge's cap is asserted to agree with the result model's own limit**, so the two
  can never drift into a state where a legitimate retrieval refuses to build.

## C5. Still Deferred

Unchanged from A5. Three worth restating:

**Sparse search is still not enabled**, so "hybrid" remains dense-only and says so in the
logs. Rare proper nouns and exact phrases are what this costs.

**Person anchors will find nothing until Goal 9 runs.** `PersonEntityNode`s and `mentions`
edges are created during reconciliation, so on today's graph that anchor is silent by
construction. Tested against a graph where those nodes exist, so the code is known to work
the moment the data does.

**Recency and trust weighting are Goal 19's.** Stage 2 ranks on closeness and signal only,
and `Architecture.md` now says which layer applies which factor rather than presenting one
formula that no single layer implements.
