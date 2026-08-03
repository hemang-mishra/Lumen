# LLM Abstraction Architecture

Lumen interacts with various AI models for transcription, extraction, and reasoning. To prevent **vendor lock-in** and allow the system to scale from a fully self-hosted, offline deployment to a highly-capable cloud-API deployment, all AI integrations are routed through an abstraction layer using Python `Protocol`s.

This document describes how Lumen abstracts these dependencies.

---

## 1. The Goal: Model-Agnostic Design

Hardcoding calls to `openai` or `google-genai` restricts the app. By designing around generic capabilities rather than specific SDKs, Lumen achieves:
- **Zero Vendor Lock-in:** The underlying LLM can be swapped via configuration without touching business logic.
- **Role-Based Routing:** Tasks are matched to a model-capability role — `LIGHTWEIGHT` (fast, cheap, low-risk) or `THINKING` (deeper reasoning, high-consequence) — not to a specific vendor. Microextraction and low-risk Reconciliation actions use `LIGHTWEIGHT`; Macroextraction and high-consequence Reconciliation actions use `THINKING`.
- **Single Point of Configuration:** Every role (`LIGHTWEIGHT`, `THINKING`, `EMBEDDING`, `TRANSCRIPTION`, `TTS`) is configured once, in `lumen.config.ProviderConfig`, as an independent (provider, model) pair. The abstraction never assumes or enforces where a role's provider runs — an operator who wants every call local reconfigures all five roles to local providers as a one-time deployment choice; the pipeline never makes that decision per piece of content at runtime.

---

## 2. The Provider Protocols

In Python, we use `typing.Protocol` (structural subtyping) to define the interfaces that any AI provider must satisfy. Lumen defines three core protocols:

### A. The LLM Provider

Any LLM integration (Gemini, OpenAI, Anthropic, or Ollama) must implement `LLMProvider`.

```python
from typing import Protocol, Dict, Any, Optional

class LLMProvider(Protocol):
    def generate_structured_extraction(
        self, 
        prompt: str, 
        schema: Dict[str, Any], 
        system_instruction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Forces the LLM to return data matching a specific JSON schema.
        Used heavily in Stage 1 (Microextraction) and Stage 3 (Reconciliation).
        """
        ...

    def generate_chat_response(
        self, 
        messages: list[Dict[str, str]], 
        context: Optional[str] = None
    ) -> str:
        """
        Used for the active Chat Interface and GraphRAG generation.
        """
        ...
```

### B. The Embedding Provider

Vector databases require dense arrays. The embedding protocol is simple:

```python
from typing import Protocol, List

class EmbeddingProvider(Protocol):
    def embed_text(self, text: str) -> List[float]:
        """
        Generates a dense vector representation of the input text.
        """
        ...

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Generates vectors for a batch of inputs to save API calls.
        """
        ...
```

### C. The Audio Transcription Provider (STT)

To support both local `whisper.cpp` and cloud APIs:

```python
from typing import Protocol

class AudioTranscriptionProvider(Protocol):
    def transcribe(self, audio_file_path: str) -> str:
        """
        Converts speech in an audio file to text, returning the transcript.
        """
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

    def resolve(self, role: ModelRole) -> tuple[str, str]:
        ...  # returns the configured (provider, model) pair for that role
```

### Role-Based Routing (Goal 4)

The pipeline requests a role, never a vendor. A factory (built in Goal 4) turns that role
into a concrete provider by reading `ProviderConfig`:

```python
def get_llm_provider(role: ModelRole, config: AppConfig) -> LLMProvider:
    provider_name, model_name = config.providers.resolve(role)
    return _PROVIDER_FACTORIES[provider_name](model=model_name)  # e.g. gemini/ollama

def process_reconciliation(observation, action: ReconciliationAction, config: AppConfig):
    role = ModelRole.THINKING if action in (EVOLVE, CONTRADICT, DIALECTIC) else ModelRole.LIGHTWEIGHT
    provider = get_llm_provider(role, config)
    return provider.generate_structured_extraction(...)
```

There is no content-sensitivity branch here, and none is added upstream either — the
abstraction has no concept of "identity-critical" content to route around. If an operator
wants every call to run on a local, private model, they configure all five roles to local
providers in `ProviderConfig` once; that is a deployment choice, not a pipeline decision.
This keeps the boundary honest: no single vendor controls the pipeline, and full local/
private operation is available to anyone who wants it, without the abstraction needing to
know why they want it.
