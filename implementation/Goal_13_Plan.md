# Goal 13: Query Formulation Layer

**Branch:** `goal13`
**Status:** ✅ Complete
**Depends on:** Goal 11 (the graph can be read) ✅, Goal 12 (there is a graph worth reading) ✅
**Blocks:** Goal 14 (the three retrieval passes), and through it Goals 15–16

---

## Objective

Goals 1–12 built the half of the system that *writes*. A person talks, and days later
their words have become patterns, beliefs and causal chains in a graph. Phase 4 builds the
half that *reads it back* — silently, mid-conversation, while the person is simply talking.

This goal builds the first component of that half: the thing that decides, on every single
turn, whether the conversation just touched something the graph knows about.

It is a small component with a large consequence. Everything downstream — the three
retrieval passes, the scoring, the context assembly, the injection — runs only when this
layer says it should. If it says yes too often, every "yeah, go on" costs a three-second
pause and the conversation stops feeling like a conversation. If it says no too often, the
system has a rich history of the person and never uses it. And on the turns where somebody
is actually falling apart, this layer is the thing that has to notice and get out of the
way.

---

# SECTION A — LOGIC (please verify)

*Short by design. This is what the goal means; Section B is how it's coded.*

## A1. What Gets Built

| Deliverable | What it is |
|---|---|
| **The turn classifier** | Reads the user's latest message and answers two questions: *does this touch anything in their history?* and *what emotional state are they in right now?* |
| **The retrieval signal** | The structured answer it produces — up to three reasons to go looking, plus the emotional reading. This is the only thing Goal 14 will receive. |
| **A day-session object** | A small in-memory record of the current day's conversation: the recent turns, and which sensitive topics the user has opened. Created fresh each calendar day and thrown away at midnight. |
| **A crisis floor** | A short, fixed list of unmistakable distress phrases, written in plain code. If one appears, retrieval is switched off for that turn regardless of what the AI classifier thought. |
| **A grounding check** | Before a signal leaves this layer, we verify the things it names actually exist in the graph. A trigger pointing at a person who was never recorded is dropped rather than passed on. |
| **A debug endpoint** | One read-only URL where you can paste a sentence and see exactly what the classifier makes of it. |

## A2. The Decisions Taken

*All eight were put to you explicitly before this plan was written.*

1. **The crisis judgement is not left to the model alone.** The fast classifier reads the
   emotional register as normal and is allowed to *escalate* a turn to crisis. But a short
   hard-coded phrase list sits underneath it, and if one of those phrases appears, the turn
   is crisis no matter what the model said. The asymmetry is the point: the model can turn
   retrieval off, and can never turn it back on. The cost is a maintained phrase list and
   the occasional false alarm, which costs one skipped retrieval — the cheapest possible
   way to be wrong.

2. **If the classifier fails or is slow, the answer is "no retrieval".** Not a guess, not a
   keyword fallback. A failed classification is indistinguishable from a trivial turn from
   the outside, and the harmless reading of the two is the same: say nothing.

3. **Triggers are checked against the graph before they leave.** The model can name a
   person or a life-era freely; we then confirm that person has a record and that era tag
   really exists, and drop the trigger if not. This matters more than it sounds: eras are
   stored as free text with no fixed vocabulary, so a model confidently answering
   `HIGH_SCHOOL` against a graph that recorded `high school years` would silently retrieve
   nothing, forever, with no error anywhere. The fix is to *hand the model the user's real
   era names in the prompt* and reject anything outside them.

4. **Trivial turns skip the model call entirely** — but only on a tiny frozen list of pure
   acknowledgements (`yeah`, `ok`, `go on`, `thanks`, `hmm`, and a handful more), matched
   exactly. Deliberately **not** a length rule: the shortest turns in therapy are often the
   heaviest ones, and "I can't anymore" is four words.

5. **This goal owns the day-session state.** The spec says a sensitive topic, once opened
   by the user, stays open for the rest of that day — which is meaningless unless something
   remembers the day. It lives in memory, keyed by calendar date, and is discarded when the
   date changes, exactly as the spec describes. Goal 14 will hang its retrieval buffer off
   the same object.

