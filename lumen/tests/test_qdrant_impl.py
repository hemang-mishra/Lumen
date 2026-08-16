"""
Test suite for QdrantVectorProvider.

Tests collection initialization, upsert, search, idempotency,
sparse vector warnings, and cleanup.
"""

from __future__ import annotations

import logging

import pytest

from lumen.vector.qdrant_impl import QdrantVectorProvider, _connection_for


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

    def test_a_collection_built_for_another_model_is_refused(self, tmp_path):
        """
        Changing the embedding model changes how wide its vectors are, and a
        collection's width is fixed when it is made. Left unchecked, that
        surfaces once per record, mid-run, as an array error naming neither
        the model nor the setting — while records keep being saved that
        nothing can find.
        """
        location = str(tmp_path / "vectors")

        first = QdrantVectorProvider(location=location, vector_size=768)
        first.init_collection()
        first.close()

        second = QdrantVectorProvider(location=location, vector_size=3072)
        try:
            with pytest.raises(ValueError, match="768"):
                second.init_collection()
        finally:
            second.close()

    def test_a_collection_of_the_right_width_is_left_alone(self, tmp_path):
        location = str(tmp_path / "vectors")

        first = QdrantVectorProvider(location=location, vector_size=768)
        first.init_collection()
        first.close()

        second = QdrantVectorProvider(location=location, vector_size=768)
        try:
            second.init_collection()
            second.upsert("obs_001", _dummy_vector(), {"node_type": "test"})
        finally:
            second.close()


# ---------------------------------------------------------------------------
# Upsert Operations
# ---------------------------------------------------------------------------

class TestUpsert:
    """Tests for upsert() behavior."""

    def test_basic_upsert(self, provider):
        """Can upsert a vector and retrieve it via search."""
        provider.upsert("obs_001", _dummy_vector(), {"node_type": "ObservationNode"})
        results = provider.hybrid_search(_dummy_vector(), limit=1)
        assert [hit.node_id for hit in results] == ["obs_001"]

    def test_upsert_idempotent(self, provider):
        """Upserting the same node_id twice must replace, not duplicate."""
        provider.upsert("obs_dup", _dummy_vector(val=0.1), {"node_type": "ObservationNode"})
        provider.upsert("obs_dup", _dummy_vector(val=0.2), {"node_type": "ObservationNode"})

        # Search with the second vector should find it
        results = provider.hybrid_search(_dummy_vector(val=0.2), limit=10)
        # Should only appear once
        assert [hit.node_id for hit in results].count("obs_dup") == 1

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
        assert [hit.node_id for hit in results] == ["node_a"]

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

    def test_every_hit_carries_its_score(self, provider):
        # The score is most of what a caller needs: it is the difference
        # between "the same thing said again" and "something adjacent".
        provider.upsert("near", _dummy_vector(val=0.5), {"node_type": "PatternNode"})

        results = provider.hybrid_search(_dummy_vector(val=0.5), limit=1)

        assert results[0].score == pytest.approx(1.0, abs=1e-4)

    def test_hits_come_back_closest_first(self, provider):
        near = [1.0] * 384 + [0.0] * 384
        far = [0.0] * 384 + [1.0] * 384
        provider.upsert("near", near, {"node_type": "PatternNode"})
        provider.upsert("far", far, {"node_type": "BeliefNode"})

        results = provider.hybrid_search(near, limit=2)

        assert [hit.node_id for hit in results] == ["near", "far"]
        assert results[0].score > results[1].score

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
            assert [hit.node_id for hit in results] == ["ctx_001"]
        # After exiting, client should be None
        assert p.client is None


# ---------------------------------------------------------------------------
# Where the index lives
# ---------------------------------------------------------------------------

class TestWhereTheIndexLives:
    """
    Tests for reading a location string.

    These exist because the underlying client's `location` argument is a
    *host*, not a path. Configured with "./lumen_vectors" — the obvious thing
    to write for a personal, file-backed deployment, and what this project's
    own .env.example recommends — it tried to resolve that as a server name
    and failed with a DNS error. Which form was meant is worked out from the
    value instead.
    """

    def test_the_in_memory_marker_is_passed_through(self):
        assert _connection_for(":memory:") == {"location": ":memory:"}

    def test_a_url_is_treated_as_a_server(self):
        assert _connection_for("http://localhost:6333") == {"url": "http://localhost:6333"}
        assert _connection_for("https://qdrant.example") == {"url": "https://qdrant.example"}

    def test_anything_else_is_treated_as_a_folder(self):
        assert _connection_for("./lumen_vectors") == {"path": "./lumen_vectors"}
        assert _connection_for("/var/lib/lumen/vectors") == {"path": "/var/lib/lumen/vectors"}

    def test_a_folder_actually_opens(self, tmp_path):
        # The bug this catches raised before a single vector was written.
        with QdrantVectorProvider(location=str(tmp_path / "vectors")) as provider:
            provider.init_collection()
            provider.upsert("obs_kept", _dummy_vector(), {"node_type": "test"})

    def test_what_a_folder_holds_survives_the_process_that_wrote_it(self, tmp_path):
        # The whole point. A graph on disk beside an index that empties on
        # every restart produces records semantic search can never find, and
        # nothing anywhere reports an error.
        path = str(tmp_path / "vectors")

        with QdrantVectorProvider(location=path) as writer:
            writer.init_collection()
            writer.upsert("obs_kept", _dummy_vector(), {"node_type": "test"})

        with QdrantVectorProvider(location=path) as reader:
            found = reader.hybrid_search(_dummy_vector(), limit=1)

        assert [hit.node_id for hit in found] == ["obs_kept"]
