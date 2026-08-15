# Goal 7: Post-Extraction Validation Layer

**Branch:** `goal7`
**Status:** ✅ Complete
**Depends on:** Goal 4 (LLM providers + fakes) ✅, Goal 6 (extraction, validation rules) ✅
**Blocks:** Goal 10 (orchestrator writes the failure edges), Goal 18 (HITL queue surfaces them)

---

## Objective

Goal 6 checks every extracted item and throws away whatever breaks a rule. That was the
right first move — nothing invalid ever reaches the graph — but it means a model that
fumbles one observation costs that observation permanently, and nobody ever finds out.

This goal closes that loop. A rejected item is quoted back to the model with the rule it
broke and asked for again, up to three attempts in total. What still fails is not silently
lost: it becomes an `ObservationNode` carrying `status: EXTRACTION_FAILED`, kept with its
original content so a person can see what could not be read and fix it themselves.

The thing to hold onto while building it: **a retry is a pressure to produce output.** Ask
a model twice for something it has no basis for and it will invent one. Which rules are
worth re-asking, and which must never be re-asked, is the real content of this goal.

---

# SECTION A — LOGIC (please verify)

*Short by design. This is what the goal means; Section B is how it's coded.*

## A1. What Gets Built

| Deliverable | What it is |
|---|---|
| **The correction loop** | Up to 3 attempts per episode. Attempt 1 is Goal 6's reading unchanged; attempts 2 and 3 re-ask only for what failed. Lives in its own module; `extract()` delegates to it and stays the single public name. |
| **The correction prompt** | Quotes the rejected items back, names the rule each broke and what a valid answer looks like, and asks for corrected versions of those items only. |
| **A retryable / terminal split** | Each of the 12 drop reasons is classified. Some are worth re-asking; three must never be, and one of those is a safety rule rather than an efficiency one. |
| **`EXTRACTION_FAILED` nodes** | Observations that fail all three attempts become real nodes with their content preserved, the rejected type recorded, and the rule that refused them. |
| **2 contract additions** | `ExtractionResult.failed_observations` and `ExtractionResult.read_failed`. |
| **Honest attempt counting** | `retry_count` on the result and `extraction_attempt` on each node stop being hardcoded and start recording what actually happened. |

## A2. The Decisions You Made

1. **A retry re-asks only for the items that failed.** The correction prompt quotes them
   back with their rule violations; everything that already validated is untouched. This
   matches the per-observation counting in the spec, costs a fraction of a full call, and —
   the part that matters — means a good finding from attempt 1 can never be re-rolled into
   a worse one on attempt 2. Re-asking for the whole episode would make the output of the
   stage unstable across attempts.

2. **The loop lives inside `extract()`, implemented in its own module.** Goal 10 needs no
   knowledge that retries exist, and there is still exactly one public name. Setting
   `max_extraction_attempts` to 1 in config turns the whole thing off, which is how Goal 6's
   behaviour stays reachable and testable.

3. **A dead call is retried the same as a broken item.** A provider error, an unparseable
   reply and a reply of the wrong shape all mean the same thing — no usable reading — and
   the provider layer only retries transport failures, so nothing else would ever re-ask a
   model that returns unparseable JSON three times running. After the last attempt the
   result says so explicitly, so Goal 10 can mark the episode `SUSPENDED` rather than
   storing an episode that merely looks empty.

4. **Failed items get their own list on the result.** `failed_observations` sits beside
   `observations`, so a failed extraction is structurally incapable of reaching retrieval,
   and Goal 10 knows exactly which nodes need a `failed_extraction` edge without filtering
   on a status field it has to remember to check.

5. **A failed node is typed `CONTEXT`.** The commonest failure is an invented type, so the
   type is the one thing that cannot be trusted. `CONTEXT` is the neutral "what happened"
   label; the type the model actually attempted and the rule that refused it are recorded
   in the node's evidence, so the review card can show both. A new `UNCLASSIFIED` type was
   rejected — it would enter the enum dictionary that gets rendered into the extraction
   prompt, and a model shown an escape hatch uses it.

6. **Stage 0 is left alone.** Goal 5 deferred a shared retry helper here, but Stage 0's
   failures degrade rather than corrupt: a failed split keeps the entry whole, a failed
   score routes it to lighter handling. Retrying there would buy quality, not safety, and
   would re-open a stage whose behaviour is settled.

