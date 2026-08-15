# Goal 11: Graph Read & Debug APIs

**Branch:** `goal11`
**Status:** ✅ Complete
**Depends on:** Goal 10 (something now writes to the graph) ✅, Goal 3 (run history) ✅, Goal 1 (graph store) ✅
**Blocks:** Goal 12 (multi-session integrity), Goal 13 (query formulation), Goal 20 (BFF)

---

## Objective

Goal 10 made the graph fill up. Nothing can look inside it.

Today the only way to see what a run produced is to open the database file by hand and
write Cypher. That is fine for one person debugging one entry, and it is the wrong answer
for everything that comes next: Goal 12 has to walk three days of entries and prove a
pattern did not fragment, the graph explorer screen has to draw the thing, and every
future bug report about "the AI thinks I believe X" starts with *where did X come from*.

This goal builds the way in — and it is read-only. Nothing here can change the graph.

---

# SECTION A — LOGIC (please verify)

*Short by design. This is what the goal means; Section B is how it's coded.*

## A1. What Gets Built

| Deliverable | What it is |
|---|---|
| **Seven ways to read the graph** | Named questions — "what's near this?", "how did this belief change over time?", "what came out of that entry?" — not a general query box. |
| **A web API** | The first HTTP surface in the project. FastAPI, read-only, roughly ten endpoints. |
| **The provenance answer** | Given any node, say which entry produced it, which run, and which decision placed it there. |
| **The run trace view** | Every stage of a past run with its timings, model, and what went in and out. |

## A2. The Decisions Taken

1. **Named questions, not a query box.** Goal 1 left a note to add a general
   "run any query" method here. Taking it would put database-shaped thinking into the web
   layer and quietly end the promise that the graph engine can be swapped. Instead there
   is a fixed, small set of questions the system knows how to answer. Anything not on the
   list is a deliberate addition, visible in a code review. **This cancels that earlier
   note rather than deferring it again.**

2. **Read-only, with no way to write.** Not "we chose not to add write endpoints" — the
   API is handed a reader that structurally cannot write. Editing the graph by hand is
   exactly how an append-only history stops being trustworthy, and the one legitimate way
   to change a decision is the review queue in Goal 18.

3. **Nothing raw leaves the database.** The graph stores every node type in one wide
   shape, so reading one back gives dozens of empty columns and lists stored as text.
   Every response is a checked, tidied object instead — empty fields dropped, lists read
   back as lists.

4. **You can ask what the graph looked like on a past date.** Every record and link
   carries the date it became true, so "show me what I believed in March" is a filter
   rather than a feature. Worth having now because the graph explorer screen is specified
   with a timeline scrubber, and retrofitting dates into queries later is painful.

5. **Withdrawn links are hidden by default, not deleted.** A link that a rolled-back
   decision retired stays in the graph but is left out of normal reads. There is a switch
   to include them, because "what did this look like before the rollback" is a real
   question when something has gone wrong.

6. **Depth is capped at three hops.** In a well-connected graph, four hops is most of it.
   A cap keeps one careless request from pulling the whole history into a browser.

7. **The debug half reads the run history, not the graph.** "Which run wrote this node"
   was already answerable — Goal 3 built the log and Goal 10 filled it in. This goal
   exposes it.

## A3. The Questions the API Can Answer

| Question | Why it exists |
|---|---|
| What records are there, of this kind, in this period? | The explorer's starting view; the first thing Goal 12 checks. |
| What is this one record? | Click-through detail. |
| What is connected to this, within N hops? | The explorer's expand-on-click, and how Goal 12 proves a pattern is one pattern. |
| How has this belief changed over time? | Walks the version chain end to end. Goal 12 verifies these link up. |
| What decisions were made about this? | Every change to a record, with what the model thought and why. |
| What came out of this entry? | Everything one episode produced, and the links between them. |
| How many of each kind of record exist? | The overview counters, and the cheapest possible "is the graph growing sensibly". |
| What happened during this run? | Stage-by-stage timings and payloads for a past run. |
| Where did this node come from? | Node → run → conversation. |

## A4. What This Goal Deliberately Leaves Undone

