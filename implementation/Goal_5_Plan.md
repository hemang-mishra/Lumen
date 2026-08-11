# Goal 5: Stage 0 — Preprocessing

**Branch:** `goal5`
**Status:** 📋 Planned
**Depends on:** Goal 2 (pipeline DTOs, enums) ✅, Goal 3b (trace ids, JSON logging) ✅, Goal 4 (LLM providers + fakes) ✅
**Blocks:** Goal 6 (microextraction), Goal 10 (orchestrator)

---

## Objective

Stage 0 is the only place in Lumen that touches raw human noise. A decayed session
buffer arrives holding hesitation fillers, self-corrections, code-mixed Hindi/English,
operational questions, and AI turns the user never said. Stage 0 hands Goal 6 clean
English episodes, a coreference map, and a routing decision — and nothing else.

Its real job is refusal. A 20-word voice dump must not reach the extraction LLM, because
the extraction LLM will invent something plausible from it. Everything downstream trusts
that whatever arrives is worth extracting from.

---

# SECTION A — LOGIC (please verify)

*Short by design. This is what the goal means; Section B is how it's coded.*

## A1. What Gets Built

| Deliverable | What it is |
|---|---|
| **`preprocess()`** | One pure function: `SessionDecayEvent` in, `PreprocessingResult` out. No DB access, no global state, providers injected. |
| **Deterministic pre-clean** | Regex stripping of zero-risk standalone fillers (`uh`, `um`, `hmm`), voice entries only. Runs before any LLM sees the text. |
| **4 LLM passes** | `CONVERSATION` (chat only), `NORMALIZE`, `STRUCTURE`, `TRIAGE`. Each is a separate function with its own prompt, own response model, and own fallback. |
| **The quality gate** | Word-count short-circuit, then per-episode coherence scoring, then the `REFLECTION` / `RAW_CAPTURE` / `DISCARD` routing decision. |
| **Reflection prompts** | 3 follow-up questions generated for every `RAW_CAPTURE` episode, returned in the DTO. |
| **3 DTO amendments** | `SessionDecayEvent.source_modality`, `PreprocessedEpisode.episode_id`, `PreprocessedEpisode.episode_summary`. |
| **`PipelineConfig`** | Thresholds (30 words, 0.4 coherence, 3 prompts) as env-overridable config, not literals buried in code. |

## A2. The Decisions You Made

1. **Three LLM passes, plus one more for chat.** `NORMALIZE` (LIGHTWEIGHT) cleans and
   translates; `STRUCTURE` (THINKING) segments into episodes and builds the coreference
   map; `TRIAGE` (LIGHTWEIGHT) scores coherence and writes reflection prompts. Chat
   buffers add `CONVERSATION` (THINKING) at the front. One mega-call was rejected because
   a single failure would lose everything and no sub-step could be replayed; 6–8 granular
   calls were rejected as double the cost for observability nobody has asked for yet.

2. **ASR cleaning is hybrid.** Regex removes only fillers that carry zero semantic risk —
   standalone `uh`, `um`, `hmm`, `uh-huh`. Everything requiring judgement (`like`,
   `you know`, `right`, `basically`, self-corrections, `[CORRECTED_FROM]` annotation)
   goes to the LLM. Pure regex was rejected because Preprocessing.md's own preservation
   rule is stated in terms of syntactic dependency, which regex cannot evaluate — it
   would destroy `right, so the issue was...` and `like` used as a verb. spaCy was
   rejected as a heavy English-only dependency for one sub-step.

3. **Dialogue-act classification and the Stage 0.5 rollup are in scope.** Both are pure
   functions over the buffer and Goal 6 needs their output — provenance depends on
   `CO_CREATED` markers, and without the rollup, chat input fragments the graph with
   hypotheses the user already discarded.

4. **Semantic Day Grouping and multi-day import splitting are out of scope.** Semantic
   Day Grouping requires reading *other* buffers from the operational DB, which breaks
   the pure-function rule for pipeline stages; it belongs in the ingestion layer that
   creates buffers, not in the stage that consumes one. Multi-day splitting is deferred
   with it. A buffer whose messages span several `event_date`s is still processed, using
   the buffer's own date, with a logged warning naming the dates — it degrades loudly.

