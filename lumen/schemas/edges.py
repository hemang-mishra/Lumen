"""
Pydantic edge models for the Lumen knowledge graph, and the logical -> physical
edge-table resolver.

Kuzu requires typed edge tables (FROM TableA TO TableB), so
lumen/graph/kuzu_impl.py fans the 20 *logical* edge types documented in
docs/Graph/Schema.md out into 43 *physical* tables (EDGE_REGISTRY) — one per
valid (from_table, to_table) pair per logical type, e.g. `contains` becomes
`contains_obs` / `contains_evt` / `contains_sess` / `contains_chain`.

Business logic should never need to know Kuzu's physical naming (HLD Rule 1).
This module models the 20 logical edges from the docs and provides
resolve_edge_table() to translate (logical type, from node type, to node type)
into the correct physical table name, keeping that translation the provider
layer's problem to execute, not the caller's problem to know.

⚠️ KNOWN GAP (flagged, not silently fixed — see implementation/Goal_2_Plan.md):
lumen/graph/kuzu_impl.py generates every one of the 43 physical edge tables
with the SAME four columns (valid_from, invalidated_at, decision_id,
confidence). Schema.md's edge schema examples require `dialectic` edges to
carry `tension_summary` and `regulates` edges to carry `regulation_summary` —
neither column exists in the Kuzu DDL. DialecticEdge and RegulatesEdge below
are modeled faithfully per the docs; writing one through the current
GraphProvider.write_edge() will fail against Kuzu until the edge DDL is
extended. This is out of Goal 2's scope (which only touches write_node());
it must be resolved before Goal 9 (Reconciliation) writes real edges.

See: docs/Graph/Schema.md Section "Edge Types"
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lumen.schemas.base import model_to_graph_dict


class LogicalEdgeType(StrEnum):
    """The 20 logical edge types from Schema.md's Edge Types table."""

    CONTAINS = "contains"
    CHAIN_CONTAINS = "chain_contains"
    SAME_AS = "same_as"
    REINFORCES = "reinforces"
    EVOLVED_FROM = "evolved_from"
    CAUSED_BY = "caused_by"
    BRANCHES_TO = "branches_to"
    CONTRADICTS = "contradicts"
    DIALECTIC = "dialectic"
    REGULATES = "regulates"
    MENTIONS = "mentions"
    DECIDED_BY = "decided_by"
    ANALYZED_IN = "analyzed_in"
    ALIAS_OF = "alias_of"
    INVESTIGATED_BY = "investigated_by"
    CLOSES = "closes"
    FOLLOWS_FROM = "follows_from"
    ADOPTED_AS = "adopted_as"
    SUPERSEDED_BY = "superseded_by"
    FAILED_EXTRACTION = "failed_extraction"


# ---------------------------------------------------------------------------
# Edge models
# ---------------------------------------------------------------------------


