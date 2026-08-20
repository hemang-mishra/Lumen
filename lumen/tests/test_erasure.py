"""
Tests for forgetting, in a system built never to forget.

Run against real stores throughout. Every claim here is about what a database
holds afterwards, and a stand-in would agree with whatever it was told —
which is the last thing worth trusting about an operation that cannot be
undone.

The file is in three parts: working out what an erasure covers, carrying one
out, and deciding whether to start one at all.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from lumen.config import AppConfig, MaintenanceConfig, OperationalConfig
from lumen.erasure.contracts import ErasureRefused, ErasureRequest
from lumen.erasure.runner import ErasureRunner
from lumen.erasure.service import ErasureService
from lumen.erasure.targets import GraphTargets
from lumen.graph.queries import tidy_row
from lumen.operational.enums import ErasureInitiator, ErasureScope, ErasureStatus
from lumen.operational.schemas import BufferMessageRecord, SessionBufferRecord

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
USER = "tester"


@pytest.fixture
def history(graph_store, vector_store, ops_store):
    """
    Two evenings of writing, in every store that holds a piece of them.

    Two rather than one, because the interesting question about erasing a
    single entry is what it leaves alone.
    """

    def _write(entry_id: str, episode_id: str, words: str, label: str = "evening") -> None:
        graph_store.write_node(
            "EpisodeNode",
            {
                "node_id": episode_id,
                "entry_id": entry_id,
                "occurred_at": NOW.isoformat(),
                "created_at": NOW.isoformat(),
                "valid_from": NOW.isoformat(),
                "event_date": date(2026, 8, 20),
                "session_label": label,
                "source_modality": "TEXT_ENTRY",
                "entry_class": "REFLECTIVE",
                "episode_summary": words,
                "episode_index": 1,
                "total_episodes_in_entry": 1,
                "reconciliation_status": "COMPLETE",
                "raw_text_hash": "hash",
                "overarching_themes": [words],
            },
        )
        graph_store.write_node(
            "ObservationNode",
            {
                "node_id": f"obs_{entry_id}",
                "episode_id": episode_id,
                "occurred_at": NOW.isoformat(),
                "created_at": NOW.isoformat(),
                "valid_from": NOW.isoformat(),
                "type": "EMOTION",
                "content": words,
                "signal_strength": "STANDARD",
                "provenance": "USER_GENERATED",
                "verification_status": "IMPLICIT",
                "extraction_confidence": "HIGH",
                "status": "ACTIVE",
                "raw_evidence": [words],
            },
        )
        graph_store.write_edge("contains_obs", episode_id, f"obs_{entry_id}")
        vector_store.upsert(f"obs_{entry_id}", [0.1] * 768, {"node_type": "ObservationNode"})

        ops_store.buffers.create_buffer(
            SessionBufferRecord(
                session_id=entry_id,
                user_id=USER,
                event_date=date(2026, 8, 20),
                session_label=label,
            )
        )
        ops_store.buffers.append_message(
            entry_id,
            BufferMessageRecord(
                message_id=f"msg_{entry_id}",
                session_id=entry_id,
                seq=1,
                role="USER",
                content=words,
                timestamp=NOW,
                event_date=date(2026, 8, 20),
            ),
        )

    _write("sess_monday", "ep_monday", "I felt small on Monday", "morning")
    _write("sess_tuesday", "ep_tuesday", "Tuesday was better", "evening")
    return _write


@pytest.fixture
def service(graph_store, vector_store, ops_store, ops_config):
    """The erasure service over the real stores."""
    config = AppConfig(operational=ops_config, default_user_id=USER)
    return ErasureService(
        config=config, graph=graph_store, vectors=vector_store, ops=ops_store
    )


def whole(**overrides) -> ErasureRequest:
    """An ask to erase everything, properly confirmed."""
    return ErasureRequest(
        user_id=USER, scope=ErasureScope.ALL, confirmation="ERASE", **overrides
    )


def one_entry(entry_id: str, **overrides) -> ErasureRequest:
    """An ask to erase one piece of writing, properly confirmed."""
    return ErasureRequest(
        user_id=USER,
        scope=ErasureScope.ENTRY,
        entry_id=entry_id,
        confirmation="ERASE",
        **overrides,
    )


class TestWorkingOutWhatIsCovered:
    def test_one_entry_reaches_what_was_read_out_of_it(self, graph_store, history):
        targets = GraphTargets(graph_store)

        found = targets.for_entry("sess_monday")

        assert set(found) == {"ep_monday", "obs_sess_monday"}

    def test_it_does_not_reach_another_evening(self, graph_store, history):
        targets = GraphTargets(graph_store)

        assert "ep_tuesday" not in targets.for_entry("sess_monday")

    def test_an_entry_nobody_wrote_covers_nothing(self, graph_store, history):
        assert GraphTargets(graph_store).for_entry("sess_never") == []

    def test_everything_walks_every_kind_a_page_at_a_time(self, graph_store, history):
        targets = GraphTargets(graph_store, batch_size=1)

        found = [node_id for _, page in targets.everything() for node_id in page]

        assert set(found) == {
            "ep_monday",
            "ep_tuesday",
            "obs_sess_monday",
            "obs_sess_tuesday",
        }

    def test_paging_never_repeats_a_record(self, graph_store, history):
        targets = GraphTargets(graph_store, batch_size=1)

        found = [node_id for _, page in targets.everything() for node_id in page]

        assert len(found) == len(set(found))


class TestWhatAPreviewSays:
    def test_it_counts_without_changing_anything(self, service, graph_store, history):
        plan = service.preview(one_entry("sess_monday"))

        assert plan.total_records == 2
        assert plan.records_by_kind == {"EpisodeNode": 1, "ObservationNode": 1}
        assert tidy_row(graph_store.get_node("ep_monday"))["episode_summary"] == (
            "I felt small on Monday"
        )

    def test_it_says_plainly_what_it_will_not_reach(self, service, history):
        # A belief drawn from this evening and nine others is not a copy of
        # this evening. Somebody should be told that before they agree,
        # rather than discovering it afterwards.
        plan = service.preview(one_entry("sess_monday"))

        assert plan.not_reached
        assert any("standing beliefs" in limit for limit in plan.not_reached)

    def test_a_whole_erasure_counts_the_conversations_too(self, service, history):
        plan = service.preview(whole())

        assert plan.total_records == 4
        assert plan.conversations == 2


class TestCarryingOneOut:
    def test_the_words_are_gone(self, service, graph_store, history):
        service.erase(one_entry("sess_monday"), at=NOW)

        episode = tidy_row(graph_store.get_node("ep_monday"))
        note = tidy_row(graph_store.get_node("obs_sess_monday"))

        assert episode["episode_summary"] == "[ERASED: 2026-08-20]"
        assert note["content"] == "[ERASED: 2026-08-20]"
        assert note["raw_evidence"] == ["[ERASED: 2026-08-20]"]

    def test_the_shape_of_the_history_survives(self, service, graph_store, history):
        # What makes this possible at all in a store that never deletes: the
        # proof that a history existed stays, and the history does not.
        service.erase(one_entry("sess_monday"), at=NOW)

        note = tidy_row(graph_store.get_node("obs_sess_monday"))

        assert note["node_id"] == "obs_sess_monday"
        assert note["episode_id"] == "ep_monday"
        assert note["status"] == "ACTIVE"
        assert note["occurred_at"]

    def test_the_links_survive(self, service, graph_store, history):
        service.erase(one_entry("sess_monday"), at=NOW)

        neighbourhood = graph_store.get_neighborhood("ep_monday", depth=1)

        assert any(edge.to_node_id == "obs_sess_monday" for edge in neighbourhood.edges)

    def test_the_other_evening_is_untouched(self, service, graph_store, history):
        service.erase(one_entry("sess_monday"), at=NOW)

        assert tidy_row(graph_store.get_node("ep_tuesday"))["episode_summary"] == (
            "Tuesday was better"
        )

    def test_the_search_index_forgets_them_too(self, service, vector_store, history):
        # A stored position is a reconstruction of the words it was made
        # from. Leaving it would keep the record findable by everything it
        # used to say.
        service.erase(one_entry("sess_monday"), at=NOW)

        assert vector_store.get_vectors(["obs_sess_monday"]) == {}
        assert "obs_sess_tuesday" in vector_store.get_vectors(["obs_sess_tuesday"])

    def test_the_person_s_own_sentences_go_as_well(self, service, ops_store, history):
        # The graph holds what was read out of an evening. The working
        # database holds the evening itself.
        service.erase(one_entry("sess_monday"), at=NOW)

        assert ops_store.buffers.get_messages("sess_monday")[0].content == (
            "[ERASED: 2026-08-20]"
        )
        assert ops_store.buffers.get_messages("sess_tuesday")[0].content == (
            "Tuesday was better"
        )

    def test_everything_carries_the_same_date(self, service, graph_store, ops_store, history):
        # One erasure is one event, and two clocks would make it look like
        # two.
        service.erase(one_entry("sess_monday"), at=NOW)

        assert tidy_row(graph_store.get_node("ep_monday"))["episode_summary"] == (
            ops_store.buffers.get_messages("sess_monday")[0].content
        )

    def test_a_whole_erasure_reaches_both_evenings(self, service, graph_store, history):
        service.erase(whole(), at=NOW)

        for episode in ("ep_monday", "ep_tuesday"):
            assert tidy_row(graph_store.get_node(episode))["episode_summary"] == (
                "[ERASED: 2026-08-20]"
            )

    def test_a_person_keeps_being_a_person(self, service, graph_store, history):
        graph_store.write_node(
            "PersonEntityNode",
            {
                "node_id": "per_alex",
                "canonical_name": "Alex",
                "first_mentioned_at": NOW.isoformat(),
                "last_mentioned_at": NOW.isoformat(),
                "relationship_to_user": "FRIEND",
                "relationship_sentiment_trend": "STABLE",
                "status": "ACTIVE",
                "aliases": ["Al"],
                "mention_count": 4,
            },
        )

        service.erase(whole(), at=NOW)
        person = tidy_row(graph_store.get_node("per_alex"))

        assert "Alex" not in person["canonical_name"]
        assert person["canonical_name"].startswith("[ERASED_PERSON_")
        assert person["mention_count"] == 4

    def test_it_works_in_batches_small_enough_not_to_block_anybody(
        self, graph_store, vector_store, ops_store, history
    ):
        # A whole history in one statement would hold the store's write lock
        # for as long as it took, and somebody could be mid-conversation.
        runner = ErasureRunner(
            graph=graph_store,
            vectors=vector_store,
            ops=ops_store,
            config=MaintenanceConfig(erasure_batch_size=1),
        )

        report = runner.run(whole(), entry_ids=["sess_monday"], at=NOW)

        assert report.records_anonymized == 4


class TestTheReceipt:
    def test_one_is_written_and_closed(self, service, history):
        report = service.erase(one_entry("sess_monday"), at=NOW)
        audits = service.audits(USER)

        assert report.status is ErasureStatus.COMPLETE
        assert [record.id for record in audits] == [report.audit_id]
        assert audits[0].status is ErasureStatus.COMPLETE

    def test_it_says_what_was_actually_removed(self, service, history):
        report = service.erase(one_entry("sess_monday"), at=NOW)
        audit = service.audits(USER)[0]

        assert audit.nodes_anonymized == 2
        # One of the two had a vector. A record claiming more than happened
        # is worse than no record.
        assert audit.embeddings_deleted == 1

    def test_it_holds_no_readable_name(self, service, history):
        service.erase(one_entry("sess_monday"), at=NOW)
        audit = service.audits(USER)[0]

        assert USER not in audit.user_id_hash

    def test_two_erasures_in_one_day_are_two_receipts(self, service, history):
        first = service.erase(one_entry("sess_monday"), at=NOW)
        second = service.erase(one_entry("sess_tuesday"), at=NOW)

        assert first.audit_id != second.audit_id
        assert len(service.audits(USER)) == 2

    def test_who_asked_is_recorded(self, service, history):
        service.erase(
            one_entry("sess_monday", initiated_by=ErasureInitiator.ADMIN_REQUEST),
            at=NOW,
        )

        assert service.audits(USER)[0].initiated_by is ErasureInitiator.ADMIN_REQUEST


class TestWhenAStepFails:
    def test_the_rest_still_runs_and_the_receipt_says_it_failed(
        self, graph_store, ops_store, history
    ):
        # An erasure that stops at the first refusal leaves more words behind
        # than one that carries on and reports what it could not reach.
        class RefusesToDelete:
            def delete(self, node_ids):
                raise RuntimeError("the index is unreachable")

        runner = ErasureRunner(
            graph=graph_store, vectors=RefusesToDelete(), ops=ops_store
        )

        report = runner.run(one_entry("sess_monday"), entry_ids=["sess_monday"], at=NOW)

        assert report.status is ErasureStatus.FAILED
        assert report.records_anonymized == 2
        assert any("index" in failure for failure in report.failures)

    def test_a_crash_mid_sweep_still_leaves_a_trace(
        self, graph_store, vector_store, ops_store, history
    ):
        # The record is opened before any of the work, because a history
        # half forgotten with nothing saying so is the worst outcome there
        # is.
        class Explodes:
            def delete(self, node_ids):
                raise RuntimeError("boom")

        runner = ErasureRunner(graph=graph_store, vectors=Explodes(), ops=ops_store)
        report = runner.run(whole(), entry_ids=[], at=NOW)

        assert ops_store.erasure.get(report.audit_id) is not None


class TestDecidingWhetherToStart:
    def test_the_wrong_phrase_erases_nothing(self, service, graph_store, history):
        with pytest.raises(ErasureRefused):
            service.erase(
                ErasureRequest(
                    user_id=USER,
                    scope=ErasureScope.ENTRY,
                    entry_id="sess_monday",
                    confirmation="please",
                )
            )

        assert tidy_row(graph_store.get_node("ep_monday"))["episode_summary"] == (
            "I felt small on Monday"
        )

    def test_no_phrase_at_all_erases_nothing(self, service, history):
        with pytest.raises(ErasureRefused):
            service.erase(
                ErasureRequest(
                    user_id=USER, scope=ErasureScope.ENTRY, entry_id="sess_monday"
                )
            )

    def test_an_entry_nobody_wrote_is_refused_rather_than_succeeding_quietly(
        self, service, history
    ):
        # Silently succeeding on a mistyped identifier would tell somebody
        # their evening had been forgotten when nothing was touched.
        with pytest.raises(ErasureRefused):
            service.erase(one_entry("sess_never"))

    def test_no_receipt_is_written_for_a_refusal(self, service, history):
        with pytest.raises(ErasureRefused):
            service.erase(one_entry("sess_never"))

        assert service.audits(USER) == []

    def test_erasing_one_entry_means_saying_which(self):
        with pytest.raises(ValueError):
            ErasureRequest(user_id=USER, scope=ErasureScope.ENTRY, confirmation="ERASE")

    def test_erasing_everything_must_not_also_name_one(self):
        # Somebody who sent both meant one of the two, and guessing which
        # would either erase far more than they wanted or far less.
        with pytest.raises(ValueError):
            ErasureRequest(
                user_id=USER,
                scope=ErasureScope.ALL,
                entry_id="sess_monday",
                confirmation="ERASE",
            )
