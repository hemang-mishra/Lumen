# Goal 1: Database Initialization Protocol

**Status:** ✅ Complete
**Tests:** 38 passing, 98% coverage
**Implemented by:** Code review + refactor cycle

---

## Objective

Establish the database layer for Lumen without introducing any LLM logic. Kuzu (embedded graph store) and Qdrant (local/memory vector store) behind abstract Provider Protocols, with a central configuration module and proper pytest test suites.

## Directory Structure (as implemented)

```text
lumen/
├── __init__.py               # Package root
├── config.py                 # AppConfig — central provider configuration
├── graph/
│   ├── __init__.py           # Re-exports GraphProvider, KuzuGraphProvider
│   ├── provider.py           # GraphProvider Protocol (6 methods)
│   └── kuzu_impl.py          # KuzuGraphProvider + NODE_TABLES + EDGE_REGISTRY
├── vector/
│   ├── __init__.py           # Re-exports VectorProvider, QdrantVectorProvider
│   ├── provider.py           # VectorProvider Protocol (4 methods)
│   └── qdrant_impl.py        # QdrantVectorProvider
└── tests/
    ├── __init__.py
    ├── test_kuzu_impl.py     # 27 tests (5 test classes)
    └── test_qdrant_impl.py   # 11 tests (4 test classes)
```

## What Was Built

### 1. Central Configuration ([`lumen/config.py`](file:///Users/hemangmishra/Projects/Lumen/lumen/config.py))
- `GraphConfig` — `db_path` (default `./lumen_graph.db`, overridable via `LUMEN_GRAPH_DB_PATH` env var)
- `VectorConfig` — `location`, `collection_name`, `vector_size` (overridable via `LUMEN_VECTOR_LOCATION`)
- `AppConfig` — top-level frozen dataclass composing both configs

### 2. GraphProvider Protocol ([`lumen/graph/provider.py`](file:///Users/hemangmishra/Projects/Lumen/lumen/graph/provider.py))
6 methods defined:
- `init_schema()` — idempotent schema creation
- `write_node(node_type, properties)` — with node_type validation
- `write_edge(edge_type, from_id, to_id, properties)` — with typed MATCH (no Cartesian product)
- `get_node(node_id)` — single node lookup
- `get_nodes_by_ids(node_ids)` — batch lookup (HLD Section 4.2 read path)
- `close()` — resource cleanup

### 3. KuzuGraphProvider ([`lumen/graph/kuzu_impl.py`](file:///Users/hemangmishra/Projects/Lumen/lumen/graph/kuzu_impl.py))
- **15 node tables** — all from [`docs/Graph/Schema.md`](file:///Users/hemangmishra/Projects/Lumen/docs/Graph/Schema.md)
- **43 edge tables** — stored in `EDGE_REGISTRY` with `_EDGE_LOOKUP` dict for O(1) type resolution
- Context manager support (`with KuzuGraphProvider(...) as p:`)
- Structured logging via `logging.getLogger(__name__)`
- Catches only `RuntimeError` (not bare `Exception`) in `_get_existing_tables()`

### 4. VectorProvider Protocol ([`lumen/vector/provider.py`](file:///Users/hemangmishra/Projects/Lumen/lumen/vector/provider.py))
4 methods defined:
- `init_collection()` — idempotent
- `upsert(node_id, vector, payload)` — with deterministic UUID5
- `hybrid_search(dense_vector, sparse_vector=None, limit=10)` — sparse made optional, logs warning when provided
- `close()` — resource cleanup

### 5. QdrantVectorProvider ([`lumen/vector/qdrant_impl.py`](file:///Users/hemangmishra/Projects/Lumen/lumen/vector/qdrant_impl.py))
- Configurable `collection_name` and `vector_size` via constructor
- Payload indexes on `node_type`, `status`, `signal_strength`
- Context manager support
- Sparse BM25 search honestly deferred (logs warning, does not silently ignore)

### 6. Test Suites

**[`test_kuzu_impl.py`](file:///Users/hemangmishra/Projects/Lumen/lumen/tests/test_kuzu_impl.py)** — 27 tests:
| Class | Tests | Coverage |
|---|---|---|
| `TestSchemaInit` | All 15 node tables created, all 43 edge tables created, idempotency | Schema DDL |
| `TestWriteNode` | Episode, Observation, Pattern, Belief, Event, DecisionAudit, JSON serialization, missing ID error, invalid type error | `write_node()` |
| `TestWriteEdge` | contains, reinforces, evolved_from, caused_by, mentions, decided_by, invalid edge error, edge properties | `write_edge()` |
| `TestReadOperations` | get_node (found/missing), get_nodes_by_ids (batch/empty) | `get_node()`, `get_nodes_by_ids()` |
| `TestContextManager` | with-statement lifecycle, resource cleanup | `close()` |

**[`test_qdrant_impl.py`](file:///Users/hemangmishra/Projects/Lumen/lumen/tests/test_qdrant_impl.py)** — 11 tests:
| Class | Tests | Coverage |
|---|---|---|
| `TestCollectionInit` | Creation, idempotency, custom name, custom vector size | `init_collection()` |
| `TestUpsert` | Basic upsert, idempotent upsert, payload contains node_id | `upsert()` |
| `TestSearch` | Correct IDs returned, limit respected, empty collection, sparse vector warning | `hybrid_search()` |
| `TestContextManager` | with-statement lifecycle | `close()` |

## Key Design Decisions

1. **`EDGE_REGISTRY` + `_EDGE_LOOKUP`:** Kuzu requires typed edge tables (FROM X TO Y). We define all valid triples as a module-level list, then build an O(1) lookup dict so `write_edge()` resolves node labels without scanning all 15 tables.
2. **`NODE_TABLES` as dict:** DDL strings keyed by table name instead of raw list, enabling O(1) validation in `write_node()`.
3. **Sparse search deferred honestly:** Rather than silently ignoring the `sparse_vector` parameter, we log a warning. BM25 will be enabled when `SparseVectorConfig` is added to Qdrant collection setup.
4. **Context managers:** Both providers support `with` statements to prevent resource leaks (Kuzu file locks, Qdrant connections).

## What's Deferred to Later Goals

| Item | Target Goal |
|---|---|
| Pydantic-typed `write_node()` signature | Goal 2 |
| Sparse/BM25 vector configuration | Goal 8 |
| DDL extraction to structured schema builder (P2 from review) | Backlog |
| `execute_cypher()` for ad-hoc traversal | Goal 11 |
