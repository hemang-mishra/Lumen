# Goal 13b: Import & Inspection Surfaces

**Branch:** `goal13b`
**Status:** ✅ Complete
**Depends on:** Goal 10 (the pipeline runs end to end) ✅, Goal 11 (the graph can be read) ✅
**Blocks:** nothing in the plan — this is an insert, in the manner of Goal 3b

---

## Objective

Thirteen goals in, Lumen could do the whole job and there was no way to put anything
into it except a Python fixture, and no way to look at what happened except by knowing a
`trace_id` by heart and grepping a JSONL file.

That is not a cosmetic gap. Goals 14–16 build retrieval and RAG on top of a graph that,
until now, could only be populated by `lumen/simulation/`'s hand-written corpus — four
days of writing composed specifically to exercise the stages. Tuning retrieval against
that corpus tunes retrieval against itself.

So this goal builds three things and nothing else:

1. **Real input.** Upload a chat export and run it through the shipped pipeline.
2. **Real inspection.** Turn one `trace_id` into the readable story of a run, and list
   the runs so somebody can find a `trace_id` in the first place.
3. **Real configuration.** A committed `.env.example`, and a process that reads `.env`.

The pages are a **test harness, not the product UI** — vanilla HTML, no build step,
meant to be deleted when the real front end is designed.

---

# SECTION A — THE DECISIONS

## A1. One logical date per conversation, taken from its first message

*Per explicit user decision.* A conversation that starts at eleven at night and runs past
midnight is one evening's thinking. Splitting it at the stroke of twelve would produce a
second entry about the same evening that reconciliation would then have to compare
against the first — and it would compare it against the graph's copy of its own earlier
conclusions, which is the exact failure Goal 10 spent a rule preventing.

The schema does allow per-message dates (`BufferMessage.event_date` is deliberately
distinct from `timestamp`), so this is a choice, not a constraint. Changing it later is
one line in `_read_conversation`.

**The time zone is part of this decision and was nearly missed.** An export records an
instant; only the reader knows which calendar day it falls in. Nine in the evening in
India is half past three in the afternoon UTC — the same instant, and the day has or has
not turned over depending on which you measure. `LUMEN_IMPORT_TIMEZONE` exists for this,
defaulting to UTC, and is passed *into* the parser rather than read by it.

## A2. The export format is not the official OpenAI one

The user's sample is one conversation per file with a flat message list:

```json
{ "id": "...", "title": "Aug 2", "lastUpdated": "…Z",
  "messages": [{ "id": "…", "role": "user", "content": "…", "timestamp": "…Z" }] }
```

No `mapping`, no `parent`/`children`, no `current_node`, so none of the branch-walking
the official export needs. Assistant messages are kept, not dropped: Stage 0's
`co_created_spans` detection can only run while the dialogue is still turn-by-turn, and
an import that discarded the assistant's half would silently lose that signal.

## A3. The reading is generous; the reporting is not

A missing timestamp, text arriving as a list of fragments, a message with no id, a
`memcite` marker the exporter left behind — each is worked around rather than refused,
because an export is somebody's real history and rejecting the file loses all of it.

But every accommodation is **counted and handed back**: `skipped_roles`,
`artefacts_removed`, and a `rejected` list carrying a plain-language reason per
conversation. A file of thirty conversations where two are unreadable imports
twenty-eight and says which two were dropped and why.

## A4. Run immediately, answer at once

*Per explicit user decision.* No confirm step. But one conversation is 6–20 model calls
and takes minutes, so the request cannot wait for it. The upload answers **202** with the
identifiers already settled — the caller is handed something to follow before the work
starts, and polls `GET /ingest/imports/{batch_id}` until `finished`.

## A5. The read-only API guarantee, narrowed precisely

Goal 11 asserted that every verb is a GET and the routes hold a `ReadOnlyGraph`. Uploading
breaks the letter of that, and it is worth being exact about what survives.

**What is unchanged:** no route can reach a graph write. `get_graph` still hands back a
`ReadOnlyGraph`; the upload routes never touch it. What they hold is `IngestWorker`, and
the only thing they can do with it is put an identifier on a queue.

**What changed:** the *process* now has a writer. The graph, the vector store and the
models live inside the worker, on its own thread.

Three tests pin this rather than one:

- `test_everything_touching_the_graph_is_a_get` — unchanged; `/graph`, `/debug` and
  `/health` stay GET-only.
- `test_every_post_is_one_of_the_three_that_have_earned_it` — an allow-list with a
  written reason per entry, so a fourth POST is a deliberate act.
- `test_the_upload_routes_cannot_reach_the_graph_themselves` — the ingest router names
  no graph handle, no vector store and no `run_pipeline`.

