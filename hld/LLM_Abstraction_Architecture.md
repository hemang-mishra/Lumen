# LLM Abstraction Architecture

Lumen interacts with various AI models for transcription, extraction, and reasoning. To prevent **vendor lock-in** and allow the system to scale from a fully self-hosted, offline deployment to a highly-capable cloud-API deployment, all AI integrations are routed through an abstraction layer using Python `Protocol`s.

This document describes how Lumen abstracts these dependencies.

---

## 1. The Goal: Model-Agnostic Design

Hardcoding calls to `openai` or `google-genai` restricts the app. By designing around generic capabilities rather than specific SDKs, Lumen achieves:
- **Zero Vendor Lock-in:** The underlying LLM can be swapped via configuration without touching business logic.
- **Dynamic Routing:** Tasks with varying complexity can be routed to different providers. For example, Microextraction (low complexity) might use a local Llama model, while Macroextraction (high complexity) might use a cloud reasoning model like Gemini Pro.
- **Security-Tier Compliance:** Data tagged as `CRITICAL` can be strictly routed to a High-Security Provider (e.g., self-hosted) by checking the instance type of the injected provider.

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

The system uses a **Dependency Injection** pattern. During startup, the configuration loads the desired provider implementations.

```python
# config.py
from lumen.providers.gemini import GeminiLLMProvider
from lumen.providers.ollama import OllamaLLMProvider

class AppConfig:
    def __init__(self):
        # Default providers for STANDARD/ELEVATED tiers
        self.fast_llm = GeminiLLMProvider(model="gemini-2.5-flash")
        self.reasoning_llm = GeminiLLMProvider(model="gemini-2.5-pro")
        
        # High-Security provider for CRITICAL tier
        self.high_security_llm = OllamaLLMProvider(model="llama-3.3-8b")
```

### Context-Aware Routing
When the pipeline processes an episode, it selects a provider based on the content type and action severity.

```python
def process_reconciliation(episode, config: AppConfig):
    if episode.is_identity_critical():
        provider = config.high_security_llm
    else:
        provider = config.reasoning_llm
        
    return provider.generate_structured_extraction(...)
```

By enforcing this boundary, Lumen ensures that no single vendor controls the user's data processing pipeline, and high-security requirements can be met simply by plugging in a compliant provider.