| Not built | Where it goes | Why |
|---|---|---|
| Any write, edit or delete endpoint | Goal 18 (review queue), Goal 19 (erasure) | Those are the only two legitimate ways the graph changes. |
| Chat, ingest, query, reports endpoints | Goals 16, 18, 20 | This goal builds the API's foundations and the graph routes only. |
| Login / access control | Goal 20 | Single local user, bound to localhost. |
| WebSocket live updates | Goal 20 | Nothing pushes yet. |
| The front-end screens | Out of scope | This is the data behind them. |
| Search-by-meaning endpoints | Goals 13–15 | That is the query layer, and it needs its own thinking. |

## A5. The Risk Worth Naming

**A read API makes it very easy to add a write one.** Every future goal with a "just let
me fix this one node" moment will find a working API with routing, validation and tests
already in place, and the shortest path will be one more endpoint. That is precisely how
append-only histories stop being append-only.

The mitigation is structural rather than a rule in a document: the API layer is given a
read-only view of the graph, so a write endpoint would not merely be poor judgement — it
would not compile against what it has. A test asserts the API package never names a write
method.

## A6. Definition of Done

- [x] Eleven read endpoints, documented automatically, running on a local server.
- [x] Every graph read is a named question; no general query method exists anywhere.
- [x] The API cannot write: enforced by what it is handed, and asserted by three tests.
- [x] A node can be traced to its run, its entry, and the decision that placed it.
- [x] Version chains, causal anchors and episode contents are all walkable — the three
      things Goal 12 needs.
- [x] Time-filtered reads return the graph as it stood on a chosen date.
- [x] Tested against a real graph built by actually running the pipeline, not a seeded
      fixture — the same entry Goal 10 ends with.
- [x] 1937 tests passing; **100% coverage** on `lumen/api/`, 99% on `lumen/graph/`.

---

# SECTION B — LOW-LEVEL DESIGN

## B1. Files

```
lumen/graph/
├── provider.py            ← Protocol gains seven reads + a ReadOnlyGraph view
├── kuzu_impl.py           ← implements them
└── queries.py             ← NEW: Cypher fragments, filter building, row tidying

lumen/api/
├── __init__.py
├── main.py                ← create_app(), lifespan, exception handlers
├── deps.py                ← injected providers, resolved per request
├── schemas.py             ← response models (never raw rows)
├── errors.py              ← NotFound / BadRequest → JSON problem shapes
└── routes/
    ├── __init__.py
    ├── graph.py           ← /graph/*
    └── debug.py           ← /debug/*
```

New dependencies: `fastapi` (runtime), `httpx` (dev, for `TestClient`).

## B2. The Read Surface on `GraphProvider`

Seven methods, each one question. All return plain rows; tidying happens in `queries.py`
and typing happens at the API boundary.

```python
def find_nodes(
    self, node_types: list[str], *, since: datetime | None = None,
    until: datetime | None = None, domain: str | None = None,
    signal_strength: str | None = None, era_tag: str | None = None,
    active_only: bool = True, limit: int = 50, offset: int = 0,
) -> list[dict[str, Any]]: ...

def get_neighborhood(
    self, node_id: str, *, depth: int = 1, edge_types: list[str] | None = None,
    direction: str = "both", as_of: datetime | None = None,
    include_invalidated: bool = False, limit: int = 200,
) -> GraphSlice: ...

def get_version_chain(self, node_id: str) -> list[dict[str, Any]]: ...

def get_decision_history(self, node_id: str) -> list[dict[str, Any]]: ...

def get_episode_contents(self, episode_id: str) -> GraphSlice: ...

def get_causal_chain(self, chain_id: str) -> list[dict[str, Any]]: ...

def count_by_type(self) -> dict[str, int]: ...
```

`GraphSlice` is a `NamedTuple` of `(nodes: list[dict], edges: list[EdgeRow])`, alongside
`ScoredHit` in `provider.py`. `EdgeRow` is `(table, from_id, to_id, properties)` — **edges
have no id column**, which is why the three-part key is the identity everywhere. That is
the same fact Goal 9 hit when it made `decision_id` the rollback handle.

### Why each one

* **`find_nodes`** — the explorer's list view and Goal 12's first check. Filters are
  composed rather than hard-coded because the columns differ per table (`domain` exists on
  beliefs and patterns, not observations; `era_tag` on patterns, `historical_era` on
  episodes — `ERA_COLUMNS` already knows this).
* **`get_neighborhood`** — the multi-hop traversal the Master Plan asks for. One
  variable-length Cypher match per direction, not N round trips.
