"""
Reading a stored vector back out of the index.

Added for the continuity check, which needs to know where a node sits
without searching for it. The interesting part is the identifier: the index
wants a UUID and Lumen names things like `pat_2026_06_11_001`, so one is
derived from the other — and this read only works because the derivation is
the same one the write uses.
"""

from __future__ import annotations

import pytest

from lumen.query.buffer import cosine


class TestReadingVectorsBack:
    def test_what_was_written_comes_back(self, vector_store):
        # Pointing the same way, not identical numbers. The collection
        # measures cosine distance, so the index scales everything to unit
        # length on the way in and keeps no copy of the original — which
        # changes nothing for a comparison of directions.
        vector_store.upsert("obs_1", [0.5] * 768, {"node_type": "ObservationNode"})

        found = vector_store.get_vectors(["obs_1"])

        assert cosine(found["obs_1"], [0.5] * 768) == pytest.approx(1.0)

    def test_several_come_back_at_once(self, vector_store):
        vector_store.upsert("obs_1", [0.1] * 768, {})
        vector_store.upsert("obs_2", [0.2] * 768, {})

        found = vector_store.get_vectors(["obs_1", "obs_2"])

        assert set(found) == {"obs_1", "obs_2"}

    def test_a_node_never_indexed_is_absent_rather_than_empty(self, vector_store):
        # "Never indexed" and "indexed" mean different things to the thing
        # asking, so the answer must be able to say which.
        vector_store.upsert("obs_1", [0.1] * 768, {})

        found = vector_store.get_vectors(["obs_1", "obs_missing"])

        assert set(found) == {"obs_1"}

    def test_asking_about_nothing_answers_nothing(self, vector_store):
        assert vector_store.get_vectors([]) == {}

    def test_a_rewritten_vector_reads_back_as_the_new_one(self, vector_store):
        # The same node written twice is one point, not two — which is the
        # same derivation this read depends on.
        vector_store.upsert("obs_1", [1.0] + [0.0] * 767, {})
        vector_store.upsert("obs_1", [0.0, 1.0] + [0.0] * 766, {})

        found = vector_store.get_vectors(["obs_1"])

        assert cosine(found["obs_1"], [0.0, 1.0] + [0.0] * 766) == pytest.approx(1.0)

    def test_the_vector_read_back_is_the_one_search_would_match_on(
        self, vector_store, embedder
    ):
        # The point of the whole thing: the continuity check compares
        # against exactly what the index would have compared against.
        vector = embedder.embed_text("the critic brain pattern")
        vector_store.upsert("pat_1", vector, {"node_type": "PatternNode"})

        found = vector_store.get_vectors(["pat_1"])

        assert cosine(found["pat_1"], vector) == pytest.approx(1.0, abs=1e-6)
