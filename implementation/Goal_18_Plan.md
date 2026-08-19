# Goal 18: The Review Queue — Answering the Questions Lumen Could Not

**Branch:** `goal18`
**Depends on:** Goal 9 (the decisions that escalate), Goal 10 (the escalation path and the
one write path), Goal 3 (the `hitl_queue` table and its repository)
**Spec:** `docs/Extraction/Reconciliation.md` §"HITL Review Queue" and
§"Tie-Breaking & The AMBIGUOUS Escalation", `docs/frontend/Requirements.md` S7,
`docs/Graph/Schema.md` (`DecisionAuditNode`)

---

# SECTION A — LOGIC (please verify)

## Objective

Lumen already knows when it cannot decide something. Since Goal 9 it recognises a tie
between two readings, and a reading it is not confident enough to act on. Since Goal 10 it
writes those undecided items into a queue instead of guessing.

**Nothing has ever taken anything out of that queue.** Items go in and stay there. The
decision they are waiting on is suspended — the graph records that Lumen hesitated, and
that is where it ends. There is no way to see the questions, no way to answer them, and no
way for an answer to reach the graph.

This goal is the other half. It makes the queue a queue: bounded, ordered, answerable, and
capable of turning a single tap into the graph write that was held back.

## A1. What Gets Built

| | What it is |
|---|---|
| **The frozen proposal** | A saved copy of exactly what Lumen was going to write, kept at the moment it decided to ask instead. Today that intention is thrown away and only a summary survives. |
| **A bounded queue** | The cap of 40 actually enforced. Items arriving at a full queue are *parked*, not decided, and are let in the moment room appears. |
| **The card** | One item, assembled for a person: what was extracted, what it was matched against, what Lumen proposed, how confident it was, and how old the question is. |
| **The answer** | Approve, Reject, or Snooze — and for a tie, take the first reading, take the second, or make it a new thing. An answer executes the suspended write for real. |
| **Housekeeping** | The two things that happen on a clock: a snoozed item auto-resolving after 7 days, and parked items being admitted. |
| **The counts** | How many are waiting, and how long the oldest has waited. |
| **A way to use it** | Five API routes and a plain inspection page. |

## A2. The Decisions Taken

**1. The proposal is frozen, not re-thought.** At the moment an item escalates, Lumen saves
the complete change it was about to make — both candidate readings for a tie, the new
wording for an evolved belief, the new record for a branch. Answering the card replays that
saved proposal. No model is called, a tap does not wait on the network, and what you
approve is exactly what you were shown.

**2. Snoozing hides an item for 24 hours.** The spec says a snoozed item "retains its
position in queue", which read literally means the card you just deferred is still the top
card. Snooze means *not now*. The item vanishes for a day, then returns in its normal
priority position. Recorded as a deliberate divergence from the spec's wording.

**3. Housekeeping runs whenever the queue is touched, and on demand.** Listing the queue or
answering a card first does its chores: auto-resolve anything overdue, admit anything
parked. There is also an explicit endpoint to force it, which is what Goal 20's scheduler
will call. No background timer is invented here, and the queue is self-correcting for
anybody who opens it.

**4. Three answers, and nothing is discarded.** Approve does the recommended thing. Reject
records the finding as its own separate thing (a BRANCH) rather than throwing it away. A tie
offers take-the-first, take-the-second, or make-it-new. There is no button that suppresses
what a person said — the graph is append-only, and a review queue that can delete is a
different and more dangerous feature.

**5. An answer leaves two notes, not one.** The original note is stamped resolved — when,
and with which choice. A second, fresh note records the action actually taken and carries
its own undo pointer. Read back later, the graph tells the true story: it hesitated, you
decided, this is what happened. Rewriting the first note in place would make it read as
though Lumen had been confident all along.

**6. Undo is deferred.** Every note this goal writes carries a correct undo pointer, so
nothing is lost. Building the reversal itself is broader than the queue — it applies equally
to the thousands of decisions Lumen makes without asking — and belongs in its own piece of
work.

