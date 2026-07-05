# Intelligence Surfaces (Applications)

*“Applications” built on top of the Lumen database are fundamentally different from traditional features. Because the underlying data is a versioned Knowledge Graph, we refer to these top-level applications as **Intelligence Surfaces**.*

An Intelligence Surface is a read-heavy, custom-engineered aggregation layer that sits on top of the core graph. Instead of just answering a direct chat query (e.g., "What did I do yesterday?"), an Intelligence Surface traverses the graph to synthesize complex, life-level guidance.

---

## Architectural & Cost Impact

**Do Intelligence Surfaces require fundamental updates to the architecture?**
**No.** 

The brilliance of the "Late Binding" architecture is that the extraction pipeline (Stages 0 through 4) is completely decoupled from how the data is used. The pipeline's only job is to build a high-fidelity, highly connected, append-only graph. 

Intelligence Surfaces are strictly **consumers** of this graph (residing in Stage 5 Query and Stage 6 Macroextraction). They do not require new database schemas, new node types, or changes to how you log your journal entries. 

**Cost Implications:**
The cost is highly manageable because these surfaces rely on **aggregation, not raw extraction**. 
1. **Cached / Pre-computed Surfaces (e.g., Personal Laws, Trajectory Viewer):** These are generated automatically during the scheduled Monthly/Quarterly Macroextraction runs. They add perhaps 1–2 additional Gemini Pro calls per month (~$0.05 total) because they simply summarize the highly-weighted `PatternNode`s that the graph has already identified.
2. **On-Demand Surfaces (e.g., Personal Debugger, Decision Simulator):** These run in real-time when you press a button. They do a fast vector retrieval (virtually free) and 1 synthesis LLM call (Gemini Flash or Pro). Cost per use is fractions of a cent.

---

## Defining the Core Intelligence Surfaces

Here are the primary applications that can be carved out of the Lumen graph.

### 1. The Personal Laws Dashboard
* **What it is:** The translation of `PatternNode`s into immutable life rules. When a specific behavioral loop (e.g., `Unplanned Weekend → Dissatisfaction`) has been reinforced by the extraction pipeline dozens of times across multiple years, it graduates into a "Personal Law."
* **How it works:** Driven by the `Proof Chains` logic in Macroextraction. The system scans for patterns with an `episode_count > 20` and high confidence, distilling them into strict "If X, then Y" rules specific to your psychology.
* **Cost:** Pre-computed monthly. No daily cost.

### 2. The Personal Debugger
* **What it is:** A UI where you input your current raw symptoms (e.g., "slept 5 hours, feel overwhelmed, avoiding my laptop") and the system diagnoses you based on your own historical data.
* **How it works:** Built entirely on top of the **Counterfactual Retrieval** layer described in `Query/RAGArchitecture.md`. It embeds your current state, retrieves past episodes with identical `INTERNAL_STATE` observations, and outputs the most statistically likely `TRIGGER` and the historical `ACTION` that successfully fixed it.
* **Cost:** On-demand (1 Embedding call + 1 LLM synthesis call).

### 3. The Decision Simulator
* **What it is:** A tool for evaluating upcoming choices (e.g., "Should I take this new project?") against your historical environmental and psychological dependencies.
* **How it works:** You describe the environment of the proposed choice (e.g., "High autonomy, unstructured, solo work"). The system retrieves your past `ENVIRONMENTAL_DEPENDENCY` observations and compares them to the outcomes of similar past projects. It outputs a compatibility score based purely on how you have historically reacted to those variables.
* **Cost:** On-demand.

### 4. Life Trajectory Viewer
* **What it is:** A visual representation of your "Identity Evolution." It shows what themes dominated your mental space in 2024 vs 2025 vs 2026.
* **How it works:** Time-series analysis of `BeliefNode` version chains (via the `evolved_from` edges) and Archetype Shifts.
* **Cost:** Pre-computed quarterly.

### 5. Biographical Gap Detection
* **What it is:** An AI-native feature that notices what you *aren't* talking about.
* **How it works:** The system compares the semantic distribution of your graph against a baseline human distribution. If it notices you log 400 entries about productivity and career, but 0 entries about relationships or spirituality over a 6-month period, it surfaces a gentle nudge: *"Is this area stable, or is it being actively neglected?"*
* **Cost:** Pre-computed quarterly.

---

## Summary
By treating the database as the source of truth, "Chatting with your data" is just the baseline MVP. The real product value comes from these **Intelligence Surfaces**, which turn your past experiences into proactive tools for future decisions.