7. **Ungrounded evidence stays a count, not a failure.** Nothing has changed about why: any
   translated entry is legitimately paraphrased, so this would re-ask constantly on real
   findings. Revisit when the logged rate from actual entries shows what normal looks like.

8. **The HITL queue row is Goal 18's to write.** Recorded precisely in A4-2, because the
   two schemas currently disagree and this goal is not the right place to decide which one
   bends.

## A3. Which Rules May Be Re-Asked

This is the substance of the goal, so it is here rather than only in Section B.

| Drop reason | Re-ask? | Why |
|---|---|---|
| `UNKNOWN_TYPE` | ✅ | The commonest failure and the most recoverable — the model picked a name outside the dictionary and can be shown the real one. |
| `UNKNOWN_ENUM_VALUE` | ✅ | Same shape of mistake in a smaller field. |
| `SIGNAL_FLOOR` | ✅ | The model contradicted itself: it chose a type that marks unusual weight, then called it ordinary. Worth one more look. |
| `UNKNOWN_STEP_TYPE` | ✅ | One unreadable step costs the whole sequence, so recovering it is worth a call. |
| `EMPTY_CONTENT` | ✅ | An item that arrived blank may simply have been truncated. |
| `EXCLUDED_TYPE` | ❌ | The type needs audio and this stage has a transcript. No number of attempts changes that. |
| `TYPE_NOT_ALLOWED_HERE` | ❌ | The wrong reading was run over a thin entry. Re-asking would repeat the mistake. |
| `CHAIN_TOO_SHORT` | ❌ | A one-step sequence is a finding, not a chain. Asking again mostly invites the model to pad it into one. |
| `OVER_LIMIT` | ❌ | Nothing was wrong with the item; there were simply too many. |
| **`QUOTE_NOT_FOUND`** | ❌ **never** | **The one that matters.** This rule fires when a thin entry produced a feeling the person never put into words. A retry saying "give me the quote" is a direct instruction to produce one — the model will oblige, and the fabricated quote will pass the check on the second attempt. Retrying this rule would convert the strongest guard in the pipeline into a mechanism for defeating it. |
| `UNKNOWN_PERSON` | n/a | Never dropped the item in the first place; only the name was removed. |

**Terminal rejections are discarded, not failed.** They do not become `EXTRACTION_FAILED`
nodes, because those exist to ask a person for help and there is nothing a person can do
about a category that requires audio.

## A4. Where the Specs Disagree With Themselves

1. **Three re-extractions, or three attempts?** `Reconciliation.md` says an observation "may
   be re-extracted at most **3 times**" and, in the same paragraph, that "on the **third
   failure**" it is written as `EXTRACTION_FAILED`. Read literally those are four attempts
   and three. **Three total attempts wins** — it agrees with "third failure", and with
   `ObservationNode.extraction_attempt`, which counts attempts rather than repeats. The
   doc gets one sentence fixed so the next reader is not left to choose.

2. **A failed extraction cannot be queued as written.** `hitl_queue.audit_node_id` is
   `NOT NULL` and unique, but an extraction failure never reaches reconciliation and so has
   no `DecisionAuditNode` — and `DecisionAuditNode` cannot be honestly built for one, since
   it requires an action, a confidence and a rollback pointer, none of which exist here.
   Meanwhile `HitlEntryType.EXTRACTION_FAILED` already exists and expects to be used. Per
   A2-8 this is flagged, not fixed: Goal 18 resolves it with the queue UI in front of it.
   Goal 7's job is to make sure the failed nodes exist to be queued.

3. **Only observations can fail.** The `failed_extraction` edge is defined as
   `EpisodeNode → ObservationNode`, so there is nowhere for a failed *event* or a failed
   *chain* to go. Those are discarded after their attempts are spent, with a warning. Not
   worth a new edge table in this goal, but it means the failure record is incomplete by
   construction, and that should be a known limitation rather than a surprise in Goal 12.

4. **`DropRule.REJECTED_BY_SCHEMA` is dead.** Declared in Goal 6, never raised. This goal
   either uses it — a corrected item that still will not build is exactly that case — or
   deletes it. B4 uses it.

## A5. What This Goal Deliberately Leaves Undone

