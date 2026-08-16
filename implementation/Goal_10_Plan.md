# Goal 10: End-to-End Extraction Pipeline Harness

**Branch:** `goal10`
**Status:** ✅ Complete
**Depends on:** Goals 5–9 (the four stages) ✅, Goal 3 (operational DB) ✅, Goal 1 (graph/vector) ✅
**Blocks:** Goal 11 (graph read APIs), Goal 12 (multi-session integrity), Goal 20 (BFF)

---

## Objective

Five goals built four stages that had never met. Each one takes a Pydantic model and
returns another, and each one was tested against a constructed input and an asserted
output. Nothing had ever run one after another, and nothing had ever written a single node
to a real graph from a real journal entry.

This goal chains them and saves the result. It is the first point in the project where
running the code changes something permanently.

---

# SECTION A — LOGIC (please verify)

*Short by design. This is what the goal means; Section B is how it's coded.*

## A1. What Gets Built

| Deliverable | What it is |
|---|---|
| **`run_pipeline()`** | One conversation in; a graph that has grown, and a report of what happened, out. Chains all four stages and saves everything they produced. |
| **The episode record** | The thing that holds an entry together. Nothing before this goal created one, even though every stage refers to it. |
| **One transaction per episode** | Everything an episode produces is saved together or not at all. |
| **The search index** | Every record a person's writing produced is made findable by meaning, in one batch, computed before anything is written. |
| **The run log** | Every record, link and search entry, tied back to the episode and the conversation that produced it. |
| **The review queue** | Items the system could not settle are put in front of the person instead of being discarded. |
| **`repair_index()`** | Fixes the one kind of damage a transaction cannot prevent: a record saved correctly but never made findable. |
| **Small fixes below** | Graph transactions, a home for the coreference map, per-episode run tracking, and two identifier bugs that only appear once something runs two episodes in a row. |

## A2. The Decisions You Made

1. **A plain function, not a job queue.** `run_pipeline(session)` runs everything
   synchronously, right there. No Redis, no worker process, no background thread. The
   eventual queue and the "watch for idle conversations" loop move to Goal 20, where the
   API server actually exists to host them. Goals 11 and 12 both want simple, ordered runs
   anyway.

2. **An episode saves whole or not at all.** Every record, link and small update goes in
   one graph transaction. A failure partway through leaves the graph exactly as it was.
   This needed begin/commit/rollback added to the graph layer — the database supported it,
   we had simply never exposed it.

3. **Episodes fail independently.** One entry usually holds several unrelated topics. If
   one breaks — a model times out, a reply is nonsense — the others are still saved, that
   one is recorded as failed, and the run reports "completed with failures". Losing three
   good topics because a fourth had a bad model reply is the worse outcome.

4. **Undecided items go to the review queue now.** Goal 18 builds the screen; without this,
   every item the system was least sure about, produced between now and then, would be
   silently thrown away.

5. **Everything a person said is indexed; machinery is not.** Observations, events,
   reflections, and any new belief or pattern become findable by meaning. Decision notes
   and person records do not. The list is asserted equal to the list the search stage
   reads, so the two cannot drift — indexing a kind nothing searches for is waste, and
   failing to index one it does search for is a hole that never announces itself.

6. **Search entries are computed before anything is written.** If the embedding model is
   down, nothing is saved anywhere and the episode is simply retried. Only the index write
   itself happens after the graph commits; if *that* fails, the records are real and
   correct but unfindable, the run reports failure, and the log names them by id so
   `repair_index()` can recover them without redoing the entry.

7. **Re-running an entry is safe, and skips whole episodes.** An episode already in the
   graph is not read, not decided, and not saved again. Skipping only the saving would
   have run reconciliation against a graph containing its own previous conclusions and
   recorded the entry as a repeat of itself.

8. **The coreference map lives in the operational database.** The episode record has always
   carried a pointer to one, and nothing had ever stored one. It goes with the run's own
   bookkeeping rather than into the graph, because who the pronouns referred to is a note
   about how the text was read, not something the person believes.

## A3. What One Entry's Journey Now Looks Like

```
A conversation goes quiet
  │
  ├─ Start a run, snapshot the settings
  ├─ Clean it, split it into topics          ← Stage 0, once per entry
  ├─ Store who the pronouns meant
  │
  └─ For each topic, on its own:
       ├─ Already in the graph? → skip it entirely
       ├─ Read it                            ← Stage 1
       ├─ Too thin to compare? → skip the next two, save it as written
       ├─ Search the past for it             ← Stage 2
       ├─ Decide what it means               ← Stage 3
       ├─ Build the full plan: the episode, everything in it, and every
       │  decision's consequences — checked as one before anything is saved
       ├─ Work out the searchable form of every record   ← nothing written yet
       ├─ Save it all in one transaction     ← Stage 4
       ├─ Write the search entries
       ├─ Log everything that landed
       └─ Queue anything nobody could settle
  │
  └─ Mark the conversation dealt with, close the run
```

