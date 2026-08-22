"""
Tests for taking the history that already exists and giving it to an account.

There is a real graph on disk from before anybody had to sign in. Per-user
stores put everybody's history somewhere new, and the failure this ships to
prevent is that one being left behind in a place nothing looks any more —
while the system reports itself as working perfectly.

The two halves move differently and both are checked here: the graph is a
directory and is moved, and the search index has no rename so its points are
copied through the same interface everything else uses.
"""

from __future__ import annotations

import pytest

from lumen.config import AppConfig, GraphConfig, VectorConfig
from lumen.stores import StoreRegistry
from lumen.stores.adopt import AdoptionRefused, adopt, main
from lumen.stores.keys import graph_dir


@pytest.fixture
def settings(tmp_path):
    return AppConfig(
        graph=GraphConfig(db_root=str(tmp_path / "graphs")),
        vector=VectorConfig(location=str(tmp_path / "vectors"), vector_size=8),
    )


@pytest.fixture
def the_old_world(settings, tmp_path):
    """The single-user arrangement, with real writing in it."""
    from lumen.graph.kuzu_impl import KuzuGraphProvider
    from lumen.vector.qdrant_impl import QdrantVectorProvider, open_client

    old_path = str(tmp_path / "lumen_graph.db")
    graph = KuzuGraphProvider(old_path)
    graph.init_schema()
    graph.write_node(
        "LessonNode",
        {
            "node_id": "les_1",
            "created_at": "2026-01-01T00:00:00+00:00",
            "valid_from": "2026-01-01T00:00:00+00:00",
            "lesson_statement": "five years of writing",
            "domain": "EMOTIONAL",
            "signal_strength": "HIGH",
            "lesson_confidence": 0.9,
            "status": "ACTIVE",
        },
    )
    graph.close()

    client = open_client(settings.vector.location)
    index = QdrantVectorProvider(
        location=settings.vector.location,
        collection_name="lumen_nodes",
        vector_size=8,
        client=client,
    )
    index.init_collection()
    for number in range(3):
        index.upsert(f"les_{number}", [float(number)] + [0.0] * 7, {"node_type": "LessonNode"})
    client.close()

    return old_path


class TestTakingItOver:
    def test_the_graph_ends_up_under_the_account(self, settings, the_old_world):
        report = adopt(
            "usr_real",
            old_graph_path=the_old_world,
            old_collection="lumen_nodes",
            config=settings,
        )

        assert report.graph_moved is True
        assert graph_dir(settings.graph.db_root, "usr_real").exists()

    def test_the_writing_is_still_there_afterwards(self, settings, the_old_world):
        # The whole point. A migration that moved a directory and lost the
        # history would report success.
        adopt(
            "usr_real",
            old_graph_path=the_old_world,
            old_collection="lumen_nodes",
            config=settings,
        )

        registry = StoreRegistry(settings)
        try:
            with registry.lease("usr_real") as stores:
                assert stores.graph.count_by_type()["LessonNode"] == 1
        finally:
            registry.close()

    def test_the_search_entries_come_across(self, settings, the_old_world):
        # Copied rather than renamed, because a collection cannot be renamed
        # and going underneath to move files would tie this to one vendor.
        report = adopt(
            "usr_real",
            old_graph_path=the_old_world,
            old_collection="lumen_nodes",
            config=settings,
        )

        assert report.points_copied == 3

    def test_what_was_indexed_can_still_be_found(self, settings, the_old_world):
        adopt(
            "usr_real",
            old_graph_path=the_old_world,
            old_collection="lumen_nodes",
            config=settings,
        )

        registry = StoreRegistry(settings)
        try:
            with registry.lease("usr_real") as stores:
                found = stores.vectors.get_vectors(["les_0", "les_1", "les_2"])
                assert len(found) == 3
        finally:
            registry.close()

    def test_nothing_is_left_where_it_used_to_be(self, settings, the_old_world):
        from pathlib import Path

        adopt(
            "usr_real",
            old_graph_path=the_old_world,
            old_collection="lumen_nodes",
            config=settings,
        )

        assert not Path(the_old_world).exists()


class TestRunningItTwice:
    def test_the_second_run_changes_nothing(self, settings, the_old_world):
        adopt(
            "usr_real",
            old_graph_path=the_old_world,
            old_collection="lumen_nodes",
            config=settings,
        )

        again = adopt(
            "usr_real",
            old_graph_path=the_old_world,
            old_collection="lumen_nodes",
            config=settings,
        )

        assert again.already_done is True
        assert again.graph_moved is False
        assert again.points_copied == 0

    def test_the_history_survives_a_second_run(self, settings, the_old_world):
        for _ in range(2):
            adopt(
                "usr_real",
                old_graph_path=the_old_world,
                old_collection="lumen_nodes",
                config=settings,
            )

        registry = StoreRegistry(settings)
        try:
            with registry.lease("usr_real") as stores:
                assert stores.graph.count_by_type()["LessonNode"] == 1
        finally:
            registry.close()


