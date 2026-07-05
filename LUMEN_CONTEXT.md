# LUMEN: Core Context & Architecture

**Purpose of this Document:** This file serves as the master context injection for any LLM working on the Lumen project. Read this to understand the project's philosophy, data structures, and technical implementation.

---

## 1. What is Lumen?
Lumen is not a standard journal or note-taking app. It is a **Personal Cognitive Engine** and a **Versioned Knowledge Graph** of the user's mind. 
It processes unstructured daily logs (text or voice), extracts structured psychological and behavioral patterns, and acts as an active Chat Interface (a "Chief Operating Officer for the brain") to cure "Productivity Amnesia," track identity evolution, and defeat cognitive distortions using the user's own historical data.

### Core Philosophies:
1. **Late Binding:** Observations are extracted *blindly* (zero history context) to prevent the LLM from hallucinating continuity (Anchoring Bias). History is only introduced later during Reconciliation.
2. **Append-Only Immutable Graph:** Lumen doesn't overwrite facts (e.g., "User likes X"). It tracks the *evolution* of beliefs over time using Causal Chains and `EVOLVE` edges.
3. **Abstracted Provider Routing:** Content is processed via configurable default providers. High-security (`CRITICAL` tier) data is routed to your configured high-security providers.

---

## 2. Technical Architecture: The 7-Step Pipeline

Lumen operates via a real-time Chat Interface backed by an asynchronous processing pipeline.

**The Interface Loop:**
User messages the app ➔ Lumen runs GraphRAG (Step 5) to fetch relevant history ➔ Premium LLM (`gpt-4o`) generates empathetic, context-aware response ➔ *Asynchronously*, the new log enters the extraction pipeline (Steps 0-4).

### Step 0: Preprocessing & Speaker Diarization
*   Cleans raw voice (Whisper STT) or text.
*   Separates "User" messages from "AI" prompts to ensure the AI's theories are not extracted as the user's organic thoughts.
*   Builds an intra-document coreference map.

### Step 1: Blind Microextraction (The Enums)
*   The LLM is given the preprocessed text *without any historical graph context*.
*   It extracts conceptual episodes using a strict Enum taxonomy to prevent database fragmentation.
*   **Key Enums include:** 
    *   `ENVIRONMENTAL_DEPENDENCY`: Productivity hacks and environmental rules.
    *   `SOCIAL_PERFORMANCE_STATE`: The "Narrator Mind" / Audience consciousness.
    *   `BELIEF`: Core operating rules (e.g., philosophies from books like the a philosophical text).
    *   `PATTERN`: Behaviors mapped as `[Trigger] + [Internal State] + [Action]`.
*   Every node must carry an `extraction_signal_strength` (`STANDARD`, `HIGH`, `CRITICAL`).

### Step 2: Semantic Candidate Retrieval
*   Uses **HyDE** (Hypothetical Document Embeddings) + **Hybrid Search** (BM25 + Vector Dense) to find historical nodes in the graph that match the newly extracted observations.

### Step 3: Reconciliation & Decision Audit
*   The system compares the new blind extraction against the retrieved historical nodes.
*   It executes one of 6 Graph Actions: `MERGE`, `REINFORCE`, `EVOLVE`, `BRANCH`, `CONTRADICT`, or `AMBIGUOUS` (escalates to Human-in-the-Loop review).
*   *Example:* If a belief changes, it draws an `EVOLVE` edge to preserve the lineage.

### Step 4: Graph Write
*   Appends the new nodes and Decision Audit edges to the persistent Knowledge Graph.

### Step 5: Query Layer (Multi-Hop GraphRAG)
*   The engine that powers the Chat Interface. Traces causal chains through the graph (e.g., `Belief` ➔ `Pattern` ➔ `Episode`) to inject structural psychological history into the active chat prompt.

### Step 6: Macroextraction (Periodic Synthesis)
*   Weekly, Monthly, and Quarterly batch jobs that zoom out across the graph to detect long-term `Archetype Shifts` and `Biographical Gaps` that the daily micro-extractions cannot see.

---

## 3. Cost & Infrastructure Optimization
To run an active voice/text chat interface without prohibitive API costs:
*   **Speech-to-Text (STT):** Configured via STT Provider Protocol (e.g., local whisper.cpp or cloud API).
*   **Text-to-Speech (TTS):** Configured via TTS Provider Protocol (e.g., System Neural Voices or cloud API).
*   **Background Extraction (Steps 1-4):** Fast, cheap models via the Fast LLM Provider handling structured JSON extraction.
*   **Active Chat Interface:** Premium models via the Reasoning LLM Provider fed with GraphRAG context for maximum conversational empathy.
