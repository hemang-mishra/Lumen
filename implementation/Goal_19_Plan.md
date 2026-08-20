# Goal 19: Temporal Decay, the Frequency Counter, and Erasure

**Branch:** `goal19`
**Depends on:** Goal 14 (the three retrieval passes and where a candidate is scored),
Goal 15 (the briefing that decides what is actually used), Goal 17 (the ageing bands
this goal makes real), Goal 3 (`data_erasure_audit`, already built and unused),
Goal 1 (the graph and vector providers that must gain write paths)
**Spec:** `docs/Graph/Schema.md` §"Temporal Decay", §"Retrieval Score Formula",
§"Soft Delete / Erasure (DPDP/GDPR Compliance)"; `docs/Extraction/Architecture.md`
§"Extraction Priority Scoring"; `docs/Query/RAGArchitecture.md` §"Query Feedback Loop";
`docs/Extraction/Macroextraction.md` §"Proof Chains"

---

# SECTION A — LOGIC (please verify)

## Objective

Lumen can find things and can talk. Two things it still cannot do are the reason this goal
exists.

**It has no sense of time when ranking.** A belief the person reaffirmed last week and one
they last mentioned three years ago compete on exactly equal terms today. Every goal from 8
onward has left a note saying "time decay is Goal 19's", because inventing a decay curve
early would have meant building it twice. Goal 17 made this visible rather than theoretical:
it now *reports* that a pattern has gone quiet, and prints the multiplier that should be
applied to it — and nothing applies it. The report states a number the system does not use.

**It has no way to forget on request.** The graph is append-only by design, so ordinary
deletion is architecturally impossible. The specification's answer is erasure by
anonymisation: replace the words, keep the structure. The audit table for this was built in
Goal 3 and has never had a single row written to it, because the procedure that writes it
does not exist. Today, if the person asked Lumen to forget them, there is no answer.

This goal finishes the score, closes the feedback loop the score depends on, and builds the
erasure path.

## A1. What Gets Built

| | What it is |
|---|---|
| **The finished score** | Ranking gains three factors it was always specified to have: how recently the record was reinforced, whether the person confirmed it or an AI suggested it, and how often it has proven useful. One shared table of weights, so no two parts of Lumen can disagree about what a record is worth. |
| **The frequency counter** | A counter on each pattern and belief, raised when that record is actually put in front of the assistant. It feeds a small ranking boost, which is what makes the counter worth keeping at all. |
| **Erasure** | Two procedures — forget everything, and forget one entry. Both replace the words, keep the shape of the history, delete the search index entries, clear the raw text out of the working database, and leave a receipt containing no personal information. |
| **Proof chains** | The one report section Goal 17 could not build, because it needs a scan of the whole history rather than a look at one month. A pattern the person has independently rediscovered ten or more times, laid out chronologically. |
| **A maintenance surface** | The endpoints that run these on demand. Putting them on a clock is Goal 20's, as it is for the review queue and the reports. |

## A2. The Decisions Taken

**1. One decay curve, used everywhere, rather than two that nearly agree.**
Goal 17 already ships an ageing rule: quiet past 180 days is "cooling" and worth 0.85, quiet
past a year is "dormant" and worth 0.5. The retrieval specification has its own, finer
version: 1.0 under 30 days, then 0.85, then 0.70, then 0.50. These are the same idea at two
resolutions, and if both ship, a monthly report tells the person a quiet pattern counts for
0.85 while retrieval quietly counts it as 0.70. The report would be wrong about the system
it describes. So there is one curve — the finer one — and the monthly report keeps its band
*labels* ("cooling", "dormant") while reading its *multiplier* from the same function
retrieval uses.

**2. Age costs a record rank; it never removes one.** The floor is 0.5. Nothing decays to
nothing, nothing is deleted for being old, and a record found by an anchor lookup — someone's
name, a period of their life — still arrives even at full age. This matters more here than
anywhere else in Lumen: the whole point of the anchor searches is to reach material that
resemblance would never have found, and half of that material is old on purpose.

**3. Decay applies to every kind of record, not only to beliefs and patterns.**
`Schema.md` says decay applies to beliefs and patterns; `Architecture.md`'s own table says
"age of observation". They cannot both be followed. The query layer retrieves observations,
episodes and lessons too, and decaying the belief while treating the three-year-old note it
came from as though it happened today is not defensible. So the curve applies to everything,
reading the most honest date each kind has: for a belief or pattern, when it was last
reinforced; for everything else, when it happened. **This is a recorded divergence from
`Schema.md`**, not a silent choice.