5. **`DISCARD` is a structural condition, never a model judgement.** It fires only when
   nothing extractable survives AI-turn stripping, dialogue-act filtering, and cleaning:
   an all-`OPERATIONAL_REQUEST` buffer, an empty buffer, or text that reduces to
   whitespace. No coherence band discards anything. This is the one gate that throws user
   input away, so a model is not allowed to be the one deciding.

6. **The LLM detects language and translates; no fastText.** Detection and translation
   happen inside `NORMALIZE`, which already has the text. The dependency and its ~1MB
   model file buy nothing here, and the documented example — `mujhe samajh nahi aaya` —
   is romanized Hindi in pure ASCII, which is exactly the case a cheap encoding check
   would miss. This means `NORMALIZE` runs on every entry, including typed English.

7. **Reflection prompts come from Stage 0, off the cleaned text.** Preprocessing.md
   derives them from the `CONTEXT` observation, but that observation is a Stage 1 output
   and is a one-sentence restatement of the same text — so Stage 0 can produce equally
   good questions a stage earlier, and `PreprocessingResult.pending_reflections` stops
   being a field no stage populates. **No `pending_reflections` table is added.** Nothing
   reads it until Goal 18; Goal 10's orchestrator will persist what the DTO carries.

8. **Three DTO fields added, one declined.** `source_modality` on `SessionDecayEvent`
   (without it Stage 0 cannot tell voice from typed text, so it cannot gate the ASR
   rules), `episode_id` and `episode_summary` on `PreprocessedEpisode` (both are required
   by contracts downstream and have no other producer). `language_tags` was declined — see
   A5.

9. **The gate runs before segmentation, the score runs after.** Word count on the whole
   cleaned session short-circuits to `RAW_CAPTURE` with no segmentation and no scoring.
   Above threshold: segment first, then score each episode independently. The session's
   `quality_gate_decision` is `REFLECTION` if **any** episode scores ≥ 0.4.

## A3. Decision 9 Reorders Decision 1

Worth stating plainly because the two answers were given separately. The pass order is
**NORMALIZE → gate → STRUCTURE → TRIAGE**, not the NORMALIZE → TRIAGE → STRUCTURE order
sketched when the pass shape was chosen. Scoring cannot come second, because decision 9
scores *episodes*, and episodes do not exist until `STRUCTURE` has run.

The cheap rejection still happens first — it is just the word-count gate doing it, in
code, rather than a scoring call. A 15-word dump therefore costs **one** LIGHTWEIGHT call
(reflection prompts), never a THINKING call. That was the point of putting a gate there.

**Call cost per session:**

| Input | Calls |
|---|---|
| Any source, under 30 clean words | 1 (prompts only) |
| Typed or voice, over threshold | 3 |
| Chat buffer, over threshold | 4 |

## A4. Where the Specs Disagree With Themselves

Found while reading. Each needs a doc fix, listed in B12.

1. **Who segments episodes.** `Architecture.md`'s pipeline diagram puts "coreference map,
   episode segmentation" under **Stage 1**. `Preprocessing.md` §4, `Microextraction.md`
   line 150, `PreprocessingResult.episodes`, and `ExtractionResult.episode_id` (singular —
   one extraction per episode) all put them in **Stage 0**. Four sources to one; the
   diagram is stale. Stage 0 segments.

2. **What a sub-threshold entry does.** `Architecture.md` says low-coherence entries are
   "held for HITL review rather than processed". `Preprocessing.md` routes them to
   `RAW_CAPTURE` with minimal capture and reflection prompts. These are different systems.
   `RAW_CAPTURE` wins — it is the more detailed spec, and the HITL queue does not exist
   until Goal 18.

3. **`RAW_CAPTURE` observation types.** `Preprocessing.md` says only `CONTEXT` is
   extracted; `Microextraction.md` says `CONTEXT` **and** `EMOTION`. Not Stage 0's call —
   flagged for Goal 6, which owns that path. Recorded here so it is not rediscovered.

4. **Coreference map shape.** `Microextraction.md` shows
   `[{canonical, aliases_in_document}]`; `Preprocessing.md` and the shipped
   `CoreferenceMap` model use `{resolved_entities, ambiguous_refs}`. The shipped model
   wins; `Microextraction.md`'s example is wrong and drops the ambiguity data entirely.

