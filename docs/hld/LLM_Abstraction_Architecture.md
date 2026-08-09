# LLM Abstraction Architecture

Lumen interacts with various AI models for transcription, extraction, and reasoning. To prevent **vendor lock-in** and allow the system to scale from a fully self-hosted, offline deployment to a highly-capable cloud-API deployment, all AI integrations are routed through an abstraction layer using Python `Protocol`s.

This document describes how Lumen abstracts these dependencies.

**Whose lock-in this prevents.** The abstraction exists for whoever *deploys* Lumen — the
maintainer. It keeps swapping Gemini for Ollama a configuration change rather than a rewrite.
It is not a user-facing feature: the person writing journal entries never chooses a model, and
no UI offers them one. Provider selection is a deployment property, decided once by the
maintainer and fixed for the life of the process (Goal 4, decision A2-2).

---

## 1. The Goal: Model-Agnostic Design

Hardcoding calls to `openai` or `google-genai` restricts the app. By designing around generic capabilities rather than specific SDKs, Lumen achieves:
- **Zero Vendor Lock-in:** The underlying LLM can be swapped by the maintainer via configuration, without touching business logic.
- **Role-Based Routing:** Tasks are matched to a model-capability role — `LIGHTWEIGHT` (fast, cheap, low-risk) or `THINKING` (deeper reasoning, high-consequence) — not to a specific vendor. Microextraction and low-risk Reconciliation actions use `LIGHTWEIGHT`; Macroextraction and high-consequence Reconciliation actions use `THINKING`.
- **Single Point of Configuration:** Every role (`LIGHTWEIGHT`, `THINKING`, `EMBEDDING`, `TRANSCRIPTION`, `TTS`) is configured once, in `lumen.config.ProviderConfig`, as an independent (provider, model) pair. The abstraction never assumes or enforces where a role's provider runs — a maintainer who wants every call local reconfigures all five roles to local providers as a one-time deployment choice; the pipeline never makes that decision per piece of content at runtime.

---

## 2. The Provider Protocols

In Python, we use `typing.Protocol` (structural subtyping) to define the interfaces that any AI provider must satisfy. Lumen defines four protocols, all in `lumen/providers/protocols.py`.

Goal 4 implements `LLMProvider` and `EmbeddingProvider` for Gemini and Ollama. The two audio
protocols are **defined but not implemented** — they get bodies when voice ingestion is built.

### A. The LLM Provider

Any LLM integration (Gemini, OpenAI, Anthropic, or Ollama) must implement `LLMProvider`.

```python
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
    ) -> StructuredResult:
        """
        Forces the LLM into native structured-output mode. The caller passes a
        Pydantic model; the provider derives the JSON schema from it, so no
        schema is ever hand-written.

        Returns the *unvalidated* parsed dict alongside the raw text. Validation
        and semantic re-prompting belong to the validation layer (Goal 7), not
        here — this layer's only judgment about content is syntactic: json.loads
        either succeeded (`data` is set) or it did not (`data is None`, with the
        raw text preserved so a corrective re-prompt can be built).
        """
        ...

    def generate_text(
        self,
        messages: list[ChatMessage],
        *,
        system_instruction: str | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        """
        Free-form generation. Used for the active Chat Interface and GraphRAG.
        """
        ...
```

Both methods raise a typed error from `lumen/providers/errors.py` rather than a vendor
exception. Timeouts, rate limits, and 5xx failures are retried inside the provider with
bounded backoff; a response that arrived intact but malformed is never retried here.

### B. The Embedding Provider

Vector databases require dense arrays.

```python
class EmbeddingProvider(Protocol):
    provider_name: str
    model_name: str
    dimensions: int          # checked against VectorConfig.vector_size at construction

    def embed_text(self, text: str, *, task_type: EmbeddingTaskType = DOCUMENT) -> list[float]:
        """Dense vector for one input."""
        ...

    def embed_batch(self, texts: list[str], *, task_type: EmbeddingTaskType = DOCUMENT) -> list[list[float]]:
        """
        Vectors for a batch, returned in input order — callers zip the result
        against their node_ids, so order is part of the contract.
        """
        ...
```

`task_type` exists because retrieval is **asymmetric** — a short first-person question and the
terse declarative node that answers it share almost no vocabulary. Models trained on
query→document pairs take a task label so a query's vector is pushed toward documents that
*answer* it rather than documents that merely *resemble* it. Stage 2 and the query-time
retrieval passes both embed queries against a store of documents, so both need this.

The two configured models expose the same concept differently, and the provider hides that:

| Model | How the task type is expressed |
|---|---|
| `text-embedding-004` (Gemini) | An API parameter — `RETRIEVAL_DOCUMENT`, `RETRIEVAL_QUERY`, `SEMANTIC_SIMILARITY`, `CLASSIFICATION`. |
| `nomic-embed-text` (Ollama) | An instruction **prefix** on the text — `search_document: `, `search_query: `, `clustering: `, `classification: `. Ollama's endpoint has no task-type field, so the provider prepends it. Omitting the prefix measurably degrades retrieval. |

The prefix table is keyed by **model name, not provider** — the scheme belongs to nomic, and
prepending its prefixes to a model that never trained on them would corrupt the vectors.
Models not in the table get no prefix and a logged warning.

