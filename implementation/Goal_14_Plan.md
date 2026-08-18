# Goal 14: Parallel Retrieval Passes (A, B, C)

**Branch:** `goal14`
**Depends on:** Goal 8 (extraction-time retrieval), Goal 11 (graph reads), Goal 13 (query formulation), Goal 13b (a graph with real content in it)
**Spec:** `docs/Query/Conversational_RAG_Mode.md` Stage 2 + Stage 4, `docs/Extraction/Architecture.md` Stage 2

---

## Objective

Goal 13 built the router: on every turn of a live conversation it decides *whether*
anything in the person's history is worth fetching, and hands back a checked list of
reasons. Nothing yet acts on those reasons.

Goal 14 acts on them. Given one turn's reasons, go and find the actual records — three
different ways, at the same time, inside a three-second budget — and hand back a ranked
list of what was found. Goal 15 compresses that list into the ≤400-token block, Goal 16
puts it in front of the AI.

---

# SECTION A — LOGIC (please verify)

*Plain-language description of what this goal builds and the calls being made. This is
the part worth reviewing.*

## A1. What Gets Built

One new component: **the conversational retriever**. It takes the signal Goal 13
produced for a turn and returns the records worth knowing about before the AI answers.

It searches in three different ways, because they fail in different places:

| | What it does | Why it exists |
|---|---|---|
| **Pass A — Semantic** | Turns the turn into a made-up journal entry, embeds it, and asks the search index for the closest stored records. | Finds things phrased the same way. This is ordinary search. |
| **Pass B — Structural** | Follows *anchors* instead of words: the person named, the period of life referred to, the questions still unfinished. Reads no text at all. | Someone describing recovery uses none of the words they used describing the injury. No measure of word-distance connects those two. Only the anchor does. |
| **Pass C — Continuity** | Checks what was already surfaced *earlier today* and re-offers whatever is still relevant, with a boost. | Without it, every turn retrieves from scratch and the conversation loses its thread. The afternoon's realisation and the evening's origin story are the same story, and only this pass knows the afternoon happened. |

Pass C is the genuinely new one. A and B exist already for the extraction pipeline
(Goal 8) and are rebuilt here because the question is different: the pipeline asks *"is
this new thing the same as something old?"*, this asks *"what does this person's history
say about what they just told me?"* — one is about matching, the other about relevance.

## A2. The Decisions Taken

**1. A and B run at the same time; C runs after A.**
The spec calls all three parallel. C cannot be: its whole job is comparing today's
earlier findings against *this* turn, and the measurement it needs is the one A just
computed. Giving C its own model call to measure the same sentence again would double
the cost of the turn to learn nothing. So A and B genuinely run side by side, and C runs
afterwards on numbers already in memory — which takes about a millisecond.

**2. Three seconds is a wall clock, not a hope.**
The budget is enforced from outside, the same way Goal 13 enforced its 600ms. Whatever
finished by the deadline is returned; whatever did not is abandoned and reported as
abandoned. A pass that times out costs that pass and nothing else — losing the semantic
half does not lose the anchors.

**3. Finding nothing and failing to look are reported differently.**
Goal 8's hardest-won lesson, and it applies here too. A search that quietly returns
nothing looks exactly like a person with no history on the subject. Every pass reports
whether it ran, so nobody downstream has to guess.

**4. The most sensitive records are locked by default.**
A record marked CRITICAL — the highest-signal material in someone's history — is *not*
freely surfaced. The opposite: the higher the signal, the more deliberately it is gated.
A CRITICAL record about a painful area of life is withheld unless the person has raised
that area themselves today. Goal 13 already detects that opening and records it on the
session; this goal is the first thing that acts on it.

Two rules the spec left unstated and this goal has to settle:

- *Which areas count as sensitive?* Four: how the person sees themselves, their
  relationships, their health, and their spiritual life. **Not** "emotional" — in a
  therapeutic conversation nearly everything is emotional, and gating that would gate
  the entire graph.
- *What about a CRITICAL record that belongs to no area at all?* (Individual
  observations record no area of life — only the standing beliefs and patterns do.) It
  is treated as sensitive and stays locked until the person has opened *some* sensitive
  area today. The safe reading of "we do not know what this is about" is caution, since
  this is by definition the heaviest material in the graph.

