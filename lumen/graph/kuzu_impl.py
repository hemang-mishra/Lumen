"""
KuzuGraphProvider — Kuzu (embedded) implementation of the GraphProvider Protocol.

Kuzu is the "SQLite of graph databases" — embedded, zero-config, file-based.
The Cypher query syntax it uses is identical to Neo4j, so when the project
scales, only the connection string changes.

See: docs/hld/Technical_HLD.md Section 2.2
Schema source: docs/Graph/Schema.md
"""

from __future__ import annotations

import json
import logging
from typing import Any

import kuzu

from lumen.graph.provider import GraphProvider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Edge Registry
#
# Kuzu requires typed edge tables (FROM TableA TO TableB). Unlike Neo4j,
# there is no generic "any-to-any" relationship. We define every valid
# (from_table, to_table, edge_name) triple from Schema.md, and build an
# internal lookup so that write_edge() can resolve the correct node labels
# without doing a Cartesian product scan across all 15 node tables.
# ---------------------------------------------------------------------------

EDGE_REGISTRY: list[tuple[str, str, str]] = [
    # contains — Episode structurally contains its children
    ("EpisodeNode", "ObservationNode", "contains_obs"),
    ("EpisodeNode", "EventNode", "contains_evt"),
    ("EpisodeNode", "SessionNode", "contains_sess"),
    ("EpisodeNode", "CausalChainNode", "contains_chain"),

    # chain_contains — CausalChain contains ordered steps
    ("CausalChainNode", "CausalStepNode", "chain_contains"),

    # same_as — MERGE result
    ("ObservationNode", "PatternNode", "same_as_obs_pat"),
    ("PatternNode", "PatternNode", "same_as_pat_pat"),

    # reinforces — REINFORCE result
    ("ObservationNode", "PatternNode", "reinforces_obs_pat"),
    ("ObservationNode", "BeliefNode", "reinforces_obs_bel"),
    ("EventNode", "PatternNode", "reinforces_evt_pat"),
    ("EventNode", "BeliefNode", "reinforces_evt_bel"),

    # evolved_from — EVOLVE result (new version → old version)
    ("PatternNode", "PatternNode", "evolved_from_pat"),
    ("BeliefNode", "BeliefNode", "evolved_from_bel"),

    # caused_by — causal anchor for EVOLVE / BRANCH
    ("PatternNode", "EventNode", "caused_by_pat_evt"),
    ("BeliefNode", "EventNode", "caused_by_bel_evt"),
    ("PatternNode", "SessionNode", "caused_by_pat_sess"),
    ("BeliefNode", "SessionNode", "caused_by_bel_sess"),

    # branches_to — BRANCH result
    ("ObservationNode", "PatternNode", "branches_to_obs"),
    ("EventNode", "PatternNode", "branches_to_evt"),
    ("SessionNode", "PatternNode", "branches_to_sess"),

    # contradicts — CONTRADICT result
    ("ContradictionNode", "BeliefNode", "contradicts"),

    # dialectic — DIALECTIC result (any combo of Belief/Pattern)
    ("BeliefNode", "BeliefNode", "dialectic_bel_bel"),
    ("PatternNode", "PatternNode", "dialectic_pat_pat"),
    ("BeliefNode", "PatternNode", "dialectic_bel_pat"),
    ("PatternNode", "BeliefNode", "dialectic_pat_bel"),

    # regulates — REGULATE result
    ("SessionNode", "PatternNode", "regulates_sess"),
    ("ObservationNode", "PatternNode", "regulates_obs"),

    # mentions — person entity references
    ("ObservationNode", "PersonEntityNode", "mentions_obs"),
    ("EventNode", "PersonEntityNode", "mentions_evt"),
    ("SessionNode", "PersonEntityNode", "mentions_sess"),

    # decided_by — meta-edge linking reconciliation results to audit
    ("ObservationNode", "DecisionAuditNode", "decided_by_obs"),
    ("PatternNode", "DecisionAuditNode", "decided_by_pat"),
    ("BeliefNode", "DecisionAuditNode", "decided_by_bel"),
    ("EventNode", "DecisionAuditNode", "decided_by_evt"),
    ("ContradictionNode", "DecisionAuditNode", "decided_by_con"),

    # analyzed_in — macroextraction coverage
    ("EpisodeNode", "MacroextractionReportNode", "analyzed_in"),

    # alias_of — cross-entry person merge
    ("PersonEntityNode", "PersonEntityNode", "alias_of"),

    # investigated_by — open loop → episode link
    ("OpenLoopNode", "EpisodeNode", "investigated_by"),

    # closes — episode resolves an open loop
    ("EpisodeNode", "OpenLoopNode", "closes"),

    # follows_from — intra-session episode ordering
    ("EpisodeNode", "EpisodeNode", "follows_from"),

    # adopted_as — principle references
    ("ObservationNode", "AdoptedPrincipleNode", "adopted_as_obs"),
    ("SessionNode", "AdoptedPrincipleNode", "adopted_as_sess"),

    # superseded_by — principle version chain
    ("AdoptedPrincipleNode", "AdoptedPrincipleNode", "superseded_by"),

    # failed_extraction — validation-failed observations
    ("EpisodeNode", "ObservationNode", "failed_extraction"),
]

