# Goal 2: Pydantic Schema Contracts

**Branch:** `goal2`
**Status:** 📋 Planned
**Depends on:** Goal 1 (Database Initialization Protocol) ✅
**Blocks:** Goals 4, 5, 6, 7 — everything downstream consumes these contracts.

---

## Objective

Turn the graph schema and the pipeline stage contracts from prose in `docs/` into
executable, self-validating Python types. After this goal, no stage of the pipeline
passes an untyped dict to another stage, and no invalid node can reach the graph.

---

# SECTION A — LOGIC (please verify)

*This is the part worth reading carefully. It describes what the schemas will mean,
not how they'll be coded.*

## A1. What Gets Built

| Deliverable | What it is |
|---|---|
| **15 node models** | One Pydantic model per node type in `docs/Graph/Schema.md` — Episode, Observation, Event, Session, CausalChain, CausalStep, Pattern, Belief, Lesson, AdoptedPrinciple, PersonEntity, DecisionAudit, Contradiction, MacroextractionReport, OpenLoop. |
| **20 edge models** | One per *logical* edge type in the docs (`contains`, `reinforces`, `evolved_from`, …), each carrying its own required payload. A resolver maps a logical edge to the right one of Kuzu's 43 physical tables. |
| **~25 enums** | Every closed vocabulary in the docs becomes a Python enum — observation types, signal strengths, reconciliation actions, lifecycle states, domains, and so on. An LLM cannot invent a category that isn't in the dictionary. |
| **9 pipeline DTOs** | The typed hand-off objects between stages, per `Technical_HLD.md` §5. |
| **ID helper** | One function that produces the semantic node IDs the docs use (`obs_2026_06_11_004`). |
| **Provider refactor** | `GraphProvider.write_node()` learns to accept a model, not just a dict. |

## A2. The Four Decisions You Made

1. **Timestamps are real dates.** Models use `datetime`/`date`, so a malformed date from
   an LLM fails at the boundary instead of rotting in the graph. They're converted to
   ISO strings only at the moment of the Kuzu write. Goal 19's temporal decay math then
   works without reparsing anything.
2. **`node_id` is required, and one helper builds it.** Models never invent their own ID.
   `make_node_id()` produces the human-readable format from the docs, so the graph stays
   debuggable by eye.
3. **Business rules live in the models.** "A suppressed-emotion observation must be
   HIGH or CRITICAL signal" and "an EVOLVE decision must carry a delta description" are
   enforced by the model itself — so they hold no matter who constructs it. Goal 7 then
   only has to build the *retry loop* around a validation that already exists.
4. **Unknown fields are rejected.** If the extraction LLM invents `emotional_intensity:
   0.8`, that's a loud error Goal 7's retry can act on — not data that silently vanishes.

## A3. The Rules the Models Will Enforce

These are the semantic guarantees, drawn from the docs. **This is the list most worth
your review** — each becomes a hard failure.

| # | Rule | Source |
|---|---|---|
| 1 | `SUPPRESSED_EMOTION_SURFACING`, `METACOGNITIVE_INTERRUPT`, `METACOGNITIVE_BREAKTHROUGH`, and `PROSODY_SIGNAL` observations must carry `HIGH` or `CRITICAL` signal strength. | Microextraction.md; Architecture.md §Validation |
| 2 | Every observation must have a `type` from the closed dictionary and a non-null `signal_strength`. | Architecture.md §Validation |
| 3 | Causal step types are limited to `TRIGGER \| INTERNAL_STATE \| ACTION \| OUTCOME \| LESSON`. | Microextraction.md |
| 4 | A Pattern/Belief at `version: 1` must have no `previous_version_id`; any version above 1 must have one. Versions are never zero or negative. | Schema.md §Temporal Model |
| 5 | A `DecisionAuditNode` with `action: EVOLVE` must carry a non-null `delta_description`. | Schema.md; Reconciliation.md |
| 6 | A decision sourced `STRUCTURAL` must name its anchor (`NAMED_PERSON` or `HISTORICAL_ERA`) and the anchor's value. | Schema.md §DecisionAuditNode |
| 7 | An `AMBIGUOUS` decision can never be `ACTIVE` — it must be `PENDING_HITL`. AMBIGUOUS never auto-executes. | Reconciliation.md |
| 8 | `provenance: CO_CREATED` defaults to `verification_status: UNVERIFIED`; `USER_GENERATED` defaults to `IMPLICIT`. (The 0.5× vs 1.0× trust weight depends on this.) | Architecture.md §Trust Weight |
| 9 | A belief flagged `is_contradicted` must name its `ContradictionNode`, and vice versa. | Schema.md §BeliefNode |
| 10 | A contradiction cannot link a belief to itself; once resolved, it must record when. | Schema.md §ContradictionNode |
| 11 | An episode's `episode_index` must fall within `total_episodes_in_entry`. | Schema.md §EpisodeNode |
| 12 | All confidence values sit in `0.0–1.0`. Counters (`evidence_count`, `mention_count`, `query_frequency`, `snooze_count`) are never negative. | Schema.md |
| 13 | An `AdoptedPrincipleNode`'s `lifecycle_history` must be non-empty and its last entry must agree with the current `lifecycle_state`. | Schema.md §AdoptedPrincipleNode |
| 14 | An edge's `invalidated_at`, when set, cannot precede its `valid_from`. Every reconciliation-produced edge must name its `decision_id`. | Schema.md §Edge Timestamps |
| 15 | A `dialectic` edge requires a `tension_summary`; a `regulates` edge requires a `regulation_summary`. | Schema.md §Edge Schemas |