## A4. The Failure Paths, Plainly

| What went wrong | What happens |
|---|---|
| Nothing survived cleaning | Nothing is written. The conversation is marked discarded. The run succeeds — nothing failed, the entry simply held nothing. |
| The entry is too thin to compare against the past | Searching and deciding are skipped and recorded as skipped. It is still saved. |
| An episode could not be read at all | It is saved as an episode with something outstanding, rather than as an uneventful day. |
| The embedding model is down | Nothing is written anywhere. Retry the episode. |
| A write fails partway | Every write in that episode is undone. Other episodes are unaffected. |
| A record saved but could not be indexed | The record is kept. The run reports failure and names it. `repair_index()` fixes it. |
| Anything else, in one episode | That episode is recorded as failed with its reason; the rest of the entry continues. |

## A5. Bugs Found by Running It

Three real defects that no amount of per-stage testing could have surfaced, because all
three need two things to meet.

1. **Two topics written on the same day produced colliding decision-note identifiers.**
   Notes are numbered from one within an episode, and reconciliation had no idea which
   episode it was looking at. The second topic reused the first one's ids and the save was
   refused on a duplicate key. Goal 6 had already solved exactly this for observations;
   the notes never got the same treatment because nothing had ever run two episodes.

2. **A failed write left the rollback itself failing.** The database abandons a
   transaction on its own when a statement is rejected, so our explicit rollback then
   errored — and that error replaced the real failure in every log and every report.

3. **`language_tags` had no producer.** Stage 0 worked out which languages an entry was
   written in, logged them, and threw them away. An entry written in Hindi would have been
   stored as English with nothing recording that what is on disk is a translation.

## A6. What This Goal Deliberately Leaves Undone

| Not built | Where it goes | Why |
|---|---|---|
| Redis / RQ job queue | Goal 20 | Needs a long-running process to host it. |
| The idle-conversation watcher | Goal 20 | Same. Goal 3 shipped the query; the schedule needs an owner. |
| `rerun_from_stage` | Later, cheaply | Every stage's input and output is already stored; only a caller is missing. |
| Running episodes in parallel | Not planned | Deciding is explicitly not parallel-safe. |
| Queueing failed extractions for review | Goal 18 | The queue insists every row point at a decision note, and a failed extraction has none. |
| Cross-entry person merging | Later | Unchanged from Goal 9. |

## A7. The Risk Worth Naming

**The two stores cannot be written to as one.** Everything else here is protected by a
transaction; this is not, and cannot be. A record can commit to the graph and fail to
reach the search index, and from the graph's side it looks perfect.

Three things reduce it and none of them eliminate it: the vectors are computed before
anything is written, so the common failure costs nothing; the run reports failure rather
than success when it happens; and the log records enough to repair it exactly. What
remains is a window between the commit and the index write in which a crash leaves records
that are correct, permanent, and unfindable until someone runs the repair.

## A8. Definition of Done

- [x] `run_pipeline()` takes one conversation from raw text to a saved graph.
- [x] A real journal entry, read from a file, produces episode / observation / event /
      session / pattern / person / decision-note records in a real Kuzu database and
      matching vectors in a real Qdrant collection.
- [x] An episode's writes are atomic; a failure leaves the graph untouched.
- [x] One failing episode does not cost the others.
- [x] Re-running an entry duplicates nothing and re-decides nothing.
- [x] Every node in the graph can be traced back to the run and the episode that made it.
- [x] Undecided items reach the review queue; running again does not ask twice.
- [x] 1799 tests passing (1649 from Goals 1–9 + 150 new); **100% coverage** on `lumen/pipeline/orchestration/` and
      `lumen/operational/`, 98% on `lumen/graph/`.

---

# SECTION B — LOW-LEVEL DESIGN

## B1. Files

```
lumen/pipeline/orchestration/
├── __init__.py       ← exports run_pipeline, repair_index, the four errors
├── runner.py         ← run_pipeline(): job lifecycle, the episode loop, failure isolation
├── episode.py        ← think(): one episode through Stages 1 → 2 → 3. Writes nothing.
├── compose.py        ← the episode record and the structural edges; merges Stage 3's plan
├── embed.py          ← what gets indexed, its text, batching, and repair_index()
├── commit.py         ← executes a plan: one graph transaction, then the index
├── contracts.py      ← IndexEntry, CommitReport, and the four failure types
└── bookkeeping.py    ← everything that touches the operational DB
```

