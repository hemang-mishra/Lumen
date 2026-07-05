# LLM & Inference Call Estimation

This document estimates the number of LLM and Embedding API calls required by the Lumen architecture. It breaks down the cost of a single journal entry, background jobs, and scheduled analysis.

## 1. Per-Entry Pipeline (The Daily Cost)

When a user logs a single journal entry (e.g., a 5-minute voice note or a 300-word text entry), it passes through Stages 0 to 4.

| Stage | Operation | Model Type | Estimated Calls | Notes |
|---|---|---|---|---|
| **Pre-Capture** | Reflection Prompt Engine | Fast LLM (Gemini Flash) | 1 call | Generates 2-3 questions before the user starts journaling. |
| **Stage 0** | Voice to Text (ASR) | STT Provider | 1 call | *If audio input.* Transcribes audio. |
| **Stage 0** | Quality Gate & Coreference | Fast LLM (Gemini Flash) | 1 call | Scores coherence, determines entry type (`REFLECTION` vs `RAW`), and generates document-scoped coreference map. |
| **Stage 1** | Microextraction | Fast LLM Provider | 1 call | Blind extraction of observations. Large entries might be chunked, but usually 1 call per entry. |
| **Stage 2** | HyDE Generation | Fast LLM (Gemini Flash) | *N* calls | Generates a hypothetical document for each extracted observation to improve retrieval. (*N* = number of observations, avg ~5 per entry). |
| **Stage 2** | Embeddings | Embedding Provider | 1 batch call | Embeds the raw observations + HyDE generations. (Batched = 1 API call). |
| **Stage 3** | Reconciliation | Reasoning LLM (Gemini Pro) | *N* calls | Compares each extracted observation against retrieved graph candidates to decide the action (`MERGE`, `EVOLVE`, etc.). |
| **Validation** | Post-Extraction Retry | Fast LLM (Gemini Flash) | ~0.2 calls | Only triggered if schema validation fails (estimated 20% retry rate). |

**Total LLM Calls per Entry:** ~3 base calls + (2 × *N* observations)  
*For an average entry yielding 5 observations: **~13 LLM calls + 1 Embedding call + 1 ASR call.***

---

## 2. On-Demand Queries (GraphRAG)

When the user asks the system a question or triggers a counterfactual retrieval.

| Operation | Model Type | Estimated Calls | Notes |
|---|---|---|---|
| Query Embedding | Embedding Provider | 1 call | Embeds the user's query. |
| Query Routing / Planning | Fast LLM | 1 call | Determines query type (Temporal, Relational, Counterfactual, etc.) and plans the graph traversal. |
| Context Compression | Fast LLM | 1 call | Summarizes the retrieved graph nodes before generation to fit context limits efficiently. |
| Final Generation | Reasoning LLM (Gemini Pro) | 1 call | Synthesizes the final answer using the compressed context. |

**Total LLM Calls per Query:** **3 LLM calls + 1 Embedding call.**

---

## 3. Scheduled Macroextraction

Periodic intelligence running in the background.

| Schedule | Operation | Model Type | Estimated Calls | Notes |
|---|---|---|---|---|
| **Weekly** | 7-Day Synthesis | Reasoning LLM | 1 call | Generates weekly trend report. |
| **Monthly** | 30-Day Deep Dive | Reasoning LLM | 1-2 calls | Analyzes archetype shifts, contradictions, and proof chains. May require chunking if node count is high. |
| **Quarterly** | 90-Day Synthesis | Reasoning LLM | ~3 calls | Deep analysis of long-term trends and identity shifts. |
| **Quarterly** | Re-embedding Migration | Embedding Provider | 1 large batch | Re-embeds all active nodes in the graph to combat vocabulary drift. |

---

## 4. Total Monthly Estimate (Active User)

Assuming an active user logging **1 entry per day**, making **5 queries a week**, and running all scheduled jobs:

### **Monthly Volume:**
- 30 Journal Entries (yielding ~150 observations)
- 20 Queries
- 4 Weekly Macroextractions
- 1 Monthly Macroextraction

### **Cumulative Calls per Month:**
- **ASR:** 30 calls
- **Embeddings:** ~50 batch calls (30 entries + 20 queries)
- **Fast LLM Provider:** ~280 calls 
  - *Breakdown: 30 pre-prompts + 30 Stage0 + 30 Stage1 + 150 HyDE + 40 Query Prep + ~6 Retries*
- **Reasoning LLM Provider:** ~175 calls 
  - *Breakdown: 150 Reconciliation decisions + 20 Query Generations + 4 Weekly + 1 Monthly*

### **Cost Optimization Strategies:**
1. **HyDE Bypassing:** Skip HyDE generation (saving 150 Fast LLM calls) for low-complexity observations (e.g., simple `CONTEXT` logs).
2. **Batching Reconciliation:** Prompt the LLM to reconcile all 5 observations from a single entry in one prompt instead of 5 separate calls. *Tradeoff: higher chance of LLM dropping constraints or mixing context.*
3. **Self-Hosted Providers:** Routing to self-hosted models for `STANDARD` and `ELEVATED` data drops the Fast LLM API cost to $0.
