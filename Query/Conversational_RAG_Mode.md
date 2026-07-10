# Conversational RAG Mode

*This document specifies the real-time retrieval layer that augments a live therapeutic chat session. It is distinct from the on-demand query surfaces (Personal Debugger, Decision Simulator) described in [`RAGArchitecture.md`](RAGArchitecture.md), which assume a user is explicitly asking a question. Conversational RAG Mode assumes the user is simply talking.*

---

## Why a Separate Mode?

The existing query architecture was designed for **pull queries** — the user asks, the system retrieves. In a live therapy session, queries are almost never explicit. Consider the following real session examples:

**June 20:**
> *"I think since childhood, I haven't gone out alone...even in campus I hesitate to go for a lone walk. I don't know, there is something that hits different."*

No question. No explicit "what does my history say about this?" But Lumen has:
- A `PatternNode`: *Fear of Alone Exploration*
- An `ENVIRONMENTAL_DEPENDENCY`: *Safety = structured institutions (campus, hostel)*
- A `BELIEF`: *Outside world is dangerous / people will trick you*
- A causal chain: *Childhood conditioning → Felt resistance → Avoidance*

If the RAG surfaces this, the AI doesn't need to re-derive the pattern from scratch mid-session. It can immediately respond from the user's *specific* history rather than from generic therapeutic knowledge.

**June 21 (evening):**
> *"A small 13 or 15 year queer kid trying to figure out what the fuck is going on...The war began."*

Lumen has:
- `historical_era: a major entrance exam_PREP`
- `IDENTITY_FUSION_STATE`: self-worth fused to a major entrance exam outcome
- `COGNITIVE_DISTORTION`: *gap treated as character flaw*
- Pattern: *Critic Brain origin — aspirations exceed tools → identity = the gap*

The AI receiving this context mid-session can make the single most therapeutically powerful move: connecting the June 21 realization directly back to the June 20 resistance to go outside alone — *they share the same root (childhood belief: I am not capable of handling the world alone)*. Without RAG, the AI can see this only if it's in its context window. With RAG, it can see it even 3 months later.

---

## Design Principles

1. **Invisible to the user.** The RAG operates as a system prompt injection before each AI turn. The user should never feel they are querying a database.
2. **Non-blocking.** The AI must never wait for RAG to respond. RAG delivers context asynchronously; if it misses the window, the AI responds without it.
3. **Conservative injection.** Better to inject less and be precise than to inject a wall of text that dilutes the AI's therapeutic focus.
4. **Sensitivity-aware.** CRITICAL-tier nodes require an explicit in-session unlock signal before injection.

---

## Architecture

```
User turn arrives
       │
       ▼
[Query Formulation Layer]   ← NEW
  Classify turn → identify retrieval triggers
       │
  ┌────┴────────────────────────────────────┐
  │                                         │
  ▼                                         ▼
[Async Retrieval]                    [AI generates response]
  Pass A: Semantic                    (no waiting)
  Pass B: Structural
  Pass C: Continuity
       │
       ▼
[Context Assembly]
  Score + rank + compress (max 400 tokens)
       │
       ▼
[Injection Decision]
  Should this context be injected into the next turn?
       │
       ▼
[System Prompt Patch]
  Injected silently before AI's next turn (if retrieval beat the AI)
  OR queued for the turn after (if AI responded first)
```

---

## Stage 1: Query Formulation Layer

The Query Formulation Layer is the component that **translates a conversational turn into a structured retrieval signal**. It is a fast, lightweight LLM call (Gemini Flash, <100ms target) that takes the current user turn and outputs a structured `RetrievalSignal`.

### RetrievalSignal Schema

```json
{
  "session_id": "sess_2026_06_21_afternoon",
  "turn_index": 4,
  "retrieval_triggers": [
    {
      "trigger_type": "PATTERN_MENTION",
      "domain": "avoidance_resistance",
      "keywords": ["going out alone", "fear", "resistance", "childhood"]
    },
    {
      "trigger_type": "HISTORICAL_ERA",
      "era": "CHILDHOOD_HOME"
    }
  ],
  "named_entities_mentioned": [],
  "emotional_register": "VULNERABLE",
  "query_formulation_confidence": 0.87
}
```

### Trigger Types