`LUMEN_ENABLE_INGEST=false` removes the routes from the application entirely rather than
mounting them to answer 503, and opens no worker.

## A6. The upload is refused before anything is written

`worker.ensure_ready()` runs first. A deployment with no credential answers **503 saying
so** instead of accepting the file, storing it, and reporting a failure four minutes later
that reads as a problem with the export.

The models are still built lazily, on that first check — a missing credential must not
stop the service from starting, because every other thing it does reads two local
databases and needs no model.

## A7. One worker thread, and that is not a limitation to fix later

Two imports side by side would be two transactions on one Kuzu connection. Importing is
measured in model calls anyway, and somebody watching their own history load would rather
it arrive in order.

---

# SECTION B — WHAT WAS BUILT

## B1. Files

| Path | What |
|---|---|
| `lumen/ingest/contracts.py` | `ParsedMessage`, `ParsedConversation`, `RejectedConversation`, `ImportPlan`, `StagedConversation` |
| `lumen/ingest/chatgpt_json.py` | `parse_export()` — a **pure function**, no DB, no clock, no config |
| `lumen/ingest/loader.py` | `stage_conversations()` — writes through existing repository methods only |
| `lumen/ingest/worker.py` | `IngestWorker`, `IngestResources`, `build_resources` |
| `lumen/api/routes/ingest.py` | `POST /ingest/file`, `POST /ingest/json`, `GET /ingest/imports`, `GET /ingest/imports/{batch_id}` |
| `lumen/api/static/` | `index.html`, `episodes.html`, `trace.html`, `chat.html`, `app.css`, `app.js` — mounted at `/ui` |
| `lumen/env.py` | `load_env()` |
| `lumen/__main__.py` | `python -m lumen`, one worker, not configurable |
| `lumen/operational/migrations/versions/0003_imports.py` | the `imports` table |
| `.env.example` | every `LUMEN_*` variable with its real default |

## B2. The `imports` table

One row per (upload, conversation). It earns its place three times: it is the ingestion
history, it is the dedupe key, and it is the **only** join from an upload to the
`trace_id` of the run it caused — a session buffer knows nothing about traces.

`UniqueConstraint(user_id, source_conversation_id)` is the load-bearing part, scoped to
the user because two people exporting from the same application can hold the same
conversation identifier. The check also runs in code before staging, so a repeat costs
one indexed lookup rather than a rejected insert after the messages are already written.

`ImportStatus.DUPLICATE` exists as an outcome because "we have seen this before, here is
the original run" is a different and more useful answer than silence.

## B3. Staging goes through the doors a live conversation uses

`find_or_create` → `append_message` → `mark_status(DECAYED)` → `build_decay_event`, the
same four steps as `simulation/runner.py:_arrive`. No new write path into a session
buffer, so an imported conversation and a typed one are indistinguishable to every stage
downstream — which is the only arrangement in which testing the pipeline on imported
history proves anything.

**Two collisions had to be handled.** Buffers are keyed by (user, day, label), so two
different exported conversations sharing a day and a title would merge — or worse, the
second would be appended to a buffer the pipeline had already processed. A buffer that is
already occupied is not reused; the fallback label is derived from the conversation's own
identifier, so it stays stable across re-imports. And message ids are the primary key
across every buffer there has ever been, so they are prefixed with the session id.

## B4. Amendments to earlier goals

**Goal 1 — `KuzuGraphProvider` is now safe for one writer and several readers.**
This is the most significant change outside the new code, and it was forced. Kuzu is
embedded and takes a file lock, so a process can hold exactly one provider; a web server
that both reads the graph and imports in the background therefore *must* share the
object. A transaction belongs to the connection, not to the caller who opened it — so
without a lock, a read arriving mid-import would run **inside the importer's uncommitted
transaction** and report half an episode that might yet be rolled back.

Every statement now goes through one guarded `_execute`, and `transaction()` holds a
re-entrant lock for its whole length. Same-thread nesting still raises, as before; another
*thread* now waits its turn instead of being refused, which is a different question with a
different right answer. Two tests pin the concurrent behaviour.

**Goal 1 — `QdrantVectorProvider` can now actually persist.** `LUMEN_VECTOR_LOCATION` was
documented as accepting a path, and did not: the client's `location` argument is a *host*,
so `./lumen_vectors` was resolved as a DNS name and failed. `_connection_for()` now tells
the three forms apart (`:memory:`, a URL, a folder). This was found by running the service
by hand, not by the suite — no test had ever configured a persistent vector store.