**4. Something an AI suggested and the person never confirmed counts for half.**
This is the specification's trust weight and it is deliberately blunt. Lumen writes down
insights that came out of a conversation with itself. If those rank equally with things the
person said in their own words, the system slowly starts quoting itself back and calling it
their history. Confirming an insight in the review queue promotes it, which is what makes
Goal 18's queue worth answering.

**5. A record is counted as "used" when it reaches the assistant, not when a search returns
it.** A search returns a dozen candidates and the briefing keeps three. Counting all twelve
would make the counter a measure of what the search engine likes rather than what actually
helped, and that number then feeds back into ranking. Only what was put in front of the
assistant is counted.

**6. At most one count per record per day.** A record that stays relevant for twelve turns of
one conversation is one concern, not twelve. Without this, a single long conversation about
one subject would push that subject's records to the top of the ranking permanently.

**7. Counting happens after the reply has gone out, and a failed count is dropped.**
Nobody waits on bookkeeping. The counter is a convenience, and a lost increment costs a
record a hundredth of a point of ranking; a conversation stalled behind a database write
costs the person the thing they came for.

**8. The frequency boost is capped, because it is a feedback loop.** More retrieved means
more counted means more retrieved. The specification caps the boost at 1.5×, which is what
keeps the loop from running away, and that cap is the reason the feature is safe to ship.

**9. Erasure replaces words, keeps the shape.** Node identifiers, links, dates, types,
versions and decisions all survive; every field a person wrote survives only as
`[ERASED: <date>]`. Names of other people become an unreadable hash. This is what makes
erasure possible in an append-only design at all — and the audit trail continues to prove
what the system did, without saying what it was about.

**10. Erasure clears the working database too, not just the graph.** The specification's
procedure covers the graph and the search index. It does not mention that the person's raw
words are also sitting in the operational database — the conversation buffers, the message
rows, the rolling summaries, the co-reference maps of who was being talked about, and the
review queue's frozen proposals, which by Goal 18's design contain the full text of records
about to be written. **Anonymising the graph alone would leave the person's actual sentences
on disk in three other tables.** This goal erases those too. Recorded as an addition to the
specification.

**11. Erasing one entry cannot un-derive a belief, and says so.** Single-entry erasure
reaches the records that trace back to that entry: its episode, its observations, its events,
its causal steps. A belief built from that entry and nine others is not reached — the
specification scopes it that way and it is the right scope, because a standing belief is not
a copy of any one entry. The audit record names what was left behind rather than implying a
completeness it does not have.

**12. Erasure is irreversible, so it asks first and shows its work.** There is a preview that
counts what would be erased and touches nothing, and the erasure itself requires an explicit
confirmation in the request. It runs in bounded batches so a large history does not hold the
graph's write lock long enough to freeze a live conversation.

## A3. Judgement Calls (flagging, not asking)

- **Erasure runs in the foreground of its own request, not as a background job.** The
  specification calls it asynchronous. Until Goal 20 brings a real worker, "asynchronous"
  would mean a thread nobody can observe, and the one operation in Lumen that cannot be
  retried safely should not be the first thing to run somewhere nothing can see it. It is
  a request that takes a while and reports what it did. Goal 20 can move it.
- **A failed erasure leaves a `FAILED` audit row and does not roll back.** Partial
  anonymisation is not a corrupt state — it is less content than before, which is the
  direction the person asked for. Reversing it would restore words they asked to have
  removed. The row records what was done so the sweep can be re-run.
- **Proof chains pick their five examples by spread across time, not by "most distinct".**
  The specification asks for the five most distinct instances without defining distinctness.
  Anything a model chose here would be unrepeatable. Spreading the five across the years the
  pattern spans is computable, checkable by hand, and directly serves what the section is
  for: showing that this has been true for a long time in different circumstances.
- **The frequency counter exists on beliefs and patterns only.** That is where the column is,
  and it is where it belongs — the counter is about standing ideas, not individual notes.
  Everything else is skipped silently rather than being an error.

## A4. What Is Deliberately Not Built

| Not built | Why |
|---|---|
| Emotional valence time-series | Goal 17 deferred it here. Nothing has changed: no observation in Lumen carries a mood score, so every point on that chart would have to be invented by a model and then drawn as though it were a measurement. It needs a per-observation valence produced at extraction time — a change to Stage 1, not a maintenance job. Raised for its own goal. |
| Prospective memory (predicted triggers) | Goal 17 deferred it here. It is a forecasting feature with no ground truth to check it against, and it predicts things about a person's next week. It does not belong in the goal that makes the ranking honest. Moved to `ROADMAP.md`. |
| Automatic retention-policy erasure | The audit record already has a value for it. Deciding that data expires on a timer is a product policy nobody has set, and the machinery to run it on a clock is Goal 20's. |
| Undo of an erasure | Impossible by construction, and stated as such. |