class TestWhenItRefuses:
    def test_it_will_not_move_into_somewhere_already_occupied(
        self, settings, the_old_world
    ):
        # Two histories in one directory cannot be separated afterwards, so
        # refusing is the only answer that leaves both intact.
        destination = graph_dir(settings.graph.db_root, "usr_real")
        destination.mkdir(parents=True)
        (destination / "something.db").write_text("somebody else's history")

        with pytest.raises(AdoptionRefused, match="already holds"):
            adopt(
                "usr_real",
                old_graph_path=the_old_world,
                old_collection="lumen_nodes",
                config=settings,
            )

    def test_it_refuses_when_the_account_already_has_its_own_stores(
        self, settings, the_old_world
    ):
        # The shape this actually takes in production: the database is a
        # single file, not a folder. A check that only knew how to look
        # inside a folder crashed here rather than refusing.
        registry = StoreRegistry(settings)
        with registry.lease("usr_live"):
            pass
        registry.close()

        with pytest.raises(AdoptionRefused, match="already holds"):
            adopt(
                "usr_live",
                old_graph_path=the_old_world,
                old_collection="lumen_nodes",
                config=settings,
            )

    def test_nothing_is_moved_when_it_refuses(self, settings, the_old_world):
        from pathlib import Path

        destination = graph_dir(settings.graph.db_root, "usr_real")
        destination.mkdir(parents=True)
        (destination / "something.db").write_text("somebody else's history")

        with pytest.raises(AdoptionRefused):
            adopt(
                "usr_real",
                old_graph_path=the_old_world,
                old_collection="lumen_nodes",
                config=settings,
            )

        assert Path(the_old_world).exists()

    def test_an_unsafe_identifier_is_refused(self, settings, the_old_world):
        from lumen.stores.keys import UnsafeUserKey

        with pytest.raises(UnsafeUserKey):
            adopt(
                "../../etc",
                old_graph_path=the_old_world,
                old_collection="lumen_nodes",
                config=settings,
            )


class TestWhenThereIsNothingToTakeOver:
    def test_a_deployment_that_never_wrote_anything_is_fine(self, settings, tmp_path):
        report = adopt(
            "usr_new",
            old_graph_path=str(tmp_path / "nothing_here.db"),
            old_collection="lumen_nodes",
            config=settings,
        )

        assert report.graph_moved is False
        assert report.points_copied == 0


class TestTheCommand:
    """
    The thing an operator actually runs.

    Written as a command rather than a list of steps because it is run once,
    on a machine holding somebody's only copy of five years of writing. What
    matters here is that it says plainly what it did, and that a refusal
    leaves with a non-zero status instead of looking like success.
    """

    @pytest.fixture(autouse=True)
    def pointed_at_the_test_stores(self, settings, monkeypatch):
        """The command reads the environment, so the environment is the test."""
        monkeypatch.setenv("LUMEN_GRAPH_DB_ROOT", settings.graph.db_root)
        monkeypatch.setenv("LUMEN_VECTOR_LOCATION", settings.vector.location)
        monkeypatch.setenv("LUMEN_VECTOR_SIZE", "8")

    def _run(self, user, old_graph):
        return main(
            [
                "--user",
                user,
                "--from-graph",
                old_graph,
                "--from-collection",
                "lumen_nodes",
            ]
        )

    def test_it_moves_the_history_and_reports_what_it_did(
        self, settings, the_old_world, capsys
    ):
        assert self._run("usr_cli", the_old_world) == 0

        printed = capsys.readouterr().out
        assert "usr_cli" in printed
        assert "moved" in printed
        assert "3 search entries" in printed
        assert graph_dir(settings.graph.db_root, "usr_cli").exists()

    def test_running_it_again_says_there_is_nothing_to_do(
        self, the_old_world, capsys
    ):
        # An operator who cannot remember whether it already ran should be
        # able to find out by running it, not by guessing.
        self._run("usr_cli", the_old_world)
        capsys.readouterr()

        assert self._run("usr_cli", the_old_world) == 0
        assert "Nothing to do" in capsys.readouterr().out

    def test_a_refusal_leaves_with_a_failing_status(
        self, settings, the_old_world, capsys
    ):
        # A shell script that carries on after this would run the service
        # against stores that do not hold the history.
        registry = StoreRegistry(settings)
        with registry.lease("usr_taken"):
            pass
        registry.close()

        assert self._run("usr_taken", the_old_world) == 1
        assert "Refused" in capsys.readouterr().err

    def test_an_unsafe_identifier_does_not_reach_the_filesystem(
        self, the_old_world, capsys
    ):
        assert self._run("../escape", the_old_world) == 1
        assert "Refused" in capsys.readouterr().err
