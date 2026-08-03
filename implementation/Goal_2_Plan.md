# Goal 2: Pydantic Schema Contracts

**Branch:** `goal2`
**Status:** ✅ Complete
**Tests:** 222 passing (38 Goal 1 + 184 new), 100% coverage on `lumen/schemas/` and `lumen/config.py`
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
| **20 edge models** | One per *logical* edge type in the docs (`contains`, `reinforces`, `evolved_from`, …), each carrying its own required payload. A resolver maps a logical edge to the right one of Kuzu's 44 physical tables (verified against EDGE_REGISTRY — the Goal 1 docs said 43, actual count is 44). |
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
| 8 | `provenance: USER_GENERATED` defaults `verification_status` to `IMPLICIT` (1.0× trust); `CO_CREATED` and `AI_GENERATED` both default to `UNVERIFIED` (0.5× trust) — Architecture.md's table only states the first two explicitly; `AI_GENERATED`'s default was undocumented and resolved per explicit user decision to match `CO_CREATED` rather than falling through to the trusted default. | Architecture.md §Trust Weight |
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

1. ~~**Routing tiers.**~~ **RESOLVED — redesigned, not merely flagged.** Originally:
   `Technical_HLD.md` §2.7 listed three tiers (`STANDARD`/`ELEVATED`/`CRITICAL`);
   `Schema.md` said `DecisionAuditNode.routing_tier` had exactly two values
   (`STANDARD`/`HIGH_SECURITY`, "there is no intermediate tier"). I initially
   implemented the two-value version and deferred the conflict to Goal 4. On review,
   the user clarified the *actual* intent was never a privacy/security tier at all —
   the abstraction should route purely on model capability (`LIGHTWEIGHT`/`THINKING`/
   `EMBEDDING`, later extended to include `TRANSCRIPTION`/`TTS`), through a single
   point of configuration, with no built-in assumption about deployment locality
   (local vs. cloud). This turned out to remove a much bigger, explicitly documented
   feature than a two-vs-three-tier naming question — see Section A5c.
2. **The `domain` vocabulary.** `Schema.md` defines Pattern domains as
   `COGNITIVE_STYLE | EMOTIONAL | BEHAVIORAL | RELATIONAL | CAREER | HEALTH`, but its
   own `BeliefNode` example uses `SELF_CONCEPT`, which isn't in that list.
   `AdoptedPrincipleNode` has a separate, overlapping vocabulary
   (`PRODUCTIVITY | HEALTH | RELATIONAL | COGNITIVE | IDENTITY`). I'll implement one
   shared `Domain` enum that is the union — adding `SELF_CONCEPT` — plus the separate
   `PrincipleDomain` as documented. **Confirmed:** `SELF_CONCEPT` added.
   **Follow-up, also confirmed:** on review, the resulting 7-value list still had real
   coverage gaps for "all aspects of life" — no domain for money beliefs, and nothing
   for the meaning/mortality/purpose territory that `EXISTENTIAL_REFLECTION` and
   `CORE_WOUND` observations describe (they'd otherwise get force-fit into
   `SELF_CONCEPT`, a category error). `FINANCIAL`, `SPIRITUALITY`, `RECREATIONAL`,
   and `ENVIRONMENTAL` were added on top; `Schema.md`'s PatternNode domain comment
   was updated to match, since this is an intentional spec expansion, not a
   discrepancy being worked around. `PrincipleDomain` and `LoopCategory` remain
   separate, still-overlapping vocabularies — unresolved, noted as a wrinkle, not
   fixed here.

## A5b. A Discrepancy Found During Implementation (please read)

While building `edges.py` I found that `lumen/graph/kuzu_impl.py`'s edge DDL gives
**every one of the 44 physical edge tables the same four columns**
(`valid_from`, `invalidated_at`, `decision_id`, `confidence`) — but `Schema.md`'s edge
schema examples require `dialectic` edges to also carry `tension_summary` and
`regulates` edges to carry `regulation_summary`. Neither column exists in the Kuzu
schema today.