**5. A crisis turn retrieves nothing, and that is already guaranteed.**
Goal 13 throws the reasons away when the person is in acute distress, so this component
receives an empty list and does nothing. It still says so explicitly rather than
returning silently, because "the graph had nothing" and "now is not the time" are
different facts and the log should be able to tell them apart.

**6. Each reason narrows the search to the kind of record that could answer it.**
Someone describing a physical sensation ("my chest goes tight") should not be answered
with career beliefs. Each kind of reason carries the kinds of record it can be answered
by, as a table:

| Reason | Where it looks |
|---|---|
| Recurring behaviour | Semantic, unrestricted |
| Questioning a belief | Semantic + the standing beliefs in that area |
| A period of the past | Anchors only — everything filed under that period |
| A person | Anchors only — everything that mentions them, and the patterns those became |
| A physical sensation | Semantic, restricted to bodily and surfacing-emotion records |
| A claim about who they are | Semantic, restricted to beliefs and self-model records |
| A claim of improvement | The unfinished questions and the standing records that could now be closed |
| An unfinished question | The open-questions table |

**7. The buffer holds five, and CRITICAL entries are never pushed out.**
Anything surfaced today stays available for five more turns; if it goes unmentioned for
five consecutive turns it drops out. The spec says CRITICAL entries are never evicted
mid-session, which — combined with a five-slot buffer — means a day can fill entirely
with protected entries and admit nothing new. The rule shipped: protected entries are
never removed, and a new record that cannot get a slot is still returned to the AI this
turn, it simply does not join the buffer. Nothing is lost; the buffer just stops growing.

**8. Ranking here is provisional; the final ranking is Goal 15's.**
This goal orders candidates by *how close × how much the record weighs × the
continuity boost*, and cuts the list to a dozen. The fourth factor in the spec's formula
— how recently it happened — is deliberately absent: temporal decay is Goal 19's, and
inventing a decay curve now would mean building it twice.

## A3. What One Turn Costs

| | Model calls | Wall clock |
|---|---|---|
| Turn with no reason to search (~60–70% of turns) | 0 | ~0ms — the retriever is not called at all |
| Turn with reasons | 1 (writing the search text) + 1 embedding | Budgeted at ≤3s; typically 0.6–1.2s |
| Turn in crisis | 0 | ~0ms |

The three-second window is spent while the person is still reading the previous reply,
which is what makes it invisible rather than a pause.

## A4. What This Goal Deliberately Leaves Undone

| Deferred | To | Why |
|---|---|---|
| Compressing records into the ≤400-token briefing | Goal 15 | This goal produces candidates; compression is a separate concern with its own templates. |
| The per-mood injection caps (3 nodes / 1–2 / 5) | Goal 15 | Those are injection policy, not retrieval. Retrieval fetches; assembly decides how much survives. |
| Time decay in the score | Goal 19 | The Master Plan puts it there and it needs its own tests. |
| Carry-forward when the budget is missed | Goal 16 | This goal *measures* whether the budget was met and says so on the result. Acting on that — inject now vs. prepend next turn — belongs with the thing that owns the turn. |
| Sparse/BM25 half of "hybrid" search | unchanged | Never enabled; the provider logs a warning and searches dense-only, as it has since Goal 1. |

## A5. The Risk Worth Naming

The buffer's relevance measure is the weakest link. It compares today's earlier
findings against the current turn using the vectors already in the index — free and
instant, but it only works if those vectors are there. A record whose vector is missing
(imported before the index existed, or written when an embedding call failed) falls back
to plain word overlap, which is a much blunter instrument. That is visible in the logs
rather than silent, but it will make Pass C weaker on an old or partially-indexed graph
than the tests suggest.

## A6. Definition of Done

1. Given a `HISTORICAL_ERA` reason, Pass B returns the records filed under that period
   from a real graph — the Master Plan's named test.
2. Given a person's name, Pass B returns both the notes mentioning them and the standing
   patterns those notes became.
3. A record surfaced on turn 3 is re-offered with a boost on turn 6 when the subject
   comes back, and is gone by turn 9 if it does not.
4. A CRITICAL record in a sensitive area is withheld until the person opens that area,
   and offered afterwards, within the same day.