**7. The screen is a plain inspection page.** Same as Goals 13b and 17. Enough to answer a
real card by hand and prove the thing works end to end. The mobile-first, one-tap surface is
Goal 29's, and the design system it should be built on does not exist until Goal 23.

## A3. Judgement Calls (flagging, not asking)

- **Extraction failures still cannot be queued.** The queue requires a decision note and a
  failed reading never produced one. The spec, `FR-S7-7`, and Goal 29 all already say this;
  this goal does not change it. Those items stay reachable from their episode.
- **Reject and auto-resolve are the same action.** Both come out as BRANCH — the spec says
  so for both. The difference is only who chose it, and that is recorded.
- **A tie's two readings share one piece of new wording.** When Lumen proposed two actions
  it produced only one draft of new content, so if both readings need new content they get
  the same draft. Faithful to what was actually proposed; noted because it looks like an
  oversight and is not.
- **A frozen proposal can go stale.** It was built against the graph as it stood that day.
  If the record it points at has since been superseded — by a later entry evolving the same
  belief — approving it blindly would attach today's answer to yesterday's version. On
  answering, Lumen re-checks that the target is still current; if it is not, Approve is
  refused with a plain explanation and Reject remains available. Answering is never silently
  wrong.
- **The cap counts everything unresolved**, including snoozed items resting out of sight.
  They are still questions the person owes an answer to.

## A4. How You'll Know It Works

1. Force a tie during a real run. The card appears at the top of the queue with both
   readings, both confidences, and the entry it came from.
2. Approve it. The graph gains the link that was suspended, the original note reads
   resolved with your choice, a new note records what was done, and the card leaves.
3. Reject a low-confidence card. The finding becomes its own record instead.
4. Snooze a card. It disappears, and is back tomorrow.
5. Push 41 items in. The 41st is parked, not guessed. Answer one; it is admitted.
6. Move a snoozed item's clock back 8 days and run housekeeping. It auto-branches and says
   so.

---

# SECTION B — LOW-LEVEL DESIGN

## B1. Module Layout

A package, not a route file. The Master Plan named `lumen/api/routes/hitl.py`; that stays
the web surface only, and every judgement lives behind it — the shape Goal 17 settled on.

```
lumen/review/
├── __init__.py        exports the service and the contracts
├── contracts.py       FrozenProposal, QueueCard, Resolution*, SweepReport
├── freeze.py          SettledDecision → FrozenProposal            (pure)
├── capacity.py        who gets in, who parks                      (pure)
├── cards.py           queue rows + graph reads → QueueCard        (read-only)
├── resolve.py         (item, proposal, choice) → GraphWritePlan    (pure)
├── housekeeping.py    auto-resolve and admission                  (orchestrating)
└── service.py         ReviewService — the narrow surface routes hold
```

`lumen/review/` rather than `lumen/pipeline/review/`: this is not a stage in the ingestion
pipeline. It runs on a person's schedule, days later, and reads the pipeline's leftovers.

## B2. Contracts (`contracts.py`)

```python
class ProposalVariant(BaseModel):
    """One thing Lumen was prepared to do, saved whole."""
    model_config = ConfigDict(extra="forbid")

    action: ReconciliationAction
    target_node_id: str | None = None
    target_type: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    fragment: PlanFragment              # the actual nodes/edges/bookkeeping
    primary_edge_handle: str | None = None
    delta_description: str | None = None
    summary: str = ""                   # one line, for the card


class FrozenProposal(BaseModel):
    """
    Everything needed to carry out a held-back decision later.

    Saved when the item escalates, replayed when the person answers. The
    fragment inside is the real write plan the pipeline would have run, so
    replaying it produces byte-identical records to the ones that would have
    landed that day.
    """
    model_config = ConfigDict(extra="forbid")

    audit_node_id: str
    entry_type: HitlEntryType
    source_node_id: str
    source_type: str
    source_text: str
    episode_id: str
    event_date: date
    episode_index: int
    frozen_at: datetime
    primary: ProposalVariant                    # "Action A" / the recommendation
    runner_up: ProposalVariant | None = None    # "Action B", ties only
    fallback: ProposalVariant                   # BRANCH — Reject and auto-resolve
```

