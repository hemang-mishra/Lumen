# Goal 4: LLM & Embedding Provider Abstraction Layer

**Branch:** `goal4`
**Status:** ✅ Complete
**Depends on:** Goal 2 (Pydantic contracts, `ModelRole`, `ProviderConfig`) ✅, Goal 3b (trace ids, JSON logging) ✅
**Blocks:** Goal 5 (quality gate), Goal 6 (microextraction), Goal 8 (HyDE + embeddings), Goal 9 (reconciliation), Goal 13 (query formulation)

---

## Objective

Goal 2 decided *how* Lumen names its AI capabilities — five `ModelRole`s, each mapped to a
`(provider, model)` pair in `ProviderConfig`. Nothing reads that config yet.

Goal 4 builds the layer that turns a role into an actual model call: the Protocols, two real
implementations (Gemini, Ollama), a role-resolution factory, transport-level resilience, and
a deterministic fake that lets every later pipeline goal run end-to-end with no network.

After this goal, a pipeline stage asks for *what kind of thinking it needs* and never learns
which vendor answered.

---

# SECTION A — LOGIC (please verify)

*Short by design. This is what the goal means; Section B is how it's coded.*

## A1. What Gets Built

| Deliverable | What it is |
|---|---|
| **4 Protocols** | `LLMProvider`, `EmbeddingProvider`, `AudioTranscriptionProvider`, `TTSProvider`. The last two are defined but **not implemented** — they get bodies when a voice goal needs them. |
| **Gemini implementation** | `LIGHTWEIGHT` / `THINKING` text + structured generation, and `EMBEDDING` via `text-embedding-004`. |
| **Ollama implementation** | The same three roles, locally. This is what makes "run Lumen fully offline" a config change rather than a rewrite. |
| **Role-resolution factory** | `get_llm_provider(role)` / `get_embedding_provider()`. Reads `ProviderConfig`, caches instances, refuses nonsense (asking for an LLM under the `EMBEDDING` role). |
| **Typed error hierarchy + retry** | Every vendor exception becomes one of six Lumen errors, split into retryable and not. Bounded backoff around the retryable ones. |
| **`FakeLLMProvider` / `FakeEmbeddingProvider`** | Shipped in the package, registered under the provider name `"fake"`. Scripted responses; deterministic hash-derived vectors. |
| **Per-call telemetry** | One JSON log line per model call under the active `trace_id` — role, model, latency, tokens, attempt, outcome. No new table. |

## A2. The Decisions You Made

1. **Three roles now, two later.** `LIGHTWEIGHT`, `THINKING`, `EMBEDDING` get real implementations. `TRANSCRIPTION` and `TTS` get Protocols only — Goals 5–9 need none of them, and whisper.cpp brings a binary + model-file dependency that belongs with the voice-ingestion work.
2. **Provider selection is a maintainer decision made at deployment time.** The vendor-lock-in this layer defends against is *yours*, not the end user's. Which model backs `THINKING` is an operational choice about cost, latency, and privacy posture — it is not a preference the person writing journal entries expresses, and there will be no UI for it. `ProviderConfig` is therefore resolved from **env vars, falling back to code defaults**. The application builds one `AppConfig` at startup and passes it down, so in practice the environment is read once per process; changing a provider means changing the environment and restarting, which is exactly the ceremony a change of that consequence deserves. (The *reads* happen per-construction rather than at import — see A9/B-1 for why that distinction was a bug.)
3. **Provider config never passes through `user_settings`.** Goal 3 gave that table a **DB override > env var > code default** chain, and justified it partly as "what lets the Settings UI change a `ModelRole`'s provider at runtime". Per decision 2, that rationale is withdrawn: no runtime override path for provider, model, or credentials is built, and the factory does not read the operational DB at all. `user_settings` keeps its precedence chain for genuine user preferences; provider routing is simply not one of them.
4. **Credentials come from environment variables — permanently.** The `api_keys` table is not deferred, it is **dropped from the design**. Secrets live in the process environment, where the deployment already manages them: a `.env` file locally, Docker/systemd secrets in production. Storing them in the operational DB would mean inventing an encryption scheme, a key to protect that scheme, and a place to keep *that* — a chain that ends in an environment variable regardless.
5. **Sync Protocols, batch methods for throughput.** Matches Goal 3's sync repositories, keeps pipeline stages ordinary testable functions. Batching — embedding 50 observations in one call — lives *inside* `embed_batch`, not in the call signature. Optional thread-pool concurrency lives there too, but is **off by default**: against a per-minute cloud quota, parallel chunks cause more failures than they save time (A9/S-4).
6. **Pydantic model in, raw dict out.** The caller passes a `type[BaseModel]`; the provider derives the JSON schema from it and switches the vendor into native structured-output mode. What comes back is the **unvalidated** dict plus the raw text. Validation and semantic re-prompting stay Goal 7's job — the two layers never overlap.
7. **Transport retries only.** The provider retries timeouts, 429s, and 5xx/connection failures — three attempts, exponential backoff with jitter. A response that *arrived successfully but was malformed* is never retried here; it is handed up for Goal 7 to re-prompt. Two kinds of failure, two owners.
8. **Telemetry is log lines, not a table.** Goal 3b's JSON logs already carry `trace_id`. An `llm_calls` table would need a migration and has no reader until someone wants aggregate cost reporting — revisit in Goal 19.
9. **Mocked SDKs in CI, live tests opt-in.** Unit tests mock at the vendor client boundary and cover the code that is genuinely ours: request shaping and response parsing. A `@pytest.mark.live` suite, deselected by default, hits real Gemini/Ollama when a key or daemon is present.
10. **The fake ships in the package, not in tests.** Goal 10's E2E harness and any offline demo mode need it as much as the tests do.

## A3. Two Things the Specs Don't Cover That Journal Content Forces

**Safety filters will fire on real entries.** Gemini blocks content it reads as self-harm,
violence, or harassment — categories a psychological journal legitimately contains. A blocked
response is not a transient failure and must not be retried into a loop. So: safety thresholds
are set as permissive as the API allows, and anything still blocked raises a distinct
`ProviderContentBlockedError` that Goal 7 routes to human review rather than re-prompting.
This is the single most likely cause of silent data loss in Goals 6–9, and it is invisible
until it happens to a real entry.