| Deferred | To | Why |
|---|---|---|
| Writing the `failed_extraction` edge | Goal 10 | Stages are pure functions. Goal 7 produces the nodes; the orchestrator is the only thing that writes. |
| The HITL queue row for a failure | Goal 18 | A4-2. The schema conflict needs the queue's owner to resolve it. |
| Failure records for events and chains | Not scheduled | A4-3. No edge exists for them. |
| Retry in Stage 0 | Not scheduled | A2-6. Its fallbacks degrade safely. |
| Tightening ungrounded evidence | After real-entry data | A2-7. |
| Re-embedding or re-reconciling a recovered item | Goals 8–9 | A corrected observation is an ordinary observation by the time it leaves here. |

## A6. The Risk Worth Naming

**Retrying is asking a model to try harder, and models comply.** Every rule in A3's ❌
column is there because a second ask would produce a *confident* answer rather than a
correct one — most sharply for `QUOTE_NOT_FOUND`, where the correction prompt would
literally be an instruction to produce the missing quote.

So the loop is built to be provably narrow:

1. **The retryable set is a frozen table**, not a rule expressed in prose that the next
   person reinterprets. Adding a reason to it is a visible diff with a test attached.
2. **The correction prompt never restates the item's content back as a target.** It shows
   the item, names the field that was wrong, and asks for that field — so the model is
   correcting a label, not re-arguing the finding.
3. **A recovered item is marked with the attempt that produced it.** If corrected items
   later turn out to be systematically worse than first-attempt ones, `extraction_attempt`
   is what makes that visible instead of a suspicion.

The second-order risk is cost: three attempts on every episode of a bad day is three times
the spend. Attempts are capped, terminal rules end the loop immediately, and the loop stops
the moment nothing retryable is left — so the common case stays exactly one call.

## A7. Definition of Done

- [ ] A clean reading still costs exactly one call, and `retry_count` is 0.
- [ ] A reply with one invented type costs a second call, recovers the item, and reports
      `retry_count: 1` with `validation_passed: True`.
- [ ] The recovered item carries `extraction_attempt: 2`; its untouched siblings carry 1.
- [ ] An item that fails all three attempts appears in `failed_observations`, never in
      `observations`, with `status: EXTRACTION_FAILED` and its original content intact.
- [ ] The rejected type name and the rule that refused it are both recoverable from the
      failed node.
- [ ] **A thin entry whose feeling had no quote is never re-asked** — one call, no
      correction, no fabricated quote. Asserted directly.
- [ ] A `PROSODY_SIGNAL` observation costs no extra call and produces no failed node.
- [ ] A provider that fails three times produces `read_failed: True`, an empty result, and
      nothing invented.
- [ ] A provider that fails once then succeeds produces a normal result.
- [ ] The loop stops as soon as nothing retryable remains — a reply mixing a retryable and
      a terminal failure costs one correction, not two.
- [ ] `max_extraction_attempts: 1` reproduces Goal 6's behaviour exactly.
- [ ] The correction prompt is asserted to name the rule and the field, and **not** to
      contain an instruction to produce evidence.
- [ ] Journal text never appears in a log line unless `LUMEN_LOG_PROMPTS=true` — including
      in the new per-attempt lines.
- [ ] ≥90% coverage on `lumen/pipeline/extraction/`.

---

# SECTION B — LOW-LEVEL DESIGN

## B1. Files

```
lumen/pipeline/extraction/
├── retry.py            — NEW: the loop, the retryable table, the merge
├── prompts.py          — + CORRECTION_PROMPT and its renderers
├── contracts.py        — + RejectedItem; DropRule.REJECTED_BY_SCHEMA put to use
├── validation.py       — + rejected items on the report; validate_corrections()
├── assembly.py         — + failed_observation(); extraction_attempt threaded through
└── stage.py            — delegates to the loop; reports attempts honestly

lumen/tests/
├── test_extraction_retry.py       — NEW
├── test_extraction_correction.py  — NEW: the prompt and the retryable table
└── (5 existing extraction suites extended)
```

## B2. Contract Additions

```python
# lumen/schemas/pipeline.py

class ExtractionResult(PipelineDTO):
    ...
    failed_observations: list[ObservationNode] = Field(default_factory=list)
    read_failed: bool = False
```

Both default so every existing test and caller is unaffected. `read_failed` is named to
match the field already on the stage's closing log line.