`PlanFragment` already round-trips through Pydantic (`PlannedNode` holds a `GraphNode`
union, `PlannedEdge` a typed edge), so the whole thing serialises to JSON with
`model_dump_json()` and back with `model_validate_json()`. No bespoke encoder.

```python
class ResolutionChoice(StrEnum):
    """What the person tapped. Distinct from HitlResolutionChoice, which is
    what gets written to the graph — APPROVE and ACTION_A are the same tap on
    two different card layouts and must not be conflated in the API."""
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    SNOOZE = "SNOOZE"
    ACTION_A = "ACTION_A"
    ACTION_B = "ACTION_B"
    CREATE_NEW = "CREATE_NEW"


class QueueCard(BaseModel):
    item_id: str
    entry_type: HitlEntryType
    signal_strength: SignalStrength
    status: HitlItemStatus
    asked_at: datetime
    age_days: int
    snooze_count: int
    snoozed_until: datetime | None
    auto_resolves_at: datetime | None      # None unless snoozed at least once
    episode_id: str | None
    episode_summary: str | None            # ≤ 2 sentences, from EpisodeNode
    source_text: str
    recommended_action: ReconciliationAction | None
    options: list[CardOption]              # what this layout offers
    stale: bool                            # target superseded since freezing
    stale_reason: str | None


class CardOption(BaseModel):
    choice: ResolutionChoice
    label: str
    action: ReconciliationAction
    target: CandidatePreview | None        # node_id, type, own words, dates
    confidence: float | None
    difference: str | None                 # the specific delta, ties only


class ResolutionOutcome(BaseModel):
    item_id: str
    choice: ResolutionChoice
    recorded_choice: HitlResolutionChoice
    action_taken: ReconciliationAction
    new_audit_node_id: str
    nodes_written: list[str]
    edges_written: list[tuple[str, str, str]]
    vectors_written: list[str]
    admitted: list[str]                    # parked items let in as a result


class SweepReport(BaseModel):
    auto_resolved: list[str]
    admitted: list[str]
    ran_at: datetime
    still_pending: int
    oldest_pending_at: datetime | None
```

## B3. Freezing (`freeze.py`)

One pure function, called from Stage 3.

```python
def freeze(
    decision: SettledDecision,
    context: PlanContext,
    *,
    audit_id: str,
    sequence: int,
) -> FrozenProposal
```

It builds up to three variants by calling the *existing* `plan._BUILDERS` — the same code
that would have run had the decision not been refused:

| Variant | Built from | Used by |
|---|---|---|
| `primary` | `decision.action` | Approve, Action A |
| `runner_up` | `decision.runner_up_action` against `runner_up_target_node_id` | Action B (ties only) |
| `fallback` | `ReconciliationAction.BRANCH` | Reject, Create New, auto-resolve |

`runner_up` is built by copying the decision with `action`/`target_node_id`/`confidence`
swapped for the runner-up's, then handing it to the same builder table. Where primary and
fallback would produce the same fragment (the recommendation *was* BRANCH), `fallback` is
still stored — duplicated bytes are cheaper than a special case at resolve time.

**Node ids do not collide across variants.** `promote` derives ids from content
(`bel_<slug>`, `pat_<slug>`) and versions as `<id>_v2`, so two variants proposing the same
record propose the same id, which is correct. The `exists` callable in `PlanContext` is
consulted at freeze time; the staleness check in B6 is what covers the gap.

### Changes this forces on Goal 9

- **`SettledDecision.runner_up_target_node_id: str | None`** — the runner-up's target is
  currently dropped when `ItemDecision` is flattened, which makes "take the second reading"
  unanswerable. Populated in `decide.py` from `runner_up.target_node_id`. *Amends Goal 9.*