## A5. How You'll Know It Works

1. **The named test.** A belief last reinforced 400 days ago and an identical one reinforced
   yesterday, matched equally well by the same question: the old one's score is exactly half
   the new one's, and it is still in the results.
2. Confirming an AI-suggested insight in the review queue visibly raises where it ranks the
   next time it is relevant.
3. Ask about the same subject on twelve turns of one conversation; the record's counter goes
   up by one, not twelve.
4. A monthly report and the live ranking state the same multiplier for the same quiet
   pattern.
5. Preview an erasure and see counts with nothing changed; run it and find every readable
   field replaced, every link and date intact, the search index empty of those records, the
   conversation buffers cleared, and one audit row that contains no name and no sentence.
6. Erase a single entry and find its episode anonymised while an unrelated one is untouched.

---

# SECTION B — LOW-LEVEL DESIGN

## B1. Module Layout

```
lumen/graph/scoring.py                 ← every weight, pure, no imports from query or pipeline
lumen/erasure/__init__.py              ← exports ErasureService only
lumen/erasure/contracts.py             ← ErasureRequest, ErasurePlan, ErasureReport
lumen/erasure/targets.py               ← what would be erased (read-only)
lumen/erasure/redact.py                ← the replacement values (pure)
lumen/erasure/runner.py                ← the only writer; owns the audit row
lumen/erasure/service.py               ← the narrow surface the web layer holds
lumen/query/frequency.py               ← collecting this turn's hits, flushing after the reply
lumen/pipeline/macroextraction/proof.py ← proof chains (whole-history scan)
lumen/api/routes/maintenance.py        ← erasure + proof endpoints
```

Modified: `lumen/query/retrieval/hydrate.py`, `.../continuity.py`, `.../contracts.py`,
`lumen/query/assembly/stage.py`, `lumen/query/chat/engine.py`, `lumen/graph/rows.py`,
`lumen/graph/provider.py`, `lumen/graph/kuzu_impl.py`, `lumen/vector/provider.py`,
`lumen/vector/qdrant_impl.py`, `lumen/operational/repositories.py`, `lumen/config.py`,
`lumen/pipeline/macroextraction/{aging,contracts,assemble,narrative,prompts}.py`,
`lumen/api/{main,resources}.py`.

## B2. `lumen/graph/scoring.py` — the weights

Lives in `lumen/graph/` for the same reason `SIGNAL_WEIGHT` does: both the extraction
pipeline and the query layer rank records, and two layers disagreeing about what a record is
worth is the failure this placement prevents. Pure functions over plain values; no provider,
no clock of its own.

```python
SIGNAL_WEIGHT: dict[SignalStrength, float]      # moved here from rows.py
TRUST_WEIGHT:  dict[VerificationStatus, float]  # IMPLICIT 1.0, VERIFIED 1.0, UNVERIFIED 0.5

def recency_weight(last_seen: datetime | None, now: datetime, *, config: ScoringConfig) -> float
def trust_weight(row: Mapping[str, Any], *, config: ScoringConfig) -> float
def frequency_weight(row: Mapping[str, Any], *, config: ScoringConfig) -> float
def age_band(last_seen, now, *, config) -> PatternAgeBand | None   # FRESH/COOLING/DORMANT
def last_seen_of(row: Mapping[str, Any]) -> datetime | None
def final_score(base: float, row, *, now, config) -> float
```

- `last_seen_of` resolves the date per kind, in order:
  `last_reinforced_at` → `occurred_at` → `valid_from` → `created_at`. A belief uses the
  first; an observation falls through to the second. A row with no readable date returns
  `None` and `recency_weight` answers `1.0` for it — the cautious direction, since a missing
  date must never be read as "very old".
- `recency_weight` uses `(now - last_seen).days` and the four bands. Negative ages (a record
  dated in the future, which imports can produce) clamp to `1.0`.
- `trust_weight` reads `verification_status`; an unreadable or absent value is `IMPLICIT`
  (1.0), matching `signal_of`'s existing "a missing value can only fail to promote".
- `frequency_weight` = `min(1.0 + step × query_frequency, cap)` with `step` 0.1 and `cap`
  1.5. A missing or non-integer counter is 0.
