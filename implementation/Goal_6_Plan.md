# Goal 6: Stage 1 — Microextraction Core

**Branch:** `goal6`
**Status:** ✅ Complete
**Depends on:** Goal 2 (node models, enums, DTOs) ✅, Goal 4 (LLM providers + fakes) ✅, Goal 5 (Stage 0) ✅
**Blocks:** Goal 7 (validation retry), Goal 8 (retrieval), Goal 10 (orchestrator)

---

## Objective

Stage 1 is where text becomes graph. It reads one cleaned episode, in complete isolation
from the user's history, and returns typed nodes: observations tagged from a fixed
dictionary, the events that anchored them, and the cause-and-effect chains running through
them.

Its constraint is blindness. It is not allowed to know what the user has believed before,
because a model that has seen the master list stops reading and starts matching — the
Anchoring Bias the Late Binding model exists to prevent. Everything it produces must be
defensible from this episode's text alone.

Its second job is to be the last honest step. Stage 3 will treat whatever arrives as the
user's own words. An invented observation here becomes a permanent belief there.

---

# SECTION A — LOGIC (please verify)

*Short by design. This is what the goal means; Section B is how it is coded.*

## A1. What Gets Built

| Deliverable | What it is |
|---|---|
| **`extract()`** | One pure function: `MicroextractionInput` in, `ExtractionResult` out. No DB access, no history, one provider injected. |
| **Two extraction paths** | `REFLECTION` episodes get one THINKING call over the full enum dictionary. `RAW_CAPTURE` episodes get one LIGHTWEIGHT call for surface topic and stated feeling. Path chosen by `entry_class`, which Stage 0 already decided. |
| **The enum catalog** | The 48 observation types and their one-line definitions, rendered into the prompt from a single module. A test proves the catalog and the `ObservationType` enum can never drift apart. |
| **Assembly** | Raw model output → real `ObservationNode` / `EventNode` / `SessionNode` / `CausalChainNode` / `CausalStepNode` instances, with ids minted, timestamps set, and defaults applied. |
| **Validation** | The rule set from `Architecture.md` enforced in code, per item. An item that breaks a rule is dropped with a named reason; the rest of the extraction survives. |
| **The causal anchor** | Exactly one synthetic `SessionNode` per `REFLECTION` episode, minted in code, so Goal 9 always has something to anchor an EVOLVE against. |
| **2 contract additions** | `MicroextractionInput` (new stage-boundary DTO), `PreprocessingResult.co_created_spans` (a Stage 0 amendment). |

## A2. The Decisions You Made

1. **Goal 6 validates; Goal 7 retries.** Every extracted item is checked against the real
   node models and the documented rules before it leaves this stage — unknown observation
   type, missing signal strength, a mandatory-HIGH type marked `STANDARD`, an unknown causal
   step type. A failing item is **dropped**, counted, and logged with the rule it broke.
   Goal 6 therefore never emits an invalid node, and never emits a `retry_count` above 0.
   Goal 7 adds the correction-prompt loop, `status: EXTRACTION_FAILED`, and the
   `failed_extraction` edge on top of an unchanged output shape.

2. **`RAW_CAPTURE` extracts `CONTEXT` plus explicitly-stated `EMOTION`.** This resolves the
   conflict Goal 5 flagged and left open. `Microextraction.md`'s type list wins, but under
   `Preprocessing.md`'s "no emotional inference" rule: an `EMOTION` observation is produced
   only when the person named a feeling in their own words. The rule is enforced
   mechanically, not by asking the prompt nicely — the model must return the verbatim quote
   alongside the emotion, and an emotion whose quote is not found in the episode text is
   dropped (B7).

3. **One LLM call per episode, with the role chosen by `entry_class`.** A `REFLECTION`
   episode gets one THINKING call producing observations, events, and causal chains
   together — the chains describe the same moments as the observations, and splitting them
   across calls means two independent readings of one text that then disagree. A
   `RAW_CAPTURE` episode gets one LIGHTWEIGHT call, because paying a reasoning model to
   summarize an entry the quality gate already judged thin defeats the point of the gate.

4. **The causal anchor is minted in code, not asked for.** The model extracts `EventNode`s
   when the person describes something that happened. Independently, Stage 1 always mints
   one `SessionNode` per `REFLECTION` episode — the act of reflecting itself, which is
   exactly what `Schema.md` §3.1 describes it as. Schema rule 5 forbids a belief from
   evolving without an intervening Event or Session; making that anchor's existence depend
   on a model's judgement about what counts as an event would make a structural guarantee
   probabilistic. `RAW_CAPTURE` episodes get no anchor, because they never reach
   reconciliation.

