# Goal 12: Multi-Session Integrity

**Branch:** `goal12`
**Status:** ✅ Complete
**Depends on:** Goal 10 (the pipeline runs end to end) ✅, Goal 11 (the graph can be read) ✅
**Blocks:** Goal 17 (macroextraction), and the confidence to build the query layer at all

---

## Objective

Every test so far has processed one entry. The whole point of the system is what happens
across *many*.

A person writes about the same struggle on Monday, Wednesday and Friday, in different
words each time. The graph should end up holding one pattern with three pieces of evidence
behind it. The failure that matters — the one nothing so far could have caught — is that
it holds three patterns with one piece of evidence each. That graph looks perfectly
healthy from the inside. Every node is valid, every link is correct, every decision is
recorded. It is simply useless, because nothing in it has accumulated into anything.

This goal feeds five consecutive days through the pipeline and proves the accumulation
actually happens.

---

# SECTION A — LOGIC (please verify)

*Short by design. This is what the goal means; Section B is how it's coded.*

## A1. What Gets Built

| Deliverable | What it is |
|---|---|
| **A five-day journal** | A written-out week of entries about the same handful of themes, worded differently each day, with a deliberate arc through them. |
| **A way to run it** | One call that feeds those days through the real pipeline into real databases, in order, the way five real days would arrive. |
| **The integrity checks** | Four properties that must hold after all five days, each one a way the graph can quietly rot. |
| **A stand-in that can recognise a theme** | Today's fake embedder turns text into a hash, so two entries about the same thing land nowhere near each other. That makes cross-day recall impossible and would make this whole test prove the opposite of what it should. |

## A2. The Decisions Taken

1. **The corpus ships in the package, not in the test folder.** It is a fixture with a
   second job: Phase 3's stated objective is to "manually inspect the graph", and until
   now there has been no way to get anything into one worth looking at. Shipping it means
   anyone can populate a graph in one command and open it in the Goal 11 API. Goal 4 set
   the same precedent when the fake models shipped in the package rather than in tests.

2. **The stand-in embedder learns to recognise a theme.** The current one hashes text, so
   "the comparing is what hurts" and "I did it again with Priya's promotion" are as far
   apart as any two random sentences. Under that embedder the pipeline *must* fragment, and
   a test built on it would either pass for the wrong reason or fail for one. The new one
   places text near other text about the same declared theme. It is still a stand-in — it
   is not judging meaning, it is being told the theme — and the plan says so plainly.

3. **Patterns and beliefs become findable by who they are about.** Today the "what do I
   know about Alex" lookup returns only individual notes, never the standing patterns those
   notes produced. Goal 11 built the second hop and nothing calls it. Wiring it in gives
   cross-day recall a second route that does not depend on wording at all — which is the
   route that works when somebody describes the same thing in completely different words.

4. **Five days, one designed arc, each day with a job.** Not five variations on one entry
   and not five unrelated ones. Day by day: something new appears, it happens again, it is
   said differently, it changes, and something finally contradicts it.

5. **The checks read the graph through the Goal 11 API where they can.** That surface was
   built precisely to answer "did this pattern fragment". Using it here means the test
   exercises what a person would actually use, and any gap in it shows up as an awkward
   test rather than as a discovery six months later.

6. **Running the same five days twice must produce the same graph.** Stated as a check
   rather than assumed. A pipeline that quietly depends on timing or ordering is one that
   cannot be debugged.

## A3. The Five Days, and What Each Should Prove

| Day | What is written | What should happen | What it proves |
|---|---|---|---|
| 1 | Comparing himself to Alex after seeing his work | A pattern is created for the first time | The starting point exists |
| 2 | The same feeling, different situation, same person | The existing pattern gains evidence | **Accumulation** — it did not create a second pattern |
| 3 | The same theme in completely different words, no names | It still finds the pattern | Recall does not depend on wording |
| 4 | He realises the comparison is about pace, not ability | The belief changes into a new version | **Version chains** — the old one is kept and marked superseded |
| 5 | Two separate topics in one sitting, one contradicting Day 4 | Both are recorded and ordered; the clash is flagged | **Ordering within a day**, and that a contradiction is not silently merged |

