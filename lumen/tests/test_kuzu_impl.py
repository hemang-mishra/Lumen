"""
Test suite for KuzuGraphProvider.

Tests schema initialization, node/edge CRUD, idempotency, validation,
and error handling for the Kuzu graph database layer.
"""

from __future__ import annotations

import os
import shutil

import pytest

from lumen.graph.kuzu_impl import KuzuGraphProvider, NODE_TABLES, EDGE_REGISTRY


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    """Provide a clean temporary path for a Kuzu database."""
    path = str(tmp_path / "test_kuzu_db")
    yield path
    # Cleanup is handled by tmp_path automatically


@pytest.fixture
def provider(db_path):
    """Provide an initialized KuzuGraphProvider with schema created."""
    p = KuzuGraphProvider(db_path)
    p.init_schema()
    yield p
    p.close()


@pytest.fixture
def raw_provider(db_path):
    """Provide a KuzuGraphProvider WITHOUT schema initialized."""
    p = KuzuGraphProvider(db_path)
    yield p
    p.close()


# ---------------------------------------------------------------------------
# Schema Initialization
# ---------------------------------------------------------------------------

class TestSchemaInit:
    """Tests for init_schema() behavior."""

    def test_creates_all_node_tables(self, provider):
        """All 15 node tables defined in Schema.md must be created."""
        existing = provider._get_existing_tables()
        for table_name in NODE_TABLES:
            assert table_name in existing, f"Node table '{table_name}' was not created"

    def test_creates_all_edge_tables(self, provider):
        """All edge tables from EDGE_REGISTRY must be created."""
        existing = provider._get_existing_tables()
        for _, _, edge_name in EDGE_REGISTRY:
            assert edge_name in existing, f"Edge table '{edge_name}' was not created"

    def test_idempotent(self, provider):
        """Calling init_schema() twice must not raise."""
        # First call happens in the fixture; second call here:
        provider.init_schema()
        existing = provider._get_existing_tables()
        assert len(existing) == len(NODE_TABLES) + len(EDGE_REGISTRY)

    def test_node_table_count(self, provider):
        """Exactly 15 node tables must exist."""
        assert len(NODE_TABLES) == 15

    def test_edge_table_count(self, provider):
        """Edge registry must match expected count (all Schema.md edges)."""
        # 43 typed edge tables as of current schema
        assert len(EDGE_REGISTRY) >= 40, (
            f"Expected at least 40 edge definitions, got {len(EDGE_REGISTRY)}"
        )


# ---------------------------------------------------------------------------
# Node Write Operations
# ---------------------------------------------------------------------------

class TestWriteNode:
    """Tests for write_node() behavior."""

    def test_write_episode_node(self, provider):
        """Can write and read back an EpisodeNode."""
        node_id = provider.write_node("EpisodeNode", {
            "node_id": "ep_test_001",
            "episode_summary": "Test episode about decision making",
            "event_date": "2025-01-18",
            "session_label": "A",
        })
        assert node_id == "ep_test_001"

        result = provider.get_node("ep_test_001")
        assert result is not None
        assert result["episode_summary"] == "Test episode about decision making"

    def test_write_observation_node(self, provider):
        """Can write and read back an ObservationNode."""
        provider.write_node("ObservationNode", {
            "node_id": "obs_test_001",
            "content": "User exhibits deliberate information gathering before decisions",
            "status": "ACTIVE",
            "signal_strength": "HIGH",
        })
        result = provider.get_node("obs_test_001")
        assert result is not None
        assert result["status"] == "ACTIVE"

    def test_write_pattern_node(self, provider):
        """Can write and read back a PatternNode."""
        provider.write_node("PatternNode", {
            "node_id": "pat_test_001",
            "pattern_name": "Information Saturation",
            "version": 1,
            "is_canonical": True,
            "status": "ACTIVE",
        })
        result = provider.get_node("pat_test_001")
        assert result is not None
        assert result["pattern_name"] == "Information Saturation"

    def test_write_belief_node(self, provider):
        """Can write and read back a BeliefNode."""
        provider.write_node("BeliefNode", {
            "node_id": "bel_test_001",
            "belief_statement": "I need solitude to make good decisions",
            "version": 1,
            "is_contradicted": False,
            "status": "ACTIVE",
        })
        result = provider.get_node("bel_test_001")
        assert result is not None
        assert result["belief_statement"] == "I need solitude to make good decisions"

    def test_write_event_node(self, provider):
        """Can write and read back an EventNode."""
        provider.write_node("EventNode", {
            "node_id": "evt_test_001",
            "event_summary": "Went to cafe alone, breaking avoidance pattern",
            "signal_strength": "HIGH",
            "status": "ACTIVE",
        })
        result = provider.get_node("evt_test_001")
        assert result is not None

    def test_write_decision_audit_node(self, provider):
        """Can write and read back a DecisionAuditNode."""
        provider.write_node("DecisionAuditNode", {
            "node_id": "d_test_001",
            "action": "MERGE",
            "confidence": 0.91,
            "routing_tier": "STANDARD",
            "hitl_resolved": False,
            "status": "ACTIVE",
        })
        result = provider.get_node("d_test_001")
        assert result is not None
        assert result["action"] == "MERGE"

    def test_write_list_property_as_json(self, provider):
        """List properties must be serialized to JSON strings."""
        provider.write_node("ObservationNode", {
            "node_id": "obs_json_001",
            "content": "Test with list fields",
            "raw_evidence": ["quote one", "quote two"],
            "person_refs": ["person_001"],
        })
        result = provider.get_node("obs_json_001")
        assert result is not None
        # Stored as JSON string
        assert '"quote one"' in result["raw_evidence"]

    def test_missing_node_id_raises(self, provider):
        """write_node must raise ValueError if node_id is missing."""
        with pytest.raises(ValueError, match="node_id is required"):
            provider.write_node("EpisodeNode", {"episode_summary": "No ID"})

    def test_invalid_node_type_raises(self, provider):
        """write_node must raise ValueError for unknown node types."""
        with pytest.raises(ValueError, match="Unknown node type"):
            provider.write_node("FakeNodeType", {"node_id": "fake_001"})