5. **Open loops are extracted as observations, not as `OpenLoopNode`s.** `ObservationType`
   already has `OPEN_LOOP`, and `ExtractionResult` has no field for loop nodes. Deciding
   that an unresolved question is a *standing* investigation rather than a passing one
   requires knowing whether it has come up before — which is history, which Stage 1 is
   forbidden to see. The node gets promoted during reconciliation. `Architecture.md` is
   amended to say so (A4-3).

6. **`CO_CREATED` provenance gets a real source.** Stage 0's conversation pass already
   detects which user turns adopted an AI framing, then throws the detail away when it rolls
   the dialogue into a summary — so `Microextraction.md`'s provenance rule currently has no
   input. Stage 0 is amended to also return the adopted framings verbatim, and Stage 1 marks
   observations built on them as `CO_CREATED`.

   > **One refinement on the option as worded, please check:** the spans are carried on
   > `PreprocessingResult`, not on `PreprocessedEpisode`. Segmentation happens *after* the
   > conversation pass, so a span cannot be reliably attributed to one episode at the point
   > it is produced — and this is exactly how `coreference_map` is already handled: session
   > scope, shared by every episode. `MicroextractionInput` carries both down together.

7. **The stage boundary is a new `MicroextractionInput` DTO.** `PreprocessedEpisode` has no
   date, no coreference map, and no source modality, while every node this stage builds
   requires `occurred_at`. The wrapper carries the episode plus the session-level facts it
   needs. `PreprocessedEpisode` stays exactly as Goal 5 shipped it, and the "one Pydantic
   model in, one out" rule holds.

8. **Node ids are scoped by episode.** `obs_2026_06_11_01_003` — date, episode index, then a
   per-episode counter. Two episodes on the same day are extracted by two independent calls
   that both start counting at 1; without the episode segment they would mint the same ids.
   A new `make_scoped_node_id()` sits beside the existing helper in `ids.py`.

## A3. What Stage 1 Is Not Allowed To Do

Worth stating as rules rather than as prose, because each one is a test.

- **No history.** No graph handle, no vector handle, no candidate list. The function
  signature has nowhere to put one.
- **No invention of people.** A `person_ref` naming someone who appears neither in the
  coreference map nor in the episode text is dropped. A hallucinated name here becomes a
  hallucinated `PersonEntityNode` in Goal 9.
- **No `PROSODY_SIGNAL`.** It is derived from audio, and this stage only ever sees a
  transcript. Any such observation the model produces is dropped — the deferral is enforced,
  not just documented.
- **No promotion on failure.** If the call fails, the episode yields zero content nodes and
  `validation_passed: False`. Nothing is invented to fill the gap, and nothing already
  written is lost — the episode's full text is on the `EpisodeNode` Goal 10 writes, so a
  Goal 7 retry can recover everything.

## A4. Where the Specs Disagree With Themselves

Found while reading. Each needs a doc fix, listed in B11.

1. **How many re-extraction attempts.** `Architecture.md` says re-extraction is attempted
   **once**, then the entry goes to HITL. `Reconciliation.md` says an observation may be
   re-extracted **at most 3 times**, then gets `EXTRACTION_FAILED` and a `failed_extraction`
   edge. `Master_Plan.md` Goal 7 says 3. Two-to-one: 3 attempts. Not this goal's code, but
   `Architecture.md` gets fixed now so Goal 7 does not inherit the contradiction.

2. **Which types carry a mandatory signal floor.** `Architecture.md` lists three
   (`SUPPRESSED_EMOTION_SURFACING`, `METACOGNITIVE_INTERRUPT`, `METACOGNITIVE_BREAKTHROUGH`).
   `Microextraction.md`'s own type definitions state the floor for three more
   (`PROSODY_SIGNAL`, `IDENTITY_FUSION_STATE`, `EXISTENTIAL_REFLECTION`), and the shipped
   `HIGH_SIGNAL_REQUIRED_TYPES` has all six. The code is right; `Architecture.md`'s list is
   stale.

3. **Who creates `OpenLoopNode`.** `Architecture.md` says the Microextraction LLM must
   extract one directly. `ExtractionResult` has no field for it, and the decision needs
   history. Resolved by A2-5; the doc is amended.