- `final_score(base, row, ...)` = `base × SIGNAL_WEIGHT × recency × trust × frequency`.
  `base` is the cosine for a measured match and the anchor base score for an anchor match.
- `rows.py` keeps `SIGNAL_WEIGHT` as a re-export so Stage 2's imports do not move. Stage 2's
  scoring is unchanged — it stays cosine × signal, as `Architecture.md` requires.

## B3. Applying it in retrieval

Two call sites, because there are exactly two places a `RetrievedNode` is born:

1. **`hydrate.to_node(...)`** gains a required `now: datetime` and a `config: ScoringConfig`.
   `rank_score` becomes `scoring.final_score(starting, row, now=now, config=config)`.
   Callers (`semantic.py`, `structural.py`) already have the turn's clock.
2. **`continuity._as_candidate(...)`** applies the same helper to `entry.properties`, then
   multiplies by `session_boost_multiplier` as it does today. A record carried from earlier
   in today's conversation is decayed on the same terms as one found fresh — otherwise the
   buffer would become a way to smuggle a stale record past the curve.

`RetrievedNode` gains four read-only fields for observability, all defaulted so nothing
existing breaks: `recency_weight`, `trust_weight`, `frequency_weight`, `age_band`. Ranking
already happens in the score; these exist so the debug surface can show *why* something
ranked where it did, which is otherwise unrecoverable from one number.

`assembly/stage.py::_ordered` keeps its recency tie-break, and its docstring — which
currently says a decay curve is "a question with its own goal" — is corrected to say the
curve is now in the score and this remains only a tie-break.

## B4. `lumen/query/frequency.py` — the counter

```python
class QueryHitRecorder:
    def __init__(self, graph: GraphProvider, *, config: QueryConfig) -> None
    def note(self, session: ChatSession, context: AssembledContext, *, at: datetime) -> int
```

- Reads `context.items` — what actually reached the assistant — and takes their `node_id`s.
- Filters against `session.claim_query_hits(ids)`, a new method on `ChatSession` backed by a
  `set[str]` field with the same lifetime as everything else on the session: it dies at
  midnight with the day. Returns only the ids not already counted today, and marks them.
- Calls `graph.record_query_hits(remaining, at=at)` once, in one transaction.
- Every failure is caught and logged at warning. Returns the number counted, for the log line
  and for tests.
- Called from `ChatEngine._tidy_up`, immediately before the memory refresh, for the same
  stated reason that refresh is there: the reply has already gone out.

## B5. Erasure contracts (`lumen/erasure/contracts.py`)

```python
class ErasureScope(StrEnum):        # in schemas/enums.py, beside HitlEntryType
    ALL = "ALL"
    ENTRY = "ENTRY"

class ErasureRequest(BaseModel):    # frozen, extra="forbid"
    user_id: str
    scope: ErasureScope
    entry_id: str | None = None     # required when scope is ENTRY, rejected when ALL
    initiated_by: ErasureInitiator = USER_REQUEST
    confirmation: str               # must equal config.erasure_confirm_phrase

class ErasurePlan(BaseModel):       # what a preview returns; touches nothing
    scope, entry_id
    node_ids_by_table: dict[str, int]
    total_nodes: int
    vectors: int
    operational_rows: dict[str, int]
    not_reached: tuple[str, ...]    # e.g. "standing beliefs derived from this entry"

class ErasureReport(BaseModel):
    audit_id: str
    status: ErasureStatus
    nodes_anonymized: int
    embeddings_deleted: int
    operational_rows_cleared: int
    entry_ids_affected: tuple[str, ...]
    failures: tuple[str, ...]
```

## B6. `targets.py`, `redact.py`, `runner.py`, `service.py`

**`targets.py`** (read-only, no writes anywhere in it)
- `all_node_ids(graph, *, batch)` — pages every table via the new
  `GraphProvider.iter_node_ids`. Not `find_nodes`: that one merges and re-sorts every table
  on each page, so paging a whole graph through it is quadratic.
- `entry_node_ids(graph, entry_id, *, batch)` — the episode whose `entry_id` matches, then
  every node whose `episode_id` is one of those episodes. Two narrow reads; no traversal.
- `operational_targets(store, user_id, *, entry_id)` — the rows holding raw text:
  `buffer_messages.content`, `session_buffers.rolling_summary`, `coreference_maps.mapping`,
  `hitl_proposals.payload`, `hitl_queue` summary columns, `imports.title`. Full scope adds
  `user_settings` (the persona the person wrote).

