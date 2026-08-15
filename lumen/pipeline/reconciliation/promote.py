"""
Turning a finding into something that lasts.

Most of what a day produces belongs to that day. Where someone was, what
they did, how tired they felt — all of it is worth recording and none of it
is worth keeping as a standing claim about who they are. A much smaller set
of findings are claims: this is how I work, this is what I believe, this is
the thing I keep doing. Only those become records that will still be
retrieved years from now.

The rule is a fixed table rather than a judgement made per entry, because a
model asked "is this worth keeping?" answers differently on different days,
and the shape of someone's history should not depend on that.

Everything built here inherits its weight, its origin and the people it
names from the finding it came from. Only the name, the wording and the area
of life come from the model — the parts that genuinely need writing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from lumen.pipeline.reconciliation.catalog import PromotionTarget, promotion_for
from lumen.pipeline.reconciliation.contracts import DecisionItem, NewNodeContent
from lumen.schemas.enums import (
    Domain,
    LoopCategory,
    Provenance,
    VerificationStatus,
)
from lumen.schemas.ids import make_slug_node_id
from lumen.schemas.nodes import BeliefNode, OpenLoopNode, PatternNode
from lumen.schemas.pipeline import PlannedNode

logger = logging.getLogger(__name__)

# How long a record's name may be before it stops being a name.
MAX_NAME_LENGTH = 80


def can_promote(item: DecisionItem) -> bool:
    """Whether this finding is the sort that can become a belief or a pattern."""
    return promotion_for(item.observation_type) in (
        PromotionTarget.BELIEF,
        PromotionTarget.PATTERN,
    )


def build_standing_node(
    item: DecisionItem,
    content: NewNodeContent | None,
    *,
    at: datetime,
    exists: Callable[[str], bool],
) -> PlannedNode | None:
    """
    Build the lasting record a finding has earned, if it has earned one.

    Returns nothing when the finding belongs to its day — which is the
    common case and not a failure. The finding is still saved and still
    linked to the entry it came from; it simply does not also become a
    standing claim.
    """
    target = promotion_for(item.observation_type)
    if target is PromotionTarget.BELIEF:
        return _build_belief(item, content, at=at, exists=exists)
    if target is PromotionTarget.PATTERN:
        return _build_pattern(item, content, at=at, exists=exists)
    return None


def build_open_loop(
    item: DecisionItem, *, at: datetime, exists: Callable[[str], bool]
) -> PlannedNode | None:
    """
    Promote an unresolved question that has come back.

    A question asked once is a passing thought. The same question surfacing
    again is an investigation the person is actually running, and that is
    worth following. Whether it has surfaced before is not something the
    entry can say — it is exactly what the search answered.
    """
    if promotion_for(item.observation_type) is not PromotionTarget.OPEN_LOOP:
        return None
    if not item.candidates:
        logger.debug("open question %s has not come up before; left as a note", item.node_id)
        return None

    node_id = _unique_id("loop", item.text, at=at, exists=exists)
    return PlannedNode(
        node_type="OpenLoopNode",
        node=OpenLoopNode(
            node_id=node_id,
            created_at=at,
            valid_from=at,
            loop_description=item.text,
            loop_category=LoopCategory.OTHER,
            provenance=item.provenance,
            source_episode_id=item.episode_id,
            last_referenced_at=at,
        ),
        searchable_text=item.text,
    )


def build_contradicting_belief(
    item: DecisionItem,
    content: NewNodeContent | None,
    *,
    contradiction_node_id: str,
    at: datetime,
    exists: Callable[[str], bool],
) -> PlannedNode:
    """
    Build the belief for the newer half of a contradiction.

    Written knowing about the clash from the moment it is created, since the
    contradiction record is minted first. The older belief is left exactly
    as it was: the clash is fully described by the contradiction record and
    the links either side of it, and rewriting a record the person's history
    already contains to say so would be an edit, not an addition.
    """
    planned = _build_belief(item, content, at=at, exists=exists)
    belief = planned.node
    return planned.model_copy(
        update={
            "node": belief.model_copy(
                update={
                    "is_contradicted": True,
                    "contradiction_node_id": contradiction_node_id,
                }
            )
        }
    )


def next_version(
    existing: dict,
    *,
    statement: str,
    delta: str,
    at: datetime,
    took_ownership: bool,
) -> PlannedNode:
    """
    Build the next version of a belief or pattern from the current one.

    Everything not being changed is carried across, so a version chain reads
    as one record growing rather than as a series of unrelated entries. The
    previous version stays exactly as it was written.

    When the person has taken over a framing the assistant first offered,
    the new version is recorded as theirs and treated as confirmed. Once
    somebody reworks an idea in their own words it is their idea, and
    ranking it below their others forever would be wrong.
    """
    kind = existing.get("_label")
    version = int(existing.get("version", 1)) + 1
    shared = {
        "node_id": _versioned_id(str(existing["node_id"]), version),
        "created_at": at,
        "valid_from": at,
        "last_reinforced_at": at,
        "version": version,
        "previous_version_id": str(existing["node_id"]),
        "evidence_count": int(existing.get("evidence_count", 0)),
        "signal_strength": existing.get("signal_strength", "STANDARD"),
        "provenance": (
            Provenance.USER_GENERATED
            if took_ownership
            else existing.get("provenance", Provenance.USER_GENERATED)
        ),
        "verification_status": (
            VerificationStatus.VERIFIED
            if took_ownership
            else existing.get("verification_status")
        ),
        "era_tag": existing.get("era_tag"),
    }

    if kind == "PatternNode":
        node = PatternNode(
            **shared,
            pattern_name=existing.get("pattern_name", statement[:MAX_NAME_LENGTH]),
            pattern_description=statement,
            domain=_read_domain(existing.get("domain")),
            archetype_tags=_read_list(existing.get("archetype_tags")),
        )
    else:
        node = BeliefNode(
            **shared,
            belief_statement=statement,
            belief_source_summary=existing.get("belief_source_summary", statement),
            domain=_read_domain(existing.get("domain")),
            version_delta=delta,
        )

    return PlannedNode(
        node_type=str(kind),
        node=node,
        searchable_text=statement,
    )


# ---------------------------------------------------------------------------
# Building each kind
# ---------------------------------------------------------------------------


def _build_belief(
    item: DecisionItem,
    content: NewNodeContent | None,
    *,
    at: datetime,
    exists: Callable[[str], bool],
) -> PlannedNode:
    """A new belief, worded by the model and weighted by the finding."""
    statement = _statement_of(content, item)
    node_id = _unique_id("bel", _name_of(content, statement), at=at, exists=exists)
    return PlannedNode(
        node_type="BeliefNode",
        node=BeliefNode(
            node_id=node_id,
            created_at=at,
            valid_from=at,
            last_reinforced_at=at,
            belief_statement=statement,
            belief_source_summary=item.text,
            domain=_read_domain(content.domain if content else None),
            signal_strength=item.signal_strength,
            provenance=item.provenance,
            evidence_count=1,
        ),
        searchable_text=statement,
    )


def _build_pattern(
    item: DecisionItem,
    content: NewNodeContent | None,
    *,
    at: datetime,
    exists: Callable[[str], bool],
) -> PlannedNode:
    """A new pattern, worded by the model and weighted by the finding."""
    statement = _statement_of(content, item)
    name = _name_of(content, statement)
    node_id = _unique_id("pat", name, at=at, exists=exists)
    return PlannedNode(
        node_type="PatternNode",
        node=PatternNode(
            node_id=node_id,
            created_at=at,
            valid_from=at,
            last_reinforced_at=at,
            pattern_name=name,
            pattern_description=statement,
            domain=_read_domain(content.domain if content else None),
            signal_strength=item.signal_strength,
            provenance=item.provenance,
            evidence_count=1,
        ),
        searchable_text=statement,
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _statement_of(content: NewNodeContent | None, item: DecisionItem) -> str:
    """
    The wording for a new record.

    Falls back to the finding's own words when the model offered none. A
    record worded slightly clumsily is worth far more than a decision
    thrown away for want of a sentence.
    """
    if content is not None and content.statement.strip():
        return content.statement.strip()
    return item.text


def _name_of(content: NewNodeContent | None, statement: str) -> str:
    """A short name for a record, taken from its wording if none was given."""
    if content is not None and content.name.strip():
        return content.name.strip()[:MAX_NAME_LENGTH]
    return statement[:MAX_NAME_LENGTH]


def _unique_id(
    prefix: str, name: str, *, at: datetime, exists: Callable[[str], bool]
) -> str:
    """
    Build an identifier from a record's name, keeping it readable.

    Names are used because a graph full of readable identifiers can be
    debugged by eye. Two records can genuinely deserve the same name
    though, and an identifier that is already taken would stop the whole
    entry from saving, so a date is added when that happens.
    """
    base = make_slug_node_id(prefix, name)
    if not exists(base):
        return base
    dated = f"{base}_{at.strftime('%Y_%m_%d')}"
    suffix = 2
    candidate = dated
    while exists(candidate):
        candidate = f"{dated}_{suffix}"
        suffix += 1
    return candidate


def _versioned_id(previous_id: str, version: int) -> str:
    """
    The identifier for the next version of a record.

    Built from the previous one so a version chain can be followed by
    reading the identifiers alone.
    """
    base = previous_id.rsplit("_v", 1)[0]
    return f"{base}_v{version}"


def _read_domain(raw) -> Domain:
    """
    Read which part of life a record belongs to.

    An unrecognised area becomes self-concept rather than failing the
    decision. Filing something under the wrong heading is a small cost;
    losing the record entirely is not.
    """
    try:
        return Domain(str(raw).strip().upper())
    except (ValueError, AttributeError):
        return Domain.SELF_CONCEPT


def _read_list(raw) -> list[str]:
    """Read a list that was stored as text, tolerating anything unexpected."""
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str) and raw.strip().startswith("["):
        import json

        try:
            parsed = json.loads(raw)
        except ValueError:
            return []
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    return []


__all__ = [
    "can_promote",
    "build_standing_node",
    "build_open_loop",
    "build_contradicting_belief",
    "next_version",
    "MAX_NAME_LENGTH",
]
