"""
Five days of writing, and whether the graph accumulated anything.

Every other test in this suite processes one entry. What the system is for
is what happens across many, and the failure that matters cannot appear in a
single-entry test at all: the same struggle written about three times
becoming three separate records with one piece of evidence each. That graph
is valid in every particular — every node well-formed, every link correct,
every decision recorded — and completely useless, because nothing in it has
built up into anything.

The four groups below are the four ways that rot happens. They read the
finished graph through the same interface a person would use, because a
check that needs privileged access to the storage layer is not checking the
thing anybody will actually look at.
"""

from __future__ import annotations

import pytest

from lumen.config import AppConfig
from lumen.operational.enums import WriteTarget
from lumen.simulation import CORPUS, simulate_days
from lumen.simulation.corpus import BELIEF_PACE, PATTERN_COMPARISON


@pytest.fixture(scope="class")
def week(request):
    """
    The whole written week, run once for the class that reads it.

    Run once rather than per test because five days through the real
    pipeline is the expensive part, and every check here reads the same
    finished graph rather than changing it.
    """
    from lumen.graph.kuzu_impl import KuzuGraphProvider
    from lumen.operational.engine import create_ops_engine
    from lumen.operational.migrator import upgrade_to_head
    from lumen.operational.sqlalchemy_impl import SQLAlchemyOperationalStore
    from lumen.config import OperationalConfig
    from lumen.vector.qdrant_impl import QdrantVectorProvider

    directory = request.getfixturevalue("tmp_path_factory").mktemp("week")

    graph = KuzuGraphProvider(str(directory / "graph"))
    graph.init_schema()
    vectors = QdrantVectorProvider(location=":memory:", vector_size=768)
    vectors.init_collection()

    ops_config = OperationalConfig(db_url=f"sqlite:///{directory / 'ops.db'}")
    engine = create_ops_engine(ops_config)
    upgrade_to_head(engine)
    ops = SQLAlchemyOperationalStore(ops_config, engine=engine)

    reports = simulate_days(
        CORPUS, graph=graph, vectors=vectors, ops=ops, config=AppConfig()
    )

    yield {"graph": graph, "vectors": vectors, "ops": ops, "reports": reports}

    graph.close()
    vectors.close()
    ops.close()


@pytest.fixture
def graph(week):
    return week["graph"]


@pytest.fixture
def ops(week):
    return week["ops"]


@pytest.fixture
def reports(week):
    return week["reports"]


@pytest.fixture
def reader(week):
    """The read API, pointed at the finished week."""
    from fastapi.testclient import TestClient

    from lumen.api.deps import get_graph, get_ops
    from lumen.api.main import create_app

    app = create_app()
    app.dependency_overrides[get_graph] = lambda: week["graph"]
    app.dependency_overrides[get_ops] = lambda: week["ops"]
    app.state.graph = week["graph"]
    app.state.ops = week["ops"]
    return TestClient(app, raise_server_exceptions=False)


def written_nodes(ops, reports) -> list[str]:
    """Every record the week put in the graph."""
    found: list[str] = []
    for report in reports:
        trace = ops.jobs.get_trace(report.trace_id)
        found.extend(
            write.node_id
            for write in trace.writes
            if write.target is WriteTarget.GRAPH_NODE and write.node_id
        )
    return found


class TestTheWeekRan:
    def test_every_day_finished(self, reports):
        failures = {
            report.session_id: [e.error for e in report.episodes if e.error]
            for report in reports
            if report.job_status != "COMPLETE"
        }

        assert failures == {}

    def test_every_day_wrote_something(self, reports):
        assert all(report.nodes_written > 0 for report in reports)

    def test_each_day_produced_the_episodes_it_should_have(self, reports):
        counted = [len(report.episodes) for report in reports]

        assert counted == [day.expects.episodes for day in CORPUS]