5. A pass that fails or times out costs only itself; the others still answer.
6. Everything above runs against real Kuzu and real Qdrant, not stand-ins.
7. ≥90% coverage on new code (the repo's working standard is 100%).

---

# SECTION B — LOW-LEVEL DESIGN

## B1. Files

**New — `lumen/query/retrieval/`** (the package):

| File | Holds |
|---|---|
| `stage.py` | `ConversationalRetriever` — the only public name. Owns the fan-out, the budget, the merge, and the buffer update. |
| `contracts.py` | `RetrievedNode`, `RetrievalBundle`, `PassReport`, `PassAResult`. |
| `hyde.py` | Turn + reasons → search texts (one batched call) → vectors. |
| `semantic.py` | Pass A: search, hydrate, filter by the reason's wanted record kinds, rank, cut. |
| `structural.py` | Pass B: the anchor table, one lookup per reason, each failing alone. |
| `continuity.py` | Pass C: score the buffer against this turn, produce re-injections and boosts. |
| `gate.py` | The sensitivity gate — which records are withheld and why. |
| `merge.py` | Union across passes, dedupe, score, order, cut. |
| `prompts.py` | The HyDE instruction for a conversational turn. |

**New — elsewhere:**

| File | Holds |
|---|---|
| `lumen/query/buffer.py` | `SessionContextBuffer`, `BufferEntry`. Lives beside `session.py`, not inside `retrieval/`, because it is day-session state that the retriever reads — and because `session.py` has to import it, which a package-level import would turn into a cycle. |
| `lumen/query/deadline.py` | Moved up from `formulation/deadline.py` and given `run_all()`. It is query-layer infrastructure, not a formulation detail, and both halves now need it. |
| `lumen/graph/rows.py` | `preview_of`, `signal_of`, `SIGNAL_WEIGHT`, `CONTENT_TABLES`, `RETIRED_STATUSES`. Moved out of `pipeline/retrieval/` so both retrieval layers read a stored row the same way. `hydrate.py` / `semantic.py` re-export, so nothing existing changes. |
| `lumen/api/resources.py` | `LazySearchStack` — builds the retriever on first use, sharing the importer's vector store. |

**Amended:**

| File | Change |
|---|---|
| `lumen/query/session.py` | `ChatSession` gains `context_buffer`. Goal 13 said Goal 14 would attach it here. |
| `lumen/config.py` | `QueryConfig` gains the retrieval knobs (B7). |
| `lumen/vector/provider.py` + `qdrant_impl.py` | `get_vectors(node_ids)` — Pass C needs a stored node's vector to compare it against this turn, and nothing could read one back. |
| `lumen/query/formulation/stage.py`, `lumen/api/routes/query.py`, `lumen/api/deps.py`, `lumen/api/main.py`, `lumen/api/schemas.py` | Import move; new `POST /query/retrieve`. |
| `lumen/api/static/*` | A retrieval panel on the existing turn-reading page. |

## B2. Contracts (`retrieval/contracts.py`)

```python
class RetrievalPass(StrEnum):        # → schemas/enums.py
    SEMANTIC = "A"; STRUCTURAL = "B"; CONTINUITY = "C"

class RetrievedNode(BaseModel):      # frozen
    node_id, node_type, preview
    found_by: RetrievalPass
    trigger_type: TriggerType | None      # which reason surfaced it
    similarity: float | None              # measured; None for anchors
    signal_strength: SignalStrength
    domain: Domain | None
    era_tag: str | None
    occurred_at: datetime | None
    anchor_type: StructuralAnchorType | None
    anchor_value: str | None
    boosted: bool                         # was already in today's buffer
    rank_score: float                     # provisional; Goal 15 re-ranks
    properties: dict[str, Any]            # the tidied row, for Goal 15's templates

class PassReport(BaseModel):
    which: RetrievalPass; ran: bool; found: int; kept: int
    duration_ms: int; failure: str | None

class RetrievalBundle(BaseModel):
    session_id, turn_index
    candidates: tuple[RetrievedNode, ...]
    passes: tuple[PassReport, ...]
    latency_ms: int
    within_budget: bool          # false → Goal 16 carries it forward
    search_failed: bool          # nothing could be looked up, as opposed to nothing found
    gated: tuple[str, ...]       # node ids withheld by the sensitivity gate
    suppressed_by_crisis: bool
```

`properties` carries the tidied row rather than a hand-picked subset. Goal 15's six
compression templates each need different columns (a pattern's typical trigger, a
belief's date, an open question's wording); re-reading the graph inside a three-second
budget to fetch rows already in hand would be the wrong trade. It is internal to the
query layer — the HTTP surface maps it to a response model, as Goal 11 requires.

## B3. `retrieve()` — the sequence (`retrieval/stage.py`)

```
ConversationalRetriever.retrieve(signal, session) -> RetrievalBundle

 1. signal.should_retrieve is False   → empty bundle, reason recorded, no work
 2. deadline = now + retrieval_budget_seconds
 3. submit Pass A and Pass B to the pool; wait until the deadline
       A: hyde(triggers) -> embed_batch -> per-trigger search -> hydrate
          -> filter -> rank -> keep N.   Returns candidates + the query vector.
       B: for each trigger, its anchor lookups; each contained.
 4. Pass C, in memory, using A's vector (or word overlap if A produced none)
 5. gate: drop CRITICAL records in locked areas, record their ids
 6. merge: dedupe (B beats A beats C), score, order, cut to the cap
 7. buffer.remember(kept, turn_index); buffer.evict_stale(turn_index)
 8. one log line: per-pass counts, gated count, latency, budget met
```

Ordering rationale for step 6's dedupe: an anchor copy knows *why* it was found, which
the semantic copy does not, and that changes how much it should be trusted. Same rule
Goal 8 settled on.

## B4. Pass A (`semantic.py`, `hyde.py`)

- **One HyDE call for all reasons**, batched and aligned by index — a short reply is
  padded with the turn's own text rather than shifted up, because a search run with
  another reason's text returns confident wrong matches, which is worse than none.
- Fallback when the call fails or times out: the turn's own text plus the reason's
  keywords. A worse search, but a real one.
- Embedded as DOCUMENT (same reasoning as Goal 8 — the hypothetical *is* the
  question-to-document correction; labelling it a query applies it twice).
- Overfetch `conversational_pass_a_overfetch`, keep `conversational_pass_a_keep`,
  because ranking happens after the search and a weighty record can sit just below the
  raw-distance cut.
- Filtered by `CONTENT_TABLES` and `RETIRED_STATUSES` (shared with the pipeline), then
  by the reason's own wanted kinds:

```python
WANTED: dict[TriggerType, NodeFilter] = {
  SOMATIC_MARKER:     tables={ObservationNode},
                      observation_types={PHYSIOLOGICAL_CAPACITY_STATE,
                                         SUPPRESSED_EMOTION_SURFACING},
  IDENTITY_STATEMENT: tables={BeliefNode, ObservationNode},
                      observation_types={BELIEF, META_BELIEF, IDENTITY_FUSION_STATE},
  # everything else: unrestricted
}
```

## B5. Pass B (`structural.py`)

A table from reason → lookups, so adding a reason is adding a row:

| Reason | Lookups |
|---|---|
| `NAMED_PERSON` | `get_node(person_id)` → canonical name → `find_linked_to_person(name, PERSON_LINKED_TYPES)` |
| `HISTORICAL_ERA` | `find_by_era(era, ERA_TAGGED_TYPES)` |
| `OPEN_LOOP_MATCH` | `find_nodes(["OpenLoopNode"])` |
| `PROGRESS_CLAIM` | `find_nodes(["OpenLoopNode"])` + `find_nodes(["PatternNode","BeliefNode"], domain=…)` |
| `BELIEF_CHALLENGE` | `find_nodes(["BeliefNode"], domain=…)` |
| others | none — semantic answers them |

The person lookup takes the extra `get_node` hop deliberately: Goal 13 grounds a name
into a *record id*, and the existing graph read is keyed by canonical name. Reading the
record to get its own spelling is one cheap read and keeps both sides using the single
named read they already have, rather than adding a second one that means the same thing.

Every lookup is wrapped so it fails alone. Anchors are additive; losing one should cost
one.

## B6. Pass C (`continuity.py`, `buffer.py`)

```python
@dataclass
class BufferEntry:
    node_id, node_type, preview, signal_strength
    first_seen_turn, last_relevant_turn
    vector: tuple[float, ...] | None      # cached when it entered the buffer
    properties: dict

class SessionContextBuffer:
    max_entries=5, max_idle_turns=5
    def remember(nodes, *, turn_index, vectors) -> None
    def relevant_to(query_vector, keywords, *, turn_index) -> list[tuple[BufferEntry, float]]
    def evict_stale(turn_index) -> list[str]
    @property entries
```

- Relevance = cosine against Pass A's query vector when both vectors exist; word overlap
  against the entry's preview otherwise. Above `session_boost_threshold` it counts.
- A relevant entry already found by A or B → `boosted=True`, `rank_score × 1.3`.
- A relevant entry nobody found this turn → returned as a Pass C candidate.
- Eviction: idle for `max_idle_turns` → out, unless CRITICAL. Full buffer with no
  evictable entry → the new record is returned this turn but not admitted, logged.
- Vectors are fetched once per newly-admitted node via `VectorProvider.get_vectors`, so
  the per-turn cost really is arithmetic.

## B7. Config (`QueryConfig`)

| Field | Env | Default |
|---|---|---|
| `retrieval_budget_seconds` | `LUMEN_RETRIEVAL_BUDGET_SECONDS` | 3.0 |
| `semantic_pass_timeout_seconds` | `LUMEN_PASS_A_TIMEOUT_SECONDS` | 2.0 |
| `structural_pass_timeout_seconds` | `LUMEN_PASS_B_TIMEOUT_SECONDS` | 0.5 |
| `conversational_pass_a_keep` | `LUMEN_CONV_PASS_A_KEEP` | 5 |
| `conversational_pass_a_overfetch` | `LUMEN_CONV_PASS_A_OVERFETCH` | 20 |
| `conversational_pass_b_keep` | `LUMEN_CONV_PASS_B_KEEP` | 5 |
| `conversational_candidate_cap` | `LUMEN_CONV_CANDIDATE_CAP` | 12 |
| `session_buffer_size` | `LUMEN_SESSION_BUFFER_SIZE` | 5 |
| `session_buffer_max_idle_turns` | `LUMEN_SESSION_BUFFER_IDLE_TURNS` | 5 |
| `session_boost_multiplier` | `LUMEN_SESSION_BOOST` | 1.3 |
| `session_boost_threshold` | `LUMEN_SESSION_BOOST_THRESHOLD` | 0.35 |
| `anchor_base_score` | `LUMEN_ANCHOR_BASE_SCORE` | 0.6 |
| `retrieval_max_workers` | `LUMEN_RETRIEVAL_MAX_WORKERS` | 4 |

`anchor_base_score` is a stand-in closeness used *only* for ordering — an anchor match
is not a measurement, so `similarity` stays unset on those candidates and nothing
downstream can mistake a policy number for one.

## B8. Provider amendment

```python
def get_vectors(self, node_ids: list[str]) -> dict[str, list[float]]:
    """The stored vector for each id that has one."""
```

Qdrant: `client.retrieve(ids=[uuid5(...)], with_vectors=True)`. The id derivation is
already `uuid5(NAMESPACE_OID, node_id)` on write, so this is symmetric with `upsert`.

## B9. HTTP surface

`POST /query/retrieve` — read the sentence *and* fetch what it points at, returning
both the signal and the candidates with their per-pass reports. POST for the same
reason Goal 13's formulate route is: the body is somebody's sentence about their own
life and a GET would put it in every access log on the way.

An optional `session_key` makes the day-session persist across calls, which is the only
way to see Pass C work by hand — the buffer is a fact about a conversation, and a
throwaway session per request has none.

The retriever needs a vector store, and a local Qdrant path can only be opened once per
process, so `LazySearchStack` shares the importer's — built on first use, exactly like
the importer's own, so a deployment with no model configured still starts and still
serves every route that does not need one. Its language model is built with retries
disabled, for Goal 13's reason: a call that has already missed a sub-second deadline
gains nothing from being tried again.

## B10. Docs amended ahead of coding

`Conversational_RAG_Mode.md`:
1. Pass C cannot be parallel with Pass A — it consumes A's measurement. Stated, with
   the reason.
2. Where Pass C's numbers come from (cached stored vectors, and the word-overlap
   fallback) — previously unspecified, which made "<20ms, always succeeds" unbuildable.