class LumenEdge(BaseModel):
    """
    Base class for every logical edge. See Schema.md Edge Timestamps.

    Structural edges (contains, chain_contains, mentions, follows_from,
    analyzed_in, alias_of, investigated_by, closes, decided_by,
    superseded_by, failed_extraction) use this directly — the docs mark
    them "written once; never invalidated" / "No" reversibility, with no
    decision_id.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    source_node_id: str = Field(min_length=1)
    target_node_id: str = Field(min_length=1)
    valid_from: datetime
    invalidated_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_invalidation_order(self) -> "LumenEdge":
        """Rule A3-14: invalidated_at cannot precede valid_from."""
        if self.invalidated_at is not None and self.invalidated_at < self.valid_from:
            raise ValueError("invalidated_at cannot precede valid_from")
        return self

    def to_graph_dict(self) -> dict[str, Any]:
        """See lumen.schemas.base.model_to_graph_dict()."""
        return model_to_graph_dict(self)


class ReconciliationEdge(LumenEdge):
    """
    Base for edges produced by a Stage-3 Reconciliation decision — same_as,
    reinforces, caused_by, branches_to, contradicts, dialectic, regulates,
    adopted_as, and evolved_from (see EvolvedFromEdge). All carry a
    decision_id per Schema.md's Edge Timestamps table ("decision_id | All
    Reconciliation edges | Foreign key to the DecisionAuditNode...").
    """

    decision_id: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class EvolvedFromEdge(ReconciliationEdge):
    """
    EVOLVE result (new version -> old version). Append-only: unlike the other
    ReconciliationEdge subtypes it is not expected to be soft-deleted via
    invalidated_at in normal operation, but per Schema.md's Version Chain
    Example it still records the decision_id (and, consistent with every
    other reconciliation edge, a confidence) that produced it.
    """


class DialecticEdge(ReconciliationEdge):
    """
    DIALECTIC result. See Schema.md Edge Schemas #dialectic.

    ⚠️ tension_summary has no backing Kuzu column today — see module
    docstring "KNOWN GAP".
    """

    tension_summary: str = Field(min_length=1)


class RegulatesEdge(ReconciliationEdge):
    """
    REGULATE result. See Schema.md Edge Schemas #regulates.

    ⚠️ regulation_summary has no backing Kuzu column today — see module
    docstring "KNOWN GAP".
    """

    regulation_summary: str = Field(min_length=1)


__all__ = [
    "LogicalEdgeType",
    "LumenEdge",
    "ReconciliationEdge",
    "EvolvedFromEdge",
    "DialecticEdge",
    "RegulatesEdge",
    "resolve_edge_table",
    "UnsupportedEdgeError",
    "LOGICAL_TO_PHYSICAL",
]


# ---------------------------------------------------------------------------
# Logical -> physical resolver
# ---------------------------------------------------------------------------


class UnsupportedEdgeError(ValueError):
    """Raised when (logical_type, from_type, to_type) has no physical table."""


# Every triple is transcribed 1:1 from lumen/graph/kuzu_impl.py EDGE_REGISTRY.
# A test (test_schemas_edges.py) asserts this dict's value set is exactly
# EDGE_REGISTRY's physical names, so the two can never silently drift apart.
LOGICAL_TO_PHYSICAL: dict[tuple[LogicalEdgeType, str, str], str] = {
    (LogicalEdgeType.CONTAINS, "EpisodeNode", "ObservationNode"): "contains_obs",
    (LogicalEdgeType.CONTAINS, "EpisodeNode", "EventNode"): "contains_evt",
    (LogicalEdgeType.CONTAINS, "EpisodeNode", "SessionNode"): "contains_sess",
    (LogicalEdgeType.CONTAINS, "EpisodeNode", "CausalChainNode"): "contains_chain",
    (LogicalEdgeType.CHAIN_CONTAINS, "CausalChainNode", "CausalStepNode"): "chain_contains",
    (LogicalEdgeType.SAME_AS, "ObservationNode", "PatternNode"): "same_as_obs_pat",
    (LogicalEdgeType.SAME_AS, "PatternNode", "PatternNode"): "same_as_pat_pat",
    (LogicalEdgeType.REINFORCES, "ObservationNode", "PatternNode"): "reinforces_obs_pat",
    (LogicalEdgeType.REINFORCES, "ObservationNode", "BeliefNode"): "reinforces_obs_bel",
    (LogicalEdgeType.REINFORCES, "EventNode", "PatternNode"): "reinforces_evt_pat",
    (LogicalEdgeType.REINFORCES, "EventNode", "BeliefNode"): "reinforces_evt_bel",
    (LogicalEdgeType.EVOLVED_FROM, "PatternNode", "PatternNode"): "evolved_from_pat",
    (LogicalEdgeType.EVOLVED_FROM, "BeliefNode", "BeliefNode"): "evolved_from_bel",
    (LogicalEdgeType.CAUSED_BY, "PatternNode", "EventNode"): "caused_by_pat_evt",
    (LogicalEdgeType.CAUSED_BY, "BeliefNode", "EventNode"): "caused_by_bel_evt",
    (LogicalEdgeType.CAUSED_BY, "PatternNode", "SessionNode"): "caused_by_pat_sess",
    (LogicalEdgeType.CAUSED_BY, "BeliefNode", "SessionNode"): "caused_by_bel_sess",
    (LogicalEdgeType.BRANCHES_TO, "ObservationNode", "PatternNode"): "branches_to_obs",
    (LogicalEdgeType.BRANCHES_TO, "EventNode", "PatternNode"): "branches_to_evt",
    (LogicalEdgeType.BRANCHES_TO, "SessionNode", "PatternNode"): "branches_to_sess",
    (LogicalEdgeType.CONTRADICTS, "ContradictionNode", "BeliefNode"): "contradicts",
    (LogicalEdgeType.DIALECTIC, "BeliefNode", "BeliefNode"): "dialectic_bel_bel",
    (LogicalEdgeType.DIALECTIC, "PatternNode", "PatternNode"): "dialectic_pat_pat",
    (LogicalEdgeType.DIALECTIC, "BeliefNode", "PatternNode"): "dialectic_bel_pat",
    (LogicalEdgeType.DIALECTIC, "PatternNode", "BeliefNode"): "dialectic_pat_bel",
    (LogicalEdgeType.REGULATES, "SessionNode", "PatternNode"): "regulates_sess",
    (LogicalEdgeType.REGULATES, "ObservationNode", "PatternNode"): "regulates_obs",
    (LogicalEdgeType.MENTIONS, "ObservationNode", "PersonEntityNode"): "mentions_obs",
    (LogicalEdgeType.MENTIONS, "EventNode", "PersonEntityNode"): "mentions_evt",
    (LogicalEdgeType.MENTIONS, "SessionNode", "PersonEntityNode"): "mentions_sess",
    (LogicalEdgeType.DECIDED_BY, "ObservationNode", "DecisionAuditNode"): "decided_by_obs",
    (LogicalEdgeType.DECIDED_BY, "PatternNode", "DecisionAuditNode"): "decided_by_pat",
    (LogicalEdgeType.DECIDED_BY, "BeliefNode", "DecisionAuditNode"): "decided_by_bel",
    (LogicalEdgeType.DECIDED_BY, "EventNode", "DecisionAuditNode"): "decided_by_evt",
    (LogicalEdgeType.DECIDED_BY, "ContradictionNode", "DecisionAuditNode"): "decided_by_con",
    (LogicalEdgeType.ANALYZED_IN, "EpisodeNode", "MacroextractionReportNode"): "analyzed_in",
    (LogicalEdgeType.ALIAS_OF, "PersonEntityNode", "PersonEntityNode"): "alias_of",
    (LogicalEdgeType.INVESTIGATED_BY, "OpenLoopNode", "EpisodeNode"): "investigated_by",
    (LogicalEdgeType.CLOSES, "EpisodeNode", "OpenLoopNode"): "closes",
    (LogicalEdgeType.FOLLOWS_FROM, "EpisodeNode", "EpisodeNode"): "follows_from",
    (LogicalEdgeType.ADOPTED_AS, "ObservationNode", "AdoptedPrincipleNode"): "adopted_as_obs",
    (LogicalEdgeType.ADOPTED_AS, "SessionNode", "AdoptedPrincipleNode"): "adopted_as_sess",
    (LogicalEdgeType.SUPERSEDED_BY, "AdoptedPrincipleNode", "AdoptedPrincipleNode"): "superseded_by",
    (LogicalEdgeType.FAILED_EXTRACTION, "EpisodeNode", "ObservationNode"): "failed_extraction",
}


def resolve_edge_table(logical: LogicalEdgeType, from_type: str, to_type: str) -> str:
    """
    Resolve a (logical edge type, source node type, target node type) triple
    to its physical Kuzu table name.

    Raises UnsupportedEdgeError naming the valid (from_type, to_type) pairs
    for that logical type if the combination isn't registered.
    """
    key = (logical, from_type, to_type)
    physical = LOGICAL_TO_PHYSICAL.get(key)
    if physical is not None:
        return physical

    valid_pairs = [
        (f, t) for (lt, f, t) in LOGICAL_TO_PHYSICAL if lt == logical
    ]
    raise UnsupportedEdgeError(
        f"No physical edge table for logical type {logical!r} from "
        f"{from_type!r} to {to_type!r}. Valid (from, to) pairs for "
        f"{logical!r}: {valid_pairs}"
    )