I implemented `DialecticEdge` and `RegulatesEdge` faithfully per the docs (so the
Pydantic layer is correct), and flagged the gap in `edges.py`'s module docstring —
but I did **not** touch `kuzu_impl.py`'s DDL, since Goal 2's scope (per
`Master_Plan.md`) only covers refactoring `write_node()`, not `write_edge()` or the
edge table schema. This means **writing a real `dialectic` or `regulates` edge
through `GraphProvider.write_edge()` will fail today** — Kuzu will reject the extra
property. It doesn't block Goal 2 (no goal-2 test writes edges through Kuzu), but
it will block Goal 9 (Reconciliation) unless resolved first — either by extending
`EDGE_REGISTRY`'s DDL generation to support per-edge-type extra columns, or by a
narrower fix scoped to just these two edge types.

## A5c. LLM Routing Redesign (post-hoc, explicit user request)

After Goal 2 originally shipped, the user clarified that the `RoutingTier`
(`STANDARD`/`HIGH_SECURITY`) design was never what they wanted — not a naming
disagreement to resolve later, but the wrong model entirely. Investigating the actual
size of what was being changed surfaced a real, explicitly documented, marketed
feature: `HLDv2.md` stated *"Model selection is not a configuration preference — it
is enforced in code based on content type and action severity,"* with an
**episode-level cascading rule** — if any single observation in an episode was
`HIGH_SECURITY`, the entire episode's pipeline (extraction, reconciliation,
embeddings, macroextraction synthesis) was force-routed to a "High-Security
Provider." `LUMEN_CONTEXT.md` marketed this as a privacy guarantee.

Given the size, I confirmed scope explicitly before touching anything (twice — once
to check whether the privacy concept should survive at all in any form, once again
after showing the actual episode-cascade mechanism, since the first answer may not
have accounted for it). **Confirmed: remove the feature entirely.** Privacy is now
purely an operator/deployment choice — configure every model role to a local
provider if you want guaranteed-local processing — not a runtime, per-content
routing decision the pipeline makes.

**What changed:**

- `RoutingTier` (`STANDARD`/`HIGH_SECURITY`) → `ModelRole`
  (`LIGHTWEIGHT`/`THINKING`/`EMBEDDING`/`TRANSCRIPTION`/`TTS`) in
  `lumen/schemas/enums.py`. Describes *what kind* of model a task needs, never
  *where* it runs.
- `DecisionAuditNode.routing_tier` → `model_role: ModelRole`, with `kuzu_impl.py`'s
  DDL column renamed to match.
- New `ProviderConfig` in `lumen/config.py` — the single point of configuration the
  user asked for. Each of the five roles independently maps to a `(provider, model)`
  pair, env-var overridable, with a `.resolve(role)` accessor. No role's resolution
  depends on any other's; the abstraction never inspects content or enforces locality.
- Docs updated to match: `Schema.md` (§9 field + prose), `Reconciliation.md`
  (confidence-threshold table, two JSON examples, explanatory callout),
  `Preprocessing.md` (coherence-scoring call), `Technical_HLD.md` §2.7 (full tier
  table rewritten as a role table), `LLM_Abstraction_Architecture.md` (Section 1
  goals + Section 3 config/routing example), `HLDv2.md` (two glossary entries, the
  Macroextraction routing note, and the entire Cost & Model Routing section — the
  episode-cascade rule was deleted, not reworded), and `LUMEN_CONTEXT.md`'s privacy
  pitch.
- **A pre-existing doc disagreement surfaced and resolved while rewriting HLDv2.md's
  routing table:** `HLDv2.md` had grouped `MERGE` with the high-reasoning actions
  (`EVOLVE`/`CONTRADICT`/`DIALECTIC`), while `Reconciliation.md`'s own per-action
  table listed `MERGE` alongside the low-risk actions (`REINFORCE`/`BRANCH`/
  `REGULATE`). I treated `Reconciliation.md` as canonical — it's the detailed,
  single-purpose doc for this exact table — and made `HLDv2.md`'s summary match it
  (`MERGE` → `LIGHTWEIGHT`).
- `ROADMAP.md` and `calls.md` still reference the old tiers — left untouched since
  they read as informal notes, not part of CLAUDE.md's canonical spec list. Flagged,
  not fixed.

**Not touched:** `docs/hld/Interface_Architecture.md` and `docs/Query/*.md` were not
searched for tier references beyond the repo-wide grep already run; the grep found
nothing there, but worth a second look if Goal 13+ (Query layer) surfaces something.

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

