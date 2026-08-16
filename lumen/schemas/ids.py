"""
node_id generation.

Schema.md's node IDs follow a human-readable, semantic format
(obs_2026_06_11_004, pat_decision_saturation) rather than opaque UUIDs, so the
graph stays debuggable by eye. This module is the single place that knows
that format — models require node_id as a plain field and never generate
their own, keeping ID policy out of the schema layer (see
implementation/Goal_2_Plan.md Section A2).

Keyed by node_type string (matching lumen/graph/kuzu_impl.py NODE_TABLES)
rather than by model class, to avoid a circular import between this module
and nodes.py.

See: docs/Graph/Schema.md (node_id examples throughout)
"""

from __future__ import annotations

import re
from datetime import date

NODE_ID_PREFIXES: dict[str, str] = {
    "EpisodeNode": "ep",
    "ObservationNode": "obs",
    "EventNode": "evt",
    "SessionNode": "sess",
    "CausalChainNode": "chain",
    "CausalStepNode": "step",
    "PatternNode": "pat",
    "BeliefNode": "bel",
    "LessonNode": "les",
    "AdoptedPrincipleNode": "prin",
    "PersonEntityNode": "person",
    "DecisionAuditNode": "d",
    "ContradictionNode": "con",
    "MacroextractionReportNode": "macro",
    "OpenLoopNode": "loop",
}

# obs_2026_06_11_004 or pat_decision_saturation — lowercase prefix, then
# underscore-separated alphanumerics.
SEMANTIC_ID_RE = re.compile(r"^[a-z]+(_[a-zA-Z0-9]+)+$")


def make_node_id(prefix: str, occurred_at: date, seq: int) -> str:
    """
    Build a date-keyed semantic node_id, e.g. make_node_id("obs", d, 4)
    -> "obs_2026_06_11_004".

    Used for nodes anchored to a specific occurrence: Episode, Observation,
    Event, Session, CausalChain, DecisionAudit.
    """
    if not prefix:
        raise ValueError("prefix must not be empty")
    if seq < 0:
        raise ValueError(f"seq must be non-negative (got {seq})")
    return f"{prefix}_{occurred_at.strftime('%Y_%m_%d')}_{seq:03d}"


def make_scoped_node_id(
    prefix: str, occurred_at: date, episode_index: int, seq: int
) -> str:
    """
    Build a node_id that is unique within a day even when several episodes
    are processed separately, e.g.
    make_scoped_node_id("obs", d, 1, 3) -> "obs_2026_06_11_01_003".

    Each episode is extracted by its own independent call, and every one of
    them starts counting its nodes from 1. Without the episode number in
    the middle, the second episode of a day would mint ids the first one
    had already used.
    """
    if not prefix:
        raise ValueError("prefix must not be empty")
    if episode_index < 1:
        raise ValueError(f"episode_index must be at least 1 (got {episode_index})")
    if seq < 0:
        raise ValueError(f"seq must be non-negative (got {seq})")
    return (
        f"{prefix}_{occurred_at.strftime('%Y_%m_%d')}_{episode_index:02d}_{seq:03d}"
    )


def make_slug_node_id(prefix: str, slug: str) -> str:
    """
    Build a stable, content-derived semantic node_id, e.g.
    make_slug_node_id("pat", "Decision Saturation") -> "pat_decision_saturation".

    Used for nodes identified by a durable concept rather than a moment in
    time: PersonEntity, Pattern, Belief.
    """
    if not prefix:
        raise ValueError("prefix must not be empty")
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", slug.strip().lower()).strip("_")
    if not normalized:
        raise ValueError(f"slug produced an empty identifier (got {slug!r})")
    return f"{prefix}_{normalized}"


def person_node_id(name: str) -> str:
    """
    The identifier a person's record has, worked out from their name alone.

    Deterministic on purpose: it means asking whether someone is already
    known is a single lookup, with no search and no matching involved.

    It lives here, with the rest of the id policy, because two parts of the
    system now derive it — the pipeline when it records somebody named in an
    entry, and the live conversation when it checks whether a name just
    spoken belongs to anyone. Two copies of this rule that drifted apart
    would mean the second never finds what the first wrote.
    """
    return make_slug_node_id(NODE_ID_PREFIXES["PersonEntityNode"], name)


__all__ = [
    "NODE_ID_PREFIXES",
    "SEMANTIC_ID_RE",
    "make_node_id",
    "make_scoped_node_id",
    "make_slug_node_id",
    "person_node_id",
]