- **`plan.plan_for` returns the frozen proposal too** for refused decisions:
  `tuple[PlanFragment, DecisionAuditNode, FrozenProposal | None]`. The fragment it
  contributes to the live write plan is unchanged — still empty for a refusal. The proposal
  is a by-product, not a change in what gets written.
- **`HitlEscalation.proposal: FrozenProposal`** in `reconciliation/contracts.py`, carried
  through `ReconciliationOutcome.escalations` exactly as the summary already is.

`freeze.py` importing from `lumen.pipeline.reconciliation.plan` is the one direction of
coupling accepted here: the alternative is duplicating eight builders, and two copies of the
rule for what EVOLVE writes is a guaranteed future divergence.

## B4. Capacity (`capacity.py`)

Pure arithmetic, no I/O, so the cap is testable without a database.

```python
def admissions(*, pending: int, cap: int, parked: Sequence[HitlQueueItemRecord]) -> list[str]:
    """Which parked items fit, in priority order. Empty when full."""

def entry_status(*, pending: int, cap: int) -> HitlItemStatus:
    """PENDING_HITL while there is room, SUSPENDED_QUEUE_FULL once there is not."""
```

`pending` counts every unresolved item including snoozed ones. Parked items are ordered by
`(priority_rank, signal_rank, created_at)` — the same ordering the queue itself uses, so a
critical tie parked behind twenty routine items is admitted first.

## B5. Building Cards (`cards.py`)

```python
def build_card(
    item: HitlQueueItemRecord,
    proposal: FrozenProposal,
    *,
    graph: ReadOnlyGraph,
    now: datetime,
    auto_resolve_days: int,
) -> QueueCard
```

Read-only. Per card it does at most three graph reads, batched: the source node, the
candidate targets (`get_nodes_by_ids`), and the episode. Listing pulls the episode nodes for
the whole page in one call rather than one per card.

Layout comes from `entry_type`:

- `AMBIGUOUS_TIE` → three options: `ACTION_A` (primary), `ACTION_B` (runner-up),
  `CREATE_NEW` (fallback). `difference` on each is the variant's `delta_description` or
  `summary`.
- `BELOW_THRESHOLD` → two options: `APPROVE` (primary), `REJECT` (fallback). Plus snooze,
  which is not an option on the card because it applies to every layout.

`stale` is computed here so the card can grey out Approve before a person taps it, using the
same check `resolve` enforces (B6).

## B6. Answering (`resolve.py`)

Pure: takes the item, its proposal and a choice, returns a plan. It writes nothing.

```python
def plan_resolution(
    item: HitlQueueItemRecord,
    proposal: FrozenProposal,
    choice: ResolutionChoice,
    *,
    at: datetime,
    current: Mapping[str, dict],        # target rows as they stand now
) -> ResolutionPlan
```

```python
class ResolutionPlan(BaseModel):
    write_plan: GraphWritePlan
    new_audit: DecisionAuditNode
    action_taken: ReconciliationAction
    recorded_choice: HitlResolutionChoice
```

Steps:

1. **Pick the variant.** `APPROVE`/`ACTION_A` → primary; `ACTION_B` → runner-up (a refusal
   if the item has none); `REJECT`/`CREATE_NEW` → fallback.
2. **Staleness check.** For a variant with a `target_node_id`, the row in `current` must
   exist and must not be superseded (`superseded_at is None` / `is_current`). If it is,
   raise `StaleProposalError` naming the node and the version that replaced it. Fallback
   variants have no target and are therefore always answerable — which is why Reject stays
   available on a stale card.
3. **Mint the second note.** A `DecisionAuditNode` for the action actually taken:
   `action=variant.action`, `hitl_resolved=True`,
   `hitl_resolution_timestamp=at`, `hitl_resolution_user_choice=recorded_choice`,
   `confidence=variant.confidence`, `source_node_id=proposal.source_node_id`, and a
   rollback pointer over the edge this resolution creates. Its id is
   `f"{proposal.audit_node_id}_r"` — deterministic, so answering twice cannot mint two, and
   readable as "the resolution of that decision".