3. The buffer deadlock: five slots, CRITICAL never evicted. The shipped rule written down.
4. Stage 4 named no sensitive domains and ignored that observations carry no domain at
   all. Both rules written down.
5. `conv_score`'s `recency_weight` marked as Goal 19's, as Goal 8 did for the
   extraction-side formula.
6. Pass A's "<800ms" contains a model call the same document prices at 300–800ms. Budget
   restated as 2s inside the 3s window.
7. `PROGRESS_CLAIM` → "closure detection" was undefined. Defined.

## B11. Test plan (~150 tests)

Against **real Kuzu and real Qdrant**, seeded per test — every question these passes ask
is a query, and a stand-in would agree with whatever it was told.

| File | Covers |
|---|---|
| `test_query_retrieval_semantic.py` | HyDE batching and alignment, fallback, kind filters, ranking, overfetch, index failure |
| `test_query_retrieval_structural.py` | Each anchor, contained failures, the person second hop, era spelling |
| `test_query_buffer.py` | Admission, refresh, idle eviction, CRITICAL protection, full-and-protected |
| `test_query_retrieval_continuity.py` | Boost of a re-found node, re-injection of an unfound one, the overlap fallback |
| `test_query_retrieval_gate.py` | Locked/unlocked, domain-less CRITICAL, non-sensitive CRITICAL |
| `test_query_retrieval_stage.py` | Fan-out, budget, per-pass isolation, crisis, merge order, cap, the log line |
| `test_query_deadline.py` | `run_all` — partial completion, all-timeout, exceptions |
| `test_api_query.py` (extended) | `/query/retrieve`, persistent session, 503 with no model |
| `test_vector_get_vectors.py` | Round-trip, missing ids |