* **`get_version_chain`** — walks `evolved_from` backwards to v1 and forwards to the head,
  returning the whole chain ordered by `version`. Answers "when did this change and into
  what".
* **`get_decision_history`** — every `DecisionAuditNode` reachable by `decided_by_*` from
  a node, newest first. This is the "where did X come from" answer.
* **`get_episode_contents`** — one episode plus everything `contains_*` reaches, plus the
  edges between them. Goal 12's per-day assertion.
* **`get_causal_chain`** — a chain and its steps in `step_index` order.
* **`count_by_type`** — one `MATCH (n:T) RETURN count(n)` per table.

### The read-only view

```python
class ReadOnlyGraph(Protocol):
    """Every read on GraphProvider and none of the writes."""
```

`GraphProvider` is declared to extend it. `deps.py` types its dependency as
`ReadOnlyGraph`, so `write_node` is not merely discouraged in the API layer — it is not on
the object's declared type. A test greps `lumen/api/` for write method names.

## B3. `queries.py` — keeping `kuzu_impl` readable

`kuzu_impl.py` is already ~800 lines. The new reads need composed `WHERE` clauses,
variable-length matches, and row tidying, so that goes in its own module:

* `build_filters(...)` → `(clause, params)`, skipping filters a table has no column for
  and logging when it does (the `find_by_era` precedent).
* `temporal_clause(as_of)` → `n.valid_from <= $as_of`.
* `edge_liveness_clause(include_invalidated, as_of)` → `r.invalidated_at IS NULL` or, for
  a past date, `r.invalidated_at IS NULL OR r.invalidated_at > $as_of`.
* `tidy_row(row)` → drops nulls, decodes the JSON-string columns Kuzu stores lists in,
  keeps `_label` as `node_type`.

`kuzu_impl` composes these; it does not build query strings inline.

## B4. `lumen/api/` — the HTTP layer

**`main.py`** — `create_app(config: AppConfig | None = None) -> FastAPI`. A factory, not a
module-level app, so a test builds one against temporary databases. Lifespan opens the
graph store and the operational store on startup and closes both on shutdown.

**`deps.py`** — `get_graph() -> ReadOnlyGraph` and `get_ops() -> OperationalStore`, reading
from `app.state`. Overridable in tests through FastAPI's dependency overrides rather than
by monkeypatching.

**`schemas.py`** — response models. Nothing raw crosses the boundary:

| Model | Shape |
|---|---|
| `NodeView` | `node_id`, `node_type`, `properties: dict` (tidied) |
| `EdgeView` | `edge_type`, `from_node_id`, `to_node_id`, `valid_from`, `invalidated_at`, `decision_id`, `confidence` |
| `GraphSliceView` | `nodes: list[NodeView]`, `edges: list[EdgeView]`, `truncated: bool` |
| `NodeListView` | `nodes`, `total`, `limit`, `offset` |
| `VersionChainView` | `versions: list[NodeView]`, `current_version_id` |
| `DecisionHistoryView` | `decisions: list[NodeView]` |
| `EpisodeDetailView` | `episode: NodeView`, `contents: GraphSliceView` |
| `GraphStatsView` | `counts: dict[str, int]`, `total` |
| `TraceView` | wraps the existing `PipelineTrace` |
| `ProvenanceView` | `node_id`, `job_id`, `trace_id`, `session_id`, `episode_id`, `written_at` |

`truncated` matters: a neighbourhood that hit the limit and one that was genuinely that
size look identical otherwise, and a partial graph drawn as a complete one is a wrong
answer that looks right — the same class of failure as Goal 8's `search_failed`.