4. **Re-stamp the decision edges.** The variant's fragment carries edges whose
   `decision_id` is the *original* note. They are rewritten to point at the new note, so
   provenance leads to the decision that was actually acted on.
5. **Stamp the original note.** A `PlannedBookkeeping` op (B10) marking it resolved.
6. **Link the two notes.** A `supersedes` edge, `DecisionAuditNode → DecisionAuditNode`, so
   a reader arriving at either one finds the other. Needs an `EDGE_REGISTRY` entry.
7. **Assemble.** `GraphWritePlan(nodes=[*fragment.nodes, new_audit], edges=…,
   bookkeeping=[*fragment.bookkeeping, stamp], existing_node_ids=…)`. `existing_node_ids`
   must include the source node, the targets, and the original audit node — all written long
   ago, and the plan's validator will reject the edges otherwise.

**A resolution can legitimately write nothing.** A BRANCH whose finding does not warrant a
standing record produces an empty fragment (`promote.build_standing_node` returning `None`).
The resolution still writes its note and still leaves the queue. "Answered, and the answer
was that nothing further is needed" is a real outcome and must not read as a failure.

## B7. Housekeeping (`housekeeping.py`)

```python
def sweep(*, ops, graph, vectors, config, now) -> SweepReport
```

Two phases, in order, because the first frees room for the second:

1. **Auto-resolve.** `ops.hitl.find_auto_resolvable(now - timedelta(days=7))` returns
   unresolved items with `snooze_count >= 1` and `last_snoozed_at` older than the cutoff.
   Each is resolved through the same path a person's tap uses, with
   `choice=REJECT` and `recorded_choice=AUTO_BRANCH_AFTER_SNOOZE`. One failure is logged and
   skipped, never allowed to abort the sweep — one unanswerable item must not freeze
   everyone else's queue.
2. **Admission.** `capacity.admissions(...)` over the parked items, then
   `ops.hitl.admit(item_id)` for each, which flips `SUSPENDED_QUEUE_FULL → PENDING_HITL`.

Idempotent by construction: both phases select on state, so a second run in the same second
finds nothing to do. Every sweep logs what it did at `INFO` — a system that resolves things
on a person's behalf must be able to say what it resolved and why.

## B8. The Service (`service.py`)

```python
class ReviewService:
    def __init__(self, *, config, graph: GraphProvider, ops, vectors) -> None: ...

    def list_queue(self, user_id: str, *, limit: int = 20) -> QueueView: ...
    def get_card(self, user_id: str, item_id: str) -> QueueCard: ...
    def resolve(self, user_id: str, item_id: str, choice: ResolutionChoice) -> ResolutionOutcome: ...
    def snooze(self, user_id: str, item_id: str) -> QueueCard: ...
    def sweep(self, user_id: str) -> SweepReport: ...
    def counts(self, user_id: str) -> QueueCounts: ...
```

The same reasoning as `MacroextractionService`: a route holding one of these can answer a
card and nothing else. It cannot reach the graph.

- `list_queue` and `resolve` sweep first (decision A2-3). `counts` does not — a badge count
  is polled from every screen and must not carry write side effects.
- `resolve` holds a lock (`threading.Lock`) for the read-modify-write, because two taps on
  one card would each see it pending and both execute the write.
- Writing goes through `orchestration.commit.commit(plan, entries, graph=…, vectors=…)`
  with entries from `orchestration.embed.prepare_index(plan)`. No second write path, and new
  records become searchable for free.
- `IndexWriteFailed` is caught: the graph is right, the item is resolved, and the outcome
  reports the unindexed ids for `repair_index` — the behaviour Goal 10 already settled.