5. **Themes and era.** `Microextraction.md` describes `overarching_themes` and
   `historical_era` as extraction-LLM outputs, but both are fields on
   `PreprocessedEpisode` — a Stage 0 contract. `STRUCTURE` produces them; it has already
   had to understand each episode's topic in order to split on it.

## A5. What This Goal Deliberately Leaves Undone

| Deferred | To | Why |
|---|---|---|
| Semantic Day Grouping | Ingestion layer (Goal 20) | Needs cross-buffer DB reads; breaks the pure-function rule (A2-4). |
| Multi-day import splitting | Ingestion layer (Goal 20) | Same owner. Buffers should arrive already split by date. |
| `pending_reflections` table + 30-day TTL | Goal 18 | No reader until the review UI exists (A2-7). |
| `PreprocessedEpisode.language_tags` | Not scheduled | Declined. `EpisodeNode.language_tags` keeps defaulting to `["en"]`, so **a translated entry is not distinguishable from a native-English one in the graph.** Detected languages are logged per call, so the information exists in `lumen.jsonl` — it just does not reach the node. |
| `coreference_map_id` | Goal 10 | `EpisodeNode` requires one; `CoreferenceMap` has no id field. The orchestrator mints it at write time. |
| `PROSODY_SIGNAL` / audio features | Voice ingestion goal | Needs the audio, not the transcript. Stage 0 only ever sees text. |
| Whisper.cpp / actual ASR | Voice ingestion goal | Stage 0 consumes a transcript; producing one is upstream. |
| A shared LLM-validation retry helper | Goal 7 | Stage 0 uses per-pass fallbacks (B9). Goal 7 generalizes it. |

## A6. The Risk Worth Naming

**Every pass can fail, and the failure directions are not symmetric.** A failed `TRIAGE`
that defaults to `REFLECTION` sends junk into extraction. A failed `STRUCTURE` that
returns zero episodes silently deletes a session. So each pass has a **conservative**
fallback, and the whole table is in B9: bad segmentation collapses to one episode holding
everything, bad scoring routes to `RAW_CAPTURE`, bad normalization keeps the
regex-cleaned text. Nothing is ever dropped by a failure, and nothing is ever promoted by
one.

The second-order risk is that these fallbacks are silent quality loss — a `STRUCTURE`
pass failing on every session would still produce a green pipeline with one giant episode
each time. Every fallback therefore logs at WARNING with the trace id, and the DoD
requires a test per fallback path.

## A7. Definition of Done

- [ ] `preprocess()` is a pure function — no DB, no global state, providers injected; a test asserts `lumen.pipeline` imports nothing from `lumen.operational`.
- [ ] A 20-word entry costs exactly one LLM call and returns `RAW_CAPTURE` with 3 reflection prompts.
- [ ] An all-`OPERATIONAL_REQUEST` buffer returns `DISCARD` with zero episodes; no LLM judgement is involved in that decision.
- [ ] A code-mixed Hindi/English entry comes back as English, and the untranslated text never reaches `STRUCTURE`.
- [ ] `right, so the issue was...` survives cleaning; `So um, I was like, really frustrated` loses its fillers — both asserted on the doc's own examples.
- [ ] A self-correction keeps only the correction, unless the false start carries new information, in which case both survive with `[CORRECTED_FROM]`.
- [ ] Voice-only rules do not fire on `TEXT_ENTRY` input.
- [ ] A session mixing one deep reflection with one throwaway aside produces two episodes with different `entry_class` values, and a session-level decision of `REFLECTION`.
- [ ] Each of the four passes has a fallback test proving a malformed response degrades conservatively and logs a warning.
- [ ] Journal text never appears in a log line unless `LUMEN_LOG_PROMPTS=true`.
- [ ] Every episode carries the ambient `trace_id`; the whole stage runs offline against `FakeLLMProvider`.
- [ ] ≥90% coverage on `lumen/pipeline/`.

---

# SECTION B — LOW-LEVEL DESIGN

## B1. Files

```
lumen/pipeline/
├── __init__.py            — re-exports preprocess()
└── preprocessing/
    ├── __init__.py        — public surface: preprocess()
    ├── stage.py           — preprocess(): sequences passes, assembles PreprocessingResult
    ├── transcript.py      — buffer → transcript, AI-turn stripping, word count, hashing, gates
    ├── fillers.py         — the deterministic filler table + stripper (voice only)
    ├── contracts.py       — Pydantic response models, one per pass
    ├── prompts.py         — prompt templates, one per pass
    └── passes.py          — the four pass functions + their fallbacks

lumen/tests/
├── test_preprocessing_transcript.py
├── test_preprocessing_fillers.py
├── test_preprocessing_passes.py
├── test_preprocessing_gate.py
├── test_preprocessing_stage.py
└── test_preprocessing_trace.py
```