6. **The <100ms budget in the spec is not achievable and will be corrected.** A real call
   to a cloud model takes 300–800ms. We set a configurable deadline (default 600ms), abandon
   the call if it passes, and record the real measured time on every turn. The spec gets
   amended to state the honest number and why.

7. **The code stays sequential.** There is one call here and nothing to run in parallel.
   Concurrency belongs to Goal 14, which fans out, and Goal 16, which serves web requests.

8. **The classifier sees a small window of recent turns** (default 4) as background, but
   classifies only the newest one. Several of the spec's own trigger types cannot be
   recognised from a sentence in isolation — "I don't feel that anymore" has no subject,
   and "can you explain what you said earlier?" is only trivial once you know it refers to
   the AI, not to something the person once believed.

## A3. The Nine Answers It Can Give

Eight reasons to retrieve, and one reason not to. All nine come straight from the spec.

| Trigger | Fires when | Grounded against |
|---|---|---|
| `PATTERN_MENTION` | A recurring behaviour, feeling or situation is described | The domain must be one of the eleven real ones |
| `BELIEF_CHALLENGE` | The person questions something they used to hold | Domain, as above |
| `HISTORICAL_ERA` | A past life period is referenced | The era must exist in this user's graph |
| `NAMED_PERSON` | Someone the graph knows about is mentioned | The person must have a record |
| `SOMATIC_MARKER` | A physical sensation is described | — |
| `IDENTITY_STATEMENT` | A claim about who they are or are not | — |
| `PROGRESS_CLAIM` | A claim that something has changed for the better | — |
| `OPEN_LOOP_MATCH` | The turn resembles a question left open in an earlier session | At least one open loop must exist |
| `NO_TRIGGER` | Small talk, logistics, acknowledgement | — |

And four readings of emotional state, which control how much gets injected later:
`STABLE`, `VULNERABLE`, `REFLECTIVE`, and `CRISIS` — where crisis means the signal comes
back carrying nothing at all.

## A4. What One Turn Costs

| Kind of turn | Model calls | Wait |
|---|---|---|
| "yeah" / "go on" / "thanks" | 0 | ~0ms |
| A turn matching the crisis floor | 0 | ~0ms |
| Everything else | 1 (cheap model) | up to 600ms, then abandoned |

The graph lookups used for grounding are local and embedded — single-digit milliseconds —
and only happen when the model actually returned a trigger to check.

## A5. What This Goal Deliberately Leaves Undone

- **No retrieval.** Nothing is searched for and nothing is injected. This layer produces a
  signal and stops. Goal 14 acts on it.
- **No session continuity buffer.** Pass C's running list of already-surfaced nodes is
  Goal 14's, and will attach to the day-session object built here.
- **No chat endpoint, no streaming, no carry-forward.** Goal 16.
- **No nickname matching.** If the graph knows "Priya" and the person says "my sister", the
  person trigger will not ground. Cross-entry alias matching was already deferred by Goal 9
  and stays deferred; this layer inherits the limitation rather than inventing a second
  half-answer to it.
- **No persistence of anything.** Every turn classification is logged and then forgotten.

## A6. The Risk Worth Naming

The honest weakness of this design is that a cheap, fast model is being asked to make a
clinically-shaped judgement about someone's emotional state, several times a minute.

Two things contain it. The crisis floor means the worst error — injecting historical data
into somebody's breakdown — has a deterministic backstop that no model can override. And
every other error this layer can make is *quiet*: a missed trigger means the AI answers
from the conversation alone, which is what it would have done anyway, and a spurious
trigger costs a search that finds little and a scoring step that discards it. There is no
path from a misclassification here to a wrong record in the graph, because this layer
cannot write.

What it cannot contain is a systematically bad classifier — one that says `NO_TRIGGER` to
everything. That would be invisible: the system would simply feel like it had no memory.
The mitigation is the debug endpoint, plus a fixed set of worked examples in the test suite
drawn from the spec's own session excerpts, so a regression in trigger quality fails a test
rather than quietly degrading the product.

## A7. Definition of Done

