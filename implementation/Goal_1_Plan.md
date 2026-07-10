# Goal 1: Database Initialization Protocol

This document details the exact implementation steps for Goal 1 of the Master Plan.

## Objective
Establish the database layer for Lumen without introducing any LLM logic. We will use **Kuzu** (embedded graph store) and **Qdrant** (local/memory vector store). We will define the abstract Provider Protocols to ensure production-readiness, and initialize the complete Cypher schema for Kuzu based on `Schema.md`.

## 1. Directory Structure

We will create the following structure under a new `lumen/` Python package:

```text
lumen/
├── graph/
│   ├── __init__.py
│   ├── provider.py       # GraphProvider Protocol
│   ├── schema.py         # Kuzu DDL (Data Definition Language) queries
│   └── kuzu_impl.py      # Kuzu implementation of GraphProvider
├── vector/
│   ├── __init__.py
│   ├── provider.py       # VectorProvider Protocol
│   └── qdrant_impl.py    # Qdrant implementation of VectorProvider
└── tests/
    └── test_db_init.py   # E2E test for DB initialization and basic writes
```

## 2. Graph Provider & Kuzu Schema

### The Protocol (`lumen/graph/provider.py`)
We will define a `typing.Protocol` named `GraphProvider` with the following interface:
- `init_schema()`
- `write_node(node_type: str, properties: dict)`
- `write_edge(edge_type: str, from_id: str, to_id: str, properties: dict)`
- `get_node(node_id: str) -> dict`

### Kuzu Schema Initialization (`lumen/graph/schema.py`)
Kuzu requires explicit table definitions. We will define a sequence of `CREATE NODE TABLE` and `CREATE REL TABLE` queries mapping exactly to `Schema.md`.

**Node Tables:**
1. `EpisodeNode`
2. `ObservationNode`
3. `EventNode`
4. `SessionNode`
5. `CausalChainNode`
6. `CausalStepNode`
7. `PatternNode`
8. `BeliefNode`
9. `LessonNode`
10. `AdoptedPrincipleNode`
11. `PersonEntityNode`
12. `DecisionAuditNode`
13. `ContradictionNode`
14. `MacroextractionReportNode`
15. `OpenLoopNode`

**Rel (Edge) Tables:**
1. `contains` (FROM EpisodeNode TO ObservationNode/EventNode/SessionNode/CausalChainNode)
2. `chain_contains` (FROM CausalChainNode TO CausalStepNode)
3. `same_as` (FROM ObservationNode/PatternNode TO PatternNode)
4. `reinforces` (FROM ObservationNode/EventNode TO PatternNode/BeliefNode)
5. `evolved_from` (FROM PatternNode/BeliefNode TO PatternNode/BeliefNode)
6. `caused_by` (FROM PatternNode/BeliefNode TO EventNode/SessionNode)
7. `branches_to` (FROM ObservationNode/EventNode/SessionNode TO PatternNode)
8. `contradicts` (FROM ContradictionNode TO BeliefNode)
9. `dialectic` (FROM BeliefNode/PatternNode TO BeliefNode/PatternNode)
10. `regulates` (FROM SessionNode/ObservationNode TO PatternNode)
11. `mentions` (FROM ObservationNode/EventNode/SessionNode TO PersonEntityNode)
12. `decided_by` (FROM ANY_EDGE TO DecisionAuditNode)
13. `analyzed_in` (FROM EpisodeNode TO MacroextractionReportNode)
14. `alias_of` (FROM PersonEntityNode TO PersonEntityNode)
15. `investigated_by` (FROM OpenLoopNode TO EpisodeNode)
16. `closes` (FROM EpisodeNode TO OpenLoopNode)
17. `follows_from` (FROM EpisodeNode TO EpisodeNode)
18. `adopted_as` (FROM ObservationNode/SessionNode TO AdoptedPrincipleNode)
19. `superseded_by` (FROM AdoptedPrincipleNode TO AdoptedPrincipleNode)
20. `failed_extraction` (FROM EpisodeNode TO ObservationNode)

### Kuzu Implementation (`lumen/graph/kuzu_impl.py`)
Will wrap `import kuzu`, manage the database connection (`kuzu.Database(db_path)`), and execute the DDL queries on initialization if the tables do not exist.

## 3. Vector Provider & Qdrant Setup

### The Protocol (`lumen/vector/provider.py`)
- `init_collection()`
- `upsert(node_id: str, vector: list[float], payload: dict)`
- `hybrid_search(dense_vector: list[float], sparse_vector: dict, limit: int) -> list[str]`

### Qdrant Implementation (`lumen/vector/qdrant_impl.py`)
Will wrap `qdrant_client.QdrantClient`. For local development, we will support `:memory:` and local path modes.
- **Collection Name:** `lumen_nodes`
- **Dense Vector Config:** Size 768 (matching `text-embedding-004` which is default), Distance `Cosine`.
- **Sparse Vector Config:** Configured for BM25 hybrid search.
- **Payload Indexes:** Will create indexes for `node_type`, `status`, and `signal_strength` to ensure fast filtering during Pass A retrieval.

## 4. Verification and Testing

We will write `tests/test_db_init.py` which will:
1. Initialize an in-memory Qdrant client and a temporary-directory Kuzu database.
2. Trigger `init_schema()` and `init_collection()`.
3. Create a mock `EpisodeNode` and a mock `ObservationNode`.
4. Create a `contains` edge between them.
5. Insert mock embeddings into Qdrant.
6. Assert that the nodes and edges can be retrieved via Cypher from Kuzu.
7. Assert that the node IDs can be retrieved via a mock vector search from Qdrant.

## Next Step

Once this plan is approved, I will create the python files, write the implementations, run the test script, and verify that the database layer is correctly instantiated.