The resolver closes the 20-logical → 44-physical gap without leaking Kuzu naming:

```python
_LOGICAL_TO_PHYSICAL: dict[tuple[LogicalEdgeType, str, str], str] = {
    (LogicalEdgeType.CONTAINS, "EpisodeNode", "ObservationNode"): "contains_obs",
    (LogicalEdgeType.CONTAINS, "EpisodeNode", "EventNode"):       "contains_evt",
    ...
}

def resolve_edge_table(logical: LogicalEdgeType, from_type: str, to_type: str) -> str:
    """Raise UnsupportedEdgeError naming the valid pairs if the combo is invalid."""
```

A test asserts the resolver's value-set is *exactly* the 44 names in `EDGE_REGISTRY` —
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
| `test_schemas_edges.py` | 20 | Each edge's required payload; resolver hits all 44 tables; resolver rejects invalid pairs; **parity test vs `EDGE_REGISTRY`**; `invalidated_at` ordering |
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

---

# SECTION C — RESULTS

## C1. What Was Built

Matches Section B1's file layout, plus `lumen/tests/conftest.py`, six new schema test
files (`test_schemas_base_and_ids.py` was added beyond the original plan to give
`base.py`/`ids.py` direct coverage), a `lumen/config.py` addition (`ProviderConfig`,
from the post-hoc LLM routing redesign in A5c), and its test file.

```
lumen/schemas/
├── __init__.py        # curated re-exports
├── enums.py            # ~30 StrEnum classes, incl. ModelRole (A5c redesign)
├── base.py             # GraphNode → LumenNode → TemporalNode → VersionedNode,
│                        # SignalProvenanceMixin, PersonRefsMixin, model_to_graph_dict()
├── ids.py               # make_node_id(), make_slug_node_id(), NODE_ID_PREFIXES
├── nodes.py             # 15 node models, one A3-rule validator per applicable model
└── edges.py + pipeline.py  # as planned

lumen/config.py           # + ProviderConfig: single point of configuration for the
                           # 5 ModelRole roles, added post-hoc per A5c

lumen/tests/
├── conftest.py                          # 15 sample_* fixtures, one per node type
├── test_schemas_enums.py                # enum parity + ModelRole tests
├── test_schemas_nodes.py                # per-node construction + all A3 rules
├── test_schemas_edges.py                # edge payloads + resolver parity
├── test_schemas_pipeline.py             # DTO construction + candidate cap
├── test_schemas_base_and_ids.py         # base hierarchy + id helpers (added beyond plan)
├── test_schemas_graph_integration.py    # model → Kuzu → dict round-trip, all 15 types
└── test_config.py                       # ProviderConfig defaults + role resolution (A5c)
```

**Result: 222 tests passing (38 Goal 1 + 184 new), 100% coverage on every module in
`lumen/schemas/` and on `lumen/config.py`, 98% on `lumen/graph/` (the 2 missed lines
are pre-existing Goal 1 code, an exception branch in `_get_existing_tables()`,
untouched by this goal).**

## C2. Deviations From the Plan