## B12. Build order

1. `graph/rows.py` + re-exports; `query/deadline.py` move + `run_all`; `get_vectors`.
2. Config, enums, contracts.
3. `buffer.py` + `ChatSession` attachment.
4. Pass B (no model needed — testable first).
5. Pass A (`hyde.py`, `prompts.py`, `semantic.py`).
6. Pass C, gate, merge.
7. `stage.py`.
8. API + UI.
9. Docs, Master Plan, Section C.

---

# SECTION C — WHAT WAS ACTUALLY BUILT

## C1. Files

**New:**

| File | What it does |
|---|---|
| `lumen/query/retrieval/stage.py` | `ConversationalRetriever` — fan-out, budget, gate, merge, buffer update, one log line |
| `lumen/query/retrieval/contracts.py` | `RetrievedNode`, `PassReport`, `RetrievalBundle`, `PassAResult`, the HyDE reply shapes, `store_searches` / `consulted_nothing` |
| `lumen/query/retrieval/semantic.py` | Pass A, plus `NodeFilter` / `WANTED` and `SearchUnavailable` |
| `lumen/query/retrieval/structural.py` | Pass B, the `LOOKUPS` table, `has_anchors` |
| `lumen/query/retrieval/continuity.py` | Pass C — `revisit`, `to_entries` |
| `lumen/query/retrieval/gate.py` | The sensitivity gate |
| `lumen/query/retrieval/merge.py` | Dedupe by precedence, boost, order, cut |
| `lumen/query/retrieval/hyde.py`, `prompts.py` | The invented-record call and its fallback |
| `lumen/query/retrieval/hydrate.py` | Row → candidate, in one place for all three passes |
| `lumen/query/buffer.py` | `SessionContextBuffer`, `BufferEntry`, `cosine`, `word_overlap` |
| `lumen/query/deadline.py` | Moved up from `formulation/`, plus `run_all` and `Attempt` |
| `lumen/graph/rows.py` | Row reading shared by both retrieval layers |
| `lumen/api/resources.py` | `LazySearchStack` |