**`errors.py`** — `GraphNotFound` → 404, invalid parameters → 422 (FastAPI's own), and a
catch-all that logs with the trace id and returns a plain 500 without leaking internals.

## B5. Endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/health` | liveness + whether both stores answer |
| GET | `/graph/stats` | `GraphStatsView` |
| GET | `/graph/nodes` | `NodeListView` — `?types=&since=&until=&domain=&signal=&era=&active_only=&limit=&offset=` |
| GET | `/graph/nodes/{node_id}` | `NodeView`, 404 if absent |
| GET | `/graph/nodes/{node_id}/neighbors` | `GraphSliceView` — `?depth=1..3&edge_types=&direction=&as_of=&include_invalidated=` |
| GET | `/graph/nodes/{node_id}/versions` | `VersionChainView` |
| GET | `/graph/nodes/{node_id}/decisions` | `DecisionHistoryView` |
| GET | `/graph/episodes/{episode_id}` | `EpisodeDetailView` |
| GET | `/graph/chains/{chain_id}` | `GraphSliceView` |
| GET | `/debug/traces/{trace_id}` | `TraceView` |
| GET | `/debug/nodes/{node_id}/provenance` | `ProvenanceView` |

Routes stay thin: validate, call one provider method, map to a response model. No logic
worth testing lives in a route handler.

`depth` is `Query(1, ge=1, le=3)` and `limit` is capped at 200 — enforced by the framework,
so a bad request is rejected before any query runs.

## B6. Amendments to Earlier Goals

| Layer | Change | Why |
|---|---|---|
| **Goal 1 — deferral** | `execute_cypher()` is **cancelled**, not deferred. | A general query method breaks Rule 1 and would leak Cypher into the API. Recorded the way Goal 3 recorded the cancelled `api_keys`. |
| **Goal 1/2 — `GraphProvider`** | Seven reads added; `ReadOnlyGraph` extracted. | The read surface, and a structural bar on writing from the API. |
| **Goal 8 — person anchors** | `find_linked_to_person` gains the optional second hop it skipped. | Goal 8 recorded that beliefs reach a person only through an observation and that the second hop "belongs with Goal 11's traversal work". `get_neighborhood` makes it a two-line change. |
| **`pyproject.toml`** | `fastapi` added; `httpx` in the dev group. | First HTTP surface. |

## B7. Test Plan (~85 tests)

| File | Covers |
|---|---|
| `test_graph_queries.py` (~15) | Filter composition, temporal clauses, row tidying. Pure — no database. |
| `test_graph_traversal.py` (~35) | The seven reads against a real Kuzu database: depth, direction, edge filters, version chains forward and backward, withdrawn links hidden and shown, `as_of`, pagination, empty and missing inputs. |
| `test_api_graph.py` (~20) | Every graph endpoint: shape, 404s, parameter validation, depth cap, `truncated`. |
| `test_api_debug.py` (~8) | Trace and provenance, including unknown ids. |
| `test_api_app.py` (~7) | Factory, lifespan opening and closing both stores, health, error handlers, and the assertion that the API package names no write method. |

**The integration test runs the pipeline first.** Rather than seeding nodes by hand,
`test_api_graph.py` builds its graph by calling `run_pipeline` on the same entry Goal 10's
end-to-end test uses. A hand-seeded fixture would agree with whatever shape the test
author imagined; a graph the pipeline actually produced is the one the API has to serve.

## B8. Build Order

1. `queries.py` + its unit tests — pure, fast, no infrastructure.
2. `ReadOnlyGraph` + the seven Protocol signatures.
3. Kuzu implementations, one at a time, each with its traversal tests.
4. `pyproject` dependencies.
5. `schemas.py`, `errors.py`, `deps.py`, `main.py` — app wiring, health endpoint, app tests.
6. `routes/graph.py`, then `routes/debug.py`.
7. The pipeline-backed integration test.
8. Goal 8's second person hop.
9. Doc amendments: `Technical_HLD.md` §3.2 and §7.2 (what the graph route actually
   serves), `Schema.md` (the traversal patterns now supported), `Goal_1_Plan.md`
   (`execute_cypher` cancelled).
10. `Master_Plan.md` checkbox and result line; Section C of this document.

---

# SECTION C — RESULTS

## C1. What Was Built

As planned, with one endpoint more than the ten estimated (eleven, counting `/health`).
`lumen/graph/queries.py` and `lumen/api/` landed with the module layout Section B1 gives.

| Surface | Endpoints |
|---|---|
| Graph | `/graph/stats`, `/graph/nodes`, `/graph/nodes/{id}`, `/graph/nodes/{id}/neighbors`, `/graph/nodes/{id}/versions`, `/graph/nodes/{id}/decisions`, `/graph/episodes/{id}`, `/graph/chains/{id}` |
| Debug | `/debug/traces/{trace_id}`, `/debug/nodes/{id}/provenance` |
| Health | `/health` |

## C2. Deviations From the Plan

1. **Walking out is done a step at a time, not with one variable-length pattern.** The
   plan said "one variable-length Cypher match per direction". Kuzu returns such a match as
   whole paths — a bag of nodes and a bag of relationships — and the relationships carry
   only internal offsets, not the `node_id`s of the two records they joined. Since the two
   ends *are* a link's identity here, that shape is unusable. Expanding one hop at a time
   with `MATCH (a)-[r]->(b) ... RETURN a.node_id, label(r), b.node_id, r` gives the ends
   explicitly, and at a cap of three hops it is three queries rather than one. It also
   means the limit stops the walk rather than trimming an answer already fetched.

2. **The date filter is applied after the walk, not inside it.** By the time the frontier
   is mixed, the tables are mixed too, and four of them have no `valid_from` column at all.
   Naming a column a table lacks is an error in Kuzu rather than an empty result, so the
   condition cannot go in the untyped match. Applied afterwards it is exact and cannot
   crash.

3. **`EdgeRow` and `GraphSlice` live in `provider.py`, beside `ScoredHit`.** The plan put
   `GraphSlice` there and left `EdgeRow` unplaced; both belong with the Protocol that
   returns them.

4. **`NodeListView` is reused for causal chains.** The plan gave `/graph/chains/{id}` a
   `GraphSliceView`. A chain's steps are an ordered list and there are no links worth
   returning among them, so the list shape is the honest one.

## C3. Things Caught While Implementing

Three defects, all in the layer below, all found by running real queries against a real
database rather than by reading the code.

1. **`OpenLoopNode` has `resolution_status`, not `status`.** The filter table claimed a
   plain `status` column for it. Because Kuzu errors on an unknown column rather than
   matching nothing, listing "everything in the graph" crashed instead of coming back
   short. Fixed, and a new test asserts every column named in `FILTER_COLUMNS` exists in
   that table's DDL — the check that would have caught it.

2. **Four tables have no `valid_from`,** not the two the plan assumed:
   `DecisionAuditNode` and `MacroextractionReportNode` join `CausalStepNode` and
   `PersonEntityNode`. Same failure mode. The set is now derived from the schema at import
   rather than hand-written, so it cannot drift.

3. **A node's shape depends on how it was fetched** — 121 columns from an untyped
   `MATCH (n)`, 21 from a typed `MATCH (n:BeliefNode)`, for the same node. A version chain
   assembled as it was walked mixed both, so the same history came back in two different
   shapes depending on where the walk started. Chains now collect ids and fetch them
   together in one call, which is also fewer queries. Tidying at the API boundary already
   hid this from callers; the provider handing back inconsistent rows was still a trap
   waiting for the next reader.

A fourth, smaller: `find_linked_to_person` can now reach the same pattern by two routes —
branched into and later reinforced — so results are de-duplicated. A duplicate wastes one
of very few candidate places.

## C4. What the Tests Cover

138 new tests.

| File | Count | Focus |
|---|---|---|
| `test_graph_queries.py` | 30 | Filter composition, temporal clauses, tidying. No database. |
| `test_graph_traversal.py` | 51 | The seven reads plus the person second hop, against real Kuzu. |
| `test_api_app.py` | 14 | Factory, lifespan, health, error shapes, and that the API cannot write. |
| `test_api_graph.py` | 33 | Every graph endpoint, against a pipeline-built graph. |
| `test_api_debug.py` | 10 | Trace and provenance. |

Three tests are worth naming because they guard a rule rather than a behaviour: the write
methods are absent from `ReadOnlyGraph`, no file under `lumen/api/` names one, and every
verb in the generated OpenAPI document is `GET`.

`test_api_graph.py` and `test_api_debug.py` build their graph by calling `run_pipeline` on
Goal 10's entry. That is the first time the API and the pipeline have been checked against
each other, and it is why the shape mismatches above surfaced here rather than in Goal 12.

## C5. Still Deferred

Unchanged from A4: writes of any kind (Goals 18, 19), the other route groups (Goals 16,
18, 20), auth and WebSockets (Goal 20), the front-end, and search-by-meaning (Goals 13–15).

Added by implementation:

| Item | Target | Why |
|---|---|---|
| `rerun_from_stage` endpoint | Later | The debug view specifies a re-run button. Every stage's payloads are already stored, so it is a small addition — but it is a write, and this goal ships none. |
| Sensitivity-tier filtering | Goal 19 | The explorer's spec mentions it; nothing in the schema records a tier yet. |
| Paging metadata beyond `count` | When a caller needs it | A total across mixed node types costs a count query per table; nothing currently draws a page count. |