1. A turn goes in, a structured signal comes out, and it is right on the spec's own
   examples: small talk yields `NO_TRIGGER`, "I think it's my childhood" yields a real
   trigger, a distress phrase yields `CRISIS` with nothing attached.
2. Naming a person the graph has never heard of does not produce a person trigger.
3. Naming an era in different words than the graph stored still grounds, because the model
   was shown the graph's own vocabulary.
4. A sensitive topic opened on turn 3 is still recorded as open on turn 30, and gone the
   next day.
5. A classifier that hangs costs at most the configured deadline, and the turn proceeds.
6. The debug endpoint classifies a pasted sentence against the Goal 12 simulated graph.
7. ≥90% coverage on everything new; the full suite (2010 tests) still passes.

---

# SECTION B — LOW-LEVEL DESIGN

*Implementation detail. Not intended for review.*

## B1. Files

```
lumen/query/                          ← NEW top-level package: the read half
├── __init__.py                       ← exports formulate, ChatSession, SessionRegistry
├── session.py                        ← ChatSession, SessionRegistry (in-memory, per calendar day)
└── formulation/
    ├── __init__.py                   ← exports formulate() and nothing else
    ├── stage.py                      ← formulate(): the ten-step sequence
    ├── contracts.py                  ← internal-only shapes (the raw model reply)
    ├── prompts.py                    ← system instruction + turn-window rendering
    ├── triage.py                     ← the frozen acknowledgement set
    ├── safety.py                     ← the crisis floor
    ├── grounding.py                  ← graph checks + era vocabulary (cached per session)
    └── deadline.py                   ← run-with-timeout wrapper around one provider call

lumen/schemas/query.py                ← NEW: ChatTurn, RetrievalTrigger, RetrievalSignal
lumen/schemas/enums.py                ← AMEND: TriggerType, EmotionalRegister, FormulationPath
lumen/config.py                       ← AMEND: QueryConfig, wired into AppConfig
lumen/graph/provider.py               ← AMEND: list_era_tags() on ReadOnlyGraph
lumen/graph/kuzu_impl.py              ← AMEND: implement it
lumen/api/routes/query.py             ← NEW: GET/POST debug classification endpoint
lumen/api/{main,deps,schemas}.py      ← AMEND: mount the router, provide the deps

lumen/tests/test_query_session.py
lumen/tests/test_query_formulation_safety.py
lumen/tests/test_query_formulation_grounding.py
lumen/tests/test_query_formulation_stage.py
lumen/tests/test_api_query.py
```

Why a new top-level `lumen/query/` rather than `lumen/pipeline/query/`: the pipeline
package is the ingestion path, and Goals 5/6 asserted (and Goal 10 narrowed) that its
stages import no persistence. This is a different axis of the system — it reads, it holds
per-session state, and it is driven by a live conversation rather than by a decayed
buffer. Filing it under `pipeline/` would make both of those look like violations of rules
that do not apply to it. `Technical_HLD.md` §2 already names this the **Query Service**.

## B2. Enums (`schemas/enums.py`)

```python
class TriggerType(StrEnum):     # 9 members, verbatim from the spec table
    PATTERN_MENTION, BELIEF_CHALLENGE, HISTORICAL_ERA, NAMED_PERSON,
    SOMATIC_MARKER, IDENTITY_STATEMENT, PROGRESS_CLAIM, OPEN_LOOP_MATCH, NO_TRIGGER

class EmotionalRegister(StrEnum):
    STABLE, VULNERABLE, CRISIS, REFLECTIVE

class FormulationPath(StrEnum):
    """How a signal was arrived at — for logs, tests, and the debug view."""
    CLASSIFIED        # the model answered
    ACKNOWLEDGEMENT   # matched the frozen trivial list, no call made
    SAFETY_FLOOR      # matched the crisis floor, no call made
    TIMED_OUT         # deadline passed
    CALL_FAILED       # provider error or unparseable reply
```