## A4. The Four Integrity Properties

These are the four ways the graph rots without anything looking wrong.

1. **Nothing fragmented.** One theme across five days is one standing record, not five. The
   check counts them.
2. **Evidence accumulated.** That record's evidence count and last-seen date moved on the
   days it should have, and did not on the days it should not.
3. **Version chains join up.** Every version points at the one before it, exactly one is
   current, and the older ones are kept rather than overwritten.
4. **Nothing is orphaned.** Every record traces back to the entry that produced it, the run
   that wrote it, and the decision that placed it there.

## A5. The Honest Limitation

**The models are stand-ins, so this proves the plumbing, not the judgement.**

What the test shows is that *when the model says these things*, the graph accumulates
correctly — the retrieval finds the right prior record, reconciliation reaches the right
action, the write plan builds it, and the counters move. That is the whole machinery
between "a model made a decision" and "the person's history is now different", and none of
it has ever been exercised across more than one entry.

What it does not show is that a real model would say those things. Whether a real reading
of Day 3 recognises Day 1 is a question about prompts and models, and it is answered by
running the corpus against real providers — which the plan includes as an opt-in test,
excluded from the normal suite for the same reason every other live test is.

## A6. What This Goal Deliberately Leaves Undone

| Not built | Where it goes | Why |
|---|---|---|
| Anything that fixes fragmentation if it is found | A follow-up goal | Finding it is this goal's job. Fixing it may be a prompt change, a threshold change, or a retrieval change, and which one it is cannot be known in advance. |
| Cross-day episode ordering edges | Not planned | The spec is explicit that episode ordering is within a day; across days the ordering is the date. |
| Concurrency — two entries processed at once | Later | Deciding is explicitly not parallel-safe, and the personal build processes one entry at a time. |
| Performance limits | Later | Five days is a correctness fixture, not a load test. |
| Cross-entry person merging | Later | "My mentor" and "Alex" are still two people. Unchanged since Goal 9. |

## A7. The Risk Worth Naming

**A test this large can pass while proving nothing.**

Five days, real databases, dozens of assertions — it looks thorough, and it would still be
hollow if the corpus were built to match whatever the pipeline currently does. The
temptation, when Day 3 does not find Day 1's pattern, is to reword Day 3 until it does.

The guard is that the corpus is written first, as five plausible journal entries with the
intended outcome recorded beside each, and that any change to it after the fact is
recorded in Section C as a deviation with the reason. If Day 3 has to be reworded to pass,
that reword *is* the finding, and it belongs in the results rather than being quietly
absorbed.

## A8. Definition of Done

- [x] Five days of entries run through the real pipeline into real databases, in order.
- [x] One theme across five days produces one standing record, and the count is asserted.
- [x] Evidence accumulates on the days it should, and not on the days it should not.
- [x] A version chain of two links up, with exactly one current version.
- [x] Every record traces back to its entry, its run, and its decision.
- [x] Running the same five days twice produces the same graph.
- [x] `python -m lumen.simulation` fills a graph in one command.
- [x] Every behaviour the corpus exposed is recorded in Section C, including all three
      changes made to the corpus after the fact.
- [x] 2010 tests passing; **100% coverage** on `lumen/simulation/`.

---

# SECTION B — LOW-LEVEL DESIGN

## B1. Files

```
lumen/simulation/
├── __init__.py        ← exports the corpus and simulate_days()
├── corpus.py          ← the five days: text, themes, and what each model should say
├── themes.py          ← ThemedEmbeddingProvider — a stand-in that clusters by theme
└── runner.py          ← simulate_days(): feeds the days through the real pipeline

lumen/tests/
├── test_simulation_corpus.py     ← the corpus itself is well-formed and honest
├── test_simulation_themes.py     ← the themed embedder behaves as claimed
└── test_multi_session_integrity.py  ← the four properties, after five real days
```

Nothing new in `lumen/pipeline/`. One line changes in `lumen/pipeline/retrieval/structural.py`.