**Amended:** `lumen/query/session.py` (the buffer), `lumen/query/__init__.py`,
`lumen/config.py` (13 knobs), `lumen/schemas/enums.py` (`RetrievalPass`,
`RetrievalOutcome`), `lumen/vector/provider.py` + `qdrant_impl.py` (`get_vectors`,
`_point_id`), `lumen/pipeline/retrieval/{hydrate,semantic}.py` (re-export from
`graph/rows.py`), `lumen/api/{main,deps,schemas}.py`, `lumen/api/routes/query.py`,
`lumen/api/static/chat.html`.

**Tests:** `test_query_retrieval_{stage,semantic,structural,continuity,gate,merge,hydrate,hyde,prompts}.py`,
`test_query_buffer.py`, `test_query_deadline.py`, `test_api_retrieve.py`,
`test_api_resources.py`, `test_vector_get_vectors.py`. `conftest.py` gains
`make_trigger`, `make_signal`, `index_node`, `seed_person`, `seed_open_loop`,
`hyde_replies`, `make_retriever`; `seed_pattern` gains `domain` / `signal`.

## C2. Deviations From the Plan

1. **`buffer.py` sits at `lumen/query/`, not inside `retrieval/`** — as planned, and the
   reason turned out to be load-order rather than taste: `session.py` has to import it, and a
   package-level import would run `retrieval/__init__` → `stage` → `session` while `session`
   was still half-defined.