`TRIGGER_PRECEDENCE: tuple[TriggerType, ...]` lives in `formulation/contracts.py` and fixes
the order used when capping to three, so the same turn always yields the same three.
Ordering follows retrieval cost and specificity: the structural triggers (`NAMED_PERSON`,
`HISTORICAL_ERA`, `OPEN_LOOP_MATCH`) outrank the semantic ones, because a structural
lookup is both cheaper and exact.

## B3. DTOs (`schemas/query.py`)

```python
class ChatTurn(BaseModel):          # frozen
    turn_index: int                 # 0-based within the day-session
    role: Literal["user", "assistant"]
    content: str                    # min_length=1
    timestamp: datetime

class RetrievalTrigger(BaseModel):  # frozen
    trigger_type: TriggerType
    domain: Domain | None = None    # the real enum, never free text
    era: str | None = None          # exactly as stored in the graph, post-grounding
    person_node_ids: list[str] = [] # resolved ids, not names
    keywords: list[str] = []        # ≤6, used by Goal 14's HyDE expansion

class RetrievalSignal(BaseModel):   # frozen
    session_id: str
    turn_index: int
    retrieval_triggers: list[RetrievalTrigger] = []
    named_entities_mentioned: list[str] = []   # raw, ungrounded — kept for debugging
    emotional_register: EmotionalRegister
    query_formulation_confidence: float        # 0..1
    critical_domain_opened: Domain | None = None
    unlocked_domains: list[Domain] = []        # cumulative for the day
    formulation_path: FormulationPath
    latency_ms: int
    suppressed_by_crisis: bool = False

    @property
    def should_retrieve(self) -> bool:
        return bool(self.retrieval_triggers)
```

`should_retrieve` is a property rather than a stored field so it cannot disagree with the
trigger list. `NO_TRIGGER` never appears *in* the list — an empty list is the
representation, and the enum member exists for the model's reply vocabulary and for logs.

**Spec discrepancy to record:** the spec's example signal carries
`"domain": "avoidance_resistance"`, which is not one of the eleven `Domain` values and
matches nothing in the schema. The example is illustrative prose; `Domain` is the contract.
`Conversational_RAG_Mode.md` gets its example corrected.

## B4. `formulate()` — the sequence (`formulation/stage.py`)

```python
def formulate(
    turn: ChatTurn,
    session: ChatSession,
    *,
    lightweight: LLMProvider,
    graph: ReadOnlyGraph,
    config: QueryConfig,
) -> RetrievalSignal
```

Injected providers, no globals, no writes — the Goal 8 shape, which `Technical_HLD.md` §8
already blesses for read-only stages.

1. **Crisis floor** (`safety.py`). Normalise and scan. On a hit: return immediately with
   `CRISIS`, empty triggers, `path=SAFETY_FLOOR`, `suppressed_by_crisis=True`. No model
   call — the outcome is already fixed, so paying for one only delays it.
2. **Acknowledgement check** (`triage.py`). Normalise (lowercase, strip punctuation and
   whitespace) and test for exact membership in `TRIVIAL_TURNS: frozenset[str]`. On a hit:
   return `STABLE`, empty triggers, `path=ACKNOWLEDGEMENT`.
3. **Era vocabulary** (`grounding.py`). `session.era_vocabulary` is fetched once per
   day-session via `graph.list_era_tags()` and cached on the session; a graph failure
   yields an empty vocabulary and is logged, which degrades era grounding to "reject
   everything" rather than crashing the turn.
4. **Build the prompt** (`prompts.py`). System instruction carries the nine trigger
   definitions, the four registers, the eleven `Domain` values, the user's real era tags,
   and the rule that only the final turn is being classified. User prompt carries the last
   `config.formulation_context_turns` turns rendered as `role: content`, with the turn
   under classification marked.
5. **One call, under a deadline** (`deadline.py`). `generate_structured(...,
   response_model=ClassifierReply)`. See B6.
6. **Parse and validate.** `ClassifierReply` is `extra="ignore"`; an unparseable reply or a
   provider error becomes `path=CALL_FAILED`, `NO_TRIGGER`, `STABLE`.
7. **Register.** The model may raise severity to `CRISIS`. It may not lower one the floor
   already set — unreachable here, since a floor hit returned at step 1.
