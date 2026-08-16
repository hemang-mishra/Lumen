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
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any

import kuzu

from lumen.graph import queries
from lumen.graph.provider import EdgeRow, GraphProvider, GraphSlice
from lumen.schemas.base import GraphNode

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

    # branches_to — BRANCH result (new PatternNode or new BeliefNode)
    ("ObservationNode", "PatternNode", "branches_to_obs_pat"),
    ("EventNode", "PatternNode", "branches_to_evt_pat"),
    ("SessionNode", "PatternNode", "branches_to_sess_pat"),
    ("ObservationNode", "BeliefNode", "branches_to_obs_bel"),
    ("EventNode", "BeliefNode", "branches_to_evt_bel"),
    ("SessionNode", "BeliefNode", "branches_to_sess_bel"),

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
    ("SessionNode", "DecisionAuditNode", "decided_by_sess"),
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
# Anchor lookup tables
#
# Three small maps that say how each node table answers a question the
# anchor lookups ask. They exist so those methods stay one query each
# instead of a chain of special cases.
# ---------------------------------------------------------------------------

# Which edge leads from a node table to a person. Only these three tables
# have one: a belief or a pattern reaches a person only through the
# observation that produced it, which is a second hop nothing needs yet.
MENTIONS_EDGES: dict[str, str] = {
    "ObservationNode": "mentions_obs",
    "EventNode": "mentions_evt",
    "SessionNode": "mentions_sess",
}

# The links a decision makes from a finding to a standing record, per kind
# of standing record. These are the second step for anything that never
# names a person itself: a belief about someone exists because a finding
# about them turned into it.
_FINDING_TO_STANDING: dict[str, tuple[str, ...]] = {
    "PatternNode": ("branches_to_obs_pat", "reinforces_obs_pat", "same_as_obs_pat"),
    "BeliefNode": ("branches_to_obs_bel", "reinforces_obs_bel"),
}

_REACHED_THROUGH_A_FINDING: frozenset[str] = frozenset(_FINDING_TO_STANDING)