# Build lookup: edge_name → (from_table, to_table)
_EDGE_LOOKUP: dict[str, tuple[str, str]] = {
    name: (from_t, to_t) for from_t, to_t, name in EDGE_REGISTRY
}

# Collect all valid node table names for validation
_VALID_NODE_TABLES: set[str] = set()
for from_t, to_t, _ in EDGE_REGISTRY:
    _VALID_NODE_TABLES.add(from_t)
    _VALID_NODE_TABLES.add(to_t)


# ---------------------------------------------------------------------------
# Node Table DDL
# ---------------------------------------------------------------------------

NODE_TABLES: dict[str, str] = {
    "EpisodeNode": (
        "CREATE NODE TABLE EpisodeNode ("
        "node_id STRING, entry_id STRING, occurred_at STRING, created_at STRING, "
        "valid_from STRING, event_date STRING, session_label STRING, "
        "source_modality STRING, entry_class STRING, episode_summary STRING, "
        "historical_era STRING, episode_index INT64, total_episodes_in_entry INT64, "
        "coreference_map_id STRING, reconciliation_status STRING, "
        "raw_text_hash STRING, language_tags STRING, overarching_themes STRING, "
        "PRIMARY KEY(node_id))"
    ),
    "ObservationNode": (
        "CREATE NODE TABLE ObservationNode ("
        "node_id STRING, episode_id STRING, occurred_at STRING, created_at STRING, "
        "valid_from STRING, type STRING, content STRING, signal_strength STRING, "
        "provenance STRING, open_loop_ref STRING, extraction_confidence STRING, "
        "status STRING, extraction_model STRING, extraction_attempt INT64, "
        "raw_evidence STRING, person_refs STRING, "
        "PRIMARY KEY(node_id))"
    ),
    "EventNode": (
        "CREATE NODE TABLE EventNode ("
        "node_id STRING, episode_id STRING, occurred_at STRING, created_at STRING, "
        "valid_from STRING, event_summary STRING, signal_strength STRING, "
        "status STRING, raw_evidence STRING, person_refs STRING, "
        "PRIMARY KEY(node_id))"
    ),
    "SessionNode": (
        "CREATE NODE TABLE SessionNode ("
        "node_id STRING, episode_id STRING, occurred_at STRING, created_at STRING, "
        "valid_from STRING, event_date STRING, session_label STRING, "
        "session_summary STRING, signal_strength STRING, status STRING, "
        "participant_entities STRING, "
        "PRIMARY KEY(node_id))"
    ),
    "CausalChainNode": (
        "CREATE NODE TABLE CausalChainNode ("
        "node_id STRING, episode_id STRING, created_at STRING, valid_from STRING, "
        "chain_summary STRING, is_anticipatory BOOLEAN, step_count INT64, "
        "status STRING, "
        "PRIMARY KEY(node_id))"
    ),
    "CausalStepNode": (
        "CREATE NODE TABLE CausalStepNode ("
        "node_id STRING, chain_id STRING, step_index INT64, step_type STRING, "
        "content STRING, branch_id STRING, created_at STRING, "
        "PRIMARY KEY(node_id))"
    ),
    "PatternNode": (
        "CREATE NODE TABLE PatternNode ("
        "node_id STRING, version INT64, previous_version_id STRING, "
        "created_at STRING, valid_from STRING, last_reinforced_at STRING, "
        "pattern_name STRING, pattern_description STRING, domain STRING, "
        "signal_strength STRING, provenance STRING, evidence_count INT64, "
        "query_frequency INT64, is_canonical BOOLEAN, status STRING, "
        "era_tag STRING, archetype_tags STRING, "
        "PRIMARY KEY(node_id))"
    ),
    "BeliefNode": (
        "CREATE NODE TABLE BeliefNode ("
        "node_id STRING, version INT64, previous_version_id STRING, "
        "created_at STRING, valid_from STRING, last_reinforced_at STRING, "
        "belief_statement STRING, belief_source_summary STRING, domain STRING, "
        "signal_strength STRING, provenance STRING, evidence_count INT64, "
        "query_frequency INT64, is_contradicted BOOLEAN, "
        "contradiction_node_id STRING, version_delta STRING, status STRING, "
        "era_tag STRING, "
        "PRIMARY KEY(node_id))"
    ),
    "LessonNode": (
        "CREATE NODE TABLE LessonNode ("
        "node_id STRING, created_at STRING, valid_from STRING, "
        "lesson_statement STRING, domain STRING, signal_strength STRING, "
        "lesson_confidence DOUBLE, status STRING, evidence_episodes STRING, "
        "PRIMARY KEY(node_id))"
    ),
    "AdoptedPrincipleNode": (
        "CREATE NODE TABLE AdoptedPrincipleNode ("
        "node_id STRING, created_at STRING, valid_from STRING, adopted_at STRING, "
        "principle_statement STRING, principle_name STRING, domain STRING, "
        "lifecycle_state STRING, lifecycle_updated_at STRING, "
        "source_session_id STRING, provenance STRING, supersedes_id STRING, "
        "last_referenced_at STRING, evidence_count INT64, status STRING, "
        "lifecycle_history STRING, parent_belief_ids STRING, "
        "PRIMARY KEY(node_id))"
    ),
    "PersonEntityNode": (
        "CREATE NODE TABLE PersonEntityNode ("
        "node_id STRING, canonical_name STRING, first_mentioned_at STRING, "
        "last_mentioned_at STRING, mention_count INT64, "
        "relationship_to_user STRING, relationship_sentiment_trend STRING, "
        "is_canonical BOOLEAN, status STRING, aliases STRING, "
        "linked_observation_types STRING, merged_from STRING, "
        "PRIMARY KEY(node_id))"
    ),
    "DecisionAuditNode": (
        "CREATE NODE TABLE DecisionAuditNode ("
        "node_id STRING, created_at STRING, action STRING, "
        "source_observation_id STRING, target_node_id STRING, "
        "edge_type_created STRING, edge_id STRING, confidence DOUBLE, "
        "confidence_runner_up DOUBLE, runner_up_action STRING, "
        "delta_description STRING, model_used STRING, routing_tier STRING, "
        "hitl_resolved BOOLEAN, hitl_resolution_timestamp STRING, "
        "hitl_resolution_user_choice STRING, snooze_count INT64, "
        "last_snoozed_at STRING, candidate_retrieval_source STRING, "
        "structural_anchor_type STRING, structural_anchor_value STRING, "
        "co_created_origin BOOLEAN, status STRING, rollback_pointer STRING, "
        "PRIMARY KEY(node_id))"
    ),
    "ContradictionNode": (
        "CREATE NODE TABLE ContradictionNode ("
        "node_id STRING, created_at STRING, valid_from STRING, "
        "belief_a_id STRING, belief_b_id STRING, "
        "contradiction_summary STRING, decision_id STRING, "
        "resolution_status STRING, resolved_at STRING, "
        "resolution_decision_id STRING, "
        "PRIMARY KEY(node_id))"
    ),
    "MacroextractionReportNode": (
        "CREATE NODE TABLE MacroextractionReportNode ("
        "node_id STRING, created_at STRING, report_type STRING, "
        "period_start STRING, period_end STRING, episodes_analyzed INT64, "
        "archetype_shift_detected BOOLEAN, model_used STRING, "
        "status STRING, report_content STRING, "
        "PRIMARY KEY(node_id))"
    ),
    "OpenLoopNode": (
        "CREATE NODE TABLE OpenLoopNode ("
        "node_id STRING, created_at STRING, valid_from STRING, "
        "loop_description STRING, loop_category STRING, provenance STRING, "
        "source_episode_id STRING, resolution_status STRING, "
        "resolved_at STRING, resolution_summary STRING, "
        "last_referenced_at STRING, linked_patterns STRING, "
        "linked_beliefs STRING, "
        "PRIMARY KEY(node_id))"
    ),
}