**Goal 3b — two log lines were silently lying, and one class of bug was closed.**
`logging` raises `KeyError` when `extra` names a field a `LogRecord` already carries, and
does so from inside the logging call. It never fires in tests, because pytest leaves
logging at WARNING and `logger.info(...)` returns before building a record. One instance
shipped in this goal (`extra={"filename": ...}`) and would have failed **every upload**;
it was caught by running the service.

`test_observability_extra_keys.py` now reads the syntax tree of every file in the package
and fails on any reserved key. It found two pre-existing lines in
`orchestration/embed.py` passing `extra={"trace_id": ...}` — not a crash, but
`TraceIdFilter` overwrites the field, so those lines were stamped with the ambient run
rather than the one being repaired. Renamed to `repairing_run`.

**Goal 4 — one Gemini credential became N, rotated per request.** An import of a real
export is the first workload heavy enough to matter: a few hundred embedding calls and a
few dozen extraction calls, all inside a few minutes, against a quota metered per key per
minute. The fix is capacity rather than pacing — several keys are several meters.

`ProviderConfig.gemini_api_keys` reads `GEMINI_API_KEYS` (comma-separated),
`GEMINI_API_KEY_1..N` (stopping at the first gap, so a commented-out entry cannot silently
hide the ones after it) and the original singular form, then merges and de-duplicates them.
It is a property for the same reason `gemini_api_key` is: nothing that walks the dataclass
can carry a plaintext key into `pipeline_jobs.config_snapshot`. `gemini_api_key` now
returns the first of the set, so every existing caller is unchanged.

`lumen/providers/keyring.py` holds `ApiKeyPool` — the choosing, with no vendor knowledge.
Random is the default because it holds no state and therefore stays correct across threads
and processes without anything being shared; `round_robin` is available for runs short
enough that random choice could clump. Both accept a key to *avoid*, which is what makes a
retry after a 429 land somewhere new; round-robin skips a turn rather than indexing into a
filtered list, which would shift every later position and unbalance the walk.

Two consequences worth naming:

- **Rotation happens per attempt, not per call.** `_ClientSource.acquire()` sits inside
  the lambda `call_with_retry` re-runs, so the retry of a rate-limited call is already
  under a different key without the retry layer knowing keys exist.
- **The rate-limit backoff ceiling is now conditional.** `_rate_limit_backoff_max()` is a
  hook on both base provider classes; Gemini drops from the 65s quota-minute ceiling to the
  ordinary 8s one when the pool holds more than one key. Waiting out a minute with a fresh
  key in hand would have cancelled out most of the benefit of configuring more keys.

One client is built per key and kept, guarded by a lock because embedding batches can run
several at a time. Keys never reach a log line — rotation logs a position (`"2/5"`), and a
test asserts the key text is absent from the log at DEBUG.

**Goal 11 — the debug surface gained `GET /debug/traces`.** Every other endpoint there is
keyed by a trace id and nothing in the system handed one out.

**Goal 3 — `PipelineJobRepository.list_recent()`** added to support it.

## B5. The episode page, and four bugs the first real import found

Everything in this section came out of running the shipped code against a real export
rather than against the suite. Each one is a case the tests could not have caught,
because each is about the world the service runs in — a vendor's API, a browser, a
process that keeps its log open.

### The page

`episodes.html` answers the question the harness could not: *what did it actually make
of what I wrote?* An episode, the writing it came from, and every record produced by it
with all of its properties — nothing curated out, because the field that matters is
always the one a chosen few left out.

The writing is the awkward half. An episode keeps a summary and a hash of its text and
never the text, which is right for a store of conclusions and useless to somebody
checking one. `GET /debug/episodes/{episode_id}/source` walks node → run → conversation
and reads it from the operational store instead. It belongs on the debug surface rather
than the graph one for exactly that reason: it is not a graph read.

The transcript scrolls inside its own box. A forty-message day makes a page sixty
thousand pixels tall, and everything worth comparing it against sits underneath.

### Gemini rejected every structured call

The pipeline's contracts all set `extra="forbid"` — the thing that makes a malformed
reply fail at the boundary instead of three stages downstream. Pydantic writes that as
`additionalProperties: false`, the SDK forwards it as `additional_properties`, and the
API refuses the whole request for naming a field it does not have.

So every structured call failed, on every model, with a message about JSON. Each stage
fell back to its safe answer exactly as designed, and the run reported **COMPLETE**
having extracted nothing — the worst shape a failure can take. `_response_schema()`
drops the key on the way past; the reply is still validated against the real class on
the way back, where a stray field is still refused.

### The log said a call failed and never said why