| Trigger Type | When It Fires | Retrieval Mode |
|---|---|---|
| `PATTERN_MENTION` | User describes a recurring behavior, feeling, or situation that maps to a known pattern domain | Semantic (Pass A) |
| `BELIEF_CHALLENGE` | User questions or contradicts something they previously believed | Semantic + Structural (A+B) |
| `HISTORICAL_ERA` | User references a past life period (a major entrance exam, hostel, childhood, heartbreak) | Structural (Pass B) |
| `NAMED_PERSON` | A person from the graph is mentioned | Structural (Pass B) |
| `SOMATIC_MARKER` | User describes a physical feeling (tears, tightness, weight, buzz in head) | Semantic — filter for PHYSIOLOGICAL_CAPACITY_STATE + SUPPRESSED_EMOTION_SURFACING |
| `IDENTITY_STATEMENT` | User makes a statement about who they are or are not | Semantic — filter for BELIEF and META_BELIEF nodes |
| `PROGRESS_CLAIM` | User claims a positive change ("I don't feel that anymore", "I've grown out of it") | Structural Pass B — closure detection |
| `OPEN_LOOP_MATCH` | Current turn resembles an active OpenLoopNode from a previous session | OpenLoop table lookup |
| `NO_TRIGGER` | Small talk, logistics, factual questions | No retrieval |

### Emotional Register Classification

The formulation layer also classifies the user's current emotional register. This controls injection aggressiveness:

| Register | Injection Behavior |
|---|---|
| `STABLE` | Standard injection — up to 3 nodes, 300 tokens |
| `VULNERABLE` | Conservative — 1-2 nodes max, no direct quotes, pattern-level only |
| `CRISIS` | No injection. RAG suspended. AI responds with full presence. |
| `REFLECTIVE` | Aggressive — up to 5 nodes, can include direct past quotes and causal chains |

> **Why this matters from the logs:** On June 21 when the user broke down about the "queer kid carrying things alone for seven years," the register is VULNERABLE → CRISIS. Injecting pattern data at that moment would be clinically wrong. The AI must simply be present. The RAG needs to know this and stand down.

---

## Stage 2: Three Parallel Retrieval Passes

### Pass A — Semantic Retrieval
HyDE expansion → Hybrid BM25 + Vector → Top 3–5 nodes. (Same as existing architecture.)

### Pass B — Structural Retrieval
Named-entity anchors + historical_era tags + high-sensitivity open nodes. (Defined in Architecture.md Stage 2.)

### Pass C — Session Continuity Retrieval (NEW)

Pass C is unique to Conversational RAG Mode. It maintains a `SessionContextBuffer` — a running list of the top-5 most thematically relevant nodes already surfaced in the current session. On each new turn, Pass C checks whether the new trigger is semantically adjacent to something already in the buffer.

**Why it matters:** In the June 21 session, the afternoon discussion is about "peace without the critic," and the evening is about "the war began / a major entrance exam origin." These are causally linked. Pass C ensures that when the evening turn fires a `HISTORICAL_ERA: a major entrance exam_PREP` trigger, the afternoon's `PATTERN_MENTION: critic_brain` nodes are already in the buffer and get re-injected with a boost — connecting the afternoon insight to the evening root cause. Without Pass C, each turn retrieves independently and the session loses its narrative thread.

```json
{
  "pass": "C",
  "session_context_buffer": [
    {
      "node_id": "pat_critic_brain_001",
      "injected_at_turn": 3,
      "theme": "critic_vs_healthy_motivation",
      "relevance_to_current_turn": 0.84
    }
  ],
  "action": "RE_INJECT_WITH_BOOST",
  "boost_multiplier": 1.3
}
```

**Buffer management:** Max 5 nodes. Nodes not relevant for 5+ consecutive turns are evicted. Nodes with `extraction_signal_strength: CRITICAL` are never evicted mid-session.

---

## Stage 3: Context Assembly

Candidates from Passes A, B, C are merged and ranked. The final injection block has a hard cap of **400 tokens**. This is non-negotiable.

### Ranking Formula (Conversational Mode)

```
conv_score = cosine_similarity × signal_weight × recency_weight × session_relevance_boost
```

Where `session_relevance_boost` = 1.3× if the node is already in the `SessionContextBuffer`.

### Node Compression Templates

Nodes are not injected raw. Each is compressed to a 1–2 sentence therapeutic briefing.