8. **Ground each trigger** (`grounding.py`). Per B5. Ungroundable triggers are dropped with
   a debug log naming what failed.
9. **Cap and order.** Sort by `TRIGGER_PRECEDENCE`, truncate to
   `config.max_triggers_per_turn`.
10. **Unlock and record.** A valid `critical_domain_opened` is added to
    `session.unlocked_domains`. The turn is appended to the session. On `CRISIS`, the
    trigger list is emptied *after* the unlock is recorded — the topic was still opened by
    the user, and forgetting that would re-lock it for the rest of the day.

One structured log line per turn: `trigger_types`, `register`, `path`, `latency_ms`,
`dropped` count. This is the only telemetry that will tell us whether the classifier is
quietly saying no to everything.

## B5. Grounding rules (`formulation/grounding.py`)

| Trigger | Check | Failure |
|---|---|---|
| `NAMED_PERSON` | For each name: `graph.get_node(person_node_id(name))`. `person_node_id` is Goal 9's deterministic slug, so this is one keyed read per name, no search. | Unknown names dropped; the trigger dropped if none survive. Raw names are still kept on `named_entities_mentioned`. |
| `HISTORICAL_ERA` | Normalised match (casefold, non-alphanumerics → `_`) against the cached vocabulary; the **stored** spelling is what lands on the trigger. | Trigger dropped. |
| `OPEN_LOOP_MATCH` | `graph.find_nodes(["OpenLoopNode"], limit=1)` — existence only. | Trigger dropped. |
| `PATTERN_MENTION`, `BELIEF_CHALLENGE` | `domain` must parse to `Domain`. | Domain cleared, **trigger kept** — semantic search does not need a domain, it only narrows better with one. |
| `SOMATIC_MARKER`, `IDENTITY_STATEMENT`, `PROGRESS_CLAIM` | None — these are filters over node types, not references to named things. | — |

Every graph call is wrapped: an exception grounds nothing and is logged, matching Goal 9's
`_already_known` precedent, where a graph that cannot answer is treated as not knowing.

## B6. The deadline (`formulation/deadline.py`)

The `LLMProvider` protocol has no per-call timeout and adding one would touch every
provider. Instead: a module-level `ThreadPoolExecutor` (bounded, `max_workers` from config,
`thread_name_prefix="formulate"`), submit the call, `future.result(timeout=...)`.

Three details that will otherwise bite:

- **Trace context must be copied in.** `contextvars.copy_context().run(...)` — Goal 4 found
  this exact bug, where a shared context fails only under real thread contention.
- **An abandoned call is not cancelled.** Python cannot kill a running thread. The future is
  dropped, its eventual result discarded, and a debug line logged when it lands so a
  systematically-slow provider is visible rather than merely inferred.
- **Retry must be off for this call.** `ProviderConfig.max_attempts` defaults to 3 with
  backoff; a classifier that retries has already lost its deadline twice over. The
  formulation provider is built from `dataclasses.replace(app_config.providers,
  max_attempts=1)`. Documented at the wiring site, not hidden in the factory.

## B7. Session state (`lumen/query/session.py`)

```python
@dataclass
class ChatSession:
    session_id: str          # f"{user_id}_{event_date:%Y_%m_%d}_{label}"
    user_id: str
    event_date: date
    session_label: str = ""
    turns: deque[ChatTurn]   # maxlen = config.session_max_turns
    unlocked_domains: set[Domain]
    era_vocabulary: tuple[str, ...] | None = None   # lazily filled by grounding
    created_at: datetime
    last_activity_at: datetime

    def record_turn(self, turn) -> None
    def recent_turns(self, n) -> list[ChatTurn]
    def next_turn_index(self) -> int
    def unlock(self, domain) -> None
    def is_unlocked(self, domain) -> bool

class SessionRegistry:
    """Keyed by (user_id, label). Opening on a new date replaces the old session."""
    def open(self, user_id, *, at: datetime, label: str = "") -> ChatSession
    def close(self, session_id) -> None
```