4. **`RAW_CAPTURE` observation types.** The conflict Goal 5 flagged. Resolved by A2-2; both
   docs are amended to the same wording.

## A5. What This Goal Deliberately Leaves Undone

| Deferred | To | Why |
|---|---|---|
| Re-extraction retry loop, `EXTRACTION_FAILED`, `failed_extraction` edge | Goal 7 | A2-1. Goal 6 drops bad items; Goal 7 asks again for them. |
| `PersonEntityNode` creation, `mentions` edges | Goal 9 / 10 | Stage 1 emits canonical *names*; turning a name into a node is a cross-entry identity decision. |
| `OpenLoopNode` promotion, `open_loop_ref` back-fill | Goal 9 | A2-5. Needs history. |
| Reflection-prompt follow-up on `RAW_CAPTURE` | Goal 18 | Stage 0 already produces the prompts; nothing reads them until the review UI. |
| `PROSODY_SIGNAL` | Voice ingestion goal | A3. Needs audio features, not a transcript. |
| `LessonNode`, `AdoptedPrincipleNode` | Goal 9 / 17 | Lessons arrive here as `LESSON` observations and `LESSON` chain steps; promoting one to a standing lesson is a macro judgement. |
| Embedding the extracted nodes | Goal 8 | Stage 1 produces nodes; Stage 2 is what needs them in vector space. |

## A6. The Risk Worth Naming

**The failure mode of this stage is confident invention, and it is invisible.** A dropped
observation announces itself in the counts. A *fabricated* one — a belief the user never
held, phrased in their register, tagged plausibly — passes every schema check and lands in
permanent history. No validator can catch it, because it is well-formed.

Three defences, all mechanical rather than prompt-based:

1. **Every observation must carry `raw_evidence`** — verbatim spans from the episode. An
   observation whose evidence quotes cannot be found in the source text is counted and
   logged as *ungrounded*. Goal 6 logs the rate rather than dropping on it (paraphrase after
   translation is legitimate); the metric is what tells us whether Goal 7 should tighten it
   into a rejection.
2. **The named-person guard** (A3) turns the most damaging class of invention into a hard
   drop.
3. **The `RAW_CAPTURE` quote requirement** (A2-2) means the one path with the least text to
   work from cannot infer a feeling at all.

The second-order risk is silent thinning: a prompt that drifts, or a model that returns two
observations where it once returned nine, produces a green pipeline and a hollow graph. The
closing log line therefore carries per-episode counts and the drop reasons, and the DoD
requires a test that a rich worked example yields observations across at least three
distinct types.

## A7. Definition of Done

- [ ] `extract()` is pure — no DB, no graph, no vector store, no history; a test asserts
      `lumen/pipeline/extraction/` imports nothing from `lumen.operational`, `lumen.graph`,
      or `lumen.vector`.
- [ ] A `REFLECTION` episode costs exactly one THINKING call; a `RAW_CAPTURE` episode costs
      exactly one LIGHTWEIGHT call and never a THINKING one.
- [ ] `Microextraction.md`'s worked causal-chain example round-trips: six steps, correct
      types and order, `step_count` matching.
- [ ] An unknown observation type drops that observation and keeps every valid sibling.
- [ ] `SUPPRESSED_EMOTION_SURFACING` returned as `STANDARD` is dropped, and the log names the
      rule.
- [ ] A `RAW_CAPTURE` episode yields at most one `CONTEXT` and one `EMOTION`, no causal
      chains, and no `SessionNode`.
- [ ] An `EMOTION` whose supporting quote is absent from the episode text is dropped.
- [ ] A `person_ref` naming someone absent from both the coreference map and the text is
      dropped.
- [ ] A `PROSODY_SIGNAL` observation is always dropped, on both text and voice input.
- [ ] Every `REFLECTION` episode produces exactly one `SessionNode`, including when the model
      extracted several events.
- [ ] Two episodes from the same day produce disjoint node id sets.
- [ ] A total provider failure yields zero content nodes, `validation_passed: False`, a
      warning — and nothing invented.
- [ ] The enum catalog covers `ObservationType` exactly: no missing member, no extra key.
- [ ] Journal text never appears in a log line unless `LUMEN_LOG_PROMPTS=true`.
- [ ] Every emitted DTO carries the ambient `trace_id`; the whole stage runs offline against
      `FakeLLMProvider`.
- [ ] ≥90% coverage on `lumen/pipeline/extraction/`.

---

# SECTION B — LOW-LEVEL DESIGN

