"""
Tests for deciding what becomes findable and turning it into numbers.

The most important test in this file is the shortest one: the list of record
kinds that get indexed has to be the same list the search stage reads. Every
other test here is about a record that might not be findable; that one is
about a whole category of record silently never being findable at all.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lumen.pipeline.orchestration import embed
from lumen.pipeline.orchestration.contracts import EmbeddingFailed
from lumen.providers.errors import ProviderTimeoutError
from lumen.providers.fake import FakeEmbeddingProvider
from lumen.schemas.enums import (
    EmbeddingTaskType,
    ModelRole,
    LifecycleNodeStatus,
    ObservationStatus,
)
from lumen.schemas.pipeline import GraphWritePlan, PlannedNode

MOMENT = datetime(2026, 6, 11, 20, 0, tzinfo=UTC)


class BrokenEmbedder(FakeEmbeddingProvider):
    """An embedder that cannot produce vectors."""

    def embed_batch(self, texts, *, task_type=EmbeddingTaskType.DOCUMENT):
        raise ProviderTimeoutError(
            "took too long", provider="fake", model="fake", role=ModelRole.EMBEDDING
        )


class ShortChangingEmbedder(FakeEmbeddingProvider):
    """An embedder that returns fewer vectors than it was given texts."""

    def embed_batch(self, texts, *, task_type=EmbeddingTaskType.DOCUMENT):
        return super().embed_batch(texts, task_type=task_type)[:-1]


def _plan(*nodes: PlannedNode) -> GraphWritePlan:
    return GraphWritePlan(nodes=list(nodes))


class TestWhatGetsIndexed:
    def test_the_indexed_kinds_are_exactly_what_the_search_looks_for(self):
        # The one test here that guards against a silent hole rather than a
        # visible bug. Indexing a kind nothing searches for is waste;
        # searching for a kind nothing indexes finds nothing, forever, with
        # no error anywhere.
        from lumen.pipeline.retrieval.semantic import CONTENT_TABLES

        assert embed.INDEXED_NODE_TYPES == CONTENT_TABLES

    @pytest.mark.parametrize(
        "node_type",
        ["ObservationNode", "EventNode", "SessionNode", "PatternNode", "BeliefNode"],
    )
    def test_the_things_a_person_actually_said_are_indexed(
        self, node_type, sample_observation, sample_event, sample_session,
        sample_pattern, sample_belief,
    ):
        node = {
            "ObservationNode": sample_observation,
            "EventNode": sample_event,
            "SessionNode": sample_session,
            "PatternNode": sample_pattern,
            "BeliefNode": sample_belief,
        }[node_type]

        assert embed.text_for_index(PlannedNode(node_type=node_type, node=node))

    @pytest.mark.parametrize(
        "node_type,fixture",
        [
            ("EpisodeNode", "sample_episode"),
            ("DecisionAuditNode", "sample_decision_audit"),
            ("PersonEntityNode", "sample_person"),
            ("CausalStepNode", "sample_causal_step"),
        ],
    )
    def test_machinery_is_never_indexed(self, node_type, fixture, request):
        # The note of a decision and a person's own record are how the
        # system works, not things the person said.
        node = request.getfixturevalue(fixture)

        assert embed.text_for_index(PlannedNode(node_type=node_type, node=node)) is None

    def test_a_retired_record_is_not_indexed(self, sample_pattern):
        # The search stage filters these out on the way back, so indexing
        # one costs a call and buys nothing.
        retired = sample_pattern.model_copy(update={"status": LifecycleNodeStatus.SUPERSEDED})

        assert embed.text_for_index(PlannedNode(node_type="PatternNode", node=retired)) is None

    def test_a_finding_that_could_not_be_read_is_not_indexed(self, sample_observation):
        failed = sample_observation.model_copy(
            update={"status": ObservationStatus.EXTRACTION_FAILED}
        )

        assert (
            embed.text_for_index(PlannedNode(node_type="ObservationNode", node=failed))
            is None
        )


class TestTheSearchableText:
    def test_a_finding_is_indexed_by_its_whole_content(self, sample_observation):
        # Not by a shortened preview. A record indexed from its first few
        # lines is only findable by its first few lines.
        text = embed.text_for_index(
            PlannedNode(node_type="ObservationNode", node=sample_observation)
        )

        assert text == sample_observation.content

    def test_a_pattern_is_indexed_by_its_name_and_its_description(self, sample_pattern):
        text = embed.text_for_index(
            PlannedNode(node_type="PatternNode", node=sample_pattern)
        )

        assert sample_pattern.pattern_name in text
        assert sample_pattern.pattern_description in text

    def test_wording_named_on_the_record_wins(self, sample_observation):
        planned = PlannedNode(
            node_type="ObservationNode",
            node=sample_observation,
            searchable_text="what this is really about",
        )

        assert embed.text_for_index(planned) == "what this is really about"

    def test_a_record_with_no_words_in_it_is_not_indexed(self, sample_observation):
        # An index entry for empty text matches everything and means
        # nothing, so it is left out rather than stored blank.
        blank = sample_observation.model_construct(
            **{**sample_observation.model_dump(), "content": "   "}
        )

        assert (
            embed.text_for_index(PlannedNode(node_type="ObservationNode", node=blank))
            is None
        )

    def test_blank_named_wording_falls_back_to_the_content(self, sample_observation):
        planned = PlannedNode(
            node_type="ObservationNode",
            node=sample_observation,
            searchable_text="   ",
        )

        assert embed.text_for_index(planned) == sample_observation.content


class TestPreparingTheIndex:
    def test_everything_is_embedded_in_one_request(
        self, embedder, sample_observation, sample_pattern, monkeypatch
    ):
        # A rich entry produces dozens of records. One call each would make
        # this the most expensive part of the pipeline by a wide margin.
        batches = []
        original = embedder.embed_batch
        monkeypatch.setattr(
            embedder,
            "embed_batch",
            lambda texts, **kw: batches.append(texts) or original(texts, **kw),
        )

        embed.prepare_index(
            _plan(
                PlannedNode(node_type="ObservationNode", node=sample_observation),
                PlannedNode(node_type="PatternNode", node=sample_pattern),
            ),
            embedder=embedder,
        )

        assert len(batches) == 1
        assert len(batches[0]) == 2

    def test_the_records_are_embedded_as_documents(
        self, embedder, sample_observation, monkeypatch
    ):
        seen = {}
        original = embedder.embed_batch
        monkeypatch.setattr(
            embedder,
            "embed_batch",
            lambda texts, **kw: seen.update(kw) or original(texts, **kw),
        )

        embed.prepare_index(
            _plan(PlannedNode(node_type="ObservationNode", node=sample_observation)),
            embedder=embedder,
        )

        assert seen["task_type"] is EmbeddingTaskType.DOCUMENT

    def test_each_entry_keeps_the_identifier_its_record_has(
        self, embedder, sample_observation
    ):
        # The same identifier in both stores is what lets a search result be
        # read back as a real record.
        entries = embed.prepare_index(
            _plan(PlannedNode(node_type="ObservationNode", node=sample_observation)),
            embedder=embedder,
        )

        assert [e.node_id for e in entries] == [sample_observation.node_id]

    def test_a_few_plain_facts_are_stored_alongside(self, embedder, sample_observation):
        entries = embed.prepare_index(
            _plan(PlannedNode(node_type="ObservationNode", node=sample_observation)),
            embedder=embedder,
        )

        assert entries[0].payload["node_type"] == "ObservationNode"
        assert entries[0].payload["episode_id"] == sample_observation.episode_id
        assert entries[0].payload["status"] == ObservationStatus.ACTIVE.value

    def test_a_plan_with_nothing_to_index_asks_for_nothing(
        self, embedder, sample_episode
    ):
        assert (
            embed.prepare_index(
                _plan(PlannedNode(node_type="EpisodeNode", node=sample_episode)),
                embedder=embedder,
            )
            == []
        )

    def test_a_broken_embedder_stops_the_episode_before_anything_is_written(
        self, sample_observation
    ):
        # The whole reason this runs before saving: a failure here costs a
        # re-run, not a graph full of records nobody can find.
        with pytest.raises(EmbeddingFailed):
            embed.prepare_index(
                _plan(PlannedNode(node_type="ObservationNode", node=sample_observation)),
                embedder=BrokenEmbedder(dimensions=768),
            )

    def test_a_short_reply_is_refused_rather_than_misaligned(
        self, sample_observation, sample_pattern
    ):
        # Pairing vectors with records by position means a missing one would
        # give every record after it somebody else's meaning.
        with pytest.raises(EmbeddingFailed, match="got back"):
            embed.prepare_index(
                _plan(
                    PlannedNode(node_type="ObservationNode", node=sample_observation),
                    PlannedNode(node_type="PatternNode", node=sample_pattern),
                ),
                embedder=ShortChangingEmbedder(dimensions=768),
            )


class TestRepairingTheIndex:
    def test_records_saved_without_a_search_entry_are_found_and_fixed(
        self, ops_store, graph_store, vector_store, embedder, buffer_with_messages,
        seed_observation,
    ):
        # The gap between "written to the graph" and "written to the index"
        # in the run log is exactly the repair list. Nothing extra has to be
        # remembered for this to work.
        job = ops_store.jobs.create_job(
            session_id=buffer_with_messages.session_id, user_id="local"
        )
        seed_observation("obs_unfindable", "a thing nobody can search for", indexed=False)
        ops_store.jobs.record_write(
            job_id=job.job_id,
            stage="STAGE_4_GRAPH_WRITE",
            target="GRAPH_NODE",
            node_id="obs_unfindable",
            episode_id="ep_old",
        )

        repaired = embed.repair_index(
            job.trace_id,
            ops=ops_store,
            graph=graph_store,
            vectors=vector_store,
            embedder=embedder,
        )

        assert repaired == ["obs_unfindable"]

    def test_a_second_repair_finds_nothing_left_to_do(
        self, ops_store, graph_store, vector_store, embedder, buffer_with_messages,
        seed_observation,
    ):
        job = ops_store.jobs.create_job(
            session_id=buffer_with_messages.session_id, user_id="local"
        )
        seed_observation("obs_unfindable", "a thing", indexed=False)
        ops_store.jobs.record_write(
            job_id=job.job_id,
            stage="STAGE_4_GRAPH_WRITE",
            target="GRAPH_NODE",
            node_id="obs_unfindable",
        )
        kwargs = dict(
            ops=ops_store, graph=graph_store, vectors=vector_store, embedder=embedder
        )

        embed.repair_index(job.trace_id, **kwargs)

        assert embed.repair_index(job.trace_id, **kwargs) == []

    def test_records_that_were_already_indexed_are_left_alone(
        self, ops_store, graph_store, vector_store, embedder, buffer_with_messages,
        seed_observation,
    ):
        job = ops_store.jobs.create_job(
            session_id=buffer_with_messages.session_id, user_id="local"
        )
        seed_observation("obs_fine", "a findable thing")
        for target in ("GRAPH_NODE", "VECTOR"):
            ops_store.jobs.record_write(
                job_id=job.job_id,
                stage="STAGE_4_GRAPH_WRITE",
                target=target,
                node_id="obs_fine",
            )

        assert (
            embed.repair_index(
                job.trace_id,
                ops=ops_store,
                graph=graph_store,
                vectors=vector_store,
                embedder=embedder,
            )
            == []
        )

    def test_a_record_of_a_kind_nobody_searches_for_is_left_unindexed(
        self, ops_store, graph_store, vector_store, embedder, buffer_with_messages,
        sample_episode,
    ):
        # Episode records are written to the graph but never indexed, so a
        # repair must not decide they are missing and add them.
        job = ops_store.jobs.create_job(
            session_id=buffer_with_messages.session_id, user_id="local"
        )
        graph_store.write_node("EpisodeNode", sample_episode)
        ops_store.jobs.record_write(
            job_id=job.job_id,
            stage="STAGE_4_GRAPH_WRITE",
            target="GRAPH_NODE",
            node_id=sample_episode.node_id,
        )

        assert (
            embed.repair_index(
                job.trace_id,
                ops=ops_store,
                graph=graph_store,
                vectors=vector_store,
                embedder=embedder,
            )
            == []
        )

    def test_an_unknown_run_is_reported_rather_than_guessed_at(
        self, ops_store, graph_store, vector_store, embedder, caplog
    ):
        assert (
            embed.repair_index(
                "no-such-trace",
                ops=ops_store,
                graph=graph_store,
                vectors=vector_store,
                embedder=embedder,
            )
            == []
        )
        assert "no run found" in caplog.text

    def test_a_record_missing_from_the_graph_is_reported_not_invented(
        self, ops_store, graph_store, vector_store, embedder, buffer_with_messages,
        caplog,
    ):
        job = ops_store.jobs.create_job(
            session_id=buffer_with_messages.session_id, user_id="local"
        )
        ops_store.jobs.record_write(
            job_id=job.job_id,
            stage="STAGE_4_GRAPH_WRITE",
            target="GRAPH_NODE",
            node_id="obs_vanished",
        )

        repaired = embed.repair_index(
            job.trace_id,
            ops=ops_store,
            graph=graph_store,
            vectors=vector_store,
            embedder=embedder,
        )

        assert repaired == []
        assert "no longer in the graph" in caplog.text