## B2. The Corpus (`corpus.py`)

Each day is one object holding the entry as written, the themes it is about, and what each
model should answer for it. Keeping the script beside the text is what makes it reviewable
as a journal rather than as a wall of JSON.

```python
@dataclass(frozen=True)
class SimulatedDay:
    day: int
    event_date: date
    text: str                       # what the person wrote
    themes: tuple[str, ...]         # what it is about — drives the embedder
    intent: str                     # one line: what should happen, in English
    replies: dict[str, str]         # step name → the reply that step should give
    expects: DayExpectation         # asserted after this day's run
```

`DayExpectation` records what the day should leave behind — new standing records, which
existing one gained evidence, whether a version was created — so each day is checked as it
runs rather than only at the end. A failure on Day 2 that is only noticed after Day 5 is
much harder to read.

The five days follow A3. Day 5 splits into two episodes, which is what exercises
`follows_from` ordering.

## B3. The Themed Embedder (`themes.py`)

```python
class ThemedEmbeddingProvider(BaseEmbeddingProvider):
    """Places text near other text about the same declared theme."""
```

A theme owns a fixed direction in the vector space, derived from a hash of its name so it
is stable across machines. Text is embedded as its themes' directions plus a small,
text-derived wobble, then normalised. Two entries on one theme land close; two entries on
different themes land far apart; identical text still gives an identical vector.

Themes are found by scanning the text for each theme's registered keywords. That is the
honest description of what this is: **it is told the theme, not asked to infer it.** The
class docstring says so, and a test asserts the wobble is small enough that same-theme
always beats different-theme.

The existing `FakeEmbeddingProvider` is untouched — every Goal 8 test depends on its
hash behaviour, and those tests are about ranking a seeded set, not about recall.

## B4. Scripting Five Days of Models

The fake language model already accepts a callable, which is the mechanism this needs: one
dict keyed by prompt substring cannot tell Day 1's extraction prompt from Day 3's, because
they are the same prompt with different text in it.

`simulate_days` builds a provider whose reply function finds which day's text the prompt
contains, then answers from that day's `replies`. A prompt matching no day raises rather
than guessing — the same rule the existing fake follows, and for the same reason.

## B5. The Runner (`runner.py`)

```python
def simulate_days(
    days: Sequence[SimulatedDay] = CORPUS,
    *,
    graph: GraphProvider,
    vectors: VectorProvider,
    ops: OperationalStore,
    embedder: EmbeddingProvider | None = None,
    config: AppConfig | None = None,
) -> list[RunReport]:
```

Per day: create the buffer, build the decay event, call `run_pipeline`, keep the report.
Nothing is mocked below `run_pipeline` — this is the shipped path with stand-in models at
the edges, which is the only way the test means anything.

Days run in order and each sees what the previous ones wrote. That is the entire point.

A thin `python -m lumen.simulation` entry point runs the corpus against the configured
databases so a graph can be built and then browsed through the Goal 11 API.

## B6. The Assertions (`test_multi_session_integrity.py`)

Grouped by the four properties in A4, plus per-day expectations.

| Group | Checks |
|---|---|
| **Nothing fragmented** | Exactly one standing record per theme after five days. Counted via `/graph/nodes?types=PatternNode`. Failure message names every duplicate, because "expected 1, got 4" without the four texts is not debuggable. |
| **Evidence accumulated** | The pattern's `evidence_count` after each day matches that day's expectation; `last_reinforced_at` moves only on the days it should. |
| **Version chains** | `/graph/nodes/{id}/versions` returns the chain in order; every version but the head is `SUPERSEDED`; every non-first version has a `previous_version_id` pointing at the one before it; the head is the only `ACTIVE` one. |
| **Nothing orphaned** | Every written node resolves through `/debug/nodes/{id}/provenance`; every node except the episodes is reachable from its episode; every decision has an audit note. |
| **Ordering** | Day 5's two episodes are joined by `follows_from` in the order written. |
| **Repeatability** | The whole corpus run twice into two fresh databases produces the same node ids, kinds and counts. |