| Node Type | Template |
|---|---|
| `PatternNode` | *"Pattern: [label]. Appeared [N] times. Typical trigger: [trigger]. Typical outcome: [outcome]."* |
| `BeliefNode` | *"Active belief: '[content]'. Held since [date]."* |
| `CONCEPTUAL_REFRAME` | *"Reframe introduced [date]: '[content]'."* |
| `OpenLoopNode` | *"Unresolved question from [date]: '[question]'."* |
| `EpisodeNode` (causal chain) | *"On [date]: [TRIGGER] → [INTERNAL_STATE] → [ACTION] → [OUTCOME]."* |
| `META_BELIEF` | *"Core self-model: '[content]'. [Active/Superseded]."* |

### Injection Format

```
[LUMEN CONTEXT — DO NOT EXPOSE TO USER]
Use the following to deepen your therapeutic response. Do not reference it directly unless it clearly helps.

Pattern: Fear of Alone Exploration. Triggered when attempting unstructured solo activities. Linked to childhood belief: "Outside world is dangerous, I cannot handle it alone." Fades after 10–15 minutes of actual exposure (observed June 20).

Active belief: "I am the kind of person who falls short." Originated in a major entrance exam_PREP era. Partially superseded by recent reframe but still active.

Unresolved question from June 20: "Is the resistance about leaving, or about being out there alone?"
[END LUMEN CONTEXT]
```

---

## Stage 4: High-Signal Sensitive Node Handling in Live Chat

Nodes with `signal_strength: CRITICAL` that cover deeply sensitive domains (e.g. identity, trauma, intimate relationships) are **never auto-injected**. Their `CRITICAL` signal strength reflects their importance to the user's psychological history — it does not mean they are freely surfaced. The opposite is true: the higher the signal, the more deliberately the injection must be gated.

### Unlock Signal

A `CRITICAL` signal-strength node in a sensitive domain can be injected only if the user explicitly introduces that topic in the current session. The Query Formulation Layer detects this as a `CRITICAL_DOMAIN_OPENED` event. Once opened, `CRITICAL` nodes linked to that domain become eligible for injection for the rest of the session.

**Example from June 21:** When the user says *"a queer kid trying to figure out what the fuck is going on"* — this is `CRITICAL_DOMAIN_OPENED` for `QUEER_IDENTITY`. From that point, `CRITICAL` signal-strength nodes linked to identity confusion and adolescent isolation are unlocked for injection. The unlock expires at session end and must be re-triggered in a future session.

---

## Stage 5: Injection Explicitness — Model-Discretion Rule

The AI is allowed to surface Lumen context **explicitly** to the user (e.g., *"I noticed this pattern came up before..."*) but only under model discretion, with the following constraints:

### What the AI can do
- Reference a past pattern or reframe when the connection is genuinely striking and adds therapeutic value.
- Name a recurring pattern if doing so helps the user feel *seen* rather than bombarded.
- Say something like: *"This reminds me of something you noticed a few weeks ago about resistance fading after the first 10 minutes..."* — grounding the user in their own history.

### What the AI should not do
- Surface Lumen context on every turn. Most injected context should remain invisible — absorbed into the quality of the AI's insight, not explicitly cited.
- Produce a list-style data dump: *"Here are three patterns I found: 1)... 2)... 3)..."* — this destroys conversational flow.
- Interrupt an emotionally charged turn with historical references. If the register is `VULNERABLE` or `CRISIS`, the injection stays invisible.

### Frequency constraint
**At most once per 4–5 turns** should Lumen context be surfaced explicitly. The rest of the time, the context shapes the AI's reasoning silently. The user should occasionally feel *"this AI really knows me"* — not *"this AI is constantly citing its database at me."*

### Implementation note
The system prompt includes the Lumen context block unconditionally. The explicit surfacing decision is left entirely to the AI's judgment. No additional logic gate is needed — the frequency constraint is expressed as a behavioral instruction in the system prompt.

---

## Latency Budget

### Wait Window

RAG retrieval is allowed to take up to **3 seconds** before the carry-forward policy kicks in. This is the maximum tolerated wait per turn.

| Stage | Target | Notes |
|---|---|---|
| Query Formulation | <100ms | Must complete before retrieval starts |
| Pass A (Semantic) | <800ms | Can use full window if needed |
| Pass B (Structural) | <200ms (graph lookup, no embedding) | Faster — no vector math |
| Pass C (Buffer) | <20ms (in-memory) | Always succeeds |
| Assembly + Compression | <200ms | |
| **Total wait window** | **≤3 seconds** | |