## B1. Files

```
lumen/pipeline/extraction/
├── __init__.py        — public surface: extract()
├── stage.py           — extract(): routes by entry_class, assembles ExtractionResult
├── contracts.py       — permissive LLM response models + internal result models
├── catalog.py         — the ObservationType dictionary rendered for the prompt
├── prompts.py         — REFLECTION and RAW_CAPTURE templates + system instruction
├── passes.py          — the two extraction calls and their fallbacks
├── assembly.py        — raw response → node models (ids, timestamps, defaults)
└── validation.py      — the rule set; keep/drop with a named reason

lumen/tests/
├── test_extraction_catalog.py
├── test_extraction_validation.py
├── test_extraction_assembly.py
├── test_extraction_passes.py
├── test_extraction_stage.py
└── test_extraction_trace.py
```

**Deviation from `Master_Plan.md`**, which names `lumen/pipeline/extraction.py`. Same call
Goal 5 made, for the same reason: the enum catalog alone is ~50 entries, the validation rule
set is a dozen independent predicates, and assembly is id policy plus timestamp policy. One
file lands near 900 lines. `extract()` stays the single public name.

## B2. Contract Additions

```python
# lumen/schemas/pipeline.py

class MicroextractionInput(PipelineDTO):
    """Everything Stage 1 needs about one episode, and nothing about history."""

    episode: PreprocessedEpisode
    coreference_map: CoreferenceMap
    entry_id: str = Field(min_length=1)          # the session id
    event_date: date
    occurred_at: datetime                        # logical event time for this episode
    source_modality: SourceModality
    session_label: str = ""
    co_created_spans: list[str] = Field(default_factory=list)


class PreprocessingResult(PipelineDTO):
    ...
    co_created_spans: list[str] = Field(default_factory=list)
```

`co_created_spans` defaults to empty, so every Goal 5 test keeps passing and a monologue —
which has no AI turns and therefore no adopted framings — is unaffected.

**Stage 0 amendment (small, contained):** `ConversationResponse` gains
`co_created_spans: list[str]` (the AI phrasings the user explicitly took up, verbatim);
`ConversationResult` carries them through; `preprocess()` puts them on the result. The
existing `co_created_message_ids` stays — it is per-turn audit, the spans are the content.
The conversation prompt gains one instruction and one output field. Fallback: empty list,
which degrades to everything being `USER_GENERATED` — the conservative direction, since
`CO_CREATED` carries a 0.5 trust weight and marking a user's own words as AI-derived would
under-rank their own history.

## B3. `config.py` Addition

```python
@dataclass(frozen=True)
class PipelineConfig:
    ...  # Goal 5 fields unchanged
    max_observations_per_episode: int = _env_int("LUMEN_MAX_OBSERVATIONS", 25)
    max_causal_chains_per_episode: int = _env_int("LUMEN_MAX_CAUSAL_CHAINS", 5)
    max_causal_steps_per_chain: int = _env_int("LUMEN_MAX_CAUSAL_STEPS", 12)
```

Caps, not targets. They exist so one runaway response cannot write two hundred nodes for one
paragraph. Overflow is truncated (keeping the first N, which the prompt asks to be ordered by
significance) and logged with the count dropped.

## B4. `ids.py` Addition

```python
def make_scoped_node_id(prefix: str, occurred_at: date, episode_index: int, seq: int) -> str:
    """obs_2026_06_11_01_003 — date, episode within the day, ordinal within the episode."""
```

Matches the existing `SEMANTIC_ID_RE`. `episode_index` is `%02d`, `seq` is `%03d`. Counters
are per-prefix and per-episode, held in a small `_IdMinter` in `assembly.py` so the sequence
logic exists once rather than in five places.

## B5. `catalog.py` — The Enum Dictionary

```python
OBSERVATION_TYPE_DEFINITIONS: dict[ObservationType, str]   # one line each, all 48
EXCLUDED_TYPES: frozenset[ObservationType]                 # {PROSODY_SIGNAL}
RAW_CAPTURE_TYPES: frozenset[ObservationType]              # {CONTEXT, EMOTION}

def render_type_dictionary(*, exclude: frozenset[ObservationType]) -> str
```

The definitions live in code, not parsed from the doc at runtime — docs are not a runtime
dependency. The drift risk that creates is closed by a test asserting
`set(OBSERVATION_TYPE_DEFINITIONS) == set(ObservationType)`, so adding an enum member without
its definition fails the suite.