```python
# lumen/pipeline/extraction/contracts.py

class RejectedItem(BaseModel):
    """One item that failed a rule, kept whole so it can be asked about again."""
    kind: Literal["observation", "event", "chain"]
    index: int
    rule: DropRule
    detail: str = ""
    payload: ExtractedObservation | ExtractedEvent | ExtractedCausalChain
    attempts: int = 1
```

**`RejectedItem` and `DropRecord` are deliberately separate.** `DropRecord` carries no
content and is what gets logged; `RejectedItem` carries the whole item and never leaves
memory. Goal 6 established that the drop note must not quote the person's writing, and the
arrival of a retry loop is exactly the pressure that would erode that rule if one structure
served both purposes.

## B3. `config.py` Addition

```python
max_extraction_attempts: int = _env_int("LUMEN_MAX_EXTRACTION_ATTEMPTS", 3)
```

Three total: the first reading plus at most two corrections (A4-1). Setting it to 1
disables the loop, which is how the DoD checks Goal 6's behaviour is still reachable.

## B4. `retry.py` — The Loop

```python
RETRYABLE_RULES: frozenset[DropRule] = frozenset({
    DropRule.UNKNOWN_TYPE,
    DropRule.UNKNOWN_ENUM_VALUE,
    DropRule.SIGNAL_FLOOR,
    DropRule.UNKNOWN_STEP_TYPE,
    DropRule.EMPTY_CONTENT,
})

def read_with_corrections(payload, *, provider, limits) -> ExtractionOutcome
```

The table is the whole safety argument, so it is a module-level constant with a test
asserting its exact membership — a rule joining it is then a visible diff, not a drift.

```
1.  outcome = first reading                            (Goal 6, unchanged)
2.  while attempts < max and outcome has retryable rejections:
3.      ask for corrections to those items only
4.      validate the corrections
5.      merge: newly-valid items join the kept set
6.             still-failing items keep their place, attempts + 1
7.  survivors → nodes;  still-rejected retryables → failed nodes
8.  terminal rejections → discarded, logged, no node
```

A correction that comes back and still will not build is recorded as
`DropRule.REJECTED_BY_SCHEMA` (A4-4) and counts as that item's attempt.

**The dead-call path shares the loop.** When a reading returns nothing at all, there is
nothing to correct, so the retry re-issues the original request rather than a correction.
After the last attempt the outcome carries `read_failed`.

## B5. The Correction Prompt

```
Some of what you returned could not be used. Here is each item and what was
wrong with it. Return corrected versions of these items only — do not add
new ones, and do not change anything not named below.

ITEM 1 — the type "VIBES" is not in the list. Choose the closest type from
the list, or leave this item out if none fits.
  {the item as returned}
...
```

Three properties it must have, each asserted by a test:

- **It names the field, not the finding.** The model is correcting a label; re-arguing the
  content is what produces a second, different finding rather than a corrected one.
- **It offers "leave it out" as a valid answer.** Without that, every correction is a
  demand for output, which is exactly how a retry becomes a fabrication.
- **It never asks for evidence.** Nothing in the retryable set is about evidence, and the
  phrase must not appear (A6).

The type dictionary is re-rendered into the correction prompt when a type rule was broken,
because the reason it was broken is usually that the model did not use the list.

## B6. Validation Changes

`ValidationReport` gains `rejected: tuple[RejectedItem, ...]`. Every existing `collector.drop(...)`
call site additionally records the raw item, so the two structures are produced together
and cannot fall out of step.

`validate_corrections(response, context, *, originals)` validates a correction reply against
the same rules, and matches each returned item back to the rejection it answers by position.

**Nothing about the rules themselves changes.** A corrected item is judged by exactly the
same code as a first-attempt one; there is no relaxed second pass, because a rule that can
be worn down by asking twice is not a rule.

## B7. Assembly Changes

```python
def failed_observation(self, rejected: RejectedItem) -> ObservationNode
```

- `type` = `CONTEXT` (A2-5), `status` = `EXTRACTION_FAILED`, `signal_strength` = `STANDARD`.
- `content` = the content the model produced, unchanged — this is what a person will read.
- `raw_evidence` = the rejected type name and the rule that refused it, so the review card
  can show what was attempted without a second table to join against.
- `extraction_attempt` = attempts spent.

`extraction_attempt` also stops being hardcoded on successful nodes: it records the attempt
that produced the item, so a later analysis can compare first-attempt findings against
recovered ones.

## B8. Telemetry