2. **`RetrievalOutcome` was added** — not in the plan. The bundle needed a single word for
   *why* it is empty, and the four reasons are the whole point of the layer.
3. **`store_searches` / `consulted_nothing` were added** after a test failed (C3, item 2).
4. **`weight_of` and `found_anything` were written and then deleted.** Both were unused;
   testing them would have been testing the tests.

## C3. Things Caught While Implementing

1. **One `contextvars.Context` cannot be entered by two threads at once.** The first version
   of `run_all` copied the context once and handed the same copy to every piece of work, so
   every piece after the first died with `cannot enter context: ... is already entered`. It
   fails *only* under genuine overlap — which is always, for this method — and it surfaces as
   a provider-shaped error, so it would have read as a flaky search rather than a bug here.
   Caught by the test that proves the passes really do run together.
2. **Pass C made "the graph could not be reached" unreachable.** The continuity pass always
   reports success (it is memory, not a store), so a rule of "every attempted pass failed"
   could never be true and a turn where both real searches broke reported `NOTHING` — the one
   answer this layer exists to never give by mistake. Now only passes that had a store *and*
   something to ask it count, which also fixed the quieter half: a trigger with no anchor half
   leaves Pass B with nothing to run, and that is not the same as running and finding nothing.
3. **A crisis turn detected by the distress floor reported `NOT_NEEDED`.** Goal 13 sets
   `suppressed_by_crisis` only when the *model* produced triggers that were then discarded;
   the floor makes no model call at all, so a turn caught by it had nothing to suppress. The
   outcome now reads the register as well, and both routes report `SUPPRESSED`.
4. **Qdrant stores normalised vectors.** `get_vectors` returns unit-length vectors, not what
   the embedder produced, because the collection measures cosine distance. It makes no
   difference to the only use (comparing directions) and it is now stated on the method, since
   the next caller will not expect it.
5. **A `CRITICAL` observation is gated by default, and that surprised a test of ranking.**
   Seeding a weighty observation to check that weight beats closeness produced an empty
   result — because observations carry no domain, and a domain-less CRITICAL record is
   withheld. The gate was working; the test was asking for the wrong thing. Worth recording
   because it is exactly what will happen to somebody tuning retrieval later.

## C4. Honest Limitations

- **Pass C is only as good as the index.** A record written before the vector store existed,
  or during a run whose embedding failed, has no stored position and falls back to word
  overlap. That is visible in the logs rather than silent, but it makes the continuity pass
  weaker on an old graph than the tests suggest.
- **The anchor base score is a policy number.** Ordering a list that mixes measured closeness
  with exact anchor matches requires *some* number for the anchors; 0.6 is a guess that
  wants tuning against a real graph, which is what `/query/retrieve` exists for.
- **Nicknames still do not resolve.** A person is found by an identifier derived from their
  name, so "my sister" reaches no record even when the person behind it has one. Inherited
  from the pipeline, which does not join two spellings of one person either.
- **Sparse/BM25 remains off**, as it has been since Goal 1. "Hybrid" search is dense-only and
  logs a warning when a sparse vector is passed.

## C5. What Is Still Deferred

| Deferred | To |
|---|---|
| The ≤400-token compression and the per-register injection caps | Goal 15 |
| `recency_weight` in the score | Goal 19 |
| Carry-forward when the budget is missed (the bundle *reports* it) | Goal 16 |
| Streaming the injected block into a live chat | Goal 16 |

## C6. Result

3213 tests passing (2822 from Goals 1–13b + 391 new), 20 deselected (the opt-in live
suites). **100% coverage** on `lumen/query/`, `lumen/graph/rows.py`, `lumen/api/` and
`lumen/vector/provider.py`; 99% overall.