### Carry-Forward Policy

Context is **never discarded**. There are two outcomes:

- **Retrieval completes within 3 seconds:** Context is injected into the current turn's system prompt before the AI generates its response.
- **Retrieval exceeds 3 seconds:** Context is carried forward and injected at the **start of the next turn** as a prefixed briefing block. The current turn proceeds without it.

Carried-forward context is tagged `retrieval_source: DEFERRED` so the AI knows it is slightly stale relative to the current conversation state. Deferred context is injected at a lower priority rank than fresh retrieval (0.9× conv_score).

> **Why 3 seconds?** The therapeutic conversational rhythm is preserved — the user is reading the previous AI response during this window. A 3-second retrieval window that runs in parallel with the user's reading time is effectively invisible. 5–10 seconds would exceed reading time and produce a felt pause.

---

## Session Lifecycle

### Session = Calendar Day

A session is defined by the **calendar date on which it was opened**, not by a chat window or an inactivity timeout. This maps naturally to the user's actual experience: sleep resets the mental state, and a new day genuinely means a new context.

**Rules:**
- Opening the chat on June 27th creates (or resumes) the June 27th session.
- Sending a message at 11:58 PM and another at 12:02 AM means the 12:02 AM message belongs to the **next day's session** — not to the previous one.
- The `SessionContextBuffer` is tied to a specific day. Crossing midnight flushes the buffer.
- **Previous day navigation:** Users can open any past day's session in read-only mode, or optionally query it ("what did we talk about on June 21?"). This works like navigating to a past chat — the past day's context is available but does not pollute the current day's `SessionContextBuffer`.

### Day Boundary & Wake Nudge

Since the intention is that the user opens a fresh chat after sleeping, the system handles the day boundary actively:

1. **First message of a new calendar day:** The system detects that the current date differs from the last active session date and automatically initializes a new day-session. The previous day's session is closed and queued for Microextraction if not already done.
2. **End-of-day prompt:** If the user is active late at night (after 10 PM local time, configurable) and sends a reflective message, the system may gently surface: *"It looks like today's session is winding down. Anything left you'd like to capture before tomorrow?"* This nudge triggers the final `OpenLoopNode` surfacing pass.
3. **Wake detection (optional, future):** If wearables or device signals are available, the system can detect a wake event and prompt the user to open a new day's chat, reinforcing the post-sleep → fresh-context → new-reflection habit loop.

```
New Day Session Start
  → Detect date change from last active session
  → Initialize new SessionContextBuffer (empty)
  → Load user's top-5 most recently active PatternNodes as day baseline
  → Generate Reflection Prompt from:
      1. Unresolved OpenLoopNodes from yesterday
      2. Active CONTRADICTION nodes
      3. Highest-priority PENDING_RERECONCILIATION items

Per Turn (within a day-session)
  → Query Formulation Layer (trigger type, emotional register)
  → Async: Passes A + B + C in parallel
  → Context Assembly + Compression (≤3s window)
  → Inject into current turn OR carry forward to next turn (if deferred)

Day Session End (midnight OR explicit close)
  → Flush SessionContextBuffer
  → Mark OpenLoopNodes surfaced today as PENDING_EXTRACTION
  → Flag day-session for Microextraction pipeline
  → Optionally surface end-of-day prompt if user is still active
```

---

## What This Unlocks: Concrete Examples from the Logs

### Example 1 — June 20: Resistance to going out alone

**Without RAG:** AI derives fear from scratch using generic therapeutic knowledge.

**With RAG:** Pass A retrieves `PatternNode: Avoidance_Solo_Exploration`. Pass B retrieves `BELIEF: Outside world is dangerous (childhood origin)`. The AI immediately knows this is not social anxiety — it is an old childhood belief embedded at the nervous system level that fades after the threshold is crossed. It can ask the precise question: *"When you went to the barber in Jabalpur alone — after 10–15 minutes, did the resistance stay, or did it fade?"* — because that question was explored before and the answer is in the graph.

### Example 2 — June 21 evening: "The war began"

**Without RAG:** The AI hears about a major entrance exam, aspiration, isolation, and queer identity all in one dense turn. It responds from that turn alone.