A package rather than the `orchestrator.py` the Master Plan named — the same call Goals
5–9 made. Seven separable concerns that would otherwise be one very long file, and the
separation is load-bearing here: `commit.py` makes no decisions, `compose.py` touches no
database, and both properties are checkable by reading one short file.

## B2. Public Contract

```python
def run_pipeline(
    event: SessionDecayEvent,
    *,
    graph: GraphProvider,
    vectors: VectorProvider,
    embedder: EmbeddingProvider,
    lightweight: LLMProvider,
    thinking: LLMProvider,
    ops: OperationalStore,
    config: AppConfig | None = None,
) -> RunReport: ...

def repair_index(
    trace_id: str, *, ops, graph, vectors, embedder
) -> list[str]: ...
```

Every store and model is injected. Nothing is reached for, so the signature is a complete
statement of what a run can touch, and the same function serves a test with temporary
databases and a real deployment.

New DTOs in `lumen/schemas/pipeline.py` (public, because Goal 20 will return them):
`RunReport`, `EpisodeOutcome`. New enum in `lumen/schemas/enums.py`: `EpisodeRunStatus`
(`COMPLETE | SUSPENDED | SKIPPED | FAILED | DISCARDED`) — the run's outcome, kept distinct
from `ReconciliationStatus`, which is what the episode *means*.

## B3. `compose.py` — the half Stage 3 never sees

Stage 3 is shown what was extracted, not the episode it came from, so it cannot create the
`EpisodeNode` or link anything to it. This module builds:

* the `EpisodeNode` — all of it from Stage 0's episode and the run's own knowledge;
  `coreference_map_id = coref_<entry_id>`, `language_tags` from Stage 0's detected
  languages, `reconciliation_status` from how deciding turned out;
* `contains_obs / contains_evt / contains_sess / contains_chain`;
* `chain_contains`, read off each step's `chain_id` — a step naming a chain this episode
  did not produce is dropped with a warning rather than being allowed to make the whole
  plan dangle;
* `failed_extraction` for every unreadable finding;
* `follows_from` to the previous episode **that actually committed**.

Node order: episode → sessions → events → observations → failed observations → chains →
steps → Stage 3's nodes. Anchors precede the findings they explain; chains precede their
steps.

Then it **merges** with `outcome.write_plan` into one `GraphWritePlan`. Merging before
saving is the point: the plan's own three validators — no duplicate node, no dangling
edge endpoint, nothing referring forward — now cover the whole episode instead of half of
it, and a structural mistake fails while planning rather than halfway through saving.

`status_for()` decides the episode's own status: `SUSPENDED` if the reading failed, the
decision could not be read, or anything is waiting for the person; `COMPLETE` otherwise —
including a thin entry, which is genuinely finished rather than open.

## B4. `embed.py` — what becomes findable

`INDEXED_NODE_TYPES` **is** `retrieval.semantic.CONTENT_TABLES`, not a copy of it, and a
test asserts the identity. `_TEXT_FIELDS` maps each type to the fields to search it by —
deliberately not `preview_of()`, which truncates to 240 characters; a record indexed from
a truncated preview is only findable by its opening.

Excluded: types outside that set, and anything whose `status` is in `RETIRED_STATUSES`
(the search stage filters those on the way back, so indexing them is pure cost).
`PlannedNode.searchable_text` wins when set — nothing sets it today, and honouring it
keeps Goal 9's field from being a dead one.

`prepare_index()` is one `embed_batch` per episode, `EmbeddingTaskType.DOCUMENT`, and it
refuses a reply with the wrong number of vectors rather than pairing them by position and
giving every later record somebody else's meaning.

`repair_index()` needs nothing remembered: `pipeline_write_log` records node writes and
vector writes separately, so the difference between the two lists *is* the repair set. It
reads the missing records back from the graph, re-embeds, upserts, and logs the vector
write. Running it twice finds nothing the second time.

## B5. `commit.py` — the only thing that writes

```
with graph.transaction():
    nodes → edges → bookkeeping
commit
then: vector upserts
```

`BOOKKEEPING_OPERATIONS` is a dict from `BookkeepingOperation` to the one method each may
call. A lookup rather than a branch, so the complete list of ways an existing record can
change is one readable block and there is no way to pass a field name in.

Index failures do not stop at the first — every entry is attempted, so the missing list is
complete enough to repair from. `IndexWriteFailed` carries the **full report**, not just
the failures, so the runner can still log everything that landed.