The rendered dictionary is roughly 2,000 prompt tokens on the `REFLECTION` path. That cost is
the entire point of the enum dictionary: it is what prevents the model from inventing
category names and fragmenting the graph. It is not trimmed to the "common" types, because
the rare types — `CONTEXT_SEVERANCE`, `BIOGRAPHICAL_GAP`, `IDENTITY_FUSION_STATE` — are the
high-value ones, and a model that has not been shown them will file them under `EMOTION`.

## B6. `contracts.py` — Permissive In, Strict Out

```python
class ExtractedObservation(BaseModel):
    type: str = ""                              # str, deliberately — see below
    content: str = ""
    provenance: str = "USER_GENERATED"
    extraction_signal_strength: str = "STANDARD"
    extraction_confidence: str = "STANDARD"
    person_ref: str | None = None
    raw_evidence: list[str] = Field(default_factory=list)

class ExtractedEvent(BaseModel):
    event_summary: str = ""
    signal_strength: str = "STANDARD"
    person_refs: list[str] = Field(default_factory=list)
    raw_evidence: list[str] = Field(default_factory=list)

class ExtractedCausalStep(BaseModel):
    step: int = 0
    type: str = ""
    content: str = ""
    branch_id: str | None = None

class ExtractedCausalChain(BaseModel):
    chain_summary: str = ""
    is_anticipatory: bool = False
    causal_chain: list[ExtractedCausalStep] = Field(default_factory=list)

class ReflectionExtractionResponse(BaseModel):
    observations: list[ExtractedObservation] = Field(default_factory=list)
    events: list[ExtractedEvent] = Field(default_factory=list)
    causal_mechanisms: list[ExtractedCausalChain] = Field(default_factory=list)

class RawCaptureResponse(BaseModel):
    context: str = ""
    emotion: str | None = None
    emotion_quote: str | None = None            # enforces A2-2

class ExtractionOutcome(BaseModel):             # what a pass hands back
    observations: tuple[ObservationNode, ...]
    events: tuple[EventNode, ...]
    chains: tuple[CausalChainNode, ...]
    steps: tuple[CausalStepNode, ...]
    drops: tuple[DropRecord, ...]
    used_fallback: bool = False
```

**Every enum-valued field on the response models is typed `str`, not the enum.** This is the
decision that makes A2-1 possible. If `type` were `ObservationType`, one hallucinated type
name would fail Pydantic validation for the entire response and lose eight good observations
alongside the bad one. Parsing stays permissive; judgement happens per item in
`validation.py`, where a drop is scoped to the item that earned it. Every field also carries
a default, so a missing key is a validation drop with a clear reason rather than a parse
failure with none.

## B7. `validation.py` — The Rule Set

One predicate per rule, each returning a `DropRecord(item_kind, index, rule, detail)` or
`None`. Nothing here logs the item's content — only which rule and where.

| Rule | Applies to | Action on failure |
|---|---|---|
| **V1** unknown `type` | observation | drop |
| **V2** unknown `provenance` / `signal_strength` / `extraction_confidence` | observation, event | drop |
| **V3** mandatory signal floor (`HIGH_SIGNAL_REQUIRED_TYPES`) | observation | drop |
| **V4** empty `content` / `event_summary` / `chain_summary` | all | drop |
| **V5** `type` in `EXCLUDED_TYPES` (`PROSODY_SIGNAL`) | observation | drop |
| **V6** `person_ref` absent from both the coreference map and the episode text | observation, event | clear the ref, keep the item |
| **V7** unknown causal step `type` | chain | drop **the chain**, not the step |
| **V8** step indices not contiguous from 1 | chain | renumber in order, keep, log |
| **V9** chain with fewer than 2 steps | chain | drop — a one-step "chain" is an observation |
| **V10** `RAW_CAPTURE` episode produced a non-`{CONTEXT, EMOTION}` type | observation | drop |
| **V11** `RAW_CAPTURE` `emotion_quote` not found in the episode text | the emotion only | drop |
| **V12** over the B3 caps | all | truncate, log the count |
| **V13** `raw_evidence` quotes not found in the episode text | observation, event | **keep**, count as ungrounded, log the rate |

V6 clears rather than drops because the observation itself may be perfectly real — the model
attached a wrong name to a true statement. Losing the statement to save the name is the wrong
trade; losing the name is not.

V7 drops the whole chain because a chain is a sequence: one unreadable step means the
sequence is not the one the person described, and a chain with a hole in it is worse than no
chain.