Session identity matches the operational `session_buffers` key —
`(user_id, event_date, session_label)` — so Goal 16 can join a live chat session to the
buffer that will eventually be ingested. **Nothing is persisted here**; `Technical_HLD.md`
§6 is explicit that this state is ephemeral, and the ingestion side already has its own
durable record of the same conversation.

The date comes from the caller's `at`, never from `datetime.now()` inside the class, so the
midnight-rollover rule is testable without freezing the clock.

Goal 14 will add `context_buffer: list[BufferedNode]` to `ChatSession`. Not stubbed here —
an unused field is a worse signpost than a line in this plan.

## B8. Config (`config.py`)

```python
@dataclass(frozen=True)
class QueryConfig:
    formulation_timeout_seconds: float = _env_float("LUMEN_FORMULATION_TIMEOUT_SECONDS", 0.6)
    formulation_context_turns: int      = _env_int("LUMEN_FORMULATION_CONTEXT_TURNS", 4)
    max_triggers_per_turn: int          = _env_int("LUMEN_MAX_TRIGGERS_PER_TURN", 3)
    max_keywords_per_trigger: int       = _env_int("LUMEN_MAX_TRIGGER_KEYWORDS", 6)
    era_vocabulary_limit: int           = _env_int("LUMEN_ERA_VOCABULARY_LIMIT", 50)
    session_max_turns: int              = _env_int("LUMEN_SESSION_MAX_TURNS", 200)
    formulation_max_workers: int        = _env_int("LUMEN_FORMULATION_MAX_WORKERS", 4)
```

Added to `AppConfig` as `query: QueryConfig`, following the existing `pipeline:
PipelineConfig` precedent exactly.

## B9. Graph amendment (`list_era_tags`)

```python
def list_era_tags(self, *, limit: int = 50) -> list[str]:
    """Every named period of the user's past that any record is anchored to."""
```

Distinct non-null values across the era columns Goal 8 already mapped
(`ERA_COLUMNS`: `era_tag` on patterns/beliefs, `historical_era` on episodes), active
records only, deduplicated case-insensitively while preserving stored spelling, ordered by
frequency so a truncated list keeps the eras that matter.

This stays inside Goal 11's rule — a **named** traversal answering one stated question, not
a general query method. It exists because era tags are free text with no controlled
vocabulary, which is a schema fact this layer has to work around, not a gap it can fix.

## B10. Debug endpoint (`api/routes/query.py`)

```
POST /debug/formulate   { "text": "...", "history": [...], "user_id": "..." } → RetrievalSignal
```

`POST` rather than `GET` because a therapy sentence has no business in a URL or an access
log — Goal 11 asserted "every exposed verb is GET" for the *graph* router, and that
assertion is scoped to that router and stays true. This one goes under the debug router
with its own test asserting it neither writes nor is mounted in a read-only-graph context;
it is handed `ReadOnlyGraph`, so a write is not merely discouraged but absent from the
object.

Sessions here are ephemeral per request (history supplied in the body) — the endpoint is
for inspecting classifications, not for holding a conversation. That is Goal 16's.

## B11. Amendments expected

| File | Change |
|---|---|
| `docs/Query/Conversational_RAG_Mode.md` | The <100ms budget corrected to a real number with the reasoning; the crisis floor documented as a code-level guarantee rather than a model judgement; the example's free-text `domain` corrected to a real `Domain`; era grounding documented. |
| `docs/hld/Technical_HLD.md` §2.7, §6 | `FormulationService` mapped to `lumen/query/formulation/`; the latency table corrected; `max_attempts=1` for this role noted. |
| `docs/Graph/Schema.md` | `list_era_tags` added to the read surface; a note that `historical_era` / `era_tag` are uncontrolled free text and why that forces vocabulary-grounding. |
| `implementation/Master_Plan.md` | Goal 13 checkbox and result line. |

## B12. Test plan (~110 tests)