**Deviation from `Master_Plan.md`**, which names `lumen/pipeline/preprocessing.py`. Stage 0
holds seven separable concerns (transcript assembly, regex tables, four prompts, four
response models, gate arithmetic, fallback policy); one file lands around 700 lines. Same
call Goal 4 made for `protocols.py`. `preprocess()` remains the single public name, so the
stage still reads as one function from outside.

## B2. Schema Amendments

```python
# lumen/schemas/pipeline.py

class SessionDecayEvent(PipelineDTO):
    ...
    source_modality: SourceModality = SourceModality.TEXT_ENTRY

class PreprocessedEpisode(BaseModel):
    episode_id: str = Field(min_length=1)          # ep_2026_06_11_001
    episode_summary: str = Field(min_length=1)     # one line, feeds EpisodeNode
    ...
```

`source_modality` defaults to `TEXT_ENTRY` so the 799 existing tests keep passing; the
buffer already knows the truth via `BufferSource.VOICE_NOTE`, so
`SessionBufferRepository.build_decay_event()` is updated to map
`BufferSource.VOICE_NOTE → SourceModality.VOICE_NOTE` and everything else to
`TEXT_ENTRY`. That mapping is the only operational-DB change in this goal.

`episode_id` is minted with the existing `make_node_id("ep", event_date, index)` from
`lumen/schemas/ids.py` — `ep_2026_06_11_001`. Stage 0 mints it rather than Goal 10 so that
`ExtractionResult.episode_id` has a stable referent the moment the episode exists.

`CoreferenceMap.entry_id` is set to the session id. A decayed buffer *is* one entry; there
is no separate entry identifier in the system, and inventing one would create a second key
for the same thing.

## B3. `config.py` Addition

```python
@dataclass(frozen=True)
class PipelineConfig:
    min_reflection_words: int   = _env_int("LUMEN_MIN_REFLECTION_WORDS", 30)
    coherence_threshold: float  = _env_float("LUMEN_COHERENCE_THRESHOLD", 0.4)
    reflection_prompt_count: int = _env_int("LUMEN_REFLECTION_PROMPT_COUNT", 3)
    max_episodes_per_session: int = _env_int("LUMEN_MAX_EPISODES", 12)
```

Added to `AppConfig` as `pipeline: PipelineConfig`. Both thresholds are doc constants
(30 words, 0.4) that will want tuning against real entries — they are config so tuning is
not a code change. `max_episodes_per_session` is a guard against a `STRUCTURE` pass that
shatters one session into forty fragments; over the cap, the extras are merged into the
last episode and a warning is logged.

## B4. `transcript.py` — Everything Deterministic

```python
def assemble_transcript(event: SessionDecayEvent) -> Transcript
def strip_ai_turns(messages: list[BufferMessage]) -> list[BufferMessage]
def is_chat_buffer(event: SessionDecayEvent) -> bool
def word_count(text: str) -> int
def text_hash(text: str) -> str            # blake2b hex, 16 bytes
def check_discard(text: str, messages: list[BufferMessage]) -> bool
def warn_on_multi_date(event: SessionDecayEvent) -> None
```

- `is_chat_buffer` is true when the buffer holds any `role == "AI"` message. A voice note
  or a pasted monologue has none, so the `CONVERSATION` pass is skipped by shape rather
  than by a flag someone has to remember to set.
- `check_discard` returns true when the surviving text is empty or whitespace-only, or
  when every `USER` turn classified as `OPERATIONAL_REQUEST`. Pure boolean logic over
  already-computed values — no model call, per A2-5.
- `text_hash` uses `blake2b(digest_size=16).hexdigest()`, matching the hashing already in
  `providers/fake.py`. It feeds `PreprocessedEpisode.raw_text_hash` and is computed on the
  **cleaned** text, per Preprocessing.md's note that word count and dedup happen after
  cleaning.
- `warn_on_multi_date` implements A2-4's loud degradation: logs at WARNING naming every
  distinct `event_date` in the buffer and the one being used.

