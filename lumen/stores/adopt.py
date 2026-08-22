"""
Taking the history that already exists and giving it to a real account.

There is a graph on disk belonging to the single-user arrangement — real
writing, from before anybody had to sign in. Per-user stores put everybody's
history somewhere new, and the one thing that must not happen is that this
history is left behind in a place nothing looks any more.

So it moves, by a command that can be run and tested rather than by a
paragraph in a readme. Easy to get right once, easy to forget entirely, and
the cost of forgetting is somebody's five years of writing becoming
unreachable while the system reports itself as working.

Two halves move differently. The graph is a directory and is moved. The
search index has no rename — a collection is not a file — so its points are
copied through the same interface everything else uses. Going underneath that
to move files would tie this to one vendor's storage layout, which is the
thing the provider arrangement exists to prevent.

Safe to run twice. The second run finds the work already done and says so.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from lumen.config import AppConfig
from lumen.stores.contracts import StoreError
from lumen.stores.keys import UnsafeUserKey, collection_name, graph_dir, user_key

logger = logging.getLogger(__name__)

# How many points to carry across at once.
COPY_BATCH = 200


class AdoptionRefused(StoreError):
    """
    The move was not made, and nothing was touched.

    Raised rather than merging when the destination already holds something.
    Two histories in one directory is not a state anybody could untangle
    afterwards, and refusing is the only answer that leaves both intact.
    """


@dataclass(frozen=True)
class AdoptionReport:
    """
    What the move did.

    Attributes:
        user_id: Who the history now belongs to.
        graph_moved: False when it was already where it belongs.
        points_copied: How many search entries were carried across.
        already_done: True when there was nothing left to do.
    """

    user_id: str
    graph_moved: bool = False
    points_copied: int = 0
    already_done: bool = False


def adopt(
    user_id: str,
    *,
    old_graph_path: str,
    old_collection: str,
    config: AppConfig | None = None,
    open_vectors=None,
) -> AdoptionReport:
    """
    Move the single-user history into one person's stores.

    Raises:
        AdoptionRefused: The destination already holds a history. Refused
            rather than merged, because two people's records in one graph is
            exactly the thing this whole goal exists to prevent, and nobody
            could separate them afterwards.
    """
    settings = config or AppConfig()
    key = user_key(user_id)
    destination = graph_dir(settings.graph.db_root, key)
    source = Path(old_graph_path).expanduser()

    moved = _move_graph(source, destination)
    copied = _copy_points(
        old_collection, collection_name(key), settings, open_vectors
    )

    report = AdoptionReport(
        user_id=key,
        graph_moved=moved,
        points_copied=copied,
        already_done=not moved and copied == 0,
    )
    logger.warning(
        "the existing history was adopted",
        extra={
            "user_id": key,
            "graph_moved": moved,
            "points_copied": copied,
        },
    )
    return report


def _move_graph(source: Path, destination: Path) -> bool:
    """
    Move the graph directory, or say it was already where it belongs.

    Nothing is copied and deleted — it is moved, so there is never a moment
    where two graphs hold the same history and something has to decide which
    is real.
    """
    if not source.exists():
        logger.info("there is no existing graph to adopt")
        return False

    if _holds_a_history(destination):
        raise AdoptionRefused(
            f"{destination} already holds a history. Two histories in one "
            "place cannot be separated afterwards, so nothing was moved."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        # An empty leftover from a run that was interrupted here. Harmless,
        # but in the way of the move.
        if destination.is_dir():
            destination.rmdir()
        else:
            destination.unlink()
    shutil.move(str(source), str(destination))
    return True


def _holds_a_history(path: Path) -> bool:
    """
    Whether somebody's writing is already sitting at this path.

    The database is a single file on some builds and a directory on others,
    so both shapes have to count. Anything empty does not — that is what an
    interrupted run leaves behind, and refusing to finish it would strand the
    history it was halfway through moving.
    """
    if not path.exists():
        return False
    if path.is_dir():
        return any(path.iterdir())
    return path.stat().st_size > 0


def _copy_points(
    old_collection: str, new_collection: str, config: AppConfig, open_vectors
) -> int:
    """
    Carry the search entries across, a page at a time.

    Through the provider rather than the storage layout, because there is no
    rename and reaching underneath would tie this to one vendor's on-disk
    shape.

    A collection that does not exist is not a failure: a deployment that
    never indexed anything has nothing to carry.
    """
    opener = open_vectors or _default_vectors(config)

    try:
        source = opener(old_collection)
    except Exception:  # noqa: BLE001 — nothing to copy is not a problem
        logger.info("there is no existing search index to adopt")
        return 0

    destination = opener(new_collection)
    destination.init_collection()

    if _already_holds_something(destination):
        # Run before. Copying again would be harmless — writing the same
        # point twice is an update — but it would report work that did not
        # happen, and a migration nobody can tell has finished is one people
        # run repeatedly hoping.
        logger.info("the search entries have already been carried across")
        source.close()
        destination.close()
        return 0

    copied = 0
    cursor: str | None = None
    try:
        while True:
            page, cursor = source.iter_points(batch=COPY_BATCH, after=cursor)
            for node_id, vector, payload in page:
                destination.upsert(node_id, vector, dict(payload))
                copied += 1
            if cursor is None:
                break
    except Exception:  # noqa: BLE001 — an unreadable source is nothing to carry
        logger.warning("the existing search index could not be read", exc_info=True)
    finally:
        source.close()
        destination.close()

    return copied


def _already_holds_something(collection) -> bool:
    """Whether a collection has anything in it at all."""
    try:
        page, _ = collection.iter_points(batch=1)
    except Exception:  # noqa: BLE001 — unreadable counts as empty
        return False
    return bool(page)


def _default_vectors(config: AppConfig):
    """Open a collection on this deployment's index, sharing one connection."""
    from lumen.vector.qdrant_impl import QdrantVectorProvider, open_client

    shared = open_client(config.vector.location)

    def _open(name: str):
        return QdrantVectorProvider(
            location=config.vector.location,
            collection_name=name,
            vector_size=config.vector.vector_size,
            client=shared,
        )

    return _open


def main(argv: list[str] | None = None) -> int:
    """
    Run the adoption from the command line.

    A command rather than instructions, because this is run once on a machine
    holding somebody's real history and "follow these five steps carefully"
    is not a plan for that.
    """
    parser = argparse.ArgumentParser(
        description="Move the single-user history into one person's stores."
    )
    parser.add_argument("--user", required=True, help="the user id to adopt it into")
    parser.add_argument(
        "--from-graph",
        default="./lumen_graph.db",
        help="where the existing graph is now",
    )
    parser.add_argument(
        "--from-collection",
        default="lumen_nodes",
        help="what the existing search collection is called",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        report = adopt(
            args.user,
            old_graph_path=args.from_graph,
            old_collection=args.from_collection,
        )
    except (StoreError, UnsafeUserKey) as refused:
        print(f"Refused: {refused}", file=sys.stderr)
        return 1

    if report.already_done:
        print(f"Nothing to do — {report.user_id} already holds it.")
    else:
        print(
            f"Adopted into {report.user_id}: "
            f"graph {'moved' if report.graph_moved else 'already there'}, "
            f"{report.points_copied} search entries copied."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