- Ownership is checked on every call. `item.user_id != user_id` is a `NotFound`, not a
  `Forbidden` — a wrong-user id must not confirm that the item exists.

## B9. Store Changes

**Migration `0006_review_queue`:**

- `hitl_queue.snoozed_until TIMESTAMP NULL` — what makes snooze hide something.
- `hitl_queue.resolved_action VARCHAR(32) NULL` — what was actually done, so the queue can
  be read without a graph round-trip.
- New table `hitl_proposals`:
  | column | |
  |---|---|
  | `audit_node_id` | PK, FK → `hitl_queue.audit_node_id`, `ON DELETE CASCADE` |
  | `payload` | `Text`, the `FrozenProposal` as JSON |
  | `schema_version` | `Integer`, so a future contract change can be migrated rather than guessed at |
  | `created_at` | `DateTime(timezone=True)` |

Keyed on the audit node, not the item id: the audit node is already the unique link between
the two stores, and the queue row is the thing with mechanics attached.

**`HitlQueueRepository` additions:**

```python
def save_proposal(self, audit_node_id: str, proposal_json: str) -> None: ...
def get_proposal(self, audit_node_id: str) -> str | None: ...
def list_visible(self, user_id, *, now, limit=20) -> list[HitlQueueItemRecord]: ...
def list_parked(self, user_id) -> list[HitlQueueItemRecord]: ...
def find_auto_resolvable(self, user_id, *, cutoff) -> list[HitlQueueItemRecord]: ...
def admit(self, item_id) -> HitlQueueItemRecord: ...
def snooze(self, item_id, *, until, at) -> HitlQueueItemRecord: ...
```

`list_visible` is `list_pending` plus `snoozed_until IS NULL OR snoozed_until <= now`,
ordered by `(priority_rank, signal_rank, created_at)`. `list_pending` stays as it is —
`oldest_pending_at` and Goal 17's shadow scan both depend on counting everything unresolved,
snoozed or not.

`snooze` increments `snooze_count`, sets `last_snoozed_at = at` and
`snoozed_until = at + hitl_snooze_hours`. `update_status` gains `resolved_action`.

**Store must reject a bad transition.** Resolving an already-resolved item raises
`IllegalStateTransitionError`, which the class already exists for.

## B10. Graph Provider Change

Stamping the original note is a change to an existing record, which means bookkeeping — the
only sanctioned way to touch something already written.

- `BookkeepingOperation.MARK_HITL_RESOLVED` in the enum.
- `PlannedBookkeeping` gains `choice: HitlResolutionChoice | None` and
  `resolved_action: ReconciliationAction | None`, both `None` for every existing operation.
- `GraphProvider.resolve_decision(node_id, *, choice, action, at)` — sets
  `hitl_resolved`, `hitl_resolution_timestamp`, `hitl_resolution_user_choice`, and moves
  `status` from `PENDING_HITL`/`BELOW_THRESHOLD` to `ACTIVE`. Columns all exist already
  (`kuzu_impl.py:439`); nothing in the schema changes.
- `commit._write_graph` dispatches the new operation along`MARK_SUPERSEDED`.
- `EDGE_REGISTRY`: `("DecisionAuditNode", "DecisionAuditNode", "supersedes")`.

This is the one place the append-only rule bends, and it bends exactly as far as
`Reconciliation.md` allows: *"Status changes do not delete the node."* The content of the
original note — action, confidences, runner-up, model — is never touched.

## B11. Config

`OperationalConfig` already has `hitl_queue_cap`. Two more:

```python
hitl_snooze_hours: int = _env_int("LUMEN_HITL_SNOOZE_HOURS", 24)
hitl_auto_resolve_days: int = _env_int("LUMEN_HITL_AUTO_RESOLVE_DAYS", 7)
```

Both configurable because the 24 hours is our divergence and the 7 days is the spec's, and a
deployment that wants the literal spec should be able to set snooze to 0 rather than patch
code.

## B12. API (`lumen/api/routes/hitl.py`)