class KuzuGraphProvider(GraphProvider):
    """
    Kuzu implementation of the GraphProvider Protocol.

    Manages an embedded Kuzu database instance, schema initialization,
    and typed Cypher operations.

    Usage:
        provider = KuzuGraphProvider("/path/to/db")
        provider.init_schema()
        provider.write_node("EpisodeNode", {"node_id": "ep_001", ...})
        provider.close()

    Or as a context manager:
        with KuzuGraphProvider("/path/to/db") as provider:
            provider.init_schema()
            ...
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.db = kuzu.Database(db_path)
        self.conn = kuzu.Connection(self.db)
        logger.info("KuzuGraphProvider initialized at %s", db_path)

    def __enter__(self) -> KuzuGraphProvider:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        """Release database resources (connection and file locks)."""
        if hasattr(self, "conn") and self.conn is not None:
            del self.conn
            self.conn = None  # type: ignore[assignment]
        if hasattr(self, "db") and self.db is not None:
            del self.db
            self.db = None  # type: ignore[assignment]
        logger.info("KuzuGraphProvider closed for %s", self.db_path)

    # ------------------------------------------------------------------
    # Schema Initialization
    # ------------------------------------------------------------------

    def init_schema(self) -> None:
        """
        Initialize the graph database schema if it doesn't exist.
        Idempotent — safe to call on an already-initialized database.
        """
        existing_tables = self._get_existing_tables()

        # Create Node Tables
        for table_name, ddl in NODE_TABLES.items():
            if table_name not in existing_tables:
                logger.debug("Creating node table: %s", table_name)
                self.conn.execute(ddl)

        # Create Edge Tables
        for from_table, to_table, edge_name in EDGE_REGISTRY:
            if edge_name not in existing_tables:
                ddl = (
                    f"CREATE REL TABLE {edge_name} ("
                    f"FROM {from_table} TO {to_table}, "
                    "valid_from STRING, invalidated_at STRING, "
                    "decision_id STRING, confidence DOUBLE)"
                )
                logger.debug("Creating edge table: %s (%s → %s)", edge_name, from_table, to_table)
                self.conn.execute(ddl)

        logger.info(
            "Schema initialized: %d node tables, %d edge tables",
            len(NODE_TABLES), len(EDGE_REGISTRY),
        )

    def _get_existing_tables(self) -> set[str]:
        """Query Kuzu for all existing table names."""
        existing: set[str] = set()
        try:
            res = self.conn.execute("CALL show_tables() RETURN name;")
            while res.has_next():
                existing.add(res.get_next()[0])
        except RuntimeError as e:
            # Only catch Kuzu-specific errors; let other exceptions propagate
            logger.warning("Could not list existing tables: %s", e)
        return existing

    # ------------------------------------------------------------------
    # Write Operations
    # ------------------------------------------------------------------

    def write_node(self, node_type: str, properties: dict[str, Any]) -> str:
        """Write a node to the graph and return its node_id."""
        node_id = properties.get("node_id")
        if not node_id:
            raise ValueError("node_id is required in properties")

        if node_type not in NODE_TABLES:
            raise ValueError(
                f"Unknown node type '{node_type}'. "
                f"Valid types: {sorted(NODE_TABLES.keys())}"
            )

        # Convert list/dict properties to JSON strings for Kuzu compatibility
        processed_props: dict[str, Any] = {}
        for k, v in properties.items():
            if isinstance(v, (list, dict)):
                processed_props[k] = json.dumps(v)
            else:
                processed_props[k] = v

        keys = list(processed_props.keys())
        set_clause = ", ".join(f"{k}: ${k}" for k in keys)
        query = f"CREATE (n:{node_type} {{{set_clause}}})"

        self.conn.execute(query, processed_props)
        logger.debug("Wrote node %s (type=%s)", node_id, node_type)
        return node_id

    def write_edge(
        self,
        edge_type: str,
        from_id: str,
        to_id: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """
        Write a directed edge between two existing nodes.

        Uses the EDGE_REGISTRY to resolve the correct FROM/TO node table types,
        avoiding Cartesian product scans across all 15 node tables.
        """
        if edge_type not in _EDGE_LOOKUP:
            raise ValueError(
                f"Unknown edge type '{edge_type}'. "
                f"Valid types: {sorted(_EDGE_LOOKUP.keys())}"
            )

        from_table, to_table = _EDGE_LOOKUP[edge_type]
        properties = properties or {}

        keys = list(properties.keys())
        set_clause = ""
        if keys:
            set_clause = "{" + ", ".join(f"{k}: ${k}" for k in keys) + "}"

        # Type-specific MATCH eliminates Cartesian product scan
        query = (
            f"MATCH (a:{from_table}), (b:{to_table}) "
            f"WHERE a.node_id = $from_id AND b.node_id = $to_id "
            f"CREATE (a)-[r:{edge_type} {set_clause}]->(b)"
        )

        params: dict[str, Any] = {"from_id": from_id, "to_id": to_id}
        params.update(properties)

        self.conn.execute(query, params)
        logger.debug("Wrote edge %s: %s → %s", edge_type, from_id, to_id)

    # ------------------------------------------------------------------
    # Read Operations
    # ------------------------------------------------------------------

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Retrieve a node's properties by its ID. Returns None if not found."""
        res = self.conn.execute(
            "MATCH (n) WHERE n.node_id = $node_id RETURN n",
            {"node_id": node_id},
        )
        if res.has_next():
            return res.get_next()[0]
        return None

    def get_nodes_by_ids(self, node_ids: list[str]) -> list[dict[str, Any]]:
        """
        Retrieve multiple nodes by their IDs.
        
        Used by the HLD read path (Section 4.2):
            candidate_ids = vector_store.hybrid_search(...)
            candidates = graph_store.get_nodes_by_ids(candidate_ids)
        """
        if not node_ids:
            return []

        res = self.conn.execute(
            "MATCH (n) WHERE n.node_id IN $node_ids RETURN n",
            {"node_ids": node_ids},
        )
        nodes: list[dict[str, Any]] = []
        while res.has_next():
            nodes.append(res.get_next()[0])
        return nodes