**Prompts are journal text, so they are not logged by default.** Every call logs its metadata
— role, model, latency, tokens, outcome — but the prompt and completion bodies are omitted
unless `LUMEN_LOG_PROMPTS=true` is set for debugging. Otherwise `lumen.jsonl` quietly becomes
a second, unencrypted copy of the user's private history.

## A4. Embeddings Have a Detail That Matters Later

Retrieval is **asymmetric**: you are matching a question against an answer, and the two look
nothing alike as text. A stored `PatternNode` reads "Seeking external validation through social
comparison"; six months later the user asks "why do I keep checking how everyone else is
doing?" — barely a shared word between them. Embedding models trained on query→document pairs
handle this by taking a **task type**, so a query's vector is pushed toward documents that
*answer* it rather than documents that *resemble* it.

The Protocol therefore carries `task_type` from day one. This is not a nicety that can be
retrofitted: the choice is baked into every stored vector, so getting it wrong means
re-embedding the entire graph — the same expensive migration `Architecture.md` describes for
model upgrades.

**Both configured models support this; they just expose it differently.** Gemini takes an API
parameter. `nomic-embed-text` uses **instruction prefixes** — `search_document: `,
`search_query: `, `clustering: `, `classification: ` — prepended to the text before encoding,
and Nomic's own documentation warns that omitting them degrades retrieval. Ollama's `/embed`
endpoint has no task-type field, so the *provider* prepends the prefix string. The concept is
the model's; only the transport differs.

The prefix scheme belongs to nomic specifically, not to Ollama, so it is keyed by **model
name**, not by provider. An Ollama deployment running `mxbai-embed-large` must get no prefix
at all — blindly prepending `search_document: ` there would corrupt every vector with tokens
that model never trained on. Unknown embedding models get no prefix and one warning.

Second detail: an embedding provider **declares its dimensions**, and the factory checks them
against `VectorConfig.vector_size` (768). Both defaults are natively 768-dimensional, so they
are interchangeable without touching the Qdrant collection. Swapping to a 1024-dim model
without updating that collection would otherwise fail deep inside a write, far from the cause.

## A5. What This Goal Deliberately Leaves Undone

| Deferred | To | Why |
|---|---|---|
| `AudioTranscriptionProvider` / `TTSProvider` bodies | Voice ingestion goal | No consumer; adds a binary dependency. |
| `api_keys` table + encryption | **Never — cancelled** | Superseded by A2-4. Credentials are an environment concern, not an application-schema one. |
| Any user-facing provider/model switcher | **Never — out of scope** | A2-2. Provider choice is a maintainer decision; exposing it to the end user turns a deployment property into a support burden. |
| `llm_calls` telemetry table | Goal 19 | Logs cover debugging; a table is only needed for aggregate cost reporting. |
| Circuit breaker / provider failover | Not scheduled | Real value at production scale, meaningful state and test surface for one user. |
| Response caching / replay | Goal 10 | Belongs with the E2E harness, where replaying a run is the actual use case. |
| Streaming token output | Goal 16 | Only the chat route needs it. |
| Prompt templates and versioning | Goals 5–9 | Prompts are stage-specific; this layer transports them, it doesn't author them. |

## A6. Doc Changes This Goal Requires — ✅ applied ahead of implementation

*All seven were made before coding started, so the specs describe the system Goal 4 is
about to build rather than the one Goal 2 imagined. Two of them also required deleting
code — see A8.*

1. **`LLM_Abstraction_Architecture.md` §2A** — the sketched signature is
   `generate_structured_extraction(prompt, schema: dict) -> dict`. Update to the Pydantic-model
   contract from decision A2-6, and rename `generate_chat_response` → `generate_text`.
2. **`LLM_Abstraction_Architecture.md` §2B** — `EmbeddingProvider` gains `task_type` and a
   declared `dimensions`.
3. **`Technical_HLD.md` §4.1** — **remove** the `api_keys` row from the operational-DB diagram
   and replace the accompanying note with the rule from A2-4: provider credentials are read
   from the environment and are never stored by the application. `Goal_3_Plan.md` A2-5 and its
   deferral table record `api_keys` as "deferred to Goal 4"; add a superseding line there
   pointing at this decision rather than editing the historical record.
4. **`Technical_HLD.md` §4.1 `user_settings` note** — currently says the table holds key/value
   overrides resolved DB > env > default. Add that **provider selection and credentials are
   excluded** from that chain (A2-3). Add the same superseding line to `Goal_3_Plan.md` A2-6,
   whose stated rationale — "lets the Settings UI change a `ModelRole`'s provider at runtime" —
   no longer holds.
5. **`LLM_Abstraction_Architecture.md` §1 and §3** — both describe configuration as an
   "operator" choice, which is already correct, but §3's closing paragraph invites a reading
   where a privacy-conscious *user* reconfigures roles. Sharpen to: the maintainer chooses
   providers at deployment; the abstraction exists so that choice stays cheap for the
   maintainer, not so it becomes a runtime feature.
6. **`Technical_HLD.md` §2.8** — states the audio Protocols are "already defined". They are not;
   Goal 4 defines them. Correct the tense and mark them unimplemented.
7. **`Master_Plan.md` Goal 4** — names `lumen/providers/llm_provider.py`; the Protocols live in
   `protocols.py` because the file holds all four, not just the LLM one.

## A7. One Thing to Confirm at Implementation Time

`text-embedding-004` is the configured default and is 768-dimensional, which matches
`VectorConfig.vector_size`. Google's embedding lineup moves; if that model is retired, the
default changes in `config.py` **and** `vector_size` changes with it. The dimension check in
A4 exists precisely so this fails loudly at startup rather than at write time.

## A8. Code Already Removed to Make A2-3 True

Goal 3 shipped `resolve_provider_config()` in `operational/sqlalchemy_impl.py` — a function
that layered `user_settings` rows over `ProviderConfig` with DB > env > default precedence.
It was written for the Settings-UI rationale that A2-3 withdraws, and Goal 3's own C6 notes
that nothing ever called it. Leaving it in place would have left the codebase holding a
working runtime-override path that the design forbids, waiting for someone to wire up.