**Deliberately *not* enforced here** (they need graph state, not just a single object,
so they belong to Goals 7/9): the bipartite causal-anchor rule (an EVOLVE must be
anchored to an Event or Session), per-action confidence thresholds, and the AMBIGUOUS
±0.05 tie detection.

## A4. Doc Changes This Goal Makes

**One doc edit, per your instruction.** `Microextraction.md`'s enum dictionary is missing
three types that `Architecture.md` already depends on by name. I'll add them to the
dictionary with these definitions — **please check the wording**:

- **`COGNITIVE_DISTORTION_STATE`** — A *sustained period* of operating under a distorted
  frame without catching it, described retrospectively. Distinct from
  `COGNITIVE_DISTORTION` (a single distortion caught and reality-checked) and
  `METACOGNITIVE_INTERRUPT` (caught live, mid-sentence).
- **`EXISTENTIAL_REFLECTION`** — Reflection on meaning, mortality, purpose, or one's own
  insignificance, engaged with as a question rather than as distress. Distinct from
  `EMOTION` and from `CORE_WOUND` (a biographical root cause).
- **`IDENTITY_FUSION_STATE`** — A state where self-worth is bound to an external object,
  role, outcome, or person such that losing it is experienced as losing the self (e.g.
  "if this project fails, I'm nothing"). Distinct from `IDENTITY_AFFINITY` (a preference)
  and `CORE_CONFLICT` (competing desires).

All three are flagged in `Architecture.md` as high-sensitivity nodes that Pass B
structural retrieval must surface, so they also get `signal_strength` floors of
`HIGH` alongside rule A3-1.

## A5. Two Discrepancies I Am *Not* Silently Fixing

1. **Routing tiers.** `Technical_HLD.md` §2.7 lists three tiers (`STANDARD` /
   `ELEVATED` / `CRITICAL`); `Schema.md` says the `DecisionAuditNode.routing_tier`
   field has exactly two values (`STANDARD` / `HIGH_SECURITY`) and states "there is no
   intermediate tier." I'll implement the two-value version, because that's the spec for
   *this specific field*. **Goal 4 will have to resolve which is right** for provider
   routing — flagging now, not deciding.
2. **The `domain` vocabulary.** `Schema.md` defines Pattern domains as
   `COGNITIVE_STYLE | EMOTIONAL | BEHAVIORAL | RELATIONAL | CAREER | HEALTH`, but its
   own `BeliefNode` example uses `SELF_CONCEPT`, which isn't in that list.
   `AdoptedPrincipleNode` has a separate, overlapping vocabulary
   (`PRODUCTIVITY | HEALTH | RELATIONAL | COGNITIVE | IDENTITY`). I'll implement one
   shared `Domain` enum that is the union — adding `SELF_CONCEPT` — plus the separate
   `PrincipleDomain` as documented. **Please confirm** you want `SELF_CONCEPT` added
   rather than the belief example corrected.

## A6. What This Goal Deliberately Leaves Undone