**`redact.py`** (pure; no clock, no store)
- `ERASED = "[ERASED: {iso_date}]"`, applied to `content`, `belief_statement`,
  `pattern_name`, `pattern_description`, `lesson_statement`, `loop_description`,
  `raw_evidence`, `episode_summary`, `contradiction_summary`, `session_summary`,
  `event_summary`, `chain_summary`, `principle_statement`, `delta_description`,
  `hitl_resolution_user_choice`, `report_content`.
- `person_placeholder(name)` → `[ERASED_PERSON_{sha256(name)[:8]}]`; aliases →
  `["[ERASED_ALIAS]"]`.
- One table → column map, `ERASABLE_COLUMNS: dict[str, tuple[str, ...]]`, derived from
  `NODE_TABLES` at import time and asserted complete by a test, so a node type added later
  cannot silently keep its text through an erasure.

**`runner.py`** — the only writer.
1. Write the audit row `IN_PROGRESS` first, so a crash mid-sweep still leaves a trace.
2. Anonymise the graph in batches of `erasure_batch_size` (200), each its own transaction —
   the provider's write lock is held per batch, not for the whole sweep, so a live
   conversation stalls for milliseconds rather than minutes.
3. Delete the vectors for the same ids, in the same batches.
4. Clear the operational rows.
5. Update the audit row to `COMPLETE` with the counts, or `FAILED` with what broke.
   `entry_ids_affected` is capped at the first 500 ids, since the audit table must not become
   the largest record of what existed.

**`service.py`** — `ErasureService.preview(request) -> ErasurePlan` and
`.erase(request) -> ErasureReport`. Validates the confirmation phrase and the
scope/`entry_id` pairing, holds a `threading.Lock` so two erasures cannot interleave (the
same shape `MacroService` and `ReviewService` already use), and is the only thing the API
imports.

## B7. Provider changes

**`GraphProvider`** — three additions, all narrow by construction. No caller ever supplies a
column name; the columns are fixed in the implementation, which is what keeps the existing
"a mistyped id cannot reach content" property true.

| Method | What it does |
|---|---|
| `record_query_hits(node_ids, *, at) -> int` | `SET n.query_frequency = n.query_frequency + 1` over `PatternNode` and `BeliefNode` only, one statement per table with an `IN` list. Ids belonging to other tables are skipped, not an error. |
| `anonymize_nodes(node_ids, *, at) -> int` | Per table, sets that table's erasable columns to the placeholder. `PersonEntityNode` takes the hashed-name path. Returns the number of rows actually touched. |
| `iter_node_ids(table, *, after=None, limit) -> list[str]` | Keyset paging by `node_id` for one table. The only read that can walk the whole graph without re-sorting it per page. |

`_BOOKKEEPING_TABLES` gains entries for `record_query_hits`; `anonymize_nodes` deliberately
does *not* go through `_table_for` (it is allowed everywhere, and reads the label per batch).

**`VectorProvider.delete(node_ids) -> int`** — Qdrant delete by point id. Missing ids are not
an error: a node whose vector was never written is already in the state erasure wants.

## B8. Store changes

- `ErasureRepository`: `start(record) -> str`, `finish(audit_id, *, status, counts)`,
  `list_for_user(user_id, limit)`. The existing `record`/`get` stay.
- Each content-owning repository gains one `purge_*` method rather than erasure reaching into
  tables it does not own: `SessionBufferRepository.purge_content(user_id, session_ids=None)`,
  `CoreferenceRepository.purge(...)`, `HitlQueueRepository.purge_content(...)`,
  `ImportRepository.purge_content(...)`, `SettingsRepository.purge(user_id)`.
- No migration. Every table already carries `user_id`.

## B9. Proof chains (`lumen/pipeline/macroextraction/proof.py`)

- `find_proof_chains(graph, *, config) -> list[ProofChain]`, called by the runner for
  MONTHLY and QUARTERLY reports only — it is a whole-history scan and does not belong in a
  weekly.
- For each `ACTIVE` pattern and lesson: count distinct episodes reachable through
  `reinforces_*` and `branches_to_*` edges. Keep those with `>= proof_min_instances` (10).
- `key_instances`: five, chosen by spreading across the span — the first, the last, and three
  at even intervals of elapsed time between. Computable, stable, re-derivable by hand.
- `chain_summary` follows Goal 17's rule exactly: Python produces the count, the span in
  years, and the settings; the model is given only those and asked for one sentence, and
  every episode id it returns is checked against what it was shown. A model failure costs the
  sentence, not the chain.