## B6. `bookkeeping.py` — the operational side

`stage_span()` is a context manager: it opens a stage row, hands back a mutable
`StageOutcome`, and closes the row out whether the body returns or raises. A stage that
blew up still gets a row with the error on it, because a missing row and a failed one mean
very different things when reading a run back.

`queue_escalations()` runs after the commit (the queue row points at the decision note,
which has to exist) and skips anything already queued by audit node.

`close_job()` fails the run when any episode was lost *or* anything reached the graph
without becoming searchable.

## B7. Amendments to Earlier Goals

| Layer | Change | Why |
|---|---|---|
| **Goal 1/2 — `GraphProvider`** | `transaction()` added to the Protocol; Kuzu implements it with begin/commit/rollback, refusing nesting. | An episode has to save whole or not at all. The database always supported this; we had never exposed it. |
| **Goal 1 — `kuzu_impl`** | Rollback tolerates "no active transaction". | Kuzu abandons the transaction itself on a rejected statement. Without this the cleanup error replaced the real failure everywhere. |
| **Goal 9 — `plan.py`** | Audit ids use `make_scoped_node_id` with the episode index; `PlanContext.episode_index` added. | Two episodes of one day both numbered their notes from one and collided. Id format changes `d_2026_06_11_001` → `d_2026_06_11_01_001`. |
| **Goal 5 — `PreprocessingResult`** | `detected_languages` added and populated. | `EpisodeNode.language_tags` had no producer at all. |
| **Goal 3 — ops schema** | `pipeline_stage_runs.episode_id`, `pipeline_write_log.episode_id`, uniqueness moved to `(job, stage, episode, attempt)`, new `coreference_maps` table + repository. Migration `0002_orchestration`. | Four episodes running one stage either collided on the uniqueness rule or read as one stage retried three times. And the coreference pointer led nowhere. |
| **Goal 3 — `PipelineJobRepository`** | `start_stage(..., episode_id)`, `record_write(..., episode_id)`; attempts counted per episode. | Same. |
| **Goal 2/3 — `PipelineStage`** | Moved to `lumen/schemas/enums.py`, re-exported from `lumen/operational/enums.py`. | `EpisodeOutcome.stages_run` needs it, and the schemas layer must not depend on the operational one. Same move Goal 9 made for `HitlEntryType`. |

Empty string rather than null for `episode_id`: databases treat two nulls as different
values, so a nullable column would switch the uniqueness rule off for exactly the rows it
should still cover.

## B8. The Purity Guard, Narrowed

Goals 5 and 6 each asserted that *no file in `lumen/pipeline/`* imports `lumen.operational`.
That rule protects stage purity, and the orchestrator is precisely the component whose job
is persistence. Both tests now name the four stage packages individually rather than
scanning the whole tree — as strict as before everywhere it still applies, with one
deliberate, visible exception. The vendor-SDK guard is unchanged and still covers
orchestration.

## B9. Test Plan (148 new tests, plus 2 existing guards narrowed)

| File | Covers |
|---|---|
| `test_graph_transaction.py` (11) | Commit, rollback, the database's own abort, nesting refused. Real Kuzu — a stand-in agrees it rolled back whether or not anything did. |
| `test_orchestration_compose.py` (30) | The episode record, node ordering, every structural edge, merging, status. No database: composing is pure. |
| `test_orchestration_embed.py` (30) | What is indexed and what is not, the text, batching, failure before writing, repair. |
| `test_orchestration_commit.py` (11) | Atomicity, index failures kept separate from graph failures, the report surviving. |
| `test_orchestration_bookkeeping.py` (21) | Job lifecycle, stage rows including failed ones, per-episode attempts, the write log, the queue, closing. |
| `test_orchestration_runner.py` (30) | Ordinary runs, discard, thin entries, unreadable episodes, failure isolation, re-runs, index damage. |
| `test_orchestration_e2e.py` (15) | One entry from a file, checked by reading both databases back rather than trusting the report. |

## B10. Doc Amendments

* `Architecture.md` — Stage 4 expanded: the structural half, the per-episode transaction,
  the index ordering and repair, whole-episode skipping on re-run.
* `Technical_HLD.md` §5 — the orchestrator's signature and that it is a plain function
  today, not a queued task; §10 — what re-run support actually exists, and `repair_index`.
* `Reconciliation.md` — who writes the queue row and when; why an extraction failure is
  not queued.
* `Preprocessing.md` — the detected languages now leave the stage.
* `Schema.md` §1 — who creates the `EpisodeNode`, and that `coreference_map_id` resolves
  to the operational store rather than to a node here.