class TestNothingFragmented:
    """
    One theme across five days is one standing record.

    The failure this catches is the whole reason the goal exists, and it is
    invisible from inside a single entry: every day behaves impeccably and
    the week still ends with five unrelated records that should have been
    one.
    """

    def test_the_running_thread_is_a_single_pattern(self, reader):
        body = reader.get("/graph/nodes?types=PatternNode&active_only=false").json()

        assert [node["node_id"] for node in body["nodes"]] == [PATTERN_COMPARISON], (
            "the same theme across five days should be one pattern; found "
            + ", ".join(
                f"{n['node_id']} ({n['properties'].get('pattern_name')})"
                for n in body["nodes"]
            )
        )

    def test_the_unrelated_day_did_not_join_the_thread(self, reader):
        # Day five's second subject is about cooking. If it had been folded
        # into the comparison pattern, the evidence count would be right for
        # the wrong reason.
        body = reader.get(f"/graph/nodes/{PATTERN_COMPARISON}/neighbors?depth=1").json()
        reached = {node["node_id"] for node in body["nodes"]}

        assert "obs_2026_03_06_02_001" not in reached

    def test_only_one_belief_thread_exists(self, reader):
        # Two versions of one belief, not two separate beliefs.
        body = reader.get("/graph/nodes?types=BeliefNode&active_only=false").json()
        roots = {
            node["properties"].get("previous_version_id") or node["node_id"]
            for node in body["nodes"]
        }

        assert roots == {BELIEF_PACE}


class TestEvidenceAccumulated:
    def test_the_pattern_gathered_evidence_from_three_days(self, reader):
        body = reader.get(f"/graph/nodes/{PATTERN_COMPARISON}").json()

        assert body["properties"]["evidence_count"] == 3

    def test_it_was_reinforced_by_the_days_that_should_have(self, graph):
        history = graph.get_decision_history(PATTERN_COMPARISON)
        reinforcing = [
            row["node_id"] for row in history if row.get("action") == "REINFORCE"
        ]

        assert len(reinforcing) == 2

    def test_its_last_seen_date_moved_to_the_last_day_that_touched_it(self, reader):
        body = reader.get(f"/graph/nodes/{PATTERN_COMPARISON}").json()

        assert body["properties"]["last_reinforced_at"].startswith("2026-03-04")

    def test_the_days_that_should_not_have_added_evidence_did_not(self, graph):
        # Only the two reinforcing days moved it. If every day had, the count
        # would be five and would mean nothing.
        assert graph.get_node(PATTERN_COMPARISON)["evidence_count"] == 3


class TestVersionChainsJoinUp:
    def test_the_belief_has_a_history_of_two(self, reader):
        body = reader.get(f"/graph/nodes/{BELIEF_PACE}/versions").json()

        assert body["length"] == 2

    def test_the_versions_come_back_oldest_first(self, reader):
        body = reader.get(f"/graph/nodes/{BELIEF_PACE}/versions").json()

        assert [n["properties"]["version"] for n in body["versions"]] == [1, 2]

    def test_each_version_points_at_the_one_before_it(self, reader):
        body = reader.get(f"/graph/nodes/{BELIEF_PACE}/versions").json()
        versions = body["versions"]

        for earlier, later in zip(versions, versions[1:], strict=False):
            assert later["properties"]["previous_version_id"] == earlier["node_id"]

    def test_exactly_one_version_is_current(self, reader):
        body = reader.get(f"/graph/nodes/{BELIEF_PACE}/versions").json()
        live = [
            n for n in body["versions"] if n["properties"].get("status") == "ACTIVE"
        ]

        assert len(live) == 1
        assert live[0]["node_id"] == body["current_version_id"]

    def test_the_older_version_is_kept_rather_than_overwritten(self, reader):
        # The whole promise of an append-only history. What he believed on
        # Thursday is still readable on Friday.
        body = reader.get(f"/graph/nodes/{BELIEF_PACE}").json()

        assert body["properties"]["status"] == "SUPERSEDED"
        assert "pace, not ability" in body["properties"]["belief_statement"]

    def test_the_change_says_what_changed(self, reader):
        body = reader.get(f"/graph/nodes/{BELIEF_PACE}/versions").json()
        newest = body["versions"][-1]

        assert newest["properties"]["version_delta"]

    def test_the_change_is_linked_back_to_what_caused_it(self, graph):
        # A belief cannot change out of nowhere: something has to have
        # happened, or been reflected on, for it to change.
        chain = graph.get_version_chain(BELIEF_PACE)
        newest = chain[-1]["node_id"]

        anchors = [
            edge
            for edge in graph.get_neighborhood(newest, depth=1).edges
            if edge.edge_type.startswith("caused_by")
        ]
        assert anchors