Removed: the function, its export from `lumen.operational`, and the ten generated
`providers.<role>.{provider,model}` entries in `KNOWN_SETTING_KEYS` that fed it.
`test_operational_settings.py` lost nine `TestResolveProviderConfig` cases and gained four
asserting the inverse — no settings key names a provider, and no key could carry a credential.
The remaining settings tests were rekeyed onto `logging.level` / `hitl.queue_cap`, which they
had been borrowing provider keys to exercise.

**Suite: 435 → 429 passing, all green.** Recorded in `Goal_3_Plan.md` C7.

## A9. Review Findings Folded In

A plan review before implementation found nine defects. Eight are fixed; one is open.

**Fixed in code, ahead of implementation** (both were latent bugs in shipped Goal 2 config,
not merely plan errors):

| # | The defect | The fix |
|---|---|---|
| **B-1** | Every config field read its env var as a **dataclass default**, which evaluates once at *import* time. `ProviderConfig()` after a `.env` load returned import-time values, silently. The documented per-role env override had **zero test coverage** and could not be tested as written — `monkeypatch.setenv` did nothing. | All env reads moved to `default_factory` via `_env` / `_env_int` / `_env_bool` helpers. Boolean parsing now accepts `1/yes/0/no` and keeps the default on unrecognised input instead of guessing. **24 new tests**, including all five roles redirected to a local provider — the offline-deployment path, previously unverified. |
| **S-1** | A9 claimed no key reaches the operational DB, but `pipeline_jobs.config_snapshot` is a live JSON column and `gemini_api_key` was planned as a plain field. One `asdict()` in Goal 10 and the key is in SQLite forever. Masking `__repr__` does nothing against `asdict()`. | `gemini_api_key` is a **property, not a field** — invisible to `asdict()`, `replace()`, `repr()`, and `==`. The leak is structurally impossible rather than forbidden by convention. Bonus: it re-reads env per access, so a rotated key applies without a restart. |

**Fixed in the plan:**

| # | The defect | Where |
|---|---|---|
| **B-2** | The dimension guard was circular — unknown models defaulted `dimensions` to `vector_size`, so the check could only ever pass for models already catalogued. It failed exactly where it was needed. | B8, B11 |
| **B-4** | `ThreadPoolExecutor` in `embed_batch` drops the `trace_id` ContextVar, silently voiding A9's logging guarantee on the highest-volume path. | B8, B12 |
| **S-2** | Lazy construction meant a missing key surfaced mid-pipeline, not at boot. | B11 `validate_providers()` |
| **S-3** | No `close()`, breaking the convention `GraphProvider`/`VectorProvider` set and leaving FastAPI shutdown nothing to call. | B5, B11 |
| **S-4** | An 8-second backoff ceiling against a per-*minute* quota fails all three attempts inside one exhausted window; 4 concurrent embedding workers made it likelier. | B6 (`rate_limit_backoff_max_seconds`), B8 (`embed_max_workers=1`) |
| **S-5** | `latency_ms` conflated model speed with total wall time including backoff sleeps. | B4 (`latency_ms` + `elapsed_ms`) |

Also folded in from the moderate tier: `temperature` moved to config so it can't diverge per
vendor, lazy SDK imports in the factory (fixing the build-order circularity and making an
Ollama-only deployment dependency-light), partial-batch-failure semantics stated explicitly,
and a DoD criterion for the A3 safety-block risk, which previously had a paragraph but no test.

## A10. Open Risk — Structured Output Schema Conversion (unresolved)

**Not fixed. This is the highest-risk assumption in the plan and it has no mitigation yet.**

B8 assumes the Gemini SDK converts a Pydantic model into a usable `response_schema`. Gemini's
schema support is a *subset* of JSON Schema, and `model_json_schema()` on nested models emits
`$defs`/`$ref`. Ollama's `format=` goes through its own GBNF conversion with separate limits.

Goal 6's extraction models will be the most complex in the codebase — lists of typed
observation nodes, ~29 enums, optional fields, nested causal chains — which is exactly the
shape most likely to exceed a converter. If it fails, it fails in Goal 6 with Goal 4 already
marked done, and the fallback (schema-in-prompt plus `response_mime_type="application/json"`)
is a different design.

**Mitigation: step 0 of the build order.** Spike it against a real nested model before writing
anything else.

## A11. Definition of Done

- [ ] Every pipeline-facing call goes through a Protocol; `google`/`ollama` appear in no file outside `lumen/providers/gemini.py` and `lumen/providers/ollama.py`.
- [ ] Each of the three live roles resolves to its configured provider, and each role is overridable by env var independently of the others — by the maintainer, at deployment, with no runtime or user-facing path.
- [ ] A wrong-role request and an unknown provider name both raise `ProviderConfigurationError`.
- [ ] Transient failures retry with bounded backoff; malformed-but-successful responses do not.
- [ ] Setting `LUMEN_LIGHTWEIGHT_PROVIDER=fake` runs a scripted call with zero network.
- [ ] Every call emits one log line carrying the ambient `trace_id`, with no prompt body — **including calls made on embedding worker threads**.
- [x] No API key reaches a log line, a `repr`, an error message, or the operational DB — structurally impossible via the property, asserted by 8 tests.
- [ ] `validate_providers()` fails at process start for a missing key or an unknown-dimension model, naming the env var that fixes it.
- [ ] An embedding model with unknown dimensions refuses to start rather than inheriting `vector_size`.
- [ ] A safety-blocked response raises `ProviderContentBlockedError` and is **not** retried — the A3 risk has a test, not just a paragraph.
- [ ] `close_all_providers()` releases every cached client.
- [ ] ≥90% coverage on `lumen/providers/`, live tests deselected by default.

---

# SECTION B — LOW-LEVEL DESIGN

## B1. Files

```
lumen/providers/
├── __init__.py        — public surface: get_llm_provider, get_embedding_provider, error types
├── protocols.py       — LLMProvider, EmbeddingProvider, AudioTranscriptionProvider, TTSProvider
├── results.py         — ChatMessage, LLMUsage, TextResult, StructuredResult (Pydantic)
├── errors.py          — exception hierarchy
├── retry.py           — call_with_retry
├── telemetry.py       — log_llm_call
├── gemini.py          — GeminiLLMProvider, GeminiEmbeddingProvider   [only `google.genai` import]
├── ollama.py          — OllamaLLMProvider, OllamaEmbeddingProvider   [only `ollama` import]
├── fake.py            — FakeLLMProvider, FakeEmbeddingProvider, FakeScriptRegistry
└── factory.py         — registries, get_llm_provider, get_embedding_provider, cache

lumen/tests/
├── test_providers_protocols.py
├── test_providers_errors.py
├── test_providers_retry.py
├── test_providers_factory.py
├── test_providers_fake.py
├── test_providers_gemini.py
├── test_providers_ollama.py
├── test_providers_embedding.py
├── test_providers_telemetry.py
└── test_providers_live.py        — @pytest.mark.live, deselected by default
```