**With RAG:** Pass B fires `HISTORICAL_ERA: a major entrance exam_PREP`. Pass C re-injects the afternoon session's node about "peace without war — first time growth came from curiosity, not fear." The AI receives: *"This user spent today experiencing effort without an internal war for the first time. They are now discovering where that war started."* This enables the connection: *"What you described this afternoon — running, working, feeling at peace — was your first glimpse of growth without the war. And tonight you're seeing where the war started."* That is a single profound therapeutic moment made possible only by cross-session memory.

### Example 3 — June 21: The Adler book debate

**Without RAG:** The AI explains Adler generically.

**With RAG:** Pass A retrieves `PatternNode: Critic Brain` + `META_BELIEF: Gap = Character flaw (a major entrance exam origin)` + the afternoon's `CONCEPTUAL_REFRAME: healthy inferiority vs. inferiority complex`. The AI can immediately map Adler's three-way distinction onto the user's own graph: *"Your critic brain isn't Adler's healthy inferiority feeling. It's the complex — the moment the gap stopped being 'I haven't reached my goal' and became 'I am the gap.'"* The specificity is only possible with the graph.

---

## Retrieval Trigger Strategy — Trigger-Only (Cursor Model)

### Decision

**Trigger-only retrieval.** The Query Formulation Layer runs on **every turn** as a cheap, fast router. Full retrieval (Passes A + B + C) fires only when the formulation layer detects a non-trivial trigger.

### Cost Analysis

| Approach | Money Cost | Latency Cost | Recall |
|---|---|---|---|
| **Turn-level** (retrieve every turn) | ~$0.07/month (negligible) | Up to 3s overhead on every single turn, including "hmm" and "yeah okay" | Highest |
| **Trigger-only** (retrieve only on triggers) | ~$0.02/month (negligible) | 3s overhead only on ~30–40% of turns where it actually matters | Slightly lower, practically equivalent |

Money cost is irrelevant either way. The real difference is **latency on trivial turns**. A turn where the user says *"yeah that makes sense"* or *"go on"* has nothing to retrieve. Adding a 3-second wait window to those turns degrades conversational rhythm for zero gain.

### How It Works (The Cursor Architecture)

Cursor doesn't run a codebase retrieval on every message. It runs a fast **router** on every message that decides: *"Does this need context from outside the current conversation?"* If yes, it retrieves. If no, it answers immediately.

Lumen's equivalent:

```
Every turn:
  → Query Formulation Layer runs (<100ms, always)
      Outputs: RetrievalSignal with trigger_type

  If trigger_type == NO_TRIGGER:
      → AI responds immediately from conversation context
      → No retrieval. No wait. Zero overhead.

  If trigger_type != NO_TRIGGER:
      → Passes A + B + C fire async
      → 3-second wait window applies
      → Inject or carry forward
```

**`NO_TRIGGER` examples:** *"Yeah, interesting."* / *"Go on."* / *"Can you explain what you said earlier?"* / *"What time is it?"* / *"Thanks."*

**Non-trivial trigger examples:** *"I think it's my childhood."* / *"This is exactly like what happened with a major entrance exam."* / *"I don't want that person in my life anymore."* / *"I can feel that resistance in my chest again."*

The formulation layer is cheap enough to run on every turn. The retrieval pipeline is expensive enough to run only when it matters.

---

## Open Questions — All Resolved

| # | Question | Resolution |
|---|---|---|
| 1 | **Mid-stream injection** | **N/A.** Mid-stream injection means patching the AI's system prompt *while it is generating a response token by token.* Standard LLM APIs (OpenAI, Anthropic, Gemini) do not support this — a call is stateless once started. The carry-forward policy already handles this gracefully: if retrieval arrives late, it prepends to the next turn. No mid-stream mechanism is needed or worth building. |
| 2 | **Session boundary** | **Resolved.** Session = calendar day. See Session Lifecycle section. |
| 3 | **Injection explicitness** | **Resolved.** Model-discretion injection. Explicit surfacing at most once per 4–5 turns. Most context is absorbed invisibly. See Stage 5 above. |
| 4 | **Turn-level vs trigger-only** | **Resolved.** Trigger-only, with the Query Formulation Layer as an always-on cheap router. See Retrieval Trigger Strategy above. |
| 5 | **Latency budget** | **Resolved.** 3-second wait window, carry-forward (never discard). See Latency Budget section. |