- `ProofChain` joins `ComputedFacts`; `assemble.py` and the report templates gain the
  section.

## B10. `aging.py` — the consolidation

`age_patterns` keeps its shape and its band labels, and changes in one place:
`weight_multiplier` is now `scoring.recency_weight(last_seen, ends_at, config=scoring_config)`
and the band comes from `scoring.age_band`. `MacroConfig.cooling_days`, `dormant_days`,
`cooling_multiplier` and `dormant_multiplier` are removed; the thresholds live on
`ScoringConfig`. **This amends Goal 17** and its tests, and the environment variables
`LUMEN_MACRO_COOLING_*` / `LUMEN_MACRO_DORMANT_*` are replaced by `LUMEN_DECAY_*`.

## B11. Config

```python
@dataclass(frozen=True)
class ScoringConfig:
    decay_enabled: bool  = _env_bool("LUMEN_DECAY_ENABLED", True)
    fresh_days: int      = _env_int("LUMEN_DECAY_FRESH_DAYS", 30)
    cooling_days: int    = _env_int("LUMEN_DECAY_COOLING_DAYS", 180)
    dormant_days: int    = _env_int("LUMEN_DECAY_DORMANT_DAYS", 365)
    cooling_weight: float  = _env_float("LUMEN_DECAY_COOLING_WEIGHT", 0.85)
    stale_weight: float    = _env_float("LUMEN_DECAY_STALE_WEIGHT", 0.70)
    dormant_weight: float  = _env_float("LUMEN_DECAY_DORMANT_WEIGHT", 0.50)
    unverified_weight: float = _env_float("LUMEN_TRUST_UNVERIFIED", 0.5)
    frequency_step: float  = _env_float("LUMEN_FREQUENCY_STEP", 0.1)
    frequency_cap: float   = _env_float("LUMEN_FREQUENCY_CAP", 1.5)
    frequency_enabled: bool = _env_bool("LUMEN_FREQUENCY_ENABLED", True)

@dataclass(frozen=True)
class MaintenanceConfig:
    erasure_batch_size: int = _env_int("LUMEN_ERASURE_BATCH", 200)
    erasure_confirm_phrase: str = _env("LUMEN_ERASURE_CONFIRM", "ERASE")
    proof_min_instances: int = _env_int("LUMEN_PROOF_MIN_INSTANCES", 10)
    proof_key_instances: int = _env_int("LUMEN_PROOF_KEY_INSTANCES", 5)
```

Both join `AppConfig`. `decay_enabled: False` makes every weight 1.0, which is what makes an
A/B against today's ranking possible without a code change.

## B12. API (`lumen/api/routes/maintenance.py`)

| Method | Path | What |
|---|---|---|
| `GET` | `/maintenance/erasure/preview` | `?scope=&entry_id=` → `ErasurePlan`. Touches nothing. |
| `POST` | `/maintenance/erasure` | Body is `ErasureRequest`. 400 on a wrong confirmation phrase, 404 on an unknown entry, 409 while another erasure is running. Returns `ErasureReport`. |
| `GET` | `/maintenance/erasure/audits` | The receipts, hashed ids only. |
| `POST` | `/maintenance/proof-chains` | Runs the whole-history scan and returns what it found, without writing a report. |
| `GET` | `/debug/score/{node_id}` | The four weights for one record against now — the fastest way to answer "why did this rank there". |

The specification's `DELETE /users/{user_id}/data` is deliberately not the shape used: every
route in Lumen reads `config.user_id` until Goal 21, and a path that names a user id it does
not enforce would read as multi-user support that does not exist. Recorded as a divergence;
Goal 21 moves it to the identity-bearing form.

## B13. Docs To Amend (before coding, per the doc-first rule)

| Doc | Change |
|---|---|
| `Graph/Schema.md` | Decay applies to all live content records with the per-kind date fallback (divergence); the frequency boost stated as part of the score; erasure gains the operational-database step and the partial-erasure limit. |
| `Extraction/Architecture.md` | The layer-split table: recency and trust now shipped, not pending; the "age of observation" row reconciled with the decay section. |
| `Query/Conversational_RAG_Mode.md` | `conv_score` gains `recency × trust × frequency`; the note saying decay is Goal 19's is replaced by the formula. |
| `Query/RAGArchitecture.md` | The counter is raised at injection, once per record per day. |
| `Extraction/Macroextraction.md` | The ageing multiplier is the retrieval curve; proof chains as built; valence and prospective memory stated as not built, with reasons. |
| `ROADMAP.md` | Emotional valence and prospective memory recorded where they actually belong. |

