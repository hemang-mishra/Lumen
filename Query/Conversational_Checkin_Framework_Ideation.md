# Conversational Check-in & Chained Reflection Framework (Ideation)

*This document outlines the design and integration specifications for the **Interactive Check-in Engine (ICE)**. ICE replaces static forms with a dynamic, RAG-grounded conversational check-in flow, helping users explore their emotional state and improve their relationship with work, health, and philosophy.*

---

## 1. Core Philosophy: Alignment over Obligation

Traditional productivity systems fail because they treat human beings like predictable execution machines. As explored in the June 27th logs, tracking tasks or allocating points works temporarily but decays due to the **slow erosion of meaning**:

1.  **Ritual Habituation:** Intrinsic practices (like meditation or autotelic shift reflection) degrade into superficial checklist items.
2.  **Perfectionism vs. Sustainability:** When a user is emotionally low, a system that demands 100% execution triggers a comparison/critic cycle, eventually leading to the system's abandonment.
3.  **Lack of Repair:** Systems focus on "how to work harder" rather than "how to reconnect when resistance is high."

The Conversational Check-in Engine resolves this by facilitating a **relationship check-in** before action begins. It prioritizes emotional regulation, alignment of intent, and dynamic, RAG-informed questioning.

---

## 2. Interactive Check-in Engine (ICE) Architecture

ICE acts as a conversational wrapper. During an active check-in, standard real-time Microextraction (Stage 1) is suspended to avoid polluting the graph with fragmented, mid-turn venting. The entire check-in is batched and extracted only when finalized.

```
       [User initiates check-in]
                  │
                  ▼
         [Run RAG Primer Pass] ────► Pulls active patterns, beliefs, open loops
                  │
                  ▼
       [Initialize Session Buffer] ──► Tagged: CHECK_IN_ACTIVE
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
  [User Message] ◄──► [Chaining Router] (Gemini Flash)
                            │
                            ├─► action == DEEPEN (Ask follow-up)
                            ├─► action == TRANSITION (Move to next stage)
                            └─► action == CONCLUDE (End check-in)
                                     │
                                     ▼
                           [Premium Synthesizer] (Gemini Pro)
                                     │
                                     ├─► Generate Mirror, Patterns, & Micro-Repair
                                     └─► Write unified EpisodeNode to Graph
```

---

## 3. The RAG Primer Pass

Before the first question is asked, ICE retrieves the user's active context to prime the Chaining Router.

### Retrieval Strategy
The system runs parallel semantic (Pass A) and structural (Pass B) queries on the Knowledge Graph with the check-in's domain filter (e.g., `work`, `health`, `philosophy`).

*   **Target Nodes:**
    *   `PatternNode`: e.g., `Critic_Brain_Overload` (frequency, typical triggers).
    *   `BeliefNode`: e.g., `Worth = Quality of Work` (current version, evolution history).
    *   `OpenLoopNode`: Unresolved questions from the last 7 days.
    *   `EpisodeNode` (Causal Chains): Causal chains of past successful resolutions of similar states (positive `OUTCOME` valence).

### Example Primer Payload (JSON)
```json
{
  "checkin_domain": "work",
  "retrieved_context": {
    "active_patterns": [
      {
        "id": "pat_critic_brain_001",
        "description": "Critic brain hijacks curiosity when tasks feel ambiguous, translating progress gaps into character flaws."
      }
    ],
    "active_beliefs": [
      {
        "id": "bel_worth_eq_work_v2",
        "content": "My identity is too closely related to the quality of my work.",
        "status": "ACTIVE"
      }
    ],
    "open_loops": [
      {
        "id": "ol_june_27_001",
        "question": "How do we build systems that can survive uncertain, emotionally low days?"
      }
    ]
  }
}
```

---

## 4. Check-in Templates & Stages

Each check-in domain runs through a structured sequence of stages, but the transition and wording within each stage are dynamically managed.

### A. Morning Work Check-in
Designed to align the user's internal state with their task list, optimizing their *relationship with work* before starting the day.

| Stage | Purpose | Static Archetype Prompt |
|---|---|---|
| **1. Story** | Reconnecting with meaning and the larger narrative. | *"What story are you stepping into today? Why does today's work matter?"* |
| **2. Relationship** | Surfacing emotional stance towards work (curiosity vs. obligation). | *"How are we doing? How does the work feel right now (excited, resistant, distant)?"* |
| **3. Repair** | Finding the smallest intervention to remove friction and reconnect. | *"What do we need to reconnect? (clarity, permission to fail, a 5-minute start)?"* |
| **4. Plan** | Establishing a single, meaningful next action. | *"What is the next meaningful step? Choose one concrete action."* |

### B. Gym/Physical Check-in
Designed to manage somatic awareness and prevent burnout or emotional avoidance disguised as physical fatigue.

| Stage | Purpose | Static Archetype Prompt |
|---|---|---|
| **1. Somatic Marker** | Tuning into physical sensations and energy levels. | *"How does your body feel right now? Where are you holding tension?"* |
| **2. Motivation Source**| Checking whether exercise is driven by inspiration or guilt/comparison. | *"Why are we going to the gym today? Are we taking care of our body, or keeping score?"* |
| **3. Intention** | Defining a supportive execution plan matching the energy state. | *"What does a supportive, sustainable workout look like today?"* |