| File | Covers |
|---|---|
| `test_query_session.py` (~20) | Day-session identity, midnight rollover replacing the session, turn bounding, unlock persistence within a day and loss across days, `recent_turns` window edges. |
| `test_query_formulation_safety.py` (~18) | Every floor phrase fires; the floor beats a model saying `STABLE`; the model may escalate to `CRISIS`; a floor hit makes no model call at all; near-miss phrasings do not fire; unlock is still recorded on a crisis turn. |
| `test_query_formulation_grounding.py` (~30) | Unknown person dropped, known person resolved to an id; era matched across spelling/case differences; era absent from vocabulary dropped; invalid domain cleared but trigger kept; open-loop existence; every graph call failing safely; the vocabulary cached once per session. |
| `test_query_formulation_stage.py` (~35) | The acknowledgement list; the ten-step order; deadline exceeded → `TIMED_OUT` with the turn proceeding; provider error and unparseable reply → `CALL_FAILED`; capping and precedence ordering; `should_retrieve` never disagreeing with the list; trace id reaching the log line; **the worked examples from the spec's own session excerpts**, which are the only test that actually measures trigger quality. |
| `test_api_query.py` (~12) | The endpoint against a real Kuzu graph built by Goal 12's simulation; malformed bodies; the handed-graph-is-read-only assertion. |

All model behaviour is scripted through `FakeLLMProvider`; grounding tests run against a
real embedded Kuzu, following Goal 8's precedent that a stand-in store agrees with whatever
it is told and so proves nothing about a query.

## B13. Build order

1. Enums + `schemas/query.py` + `QueryConfig`. Contracts before anything reads them.
2. `list_era_tags` on the protocol and Kuzu, with its tests.
3. `session.py` — standalone, no dependencies, fully testable alone.
4. `safety.py` and `triage.py` — pure functions over a string, no infrastructure.
5. `grounding.py` against real Kuzu.
6. `prompts.py` + `contracts.py` + `deadline.py`.
7. `stage.py`, wiring the six together.
8. The API route.
9. Doc amendments, then the Master Plan line.

---

# SECTION C — WHAT WAS ACTUALLY BUILT

## C1. Files

```
lumen/query/__init__.py                    ← QueryFormulator, ChatSession, SessionRegistry
lumen/query/session.py                     ← ChatSession, SessionRegistry, make_session_id
lumen/query/formulation/__init__.py        ← QueryFormulator, and nothing else
lumen/query/formulation/stage.py           ← the ten-step sequence
lumen/query/formulation/contracts.py       ← the model's raw reply shape + trigger ordering
lumen/query/formulation/prompts.py         ← the classifier instruction
lumen/query/formulation/triage.py          ← the frozen acknowledgement set
lumen/query/formulation/safety.py          ← the crisis floor
lumen/query/formulation/grounding.py       ← the graph checks, one per kind of reason
lumen/query/formulation/deadline.py        ← run-with-timeout around one provider call

lumen/schemas/query.py                     ← ChatTurn, RetrievalTrigger, RetrievalSignal
lumen/schemas/enums.py                     ← +TriggerType, EmotionalRegister, FormulationPath
lumen/schemas/ids.py                       ← +person_node_id (moved from reconciliation)
lumen/config.py                            ← +QueryConfig, wired into AppConfig
lumen/graph/provider.py, kuzu_impl.py      ← +list_era_tags
lumen/graph/queries.py                     ← +era_key
lumen/api/routes/query.py                  ← POST /query/formulate
lumen/api/{main,deps,errors,schemas}.py    ← wiring, +Unavailable, +FormulationRequest
lumen/pipeline/reconciliation/people.py    ← person_node_id now imported, not defined
```

Built as planned, with one addition (`api/errors.py`) explained in C2.

## C2. Deviations From the Plan

1. **`formulate()` became `QueryFormulator.formulate()`.** The plan wrote it as a free
   function taking a `DeadlineRunner`. It owns a thread pool, which is a resource with a
   lifetime, and a free function would have forced either a module-level pool (a global,
   which this codebase does not have) or a new pool per call (a thread start on every
   turn). Everything else is still injected, so the constructor is a complete statement
   of what a reading can touch.

2. **`Unavailable` and a 503 handler were added, and this fixed a real regression.**
   Building the classifier at startup made the whole API refuse to start without a model
   credential — every previous goal's read endpoints included. They read two local
   databases and need no model at all, so the failure is now caught, logged, and confined
   to the one surface that needs one. Caught by an existing test, not by review.