`get_reviewer(request) -> ReviewService` in `deps.py`, built once at startup like
`get_reporter`.

| Route | |
|---|---|
| `GET /hitl` | The queue, cards in priority order. `limit` capped at 100. Sweeps first. |
| `GET /hitl/count` | `{pending, oldest_asked_at, cap, parked, at_capacity}` — the badge. No sweep. |
| `GET /hitl/{item_id}` | One card. 404 on unknown or another user's. |
| `POST /hitl/{item_id}/resolve` | Body `{choice}`. Returns `ResolutionOutcome`. |
| `POST /hitl/{item_id}/snooze` | Returns the card with its new dates. |
| `POST /hitl/sweep` | Runs housekeeping, returns `SweepReport`. What Goal 20 will call. |

Errors: `BadRequest` for a choice the card does not offer (`ACTION_B` on a below-threshold
item), `Conflict` for an already-resolved item and for `StaleProposalError` — the request was
valid and the world moved, which is what 409 is for. Response models go in
`api/schemas.py` beside the report views.

**`lumen/api/static/review.html`** — one card per item, the options as buttons, age and
snooze count visible, cap and pending count in a header, a sweep button. Plain, same as
`reports.html`. A `nav` link added to the four existing pages.

## B13. Tests

`lumen/tests/` — new files mirroring the modules, at least 90%:

| File | Covers |
|---|---|
| `test_review_freeze.py` | Each of the eight actions freezes to a replayable fragment; runner-up variant built from the swapped decision; BRANCH-as-recommendation still gets a fallback; a fragment survives a JSON round-trip byte-for-byte. |
| `test_review_capacity.py` | Under, at, and over cap; admission order across priority and signal; empty parked list. |
| `test_review_cards.py` | Tie layout has three options with both candidates' own words; below-threshold has two; age and auto-resolve dates; stale flag; batched episode reads. |
| `test_review_resolve.py` | Every choice maps to the right variant and the right `HitlResolutionChoice`; `ACTION_B` refused without a runner-up; stale target refuses Approve but allows Reject; edges re-stamped to the new note; both notes present; empty-fragment resolution still resolves. |
| `test_review_housekeeping.py` | Auto-resolve only with `snooze_count >= 1` and past the cutoff; a never-viewed item never auto-resolves; one failure does not abort the sweep; admission after resolution; idempotent. |
| `test_review_service.py` | Ownership rejection as 404; double resolve; sweep-on-read and sweep-on-resolve; `counts` does not sweep; `IndexWriteFailed` still resolves. |
| `test_api_hitl.py` | All six routes, both error codes, response shapes. |
| `test_operational_review.py` | New repository methods, `list_visible` vs `list_pending` under snooze, migration up and down. |

**The end-to-end test the goal is judged on** — extending `test_pipeline_e2e`: run a real
episode with a forced tie, assert the item is queued with a frozen proposal, resolve it
through the service, assert the suspended edge now exists in the graph, both notes are
present and linked, the new record is in the vector index, and the queue is empty.

## B14. Deferred, and to Where

| Deferred | To | Why |
|---|---|---|
| Undo of a resolution | its own goal | Applies to every decision, not just answered ones; needs edge invalidation the provider does not have. |
| Extraction failures in the queue | Goal 29 (as reachable-from-episode) | The queue requires an audit node; giving failures a synthetic one would put a fabricated decision in the graph. |
| Mobile-first one-tap surface | Goal 29 | Needs Goal 23's design system. |
| Running the sweep on a timer | Goal 20 | The endpoint is built here; nothing schedules it yet. |
| Badge count push / weekly nudge | Goal 20 | `GET /hitl/count` is the data; delivering it is the gateway's. |
| Canonical-node designation via HITL | unscheduled | `Reconciliation.md` mentions a user marking a node canonical; no card layout in the spec offers it, so it is not invented here. |

---

# SECTION C — WHAT WAS ACTUALLY BUILT