## B14. Tests

| File | Covers |
|---|---|
| `test_graph_scoring.py` | Every band boundary at its exact day, the missing-date and future-date paths, the trust table, the frequency cap, and `final_score`'s composition. |
| `test_query_decay.py` | **The named test:** two otherwise identical beliefs 400 days apart score exactly 2:1, and both are returned. Plus: an anchor match survives full decay; the continuity buffer decays on the same terms. |
| `test_query_frequency.py` | Counted only from `context.items`; once per day across twelve turns; a graph failure logs and does not raise; a new day counts again. |
| `test_erasure_targets.py` | Full and single-entry target sets against real Kuzu; an unrelated episode untouched. |
| `test_erasure_redact.py` | Every table's erasable columns; the completeness assertion against `NODE_TABLES`; the person hash is stable and irreversible. |
| `test_erasure_runner.py` | Counts, the audit row's two transitions, batching, a mid-sweep failure leaving `FAILED`, structure and dates intact afterwards, vectors gone. |
| `test_macro_proof.py` | The 10-instance threshold, the five-instance spread, a model failure costing only the sentence, an invented episode id rejected. |
| `test_api_maintenance.py` | Preview changes nothing; wrong phrase is 400; concurrent erasure is 409. |
| `test_macro_aging.py` (amend) | The reported multiplier equals `scoring.recency_weight` for the same record. |

Target ≥90%; the recent goals have held 99–100% on new packages and this one should too —
every module here except the runner is pure.

## B15. Build Order

1. Docs (B13) — the divergences are agreed before any code depends on them.
2. `ScoringConfig`, `MaintenanceConfig`, `lumen/graph/scoring.py` + its tests.
3. `aging.py` consolidation and the Goal 17 test amendments — proves the shared curve before
   anything else uses it.
4. Retrieval application (B3) and the 400-day test.
5. `record_query_hits` on the provider, then `frequency.py`, then the engine hook.
6. `VectorProvider.delete`, `anonymize_nodes`, `iter_node_ids`.
7. `lumen/erasure/` bottom-up: contracts → redact → targets → runner → service.
8. Store purge methods.
9. Proof chains.
10. API routes and the debug score endpoint.
11. `Master_Plan.md` checkbox, result line, and Section C of this document.

## B16. Deferred, and to Where

| Deferred | To |
|---|---|
| Running erasure and the proof scan on a clock | Goal 20 (the scheduler, as for reports and the queue) |
| `DELETE /users/{user_id}/data` in its identity-bearing form | Goal 21 |
| Erasure scoped to one user's subgraph | Goal 22 — the graph has no user column until then |
| Emotional valence time-series | Its own goal; needs a valence score produced at extraction |
| Prospective memory | `ROADMAP.md` |
| A front-end erasure surface | Phase 8 |

---

# SECTION C — WHAT WAS ACTUALLY BUILT

Section B was the plan. Six things came out differently, and each is here with the reason.

## C1. The weights live beside the row reader, not on top of it

B2 said `SIGNAL_WEIGHT` would move into `scoring.py` and be re-exported from `rows.py`.
It stayed in `rows.py` and `scoring.py` imports it. The reason is a cycle: `scoring.py`
needs `rows.py` to read a stored date back, so `rows.py` cannot depend on `scoring.py`.
The dependency runs one way — rows knows how to read a row, scoring knows what a row is
worth — and every existing import is untouched.

Three date helpers moved *into* `rows.py` on the way (`read_moment`, `as_utc`,
`last_seen_at`). Three places were parsing stored timestamps with three private copies of
the same function; a fourth was about to be written.

## C2. One vocabulary meant extending the band enum, not keeping two sets of labels

Section A promised the report would "keep its band labels" while reading its multiplier
from the shared curve. That would have left two vocabularies for one curve: a report saying
`COOLING` where the ranking said `STALE`, differing by a factor the reader cannot see.

`PatternAgeBand` gained `FRESH` and `STALE`, so there are four bands and one set of words.
The report still mentions only patterns quiet beyond its own threshold, so in practice it
prints `STALE` where it used to print `COOLING` — and now prints the multiplier that is
actually applied. **This amends Goal 17** and its tests.

## C3. The redaction rules belong to the graph, not to the erasure package