def _first_unique(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """
    The first few distinct records, keeping the order they were found in.

    One record can be reached twice — a belief that a finding both branched
    into and later reinforced — and offering the same thing twice in a short
    list of candidates wastes one of very few places.
    """
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        node_id = row.get("node_id")
        if node_id and node_id not in seen:
            seen[str(node_id)] = row
    return list(seen.values())[:limit]

# Where each table records the past period a node belongs to. The column is
# not named the same in both places, and only these three record one at all.
ERA_COLUMNS: dict[str, str] = {
    "PatternNode": "era_tag",
    "BeliefNode": "era_tag",
    "EpisodeNode": "historical_era",
}

# How to tell that a node is still in play. Most tables answer with a plain
# status column; episodes have none and are always considered current.
_ACTIVE_CLAUSES: dict[str, str] = {
    "EpisodeNode": "true",
}


def _active_clause(table: str) -> str:
    """The condition that means a node of this table is still live."""
    return _ACTIVE_CLAUSES.get(table, "n.status = 'ACTIVE'")


# Which episodes still have reconciliation outstanding. An episode is
# suspended when one of its findings is waiting for the user to decide
# something, and pending re-reconciliation when a past decision was rolled
# back. Both mean the same thing here: what that episode holds has not been
# settled yet, so it is still worth surfacing.
UNSETTLED_EPISODE_STATUSES: tuple[str, ...] = ("PENDING_RERECONCILIATION", "SUSPENDED")


# The kinds of record that keep a version history. Only these two are ever
# replaced by a newer version of themselves; everything else is written once
# and stands.
_VERSIONED_TABLES: frozenset[str] = frozenset({"PatternNode", "BeliefNode"})

# The links meaning "this came out of that". Asking what an episode produced
# follows only these — an episode also points at the episode before it and at
# any report that analysed it, and following those would answer a wider
# question than the one asked.
CONTAINMENT_EDGES: tuple[str, ...] = (
    "contains_obs",
    "contains_evt",
    "contains_sess",
    "contains_chain",
    "failed_extraction",
)


def _as_text(value: datetime | date) -> str:
    """A stored timestamp's own form, which is how dates are compared."""
    return value.isoformat()


def _recency_key(row: dict[str, Any]) -> str:
    """
    How recent a record is, for ordering a mixed list of kinds.

    Records with no date of their own sort last rather than crashing the
    comparison — they are the two kinds that belong to something else's
    moment rather than having one.
    """
    return str(row.get(queries.VALID_FROM) or row.get("created_at") or "")


def _drop_after(
    nodes: list[dict[str, Any]],
    edges: list[EdgeRow],
    *,
    as_of: datetime | date,
    keep: str,
) -> tuple[list[dict[str, Any]], list[EdgeRow]]:
    """
    Remove whatever did not exist yet on the date being asked about.

    Applied after the walk rather than during it, because the tables are
    mixed by then and two of them have no date column at all — asking those
    for one is an error rather than an empty answer.

    The record the walk started from is always kept. Someone asking what was
    around a node in March is asking about its surroundings, and answering
    with nothing at all because the node itself is newer would be a strange
    reading of the question.
    """
    cutoff = _as_text(as_of)
    surviving = {
        str(row["node_id"])
        for row in nodes
        if row.get("node_id")
        and (
            str(row["node_id"]) == keep
            or not row.get(queries.VALID_FROM)
            or str(row[queries.VALID_FROM]) <= cutoff
        )
    }
    return (
        [row for row in nodes if str(row.get("node_id")) in surviving],
        [
            edge
            for edge in edges
            if edge.from_node_id in surviving and edge.to_node_id in surviving
        ],
    )


# ---------------------------------------------------------------------------
# Extra edge columns
#
# Most links only need to record when they were made and by which decision.
# Two of them carry a sentence as well, because the link is meaningless
# without it: a tension link has to say what the tension is, and a regulation
# link has to say what was interrupted. Keyed by the start of the table name,
# since each of those two logical links fans out into several typed tables.
# ---------------------------------------------------------------------------

EDGE_EXTRA_COLUMNS: dict[str, str] = {
    "dialectic": "tension_summary STRING",
    "regulates": "regulation_summary STRING",
}


def _extra_edge_columns(edge_name: str) -> str:
    """The extra columns this link table needs, as a DDL fragment."""
    for prefix, columns in EDGE_EXTRA_COLUMNS.items():
        if edge_name.startswith(prefix):
            return f", {columns}"
    return ""


# Which tables each bookkeeping operation is allowed to touch, and what it
# does to them. This is the whole of the exception to "nothing already
# written is ever changed": three operations, fixed columns, no way for a
# caller to name a column of its own.
_BOOKKEEPING_TABLES: dict[str, tuple[str, ...]] = {
    "mark_superseded": ("PatternNode", "BeliefNode"),
    "record_reinforcement": ("PatternNode", "BeliefNode"),
    "touch_person": ("PersonEntityNode",),
}


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
        "provenance STRING, verification_status STRING, "
        "open_loop_ref STRING, extraction_confidence STRING, "
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
        "signal_strength STRING, provenance STRING, verification_status STRING, "
        "evidence_count INT64, "
        "query_frequency INT64, is_canonical BOOLEAN, status STRING, "
        "era_tag STRING, archetype_tags STRING, "
        "PRIMARY KEY(node_id))"
    ),
    "BeliefNode": (
        "CREATE NODE TABLE BeliefNode ("
        "node_id STRING, version INT64, previous_version_id STRING, "
        "created_at STRING, valid_from STRING, last_reinforced_at STRING, "
        "belief_statement STRING, belief_source_summary STRING, domain STRING, "
        "signal_strength STRING, provenance STRING, verification_status STRING, "
        "evidence_count INT64, "
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
        "source_node_id STRING, target_node_id STRING, "
        "edge_type_created STRING, edge_id STRING, confidence DOUBLE, "
        "confidence_runner_up DOUBLE, runner_up_action STRING, "
        "delta_description STRING, model_used STRING, model_role STRING, "
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
        self._in_transaction = False

        # Every statement goes through this lock, and a transaction holds it
        # for its whole length.
        #
        # This is not defensive habit — it is what makes one shared provider
        # correct. Kuzu is embedded and takes a file lock, so a process can
        # only hold one of these open at a time; a web server that both reads
        # the graph and runs the pipeline in the background therefore has to
        # share this object between threads. A transaction belongs to the
        # connection, not to the caller who opened it, so without the lock a
        # read arriving mid-import would run *inside* the importer's open
        # transaction and see half of an episode that has not been committed
        # and might yet be rolled back.
        #
        # Re-entrant, because the writes inside a transaction come through
        # the same door as everything else and would otherwise deadlock
        # against the block that opened it.
        self._lock = threading.RLock()

        logger.info("KuzuGraphProvider initialized at %s", db_path)

    def _execute(self, query: str, params: dict[str, Any] | None = None) -> Any:
        """
        Run one statement, with the connection held for its duration.

        The single door every query goes through. See the note on the lock
        in __init__ for why there is one.
        """
        with self._lock:
            if params is None:
                return self.conn.execute(query)
            return self.conn.execute(query, params)

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
                self._execute(ddl)

        # Create Edge Tables
        for from_table, to_table, edge_name in EDGE_REGISTRY:
            if edge_name not in existing_tables:
                ddl = (
                    f"CREATE REL TABLE {edge_name} ("
                    f"FROM {from_table} TO {to_table}, "
                    "valid_from STRING, invalidated_at STRING, "
                    "decision_id STRING, confidence DOUBLE"
                    f"{_extra_edge_columns(edge_name)})"
                )
                logger.debug("Creating edge table: %s (%s → %s)", edge_name, from_table, to_table)
                self._execute(ddl)

        logger.info(
            "Schema initialized: %d node tables, %d edge tables",
            len(NODE_TABLES), len(EDGE_REGISTRY),
        )

    def _get_existing_tables(self) -> set[str]:
        """Query Kuzu for all existing table names."""
        existing: set[str] = set()
        try:
            res = self._execute("CALL show_tables() RETURN name;")
            while res.has_next():
                existing.add(res.get_next()[0])
        except RuntimeError as e:
            # Only catch Kuzu-specific errors; let other exceptions propagate
            logger.warning("Could not list existing tables: %s", e)
        return existing

    # ------------------------------------------------------------------
    # Write Operations
    # ------------------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """
        Run a group of writes so that they all land or none of them do.

        Anything raised inside the block undoes every write made in it and
        is then passed on to the caller, so a failure never leaves a
        half-written entry behind.

        Nesting is rejected rather than quietly flattened. A caller who
        opens a second one of these is asking for a smaller group of writes
        to be protected on its own, and the database cannot give them that
        — silently handing back a wider group would be the wrong answer to
        a question they were entitled to ask.

        Another *thread* asking for one waits instead of being refused. That
        is a different question with a different right answer: it is not
        asking for a smaller group inside this one, it is asking for its own
        group, and it can have it as soon as this one finishes. Everything
        else on this object waits alongside it, which is what keeps a read
        from landing inside somebody else's uncommitted writes.
        """
        # Taken before the check, not after. A thread that tested the flag
        # first would see another thread's open transaction and refuse,
        # when what it should do is wait its turn.
        self._lock.acquire()
        if self._in_transaction:
            self._lock.release()
            raise RuntimeError(
                "a graph transaction is already open; nested transactions "
                "are not supported"
            )

        try:
            self.conn.execute("BEGIN TRANSACTION")
            self._in_transaction = True
            try:
                yield
            except BaseException:
                self._rollback()
                logger.warning("graph transaction rolled back")
                raise
            self.conn.execute("COMMIT")
            self._in_transaction = False
            logger.debug("graph transaction committed")
        finally:
            self._lock.release()

    def _rollback(self) -> None:
        """
        Undo everything in the open transaction, however it ended.

        A statement that fails is enough for Kuzu to abandon the transaction
        on its own, and asking it to roll back after that is an error. The
        writes are already gone at that point, which is the outcome wanted,
        so it is treated as done rather than as a new problem — raising here
        would replace whatever actually went wrong with a complaint about
        the cleanup.
        """
        try:
            self.conn.execute("ROLLBACK")
        except RuntimeError as exc:
            if "no active transaction" not in str(exc).lower():
                raise
            logger.debug("transaction had already been abandoned by the database")
        finally:
            self._in_transaction = False

    def write_node(self, node_type: str, properties: GraphNode | dict[str, Any]) -> str:
        """
        Write a node to the graph and return its node_id.

        Accepts either a Pydantic node model (from lumen.schemas.nodes) or a
        raw dict. Models are serialized via to_graph_dict() before the
        existing dict-based write path runs unchanged.
        """
        if isinstance(properties, GraphNode):
            properties = properties.to_graph_dict()

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

        self._execute(query, processed_props)
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

        self._execute(query, params)
        logger.debug("Wrote edge %s: %s → %s", edge_type, from_id, to_id)

    # ------------------------------------------------------------------
    # Read Operations
    # ------------------------------------------------------------------

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Retrieve a node's properties by its ID. Returns None if not found."""
        res = self._execute(
            "MATCH (n) WHERE n.node_id = $node_id RETURN n",
            {"node_id": node_id},
        )
        if res.has_next():
            return res.get_next()[0]
        return None

    def get_nodes_by_ids(self, node_ids: list[str]) -> list[dict[str, Any]]:
        """
        Retrieve several records by id, in the order they were asked for.

        Asked one table at a time rather than with a single unlabelled match,
        which is the obvious way to write it and is not safe here: a result
        set spanning two different node tables comes back with its strings
        misread, and reading a record fails with a decoding error naming a
        byte position rather than anything about the record. Asking each
        table separately keeps every result set to one shape. It is one query
        per kind of record, all of them indexed on the primary key.
        """
        if not node_ids:
            return []

        wanted = list(dict.fromkeys(node_ids))
        found: dict[str, dict[str, Any]] = {}

        for table in NODE_TABLES:
            res = self._execute(
                f"MATCH (n:{table}) WHERE n.node_id IN $node_ids RETURN n",
                {"node_ids": wanted},
            )
            while res.has_next():
                row = res.get_next()[0]
                found[str(row.get("node_id"))] = row

        # The caller's order is the useful one — a search hands these over
        # ranked, and returning them grouped by table would silently throw
        # that ranking away.
        return [found[node_id] for node_id in wanted if node_id in found]

    # ------------------------------------------------------------------
    # Traversal
    #
    # Named questions, one method each. There is deliberately no method
    # that runs an arbitrary query: it would push query building out to
    # callers, spread graph-shaped thinking into the web layer, and quietly
    # end the promise that this store can be replaced.
    # ------------------------------------------------------------------

    def find_nodes(
        self,
        node_types: list[str],
        *,
        since: datetime | date | None = None,
        until: datetime | date | None = None,
        domain: str | None = None,
        signal_strength: str | None = None,
        era_tag: str | None = None,
        active_only: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        List records of the given kinds, newest first.

        Asked for several kinds at once, each is queried on its own terms —
        the same quality lives under different column names, and some kinds
        do not record it at all. The results are then merged and ordered as
        one list, which is why each table is asked for enough rows to cover
        the whole window rather than its share of it.
        """
        wanted = [t for t in node_types if t in NODE_TABLES] or list(NODE_TABLES)
        reach = max(int(limit) + int(offset), 1)
        gathered: list[dict[str, Any]] = []

        for table in wanted:
            filters = queries.build_filters(
                table,
                since=since,
                until=until,
                domain=domain,
                signal_strength=signal_strength,
                era_tag=era_tag,
                active_only=active_only,
            )
            gathered.extend(
                self._collect(
                    f"MATCH (n:{table}) {filters.where()} "
                    f"RETURN n {self._newest_first(table)} LIMIT {reach}",
                    filters.params,
                )
            )

        gathered.sort(key=_recency_key, reverse=True)
        return gathered[offset : offset + limit]

    def count_by_type(self) -> dict[str, int]:
        """How many records of each kind exist, including retired ones."""
        counts: dict[str, int] = {}
        for table in NODE_TABLES:
            res = self._execute(f"MATCH (n:{table}) RETURN count(n)")
            counts[table] = int(res.get_next()[0]) if res.has_next() else 0
        return counts

    def get_neighborhood(
        self,
        node_id: str,
        *,
        depth: int = 1,
        edge_types: list[str] | None = None,
        direction: str = "both",
        as_of: datetime | date | None = None,
        include_invalidated: bool = False,
        limit: int = 200,
    ) -> GraphSlice:
        """
        Everything within a few steps of one record.

        Walked a step at a time rather than as one long pattern, because a
        single variable-length match hands back whole paths and loses which
        record each link actually joined — and the two ends of a link are
        the only way to name it. Stepping out also means the limit stops the
        walk rather than trimming an answer already fetched.
        """
        start = self.get_node(node_id)
        if start is None:
            return GraphSlice(nodes=[], edges=[], truncated=False)

        seen = {node_id}
        frontier = [node_id]
        edges: list[EdgeRow] = []
        truncated = False

        for _ in range(max(int(depth), 1)):
            if not frontier or truncated:
                break

            found = self._step_out(
                frontier,
                edge_types=edge_types,
                direction=direction,
                include_invalidated=include_invalidated,
                as_of=as_of,
            )

            next_frontier: list[str] = []
            for edge in found:
                edges.append(edge)
                for end in (edge.from_node_id, edge.to_node_id):
                    if end not in seen:
                        seen.add(end)
                        next_frontier.append(end)
                if len(seen) >= limit:
                    truncated = True
                    break
            frontier = next_frontier

        nodes = self.get_nodes_by_ids(sorted(seen))
        if as_of is not None:
            nodes, edges = _drop_after(nodes, edges, as_of=as_of, keep=node_id)

        return GraphSlice(nodes=nodes, edges=edges, truncated=truncated)

    def get_version_chain(self, node_id: str) -> list[dict[str, Any]]:
        """
        Every version of a belief or pattern, oldest first.

        Walked in both directions from wherever the caller happened to
        start. Someone who reached a record through a search has no idea
        whether they are looking at the first version or the fifth, and a
        history that only runs one way from there is not a history.
        """
        start = self.get_node(node_id)
        if start is None:
            return []

        table = start.get("_label")
        if table not in _VERSIONED_TABLES:
            logger.debug("%s is not a versioned kind of record", table)
            return []

        # The ids are walked first and the records fetched together
        # afterwards. Asking for one node by itself and asking for it as one
        # of a kind come back in different shapes — the first carries a
        # column for every kind of record in the store and the second only
        # its own — so a chain assembled as it was walked would describe the
        # same history in two different shapes depending on where the walk
        # started.
        ids = self._walk_back(node_id)
        ids += self._walk_forward(node_id, table=str(table))

        chain = self.get_nodes_by_ids(ids)
        chain.sort(key=lambda row: row.get("version") or 0)
        return chain

    def get_decision_history(self, node_id: str) -> list[dict[str, Any]]:
        """
        Every decision recorded about one record, newest first.

        Reached by following the links that name a decision, which exist
        precisely so this question has an answer — a change to somebody's
        history that nobody can explain is worse than no change at all.
        """
        return sorted(
            self._collect(
                "MATCH (n)-[r]->(d:DecisionAuditNode) WHERE n.node_id = $node_id "
                "RETURN d",
                {"node_id": node_id},
            ),
            key=lambda row: str(row.get("created_at") or ""),
            reverse=True,
        )

    def get_episode_contents(self, episode_id: str) -> GraphSlice:
        """
        Everything one piece of writing produced.

        Only the links that mean "this came out of that" are followed. An
        episode also points at the episode before it and at any report that
        analysed it, and pulling those in would answer a wider question than
        the one asked.
        """
        episode = self.get_node(episode_id)
        if episode is None:
            return GraphSlice(nodes=[], edges=[], truncated=False)

        edges = self._step_out(
            [episode_id],
            edge_types=list(CONTAINMENT_EDGES),
            direction="out",
            include_invalidated=True,
            as_of=None,
        )
        child_ids = sorted({edge.to_node_id for edge in edges})

        # A sequence's steps hang off the sequence, not off the episode, so
        # they need one more step out or a chain arrives with nothing in it.
        chain_ids = [cid for cid in child_ids if cid.startswith("chain_")]
        if chain_ids:
            step_edges = self._step_out(
                chain_ids,
                edge_types=["chain_contains"],
                direction="out",
                include_invalidated=True,
                as_of=None,
            )
            edges.extend(step_edges)
            child_ids.extend(edge.to_node_id for edge in step_edges)

        return GraphSlice(
            nodes=[episode, *self.get_nodes_by_ids(sorted(set(child_ids)))],
            edges=edges,
            truncated=False,
        )

    def get_causal_chain(self, chain_id: str) -> list[dict[str, Any]]:
        """One cause-and-effect sequence's steps, in the order they happened."""
        return self._collect(
            "MATCH (c:CausalChainNode)-[:chain_contains]->(s:CausalStepNode) "
            "WHERE c.node_id = $chain_id RETURN s ORDER BY s.step_index",
            {"chain_id": chain_id},
        )

    # ------------------------------------------------------------------
    # Traversal helpers
    # ------------------------------------------------------------------

    def _step_out(
        self,
        ids: list[str],
        *,
        edge_types: list[str] | None,
        direction: str,
        include_invalidated: bool,
        as_of: datetime | date | None,
    ) -> list[EdgeRow]:
        """
        Every link one step from the given records.

        The link table is asked for by name rather than matched per type,
        so one query covers all forty-eight of them instead of forty-eight
        queries covering one each.
        """
        liveness = queries.edge_liveness_clause(
            include_invalidated=include_invalidated, as_of=as_of
        )
        conditions = [liveness]
        params: dict[str, Any] = {"ids": ids}
        if as_of is not None:
            params["as_of"] = _as_text(as_of)
        if edge_types:
            conditions.append("label(r) IN $edge_types")
            params["edge_types"] = list(edge_types)
        where = " AND ".join(conditions)

        found: list[EdgeRow] = []
        if direction in ("out", "both"):
            found.extend(
                self._edges(
                    f"MATCH (a)-[r]->(b) WHERE a.node_id IN $ids AND {where} "
                    "RETURN a.node_id, label(r), b.node_id, r",
                    params,
                )
            )
        if direction in ("in", "both"):
            found.extend(
                self._edges(
                    f"MATCH (a)-[r]->(b) WHERE b.node_id IN $ids AND {where} "
                    "RETURN a.node_id, label(r), b.node_id, r",
                    params,
                )
            )
        return found

    def _edges(self, query: str, params: dict[str, Any]) -> list[EdgeRow]:
        """Run a query returning links and gather them, dropping duplicates."""
        res = self._execute(query, params)
        found: dict[tuple[str, str, str], EdgeRow] = {}
        while res.has_next():
            from_id, table, to_id, row = res.get_next()
            key = (str(table), str(from_id), str(to_id))
            found[key] = EdgeRow(
                edge_type=str(table),
                from_node_id=str(from_id),
                to_node_id=str(to_id),
                properties={
                    k: v for k, v in row.items() if not k.startswith("_") and v is not None
                },
            )
        return list(found.values())

    def _walk_back(self, node_id: str) -> list[str]:
        """
        Follow previous_version_id back to the first version.

        Includes the record started from. A version already seen stops the
        walk: a chain that pointed at itself would otherwise be followed
        forever, and a graph that has been written to by hand can hold one.
        """
        seen = [node_id]
        current = self.get_node(node_id)
        while current and (previous_id := current.get("previous_version_id")):
            if str(previous_id) in seen:
                logger.warning("version chain loops back on itself at %s", previous_id)
                break
            previous = self.get_node(str(previous_id))
            if previous is None:
                logger.debug("version %s names a previous one that is gone", node_id)
                break
            seen.insert(0, str(previous_id))
            current = previous
        return seen

    def _walk_forward(self, node_id: str, *, table: str) -> list[str]:
        """
        Follow whichever record names this one as its previous version.

        Does not include the record started from, so the two halves of a
        walk join without repeating the middle.
        """
        found: list[str] = []
        current = node_id
        while rows := self._collect(
            f"MATCH (n:{table}) WHERE n.previous_version_id = $node_id "
            "RETURN n.node_id",
            {"node_id": current},
        ):
            following = str(rows[0])
            if following in found or following == node_id:
                logger.warning("version chain loops back on itself at %s", following)
                break
            found.append(following)
            current = following
        return found

    def _newest_first(self, table: str) -> str:
        """An ordering clause, for the tables that record when they began."""
        if not queries.has_start_date(table):
            return ""
        return f"ORDER BY n.{queries.VALID_FROM} DESC"

    # ------------------------------------------------------------------
    # Anchor Lookups
    # ------------------------------------------------------------------

    def find_linked_to_person(
        self, canonical_name: str, *, node_types: list[str], limit: int = 10
    ) -> list[dict[str, Any]]:
        """
        Find active nodes to do with a particular person.

        Three kinds of record name a person directly. A belief or a pattern
        never does — it reaches one only through the finding that produced
        it, so those take a second step, through whichever observation
        branched into or reinforced them.

        Both halves are asked for, because "what do I know about Alex" means
        the same thing whether the answer is a note from Tuesday or a
        standing belief that grew out of one.
        """
        found: list[dict[str, Any]] = []
        for table in node_types:
            if edge := MENTIONS_EDGES.get(table):
                found.extend(
                    self._collect(
                        f"MATCH (n:{table})-[:{edge}]->(p:PersonEntityNode) "
                        f"WHERE p.canonical_name = $name AND {_active_clause(table)} "
                        f"RETURN n LIMIT {int(limit)}",
                        {"name": canonical_name},
                    )
                )
            elif table in _REACHED_THROUGH_A_FINDING:
                found.extend(self._through_a_finding(canonical_name, table, limit))
            else:
                logger.debug("no route from %s to a person; skipping", table)

        return _first_unique(found, limit)

    def _through_a_finding(
        self, canonical_name: str, table: str, limit: int
    ) -> list[dict[str, Any]]:
        """
        Beliefs or patterns reached through an observation about someone.

        The middle step is any link a decision makes from a finding to a
        standing record — the finding branched into it, or reinforced it, or
        was judged the same thing. Which of those it was does not change the
        answer to "what do I know about this person".
        """
        return self._collect(
            f"MATCH (o:ObservationNode)-[r]->(n:{table}) "
            "WHERE label(r) IN $links AND r.invalidated_at IS NULL "
            "AND EXISTS { MATCH (o)-[:mentions_obs]->(p:PersonEntityNode) "
            "WHERE p.canonical_name = $name } "
            f"AND {_active_clause(table)} "
            f"RETURN n LIMIT {int(limit)}",
            {"name": canonical_name, "links": list(_FINDING_TO_STANDING[table])},
        )

    def find_by_era(
        self, era_tag: str, *, node_types: list[str], limit: int = 10
    ) -> list[dict[str, Any]]:
        """
        Find active nodes anchored to a named period of the person's past.

        The column holding that period is not named the same everywhere —
        patterns and beliefs carry era_tag, episodes carry historical_era —
        so the lookup asks each table for its own.
        """
        found: list[dict[str, Any]] = []
        for table in node_types:
            column = ERA_COLUMNS.get(table)
            if column is None:
                logger.debug("%s records no era; skipping", table)
                continue
            found.extend(
                self._collect(
                    f"MATCH (n:{table}) "
                    f"WHERE n.{column} = $era AND {_active_clause(table)} "
                    f"RETURN n LIMIT {int(limit)}",
                    {"era": era_tag},
                )
            )
        return found[:limit]

    def list_era_tags(self, *, limit: int = 50) -> list[str]:
        """
        Every named period of the past that some record is anchored to.

        Counted across all three tables that record one, then handed back
        most-used first so that cutting the list at a limit keeps the periods
        that actually carry the person's history rather than an arbitrary
        few.

        Names that differ only in spacing or capitalisation are treated as
        the same period, and the spelling that occurs most often is the one
        returned — whatever comes back has to be usable in a lookup, so it
        must be a spelling the graph really holds.
        """
        counts: dict[str, dict[str, int]] = {}
        for table, column in ERA_COLUMNS.items():
            for stored in self._era_values(table, column):
                key = queries.era_key(stored)
                if key:
                    spellings = counts.setdefault(key, {})
                    spellings[stored] = spellings.get(stored, 0) + 1

        ranked = sorted(
            counts.values(), key=lambda spellings: sum(spellings.values()), reverse=True
        )
        return [max(spellings, key=lambda name: spellings[name]) for spellings in ranked][
            : max(int(limit), 0)
        ]

    def _era_values(self, table: str, column: str) -> list[str]:
        """Every era name written on the live records of one table."""
        res = self._execute(
            f"MATCH (n:{table}) "
            f"WHERE n.{column} IS NOT NULL AND {_active_clause(table)} "
            f"RETURN n.{column}",
            {},
        )
        values: list[str] = []
        while res.has_next():
            value = res.get_next()[0]
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
        return values

    def find_unresolved_high_signal(
        self, observation_types: list[str], *, limit: int = 10
    ) -> list[dict[str, Any]]:
        """
        Find weighty observations whose episode is still awaiting
        reconciliation.

        Reached through the episode, because that is where the record of
        outstanding reconciliation lives — an observation has no such field
        of its own.
        """
        if not observation_types:
            return []

        return self._collect(
            "MATCH (e:EpisodeNode)-[:contains_obs]->(n:ObservationNode) "
            "WHERE e.reconciliation_status IN $episode_statuses "
            "AND n.type IN $types AND n.status = 'ACTIVE' "
            f"RETURN n LIMIT {int(limit)}",
            {
                "types": list(observation_types),
                "episode_statuses": list(UNSETTLED_EPISODE_STATUSES),
            },
        )

    def count_prior_decisions(
        self, target_node_id: str, *, actions: list[str]
    ) -> int:
        """
        Count the live decisions of the given kinds already recorded against
        a node.

        Rolled-back decisions are left out: something that was undone should
        not count as evidence that it happened.
        """
        if not actions:
            return 0

        res = self._execute(
            "MATCH (d:DecisionAuditNode) "
            "WHERE d.target_node_id = $target AND d.action IN $actions "
            "AND d.status <> 'ROLLED_BACK' "
            "RETURN count(d)",
            {"target": target_node_id, "actions": list(actions)},
        )
        if res.has_next():
            return int(res.get_next()[0])
        return 0

    # ------------------------------------------------------------------
    # Bookkeeping Writes
    # ------------------------------------------------------------------

    def mark_superseded(self, node_id: str, *, at: datetime) -> None:
        """Record that a newer version of this belief or pattern now exists."""
        table = self._table_for(node_id, operation="mark_superseded")
        self._execute(
            f"MATCH (n:{table}) WHERE n.node_id = $node_id "
            "SET n.status = 'SUPERSEDED'",
            {"node_id": node_id},
        )
        logger.debug("Marked %s superseded at %s", node_id, at.isoformat())

    def record_reinforcement(self, node_id: str, *, at: datetime) -> None:
        """Add one to a belief or pattern's evidence, and note when."""
        table = self._table_for(node_id, operation="record_reinforcement")
        self._execute(
            f"MATCH (n:{table}) WHERE n.node_id = $node_id "
            "SET n.evidence_count = n.evidence_count + 1, "
            "n.last_reinforced_at = $at",
            {"node_id": node_id, "at": at.isoformat()},
        )
        logger.debug("Recorded reinforcement of %s", node_id)

    def touch_person(self, node_id: str, *, at: datetime) -> None:
        """Note that a person was mentioned again, and when."""
        table = self._table_for(node_id, operation="touch_person")
        self._execute(
            f"MATCH (n:{table}) WHERE n.node_id = $node_id "
            "SET n.mention_count = n.mention_count + 1, "
            "n.last_mentioned_at = $at",
            {"node_id": node_id, "at": at.isoformat()},
        )
        logger.debug("Touched person %s", node_id)

    def _table_for(self, node_id: str, *, operation: str) -> str:
        """
        Find which table a node lives in, and refuse if this operation has no
        business touching it.

        The check is what keeps the bookkeeping operations narrow. Without
        it, a mistyped id could quietly point one of them at a table it was
        never meant to reach.
        """
        row = self.get_node(node_id)
        if row is None:
            raise ValueError(f"No node with id '{node_id}'")

        table = row.get("_label")
        allowed = _BOOKKEEPING_TABLES[operation]
        if table not in allowed:
            raise ValueError(
                f"{operation} cannot be applied to a {table}. "
                f"Allowed: {', '.join(allowed)}"
            )
        return str(table)

    def _collect(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Run a query that returns single nodes and gather them into a list."""
        res = self._execute(query, params)
        rows: list[dict[str, Any]] = []
        while res.has_next():
            rows.append(res.get_next()[0])
        return rows
