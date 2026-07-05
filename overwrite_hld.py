content = """# High-Level Architecture: The Late Binding Model

```text
Raw Text/Transcribed Journal (Input)
    ↓
Blind Microextraction (No Context)
    ↓
Candidate Retrieval (Semantic Search)
    ↓
Reconciliation & Macroextraction
    ↓
Knowledge Graph / Wisdom Database
    ↓
RAG Retriever (For Querying)
    ↓
LLM Reflection Layer
    ↓
Response
```

---

## Step 1: Ingestion & Blind Microextraction

**Input:** Raw Transcribed Text / Note  
*(Assuming transcription and capture happens upstream and is provided directly to the system as text).*

This is the first step of the **Late Binding Model**. To prevent *Anchoring Bias* (where the LLM forcefully fits new nuances into old patterns), the entry is processed in a complete vacuum.

**Goal:** Extract "Episode" level data mapped to the Microextraction schema (Context, Emotions, Triggers, Patterns, Beliefs).
**Output:** Fresh, unanchored nodes based strictly on the current text.

**Example Output (Unanchored Nodes):**
```json
{
  "context": "Spent 3 days avoiding pagination...",
  "emotions": ["overwhelmed", "stupid", "relieved"],
  "triggers": ["complexity", "uncertainty"],
  "patterns": ["Avoidance when uncertain"],
  "beliefs": ["Break difficult tasks into smaller chunks"]
}
```

---

## Step 2: Semantic Search (Candidate Retrieval)

To avoid *Fragmentation* (e.g., separating "Comparison hurts" from "Social comparison causes sadness"), the system needs historical context before committing data to the long-term graph.

1. The newly extracted nodes (beliefs, patterns) are converted to embeddings.
2. The system queries the Vector DB containing *all historical extractions*.

**Goal:** Find historical precedent without leading the witness during extraction.
**Result:** Returns the Top 3-5 closest historical matches (Candidate Nodes).

---

## Step 3: Reconciliation & Macroextraction

A second LLM call acts as the router/reconciler. It receives:
1. The pure new extraction (from Step 1).
2. The historical candidates (from Step 2).

The LLM determines the relationship between the new episode and the existing graph:
* **Instance Match:** "This new thought is another occurrence of *Past Pattern: Avoidance*. Update the frequency count."
* **Evolution (Delta):** "This is an evolution. The old belief was X, but today's episode updates it to Y." *(Wisdom Evolves)*.
* **Novel Branch:** "None of the historical candidates match. Create a completely new pattern node."

---

## Step 4: Structured Knowledge Graph Storage

Instead of flat tables, store the results as edges and nodes:
* **Episodes:** The daily journal events (immutable).
* **Patterns:** Recurring behaviors connected to multiple episodes.
* **Beliefs:** The evolving lessons connected to episodes and patterns.

---

## Step 5: Vector Database (For Querying)

Store embeddings of the updated Macro-level wisdom.
(e.g., ChromaDB, FAISS, Qdrant).

---

## Step 6: Querying & LLM Reflection

When user queries the system: *"I'm overwhelmed by my internship project."*

1. Create embedding for the query.
2. Search Vector DB for Past Patterns and Beliefs.
3. Pass retrieved subgraph to the LLM Reflection Layer.

**Prompting the Reflection Layer:**
```text
Current Problem:
...
Retrieved Wisdom (Evolutionary Nodes):
...
Help apply previous wisdom based on the user's specific past experiences.
```

---

# Cost Analysis (MVP)

| Component           | Cost |
| ------------------- | ---- |
| Transcription       | External/Assumed Upstream |
| Graph/Relational DB | Free (Local - SQLite/Neo4j) |
| Vector DB           | Free (Local - ChromaDB) |
| Extraction LLMs     | API Costs (Gemini/Claude) |

---

# The Truly Unique Feature: Periodic Reflection

The value isn't just discrete retrieval; it's the continuous distillation of personal wisdom.
By storing "Episodes", you can generate recurring reviews (e.g., Monthly Wisdom Report):
* Recurring Patterns (by frequency of occurrence)
* Most useful evolved beliefs
* Repeated mistakes vs. New breakthroughs

You're building a feedback loop between present Hemang and all previous Hemangs without losing the nuanced context of single episodes.
"""

with open('hld/HLDv1.md', 'w') as f:
    f.write(content)