New dependencies: `uv add google-genai ollama`. Note `google-genai` (the current unified SDK,
`from google import genai`), **not** the deprecated `google-generativeai`.

## B2. `config.py` Additions

Extend the existing `ProviderConfig` — it is already "the single point of configuration for
every AI provider role", so transport settings and credentials belong there rather than in a
sixth nested config object.

**Two corrections to Goal 2's config were made first** (see A9); the new fields follow the
corrected pattern:

```python
@dataclass(frozen=True)
class ProviderConfig:
    # ... existing 10 role fields unchanged (now using _env default_factory) ...

    ollama_host: str = _env("LUMEN_OLLAMA_HOST", "http://localhost:11434")

    timeout_seconds: float = _env_float("LUMEN_LLM_TIMEOUT_SECONDS", 60.0)
    thinking_timeout_seconds: float = _env_float("LUMEN_THINKING_TIMEOUT_SECONDS", 180.0)
    max_attempts: int = _env_int("LUMEN_LLM_MAX_ATTEMPTS", 3)
    backoff_base_seconds: float = _env_float("LUMEN_LLM_BACKOFF_BASE", 0.5)
    backoff_max_seconds: float = _env_float("LUMEN_LLM_BACKOFF_MAX", 8.0)
    rate_limit_backoff_max_seconds: float = _env_float("LUMEN_LLM_RATE_LIMIT_BACKOFF_MAX", 65.0)
    embed_batch_size: int = _env_int("LUMEN_EMBED_BATCH_SIZE", 32)
    embed_max_workers: int = _env_int("LUMEN_EMBED_MAX_WORKERS", 1)
    temperature: float = _env_float("LUMEN_LLM_TEMPERATURE", 0.2)
    log_prompts: bool = _env_bool("LUMEN_LOG_PROMPTS", False)

    def resolve(self, role: ModelRole) -> tuple[str, str]: ...   # existing

    def resolve_timeout(self, role: ModelRole) -> float:
        return self.thinking_timeout_seconds if role is ModelRole.THINKING else self.timeout_seconds

    @property
    def gemini_api_key(self) -> str | None:                      # ✅ already implemented
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
```

Three things worth naming:

- **`gemini_api_key` is a property, not a field.** `pipeline_jobs.config_snapshot` is a live
  JSON column that stores a config snapshot per run; a plain field would ride into SQLite on
  the first `asdict()`. A property is invisible to `asdict()`, `replace()`, `repr()`, and `==`,
  so the leak is structurally impossible rather than forbidden by convention. It also re-reads
  the environment on each access, so a rotated key applies without a restart.
- **`temperature` lives in config, not in either implementation.** A per-vendor default would
  mean switching providers silently changed extraction determinism.
- **`embed_max_workers` defaults to 1** — see the rate-limit note in B8.

## B3. `providers/errors.py`

```python
class ProviderError(Exception):
    """Base. Carries provider, model, role, attempts for log context."""
    def __init__(self, message, *, provider=None, model=None, role=None, attempts=1, cause=None)

class RetryableProviderError(ProviderError): ...
class ProviderTimeoutError(RetryableProviderError): ...
class ProviderRateLimitError(RetryableProviderError):
    retry_after_seconds: float | None      # honored by the backoff when present
class ProviderUnavailableError(RetryableProviderError): ...   # 5xx, connection refused

class ProviderConfigurationError(ProviderError): ...          # unknown name, wrong role, missing key
class ProviderResponseError(ProviderError): ...               # 4xx, empty candidates, truncation
class ProviderContentBlockedError(ProviderResponseError):
    blocked_categories: tuple[str, ...]
```

Retryability is a **type** question, never a string match on a message. Each implementation
owns a `_map_error(exc) -> ProviderError` function that translates its SDK's exceptions; that
function is the unit under test in `test_providers_errors.py`.

## B4. `providers/results.py`

```python
class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str

class LLMUsage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

class LLMResult(BaseModel):
    """Common envelope for both generation methods."""
    text: str
    provider: str
    model: str
    model_role: ModelRole
    usage: LLMUsage = LLMUsage()
    latency_ms: int          # the successful attempt alone — the model's actual speed
    elapsed_ms: int          # wall clock across all attempts, including backoff sleeps
    attempts: int = 1
    finish_reason: str | None = None

class StructuredResult(LLMResult):
    data: dict[str, Any] | None    # None when `text` was not parseable JSON
    parse_error: str | None = None
```

`data is None` is the provider's *only* judgment about content, and it is purely syntactic:
`json.loads` either worked or it did not. A JSON-parse failure is not raised — Goal 7 gets one
uniform failure path (`data is None` **or** Pydantic validation fails) and the `text` it needs
to build a corrective re-prompt.

**Two timings, because they answer different questions.** `latency_ms` measures only the
attempt that succeeded — that is the model's speed, and the number any future p50/p95 analysis
wants. `elapsed_ms` covers the whole call including retry backoff sleeps, which is what the
pipeline actually waited. Reporting one number for both would make a 3-attempt call look like
a 9-second model.

## B5. `providers/protocols.py`

```python
@runtime_checkable
class LLMProvider(Protocol):
    provider_name: str
    model_name: str
    model_role: ModelRole

    def generate_structured(
        self,
        prompt: str,
        response_model: type[BaseModel],
        *,
        system_instruction: str | None = None,
        temperature: float | None = None,
    ) -> StructuredResult: ...

    def generate_text(
        self,
        messages: list[ChatMessage],
        *,
        system_instruction: str | None = None,
        temperature: float | None = None,
    ) -> LLMResult: ...

    def close(self) -> None: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    provider_name: str
    model_name: str
    dimensions: int

    def embed_text(self, text: str, *, task_type: EmbeddingTaskType = DOCUMENT) -> list[float]: ...
    def embed_batch(self, texts: list[str], *, task_type: EmbeddingTaskType = DOCUMENT) -> list[list[float]]: ...

    def close(self) -> None: ...


class AudioTranscriptionProvider(Protocol):    # defined, unimplemented
    def transcribe(self, audio_file_path: str) -> str: ...

class TTSProvider(Protocol):                   # defined, unimplemented
    def synthesize(self, text: str, output_path: str) -> str: ...
```