## B5. `fillers.py` — The Regex Half

```python
_STANDALONE_FILLERS = frozenset({"uh", "um", "umm", "uhh", "hmm", "mmm", "uh-huh", "mm-hmm"})

def strip_standalone_fillers(text: str) -> tuple[str, int]:
    """Returns cleaned text and how many tokens were removed."""
```

Only tokens in that set, only when they stand alone as a whole word (case-insensitive,
surrounding punctuation absorbed). Nothing context-dependent lives here. `like`,
`you know`, `right`, `basically`, and `literally` are deliberately **absent** — every one
of them has a content-word sense, and deciding which sense is in play is what the
`NORMALIZE` pass is for.

Whitespace and orphaned punctuation are normalized after removal, so
`So um, I was like` → `So I was like` without a doubled comma.

Voice only. Called when `event.source_modality is SourceModality.VOICE_NOTE`.

## B6. `contracts.py` — One Response Model Per Pass

```python
class TurnClassification(BaseModel):
    message_id: str
    dialogue_act: DialogueAct
    co_created_marker: bool = False

class ConversationResponse(BaseModel):
    turns: list[TurnClassification]
    session_summary: str

class NormalizeResponse(BaseModel):
    cleaned_text: str
    detected_languages: list[str] = Field(default_factory=list)
    translated: bool = False

class SegmentedEpisode(BaseModel):
    episode_summary: str
    text: str
    overarching_themes: list[str] = Field(default_factory=list)
    historical_era: str | None = None

class StructureResponse(BaseModel):
    episodes: list[SegmentedEpisode] = Field(min_length=1)
    coreference_map: CoreferenceMap

class EpisodeScore(BaseModel):
    episode_index: int
    coherence_score: float = Field(ge=0.0, le=1.0)
    reason: str
    reflection_prompts: list[str] = Field(default_factory=list)

class TriageResponse(BaseModel):
    scores: list[EpisodeScore]
```

`ConversationResponse` classifying turns *and* rolling up in one call is deliberate — both
need whole-dialogue context, and a rollup that has not already identified the operational
turns would have to re-derive them. The per-turn acts come back rather than staying
implicit so `BufferMessage.dialogue_act` can be populated and the filtering is auditable.

`TriageResponse` carries `reflection_prompts` so that scoring and prompt-writing share one
call: the model has just decided an episode is thin, and asking what would thicken it is
free at that point.

Goal 4's contract holds — providers return an **unvalidated** dict plus raw text. Stage 0
validates each of these models itself, because Goal 7's retry layer does not exist yet and
is scoped to extraction anyway.

## B7. `prompts.py`

One module-level template string per pass, each a plain `str.format` target with named
placeholders. No f-strings at call sites, so a prompt is greppable and diffable in one
place. `TRIAGE` reproduces Preprocessing.md's documented scoring rubric (1.0 / 0.5 / 0.0
anchors) verbatim, since that wording is the spec.

Prompts are versioned only by git. Prompt templating and versioning is explicitly Goals
5–9's own business per `Goal_4_Plan.md` A5 — a shared registry is not built here.

## B8. `passes.py` — Four Functions, One Shape

```python
def run_conversation(transcript, *, provider: LLMProvider, config) -> ConversationResult
def run_normalize(text, *, is_voice: bool, provider: LLMProvider, config) -> NormalizeResult
def run_structure(text, *, entry_id: str, provider: LLMProvider, config) -> StructureResult
def run_triage(episodes, *, provider: LLMProvider, config) -> TriageResult
```

Every one does the same five things: build the prompt, call
`provider.generate_structured(prompt, ResponseModel)`, check `result.data is not None`,
validate against the response model, and on any failure log a warning and return the
fallback. The shared part lives in one private `_run_pass()` helper so the sequence is
written once.

`run_normalize` takes `is_voice` and selects between two prompt templates — the voice one
carries the self-correction and `[CORRECTED_FROM]` rules, the text one only translates.
Sending the ASR rules to a typed entry would invite the model to invent corrections that
were never spoken.

## B9. Fallback Policy

The table that A6 promises. Every direction is chosen to lose quality rather than data,
and never to promote.