Section B was the design going in. This records where the build departed from it and
why, so the next person reads the reasons rather than rediscovering them.

## C1. Where the freezing lives

**Planned:** `lumen/review/freeze.py`.
**Built:** `lumen/pipeline/reconciliation/freeze.py`.

The review package importing the reconciliation planner, while the reconciliation stage
imported the review package to do the freezing, is a cycle through both packages'
`__init__`. Moving it also fixed the layering: what is being saved is *what the
reconciliation stage was about to do*, so it belongs beside that stage. The review queue
only ever reads it back.

For the same reason `ProposalVariant` and `FrozenProposal` live in
`lumen/schemas/pipeline.py` rather than in `lumen/review/contracts.py` — they are pipeline
DTOs crossing a boundary, and keeping them in the schemas package leaves it a leaf that
imports nothing.

## C2. A plan could not survive being written down

The design assumed a `PlanFragment` could be stored as JSON and read back. It cannot. A
plan holds records as `GraphNode` and links as `LumenEdge`, so reading one back produces
the *base* type: a merge link loses its `decision_id`, a belief becomes indistinguishable
from a pattern, and validation rejects the fields that no longer belong to anything.

Added `SavedNode` and `SavedEdge`, which store the kind alongside the fields, plus
`NODE_MODELS` and `EDGE_MODELS` to look the kind back up. `SavedEdge.restamped()` moves a
link's decision pointer without unpacking it. A test freezes each of the eight actions and
asserts the whole proposal survives a round trip unchanged.

## C3. A tie had lost one of its two readings

`check_tie` relabels a decision as `AMBIGUOUS`, which is right for the permanent note — the
system has no preference — and erased the reading that was in front. The saved proposal's
"take the first reading" was therefore an action that does nothing, on exactly the card
type the whole feature exists for.

*Amends Goal 9:* `SettledDecision.tied_action` / `tied_confidence` keep the leading reading
as the label is applied. The note still records `AMBIGUOUS`; the card gets both readings.

## C4. "Record it on its own" has two names

The clock settles an expired item by standing the finding on its own — which a
recommendation card calls **Reject** and a tie card calls **Create new**. Housekeeping had
`REJECT` written into it, so it could settle a low-confidence item and could not settle a
tie: the refusal was caught, logged, and the item silently never closed.

`cards.standing_alone_choice()` asks for that answer by meaning. The `Resolver` protocol
narrowed to `(item_id)` — housekeeping decides *which* items have expired, the service
decides what settling one means. That is what the protocol's docstring always claimed.

## C5. Smaller corrections

- **`_already_written` gathers the ends of the saved links**, rather than naming the target
  and the source by hand. An `EVOLVE` attributes the change to the event that caused it, and
  that anchor appears in no field of the answer — the plan refused itself for pointing at a
  record it does not create.
- **The queue state is checked before the graph is written**, not after. The store's guard
  is still there, but by the time it fired the change had landed and the second copy failed
  on a duplicate identifier instead of on the thing that was actually wrong.
- **`count_asked` is separate from `count_pending`.** The ceiling limits what is being put to
  the person; counting the parked items against the ceiling they are queued behind would
  mean none of them ever got in. A deferred item counts against it — it is out of sight, not
  answered.
- **The service borrows the index and the embedding model** (`open_vectors` /
  `open_embedder`) instead of building its own. A file-backed index takes a lock, so a second
  handle inside one process is refused; `LazySearchStack` grew accessors to lend them.
- **`Conflict` (409)** joined the error handlers. An answer to a question already answered,
  or one whose record has been rewritten since, is a valid request that arrived late — a
  caller should reload, not fix the request.

## C6. Result

4172 tests passing (103 new), covering the new package, the queue's storage, the escalation
path, the six routes, and one end-to-end run: a real entry with a forced tie, the change
held back, the card answered, and the suspended write landing in the graph with both notes
linked.

*Plan:* this document. *Master plan:* `implementation/Master_Plan.md` Goal 18.