V13 is the ungrounded-evidence metric from A6 — deliberately not a drop in this goal.
Comparison is normalized (case, whitespace, punctuation) and asks whether any evidence span
appears in the episode text.

## B8. `assembly.py` — Raw Output → Nodes

```python
def build_observations(...)   -> list[ObservationNode]
def build_events(...)         -> list[EventNode]
def build_session_anchor(...) -> SessionNode
def build_chains(...)         -> tuple[list[CausalChainNode], list[CausalStepNode]]
```

Policy applied here, once:

- **`node_id`** — `make_scoped_node_id`, per-prefix counters from `_IdMinter`.
- **`occurred_at`** — the input's `occurred_at` for every node. The model is never asked for
  a timestamp; it cannot know one, and a hallucinated date poisons temporal decay.
- **`created_at` / `valid_from`** — `datetime.now(UTC)` at assembly, taken once per call so
  every node from one episode shares an instant.
- **`episode_id`** — from the input episode, on every node.
- **`status`** — `ObservationStatus.ACTIVE` on the `REFLECTION` path,
  `ObservationStatus.RAW_CAPTURE` on the other (`Preprocessing.md`: raw-capture observations
  bypass reconciliation and must be distinguishable at write time).
- **`provenance`** — `CO_CREATED` when the observation's content or evidence overlaps a
  `co_created_span`, otherwise the model's value, defaulting to `USER_GENERATED`.
  `verification_status` follows from it automatically via `SignalProvenanceMixin`.
- **`extraction_model`** — `provider.model_name`, never a literal.
- **`extraction_attempt`** — always 1 in this goal (A2-1).
- **`step_count`** — `len(steps)` after V8 renumbering, never the model's claim.

**The session anchor** (A2-4), `REFLECTION` path only:

```
session_summary      = episode.episode_summary          (Stage 0's own words, not re-derived)
participant_entities = ["user"] + ["ai_facilitator"] if co_created_spans else []
signal_strength      = the strongest signal among the episode's kept observations, else STANDARD
event_date, session_label, occurred_at = from the input
```

It is minted even when the model extracted events, because an event that anchors a belief and
the session in which the belief shifted are different claims, and Goal 9 chooses between them.

## B9. `stage.py` — The Sequence

```python
def extract(
    payload: MicroextractionInput,
    *,
    lightweight: LLMProvider,
    thinking: LLMProvider,
    config: AppConfig | None = None,
) -> ExtractionResult
```

Both roles are parameters for the same reason as Goal 5: the stage is unit-testable with no
infrastructure, and Goal 10 owns instantiation. Both are named even though any one call uses
only one of them, because which one is used is decided *inside* by `entry_class`, and the
caller should not have to know the routing rule to satisfy the signature.

```
1.  if entry_class is RAW_CAPTURE:                                    [code]
       RAW_CAPTURE pass ─────────────────► context + stated emotion   [LIGHTWEIGHT]
       validate, assemble, return (no chains, no anchor)
2.  REFLECTION pass ──────────────────────► observations/events/chains [THINKING]
3.  validate every item, collecting drops                             [code]
4.  assemble nodes, mint ids                                          [code]
5.  mint the session anchor                                           [code]
6.  assemble ExtractionResult + closing log line                      [code]
```

`validation_passed` is `True` only when the call succeeded, nothing was dropped, and at least
one content node survived. Goal 7 reads exactly that flag to decide whether to re-ask.
`retry_count` is always 0 here.

## B10. Fallback Policy

| Failure | Fallback | Why that direction |
|---|---|---|
| Provider error, unparseable reply, or wrong shape | Zero content nodes, no anchor, `validation_passed: False`, warning | Nothing is lost: the episode's full text is on the `EpisodeNode` and Goal 7 can re-ask. Anything invented to fill the gap would be indistinguishable from a real extraction forever. |
| Response parsed but every item dropped | Same as above, with the drop reasons logged | An extraction with nothing valid in it is a failed extraction. |
| Some items dropped | Keep the survivors, `validation_passed: False` | Partial truth beats none, and the flag still routes it to Goal 7. |
| `RAW_CAPTURE` emotion fails V11 | Keep the `CONTEXT`, drop the emotion | The path's whole justification is that it does not infer feelings. |
| Over the B3 caps | Truncate, keep the first N, log | The prompt asks for significance order, so the tail is the cheapest thing to lose. |

