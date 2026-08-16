"""
Tests for the record of what has been uploaded.

Three things this table has to get right, and each has its own class below:
recognising a conversation it has seen before, following one upload as its
conversations finish at different times, and never losing the trace of a run
that already happened.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from lumen.operational.enums import ImportStatus
from lumen.operational.repositories import RecordNotFoundError
from lumen.operational.schemas import ImportRecord

TODAY = date(2026, 8, 2)


def an_import(**overrides) -> ImportRecord:
    """One import row, with everything a caller normally fills in."""
    fields = {
        "import_id": "imp_001",
        "batch_id": "batch_001",
        "user_id": "local",
        "source_conversation_id": "conv-aug-2",
        "title": "Aug 2",
        "filename": "aug2.json",
        "event_date": TODAY,
        "message_count": 16,
        "session_id": "sb_2026_08_02_aug_2_abcd1234",
    }
    fields.update(overrides)
    return ImportRecord(**fields)


class TestRecordingAnImport:
    def test_a_recorded_import_can_be_read_back(self, ops_store):
        ops_store.imports.record(an_import())

        stored = ops_store.imports.get("imp_001")
        assert stored.title == "Aug 2"
        assert stored.event_date == TODAY
        assert stored.message_count == 16
        assert stored.status is ImportStatus.QUEUED

    def test_the_time_it_arrived_is_filled_in(self, ops_store):
        ops_store.imports.record(an_import())

        assert ops_store.imports.get("imp_001").created_at is not None

    def test_asking_for_an_import_that_does_not_exist_gives_nothing(self, ops_store):
        assert ops_store.imports.get("no_such_import") is None


class TestRecognisingARepeatUpload:
    def test_a_conversation_already_imported_is_found_by_its_export_id(self, ops_store):
        ops_store.imports.record(an_import())

        found = ops_store.imports.find_by_conversation("local", "conv-aug-2")
        assert found.import_id == "imp_001"

    def test_a_conversation_never_seen_is_not_found(self, ops_store):
        ops_store.imports.record(an_import())

        assert ops_store.imports.find_by_conversation("local", "conv-aug-9") is None

    def test_another_users_import_of_the_same_conversation_is_not_a_repeat(
        self, ops_store
    ):
        # Two people exporting from the same application can hold the same
        # conversation identifier, and neither has seen the other's history.
        ops_store.imports.record(an_import())

        assert ops_store.imports.find_by_conversation("someone_else", "conv-aug-2") is None

    def test_the_database_itself_refuses_a_second_row_for_one_conversation(
        self, ops_store
    ):
        # The dedupe check runs before staging, but the rule is enforced by
        # the database as well — a check that only lives in code is a check
        # that two requests arriving together can walk straight past.
        ops_store.imports.record(an_import())

        with pytest.raises(IntegrityError):
            ops_store.imports.record(an_import(import_id="imp_002"))

    def test_the_same_user_may_import_two_different_conversations(self, ops_store):
        ops_store.imports.record(an_import())
        ops_store.imports.record(
            an_import(import_id="imp_002", source_conversation_id="conv-aug-3")
        )

        assert len(ops_store.imports.list_recent("local")) == 2


class TestFollowingAnImport:
    def test_the_run_is_attached_once_it_starts(self, ops_store):
        ops_store.imports.record(an_import())

        updated = ops_store.imports.update_status(
            "imp_001", ImportStatus.RUNNING, job_id="job_1", trace_id="trace_1"
        )

        assert updated.status is ImportStatus.RUNNING
        assert updated.job_id == "job_1"
        assert updated.trace_id == "trace_1"

    def test_finishing_stamps_the_time_it_finished(self, ops_store):
        ops_store.imports.record(an_import())

        assert ops_store.imports.update_status("imp_001", ImportStatus.COMPLETE).finished_at

    def test_a_run_still_going_has_no_finish_time(self, ops_store):
        ops_store.imports.record(an_import())

        assert ops_store.imports.update_status("imp_001", ImportStatus.RUNNING).finished_at is None

    def test_a_failure_keeps_the_reason(self, ops_store):
        ops_store.imports.record(an_import())

        failed = ops_store.imports.update_status(
            "imp_001", ImportStatus.FAILED, error="no model is configured"
        )

        assert failed.error == "no model is configured"

    def test_a_later_update_does_not_erase_the_trace_of_an_earlier_one(self, ops_store):
        # A retry that cannot reach a model would otherwise wipe out the
        # trace id of the attempt that did run, and with it the only way
        # back to what that attempt wrote.
        ops_store.imports.record(an_import())
        ops_store.imports.update_status(
            "imp_001", ImportStatus.RUNNING, job_id="job_1", trace_id="trace_1"
        )

        after = ops_store.imports.update_status("imp_001", ImportStatus.FAILED, error="gave up")

        assert after.trace_id == "trace_1"
        assert after.job_id == "job_1"

    def test_the_first_finish_time_is_the_one_that_sticks(self, ops_store):
        ops_store.imports.record(an_import())
        first = ops_store.imports.update_status("imp_001", ImportStatus.COMPLETE)

        again = ops_store.imports.update_status("imp_001", ImportStatus.COMPLETE)

        assert again.finished_at == first.finished_at

    def test_updating_something_that_does_not_exist_says_so(self, ops_store):
        with pytest.raises(RecordNotFoundError, match="no import with id"):
            ops_store.imports.update_status("ghost", ImportStatus.COMPLETE)


class TestOneWholeUpload:
    def test_a_batch_gathers_every_conversation_in_the_file(self, ops_store):
        ops_store.imports.record(an_import())
        ops_store.imports.record(
            an_import(
                import_id="imp_002",
                source_conversation_id="conv-aug-3",
                title="Aug 3",
                event_date=date(2026, 8, 3),
            )
        )

        batch = ops_store.imports.get_batch("batch_001")

        assert [record.title for record in batch.imports] == ["Aug 2", "Aug 3"]
        assert batch.filename == "aug2.json"

    def test_a_batch_only_holds_its_own_upload(self, ops_store):
        ops_store.imports.record(an_import())
        ops_store.imports.record(
            an_import(
                import_id="imp_002",
                batch_id="batch_002",
                source_conversation_id="conv-aug-3",
            )
        )

        assert len(ops_store.imports.get_batch("batch_001").imports) == 1

    def test_an_upload_nobody_made_is_nothing_rather_than_empty(self, ops_store):
        assert ops_store.imports.get_batch("batch_nobody_made") is None

    def test_an_upload_is_unfinished_while_any_part_of_it_is_still_running(
        self, ops_store
    ):
        ops_store.imports.record(an_import())
        ops_store.imports.record(
            an_import(import_id="imp_002", source_conversation_id="conv-aug-3")
        )
        ops_store.imports.update_status("imp_001", ImportStatus.COMPLETE)

        assert ops_store.imports.get_batch("batch_001").finished is False

    def test_an_upload_is_finished_once_nothing_in_it_will_change_again(self, ops_store):
        ops_store.imports.record(an_import())
        ops_store.imports.record(
            an_import(import_id="imp_002", source_conversation_id="conv-aug-3")
        )
        ops_store.imports.update_status("imp_001", ImportStatus.COMPLETE)
        ops_store.imports.update_status("imp_002", ImportStatus.FAILED, error="nope")

        assert ops_store.imports.get_batch("batch_001").finished is True

    def test_a_conversation_recognised_from_an_earlier_upload_counts_as_settled(
        self, ops_store
    ):
        ops_store.imports.record(an_import(status=ImportStatus.DUPLICATE))

        assert ops_store.imports.get_batch("batch_001").finished is True


class TestTheHistory:
    def test_the_newest_upload_comes_first(self, ops_store):
        ops_store.imports.record(an_import(created_at=None))
        ops_store.imports.record(
            an_import(import_id="imp_002", source_conversation_id="conv-aug-3")
        )

        history = ops_store.imports.list_recent("local")

        assert history[0].created_at >= history[1].created_at

    def test_the_history_is_one_persons_own(self, ops_store):
        ops_store.imports.record(an_import())
        ops_store.imports.record(
            an_import(import_id="imp_002", user_id="someone_else")
        )

        assert [record.import_id for record in ops_store.imports.list_recent("local")] == [
            "imp_001"
        ]

    def test_the_history_can_be_kept_short(self, ops_store):
        for index in range(5):
            ops_store.imports.record(
                an_import(
                    import_id=f"imp_{index}",
                    source_conversation_id=f"conv-{index}",
                )
            )

        assert len(ops_store.imports.list_recent("local", limit=2)) == 2

    def test_somebody_who_has_uploaded_nothing_has_an_empty_history(self, ops_store):
        assert ops_store.imports.list_recent("nobody") == []