| Deferred | To |
|---|---|
| `trace_id` gets a field on every pipeline DTO but nothing populates it | Goal 3b |
| `MacroextractionReportNode.report_content` stays an untyped dict | Goal 17 |
| Per-action confidence thresholds; AMBIGUOUS tie detection | Goal 9 |
| The retry/re-extract loop that consumes these validation errors | Goal 7 |
| API request/response models (`schemas/api.py`) | Goal 20 |
| Multi-version embedding fields (`embedding_v1`/`v2`, `active_embedding_version`) | Backlog — not in Schema.md's node definitions yet |

## A7. Definition of Done

- Every one of the 15 node types can be instantiated, validated, and round-tripped
  through `GraphProvider.write_node()` into Kuzu and back.
- Every rule in A3 has a test proving it *rejects* the invalid case.
- Goal 1's 38 existing tests still pass untouched.
- ≥90% coverage on `lumen/schemas/`.

---

# SECTION B — LOW-LEVEL DESIGN

*Implementation detail. Skim or skip.*

## B1. Files

```
lumen/schemas/
├── __init__.py        # curated re-exports: from lumen.schemas import ObservationNode
├── enums.py           # ~25 StrEnum classes — single source of closed vocabularies
├── base.py            # LumenNode, TemporalNode, VersionedNode, to_graph_dict()
├── ids.py             # make_node_id(), NODE_ID_PREFIXES
├── nodes.py           # 15 node models
├── edges.py           # 20 logical edge models + resolve_edge_table()
└── pipeline.py        # 9 stage DTOs + supporting sub-models

lumen/tests/
├── test_schemas_enums.py     # enum ↔ doc parity
├── test_schemas_nodes.py     # per-node construction + all A3 rules
├── test_schemas_edges.py     # edge payloads + physical-table resolution
├── test_schemas_pipeline.py  # DTO construction + nesting
└── test_schemas_graph_integration.py  # model → Kuzu → dict round-trip
```

Modified: `lumen/graph/provider.py`, `lumen/graph/kuzu_impl.py`,
`docs/Extraction/Microextraction.md`, `implementation/Master_Plan.md`.

## B2. `enums.py`

All enums are `class X(StrEnum)` so a member serializes to its own string with no
custom encoder, and `X("HIGH")` parses raw LLM JSON directly.

**Cross-cutting:** `SignalStrength`, `Provenance`, `VerificationStatus`,
`ExtractionConfidence`, `Domain`, `RoutingTier`.

**Per-node status enums** (statuses are *not* interchangeable across node types —
one shared `Status` enum would let `IMMUTABLE` onto an observation):
`ObservationStatus`, `LifecycleNodeStatus` (Pattern/Belief: `ACTIVE|SUPERSEDED|SUPPRESSED`),
`DecisionStatus` (6 values), `ReportStatus`, `NodeStatus` (generic `ACTIVE|SUSPENDED`
for the rest).

**Domain-specific:** `ObservationType` (~48 members, from the updated Microextraction
dictionary), `CausalStepType`, `SourceModality`, `EntryClass`, `ReconciliationStatus`,
`ReconciliationAction`, `PrincipleDomain`, `LifecycleState`, `RelationshipToUser`,
`SentimentTrend`, `ContradictionResolutionStatus`, `LoopCategory`, `LoopResolutionStatus`,
`ReportType`, `CandidateRetrievalSource`, `StructuralAnchorType`, `HitlResolutionChoice`,
`QualityGateDecision`, `DialogueAct`.

Module-level constant `HIGH_SIGNAL_REQUIRED_TYPES: frozenset[ObservationType]` backs
rule A3-1 so the floor list is data, not a hardcoded `if`.

## B3. `base.py`

```python
class LumenNode(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        use_enum_values=False,
        validate_assignment=True,
        populate_by_name=True,      # accept alias OR canonical name
        str_strip_whitespace=True,
    )
    node_id: str = Field(min_length=1)
    created_at: datetime

    def to_graph_dict(self) -> dict[str, Any]: ...
```

Three-level hierarchy, because the docs' nodes genuinely differ:

- `LumenNode` — `node_id` + `created_at`. Everything.
- `TemporalNode(LumenNode)` — adds `valid_from`. Most nodes, but *not*
  `PersonEntityNode`, `CausalStepNode`, or `DecisionAuditNode`.