Every fallback logs at WARNING with the pass name, the episode id, the reason, and never the
text.

## B11. Doc Amendments Required

Applied **before** coding, as Goals 4 and 5 did.

1. `Microextraction.md` — `RAW_CAPTURE` extracts `CONTEXT` and explicitly-stated `EMOTION`,
   with the quote requirement (A2-2); note that `OPEN_LOOP` is extracted as an observation
   and promoted to a node during reconciliation (A2-5); note that `provenance: CO_CREATED` is
   driven by the adopted spans Stage 0 now returns (A2-6).
2. `Preprocessing.md` — same `RAW_CAPTURE` wording (A2-2); document `co_created_spans` on the
   preprocessing result (A2-6).
3. `Architecture.md` — re-extraction limit is 3, not 1 (A4-1); complete the mandatory
   signal-floor list to the six shipped types (A4-2); `OpenLoopNode` creation moves from
   Microextraction to Reconciliation (A4-3); record the synthetic session anchor in the
   Stage 1 summary (A2-4).
4. `Technical_HLD.md` §5 — add `MicroextractionInput`; add `co_created_spans` to
   `PreprocessingResult`.
5. `Schema.md` §3.1 — note that one `SessionNode` per `REFLECTION` episode is minted by
   Stage 1 as the causal anchor.
6. `Master_Plan.md` — record the package-vs-module deviation (B1) and tick Goal 6.

## B12. Test Plan (~120 tests)

| File | Covers |
|---|---|
| `test_extraction_catalog.py` | Catalog covers `ObservationType` exactly; `PROSODY_SIGNAL` is excluded from the rendered dictionary; every definition is non-empty and one line. |
| `test_extraction_validation.py` | Every rule V1–V13, each proved to drop **only** its own item; the six mandatory-floor types; unknown enum values on all three enum-valued fields; step renumbering; the person guard clearing rather than dropping; cap truncation counts. |
| `test_extraction_assembly.py` | Id scoping — two episodes, same day, disjoint ids; one shared `created_at` per call; `status` differs by path; `step_count` recomputed, not trusted; `CO_CREATED` set from a span and `verification_status` following it; the anchor's summary, participants, and signal roll-up. |
| `test_extraction_passes.py` | Each path against a scripted `FakeLLMProvider`: correct response model, correct role, correct prompt template. All three failure modes (call fails, unparseable, wrong shape) per path, each asserting zero nodes and the warning. |
| `test_extraction_stage.py` | `Microextraction.md`'s worked six-step chain round-trips; a rich example yields ≥3 distinct observation types; `RAW_CAPTURE` yields ≤2 observations, no chains, no anchor; call counts are exactly one per episode and the role matches `entry_class`; `validation_passed` semantics across all four cases; `lumen/pipeline/extraction/` imports nothing from `operational`/`graph`/`vector`. |
| `test_extraction_trace.py` | Every log line carries the ambient trace id; emitted DTOs carry it; a distinctive journal sentence appears in no captured log line by default, across the success, drop, and total-failure paths. |
| `test_pipeline_schemas.py` (extend) | `MicroextractionInput` validation; `PreprocessingResult.co_created_spans` defaults empty. |
| `test_preprocessing_passes.py` (extend) | The conversation pass returns adopted spans; its fallback returns none. |
| `test_schemas_ids.py` (extend) | `make_scoped_node_id` format, bounds, and `SEMANTIC_ID_RE` conformance. |

All LLM interaction runs against `FakeLLMProvider` with registered scripts — no network, no
credentials, no live tests in this goal.

## B13. Build Order

0. **Doc amendments (B11).** Two of them change what gets built.
1. `pipeline.py` (`MicroextractionInput`, `co_created_spans`) + `ids.py` + `PipelineConfig` —
   contracts first.
2. Stage 0 amendment: conversation pass returns adopted spans.
3. `catalog.py` — the dictionary and its drift test.
4. `contracts.py`, `prompts.py`.
5. `validation.py` — pure predicates, fully testable with no LLM.
6. `assembly.py` — id and timestamp policy.
7. `passes.py`, then `stage.py`.
8. `test_extraction_trace.py` — once there is a real sequence to trace through.
9. `Master_Plan.md` checkbox, and Section C of this document.

---

# SECTION C — RESULTS

**Status:** ✅ Complete
**Tests:** 1151 passing (968 before this goal + 183 new), 9 live tests still deselected.
**Coverage:** **100%** on `lumen/pipeline/` (all 15 modules across both stages),
`lumen/config.py`, `lumen/schemas/pipeline.py`, and `lumen/schemas/ids.py`.