`EmbeddingTaskType` is a new `StrEnum` in `lumen/schemas/enums.py` (it is a knowledge-layer
vocabulary, not process state): `DOCUMENT`, `QUERY`, `SIMILARITY`, `CLASSIFICATION`.

`embed_batch` is the primary method; `embed_text` delegates to it with a one-element list, so
implementations have one code path.

`close()` matches the convention `GraphProvider` and `VectorProvider` already set. These objects
hold HTTP clients with connection pools and the factory pins them for the process lifetime, so
FastAPI's shutdown hook needs something to call. Implementations that hold nothing releasable
(the fakes) define it as a no-op rather than omitting it, so the Protocol check stays honest.

## B6. `providers/retry.py`

```python
class RetryOutcome(NamedTuple):
    value: T
    attempts: int
    latency_ms: int      # the successful attempt alone
    elapsed_ms: int      # everything, backoff sleeps included

def call_with_retry(
    fn: Callable[[], T],
    *,
    provider: str,
    model: str,
    role: ModelRole,
    max_attempts: int,
    base_delay: float,
    max_delay: float,
    rate_limit_max_delay: float,
    sleeper: Callable[[float], None] = time.sleep,
) -> RetryOutcome: ...
```

- Retries only `RetryableProviderError`. Everything else propagates on attempt 1.
- Delay: `min(max_delay, base_delay * 2**(n-1))` with **full jitter** — `random.uniform(0, d)`.
- **Rate limits get their own, much larger ceiling.** A `ProviderRateLimitError` uses
  `rate_limit_max_delay` (default 65s) instead of `max_delay` (8s), and a `retry_after_seconds`
  from the response overrides the computed value entirely. Gemini's free tier meters per
  *minute*: three attempts inside a 15-second window all land in the same exhausted quota and
  fail together, which is the default behaviour an 8-second ceiling produces. One wait that
  crosses the minute boundary is worth more than three that don't.
- Every attempt after the first logs at WARNING with `attempt`, `error_type`, `delay_ms`.
- The final failure re-raises the last error with `attempts` populated.
- `sleeper` is injected so tests assert the delay *sequence* without spending real seconds.

**What this is not.** This is reactive backoff, not rate-limit *management*. Proactive
client-side throttling (a token bucket shared across calls) is still absent; the mitigation
for the path that would actually trip a quota — batch embedding — is to keep it sequential by
default (B8). If Goals 6–9 turn out to saturate a free tier anyway, a token bucket is the next
step, and it belongs here rather than in each implementation.

## B7. `providers/telemetry.py`

```python
def log_llm_call(*, provider, model, role, latency_ms, elapsed_ms, attempts, usage,
                 outcome, finish_reason=None, error_type=None,
                 prompt=None, completion=None, log_prompts=False) -> None
```