| Pass fails | Fallback | Why that direction |
|---|---|---|
| `CONVERSATION` | Concatenate all `USER` turns in order; no acts, no `CO_CREATED` flags | Keeps every word the user said. Losing rollup means fragmentation, which Reconciliation can still work with; losing the turns means losing the session. |
| `NORMALIZE` | Use the regex-cleaned text as-is | Untranslated, still-noisy text extracts badly — but it extracts. Discarding it because a cleaner failed would be absurd. |
| `STRUCTURE` | One episode holding the whole text; empty `CoreferenceMap` | Under-segmenting merges two topics into one episode, which Stage 2 can still retrieve against. Over-segmenting or dropping episodes cannot be undone. |
| `TRIAGE` | `RAW_CAPTURE` for every episode, score `0.0`, generic reflection prompts | The conservative direction. An unscored episode must never be promoted into full extraction. |
| Episode count over `max_episodes_per_session` | Merge the overflow into the final episode | Preserves all text while holding the contract. |

Every fallback emits `logger.warning` with the pass name, the trace id, and the reason —
never the text itself.

`coherence_score = 0.0` is also what a structurally-gated episode gets, since it was never
scored. That is a slight lie the field's type forces: it is not nullable, and making it
nullable is a shipped Goal 2 contract change that was not approved. `entry_class` is the
field to trust; if Goal 6 ever needs to tell "scored 0.0" from "never scored", the field
goes `float | None` then.

## B10. `stage.py` — The Sequence

```python
def preprocess(
    event: SessionDecayEvent,
    *,
    lightweight: LLMProvider,
    thinking: LLMProvider,
    config: AppConfig | None = None,
) -> PreprocessingResult
```

Providers are parameters, not factory lookups, so the stage is unit-testable with no
infrastructure and Goal 10 controls instantiation. Both roles are required rather than one
bundle object — `THINKING` and `LIGHTWEIGHT` are used for different passes, and making
that visible in the signature is the point.

```
1.  warn_on_multi_date(event)                                   [code]
2.  messages = strip_ai_turns(...) if not chat else all         [code]
3.  if is_chat_buffer:  CONVERSATION ────────────► summary text  [THINKING]
    else:               assemble_transcript ─────► raw text      [code]
4.  if voice:  strip_standalone_fillers                          [code]
5.  NORMALIZE ───────────────────────────────────► clean English [LIGHTWEIGHT]
6.  if check_discard(...):  return DISCARD, episodes=[]          [code]
7.  if word_count < 30:                                          [code]
       one RAW_CAPTURE episode, TRIAGE in prompts-only mode      [LIGHTWEIGHT]
       return
8.  STRUCTURE ───────────────────────────────────► episodes+coref [THINKING]
9.  TRIAGE ──────────────────────────────────────► scores+prompts [LIGHTWEIGHT]
10. assemble PreprocessingResult                                 [code]
```

**Step 6 sits after `NORMALIZE`, not before**, because the discard test is on what
survives cleaning — a buffer of pure filler is not empty until the fillers are gone.

**Session decision (step 10):** `DISCARD` if step 6 fired. Otherwise `REFLECTION` if any
episode scored ≥ `coherence_threshold`, else `RAW_CAPTURE`. Per-episode `entry_class` is
set from that episode's own score, so a `REFLECTION` session can legitimately contain
`RAW_CAPTURE` episodes — which is the whole point of decision 9.

`processing_time_ms` is measured around the entire function with
`time.perf_counter`, including LLM latency. `pending_reflections` is the flattened,
de-duplicated union of every `RAW_CAPTURE` episode's prompts.

## B11. Telemetry

Goal 4's `log_llm_call` already fires per model call and Goal 3b's filter injects the
trace id, so per-pass LLM telemetry needs no new code. Stage 0 adds one INFO line of its
own on completion, through `logging.getLogger("lumen.pipeline.preprocessing")`:
session id, source modality, chat or not, input message count, clean word count, episode
count, the decision, per-pass fallback flags, and total ms.

**No journal text in any of it.** Same rule as Goal 4 — the bodies only appear under
`LUMEN_LOG_PROMPTS=true`, which the providers already honour. Detected language codes are
logged (they are metadata, not content), which is where the declined `language_tags`
information ends up living.

## B12. Doc Amendments Required

To be applied **before** coding, as Goal 4 did, so the specs describe what gets built.