1. **Base hierarchy grew a fourth level.** The plan's B3 sketch had `LumenNode` as the
   root with `node_id` + `created_at`. Cross-checking `PersonEntityNode` against the
   actual Kuzu DDL in `kuzu_impl.py` (not just `Schema.md`'s prose example) showed it
   has neither `created_at` nor `valid_from` at all. Rather than force a field onto a
   table that doesn't have it, I split out a root `GraphNode` (just `node_id`), with
   `LumenNode` adding `created_at` on top. `PersonEntityNode` extends `GraphNode`
   directly. `MacroextractionReportNode` and `DecisionAuditNode` also turned out to
   lack `valid_from` (only `TemporalNode`'s subclasses get it) — noted in `base.py`'s
   docstring so the next person doesn't have to re-derive this from the DDL.
2. **Two DRY mixins added beyond the plan's three-class hierarchy:**
   `SignalProvenanceMixin` (signal_strength + provenance + verification_status +
   rule A3-8's default) and `PersonRefsMixin` (person_refs + alias handling), each
   used via multiple inheritance by exactly the node types that share the field
   cluster in the DDL (`ObservationNode`/`EventNode` for `PersonRefsMixin` —
   `SessionNode` was deliberately excluded once I confirmed it has no `person_refs`
   column, unlike what `Schema.md`'s prose vaguely implied).
3. **`EDGE_REGISTRY` has 44 physical tables, not 43.** Both `Goal_1_Plan.md` and this
   plan's own B6 stated 43 (copying the number from Goal 1's documentation without
   re-counting). I verified the actual list length programmatically — it's 44. Fixed
   in this doc; `Goal_1_Plan.md` still says 43 and should be corrected in a follow-up
   if anyone is relying on that count.
4. **A new discrepancy found and flagged, not fixed:** Kuzu's edge DDL gives every
   edge table the same four columns; `dialectic` and `regulates` edges need two more
   (`tension_summary`, `regulation_summary`) that don't exist anywhere in the schema.
   Documented in `edges.py`'s module docstring and Section A5b above — this is a real
   blocker for Goal 9, not addressed here since it's outside Goal 2's stated scope
   (`write_node()` only, not `write_edge()` or the edge DDL).
5. **Field-level provenance restriction found via example, not stated as a rule.**
   `Schema.md`'s `AdoptedPrincipleNode` example comments `provenance` as
   `USER_GENERATED | CO_CREATED` — narrower than the general 3-value `Provenance`
   enum. Implemented as a dedicated validator on `AdoptedPrincipleNode` rejecting
   `AI_GENERATED`, since the shared enum couldn't be narrowed without breaking every
   other node that uses it.
6. **Dead code removed, not tested around.** The first draft of `to_graph_dict()`'s
   coercion helpers handled a raw `BaseModel` appearing inside a list/dict — but
   `model_dump(mode="python")` already recursively flattens nested models into plain
   dicts before that code runs, making the branch unreachable. Verified this with a
   throwaway script, then deleted the dead branches rather than writing a test to
   force coverage of code that can never execute in practice.
7. **`AI_GENERATED` verification_status default bug caught by user review, fixed.**
   The first draft of `SignalProvenanceMixin._default_verification_status()` only
   branched on `CO_CREATED` vs. everything else, silently giving `AI_GENERATED`
   content the same `IMPLICIT` (1.0×) trust weight as content the user directly
   articulated — even though `Architecture.md`'s Trust Weight table never specifies
   a default for `AI_GENERATED` at all. Caught in review; fixed so only
   `USER_GENERATED` gets `IMPLICIT`, and both `CO_CREATED` and `AI_GENERATED`
   default to `UNVERIFIED` (see corrected rule A3-8 above).

## C3. Confirmed With the User Before Building

- `SELF_CONCEPT` added to the shared `Domain` enum (Section A5, option 2).
- The three new `ObservationType` members added to `Microextraction.md` with the
  definitions drafted in Section A4, verbatim.
- Explicit instruction to follow SOLID/composition-based design — reflected in the
  mixin-based field-cluster sharing (C2.2) rather than deeper single-inheritance
  chains or field duplication across node models.
- `AI_GENERATED` provenance defaults `verification_status` to `UNVERIFIED` (same
  floor as `CO_CREATED`), not `IMPLICIT` — caught in review as a real trust-weight
  bug, fixed per C2.7.
- `FINANCIAL`, `SPIRITUALITY`, `RECREATIONAL`, and `ENVIRONMENTAL` added to `Domain`
  to close life-coverage gaps identified in review (C2.2 follow-up); `Schema.md`'s
  PatternNode domain comment updated to match, since this was an intentional spec
  expansion rather than a documented-vs-implemented discrepancy.

## C4. What's Still Deferred

Unchanged from Section A6, plus the new edge-DDL gap from C2.4, which should be
resolved before Goal 9 (Reconciliation) attempts to write a `dialectic` or
`regulates` edge.

The LLM routing redesign (A5c) is a schema/config/doc change only — no goal 4 code
exists yet. Goal 4 still owns: `lumen/providers/llm_provider.py` (Protocol),
concrete Gemini/Ollama implementations, and the role-resolution factory that turns
`ProviderConfig.resolve(role)`'s `(provider, model)` string pair into an actual
provider instance. `ProviderConfig` itself and the `ModelRole` enum are done; nothing
reads them yet.