- `VersionedNode(TemporalNode)` — adds `version`, `previous_version_id`,
  `last_reinforced_at`, `evidence_count`, `query_frequency`, `signal_strength`,
  `provenance`, `verification_status`, plus rules A3-4 and A3-8. Only `PatternNode`
  and `BeliefNode`.

`to_graph_dict()` is the single serialization boundary — it is the *only* place that
knows Kuzu stores everything as STRING:

1. `model_dump(by_alias=False, mode="python")`
2. `datetime`/`date` → `.isoformat()`
3. `Enum` → `.value`
4. `list`/`dict` → `json.dumps(...)` (matching Kuzu's existing JSON-in-STRING columns)
5. `None` → omitted, so Kuzu applies its own null

## B4. `ids.py`

```python
NODE_ID_PREFIXES: dict[type[LumenNode], str]   # ObservationNode → "obs", PatternNode → "pat", …

def make_node_id(prefix: str, occurred_at: date, seq: int) -> str:
    """obs_2026_06_11_004 — prefix, ISO date with underscores, zero-padded seq."""

def make_slug_node_id(prefix: str, slug: str) -> str:
    """pat_decision_saturation — for stable semantic IDs (patterns, persons)."""
```

Both are pure functions with no I/O; sequence allocation is the orchestrator's problem
in Goal 10. A `SEMANTIC_ID_RE` regex is exported so tests and later goals can assert
format compliance.

## B5. `nodes.py` — Notable Field Mappings

Straightforward transcription of `Schema.md`, with these specifics:

- **Aliases** (per your decision): `signal_strength` carries
  `validation_alias=AliasChoices("signal_strength", "extraction_signal_strength")`;
  `person_refs: list[str]` carries `AliasChoices("person_refs", "person_ref")` with a
  `mode="before"` validator wrapping a bare string or `None` into a list. Canonical
  (graph) names are what serialize out.
- **Optional-vs-required** follows the docs literally: `historical_era`, `era_tag`,
  `open_loop_ref`, `branch_id`, `previous_version_id` etc. are `X | None = None`;
  everything shown with a concrete value in an example is required.
- **`rollback_pointer`** on `DecisionAuditNode` becomes a nested
  `RollbackPointer(edge_to_invalidate: str, nodes_to_requeue: list[str])`, JSON-encoded
  by `to_graph_dict()` into the existing STRING column.
- **`lifecycle_history`** becomes `list[LifecycleHistoryEntry(state, at, reason)]`.
- **`report_content`** stays `dict[str, Any]` (Goal 17).
- Validators from A3 attach as `@model_validator(mode="after")`, one per rule, each
  named for the rule it enforces (`_validate_evolve_requires_delta`) so a failure
  message points straight at the doc line.

## B6. `edges.py` — Logical Models + Physical Resolver

```python
class LumenEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    source_node_id: str
    target_node_id: str
    valid_from: datetime
    invalidated_at: datetime | None = None

class ReconciliationEdge(LumenEdge):      # edges born of a Stage-3 decision
    decision_id: str                       # required — rule A3-14
    confidence: float = Field(ge=0.0, le=1.0)

class DialecticEdge(ReconciliationEdge):
    tension_summary: str = Field(min_length=1)     # rule A3-15

class RegulatesEdge(ReconciliationEdge):
    regulation_summary: str = Field(min_length=1)  # rule A3-15
```

Structural edges (`contains`, `chain_contains`, `mentions`, `follows_from`,
`analyzed_in`, `alias_of`, `investigated_by`, `closes`, `evolved_from`,
`superseded_by`, `failed_extraction`) subclass `LumenEdge` directly — no `decision_id`,
because the docs mark them "written once; never invalidated."

The resolver closes the 20-logical → 43-physical gap without leaking Kuzu naming:

```python
_LOGICAL_TO_PHYSICAL: dict[tuple[LogicalEdgeType, str, str], str] = {
    (LogicalEdgeType.CONTAINS, "EpisodeNode", "ObservationNode"): "contains_obs",
    (LogicalEdgeType.CONTAINS, "EpisodeNode", "EventNode"):       "contains_evt",
    ...
}

def resolve_edge_table(logical: LogicalEdgeType, from_type: str, to_type: str) -> str:
    """Raise UnsupportedEdgeError naming the valid pairs if the combo is invalid."""
```

A test asserts the resolver's value-set is *exactly* the 43 names in `EDGE_REGISTRY` —
so the two can never silently drift apart.

## B7. `pipeline.py` — Stage DTOs

Base carries the observability hook:

```python
class PipelineDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trace_id: str | None = None   # populated in Goal 3b
```

The seven models named in `Technical_HLD.md` §5, plus four sub-models the HLD
references but never defines — reconstructed from `Preprocessing.md`:

| Model | Notes |
|---|---|
| `BufferMessage` | `message_id`, `role` (`USER`/`AI`), `content`, `timestamp`, `event_date`, `dialogue_act: DialogueAct \| None`, `co_created_marker: bool` |
| `SessionDecayEvent` | as HLD §5 |
| `ResolvedEntity` / `AmbiguousRef` / `CoreferenceMap` | exact shape of the JSON in Preprocessing.md §4 |
| `PreprocessedEpisode` | `episode_index`, `cleaned_text`, `entry_class`, `overarching_themes`, `historical_era`, `coherence_score` (0–1), `raw_text_hash` |
| `PreprocessingResult` | HLD §5, but `coreference_map: CoreferenceMap` instead of a bare `dict`, and `pending_reflections: list[str]` for the RAW_CAPTURE path |
| `ExtractionResult` | HLD §5, extended with `events`, `sessions`, `causal_chains` — Goal 6 extracts more than observations |
| `CandidateNode` | `node_id`, `node_type`, `content_preview`, `similarity_score \| None` (null for structural), `retrieval_source`, `structural_anchor_type/value` |
| `RetrievalResult` | HLD §5; validator caps the merged A∪B set at 8 per Architecture.md's merge rule |
| `ReconciliationResult` | HLD §5; reuses the same EVOLVE-needs-delta validator as `DecisionAuditNode` |

## B8. Provider Refactor

`provider.py` — widen the Protocol signature:

```python
def write_node(self, node_type: str, properties: LumenNode | dict[str, Any]) -> str: ...
```

`kuzu_impl.py` — three lines at the top of `write_node()`:

```python
if isinstance(properties, LumenNode):
    properties = properties.to_graph_dict()
```

Everything after is unchanged, so all 38 Goal 1 tests pass as-is. The existing
list/dict→JSON coercion in `write_node()` stays as a safety net for dict callers;
model callers arrive pre-serialized and pass through it untouched.

`kuzu_impl.py` importing `lumen.schemas` does not violate Rule 1 — Rule 1 forbids
*business logic importing vendor SDKs*, not the vendor adapter importing our own types.

## B9. Test Plan (~110 tests)

| File | ~n | Focus |
|---|---|---|
| `test_schemas_enums.py` | 12 | Every enum's members match the doc list exactly; `ObservationType` has all ~48; `StrEnum` string round-trip |
| `test_schemas_nodes.py` | 55 | One happy-path construction per node (15); one rejection test per A3 rule (15); alias acceptance; `extra="forbid"`; `to_graph_dict()` type coercion; datetime parsing from ISO strings |
| `test_schemas_edges.py` | 20 | Each edge's required payload; resolver hits all 43 tables; resolver rejects invalid pairs; **parity test vs `EDGE_REGISTRY`**; `invalidated_at` ordering |
| `test_schemas_pipeline.py` | 15 | Each DTO constructs and nests; candidate cap; trace_id defaults to None |
| `test_schemas_graph_integration.py` | 10 | Model → `write_node()` → Kuzu → `get_node()` round-trip for the 5 most complex nodes; list fields survive as JSON; enums land as strings |

Fixtures live in a new `lumen/tests/conftest.py` (`sample_observation`, `sample_pattern`,
…) so Goals 5–10 can build on valid instances rather than re-authoring them.

## B10. Build Order

1. Add the three enum types to `Microextraction.md` (§A4) — doc first, per project convention.
2. `enums.py` + its tests — nothing else compiles without it.
3. `base.py` + `ids.py` + tests for `to_graph_dict()`.
4. `nodes.py` in dependency order: leaves (CausalStep, PersonEntity) → containers (Episode, Observation) → versioned (Pattern, Belief) → audit (DecisionAudit, Contradiction).
5. `edges.py` + the `EDGE_REGISTRY` parity test.
6. `pipeline.py`.
7. Provider refactor + integration round-trip tests.
8. Coverage pass to ≥90%; update `Master_Plan.md` and write up results in this file.