1. `Architecture.md` line 33 + line 66 — move coreference/segmentation to Stage 0; replace "held for HITL review" with `RAW_CAPTURE` routing. (A4-1, A4-2)
2. `Preprocessing.md` — add the `DISCARD` rule (A2-5); replace the fastText paragraph with LLM-based detection (A2-6); re-source reflection prompts to cleaned text (A2-7); move Semantic Day Grouping and multi-day splitting out of Stage 0 with a pointer to the ingestion layer (A2-4).
3. `Microextraction.md` — fix the `coreference_map` example to match the shipped model (A4-4); note that `overarching_themes` / `historical_era` arrive from Stage 0 rather than being extracted (A4-5).
4. `Technical_HLD.md` §5 — `PreprocessingResult.coreference_map` is typed `dict`; it is a `CoreferenceMap`. Add `source_modality` to `SessionDecayEvent`.
5. `Master_Plan.md` — record the package-vs-module deviation (B1) and tick Goal 5.
6. **Flagged, not fixed:** the `RAW_CAPTURE` observation-type conflict (A4-3) is Goal 6's to resolve.

## B13. Test Plan (~90 tests)

| File | Covers |
|---|---|
| `test_preprocessing_transcript.py` | AI-turn stripping; chat detection by shape; word count after cleaning; blake2b hash stability; multi-date warning names every date; `check_discard` on empty, whitespace-only, and all-operational buffers. |
| `test_preprocessing_fillers.py` | The doc's two worked examples verbatim; `right, so...` and `like` as a verb both survive; punctuation cleanup leaves no doubled commas; removal count is accurate; **nothing is stripped from `TEXT_ENTRY` input**. |
| `test_preprocessing_passes.py` | Each pass with a scripted `FakeLLMProvider`: correct response model passed to `generate_structured`, correct parsing. Voice vs text prompt selection. The four fallback paths, each asserting the conservative outcome **and** the warning. |
| `test_preprocessing_gate.py` | 29 vs 30 words either side of the threshold; 0.39 vs 0.4 coherence; session decision is `REFLECTION` when any episode passes; a mixed session yields differing per-episode `entry_class`; over-cap episodes merge into the last; `DISCARD` returns zero episodes. |
| `test_preprocessing_stage.py` | End-to-end on scripted fakes: the doc's code-mixed example comes back English; call counts are exactly 1 / 3 / 4 per A3; `episode_index` and `total_episodes_in_entry` are consistent; `episode_id` format; `pending_reflections` de-duplicated; `processing_time_ms` populated; `lumen.pipeline` imports nothing from `lumen.operational`. |
| `test_preprocessing_trace.py` | Under a bound trace, every log line from every pass carries the trace id; the emitted DTOs carry it; a distinctive journal sentence appears in **no** captured log line by default. |
| `test_pipeline_schemas.py` (extend) | The three new DTO fields; `source_modality` defaults to `TEXT_ENTRY`; `build_decay_event` maps `BufferSource.VOICE_NOTE` correctly. |

Every LLM interaction runs against `FakeLLMProvider` with registered scripts — no network,
no credentials, no live marker in this goal. Goal 4's `fake_scripts` autouse cleanup keeps
scripts from leaking between tests.

## B14. Build Order

0. **Doc amendments (B12).** Cheapest to do first; two of them change what gets built.
1. `pipeline.py` DTO fields + `build_decay_event` mapping + `PipelineConfig` — contracts first.
2. `transcript.py`, `fillers.py` — pure, no LLM, fully testable alone.
3. `contracts.py`, `prompts.py` — the shapes the passes hand around.
4. `passes.py` with `_run_pass()` and the fallback table.
5. `stage.py` — the sequence, the gate arithmetic, the assembly.
6. `test_preprocessing_trace.py` — once there is a real sequence to trace through.
7. `Master_Plan.md` checkbox, and Section C of this document.

---

# SECTION C — RESULTS

**Status:** ✅ Complete
**Tests:** 968 passing (799 before this goal + 169 new), 9 live tests still deselected.
**Coverage:** **100%** on `lumen/pipeline/` (all 8 modules), `lumen/config.py`, and
`lumen/schemas/pipeline.py`. Suite total 99%.

## C1. What Was Built