# ---------------------------------------------------------------------------
# Edge Write Operations
# ---------------------------------------------------------------------------

class TestWriteEdge:
    """Tests for write_edge() behavior."""

    def _seed_nodes(self, provider):
        """Helper to seed a standard set of nodes for edge tests."""
        provider.write_node("EpisodeNode", {"node_id": "ep_edge_001"})
        provider.write_node("ObservationNode", {"node_id": "obs_edge_001", "status": "ACTIVE"})
        provider.write_node("PatternNode", {"node_id": "pat_edge_001", "status": "ACTIVE", "version": 1})
        provider.write_node("BeliefNode", {"node_id": "bel_edge_001", "status": "ACTIVE", "version": 1})
        provider.write_node("EventNode", {"node_id": "evt_edge_001", "status": "ACTIVE"})
        provider.write_node("SessionNode", {"node_id": "sess_edge_001", "status": "ACTIVE"})
        provider.write_node("PersonEntityNode", {"node_id": "person_edge_001", "status": "ACTIVE"})
        provider.write_node("DecisionAuditNode", {"node_id": "d_edge_001", "status": "ACTIVE"})

    def test_contains_edge(self, provider):
        """Episode → Observation edge (contains_obs) must work."""
        self._seed_nodes(provider)
        provider.write_edge("contains_obs", "ep_edge_001", "obs_edge_001", {"confidence": 1.0})

    def test_reinforces_edge(self, provider):
        """Observation → Pattern edge (reinforces_obs_pat) must work."""
        self._seed_nodes(provider)
        provider.write_edge("reinforces_obs_pat", "obs_edge_001", "pat_edge_001")

    def test_evolved_from_edge(self, provider):
        """Pattern → Pattern edge (evolved_from_pat) must work."""
        self._seed_nodes(provider)
        provider.write_node("PatternNode", {"node_id": "pat_edge_002", "status": "ACTIVE", "version": 2})
        provider.write_edge("evolved_from_pat", "pat_edge_002", "pat_edge_001")

    def test_caused_by_edge(self, provider):
        """Pattern → Event causal anchor must work."""
        self._seed_nodes(provider)
        provider.write_edge("caused_by_pat_evt", "pat_edge_001", "evt_edge_001")

    def test_mentions_edge(self, provider):
        """Observation → PersonEntity mentions edge must work."""
        self._seed_nodes(provider)
        provider.write_edge("mentions_obs", "obs_edge_001", "person_edge_001")

    def test_decided_by_edge(self, provider):
        """Observation → DecisionAuditNode edge must work."""
        self._seed_nodes(provider)
        provider.write_edge("decided_by_obs", "obs_edge_001", "d_edge_001")

    def test_invalid_edge_type_raises(self, provider):
        """write_edge must raise ValueError for unknown edge types."""
        with pytest.raises(ValueError, match="Unknown edge type"):
            provider.write_edge("fake_edge", "a", "b")

    def test_edge_with_properties(self, provider):
        """Edge properties (confidence, decision_id) must be accepted."""
        self._seed_nodes(provider)
        provider.write_edge("reinforces_obs_bel", "obs_edge_001", "bel_edge_001", {
            "confidence": 0.87,
            "decision_id": "d_edge_001",
            "valid_from": "2025-01-18T10:34:00Z",
        })


# ---------------------------------------------------------------------------
# Read Operations
# ---------------------------------------------------------------------------

class TestReadOperations:
    """Tests for get_node() and get_nodes_by_ids()."""

    def test_get_nonexistent_node_returns_none(self, provider):
        """get_node for a missing ID must return None, not raise."""
        result = provider.get_node("does_not_exist")
        assert result is None

    def test_get_nodes_by_ids(self, provider):
        """get_nodes_by_ids must return all matching nodes."""
        provider.write_node("EpisodeNode", {"node_id": "ep_batch_001"})
        provider.write_node("EpisodeNode", {"node_id": "ep_batch_002"})
        provider.write_node("EpisodeNode", {"node_id": "ep_batch_003"})

        results = provider.get_nodes_by_ids(["ep_batch_001", "ep_batch_003"])
        node_ids = [r["node_id"] for r in results]
        assert "ep_batch_001" in node_ids
        assert "ep_batch_003" in node_ids

    def test_get_nodes_by_ids_empty_list(self, provider):
        """get_nodes_by_ids with empty list must return empty list."""
        assert provider.get_nodes_by_ids([]) == []


# ---------------------------------------------------------------------------
# Context Manager
# ---------------------------------------------------------------------------

class TestContextManager:
    """Tests for context manager (with statement) support."""

    def test_context_manager(self, db_path):
        """Provider must work as a context manager and close cleanly."""
        with KuzuGraphProvider(db_path) as p:
            p.init_schema()
            p.write_node("EpisodeNode", {"node_id": "ep_ctx_001"})
            result = p.get_node("ep_ctx_001")
            assert result is not None
        # After exiting, internal resources should be released
        assert p.conn is None
