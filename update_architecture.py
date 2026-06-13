content = """# Extraction Architecture: The Late Binding Model

## The Core Dilemma in Insight Extraction
When converting a raw journal entry into structured insights, the system faces two opposing risks:

1. **Anchoring Bias (Giving too much context):** If the LLM is fed a master list of all past patterns and beliefs prior to extraction, it stops actually reading the new text. It gets lazy and forcibly fits the new, nuanced reflection into an old, pre-existing box.
2. **Fragmentation (Giving zero context):** If the LLM extracts in a complete vacuum every time, you get duplicate noise. E.g., over a month, it extracts "Comparison hurts", "Comparing myself to others is bad", and "Social comparison causes sadness" as three entirely separate entities, making frequency tracking impossible.

## The Solution: Two-Step Extraction with Internal Semantic Search
To solve this, the architecture uses **Late Binding**. Semantic search isn't just for user Q&A—it sits *inside the ingestion pipeline*.

### Step 1: Blind Microextraction (No Context)
The raw journal entry is processed in a complete vacuum. The LLM extracts the episode-level data mapping to the `Microextraction.md` schema (Context, Emotions, Triggers, Patterns, Beliefs, etc.).
* **Goal:** Absolute fidelity to the current text.
* **Architecture Fix:** Safe for **Asynchronous Batching** since it relies on zero global state. 
* **Output:** Fresh, unanchored nodes (e.g., `New Pattern: Feeling segregated despite physical proximity to the team`).

### Step 2: Semantic Search (Candidate Retrieval)
The system takes the newly extracted structured nodes and queries the Vector DB containing *all historical extractions*.
* **Architecture Fix:** To combat Semantic Drift (vocabulary mismatch), the system introduces **HyDE (Hypothetical Document Embeddings)** and **Hybrid Search** (BM25 + Vector) to reliably fetch candidates. 
* **Goal:** Find historical precedent without leading the witness.
* **Output:** The Top 3-5 closest historical matches (e.g., `Past Pattern 1: Social isolation in college groups`).

### Step 3: Reconciliation & Macroextraction (Merge or Branch)
A second LLM call is made. This prompt receives the *pure new extraction* AND the *historical candidates*. 
* **Architecture Fix:** The LLM's response is tightly constrained by **JSON Schema/Function Calling** to enforce structural logic rather than generate prose. It produces routing intents alongside a `confidence_score`. 

The LLM acts as a router to determine the relationship:
1. **Instance Match:** `{"action": "MERGE", "confidence": 0.95}` - "This new thought is another occurrence of *Past Pattern 1*."
2. **Evolution (Delta):** `{"action": "EVOLVE", "confidence": 0.92}` - "The old belief was X, but today's entry updates it to Y." 
3. **Novel Branch:** `{"action": "BRANCH", "confidence": 0.88}` - "None of the historical candidates match. This is a completely new pattern."

**Human-in-the-Loop Constraint:** If confidence falls below 85%, the extraction is routed to a UI Review Queue to prevent Knowledge Graph poisoning.

### Step 4: Systematized Appending & Immutability
To successfully record evolution without losing the historical state of mind, the graph implements **Append-Only Versioning**. When "Wisdom Evolves", `Belief v2` is created, leaving `Belief v1` immutable and forever linked to its past temporal episodes.

## Why this Architecture is Powerful
* **Scalability:** You never hit token limits trying to stuff 5 years of personal growth into a context window.
* **Nuance Preservation:** By separating the extraction of *what you said today* from *what it means in the grand scheme*, you don't lose the raw truth of the moment.
* **Graph Building:** This naturally builds a knowledge graph of your life. Over time, nodes grow thicker (more instances of the exact same pattern) or create distinct edges (beliefs transforming into new beliefs).
"""

with open('Extraction/Architecture.md', 'w') as f:
    f.write(content)