class TestNothingIsOrphaned:
    def test_every_record_leads_back_to_the_run_that_made_it(self, reader, ops, reports):
        for node_id in written_nodes(ops, reports):
            response = reader.get(f"/debug/nodes/{node_id}/provenance")
            assert response.status_code == 200, f"{node_id} has no provenance"

    def test_every_record_names_the_entry_it_came_from(self, reader, ops, reports):
        for node_id in written_nodes(ops, reports):
            body = reader.get(f"/debug/nodes/{node_id}/provenance").json()
            assert body["episode_id"], f"{node_id} belongs to no entry"

    def test_every_finding_hangs_off_its_entry(self, reader, ops, reports):
        findings = [n for n in written_nodes(ops, reports) if n.startswith("obs_")]

        for node_id in findings:
            body = reader.get(f"/graph/nodes/{node_id}/neighbors?depth=1").json()
            containers = [
                edge for edge in body["edges"] if edge["edge_type"] == "contains_obs"
            ]
            assert containers, f"{node_id} is not attached to any entry"

    def test_every_decision_left_a_note(self, reader, reports):
        decided = sum(len(report.episodes) for report in reports)
        body = reader.get(
            "/graph/nodes?types=DecisionAuditNode&active_only=false&limit=200"
        ).json()

        assert body["count"] >= decided

    def test_everything_worth_searching_for_can_be_found(self, week):
        # A record in the graph with no search entry is invisible to every
        # future entry, which is fragmentation waiting to happen.
        assert all(report.unindexed_node_ids == [] for report in week["reports"])


class TestOrderingWithinADay:
    def test_the_two_subjects_of_one_day_are_chained(self, graph):
        second = "ep_2026_03_06_002"

        following = [
            edge
            for edge in graph.get_neighborhood(second, depth=1).edges
            if edge.edge_type == "follows_from"
        ]

        assert following, "the second subject of a day is not linked to the first"
        assert following[0].from_node_id == second
        assert following[0].to_node_id == "ep_2026_03_06_001"

    def test_the_first_subject_of_a_day_follows_nothing(self, graph):
        # Ordering is within a day. Across days the order is the date, and
        # inventing a link would claim a narrative thread that is not there.
        first = graph.get_neighborhood("ep_2026_03_06_001", depth=1)
        outgoing = [
            edge
            for edge in first.edges
            if edge.edge_type == "follows_from" and edge.from_node_id == "ep_2026_03_06_001"
        ]

        assert outgoing == []


class TestTheSameWeekTwice:
    def test_running_it_again_produces_the_same_graph(self, week, tmp_path):
        # A pipeline that quietly depends on timing or on what order things
        # happened in is one nobody can debug.
        from lumen.config import OperationalConfig
        from lumen.graph.kuzu_impl import KuzuGraphProvider
        from lumen.operational.engine import create_ops_engine
        from lumen.operational.migrator import upgrade_to_head
        from lumen.operational.sqlalchemy_impl import SQLAlchemyOperationalStore
        from lumen.vector.qdrant_impl import QdrantVectorProvider

        graph = KuzuGraphProvider(str(tmp_path / "again"))
        graph.init_schema()
        vectors = QdrantVectorProvider(location=":memory:", vector_size=768)
        vectors.init_collection()
        ops_config = OperationalConfig(db_url=f"sqlite:///{tmp_path / 'again.db'}")
        engine = create_ops_engine(ops_config)
        upgrade_to_head(engine)
        ops = SQLAlchemyOperationalStore(ops_config, engine=engine)

        try:
            simulate_days(
                CORPUS, graph=graph, vectors=vectors, ops=ops, config=AppConfig()
            )

            assert graph.count_by_type() == week["graph"].count_by_type()
            assert _ids(graph) == _ids(week["graph"])
        finally:
            graph.close()
            vectors.close()
            ops.close()


def _ids(graph) -> set[str]:
    """Every record in a graph, by name."""
    return {
        str(row["node_id"])
        for row in graph.find_nodes([], active_only=False, limit=500)
    }