`log_llm_call` recorded `error_type` and dropped the message, so "the model is retired"
and "the request was malformed" — different problems with different fixes — were both
`ProviderResponseError`. Diagnosing the above needed a live probe against the API,
which is precisely the work a log line exists to prevent. `error_detail` now travels
with the type, truncated, on both the model and embedding lines.

### A collection built for another model failed one record at a time

Changing the embedding model changes how wide its vectors are, and a collection's width
is fixed when it is created. The mismatch surfaced as `could not broadcast input array
from shape (3072,) into shape (768,)`, once per record, deep in a run — while the graph
kept saving records that nothing would ever find. `init_collection` now refuses at
startup and names both numbers and the two ways out.

### `get_nodes_by_ids` could not read two kinds of record at once

Found by opening an episode: a plain `MATCH (n) WHERE n.node_id IN $ids` whose results
span two node tables comes back with its strings misread, and fails with
`UnicodeDecodeError` naming a byte position. Each id worked alone; any two from
different tables failed together. It now asks one table at a time and preserves the
caller's order, which a search's ranking depends on.

This one was waiting for Goals 14–16 — retrieval fetches mixed candidates by id, and
that is all this method is for.

**Goal 3b — the suite was writing into the log the service uses.** `configure_logging`
attaches its handler to the *root* logger and nothing detaches it at shutdown, so one
test entering an application lifespan redirected every test after it into
`./logs/lumen.jsonl`. Scripted failures — a stand-in raising "the model went away" —
sat in the production log looking exactly like real ones, in the first place anybody
looks when a real import fails. An autouse fixture now points the default at a
throwaway directory and puts the root logger back.

## B6. `create_app()` reads no files

`.env` loading lives in `create_configured_app()` and `python -m lumen`, not in
`create_app()`. A `.env` read during `create_app()` polluted `os.environ` for the whole
test session — variables outlive the test that caused them, and a file in a developer's
checkout could point a test at a real database. Caught while running the API suite.

---

# SECTION C — WHAT WAS VERIFIED

- **2822 tests passing** (2230 from Goals 1–13 + 592 new), 20 deselected as before.
- **100% coverage** on `lumen/ingest/`, `lumen/api/` and `lumen/env.py`.
- The parser is tested with **no infrastructure at all** — decoded JSON in, DTOs out.
- The worker's happy path is a **real run**: real Kuzu, real Qdrant, the shipped
  orchestrator, stand-ins only where a model would be. It asserts the graph was written
  and that the trace it points at holds the stages and the writes.
- **Driven by hand end to end**: a file uploaded through the web layer, processed by the
  real worker, all five stages COMPLETE, 9 records and 8 links written, the run readable
  on the trace page, a second upload of the same file deduped to zero queued. Both real
  bugs above were found this way and by nothing else.
- **Then driven against a real export, on a real API key**, which is where B5's four
  bugs came from. A day's conversation imported, read, and written: 20 records, 6 of
  them searchable, the whole thing readable on the episode page next to the writing it
  came from. Every one of those four failures was invisible to a suite of 2,800 tests,
  because each is about the world the service runs in rather than about its logic.

## C1. Fixture content

The test fixtures match the user's real export **structure** message for message; the
writing in them is invented. A test file is a bad place to keep somebody's journal.

## C2. What is deliberately left undone

| Deferred | To |
|---|---|
| Adapters for other export formats | when there is a second format to read |
| A real front end | its own planning pass; these pages are throwaway |
| Redis/RQ, and taking the worker out of the web process | Goal 20 |
| Kuzu/Qdrant in server mode, and therefore more than one replica | Goal 20 |
| Per-message dates / splitting a conversation at midnight | not planned; A1 is the rule |
| Re-running a failed import from the UI | not planned; the row records the failure |

## C3. The honest limitations

- **One process, always.** Every store is embedded. This is an architectural ceiling, not
  a setting, and `python -m lumen` does not expose a `--workers` flag for that reason.
- **`--reload` abandons an in-flight import.** Fine while editing the pages; not while
  importing.
- **The chat page is a stub** and says so on itself. It reports what *would* be retrieved,
  because retrieval and assembly are Goals 14–16.
- **The model names in `config.py` are a moving target.** `gemini-2.5-flash` and
  `gemini-2.5-pro` were both withdrawn during this goal, and `text-embedding-004` with
  them; a deployment reading the shipped defaults gets three 404s and a run that
  extracts nothing. The defaults are not the problem — a default has to say *something* —
  but a deployment is expected to name its own models in `.env`, and the log now says
  plainly when one has gone.
- **A duplicate upload's receipt is not in the new batch.** The conversation keeps its
  original row, so `GET /ingest/imports/{batch_id}` for an all-duplicate upload is a 404.
  The POST response carries the answer; the page never polls in that case.