One line per attempt beyond the first: episode id, attempt number, how many items were
re-asked, which rules, and how many came back valid. The closing line gains `retry_count`,
`failed` and `read_failed`.

Counts and rule names only. The correction prompt contains the person's writing and the
same `LUMEN_LOG_PROMPTS` rule applies to it as to every other prompt.

## B9. Doc Amendments Required

Applied before coding, as Goals 4–6 did.

1. `Reconciliation.md` — resolve "3 re-extractions" against "third failure" in favour of
   three total attempts (A4-1); note that only observations can carry a failure record
   (A4-3).
2. `Architecture.md` — same attempt-count wording; state that terminal rules are discarded
   rather than retried, and name `QUOTE_NOT_FOUND` as never-retried with the reason.
3. `Technical_HLD.md` §5 — `failed_observations` and `read_failed` on `ExtractionResult`.
4. `Schema.md` — note against the `failed_extraction` edge that the node is typed `CONTEXT`
   with the attempted type preserved in evidence.
5. **Flagged, not fixed:** the `hitl_queue.audit_node_id` conflict (A4-2) is Goal 18's.
6. `Master_Plan.md` — tick Goal 7 and record the deviations.

## B10. Test Plan (~70 tests)

| File | Covers |
|---|---|
| `test_extraction_correction.py` | The retryable table's exact membership; every terminal rule proved to cost no extra call; the prompt names rule and field, offers omission, and never mentions evidence; the dictionary is re-rendered only for type failures. |
| `test_extraction_retry.py` | Recovery on attempt 2 and on attempt 3; giving up after 3; `retry_count` and `extraction_attempt` accuracy; the loop stopping early when only terminal rejections remain; mixed retryable + terminal in one reply; a correction that returns fewer items than asked; a correction that returns something new (ignored); `max_extraction_attempts=1` reproducing Goal 6. |
| `test_extraction_stage.py` (extend) | `failed_observations` populated and disjoint from `observations`; `read_failed` on total failure and false otherwise; a provider failing once then succeeding. |
| `test_extraction_assembly.py` (extend) | The failed node's type, status, preserved content, and recoverable attempted-type/rule. |
| `test_extraction_validation.py` (extend) | `rejected` carries the whole item where `drops` carries none of it; corrected items judged by the identical rules. |
| `test_extraction_trace.py` (extend) | Per-attempt lines carry the trace; the correction path leaks no journal text; the failed node's content never reaches a log. |
| `test_schemas_pipeline.py` (extend) | The two new fields and their defaults. |

## B11. Build Order

0. Doc amendments (B9).
1. `pipeline.py` fields + `config.py` attempt cap — contracts first.
2. `contracts.py`: `RejectedItem`, and put `REJECTED_BY_SCHEMA` to work.
3. `validation.py`: record rejections alongside drops; `validate_corrections()`.
4. `prompts.py`: the correction template and its renderers.
5. `retry.py`: the retryable table, then the loop, then the merge.
6. `assembly.py`: `failed_observation()`, attempt threading.
7. `stage.py`: delegate, report honestly, extend the log line.
8. `Master_Plan.md` and Section C.

---

# SECTION C — RESULTS

**Status:** ✅ Complete
**Tests:** 1253 passing (1151 before this goal + 102 new), 9 live tests still deselected.
**Coverage:** **100%** on `lumen/pipeline/` (all 16 modules), `lumen/config.py`, and
`lumen/schemas/pipeline.py`.

## C1. What Was Built

| Module | Contents |
|---|---|
| `pipeline/extraction/retry.py` | `RETRYABLE_RULES`, the loop, the re-read path, the merge, and the settlement of whatever is left over. |
| `pipeline/extraction/prompts.py` | `CORRECTION_PROMPT`, one explanation per retryable rule, and the rule that decides when the type list is repeated. |
| `pipeline/extraction/validation.py` | `RejectedItem`s recorded beside drop notes; `validate_corrections()` running corrected items through the identical checkers. |
| `pipeline/extraction/assembly.py` | `failed_observation()`; the attempt number threaded onto every finding. |
| `pipeline/extraction/contracts.py` | `RejectedItem`, `DropRule.NOT_CORRECTED`, and three more fields on the outcome. |

## C2. Deviations From the Plan

1. **An attempt that recovers nothing ends the loop.** Not in B4. A model that returned an
   unusable answer once returns it again, so the third call only pays to watch it happen.
   This means an item can be given up on after two attempts rather than three — the cap is
   still three, but progress is now the condition for spending the next one.

