"""
Test suite for QdrantVectorProvider.

Tests collection initialization, upsert, search, idempotency,
sparse vector warnings, and cleanup.
"""

from __future__ import annotations

import logging

import pytest

from lumen.vector.qdrant_impl import QdrantVectorProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def provider():
    """Provide an initialized QdrantVectorProvider (in-memory)."""
    p = QdrantVectorProvider(location=":memory:")
    p.init_collection()
    yield p
    p.close()


@pytest.fixture
def raw_provider():
    """Provide a QdrantVectorProvider WITHOUT collection initialized."""
    p = QdrantVectorProvider(location=":memory:")
    yield p
    p.close()


def _dummy_vector(dim: int = 768, val: float = 0.1) -> list[float]:
    """Generate a dummy vector of the given dimension."""
    return [val] * dim


# ---------------------------------------------------------------------------
# Collection Initialization
# ---------------------------------------------------------------------------

class TestCollectionInit:
    """Tests for init_collection() behavior."""

    def test_creates_collection(self, raw_provider):
        """Collection must be created after init_collection()."""
        raw_provider.init_collection()
        collections = raw_provider.client.get_collections().collections
        names = [c.name for c in collections]
        assert "lumen_nodes" in names

    def test_idempotent(self, provider):
        """Calling init_collection() twice must not raise."""
        provider.init_collection()  # Second call (first was in fixture)
        collections = provider.client.get_collections().collections
        assert len(collections) == 1

    def test_custom_collection_name(self):
        """Custom collection name must be respected."""
        p = QdrantVectorProvider(location=":memory:", collection_name="custom_test")
        p.init_collection()
        collections = p.client.get_collections().collections
        names = [c.name for c in collections]
        assert "custom_test" in names
        p.close()

    def test_custom_vector_size(self):
        """Custom vector size must be respected."""
        p = QdrantVectorProvider(location=":memory:", vector_size=384)
        p.init_collection()
        # If we upsert with wrong size, Qdrant will raise
        p.upsert("test_001", [0.1] * 384, {"node_type": "test"})
        p.close()


# ---------------------------------------------------------------------------
# Upsert Operations
# ---------------------------------------------------------------------------

class TestUpsert:
    """Tests for upsert() behavior."""

    def test_basic_upsert(self, provider):
        """Can upsert a vector and retrieve it via search."""
        provider.upsert("obs_001", _dummy_vector(), {"node_type": "ObservationNode"})
        results = provider.hybrid_search(_dummy_vector(), limit=1)
        assert "obs_001" in results

    def test_upsert_idempotent(self, provider):
        """Upserting the same node_id twice must replace, not duplicate."""
        provider.upsert("obs_dup", _dummy_vector(val=0.1), {"node_type": "ObservationNode"})
        provider.upsert("obs_dup", _dummy_vector(val=0.2), {"node_type": "ObservationNode"})

        # Search with the second vector should find it
        results = provider.hybrid_search(_dummy_vector(val=0.2), limit=10)
        # Should only appear once
        assert results.count("obs_dup") == 1

    def test_payload_contains_node_id(self, provider):
        """Payload must always contain node_id after upsert."""
        provider.upsert("obs_payload", _dummy_vector(), {"node_type": "test"})
        results = provider.client.query_points(
            collection_name="lumen_nodes",
            query=_dummy_vector(),
            limit=1,
        ).points
        assert len(results) > 0
        assert results[0].payload["node_id"] == "obs_payload"


# ---------------------------------------------------------------------------
# Search Operations
# ---------------------------------------------------------------------------

class TestSearch:
    """Tests for hybrid_search() behavior."""

    def test_returns_correct_ids(self, provider):
        """Search must return the correct node_ids from payload."""
        # Use vectors that are distinguishable by cosine similarity.
        # node_a has a vector with weight in the first half, node_b in the second half.
        vec_a = [1.0] * 384 + [0.0] * 384
        vec_b = [0.0] * 384 + [1.0] * 384
        provider.upsert("node_a", vec_a, {"node_type": "PatternNode"})
        provider.upsert("node_b", vec_b, {"node_type": "BeliefNode"})

        # Query vector is close to node_a
        results = provider.hybrid_search(vec_a, limit=1)
        assert "node_a" in results

    def test_respects_limit(self, provider):
        """Search must respect the limit parameter."""
        for i in range(10):
            provider.upsert(f"node_{i}", _dummy_vector(val=0.1 * (i + 1)), {"node_type": "test"})

        results = provider.hybrid_search(_dummy_vector(val=0.5), limit=3)
        assert len(results) <= 3

    def test_empty_collection_returns_empty(self, provider):
        """Search on empty collection must return empty list."""
        results = provider.hybrid_search(_dummy_vector(), limit=5)
        assert results == []

    def test_sparse_vector_logs_warning(self, provider, caplog):
        """Providing sparse_vector must log a warning about fallback."""
        provider.upsert("node_sparse", _dummy_vector(), {"node_type": "test"})
        with caplog.at_level(logging.WARNING, logger="lumen.vector.qdrant_impl"):
            provider.hybrid_search(
                dense_vector=_dummy_vector(),
                sparse_vector={"term": 1.0},
                limit=5,
            )
        assert "not yet implemented" in caplog.text


# ---------------------------------------------------------------------------
# Context Manager
# ---------------------------------------------------------------------------

class TestContextManager:
    """Tests for context manager (with statement) support."""

    def test_context_manager(self):
        """Provider must work as a context manager and close cleanly."""
        with QdrantVectorProvider(location=":memory:") as p:
            p.init_collection()
            p.upsert("ctx_001", _dummy_vector(), {"node_type": "test"})
            results = p.hybrid_search(_dummy_vector(), limit=1)
            assert "ctx_001" in results
        # After exiting, client should be None
        assert p.client is None
