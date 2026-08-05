"""Tests for the record of erasures."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select

from lumen.operational import models
from lumen.operational.enums import ErasureInitiator, ErasureStatus
from lumen.operational.engine import create_session_factory, session_scope
from lumen.operational.schemas import ErasureAuditRecord

ERASED_AT = datetime(2026, 7, 1, 14, 22, tzinfo=UTC)


def _record(record_id: str = "era_2026_07_01_001", **overrides) -> ErasureAuditRecord:
    defaults = {
        "id": record_id,
        "user_id": "local",
        "erased_at": ERASED_AT,
        "nodes_anonymized": 847,
        "embeddings_deleted": 847,
        "entry_ids_affected": ["entry_2026_06_11_raw", "entry_2026_05_20_raw"],
        "initiated_by": ErasureInitiator.USER_REQUEST,
        "status": ErasureStatus.COMPLETE,
    }
    defaults.update(overrides)
    return ErasureAuditRecord(**defaults)


class TestRecording:
    def test_a_record_is_stored(self, ops_store):
        assert ops_store.erasure.record(_record()) == "era_2026_07_01_001"
        assert ops_store.erasure.get("era_2026_07_01_001") is not None

    def test_the_counts_survive(self, ops_store):
        ops_store.erasure.record(_record())
        stored = ops_store.erasure.get("era_2026_07_01_001")
        assert stored.nodes_anonymized == 847
        assert stored.embeddings_deleted == 847

    def test_the_affected_entries_survive(self, ops_store):
        ops_store.erasure.record(_record())
        stored = ops_store.erasure.get("era_2026_07_01_001")
        assert stored.entry_ids_affected == [
            "entry_2026_06_11_raw",
            "entry_2026_05_20_raw",
        ]

    def test_a_run_still_in_progress_can_be_recorded(self, ops_store):
        ops_store.erasure.record(
            _record(status=ErasureStatus.IN_PROGRESS, nodes_anonymized=0)
        )
        assert ops_store.erasure.get("era_2026_07_01_001").status == ErasureStatus.IN_PROGRESS

    def test_who_asked_is_recorded(self, ops_store):
        ops_store.erasure.record(
            _record(initiated_by=ErasureInitiator.AUTOMATED_RETENTION_POLICY)
        )
        stored = ops_store.erasure.get("era_2026_07_01_001")
        assert stored.initiated_by == ErasureInitiator.AUTOMATED_RETENTION_POLICY

    def test_an_unknown_record_reads_back_as_nothing(self, ops_store):
        assert ops_store.erasure.get("era_missing") is None


class TestIdentifierIsNeverStoredPlainly:
    def test_the_user_id_is_hashed(self, ops_store):
        """
        A record proving data was deleted must not itself preserve who it
        belonged to.
        """
        ops_store.erasure.record(_record())
        stored = ops_store.erasure.get("era_2026_07_01_001")
        assert stored.user_id_hash == hashlib.sha256(b"local").hexdigest()

    def test_the_plain_identifier_never_reaches_the_database(self, ops_store, ops_engine):
        ops_store.erasure.record(_record(user_id="hemang@example.com"))

        factory = create_session_factory(ops_engine)
        with session_scope(factory) as db:
            row = db.scalars(select(models.DataErasureAudit)).one()
            stored = {
                column.name: getattr(row, column.name)
                for column in models.DataErasureAudit.__table__.columns
            }

        assert "hemang@example.com" not in str(stored)

    def test_what_comes_back_cannot_look_like_a_plain_identifier(self, ops_store):
        """
        The type returned carries a hash field only, so nothing downstream can
        mistake it for a readable identifier.
        """
        ops_store.erasure.record(_record())
        stored = ops_store.erasure.get("era_2026_07_01_001")
        assert not hasattr(stored, "user_id")

    def test_hashing_is_consistent(self, ops_store):
        ops_store.erasure.record(_record("era_1"))
        ops_store.erasure.record(_record("era_2"))
        first = ops_store.erasure.get("era_1")
        second = ops_store.erasure.get("era_2")
        assert first.user_id_hash == second.user_id_hash

    def test_different_users_hash_differently(self, ops_store):
        ops_store.erasure.record(_record("era_1", user_id="alice"))
        ops_store.erasure.record(_record("era_2", user_id="bob"))
        assert (
            ops_store.erasure.get("era_1").user_id_hash
            != ops_store.erasure.get("era_2").user_id_hash
        )


class TestListForUser:
    def test_a_user_s_records_are_found_by_their_plain_id(self, ops_store):
        ops_store.erasure.record(_record("era_1", user_id="alice"))
        ops_store.erasure.record(_record("era_2", user_id="alice"))
        ops_store.erasure.record(_record("era_3", user_id="bob"))

        assert len(ops_store.erasure.list_for_user("alice")) == 2

    def test_the_newest_comes_first(self, ops_store):
        ops_store.erasure.record(
            _record("era_old", erased_at=datetime(2026, 1, 1, tzinfo=UTC))
        )
        ops_store.erasure.record(
            _record("era_new", erased_at=datetime(2026, 7, 1, tzinfo=UTC))
        )

        assert [r.id for r in ops_store.erasure.list_for_user("local")] == [
            "era_new", "era_old",
        ]

    def test_a_user_with_no_erasures_reads_back_empty(self, ops_store):
        assert ops_store.erasure.list_for_user("nobody") == []
