# Goal 9: Stage 3 — Reconciliation

**Branch:** `goal9`
**Status:** ✅ Complete
**Depends on:** Goal 6/7 (extraction) ✅, Goal 8 (candidate retrieval) ✅, Goal 2 (node/edge schemas) ✅
**Blocks:** Goal 10 (orchestrator / graph write), Goal 12 (multi-session integrity), Goal 18 (HITL queue)

---

## Objective

Stage 1 read today's entry with no memory. Stage 2 fetched the handful of things the
person has said before that might be related. Stage 3 is where the two meet and something
permanent is decided: *is this the same thing they've said twenty times, is it a change in
who they are, is it a contradiction they're living with, or is it genuinely new?*

This is the only stage that decides what the graph will look like a year from now. Every
other stage can be re-run. These decisions are append-only: a wrong MERGE fuses two ideas
that were never the same, and a missed one fragments a pattern into twenty orphans. Neither
is visible from the outside, and neither can be quietly undone.

---

# SECTION A — LOGIC (please verify)

*Short by design. This is what the goal means; Section B is how it's coded.*

## A1. What Gets Built

| Deliverable | What it is |
|---|---|
| **`reconcile()`** | Today's extraction plus its retrieved candidates in; one decision per item out — plus the exact records to write. Reads the graph, writes nothing. |
| **The decision call** | One cheap call covers the whole entry. Anything that came back as a high-consequence action is re-asked with the deep model before it counts. |
| **The gates** | Six checks applied in code *after* the model answers: is the action even structurally possible, is it a tie, is it a one-off dressed up as a change, is it confident enough, does the person own it now, and did the search actually run. |
| **The write plan** | For each decision, the concrete records and links it implies, fully built and validated. Goal 10 executes it; it makes no judgements of its own. |
| **Person records** | People named in the entry get a record and a link to what was said about them — which is what makes Goal 8's person-based history lookup start finding things. |
| **Open questions** | An unresolved question that has surfaced before is promoted from a passing note to a standing one. |
| **Small fixes to the layers below** | Two missing columns, one missing link type, two named bookkeeping operations, one new count query. |

## A2. The Decisions You Made

