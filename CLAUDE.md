# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What Lumen Is

Lumen converts unstructured voice/text journal entries into a versioned, append-only
knowledge graph of the user's psychological history (beliefs, patterns, lessons, causal
chains), then serves that history back via invisible RAG injection during conversation.

**The pipeline is the product.** Value lives in the chain of transformations from raw
voice → structured knowledge, not in storage. Every transformation stage must be
independently observable, replayable, and reversible.

## Commands

```bash
uv sync                                    # install deps (incl. dev group)
uv run pytest                              # run the full test suite
uv run pytest lumen/tests/test_kuzu_impl.py -v   # single file
uv run pytest --cov=lumen --cov-report=term-missing   # coverage
```

Target: **≥90% coverage on new code.** Python 3.13, `uv` is the package manager
(never `pip install` directly — use `uv add`).

## Repo Layout

```
lumen/               ← the application (all code lives here)
├── config.py        ← AppConfig: the ONLY place infra choices are made
├── graph/           ← GraphProvider Protocol + kuzu_impl.py (NODE_TABLES, EDGE_REGISTRY)
├── vector/          ← VectorProvider Protocol + qdrant_impl.py
├── schemas/         ← Pydantic models (nodes, edges, pipeline DTOs)  [Goal 2]
├── operational/     ← SQLAlchemy/SQLite ops DB                       [Goal 3]
├── providers/       ← LLM/embedding providers behind Protocols       [Goal 4]
├── pipeline/        ← Stage 0–4 pure functions + orchestrator        [Goals 5–10]
├── api/             ← FastAPI BFF                                    [Goals 11+]
└── tests/           ← pytest suites, mirrors module names
docs/                ← the specification. Read before writing code.
implementation/      ← Master_Plan.md + per-goal plans (Goal_N_Plan.md)
```

## Documentation Is the Spec

This project is **doc-first**. `docs/` is the source of truth, not the code. Before
implementing anything, read the relevant spec:

| Topic | Doc |
|---|---|
| System overview, 7-step journey | `docs/hld/HLDv2.md` |
| Tech stack, service decomposition, pipeline DTOs | `docs/hld/Technical_HLD.md` |
| **Node/edge schema, temporal model, retrieval score** | `docs/Graph/Schema.md` |
| Stage 0 — ASR cleaning, quality gate, coreference | `docs/Extraction/Preprocessing.md` |
| Stage 1 — observation type enum dictionary, causal chains | `docs/Extraction/Microextraction.md` |
| Stage 3 — 8 actions, confidence thresholds, HITL | `docs/Extraction/Reconciliation.md` |
| Validation rules, trust/recency weights, Late Binding | `docs/Extraction/Architecture.md` |
| Query-time RAG injection | `docs/Query/Conversational_RAG_Mode.md` |
| Build sequence, goal breakdown | `implementation/Master_Plan.md` |

If code and docs disagree, that is a **bug report**, not license to improvise. Surface
the discrepancy rather than silently picking one.

## Non-Negotiable Architecture Rules

From `docs/hld/Technical_HLD.md` Section 8:

1. **Providers are always Protocols.** No `import kuzu`, `import qdrant_client`,
   `google.generativeai`, or `ollama` outside their own `*_impl.py` / `providers/`
   module. Business logic has zero knowledge of vendor SDKs.
2. **Pipeline stages are pure functions.** Each stage takes a Pydantic input model,
   returns a Pydantic output model. No global state, no DB calls inside a stage —
   the orchestrator handles persistence. Any stage must be unit-testable with no
   infrastructure.
3. **The graph is append-only; there is one write path.** Content nodes are never
   updated in place — changes produce new versioned nodes with `evolved_from` edges.
   Edges soft-delete via `invalidated_at`. No component writes to the graph except
   through `GraphProvider`.
4. **Every boundary crossing is schema-validated.** Pydantic models are the contracts,
   including inside the personal-mode monolith.

Two more from `docs/Graph/Schema.md`:

5. **Bipartite causal graph.** A `BeliefNode`/`PatternNode` cannot EVOLVE or CONTRADICT
   out of nowhere — it must be anchored by an intervening `EventNode` or `SessionNode`
   via a `caused_by` edge.
6. **Every reconciliation action writes a `DecisionAuditNode`** with a rollback pointer.
   No exceptions.

## Working Conventions

- **Kuzu needs typed edge tables.** Every valid `(from_table, to_table, edge_name)`
  triple lives in `EDGE_REGISTRY` in `lumen/graph/kuzu_impl.py`. Adding an edge means
  adding a registry entry, not writing ad-hoc Cypher. Lists/dicts are JSON-serialized
  to STRING columns at the provider boundary.
- **`node_id` is the universal join key** across Kuzu, Qdrant, and SQLite. A graph
  write and its vector upsert always use the same `node_id`.
- **Everything gets a `trace_id`** once Goal 3b lands — logs, Pydantic models, graph
  writes, LLM calls.
- **Comments explain the code, not the spec.** Every module, class, and non-obvious
  function carries a docstring written in plain language that explains what it does and
  why, so the code reads on its own. Do **not** cite doc paths or section numbers in
  docstrings or comments — spec traceability belongs in `implementation/Goal_N_Plan.md`,
  which is where discrepancies and design rationale get recorded. Existing `See: docs/...`
  references are legacy; strip them when you touch that code.
- **Tests mirror modules**: `lumen/tests/test_<module>.py`, grouped into `Test*` classes
  by behavior, using `tmp_path` fixtures for anything on disk.
- **Log, don't silently degrade.** If a capability is deferred (e.g. sparse BM25 search),
  log a warning rather than ignoring the parameter.

## Goal Workflow

Work proceeds goal-by-goal per `implementation/Master_Plan.md`, one branch per goal
(`goal1`, `goal2`, …). Each completed goal:

1. Ships code + tests at ≥90% coverage.
2. Gets a `implementation/Goal_N_Plan.md` documenting what was built, key design
   decisions, and what was deferred to which later goal.
3. Updates the checkbox and result line in `Master_Plan.md`.

Use `implementation/Goal_1_Plan.md` as the template.