B6 put `ERASABLE_COLUMNS` in `lumen/erasure/redact.py`. But `KuzuGraphProvider` has to
implement `anonymize_nodes`, and a provider importing an application package inverts the
dependency the whole codebase is built on. Which columns of which table hold a person's
words is a fact about the schema, so it lives in `lumen/graph/redaction.py` beside
`rows.py`, and both the provider and the erasure package read it.

It also grew past a table-to-columns map. Three kinds of column cannot be treated alike:
a plain one becomes a marker, a list has to stay a list (a column that should hold a list
holding a sentence is a problem for every later reader), and `lifecycle_history` holds
*records* whose shape must survive while their free text goes. `replacements_for` answers
all three in one place, so the provider never has to know which is which, and
`needs_the_row` says whether a batch can be rewritten in one statement or has to be done
row by row.

## C4. Erasure has to work with no model configured

Not anticipated at all. The index handle in the API is opened through the ingest stack,
which builds an embedder and two language models on the way. That makes erasure — the one
thing somebody is entitled to do whatever else is broken — refuse to start on a deployment
whose credentials have expired.

`open_index` now opens the index alone, `build_resources` borrows an already-open one
rather than opening a second (a file-backed index takes a lock), and `LazySearchStack.index()`
hands out the index without a model. Verified against a running application with no
credentials present: preview, erase, audits, proof-chains and the score explainer all
answer.

## C5. The proof-chain sentence is composed, not narrated

B9 said the model would write `chain_summary` from the counted figures. It is built in
Python instead. It is the same sentence every time with two numbers changed: a model would
reword it in every report while adding nothing, and could reach for detail the arithmetic
never established — the spec's own example names the settings ("in the gym, during
internships"), which nothing in the graph records. This follows the precedent already in
the same package: `RE_INTERROGATION_PROMPT` is a fixed string for exactly this reason.

Lessons also turned out to need a different route. There is no `LessonNode` edge in
`EDGE_REGISTRY` at all — a lesson names its evidence in `evidence_episodes` on itself. So
patterns are traced through links and lessons through the column, which is a fact about
how lessons are written rather than a special case invented here.

## C6. Smaller corrections

- **The continuity pass never applied `signal_weight`.** Found while threading the weighting
  through: a record carried from earlier in today's conversation was ranked on closeness
  alone, so the same record scored differently depending on which search found it. It now
  goes through the same weighting as everything else. *Amends Goal 14.*
- **`VectorProvider.delete` reports what was removed, not what was asked about.** The first
  version returned the number of ids passed in. That number goes into a compliance record,
  and one that claims more than happened is worse than none.
- **One moment for the whole erasure.** The operational purges first used their own clock,
  so a single erasure stamped the graph with one date and the message rows with another.
  `at` is threaded through every purge.
- **One moment for the whole turn.** `retrieve()` takes `now`, fixed once and handed to all
  three searches, so a conversation running over midnight cannot rank two identical records
  differently for no visible reason.
- **`find_nodes` was the wrong read for a sweep.** It queries every table, merges and
  re-sorts on each page, so paging a whole history through it gets slower the further it
  goes. `iter_node_ids` pages one table by "everything after this id".
- **A record dated in the future is not decayed.** Imports can produce these; a clock
  disagreement is not a fact about a record.

## C7. The inspection page, which B12 forgot

Goals 17 and 18 each shipped one and the plan did not, which would have left the only
irreversible operation in the system reachable through `curl` and nothing else.
`maintenance.html` holds all three jobs: the score explainer, the whole-history scan, and
erasure — with the preview as the obvious button and the erase button styled as the one
thing here that cannot be taken back. Two tests were added while doing it: every page must
exist, and every page must link to every other, because a page nothing links to is one
nobody finds.

## C8. Result

4566 passing (141 new), with the new modules covered by
`test_graph_scoring.py`, `test_graph_redaction.py`, `test_query_decay.py`,
`test_query_frequency.py`, `test_erasure.py`, `test_macro_proof.py` and
`test_api_maintenance.py`. Coverage on the new code: `scoring.py`, `redaction.py`,
`frequency.py`, `aging.py` and `targets.py` at 100%, `proof.py` at 99%, the erasure package
at 98%.

*A note on measuring the route module.* Coverage cannot be measured for any single API test
file in this environment — numpy refuses to import twice in one process under `pytest-cov`,
which fails every test using the `api_client` fixture. This predates Goal 19: an untouched
file (`test_api_graph.py`) fails the same way. The routes are covered by a whole-suite run.

The named acceptance test passes: two beliefs saying the same thing at the same distance
from the same question, one reaffirmed yesterday and one untouched for 400 days — the older
scores exactly half, and is still returned.