2. **`validation_passed` is true again after a full recovery.** The plan's DoD said so and
   the first implementation did not: it kept reporting false because the first attempt's
   drops were still on the record. An item refused once and accepted next cost nothing in
   the end, and a flag that cannot tell that apart from a real loss is useless. Added
   `abandoned` to the outcome, so the flag now means *something was lost for good* rather
   than *something went wrong at some point*.

3. **`DropRule.REJECTED_BY_SCHEMA` was deleted, not used.** A4-4 expected to put it to
   work, but no reachable path produces it — a correction either builds or breaks one of
   the existing rules. It was replaced by `NOT_CORRECTED`, which describes something that
   actually happens: the model was asked to fix an item and returned nothing for it.

4. **A rejection keeps its original rule.** B2 had `again()` overwriting the rule with
   whatever went wrong most recently. That lost the fact that decided whether the item was
   worth re-asking at all, so an item the model declined to fix stopped being recognised as
   recoverable and vanished instead of becoming a failure record. The original rule is now
   permanent and `last_rule` carries what happened since; both reach the failed node.

5. **The naming factory is created by the loop and passed into the reading.** Each attempt
   was otherwise building its own and handing out names the previous attempt had already
   used. `passes.read_reflection()` takes an optional factory for this reason.

6. **The anchor can be minted late.** An episode whose first reading came back empty is
   never anchored, since there is nothing to anchor. If a correction then recovers
   something, `_ensure_anchor` mints it at the end — otherwise an episode rescued by its
   second attempt would be the one episode in the graph where a belief could never be
   recorded as changing.

7. **The thin path is never corrected at all.** Stated in A2 only for its one rule; in the
   code the whole path is excluded. It is a single cheap call with two possible outputs,
   and the only rule it can break is the one that must never be re-asked.

## C3. Things Caught While Implementing

1. **An item the model declined to fix disappeared.** Overwriting the rule with
   `NOT_CORRECTED` moved it out of the retryable set, so the code that decides what becomes
   a failure record no longer recognised it. The person would have been shown nothing at
   all for the item most likely to need their attention. Found by a test written from the
   DoD, fixed by C2-4.

2. **The "never asks for evidence" test was checking the wrong text.** It failed because the
   refused item is echoed back as JSON and its field names include `raw_evidence`. That is
   the model's own words being shown back to it, not a request. The test now checks only the
   template and the problem explanations — the parts that actually instruct.

3. **A wrong-shaped reply is path-specific, again.** The same lesson as Goal 6, in a new
   place: `{"observations": "not a list"}` breaks a reflection reply and is a perfectly
   valid thin-capture reply, because each path ignores fields it did not ask for.

## C4. What the Tests Cover

102 new tests across 2 new files plus 6 extended ones. The ones worth knowing about:

- **The retryable set is asserted by exact equality**, not member by member, so widening it
  is a deliberate act with a failing test in the way.
- **`QUOTE_NOT_FOUND` is asserted absent from the retryable set, and the thin path is
  asserted to cost exactly one call** — the two halves of the guarantee that a feeling
  nobody stated can never be talked into existence by a second question.
- **Every terminal rule is proved to cost no extra call**, so a rule quietly becoming
  retryable shows up as a call count rather than as a subtle behaviour change.
- **Names minted across attempts are asserted disjoint**, which is the bug C2-5 fixes.
- **An episode rescued by a correction is asserted to have an anchor** (C2-6).
- **The failed node is asserted to keep the person's words, the type the model attempted,
  and the rule that refused it** — everything a review card needs.
- **The private sentence is asserted absent from the logs on the correction path too**,
  including from the failed node's own content, which is now the easiest thing in the stage
  to leak because it is deliberately preserved.

## C5. Still Deferred

Unchanged from A5. Two worth restating:

**The HITL queue row still cannot be written.** `hitl_queue.audit_node_id` is `NOT NULL` and
an extraction failure has no decision behind it. The failed nodes now exist and carry
everything a queue item would need; only the row is blocked, and the conflict is written
into `Schema.md` where Goal 18 will meet it.

**Only findings can fail.** A refused event or sequence still has nowhere to be recorded, so
it is logged and discarded. Asserted by test rather than left to be discovered, but the
failure record remains incomplete by construction.