Emits one INFO (or ERROR) line through `logging.getLogger("lumen.providers")`. Goal 3b's
handler filter injects `trace_id`, so nothing here handles it explicitly — **provided the call
runs on a thread that inherited the trace context** (see B8's note on the embedding pool).
`prompt` and `completion` are attached **only** when `log_prompts` is true, and are truncated to
2000 chars. Both implementations call this exactly once per public method invocation, in a
`finally`, so failures are logged as well as successes.

## B8. `providers/gemini.py`

```python
class GeminiLLMProvider:
    def __init__(self, model: str, role: ModelRole, config: ProviderConfig):
        if not config.gemini_api_key:
            raise ProviderConfigurationError("GEMINI_API_KEY is not set", provider="gemini")
        self._client = genai.Client(api_key=config.gemini_api_key)
```

`generate_structured`:
- Build `GenerateContentConfig` with `response_mime_type="application/json"` and
  `response_schema=response_model` — the SDK derives the schema from the Pydantic class, so
  Lumen never hand-writes JSON Schema.
- `system_instruction` and `temperature` (default `0.2` for extraction determinism) go on the
  same config, along with the four `safety_settings` set to the most permissive threshold the
  API allows (see A3).
- Wrap the SDK call in `call_with_retry`; map exceptions via `_map_error`.
- Extract `usage_metadata` → `LLMUsage`; `candidates[0].finish_reason` → `finish_reason`.
- A `MAX_TOKENS` finish reason raises `ProviderResponseError` — a truncated JSON body is a
  transport-shaped failure, not something Goal 7 can re-prompt its way out of.
- `prompt_feedback.block_reason` or a `SAFETY` finish reason raises `ProviderContentBlockedError`
  with the triggering categories.
- `json.loads(text)`; on `JSONDecodeError` set `data=None`, `parse_error=str(exc)`, and log at
  WARNING. Do not raise.

`generate_text`: same skeleton, no schema, maps `ChatMessage` roles to the SDK's
`user`/`model` convention.

```python
_KNOWN_DIMENSIONS: dict[str, int] = {"text-embedding-004": 768, "nomic-embed-text": 768}

class GeminiEmbeddingProvider:
    dimensions: int
    def embed_batch(self, texts, *, task_type=DOCUMENT):
        # chunk by config.embed_batch_size, one client.models.embed_content call per chunk,
        # results reassembled in input order — order is a contract, callers zip against node_ids.
```

`EmbeddingTaskType` maps to the API's `RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY` /
`SEMANTIC_SIMILARITY` / `CLASSIFICATION`.

### An unknown model must not silently inherit `vector_size`

The obvious implementation — `dimensions` falls back to `config.vector.vector_size` for models
not in `_KNOWN_DIMENSIONS` — **defeats the guard it feeds**. B11 raises when
`provider.dimensions != config.vector.vector_size`; if the fallback *is* that value, the
comparison can only ever pass. The check would fire exclusively for catalogued models, which
are the ones that were never going to be wrong, and a maintainer configuring a genuinely new
1024-dim model gets precisely the deep-in-the-write failure A4 promises to prevent.

So: a model absent from `_KNOWN_DIMENSIONS` refuses to start, with a message naming the model
and both remedies — add it to the table, or set `LUMEN_VECTOR_SIZE` to its width explicitly.
Refusing is cheap and reversible; a graph full of wrong-width vectors is neither.

### The embedding pool must carry the trace context

`trace_id` is a `ContextVar`, and **context variables do not propagate into
`ThreadPoolExecutor` workers.** Dispatching chunks to a pool the naive way makes every log line
from a worker carry `trace_id=None` — silently breaking A9's "every call emits one log line
carrying the ambient `trace_id`" on the single highest-volume path in the system. Goal 3b
tested that two traces on separate threads stay isolated; this is the same mechanism failing in
the other direction.

Any pooled dispatch therefore submits through a copied context:

```python
ctx = contextvars.copy_context()
futures = [pool.submit(ctx.run, self._embed_chunk, chunk, task_type) for chunk in chunks]
```

**`embed_max_workers` defaults to 1**, making the pool opt-in rather than the norm. Four
concurrent chunks against a per-minute cloud quota is the fastest way to turn a 50-observation
batch into a rate-limit cascade (B6). Maintainers on a paid tier raise it; the default should
not assume they are.

**Partial failure is all-or-nothing.** If one chunk exhausts its retries, the whole
`embed_batch` raises and completed chunks are discarded. Returning a partial result would hand
callers a list that no longer lines up with their `node_ids`, and silent misalignment between a
node and its vector is worse than redoing the work. Goal 10 retries the stage.

## B9. `providers/ollama.py`

Structurally identical, differences only:

- `ollama.Client(host=config.ollama_host, timeout=...)`.
- Structured output uses `format=response_model.model_json_schema()`; options carry
  `temperature` and `num_ctx`.
- Connection refused → `ProviderUnavailableError` with a message naming `ollama_host`, since
  "the daemon isn't running" is the overwhelmingly likely cause and should say so.
- A 404 on the model name → `ProviderConfigurationError` telling the user to `ollama pull <model>`.
- Ollama has no rate limit; no `ProviderRateLimitError` mapping exists.
- `embed_batch` calls `client.embed(model, input=texts)`, which is natively batched — no thread
  pool needed.
- `task_type` is applied as an **instruction prefix on the text**, since Ollama's endpoint has
  no task-type field (A4):

  ```python
  # Keyed by model, not by provider — the prefix scheme is nomic's, not Ollama's.
  _MODEL_PREFIXES: dict[str, dict[EmbeddingTaskType, str]] = {
      "nomic-embed-text": {
          DOCUMENT:       "search_document: ",
          QUERY:          "search_query: ",
          SIMILARITY:     "clustering: ",
          CLASSIFICATION: "classification: ",
      },
  }
  ```

  Matching is on the model name with any `:tag` suffix stripped, so `nomic-embed-text:latest`
  and `nomic-embed-text:v1.5` both resolve. A model absent from the table gets **no prefix**
  and one `logger.warning` per provider instance (guarded by a flag, not one per call) naming
  the model and saying retrieval quality may suffer. Prepending nomic's prefixes to a model
  that never trained on them would be worse than omitting them — this is the one place where
  doing nothing is the safe default, so the warning carries the weight.

## B10. `providers/fake.py`

```python
class FakeLLMProvider:
    def __init__(self, script: Sequence[str] | dict[str, str] | Callable[[str], str] | None = None,
                 provider_name="fake", model="fake-model", role=ModelRole.LIGHTWEIGHT)
    calls: list[RecordedCall]        # prompt, response_model name, system_instruction, method
```

Resolution order: a **callable** script is invoked with the prompt; a **dict** matches the
first key that is a substring of the prompt; a **sequence** pops in order. When nothing
matches or the sequence is exhausted, it raises `FakeExhaustedError` — a fake that invents a
plausible response would let a broken prompt pass a green test suite.

`FakeScriptRegistry` is a module-level dict keyed by `ModelRole` that the factory reads when
it constructs a fake from config (`LUMEN_LIGHTWEIGHT_PROVIDER=fake`), since a script cannot
travel through an env var. `register_script(role, script)` / `clear_scripts()`; the latter runs
in an autouse fixture so tests never leak scripts into each other.

`FakeEmbeddingProvider` derives each vector deterministically from `blake2b(text)` seeding a
`random.Random`, then L2-normalizes to `dimensions` floats. Same text → identical vector;
different text → different vector. That is enough for Goal 8's retrieval tests to be meaningful
without a model, while staying reproducible across machines.

## B11. `providers/factory.py`

```python
_LLM_FACTORIES: dict[str, Callable[[str, ModelRole, ProviderConfig], LLMProvider]] = {
    "gemini": ..., "ollama": ..., "fake": ...,
}
_EMBEDDING_FACTORIES: dict[str, Callable[[str, ProviderConfig], EmbeddingProvider]] = {...}

_LLM_ROLES = frozenset({ModelRole.LIGHTWEIGHT, ModelRole.THINKING})

def get_llm_provider(role: ModelRole, config: AppConfig | None = None) -> LLMProvider
def get_embedding_provider(config: AppConfig | None = None) -> EmbeddingProvider

def validate_providers(config: AppConfig | None = None) -> None
def close_all_providers() -> None
def reset_provider_cache() -> None
```

- `config` defaults to a module-level `AppConfig()`. The factory takes **no session, no
  repository, and no operational-DB dependency** — provider selection is resolved entirely from
  env vars and code defaults (A2-2, A2-3). `lumen.providers` importing `lumen.operational` at
  all would be the smell; a test asserts that import edge does not exist.
- `get_llm_provider` rejects any role outside `_LLM_ROLES` with `ProviderConfigurationError` —
  asking for a chat model under `EMBEDDING` is a caller bug, and `TRANSCRIPTION`/`TTS` get an
  error naming the goal that will implement them.
- Unknown provider name → `ProviderConfigurationError` listing the registered names.
- Instances are cached in a module dict keyed by `(kind, role, provider_name, model_name)`;
  SDK clients hold connection pools and should not be rebuilt per observation. Because
  configuration is fixed for the life of the process (A2-2), this cache never needs
  invalidating in production — `reset_provider_cache()` exists for the autouse test fixture
  and nothing else.
- `get_embedding_provider` compares `provider.dimensions` against `config.vector.vector_size`
  and raises `ProviderConfigurationError` on mismatch, naming both numbers. Unknown models
  raise rather than defaulting into agreement (B8).
- **Provider construction is lazy** — `_LLM_FACTORIES` entries import their SDK inside the
  factory function, not at module scope. This resolves the build-order circularity (`factory.py`
  precedes `gemini.py`) and keeps `lumen.providers` importable when one vendor's package is
  absent, which is what makes an Ollama-only deployment genuinely dependency-light.

### `validate_providers()` — because lazy construction means late failure

Everything above is built on first use and cached. Left alone, a missing `GEMINI_API_KEY`
surfaces at the first model call — mid-pipeline, inside a job, after Stage 0 has already run
and written buffer state. Technically "at construction"; practically, an hour into a batch.

`validate_providers()` eagerly resolves every configured role, constructing each provider and
running the dimension check, then discards nothing (the instances stay cached, so startup
doubles as warm-up). It raises `ProviderConfigurationError` on the first problem, naming the
role, the provider, and the environment variable that would fix it.

It is called at process start — by the FastAPI lifespan hook (Goal 20) and by Goal 10's
orchestrator before it dispatches a job. Fail at boot, where a human is watching, not at
`STAGE_1_MICROEXTRACTION`.

### `close_all_providers()`

Walks the cache calling `close()` on each, then empties it. Wired to the same shutdown hook.
Without it the cached HTTP clients simply leak at exit, which is survivable but is not the
convention `GraphProvider` and `VectorProvider` already set.

## B12. Test Plan (~95 tests)

| File | Covers |
|---|---|
| `test_providers_protocols.py` | Each concrete class satisfies its `runtime_checkable` Protocol; audio/TTS Protocols exist with the documented signatures. |
| `test_providers_errors.py` | `_map_error` for both vendors: timeout, 429 (+`Retry-After`), 500, connection refused, 400, 404-model, safety block. Retryable vs not is asserted by `isinstance`. |
| `test_providers_retry.py` | Succeeds on attempt 1/2/3; exhausts at `max_attempts` re-raising the last error with `attempts` set; non-retryable propagates immediately; delay sequence within jitter bounds; `retry_after` overrides backoff; **a rate-limit error waits against `rate_limit_max_delay`, not `max_delay`**; `latency_ms` excludes backoff sleeps while `elapsed_ms` includes them; injected sleeper means no real sleeping. |
| `test_providers_gemini.py` | Mocked `genai.Client`. Schema derived from the Pydantic model; system instruction and temperature applied; safety settings present; usage parsed; unparseable JSON → `data=None` + `parse_error`, no raise; `SAFETY` → `ProviderContentBlockedError`; `MAX_TOKENS` → `ProviderResponseError`; missing key → `ProviderConfigurationError`. |
| `test_providers_ollama.py` | Mocked `ollama.Client`. `format=` carries the JSON schema; connection refused names the host; 404 suggests `ollama pull`; `generate_text` message mapping. |
| `test_providers_embedding.py` | Task-type mapping per vendor — Gemini sends the API parameter, Ollama prepends the prefix and the *unprefixed* text never reaches the client; `:tag` suffixes still match the prefix table; an unlisted model gets no prefix, warns once, and warns only once across many calls; batch chunking respects `embed_batch_size`; **output order matches input order** across chunks; **an uncatalogued model raises rather than inheriting `vector_size`** — the regression test for the circular guard; a mismatch between declared dimensions and `vector_size` raises naming both numbers; one failing chunk fails the whole batch rather than returning a short list. |
| `test_providers_trace.py` | **Under a bound trace, a pooled `embed_batch` (workers>1) emits log lines carrying that `trace_id` on every worker thread** — the `contextvars.copy_context()` regression test; two concurrent batches on separate traces do not bleed into each other. |
| `test_providers_factory.py` | Each of the 3 roles resolves to its configured provider; roles independently overridable via monkeypatched env; unknown name and wrong-role both raise; cache returns the same instance and `reset_provider_cache` clears it; `close_all_providers` calls `close()` on every cached instance and empties the cache; `validate_providers` raises at startup for a missing key and names the env var; `lumen.providers` imports nothing from `lumen.operational`, proving no runtime-settings path exists. |
| `test_providers_fake.py` | Sequence order, dict substring matching, callable script, exhaustion raises, call recording, registry set/clear, embedding determinism + normalization + dimension length. |
| `test_providers_telemetry.py` | One line per call carrying the ambient `trace_id`; failures logged too; prompt absent by default and present under `log_prompts=True`, truncated at 2000 chars. |
| `test_config.py` (extended) | ✅ **24 already added** — env read at construction not import, per-role independence, all five roles moved to a local provider, boolean parsing, and eight credential-containment cases (`asdict`, `repr`, `==`, `fields()`, rotation). Goal 4 adds: each new transport env var overrides independently, and `resolve_timeout` returns the longer budget only for `THINKING`. |
| `test_providers_live.py` | `@pytest.mark.live`, skipped without `GEMINI_API_KEY` / a reachable Ollama. One structured call and one embedding per vendor, asserting only shape and dimension. |

`pyproject.toml` gains:

```toml
[tool.pytest.ini_options]
markers = ["live: hits a real model API; requires credentials or a local daemon"]
addopts = "-m 'not live'"
```

## B13. Build Order

0. **Structured-output spike (still open — see A10).** Round-trip the most deeply nested
   Pydantic model in `lumen/schemas/pipeline.py` through both vendors' schema conversion in a
   throwaway `live` script. Everything below assumes this works; if it doesn't, B8's design
   changes and it is far cheaper to know now.
1. `errors.py`, `results.py`, `protocols.py` — contracts first, no dependencies.
2. `config.py` transport-field additions. *(The env-reading and credential fixes are done — A9.)*
3. `retry.py` + `telemetry.py` — pure, fully testable alone.
4. `fake.py` — gives the factory something to resolve before any SDK is involved.
5. `factory.py` — role resolution, guards, cache, lazy imports, `validate_providers`,
   `close_all_providers`.
6. `gemini.py`, then `ollama.py`.
7. `test_providers_trace.py` — the thread-context regression test, once a real embedding
   provider exists to pool.
8. `test_providers_live.py` and the pytest marker config.
9. `Master_Plan.md` checkbox and result line. (The A6 doc amendments and the A8/A10 code
   changes are already done.)

---

# SECTION C — RESULTS

**Status:** ✅ Complete
**Tests:** 799 passing (453 before this goal + 346 new), 9 live tests deselected by default.
**Coverage:** **100%** on `lumen/providers/` (all 11 modules) and `lumen/config.py`.

## C1. What Was Built

| Module | Contents |
|---|---|
| `providers/protocols.py` | `LLMProvider`, `EmbeddingProvider`, `AudioTranscriptionProvider`, `TTSProvider`. The audio pair is defined, unimplemented. |
| `providers/results.py` | `ChatMessage`, `LLMUsage`, `LLMResult`, `StructuredResult` — frozen Pydantic models. |
| `providers/errors.py` | 8 error types split by whether retrying could help. |
| `providers/retry.py` | `call_with_retry` — bounded backoff with full jitter, injectable clock. |
| `providers/telemetry.py` | `log_llm_call`, `log_embedding_call`. |
| **`providers/base.py`** | `BaseLLMProvider`, `BaseEmbeddingProvider` — the shared call sequence. Not in the plan; see C2. |
| `providers/gemini.py` | `GeminiLLMProvider`, `GeminiEmbeddingProvider`. |
| `providers/ollama.py` | `OllamaLLMProvider`, `OllamaEmbeddingProvider`. |
| `providers/fake.py` | `FakeLLMProvider`, `FakeEmbeddingProvider`, `FakeScriptRegistry`. |
| `providers/factory.py` | Role resolution, registry, cache, `validate_providers`, `close_all_providers`. |

## C2. Deviations From the Plan

1. **`base.py` was added** (B1 listed 9 modules; there are 10). Every provider runs the same
   sequence — send, retry, time, unpack, log — and only the sending and unpacking differ. That
   sequence now exists once, and a vendor supplies two methods. Without it the sequence would
   have been written four times and one of the steps would eventually have drifted.
2. **The fakes extend the same base classes as the real providers.** They were going to be
   standalone. Sharing the base means a test using a fake exercises the real retry, timing,
   JSON-parsing and logging path rather than a parallel one that could diverge from it.
3. **Providers accept an injected client.** Tests hand in a stand-in object instead of patching
   module globals, so what is under test is our request shaping and reply parsing, with no
   network and no credential.
4. **`FakeScriptExhaustedError` was added** to the error hierarchy, so an exhausted script is a
   provider error like any other rather than a bare exception.
5. **`log_embedding_call` is separate from `log_llm_call`.** The interesting numbers differ —
   texts and task type versus tokens and finish reason.
6. **`LUMEN_EMBEDDING_DIMENSIONS` was added.** See C3.

## C3. Four Bugs Caught While Implementing

1. **`Retry-After` was honoured without limit.** A Gemini test with `Retry-After: 17` made the
   suite take 34 seconds, which is how it was noticed — but the real problem is that the value
   arrives over the network. A server or proxy saying `3600` would have parked a pipeline run
   for an hour. It is now capped at the longest wait we are willing to take. Suite time for
   that file: 34s → 0.5s.
2. **A single `contextvars.Context` cannot be shared across threads.** The plan's fix for B-4
   copied the context *once* and passed it to every worker; entering one context from two
   threads at the same time raises. It passed the simple test — the tasks finished too fast to
   overlap — and only failed under a barrier that forced genuine concurrency. Each task now
   gets its own copy. The lesson is that the first test was passing for the wrong reason.
3. **The error message pointed at a setting nothing read.** B-2's refusal told the maintainer to
   "set `LUMEN_VECTOR_SIZE`", but nothing wired that into `resolve_dimensions`, so the advice
   was unfollowable. Adding a *separate* `LUMEN_EMBEDDING_DIMENSIONS` gives a real escape hatch
   without regressing B-2 — reusing `vector_size` would have restored the circular agreement
   that B-2 existed to remove. A stated width is still cross-checked against the store, so it
   is permission to proceed, not permission to be wrong.
4. **The fake embedding builder constructed a throwaway `AppConfig`**, ignoring the config it
   was handed. Harmless with default settings, wrong with any other vector width.

## C4. What the Tests Cover

346 new tests across 10 files. The ones worth knowing about:

- **Vendor isolation is enforced, not just intended.** One test greps the package for vendor
  imports outside `gemini.py`/`ollama.py`; another spawns a subprocess and asserts that
  `import lumen.providers` loads no `google` or `ollama` module at all.
- **No database path exists.** A test asserts `lumen/providers/` never mentions
  `lumen.operational`, which is the only way a runtime provider override could reappear.
- **The safety-block path has tests, not just a paragraph.** A refused prompt and a refused
  answer both raise `ProviderContentBlockedError`, and neither is retried.
- **Journal text stays out of the logs.** Four tests assert a distinctive sentence never appears
  in any captured log line — on success, on failure, and for embeddings.
- **Credentials cannot leak.** Eight tests: `asdict`, `repr`, `==`, `fields()`, and rotation.
- **Concurrency is real when tested.** A barrier proves the embedding pool actually uses several
  threads, so the trace-propagation test cannot pass by accident.

## C5. The Open Risk From A10 Is Resolved

The structured-output spike ran before any code was written. Schema conversion happens on the
client, so it was testable offline: the SDK accepts a Pydantic model nested three deep, with a
`StrEnum`, optional fields and repeated lists — the shape whose `model_json_schema()` emits
`$defs`/`$ref`. No hand-written schema and no fallback are needed.

Two live tests (`-m live`) confirm it end to end against the real API, including a nested model,
for when a credential is available.

## C6. Still Deferred

Unchanged from A5: the audio providers, the `llm_calls` table, circuit breaking and failover,
response replay, streaming, and prompt authoring. `api_keys` and any user-facing provider
switcher remain cancelled outright.

One thing named for later: there is still **no proactive rate limiting**, only reactive backoff.
The mitigation is that batch embedding runs sequentially by default. If Goals 6–9 saturate a
free tier, a token bucket belongs in `retry.py`.