> **For HyDE (Stage 2):** the hypothetical answer an LLM generates from a query is embedded as
> a **document**, not a query. Converting the question into a document is the whole point of
> the technique — labelling it `QUERY` would apply the asymmetry correction twice.

`dimensions` is declared so a model swap that changes vector width fails loudly at startup
instead of deep inside a vector write. Both defaults are natively 768-dimensional, matching
`VectorConfig.vector_size`, so they are interchangeable without rebuilding the collection.

### C. The Audio Transcription Provider (STT)

To support both local `whisper.cpp` and cloud APIs. **Defined only; no implementation yet.**

```python
class AudioTranscriptionProvider(Protocol):
    def transcribe(self, audio_file_path: str) -> str:
        """
        Converts speech in an audio file to text, returning the transcript.
        """
        ...
```

### D. The TTS Provider

**Defined only; no implementation yet.**

```python
class TTSProvider(Protocol):
    def synthesize(self, text: str, output_path: str) -> str:
        """Renders text to an audio file, returning the path written."""
        ...
```

---

## 3. Configuration & Routing

The system uses a **Dependency Injection** pattern. `lumen.config.ProviderConfig` is the
single point of configuration for every role — it maps each `ModelRole` to a
`(provider, model)` pair, independently of every other role. A small factory resolves a
role to a concrete Protocol-conforming provider instance purely from this config.

```python
# lumen/config.py (already implemented — see ProviderConfig)
@dataclass(frozen=True)
class ProviderConfig:
    lightweight_provider: str = os.environ.get("LUMEN_LIGHTWEIGHT_PROVIDER", "gemini")
    lightweight_model: str = os.environ.get("LUMEN_LIGHTWEIGHT_MODEL", "gemini-2.5-flash")
    thinking_provider: str = os.environ.get("LUMEN_THINKING_PROVIDER", "gemini")
    thinking_model: str = os.environ.get("LUMEN_THINKING_MODEL", "gemini-2.5-pro")
    embedding_provider: str = os.environ.get("LUMEN_EMBEDDING_PROVIDER", "gemini")
    embedding_model: str = os.environ.get("LUMEN_EMBEDDING_MODEL", "text-embedding-004")
    transcription_provider: str = os.environ.get("LUMEN_TRANSCRIPTION_PROVIDER", "whisper_cpp")
    transcription_model: str = os.environ.get("LUMEN_TRANSCRIPTION_MODEL", "base.en")
    tts_provider: str = os.environ.get("LUMEN_TTS_PROVIDER", "macos")
    tts_model: str = os.environ.get("LUMEN_TTS_MODEL", "default")

    # Goal 4 additions: credentials and transport settings.
    gemini_api_key: str | None = os.environ.get("GEMINI_API_KEY")
    ollama_host: str = os.environ.get("LUMEN_OLLAMA_HOST", "http://localhost:11434")
    timeout_seconds: float = ...        # per-role, longer for THINKING
    max_attempts: int = ...             # transport retries
    log_prompts: bool = ...             # default False — prompts are journal text

    def resolve(self, role: ModelRole) -> tuple[str, str]:
        ...  # returns the configured (provider, model) pair for that role
```

### Configuration Is Deployment-Time and Maintainer-Owned

Two rules follow from that, and both are enforced in code:

1. **No runtime override path.** `ProviderConfig` reads the environment once at process start.
   Provider and model selection does **not** flow through `user_settings`, despite that table's
   general `DB override > env var > code default` precedence — the provider factory has no
   operational-DB dependency at all. Changing a provider means changing the environment and
   restarting.
2. **Credentials come only from the environment.** Lumen has no `api_keys` table and no
   encryption scheme for secrets; keys are supplied the way the deployment already supplies
   secrets (a `.env` file locally, Docker/systemd secrets in production). No settings row can
   ever provide one, and no key is written to a log line, a `repr`, or an error message.

### Role-Based Routing (Goal 4)

The pipeline requests a role, never a vendor. A factory (built in Goal 4) turns that role
into a concrete provider by reading `ProviderConfig`:

```python
def get_llm_provider(role: ModelRole, config: AppConfig | None = None) -> LLMProvider:
    provider_name, model_name = (config or AppConfig()).providers.resolve(role)
    return _LLM_FACTORIES[provider_name](model_name, role, config.providers)  # gemini/ollama/fake

def process_reconciliation(observation, action: ReconciliationAction):
    role = ModelRole.THINKING if action in (EVOLVE, CONTRADICT, DIALECTIC) else ModelRole.LIGHTWEIGHT
    provider = get_llm_provider(role)
    return provider.generate_structured(prompt, ReconciliationDecision)
```

A third provider name, `fake`, resolves to a scripted `FakeLLMProvider` shipped in the package.
It is what lets the extraction pipeline and its tests run end-to-end with no network.

There is no content-sensitivity branch here, and none is added upstream either — the
abstraction has no concept of "identity-critical" content to route around. A maintainer who
wants every call to run on a local, private model configures all five roles to local providers
in `ProviderConfig` once; that is a deployment choice, not a pipeline decision and not a user
setting. This keeps the boundary honest: no single vendor controls the pipeline, and a fully
local deployment is available to any maintainer who wants one, without the abstraction needing
to know why.