1. **Stage 3 hands over a finished write plan.** It returns both the decision ("evolve
   this belief, confidence 0.94") and the records that decision implies — the new version
   of the belief, the link back to the old one, the anchor that explains what caused the
   change, the audit record. It writes none of them. Goal 10 executes the plan in order
   without interpreting it. The alternative — Stage 3 names the action and Goal 10 works
   out what it means — puts the meaning of "EVOLVE" in a different file from the logic
   that chose EVOLVE, and the two would drift.

2. **One cheap call for the entry, then a deep call for anything risky.** The whole entry
   is decided in a single lightweight call. Any item that comes back as EVOLVE, CONTRADICT
   or DIALECTIC — the three that permanently alter a long-held belief — is then re-asked
   with the deep model, which may confirm it, lower its confidence, or overrule it down to
   a safer action. Typical cost is one call; two when something consequential is claimed.
   The audit record names whichever model made the final call, so the spec's per-action
   model table is satisfied by what actually happened rather than by intention.

3. **Only claim-like findings can become permanent beliefs or patterns.** A fixed table
   says which of the ~50 finding types are eligible: beliefs, patterns, reframes, core
   wounds, breakthroughs, rumination loops, and so on. "Worked late", "felt tired",
   "was at my desk" are recorded as part of that day and never become standing records.
   Crucially, non-eligible findings *are still reconciled* — a tiredness note can still
   reinforce an existing exhaustion pattern. What they cannot do is create a new permanent
   record when nothing matches. This is the difference between a graph of a few hundred
   meaningful nodes and one of ten thousand one-off ones.

4. **Person records are created here, and open questions are promoted here.** Both were
   documented as Reconciliation's job and both were waiting on this goal. The person half
   matters immediately: Goal 8 built a lookup that finds history by who it's about, and
   that lookup has been returning nothing because nothing has ever created a person record.
   Cross-entry alias matching ("my mentor" and "Alex" are the same person) stays deferred —
   it is the same fuzzy-matching problem as pattern merging and deserves its own goal.

5. **Content is never edited; three bookkeeping fields are.** How many times a pattern has
   been evidenced, when it was last reinforced, and whether an old version is now
   superseded — these have to change on an existing record. Rather than pretend otherwise,
   they get two explicitly named operations that can touch nothing else, and the
   architecture rule is restated as what it actually means: *no content field is ever
   rewritten*. Everything the person said stays exactly as written, forever. Stated openly
   in the docs, because a rule with a hidden exception is worse than one with a visible
   one.

6. **An uncertain item waits alone.** When the system genuinely cannot choose between two
   readings, that one item is held for you and everything else from the same entry is
   written immediately. The spec said to freeze the whole entry; in practice one unanswered
   question would freeze a day's work indefinitely, and unanswered items have no expiry.
   The entry is marked as having something outstanding, so nothing is lost track of. The
   cost is honest: when you answer three days later, that item is decided against a graph
   that has moved on slightly.

7. **A failed search must never look like a new idea.** Goal 8 added `search_failed`
   precisely so this stage could tell "looked, found nothing" from "couldn't look". This is
   the stage that has to honour it: an item whose search failed is **never** branched into
   a new permanent record. It waits for review instead. Branching it would file a
   ten-year-old pattern as a brand-new discovery, silently and permanently.

## A3. The Eight Actions, Plainly

What each one actually writes. This is the table to check hardest.

| Action | Needs | What gets written |
|---|---|---|
| **MERGE** ≥0.88 | The new finding means the same as an existing record | A "same as" link, new → existing. Both records survive intact. Nothing is collapsed or deleted. |
| **REINFORCE** ≥0.80 | Consistent with an existing record, but a separate instance | A "reinforces" link, plus the evidence count and last-reinforced date on the target. |
| **EVOLVE** ≥0.93 | A real shift in something previously stable | A new *version* of the belief/pattern, a link back to the old version, the old one marked superseded, an anchor link to the event or session that caused the shift, and a mandatory description of what changed. |
| **BRANCH** ≥0.75 | Related but genuinely distinct | A new belief/pattern record (only if the finding is eligible per A2-3) and a link from today's finding to it. |
| **CONTRADICT** ≥0.85 | Two incompatible beliefs held *at the same time* | A belief record for the new side, a contradiction record joining both, and links to each. Neither belief is superseded — that is what makes it different from EVOLVE. |
| **DIALECTIC** ≥0.88 | Two opposing things that are both true | A tension link between them, with a written summary of the tension. Neither side wins. |
| **REGULATE** ≥0.82 | The person caught themselves mid-pattern | A "regulates" link from today's finding to the pattern, with a summary. The pattern is not evolved — noticing it once is not the same as changing. |
| **AMBIGUOUS** | The top two readings are within 0.05 of each other | Nothing but an audit record. Waits for you. |

Every one of these — including AMBIGUOUS and including below-threshold refusals — writes an
audit record with a rollback pointer. No exceptions, per the architecture rule.

## A4. The Gates, and Why Each Exists

Applied in code after the model answers, in this order. The model proposes; these dispose.

1. **Is the action structurally possible?** Not every action makes sense from every kind of
   finding — "the same as" only connects a finding to a pattern, never an event to a
   belief. An impossible combination falls back to the runner-up reading if that one is
   possible, and otherwise goes to review. No extra model call: a model that proposed an
   impossible link once will propose it again.

2. **Is it a tie?** Top two within 0.05 → AMBIGUOUS, no writes, waits for you. This holds
   regardless of how confident the model was: 0.92 against 0.90 is as much a tie as 0.61
   against 0.59.

3. **Is this a trial or a trait?** Overcoming a ten-year fear *once* is not a new identity.
   If a finding contradicts a belief older than six months for the first time, the bar for
   EVOLVE and CONTRADICT rises to effectively unreachable, and the system records the
   instance separately instead. Repeated instances are what eventually earn the change. One
   documented bypass: when the person themselves names the breakthrough explicitly, their
   own self-awareness overrides the scepticism.

4. **Is this a bad week, or a changed person?** A crunch-week collapse should not overwrite
   the baseline of a year-long era. The model is asked one extra question — *is this a
   short-lived spike within an ongoing period?* — and when the answer is yes, an EVOLVE is
   recorded as a separate instance instead. Only a reader of the text can make that call;
   only code should be trusted with the consequence.

5. **Is it confident enough?** Below the action's threshold → waits for review. It is
   **not** quietly downgraded to "create something new", which would turn every uncertain
   moment into a duplicate.

6. **Did the person take ownership?** When someone refines a framing the assistant
   originally offered, the refined version is recorded as theirs and marked confirmed. The
   audit record keeps the lineage so the origin is never lost.

## A5. Where the Specs Disagree With Themselves

Found while reading. Each needs a doc fix, listed in B10.

1. **Rule numbering is broken.** `Reconciliation.md` lists rules R1, R2, R3, then discusses
   "Rule R5" and "Rule R6" as if they existed — and R6's description is word-for-word R3's.
   Two rules were deleted at some point and the prose was never renumbered.

2. **The "local extremum" rule names a tag nothing produces.** It says intense short-lived
   states "are tagged as `LOCAL_EXTREMUM`" — but no node, enum or stage anywhere in the
   system produces that tag, and none ever did. As written the rule cannot run. Fixed by
   making it what it implicitly is: a judgement the reconciliation call is asked for
   directly (A4-4).

3. **Rollback points at an identifier that does not exist.** Every audit record carries
   `edge_id` and a rollback pointer naming "the edge to invalidate" — but links in the
   graph have no id column, and never have. Rollback is therefore impossible as specified.
   Resolved by identifying a link the way the database actually can: every reconciliation
   link already carries the id of the decision that created it, so the decision id *is* the
   handle. The audit record stores a readable descriptor alongside it.

4. **Decisions about a session cannot be recorded.** Findings, patterns, beliefs, events
   and contradictions can all point at their audit record. Sessions cannot — the link type
   was never defined. Since a session is one of the three things this stage decides about,
   that link is added.

5. **Two link types require summaries that have nowhere to go.** Flagged in Goal 2 and
   deferred to this goal: a tension link needs a tension summary and a regulates link needs
   a regulation summary, and neither column exists in the database. Writing one today would
   fail. Fixed here.

6. **"Suspended" changes meaning.** It described an entry whose writes were entirely frozen.
   Per A2-6 it now means an entry that was written, with one or more items still awaiting a
   person. The history lookup that searches for un-reconciled weighty material is widened to
   match, or it would stop finding exactly the items this stage set aside.

7. **The evidence count is described as derived and stored as a field.** `Schema.md`
   defines it as "count of records linked via reinforces or same_as", which is a query, and
   also gives it a column. Per A2-5 the column is authoritative and the named bookkeeping
   operation keeps it true; the doc is amended to say so.

## A6. What This Goal Deliberately Leaves Undone

| Deferred | To | Why |
|---|---|---|
| Actually writing to the graph | Goal 10 | This stage plans; the orchestrator executes. One write path, unchanged. |
| The review queue itself — storage, ordering, the 40-item cap, snooze, auto-resolve | Goal 18 | This stage produces the items and says why each is waiting. Where they queue up is a different goal, and the table already exists. |
| Rolling a decision back | Goal 18 | The pointer is written and tested here; the procedure that follows it needs an API and an invalidate operation. |
| Cross-entry alias matching for people | Later goal | Same fuzzy-matching problem as pattern merging; a person record that exists is the precondition, and that is what ships here. |
| Closing an open question | Goal 17 | Promotion needs today's entry. Deciding a question is *finished* is a longitudinal judgement. |
| Two jobs deciding the same thing at once | Goal 10 | The write-serialization queue belongs with the thing that writes. Single-user, one entry at a time until then. |
| Promoting a lesson to a standing record | Not scheduled | There is a record type for lessons but no link type that could attach one, so it cannot be written. Recorded rather than improvised. |

## A7. The Risk Worth Naming

**Nothing here fails loudly, and everything here is permanent.**

Stage 2's failure mode was silence. This stage's is worse, because it acts. A model that
drifts toward MERGE fuses distinct ideas into one blurred node, and there is no later
signal that it happened — the graph simply gets less true, and every retrieval afterwards
returns the blur. A model that drifts toward BRANCH shatters one pattern into twenty
fragments, none of which ever accumulate enough evidence to be worth surfacing. Both look
like a working system from every angle except reading the graph by hand.

Four defences, all mechanical rather than hoped-for:

1. **Every decision is auditable and reversible by construction.** The audit record and its
   rollback pointer are written for all eight actions, including the ones that write
   nothing. A bad run is a thing you can find and undo, not a thing you discover a year
   later.
2. **The high-consequence actions cannot be taken by the cheap model alone** (A2-2), and
   the two hardest to reverse carry the two highest thresholds.
3. **Uncertainty waits rather than guessing.** Below-threshold does not become BRANCH; a tie
   does not become the higher score; a failed search does not become novelty. Each of the
   three has its own test asserting nothing was written.
4. **The closing log line carries per-action counts**, so a run that has quietly become 90%
   BRANCH is visible as a number rather than as a slow decline in graph quality nobody can
   date.

A second, quieter risk: **the write plan is trusted downstream.** Goal 10 executes it
without judgement, so a plan referring to a record that does not exist would fail
mid-execution, leaving half an entry written. The plan validates its own internal
consistency before it is returned — every link's endpoints must either be created earlier
in the same plan or already exist in the graph, checked against the graph, not assumed.

## A8. Definition of Done

- [ ] `reconcile()` writes nothing — a test asserts both stores are byte-identical after a
      full run, including that no node was created.
- [ ] An entry with nothing risky in it costs exactly one model call; one with an EVOLVE
      costs exactly two, whatever the number of items.
- [ ] Each of the eight actions produces exactly the records and links A3 names, asserted
      individually.
- [ ] An identical historical record produces MERGE; a changed version produces EVOLVE with
      a non-empty description of what changed — the Master Plan's two named tests.
- [ ] EVOLVE always writes the anchor link to an event or session; a test proves an EVOLVE
      cannot be planned without one, because the bipartite rule is structural.
- [ ] A tie produces AMBIGUOUS, an audit record, a review item, and **zero** writes.
- [ ] A below-threshold decision produces a review item and **zero** writes, and is proved
      not to become a BRANCH.
- [ ] **An item whose search failed is never branched**, and a test asserts it.
- [ ] A first-time deviation from a belief older than 180 days does not EVOLVE; the same
      deviation named as a breakthrough by the person does.
- [ ] A non-eligible finding type never creates a belief or pattern, but is still allowed
      to reinforce one.
- [ ] A person named twice across two entries produces one person record, not two, and the
      second entry updates its mention count rather than creating a duplicate.
- [ ] Goal 8's person lookup finds something after a reconciliation run — the loop closes.
- [ ] An impossible action falls back to the runner-up, and to review when that is
      impossible too; neither path invents an action.
- [ ] The write plan is internally ordered and self-consistent; a plan naming an unknown
      record refuses to build.
- [ ] Tension and regulates links write successfully against real Kuzu, with their
      summaries — the Goal 2 gap is closed and proved closed.
- [ ] Journal text never appears in a log line unless `LUMEN_LOG_PROMPTS=true`.
- [ ] Every result and audit record carries the ambient `trace_id`.
- [ ] ≥90% coverage on `lumen/pipeline/reconciliation/`.

---

# SECTION B — LOW-LEVEL DESIGN

## B1. Files

```
lumen/pipeline/reconciliation/
├── __init__.py      — public surface: reconcile()
├── stage.py         — reconcile(): sequences, times, logs
├── decide.py        — the batched LIGHTWEIGHT call + the THINKING escalation
├── gates.py         — the six code-enforced checks (A4), in order
├── plan.py          — action -> nodes/edges/bookkeeping; the write plan builder
├── promote.py       — new Belief/Pattern/OpenLoop construction from a finding
├── people.py        — person record resolution and mentions links
├── catalog.py       — the frozen tables: thresholds, roles, promotion map, legality
├── contracts.py     — internal shapes: DecisionResponse, ItemDecision, Verdict
└── prompts.py       — the decision prompt and the escalation prompt

lumen/schemas/pipeline.py     — write-plan DTOs + ReconciliationOutcome
lumen/schemas/enums.py        — PlannedWriteKind (new)
lumen/graph/provider.py, kuzu_impl.py  — 1 read, 2 bookkeeping writes, 1 registry entry,
                                         2 edge columns

lumen/tests/
├── test_reconciliation_decide.py
├── test_reconciliation_gates.py
├── test_reconciliation_plan.py
├── test_reconciliation_promote.py
├── test_reconciliation_people.py
├── test_reconciliation_stage.py
└── test_graph_writes_stage3.py   — new provider surface against real Kuzu
```

**Deviation from `Master_Plan.md`**, which names `lumen/pipeline/reconciliation.py`. Same
call as Goals 5–8, for the same reason.

## B2. Signature and Output Contract

```python
def reconcile(
    extraction: ExtractionResult,
    retrievals: list[RetrievalResult],
    *,
    graph: GraphProvider,               # read-only; hydration + existence checks
    lightweight: LLMProvider,
    thinking: LLMProvider,
    episode: MicroextractionInput | None = None,
    config: AppConfig | None = None,
) -> ReconciliationOutcome
```

The graph is injected exactly as in Stage 2, and for the same reasons (Goal 8 A2-1). Three
things genuinely need it and none of them can come from `CandidateNode`, which carries only
an id, a type and a preview:

- a candidate's **age**, for the trial-vs-trait gate;
- a candidate's **full field set**, to build the next version of it on EVOLVE;
- whether a **person record already exists**, and whether a plan's endpoints exist.

New DTOs in `lumen/schemas/pipeline.py`:

```python
class PlannedNode(BaseModel):
    node_type: str                       # matches NODE_TABLES / NODE_ID_PREFIXES
    node: GraphNode                      # a fully-built, already-validated node model
    searchable_text: str | None = None   # set when Goal 10 should also embed it

class PlannedEdge(BaseModel):
    logical_type: LogicalEdgeType
    from_node_type: str
    to_node_type: str
    edge: LumenEdge                      # LumenEdge / ReconciliationEdge subclass
    # physical table resolved via resolve_edge_table() at build time and stored,
    # so an unsupported triple fails here rather than at write time

class PlannedBookkeeping(BaseModel):
    """The named exception of A2-5. Two operations, nothing else."""
    operation: Literal["MARK_SUPERSEDED", "RECORD_REINFORCEMENT", "TOUCH_PERSON"]
    node_id: str
    at: datetime

class GraphWritePlan(BaseModel):
    nodes: list[PlannedNode]             # ordered: dependencies first
    edges: list[PlannedEdge]
    bookkeeping: list[PlannedBookkeeping]

class HitlEscalation(BaseModel):
    audit_node_id: str
    source_node_id: str
    episode_id: str
    entry_type: HitlEntryType            # reused from lumen.operational.enums
    signal_strength: SignalStrength      # queue priority, computed here not there
    summary: str

class ReconciliationOutcome(PipelineDTO):
    episode_id: str
    results: list[ReconciliationResult]
    audit_nodes: list[DecisionAuditNode]
    write_plan: GraphWritePlan
    escalations: list[HitlEscalation]
    episode_status: ReconciliationStatus  # COMPLETE | SUSPENDED
    decision_model: str
    decision_time_ms: int
    decision_failed: bool = False         # the reply could not be read at all
```

`ReconciliationOutcome` is the deviation from the Master Plan's "Output: `ReconciliationResult`
+ `DecisionAuditNode`" — an entry produces many of each, plus the plan they imply, and they
have to arrive together or Goal 10 cannot execute them atomically. Recorded so the deviation
is not read as an accident (same shape as Goal 8's list deviation).

`GraphWritePlan` validates on construction:
- every edge endpoint is either the `node_id` of an earlier `PlannedNode` or is passed in as
  a known-existing id (the stage supplies the set it verified against the graph);
- `nodes` is topologically ordered — a contradiction record never precedes the belief it
  names;
- no two `PlannedNode`s share a `node_id`.

## B3. The Frozen Tables (`catalog.py`)

Four tables, all frozen, all directly citable from a test.

**Thresholds and roles** — transcribed from `Reconciliation.md`:

```python
THRESHOLD = {MERGE: 0.88, REINFORCE: 0.80, EVOLVE: 0.93, BRANCH: 0.75,
             CONTRADICT: 0.85, DIALECTIC: 0.88, REGULATE: 0.82}
ESCALATES = frozenset({EVOLVE, CONTRADICT, DIALECTIC})   # A2-2: deep model confirms
TRIAL_PENALTY_THRESHOLD = 0.98
TRAIT_AGE_DAYS = 180
TIE_WINDOW = 0.05
```

**Action legality** — derived from `LOGICAL_TO_PHYSICAL`, not hand-written, so it can never
drift from what the database supports:

| Action | Source node | Target node |
|---|---|---|
| MERGE | Observation | Pattern |
| REINFORCE | Observation, Event | Pattern, Belief |
| EVOLVE | Observation, Event, Session (trigger) | Pattern, Belief (the versioned node) |
| BRANCH | Observation, Event, Session | new Pattern, new Belief |
| CONTRADICT | Observation | Belief |
| DIALECTIC | Observation (promoted) | Belief, Pattern |
| REGULATE | Observation, Session | Pattern |

EVOLVE is the odd one: the `evolved_from` link runs *new version → old version*, both of the
same type, and the extracted finding is what triggered it rather than an endpoint. The
finding reaches the audit record via `decided_by`, and the causal anchor link runs from the
new version to the event or session (`caused_by`).

**Promotion map** — which finding types may become a standing record, and which kind:

- → **BeliefNode**: `BELIEF`, `META_BELIEF`, `EPISTEMIC_SHIFT`, `CONCEPTUAL_REFRAME`,
  `PERSPECTIVE_SHIFT`, `CORE_WOUND`, `CORE_CONFLICT`, `IDENTITY_AFFINITY`,
  `IDENTITY_FUSION_STATE`, `EXISTENTIAL_REFLECTION`, `ACCEPTANCE_ACKNOWLEDGEMENT`,
  `METACOGNITIVE_BREAKTHROUGH`
- → **PatternNode**: `PATTERN`, `RUMINATION_LOOP`, `COGNITIVE_DISTORTION`,
  `COGNITIVE_DISTORTION_STATE`, `COGNITIVE_DEFENSE_MECHANISM`, `SELF_NARRATION_PATTERN`,
  `SOCIAL_PERFORMANCE_STATE`, `SUBPERSONALITY_ACTION`, `RELATIONAL_DYNAMIC`,
  `ENVIRONMENTAL_DEPENDENCY`, `INAUTHENTICITY_STATE`, `OTHER_PERSON_MODEL`
- → **OpenLoopNode**: `OPEN_LOOP` (A2-4, and only when it has surfaced before — B7)
- → **nothing**: everything else (~30 types), which may still MERGE, REINFORCE or REGULATE

The map is a `dict[ObservationType, PromotionTarget | None]` covering **every** enum member
explicitly, with a test asserting total coverage — so a type added later fails the suite
instead of silently defaulting to unpromotable.

**Model role per outcome**: `LIGHTWEIGHT` unless the item went through escalation, in which
case `THINKING`. Recorded on the audit node.

## B4. `decide.py` — Two Calls, One Entry

**Pass 1 (LIGHTWEIGHT, one call).** Every searchable item and its candidate set, numbered.
The response is a list keyed by item index, aligned by position with padding on a short
reply — the same rule and the same reason as Goal 8's HyDE alignment (a decision applied to
the wrong finding is worse than a missing one):

```python
class ProposedAction(BaseModel):
    action: ReconciliationAction
    target_node_id: str | None
    confidence: float
    reason: str

class ItemDecision(BaseModel):
    item_index: int
    primary: ProposedAction
    runner_up: ProposedAction | None          # always requested; A5-1 wants it recorded
    is_local_extremum: bool = False           # A4-4 / A5-2
    new_node: NewNodeContent | None = None    # BRANCH/DIALECTIC/CONTRADICT payload
    delta_description: str | None = None      # EVOLVE
    contradiction_summary: str | None = None  # CONTRADICT
    tension_summary: str | None = None        # DIALECTIC
    regulation_summary: str | None = None     # REGULATE

class DecisionResponse(BaseModel):
    decisions: list[ItemDecision]
    people: list[PersonSketch] = []           # relationship/sentiment for new people only
```

The prompt carries, per item: the finding's text, type, signal strength and provenance; and
per candidate: id, type, preview, and **how it was found**. Structural candidates are
labelled as such with the rule stated in `Reconciliation.md` — *do not judge these on
similarity; ask whether today's entry describes a change, resolution or continuation of
what this node holds*. Semantic candidates carry their score; structural ones carry their
anchor.

**Pass 2 (THINKING, one call, only if needed).** The items whose primary action is in
`ESCALATES`, with their candidates, the cheap model's reasoning, and the full text of the
candidate being altered (hydrated from the graph — a version is being written from it). It
returns a `Verdict` per item: `CONFIRM` with a possibly-revised confidence and the required
payload, or `OVERRULE` to a lower-risk action with its own confidence. It may never escalate
an item *into* a higher-risk action, since it has only been shown the risky ones.

**Read failure.** An unreadable reply is re-issued rather than corrected — there is nothing
to correct — up to `max_extraction_attempts`, reusing Goal 7's rule and its config knob.
After the last attempt `decision_failed` is set, no writes are planned, the episode is
`SUSPENDED`, and every item is escalated. Nothing is invented, and nothing is branched.

## B5. `gates.py` — The Six Checks

One function per gate, each taking a decision and returning either the decision (possibly
altered) or a `Refusal` carrying the reason. Applied in the A4 order, short-circuiting on
the first refusal. Every gate has a named `GateRule` enum member which is what the audit
record and the review item quote — same shape as Goal 6's `DropRule`.

Two need graph reads:

- **trial-vs-trait** needs the target's age (`valid_from` on the hydrated candidate) and
  whether this is the *first* deviation. "First" is made concrete as: no prior audit record
  targets this node with a BRANCH, CONTRADICT or EVOLVE action. That needs one new narrow
  read, `count_prior_decisions(target_node_id, actions)` — the audit trail is already a
  first-class part of the graph, so this is a query it was built to answer.
- **ownership transfer** needs the source finding's provenance, which is on the extracted
  node and needs no read.

The tie check is deliberately *not* delegated to the model. `Reconciliation.md` is explicit
that a model claiming 0.92/0.90 must be overridden without spending a call, and it is the
one rule the doc says corrects rather than rejects.

## B6. `plan.py` — Action to Records

One builder per action, each returning `(nodes, edges, bookkeeping)`. Every builder also
emits the `DecisionAuditNode` and its `decided_by` link, so an action that forgets its audit
record cannot compile. Ids are minted with `make_node_id` / `make_slug_node_id` — decisions
are date-keyed (`d_2026_06_11_003`), beliefs and patterns slug-keyed from their name, which
also makes a re-run idempotent by construction.

The EVOLVE builder is the one worth reading twice:

```
1. hydrate the target in full                              [graph read]
2. copy it into a new node of the same type, with:
     version = old.version + 1
     previous_version_id = old.node_id
     content replaced by the new statement
     version_delta = delta_description
     provenance/verification per the ownership rule (A4-6)
3. plan: evolved_from(new -> old)
4. plan: caused_by(new -> the session or event that anchors it)   <- bipartite rule
5. plan: bookkeeping MARK_SUPERSEDED on old
6. plan: DecisionAuditNode + decided_by from the triggering finding
```

Step 4 is not optional and is not a model judgement. Stage 1 mints exactly one session per
reflective episode precisely so an anchor always exists; the builder prefers an event the
model named as the cause and falls back to that session. If neither exists — which can only
happen on a path that should never reach EVOLVE — the builder refuses and the item goes to
review rather than writing a shift with no cause.

**Rollback pointer** (A5-3): `edge_to_invalidate` holds `"{physical_table}:{from_id}->{to_id}"`
and every reconciliation link already carries `decision_id`, which is what an invalidate
query will actually match on. `nodes_to_requeue` holds the source finding.

## B7. `promote.py` and `people.py`

**Promotion** happens only when the action is BRANCH (or when DIALECTIC/CONTRADICT needs a
node for the new side) *and* the finding's type appears in the promotion map. A non-eligible
finding that would have branched produces a decision of BRANCH with no new node — recorded
in the audit trail as considered-and-not-promoted, so the absence is a fact rather than a
gap. `domain` and the name come from the model's `new_node` payload; the statement, signal
strength, provenance and person refs come from the finding itself, unchanged.

**Open loops** promote when the same question has surfaced before — made concrete as: the
finding is `OPEN_LOOP` and retrieval returned at least one candidate for it. First-time
questions stay as findings. A promoted loop gets an `investigated_by` link to its episode.
Closing a loop is deferred (A6).

**People**: for every name in the entry's findings and its resolved reference map, the
record id is `make_slug_node_id("person", name)` — deterministic, so existence is a single
`get_node`, needing no new provider read. Missing → plan a new `PersonEntityNode` (relationship
and sentiment from the model's `PersonSketch`, `UNKNOWN` when absent). Present → plan a
`TOUCH_PERSON` bookkeeping update. Either way, plan `mentions` links from each finding,
event or session that named them. Aliases stay deferred, so two spellings still produce two
records today — a known, stated limitation, and the reason cross-entry resolution is its own
goal.

## B8. Provider Amendments

```python
# lumen/graph/provider.py
def count_prior_decisions(self, target_node_id: str, *, actions: list[str]) -> int
def mark_superseded(self, node_id: str, *, at: datetime) -> None
def record_reinforcement(self, node_id: str, *, at: datetime) -> None
def touch_person(self, node_id: str, *, at: datetime) -> None
```

The last three are the named exception of A2-5. Each touches a fixed, hard-coded set of
columns (`status`; `evidence_count` + `last_reinforced_at`; `mention_count` +
`last_mentioned_at`) with no caller-supplied field names, so there is no path from a caller
to a content column. A test asserts each leaves every other column byte-identical.

**Edge DDL** — the Goal 2 gap (A5-5). The generator currently gives all 47 link tables the
same four columns. It gains a per-table extra-columns map: `dialectic_*` get
`tension_summary STRING`, `regulates_*` get `regulation_summary STRING`. Everything else is
unchanged.

**Edge registry** — `decided_by_sess` added (`SessionNode → DecisionAuditNode`, A5-4), with
the matching `LOGICAL_TO_PHYSICAL` entry. 47 → 48 physical tables.

**`find_unresolved_high_signal`** — widened to episodes that are `PENDING_RERECONCILIATION`
**or** `SUSPENDED` (A5-6). Without this the items this stage sets aside for review become
invisible to the very lookup built to surface un-reconciled weighty material.

## B9. `stage.py` — The Sequence

```
1. index retrievals by source id; drop items with no extracted node       [code]
2. hydrate every distinct candidate once                                  [graph]
3. one decision call for the whole entry                                  [LIGHTWEIGHT]
4. escalate the risky ones, if any                                        [THINKING]
5. per item: run the gates, then build the plan fragment                  [code]
6. resolve people once per entry, not per item                            [graph + code]
7. assemble the plan, verify its endpoints, order it                      [graph]
8. episode_status = SUSPENDED if anything is waiting, else COMPLETE       [code]
9. one closing log line with per-action counts                            [code]
```

Step 2 hydrates once per entry rather than once per item, because the same candidate is
routinely returned for several findings in one episode — and step 6 for the same reason
Stage 2 runs its anchors once per episode.

An item whose `RetrievalResult.search_failed` is true skips the model entirely (A2-7): it is
escalated as `BELOW_THRESHOLD` with the reason recorded, because there is nothing to decide
against and asking anyway would produce a confident BRANCH.

## B10. Doc Amendments Required

Applied before coding, as Goals 4–8 did.

1. `Reconciliation.md` — renumber R5/R6 and delete the duplicated rule (A5-1); restate the
   local-extremum rule as a per-observation judgement (A5-2); state how rollback identifies
   a link (A5-3); state the new meaning of a suspended entry (A5-6, A2-6); record the
   two-call model policy against the per-action role table (A2-2); record that only eligible
   finding types promote (A2-3).
2. `Architecture.md` — the promotion rule and its table (A2-3); person records and open-loop
   promotion are created here (A2-4); the bookkeeping exception (A2-5).
3. `Schema.md` — the two link columns (A5-5); `decided_by_sess` (A5-4); the evidence-count
   wording (A5-7); the named bookkeeping operations as the only writes that touch an
   existing record.
4. `Technical_HLD.md` §5 and §8 — the `ReconciliationOutcome` shape and the write-plan
   hand-off; restate rule 3 as *no content field is ever rewritten*.
5. `Master_Plan.md` — record the output-shape deviation and tick Goal 9.

## B11. Test Plan (~130 tests)

| File | Covers |
|---|---|
| `test_graph_writes_stage3.py` | Tension and regulates links write with their summaries against real Kuzu; `decided_by_sess`; each bookkeeping operation changes exactly its own columns and nothing else; `count_prior_decisions`; the widened unresolved lookup. |
| `test_reconciliation_decide.py` | One call for many items; alignment by position and padding on a short reply; exactly the risky items escalate; the deep model may overrule downward and never upward; an unreadable reply is re-issued and then gives up honestly; the prompt labels structural candidates and never leaks journal text into logs. |
| `test_reconciliation_gates.py` | Each of the six gates, in isolation and in order: an impossible action falls to the runner-up and then to review; a tie at 0.92/0.90 and at 0.61/0.59 both become AMBIGUOUS; a 181-day-old belief resists EVOLVE and a breakthrough bypasses it; a local extremum downgrades; below-threshold never becomes BRANCH; ownership transfer flips provenance and verification. |
| `test_reconciliation_plan.py` | Each action's exact records and links; **MERGE on an identical record and EVOLVE with a delta — the Master Plan's named tests**; EVOLVE cannot be planned without its causal anchor; the plan refuses an unknown endpoint; ordering puts dependencies first; every action emits an audit record and its link; the rollback pointer is reconstructable. |
| `test_reconciliation_promote.py` | The promotion map covers every finding type; an eligible type creates a belief/pattern and an ineligible one creates none while still being allowed to reinforce; an open loop promotes only on a repeat. |
| `test_reconciliation_people.py` | One record for a name seen twice; the second entry touches rather than duplicates; mentions links reach every naming finding; **Goal 8's person lookup finds something after a run** — the loop closes. |
| `test_reconciliation_stage.py` | Call counts are exactly 1 and 1-or-2; hydration happens once per candidate; people resolve once per entry; a failed search is never branched; a partially suspended entry still writes its confident items; both stores are unchanged by a run; per-action counts appear in the log; `trace_id` on every result and audit record. |

Kuzu runs embedded against `tmp_path`; the language models are the existing fakes. No
network, no credentials. The write plan is executed against real Kuzu in one integration
test — not because Goal 9 executes plans, but because a plan that cannot be executed is not
a plan, and that failure must surface here rather than in Goal 10.

## B12. Build Order

0. Doc amendments (B10).
1. Edge DDL columns, `decided_by_sess`, the widened lookup, `count_prior_decisions`, the
   three bookkeeping writes — with `test_graph_writes_stage3.py`. Everything else depends
   on these existing.
2. Write-plan DTOs in `schemas/pipeline.py`, with their validators.
3. `catalog.py` — the four frozen tables, and the test that the promotion map is total.
4. `contracts.py`, `prompts.py`, `decide.py`.
5. `gates.py` — pure, no infrastructure, testable first.
6. `promote.py`, `people.py`, then `plan.py`.
7. `stage.py`.
8. `Master_Plan.md` and Section C.

---

# SECTION C — RESULTS

**Status:** ✅ Complete
**Tests:** 1649 passing (1395 before this goal + 254 new), 9 live tests still deselected.
**Coverage:** **100%** on `lumen/pipeline/reconciliation/` (all 10 modules),
`lumen/schemas/pipeline.py`, `lumen/config.py`, and `lumen/graph/provider.py`.

## C1. What Was Built

| Module | Contents |
|---|---|
| `reconciliation/catalog.py` | The four frozen tables: thresholds, which actions need a second opinion, which actions are structurally possible, and which findings may become permanent. |
| `reconciliation/decide.py` | The one batched call, the escalation call, and matching answers back to findings by number. |
| `reconciliation/gates.py` | Reading a proposal into a decision, and the seven checks applied to it in order. |
| `reconciliation/plan.py` | One builder per action, the audit note every action shares, and the rollback handle. |
| `reconciliation/promote.py` | Building a belief, a pattern, a standing question, or the next version of a record. |
| `reconciliation/people.py` | Person records and the links from findings to them. |
| `reconciliation/stage.py` | `reconcile()`, the sequencing, and the closing log line. |
| `graph/provider.py`, `kuzu_impl.py` | One count query, three bookkeeping writes, two edge columns, one new link type, one widened lookup. |
| `schemas/pipeline.py` | The write-plan DTOs and `ReconciliationOutcome`, with the validators that refuse an inconsistent plan. |

## C2. Deviations From the Plan

1. **A seventh gate was added.** Four actions carry a sentence that the graph has a
   column for and the node models require — what changed, what the clash is, what the
   tension is, what was interrupted. An answer arriving without one cannot be recorded
   at all. Writing the sentence in code would be the system inventing a claim about
   someone's inner life, so `check_required_wording` refuses and the item waits.

2. **`max_decision_attempts` is its own setting**, not Goal 7's extraction knob as B4
   suggested. Default 2 rather than 3: a decision reply has nothing to correct, so a
   repeat is a re-request, and a model that returned an unusable answer twice will
   return one a third time.

3. **`PlannedEdge.properties()` was not in B2.** Edge tables store only the link's own
   columns; the two ends are how it is attached. Without this the plan would hand Goal
   10 a dict containing two columns no edge table has.

4. **`HitlEntryType` moved to `schemas/enums.py`.** Importing it from the operational
   package would have executed that package's `__init__` and pulled SQLAlchemy into a
   pipeline stage — the same shape of problem Goal 8 found with the vector package. The
   operational module re-exports it, so nothing else changed.

5. **The plan's node-ordering rule excludes `contradiction_node_id`.** A contradiction
   names the two beliefs it joins and the newer belief names the contradiction back.
   That pair refers to each other by design, so requiring both directions would make a
   legitimate plan impossible to order. Only the direction that matters for reading the
   graph forwards is enforced.

## C3. Things Caught While Implementing

1. **An ambiguous decision could have been recorded as active.** The status was derived
   from *which check* refused the decision rather than from the action itself, so an
   AMBIGUOUS decision arriving by any other route would have been marked `ACTIVE` — an
   action the node's own validator forbids from ever auto-executing. Now keyed on the
   action, which is the rule as stated.

2. **The older belief in a contradiction is deliberately not updated.** `BeliefNode`
   carries `is_contradicted` and `contradiction_node_id`, and setting them on the
   existing belief would have meant editing a content record. The contradiction node and
   its two links describe the clash completely; the flags on the newer belief are set at
   creation, and the older one is left exactly as written.

3. **Cosine-style defensive code that could never run was removed rather than tested.**
   A confidence that is not a number never reaches the clamping helper — the reply fails
   to parse first and the whole reading is re-requested. Likewise the `TIE` branch of the
   status rule became unreachable once the action check was added. Both deleted.

## C4. What the Tests Cover

254 new tests across 7 new files plus the shared fixtures. The ones worth knowing about:

- **A first deviation from a 400-day-old belief is proved not to change it**, and the
  same deviation named as a breakthrough by the person is proved to get through. The
  record is proved to stay on the decision, which is what makes the *second* occasion
  countable — without it every deviation would forever be the first.
- **Below-threshold is proved not to become a new record**, and **a failed search is
  proved never to be asked about at all**. Those two are the quiet failures that look
  identical to success from every angle except reading the graph by hand.
- **A tie at 0.92/0.90 and a tie at 0.61/0.59 are asserted to behave identically**,
  because a tie is about the gap and not the height.
- **Both stores are asserted unchanged by a run**, including that no new record appeared.
- **The plan is executed against real Kuzu in one test** — not because this stage
  executes plans, but because a plan that cannot be carried out is not a plan, and that
  has to fail here rather than in Goal 10.
- **Goal 8's person lookup is proved to find something after a run**, closing the loop
  that goal left open by construction.
- **Every action's exact records and links are asserted individually**, including that
  merging collapses nothing and that catching yourself mid-habit leaves the habit alone.
- **The promotion table is asserted to cover every finding type**, so a type added later
  fails the suite instead of silently defaulting to unpromotable.

## C5. Still Deferred

Unchanged from A6. Three worth restating:

**Nothing is written yet.** Goal 10 executes the plan, in the order the plan gives, after
writing this run's extracted nodes. That ordering is a contract the plan states out loud
via `existing_node_ids` rather than assuming.

**The review queue is Goal 18's.** This stage produces the items and says why each is
waiting; where they queue up, the 40-item cap, snooze and auto-resolve are a different
goal against a table that already exists.

**Two spellings of one person still produce two records.** Alias resolution is the same
fuzzy-matching problem as pattern merging and gets its own goal. What ships here is the
precondition: a person record that exists at all.