3. **`person_node_id` moved to `schemas/ids.py`.** The plan had grounding import it from
   `lumen/pipeline/reconciliation/people.py`. That would have had the query layer import
   the reconciliation package to name one pure function. Id derivation belongs with the
   rest of the id policy, and both callers now share one definition — a second copy that
   drifted would mean this layer never finds what the pipeline wrote.

4. **`era_key` went to `graph/queries.py`, not into grounding.** Both the store (when
   deduplicating spellings) and the query layer (when matching a model's answer) need
   *identical* semantics. Two private copies would be a bug waiting to happen, and
   `queries.py` is already the vendor-free home for exactly this kind of rule.

5. **Goal 11's "every exposed verb is GET" test was narrowed rather than deleted.** It
   now asserts that everything touching the graph or the run history is a GET, and a
   second test pins the single POST by name. The assertion was always about the graph
   routers; the new endpoint changes nothing and takes a POST only because a GET would
   put somebody's sentence about their own life into every access log it passes.

6. **`FormulationPath` was added to the enums** (planned) and earns its place: the four
   ways of producing "nothing to look up" — trivial turn, distress floor, timeout, model
   failure — are identical in the result and need opposite responses from whoever is
   reading the logs.

## C3. Things Caught While Implementing

- **A test wrote a database into the repository.** Two of the new API tests entered the
  application's real startup, which opened the *configured* graph path rather than a
  temporary one. Fixed by building those apps with `tmp_path` config, following the
  existing lifespan test.

- **`ChatSession._counted` had to be `init=False`.** Declared as an ordinary dataclass
  field, how many turns had happened would have been something a caller could state.

- **The turn window is `context_turns - 1` plus the current turn**, not `context_turns`
  plus it. The current turn has not been recorded when the reading starts, so taking the
  full window and appending would show one more turn than configured.

- **The unlock is recorded before a crisis clears the triggers.** They did open the
  subject; clearing that would make tomorrow's reading discover it again. The crisis
  suppresses this turn's lookup, not the fact that the subject is now on the table.

## C4. Honest Limitations

- **A nickname grounds to nothing.** If the graph knows `Alex` and the person says "my
  brother", the person trigger is dropped. Inherited from Goal 9's deferred alias
  matching rather than introduced here, and asserted by a test so it is a known shape
  rather than a surprise.

- **An abandoned model call is not cancelled.** Python cannot stop a running thread, so
  a call past its deadline finishes on its own and its answer is discarded. The pool is
  bounded and late arrivals are logged, which is the only evidence that would distinguish
  a systematically slow model from an occasionally unlucky one.

- **The offline tests script the model's answer, so they measure plumbing, not
  judgement.** The failure that would hurt most — a router that quietly answers "nothing
  to look up" to everything — is invisible to a scripted test by construction. That is
  what `test_query_formulation_live.py` is for, and why the per-turn log line records the
  surviving triggers.

- **The crisis floor will produce false alarms.** "I cut myself shaving" trips it. The
  cost is one skipped lookup on that turn, which is the trade the design chose.

## C5. What Is Still Deferred

- Pass A/B/C retrieval, and anything that acts on a `RetrievalSignal` — Goal 14.
- The session continuity buffer, which attaches to the `ChatSession` built here — Goal 14.
- Context assembly, the 400-token cap and the injection block — Goal 15.
- The chat endpoint, streaming, the 3-second window and carry-forward — Goal 16.
- Using `unlocked_domains` to gate `CRITICAL` nodes. This goal records what the person
  opened; the gate belongs with the retrieval that would otherwise surface them.
- Cross-entry alias matching, still Goal 9's deferral.

## C6. Result

2230 tests passing (2010 from Goals 1–12 + 220 new), plus 10 live tests deselected by
default. **100% coverage** on `lumen/query/`, `lumen/api/`, `lumen/schemas/query.py` and
`lumen/graph/queries.py`; `lumen/graph/` at 99%, with both uncovered lines pre-existing.