## B7. Amendments Expected

| Layer | Change | Why |
|---|---|---|
| `retrieval/structural.py` | `PERSON_LINKED_TYPES` gains `PatternNode` and `BeliefNode`. | Goal 11 built the second hop and nothing calls it. Without this, "what do I know about Alex" returns notes and never the pattern they produced — so the wording-independent route to yesterday's pattern does not exist. `Architecture.md` has asked for this since Goal 8. |
| `pyproject.toml` | Possibly a `simulate` script entry point. | So the corpus can be loaded with one command. |

Beyond these, **the amendments are unknown by design**. This goal exists to find out what
five days does to a pipeline that has only ever seen one entry, and Goals 10 and 11 each
turned up three real defects the moment two things met. The build order below puts the
corpus and the runner first precisely so that discovery happens early.

## B8. Test Plan (~45 tests)

| File | Count | Focus |
|---|---|---|
| `test_simulation_themes.py` | ~10 | Same theme lands close, different themes far, identical text identical, stable across processes, and the wobble never overturns a theme match. |
| `test_simulation_corpus.py` | ~10 | Every day has a reply for every step it will reach; dates are consecutive; the themes named exist; the intent of each day is recorded. Guards against a corpus that silently stops exercising what it claims to. |
| `test_multi_session_integrity.py` | ~25 | The four properties, the per-day expectations, ordering, and repeatability. |
| `test_providers_live.py` | +2 | The corpus against real models, opt-in and deselected by default. |

## B9. Build Order

1. `themes.py` and its tests — pure, fast, no infrastructure.
2. `corpus.py`, days 1 and 2 only, with the intents written before the replies.
3. `runner.py`, and get two days running end to end. **Expect this to be where things
   break**, and expect Day 2 to fragment before `PERSON_LINKED_TYPES` is widened.
4. The `structural.py` amendment; confirm Day 2 accumulates.
5. Days 3, 4 and 5, one at a time, checking each day's expectation as it lands.
6. The four property groups.
7. Repeatability, and the command-line entry point.
8. The opt-in live run.
9. Doc amendments: `Architecture.md` (person anchors now reach standing records),
   `Goal_8_Plan.md` (loop fully closed).
10. `Master_Plan.md` checkbox and result line; Section C of this document — **including
    every corpus change made after the fact, with its reason.**

---

# SECTION C — RESULTS

## C1. What Was Built

`lumen/simulation/` as planned, plus `__main__.py` for the one-command load. Five days,
four integrity property groups, and 73 new tests.

The week that shipped:

| Day | Decision reached | What it demonstrates |
|---|---|---|
| 1 | `BRANCH` → `pat_comparison_spiral` | the starting point |
| 2 | `REINFORCE` (0.93) | accumulation, with the person named |
| 3 | `REINFORCE` (0.91) | accumulation with nobody named and no shared wording |
| 4 | `BRANCH` → `bel_pace_not_ability` | a standing belief |
| 5a | `EVOLVE` (0.95) → `bel_pace_not_ability_v2` | a version chain |
| 5b | `AMBIGUOUS` | an unrelated subject staying unrelated |

Final graph: one pattern with `evidence_count` 3, one belief in two versions with only the
head active, six episodes, thirteen decision notes, one person.

## C2. Deviations From the Plan

1. **The script is told which day is running; it does not work it out.** The plan had it
   finding the day by matching the entry's text in the prompt. That works for the first two
   stages and fails for the rest: by the time the search and decision prompts are built,
   what they contain is what the *earlier models said*, not what the person wrote. Matching
   on that would have meant the fixture predicting its own output. The runner feeds days one
   at a time and already knows which one it is on, so it announces it.

2. **Day 5 evolves rather than contradicts.** The planned arc ended with a contradiction,
   which would have left no `EVOLVE` anywhere and therefore no version chain — one of the
   four named integrity properties. A contradiction is already covered by Goal 9's unit
   tests; a version chain across two days was not covered anywhere. Recorded here rather
   than quietly swapped, per A7.

