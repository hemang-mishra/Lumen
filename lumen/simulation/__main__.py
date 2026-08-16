"""
Fill a graph with a written week, so there is something to look at.

Phase 3's whole point is being able to inspect a real graph by hand, and
until now there has been no way to get anything into one short of writing an
entry and waiting. This runs the corpus against the configured databases and
prints what it left behind.

    uv run python -m lumen.simulation

Afterwards the same graph can be browsed through the read API. It writes to
whatever `LUMEN_GRAPH_DB_PATH` and `LUMEN_OPS_DB_URL` point at, so pointing
those somewhere temporary is the difference between a scratch graph and
adding five imaginary days to a real history.
"""

from __future__ import annotations

import logging
import sys

from lumen.config import AppConfig
from lumen.graph.kuzu_impl import KuzuGraphProvider
from lumen.observability.logging import configure_logging
from lumen.operational.sqlalchemy_impl import build_operational_store
from lumen.simulation.corpus import CORPUS
from lumen.simulation.runner import simulate_days
from lumen.vector.qdrant_impl import QdrantVectorProvider

logger = logging.getLogger(__name__)


def main() -> int:
    """Run the corpus into the configured stores and report what it made."""
    settings = AppConfig()
    configure_logging(settings.observability)

    graph = KuzuGraphProvider(settings.graph.db_path)
    graph.init_schema()
    vectors = QdrantVectorProvider(
        location=settings.vector.location, vector_size=settings.vector.vector_size
    )
    vectors.init_collection()
    ops = build_operational_store(settings)
    ops.init_schema()

    try:
        reports = simulate_days(
            CORPUS, graph=graph, vectors=vectors, ops=ops, config=settings
        )
        _report(reports, graph)
    finally:
        graph.close()
        vectors.close()
        ops.close()

    return 0 if all(r.job_status == "COMPLETE" for r in reports) else 1


def _report(reports, graph) -> None:
    """Print what each day did and what the graph now holds."""
    print(f"\nRan {len(reports)} days into {graph.db_path}\n")
    for report in reports:
        print(
            f"  {report.session_id:<28} {report.job_status:<9} "
            f"{report.nodes_written:>3} records  "
            f"{report.vectors_written:>3} searchable"
        )

    print("\nThe graph now holds:")
    for kind, count in sorted(graph.count_by_type().items()):
        if count:
            print(f"  {count:>3}  {kind}")

    print("\nStanding records:")
    for row in graph.find_nodes(["PatternNode", "BeliefNode"], active_only=False):
        name = row.get("pattern_name") or row.get("belief_statement") or ""
        print(
            f"  {row['node_id']:<32} v{row.get('version', 1)} "
            f"{row.get('status', ''):<11} evidence={row.get('evidence_count', 0)}  {name}"
        )
    print()


if __name__ == "__main__":  # pragma: no cover - exercised as a command
    sys.exit(main())