## C1. What Was Built

| Module | Contents |
|---|---|
| `pipeline/extraction/catalog.py` | The 47 observation types with one-line definitions, the group ordering used to render them, and the two restricted sets (audio-only, thin-path). |
| `pipeline/extraction/contracts.py` | 6 response models (deliberately permissive), 12 named drop reasons, and the outcome model each path hands back. |
| `pipeline/extraction/prompts.py` | The two templates, the system instruction, and the renderers for the type dictionary, the people list, and the mandatory-weight types. |
| `pipeline/extraction/validation.py` | The 13 rules, the cleaned-up shapes they produce, and the context they judge against. |
| `pipeline/extraction/assembly.py` | `NodeFactory` and `_IdMinter` — id policy, timestamp policy, provenance crediting, and the session anchor. |
| `pipeline/extraction/passes.py` | The shared request sequence and the two paths, each composing prompt → call → validate → assemble. |
| `pipeline/extraction/stage.py` | `extract()`, the path choice, the trust flag, and the closing log line. |

## C2. Deviations From the Plan

1. **`co_created_spans` sits on `PreprocessingResult`, not `PreprocessedEpisode`.** Flagged
   in A2-6 before coding and confirmed: segmentation runs after the conversation pass, so a
   span cannot be attributed to one episode at the moment it is found. It is session-scoped
   exactly like `coreference_map`, and `MicroextractionInput` carries both.
2. **A pass is the whole handling of one path, not just the model call.** B8 implied
   `passes.py` would only talk to the model. Each function instead composes prompt →
   request → validate → assemble, which keeps `stage.py` about routing alone and leaves
   each composed piece in its own module.
3. **The drop note names fields as the model saw them.** `extraction_signal_strength`, not
   the internal `signal_strength`. Found by a test: the note exists to explain a reply, so
   it should use the reply's vocabulary.
4. **`_check_stated_feeling` and `_plain_finding` were extracted** from
   `validate_raw_capture` — the two constructions of a thin-path finding were identical,
   and the emotion rule is the most consequential four lines in the file.
5. **The anchor is minted only when something survived.** B8 said one per `REFLECTION`
   episode; an episode that produced nothing has nothing to anchor, and a lone session node
   would claim a piece of thinking happened that left no trace.

## C3. Things Caught While Implementing

1. **The provider layer logs its own warnings.** A test asserting "exactly one warning"
   failed against the retry and JSON-parse warnings the provider emits. Asserting on the
   total would have let a stage that stopped reporting failures still look healthy, so the
   tests now filter on the stage's own marker.
2. **A wrong-shaped reply is path-specific.** `{"observations": "not a list"}` is a broken
   reflection reply but a *valid* thin-capture reply, because the model ignores fields it
   was not asked for. The failure-mode fixture now supplies a different malformed reply per
   path.
3. **`"Al"` must not match inside `"Alex"`.** The person guard compares flattened text, and
   a plain substring test would have made half the alphabet a known person. Comparison is
   word-bounded, and there is a test for exactly that.

## C4. What the Tests Cover

183 new tests across 6 new files plus 4 extended ones. The ones worth knowing about:

- **Every rule is proved to cost only its own item.** Nearly every rejection test includes a
  valid sibling and asserts it survived — that property is what the whole per-item design
  buys, and it is the one a careless refactor would silently lose.
- **The worked six-step sequence from the spec round-trips**, in order, with the step count
  recomputed rather than believed.
- **A thin entry cannot produce a feeling nobody stated.** Tested from both sides: a quote
  that is present survives, a quote that is absent is dropped, and no quote at all is
  dropped.
- **An invented person is removed while the statement survives**, and the drop note is
  asserted never to repeat the name.
- **Two episodes of the same day are proved to mint disjoint node ids**, end to end and at
  the id helper.
- **A failed reading produces nothing at all** — no observations, no events, no anchor —
  asserted directly, because a fallback that invented something here would be permanent and
  undetectable.
- **The writing stays out of the logs** across five paths, including the drop note, which is
  the most tempting place to put it.

## C5. Still Deferred

Unchanged from A5. The one worth restating: **nothing retries yet.** A dropped item is
dropped for good in this goal, and `validation_passed` is the flag Goal 7 reads to know it
should ask again. Until that lands, a model that fumbles one observation costs that
observation permanently.