3. **A `runner` test file was added** (not in B1's list). The stand-in that speaks for the
   models refuses in three distinct ways, and each refusal is what stops a quietly broken
   corpus from looking like a working one — worth testing directly rather than only through
   a five-day run.

## C3. Things Caught While Implementing

Two production bugs, both unreachable from a single entry, and both found on the first
run of day two.

1. **A person mentioned again on a later day crashed the entire episode.** Day one creates
   a record for Alex. Day two finds them already there, so it plans a small update instead
   of creating them — but still plans the link from the day's finding to them. The write
   plan's consistency check refuses a link pointing at a record nothing creates, so days 2
   and 4 failed outright and wrote nothing at all.

   The fix generalises rather than special-casing people: **a record about to receive a
   bookkeeping update is by definition a record that already exists**, so every bookkeeping
   target now counts as a known endpoint. Amends Goal 9's `_known_ids`.

2. **A standing record could not report its own decision history.** The `decided_by` link
   was written only from the finding that triggered a decision, never from the record the
   decision acted on. So a pattern with three pieces of evidence could not say where any of
   them came from — the exact question Goal 11's `/decisions` endpoint exists to answer —
   and three of the six `decided_by_*` tables were unreachable, having never been written
   at all. Decisions now link to both records they concern.

   Two Goal 9 tests asserted the single-link behaviour and were updated.

Also, minor: **`NewNodeContent.kind` is asked for and never read.** The deciding model is
asked whether a new record should be a belief or a pattern, and the answer is discarded —
what a finding becomes is determined by its observation type through Goal 9's promotion
table, which is the correct behaviour. The field costs prompt tokens and invites the model
to believe it matters. Left in place rather than changed mid-goal, because removing it
alters the prompt contract; recorded here as debt.

## C4. Changes Made to the Corpus After the Fact

A7 said any such change is itself a finding. Three were made, all recorded, none of them a
reword to make a failing assertion pass:

| Change | Why |
|---|---|
| Day 4's observation type became `CONCEPTUAL_REFRAME` | The day asked for a belief through `new_node.kind`, which is never read. The way to ask for one is the observation type. The day's intent did not change. |
| Day 5's action became `EVOLVE` | See C2-2 — a named integrity property had no coverage otherwise. |
| Day 5's wording gained "comparing" and "behind" | It claimed the comparison theme and, after the C2-2 rewrite, no longer contained a single word of it. **Caught by a corpus test, not by a failing integrity assertion** — which is exactly what those tests are for. |

## C5. What the Tests Cover

| File | Count | Focus |
|---|---|---|
| `test_simulation_themes.py` | 17 | Same theme close, different themes far, stable across processes, and the wobble never overturning a theme match. |
| `test_simulation_corpus.py` | 18 | The week is well-formed and still says what it claims: consecutive days, themes its words actually contain, an arc that still holds a creation, two accumulations and an evolution, and no reply left behind for a step that no longer runs. |
| `test_simulation_runner.py` | 13 | The stand-in's three refusals, no overlap between step markers, and the one-command load. |
| `test_multi_session_integrity.py` | 25 | The four properties, per-day expectations, ordering, and repeatability. |
| `test_providers_live.py` | +1 | The same week against real models. Deselected by default. |

The corpus tests earn their place: one of the three corpus changes above was caught by
them rather than by the integrity suite, which would have kept passing while day 5 quietly
stopped being about comparison at all.

## C6. Still Deferred

Unchanged from A6: fixing fragmentation if a real model produces it, cross-day ordering
edges, concurrency, performance, and cross-entry person merging.

Added by implementation:

| Item | Target | Why |
|---|---|---|
| Removing `NewNodeContent.kind` | A goal that touches the reconciliation prompt | It is dead weight in a prompt, and deleting it mid-goal changes what the model is asked without any way to check the effect here. |
| Corpus days beyond five | When a behaviour needs them | Five covers the four properties. More days is more runtime, not more proof. |
| A failed event or chain having somewhere to go | Unchanged from Goal 7 | Only observations can carry a failure, so the failure record is still incomplete by construction. Not exercised by this corpus. |
