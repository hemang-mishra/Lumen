"""
Shared base classes and serialization for Lumen graph node and edge models.

Four-level node hierarchy, matching how docs/Graph/Schema.md and the Kuzu DDL
in lumen/graph/kuzu_impl.py actually group node fields (verified column by
column against NODE_TABLES, since to_graph_dict() output must match Kuzu's
physical schema exactly):

    GraphNode      — node_id only. Root of every node model.
    LumenNode      — adds created_at. Every node except PersonEntityNode,
                     which has neither created_at nor valid_from in the docs
                     or the Kuzu DDL.
    TemporalNode   — adds valid_from. Most nodes, but not PersonEntityNode,
                     CausalStepNode, DecisionAuditNode, or
                     MacroextractionReportNode (see Schema.md / kuzu_impl.py).
    VersionedNode  — adds version / previous_version_id / last_reinforced_at /
                     evidence_count / query_frequency. Only PatternNode and
                     BeliefNode.

SignalProvenanceMixin and PersonRefsMixin factor out field clusters shared
identically by multiple, otherwise-unrelated node types (composition instead
of forcing them into the linear hierarchy above).

See: docs/Graph/Schema.md, docs/Extraction/Architecture.md Section "Trust Weight"
"""

from __future__ import annotations

import json
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from lumen.schemas.enums import Provenance, SignalStrength, VerificationStatus


def _coerce_graph_value(value: Any) -> Any:
    """
    Convert a single Python value into a Kuzu-STRING-compatible primitive.

    Only called on the output of model_dump(mode="python"), which has
    already recursively flattened every nested BaseModel into a plain dict
    — so no branch here needs to handle a raw BaseModel instance.
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, dict)):
        return json.dumps(_jsonable(value))
    return value


def _jsonable(value: Any) -> Any:
    """Recursively convert enums/dates within a list or dict to JSON-safe values."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def model_to_graph_dict(model: BaseModel) -> dict[str, Any]:
    """
    Serialize any Lumen schema model into the flat, Kuzu-compatible dict
    format expected by GraphProvider.write_node() / write_edge().

    This is the single boundary that knows Kuzu stores everything as
    STRING: enums -> .value, datetimes/dates -> ISO strings, lists/dicts
    -> JSON strings, None fields omitted so Kuzu applies its own null.

    Shared by GraphNode.to_graph_dict() (nodes.py) and LumenEdge.to_graph_dict()
    (edges.py) so there is exactly one place that encodes this contract.
    """
    dumped = model.model_dump(mode="python", by_alias=False)
    result: dict[str, Any] = {}
    for key, value in dumped.items():
        if value is None:
            continue
        result[key] = _coerce_graph_value(value)
    return result


class GraphNode(BaseModel):
    """
    Root of every graph node model. Carries only node_id — the one field
    every node table has, including PersonEntityNode (which has neither
    created_at nor valid_from). See Schema.md Node Types.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    node_id: str = Field(min_length=1)

    def to_graph_dict(self) -> dict[str, Any]:
        """See model_to_graph_dict()."""
        return model_to_graph_dict(self)


class LumenNode(GraphNode):
    """Adds created_at. See Schema.md Node Timestamps."""

    created_at: datetime


class TemporalNode(LumenNode):
    """Adds valid_from. See Schema.md Node Timestamps."""

    valid_from: datetime


class VersionedNode(TemporalNode):
    """
    Adds the version-chain field cluster shared by PatternNode and
    BeliefNode. See Schema.md Version Chain Example.
    """

    version: int = Field(default=1, ge=1)
    previous_version_id: str | None = None
    last_reinforced_at: datetime
    evidence_count: int = Field(default=0, ge=0)
    query_frequency: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate_version_chain(self) -> "VersionedNode":
        """Rule: v1 has no previous_version_id; v>1 must have one."""
        if self.version == 1 and self.previous_version_id is not None:
            raise ValueError(
                "version 1 must not have a previous_version_id "
                f"(got {self.previous_version_id!r})"
            )
        if self.version > 1 and self.previous_version_id is None:
            raise ValueError(
                f"version {self.version} requires a previous_version_id"
            )
        return self


class SignalProvenanceMixin(BaseModel):
    """
    The (signal_strength, provenance, verification_status) cluster shared by
    ObservationNode, PatternNode, and BeliefNode, including the trust-weight
    default rule from Architecture.md:

        USER_GENERATED -> IMPLICIT (default)
        CO_CREATED     -> UNVERIFIED (default)
        AI_GENERATED   -> UNVERIFIED (default)

    Architecture.md's Trust Weight table only names defaults for
    USER_GENERATED and CO_CREATED; AI_GENERATED is left unspecified. Per
    explicit user decision (implementation/Goal_2_Plan.md), only
    USER_GENERATED gets the trusted IMPLICIT default — content the model
    generated without direct user confirmation (AI_GENERATED or CO_CREATED
    alike) defaults to the UNVERIFIED trust floor, since IMPLICIT is meant
    for content the user actually articulated themselves.

    verification_status is left as an optional field on input; the
    model_validator resolves it to a concrete value whenever the caller
    doesn't supply one explicitly.
    """

    signal_strength: SignalStrength = Field(
        validation_alias=AliasChoices("signal_strength", "extraction_signal_strength")
    )
    provenance: Provenance
    verification_status: VerificationStatus | None = None

    @model_validator(mode="after")
    def _default_verification_status(self) -> "SignalProvenanceMixin":
        if self.verification_status is None:
            self.verification_status = (
                VerificationStatus.IMPLICIT
                if self.provenance == Provenance.USER_GENERATED
                else VerificationStatus.UNVERIFIED
            )
        return self


class PersonRefsMixin(BaseModel):
    """
    person_refs: list[str], shared identically by ObservationNode and
    EventNode (per kuzu_impl.py NODE_TABLES — SessionNode does NOT carry
    this column, only participant_entities).

    Accepts the LLM's singular person_ref alias from Microextraction.md and
    normalizes None/str/list into a list.
    """

    person_refs: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("person_refs", "person_ref"),
    )

    @field_validator("person_refs", mode="before")
    @classmethod
    def _coerce_person_refs(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return v


__all__ = [
    "model_to_graph_dict",
    "GraphNode",
    "LumenNode",
    "TemporalNode",
    "VersionedNode",
    "SignalProvenanceMixin",
    "PersonRefsMixin",
]
