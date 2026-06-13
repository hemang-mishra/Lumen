content = """# Extraction Architecture: The Late Binding Model

*(This document explains the core logic of how we extract data from journals. If you are new, start with `hld/HLDv1.md` for the overarching concepts).*

## The Core Dilemma in Insight Extraction
When converting a raw journal entry into structured insights, the system faces two opposing risks:

1. **Anchoring Bias (Giving too much context):** If the AI is fed a master list of all your past patterns and beliefs before it even looks at today's journal, it stops actually reading the new text. It gets lazy and forcibly fits your new, nuanced reflection into an old, pre-existing box.
2. **Fragmentation (Giving zero context):** If the AI extracts insights in a complete vacuum every single time, you get duplicate noise. For example, over a month, it might extract *"Comparison hurts"*, *"Comparing myself to others is bad"*, and *"Social comparison causes sadness"* as three entirely separate entities, making it impossible to see that this is actually one recurring pattern.

## The Solution: Two-Step Extraction with Internal Semantic Search
To solve this, the architecture uses **Late Binding**. This means we wait as long as possible before giving the AI your historical context. We use "semantic search" (searching by meaning, not just keywords) not just for answering user questions at the end, but internally, *during the ingestion pipeline*.

### Step 1: "Blind" Microextraction (No Context)
The raw journal entry is processed in a complete vacuum. The AI extracts the episode-level data mapping to the `Microextraction.md` schema (Context, Emotions, Triggers, Patterns, Beliefs, etc.).
* **Goal:** Absolute honesty and fidelity to the current text.
* **Architecture Feature:** Because this step doesn't rely on the broader database, it is safe for **Asynchronous Batching** (processing hundreds of entries simultaneously in parallel).
* **Output:** Fresh, unanchored nodes (e.g., `New Pattern: Feeling segregated despite physical proximity to the team`).

### Step 2: Semantic Search (Candidate Retrieval)
Now, the system takes the newly extracted structured nodes and queries the Vector Database (which contains *all historical extractions*).
* **Architecture Feature:** To combat "Semantic Drift" (where the same feeling is expressed using different vocabulary), the system introduces **HyDE (Hypothetical Document Embeddings)** and **Hybrid Search** (combining exact keyword matching with underlying meaning matching) to reliably fetch the right candidates.
* **Goal:** Find historical precedent without leading the witness during Step 1.
* **Output:** The Top 3-5 closest historical matches (e.g., `Past Pattern 1: Social isolation in college groups`).

### Step 3: Reconciliation & Macroextraction (Merge or Branch)
A second AI (LLM) call is made. This "Decision Maker" prompt receives the *pure new extraction* AND the *historical candidates*. 
* **Architecture Feature:** The AI's response is tightly constrained by **JSON Schema / Function Calling**. This forces the AI to output structural database commands rather than conversational prose. It must also provide a `confidence_score`.

The AI acts as a router to determine the relationship:
1. **Instance Match:** `{"action": "MERGE", "confidence": 0.95}` - "This new thought is another occurrence of *Past Pattern 1*."
2. **Evolution (Delta):** `{"action": "EVOLVE", "confidence": 0.92}` - "The old belief was X, but today's entry updates it to Y." 
3. **Novel Branch:** `{"action": "BRANCH", "confidence": 0.88}` - "None of the historical candidates match. This is a completely new pattern."

**Human-in-the-Loop (HITL) Constraint:** If confidence falls below a certain threshold (e.g., 85%), the extraction pauses and is routed to a UI Review Queue. A human must manually click to approve the merge or branch. This prevents the Knowledge Graph from being poisoned by bad AI connections.

### Step 4: Systematized Appending & Immutability
To successfully record how you evolve over time without losing the historical "state of mind", the graph implements **Append-Only Versioning**. When your "Wisdom Evolves", `Belief Version 2` is created, leaving `Belief Version 1` immutable (unchangeable) and forever linked to its older past episodes.

## Why this Architecture is Powerful
* **Scalability:** You never hit AI "token limits" (memory limits) trying to stuff 5 years of personal growth into a single AI prompt.
* **Nuance Preservation:** By separating the extraction of *what you said today* from *what it means in the grand scheme*, you don't lose the raw truth of the moment.
* **Graph Building:** This naturally builds a knowledge graph of your life. Over time, nodes grow thicker (because you hit the exact same pattern multiple times) or create distinct edges (beliefs transforming into new, healthier beliefs).
"""

with open('Extraction/Architecture.md', 'w') as f:
    f.write(content)