| Module | Contents |
|---|---|
| `pipeline/preprocessing/transcript.py` | Everything deterministic: chat detection, AI-turn stripping, rendering, word count, hashing, the discard tests, the multi-date warning. |
| `pipeline/preprocessing/fillers.py` | The regex filler table and stripper. Ten hesitation spellings, voice only. |
| `pipeline/preprocessing/contracts.py` | 8 response models (what the model is asked for) and 5 result models (what each step hands back). |
| `pipeline/preprocessing/prompts.py` | 6 templates — conversation, normalize×2, structure, triage, reflection-prompts — plus the shared system instruction. |
| `pipeline/preprocessing/passes.py` | `_request` (the shared five-beat sequence) and the 5 steps with their fallbacks. |
| `pipeline/preprocessing/stage.py` | `preprocess()`, the gate arithmetic, episode assembly, and the closing log line. |

## C2. Deviations From the Plan

1. **A fifth pass was added.** B10 folded reflection-prompt generation into `TRIAGE`, but
   the structural short-circuit skips `TRIAGE` entirely — leaving short entries, the ones
   that most need follow-up questions, with none. `run_reflection_prompts` is a separate
   LIGHTWEIGHT call on that path only. Call costs are unchanged from A3: still 1 / 3 / 4.
2. **`run_conversation` and `run_normalize` do not take `config`.** B8 gave all four passes
   a uniform signature; two of them have nothing tunable, and carrying a parameter only to
   discard it is dead weight. They take what they use.
3. **`CoreferencePayload` is separate from `CoreferenceMap`.** The model is never asked for
   `entry_id` — that is already known, and asking for it invites it to be wrong. The pass
   builds the real map around the payload.
4. **`_Trail` and `_Outcome` replaced a long parameter list.** `_finish` had grown to eleven
   parameters. Both are internal dataclasses; nothing outside the module sees them.
5. **`ConversationResult` distinguishes two kinds of empty summary.** An empty summary is
   correct when every turn was operational, and a lost conversation otherwise. The second
   case falls back to the person's own words. Not in the plan; found while writing the tests.

## C3. Three Things Caught While Implementing

1. **`"um"` is a substring of `"summarise"`.** A test asserting the filler never reached the
   prompt failed against the prompt's own instruction not to summarise. The test now
   asserts on the actual cleaned text rather than on a two-letter substring — the original
   assertion would have passed or failed on unrelated prompt wording.
2. **Typed `"um uh hmm"` is content, not noise.** A discard test written for voice was run
   against typed input and correctly refused to discard: only voice gets stripped, so for
   typed input those are three words the person chose. The behaviour was right and the test
   was wrong. Both cases are now tested, side by side.
3. **A shared `contextvars` copy was not needed here.** Unlike Goal 4's embedding pool, Stage 0
   is single-threaded throughout, so the trace context propagates without help. Confirmed by
   test rather than assumed.

## C4. What the Tests Cover

169 new tests across 5 new files plus 4 extended ones. The ones worth knowing about:

- **Both worked examples from the spec are asserted verbatim** — the filler sentence and the
  discourse-structural `right, so...` that must survive. A regression in the regex table
  fails on the doc's own text.
- **Every fallback has a test, and every step is tested against all three failure modes**
  (call fails, reply is not JSON, reply is the wrong shape) via a parametrized fixture.
- **A run with no script at all still produces a usable episode.** Every step falls back and
  the entry survives intact as `RAW_CAPTURE` — the guarantee A6 is built on.
- **Failed scoring never promotes.** Asserted directly, because that is the one fallback
  whose wrong direction would put invented conclusions into a permanent history.
- **Journal text stays out of the logs.** A distinctive sentence is asserted absent from every
  captured line across five paths: success, total failure, discard, short-entry, and the
  multi-date warning.
- **Both thresholds are tested from either side** — 29 vs 30 words, 0.39 vs 0.4 coherence.
- **`lumen/pipeline/` is proven never to mention `lumen.operational`**, which is the only way
  a runtime settings override could reappear.

## C5. Still Deferred

Unchanged from A5. Worth restating one: **`language_tags` never reaches `EpisodeNode`**, so a
translated entry is indistinguishable from a native-English one in the graph. The detected
languages and a `translated` flag are on the closing log line, so the information exists in
`lumen.jsonl` — recovering it into the graph later would mean replaying preprocessing.

One thing named for later: the `RAW_CAPTURE` observation-type conflict (A4-3) is still open
and belongs to Goal 6.