### C. Philosophical (Vedantic) Check-in
Designed to revise universal concepts and align daily behaviors with core philosophical beliefs.

| Stage | Purpose | Static Archetype Prompt |
|---|---|---|
| **1. Concept Recall** | Recalling a specific philosophical lens or value. | *"Which philosophical concept or a philosophical text principle feels relevant to your mind today?"* |
| **2. Application** | Identifying how it applies to current daily activities. | *"In what area of your life/work today can you practice this principle?"* |
| **3. Obstacles** | Anticipating where ego, attachment, or habit loops will resist. | *"Where do you expect your mind to resist this principle today? What is the trigger?"* |
| **4. Alignment** | Committing to a mindful reaction pattern. | *"How will you recognize that trigger and respond from awareness instead of reaction?"* |

---

## 5. Chaining Router: Dynamic Questioning Logic

Rather than presenting questions as a static questionnaire, the **Chaining Router** (Gemini Flash, target latency <100ms) runs at every user turn to keep the check-in conversational.

### The Decision Tree
On receiving the user's message, the Router decides:
1.  **`DEEPEN`:** The user's response was generic, touched on a known historical pattern, or contains suppressed emotional signals. Ask a targeted, RAG-grounded follow-up.
2.  **`TRANSITION`:** The user has answered the active stage's core question. Move to the next stage of the active template.
3.  **`CONCLUDE`:** The final stage is resolved. End the check-in and hand over to the Synthesizer.

### Guardrails against Fatigue
To prevent the feeling of being "grilled," the engine enforces a hard constraint of **max 2 deepening turns per stage**. If the user remains vague or resistant after two follow-ups, the Router automatically triggers a `TRANSITION` to keep the session moving.

### System Prompt for Chaining Router (Conceptual)
```text
You are the Dialogue Router for the Lumen Interactive Check-in Engine (ICE).
Your goal is to guide the user through the stages of the [ACTIVE_TEMPLATE].

Inputs:
- Active Stage: [CURRENT_STAGE]
- Dialogue History: [HISTORY]
- RAG Primer Context: [RAG_CONTEXT]
- User Message: "[USER_MESSAGE]"

Instructions:
1. Read the User Message. If it contains references to past patterns in [RAG_CONTEXT], or indicates unresolved emotional tension, select Action: "DEEPEN".
2. If the user has answered the active stage's core question satisfactorily, select Action: "TRANSITION" to the next stage.
3. Keep your questions extremely brief, conversational, and non-judgmental. Address the user's "work relationship" rather than evaluating their output.
4. Never execute more than 2 DEEPEN actions in a single stage.

Output JSON format:
{
  "action": "DEEPEN" | "TRANSITION" | "CONCLUDE",
  "reasoning": "...",
  "next_stage": "...",
  "next_question": "..."
}
```

---

## 6. End-of-Session Synthesis & Graph Ingestion

Once the check-in concludes, the Premium Synthesizer (Gemini Pro) evaluates the transcript of the entire dialogue.

### Synthesis Deliverables
It generates a structured synthesis block for the user containing:
1.  **The Mirror:** An empathetic reflection of their current cognitive/emotional stance (e.g., *"Today, your excitement is fueled by the novelty of a restart, but you are carrying quiet anxiety about repeating the a major entrance exam-era perfectionism cycle"*).
2.  **Longitudinal Mapping:** Citing explicit connections to past episodes/patterns in the graph (e.g., *"This matches the pattern observed on June 20, where resistance dissipated after you committed to a simple 10-minute start"*).
3.  **The Smallest Repair:** A concrete micro-action for the day (e.g., *"Your repair action for today: Write code with the deliberate intention of making it messy, then refactor. Bypasses the critic"*).

### Graph Ingestion Schema
The Synthesizer converts the session details into a structured `EpisodeNode` containing the conversation details, the co-created insights, and any new open loops.

```yaml
EpisodeNode:
  id: ep_checkin_2026_06_28_work
  created_at: "2026-06-28T12:10:00Z"
  session_type: "CHECK_IN"
  domain: "work"
  transcript_summary: "Morning work check-in focused on restarting the parser framework..."
  emotions: ["excited", "anxious", "aligned"]
  resolved_stages:
    story: "Restarting the parser framework because we love building clean compilers."
    relationship: "Excited by novelty, but carrying background perfectionism."
    repair: "Permission to write imperfect, messy code first."
    plan: "Write one single test and get it to pass."
  co_created_insights:
    - type: CONCEPTUAL_REFRAME
      content: "Writing messy initial code is not a capacity failure; it is a tactical bypass of the critic brain."
      provenance: CO_CREATED
  open_loops: []
```

This node is committed via the Stage 4 (Graph Write) pipeline, triggering Reconciliation (Stage 3) to link the `CONCEPTUAL_REFRAME` to the canonical `PatternNode: Critic_Brain` and update the `last_reinforced_at` timestamp.
