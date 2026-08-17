# Conversational RAG Mode

*This document specifies the real-time retrieval layer that augments a live therapeutic chat session. It is distinct from the on-demand query surfaces (Personal Debugger, Decision Simulator) described in [`RAGArchitecture.md`](RAGArchitecture.md), which assume a user is explicitly asking a question. Conversational RAG Mode assumes the user is simply talking.*

---

## Why a Separate Mode?

The existing query architecture was designed for **pull queries** — the user asks, the system retrieves. In a live therapy session, queries are almost never explicit. Consider the following real session examples:

**Session A:**
> *"I think since childhood, I haven't gone out alone...even in campus I hesitate to go for a lone walk. I don't know, there is something that hits different."*

No question. No explicit "what does my history say about this?" But Lumen has:
- A `PatternNode`: *Fear of Alone Exploration*
- An `ENVIRONMENTAL_DEPENDENCY`: *Safety = structured institutions (campus, hostel)*
- A `BELIEF`: *Outside world is dangerous / people will trick you*
- A causal chain: *Childhood conditioning → Felt resistance → Avoidance*

If the RAG surfaces this, the AI doesn't need to re-derive the pattern from scratch mid-session. It can immediately respond from the user's *specific* history rather than from generic therapeutic knowledge.

**Session B (evening):**
> *"A small 13 or 15 year old teenager feeling completely lost and isolated...The struggle began."*

Lumen has:
- `historical_era: HIGH_SCHOOL`
- `IDENTITY_FUSION_STATE`: self-worth fused to academic performance
- `COGNITIVE_DISTORTION`: *gap treated as character flaw*
- Pattern: *Critic Brain origin — aspirations exceed tools → identity = the gap*

The AI receiving this context mid-session can make the single most therapeutically powerful move: connecting the Session B realization directly back to the Session A resistance to go outside alone — *they share the same root (childhood belief: I am not capable of handling the world alone)*. Without RAG, the AI can see this only if it's in its context window. With RAG, it can see it even 3 months later.

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

The Query Formulation Layer is the component that **translates a conversational turn into a structured retrieval signal**. It is a fast, lightweight LLM call that takes the current user turn and outputs a structured `RetrievalSignal`.

Three things run around that call, and each is there because the call alone is not enough:

1. **A deterministic crisis floor runs first.** A short, fixed list of unambiguous distress phrases lives in code. If one appears, the turn is `CRISIS` regardless of what the model would have said, and no model call is made. The model may still *escalate* an ordinary-looking turn to `CRISIS`; it can never lower one the floor set. The asymmetry is deliberate — being wrong in the permitted direction costs one skipped lookup.
2. **Pure acknowledgements skip the call entirely.** An exact-match list of complete turns (`yeah`, `go on`, `thanks`, …) is answered `NO_TRIGGER` without a model. This is explicitly **not** a length rule: the shortest turns in a therapeutic conversation are often the heaviest.
3. **Triggers are grounded against the graph before they leave.** A `NAMED_PERSON` whose name has no `PersonEntityNode`, a `HISTORICAL_ERA` naming a period this history does not use, or an `OPEN_LOOP_MATCH` with no open loops in existence is dropped rather than passed to retrieval. An ungrounded trigger consumes the whole 3-second budget and returns nothing — indistinguishable, downstream, from a person who genuinely has no history on the subject.

Era grounding deserves its own note. `historical_era` / `era_tag` are **free-text columns with no controlled vocabulary** — they hold whatever was written when the record was made. A model left to answer freely returns `HIGH_SCHOOL` against a graph storing `high school years`, which matches nothing, silently, forever. So the prompt is given the user's real era names (via `ReadOnlyGraph.list_era_tags()`), and any era outside that list is rejected. The spelling that reaches the trigger is always the graph's own, since only that one will match.

The turn is classified against a small window of preceding turns (default 4). Several trigger types cannot be recognised from a sentence in isolation: "I don't feel that anymore" is a `PROGRESS_CLAIM` only if you can see what "that" was.

### RetrievalSignal Schema

```json
{
  "session_id": "sess_example_afternoon",
  "turn_index": 4,
  "retrieval_triggers": [
    {
      "trigger_type": "PATTERN_MENTION",
      "domain": "BEHAVIORAL",
      "keywords": ["going out alone", "fear", "resistance", "childhood"]
    },
    {
      "trigger_type": "HISTORICAL_ERA",
      "era": "CHILDHOOD_HOME"
      // ^ only ever a spelling this user's graph actually holds
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

> **Why this matters from the logs:** In Session B when the user broke down about the "teenager carrying things alone for seven years," the register is VULNERABLE → CRISIS. Injecting pattern data at that moment would be clinically wrong. The AI must simply be present. The RAG needs to know this and stand down.

---

## Stage 2: Three Parallel Retrieval Passes

> **Correction to "parallel".** Passes A and B genuinely run side by side under one
> shared wall-clock budget. Pass C cannot: its whole job is to measure today's already-surfaced
> nodes against *this* turn, and the measurement it needs is the one Pass A has just computed.
> Giving Pass C its own embedding call to measure the same sentence a second time would double
> the cost of a turn to learn nothing. The shipped order is **A ∥ B, then C** — and C is
> arithmetic on numbers already in memory, so it adds about a millisecond.

### Pass A — Semantic Retrieval
HyDE expansion → Hybrid BM25 + Vector → Top 3–5 nodes. (Same as existing architecture.)

One HyDE call covers every trigger on the turn, batched and aligned by index; a missing
hypothetical falls back to the turn's own words rather than shifting the list up, because
searching one trigger with another trigger's text returns confident wrong nodes. Sparse/BM25
remains unimplemented — the provider logs a warning and searches dense-only, as it has since
Goal 1.

Two triggers narrow the search to particular node kinds, because they name experiences the
graph records under specific types: `SOMATIC_MARKER` → `PHYSIOLOGICAL_CAPACITY_STATE` /
`SUPPRESSED_EMOTION_SURFACING`; `IDENTITY_STATEMENT` → `BeliefNode` plus `BELIEF` /
`META_BELIEF` / `IDENTITY_FUSION_STATE` observations. The rest search unrestricted.

### Pass B — Structural Retrieval
Named-entity anchors + historical_era tags + high-sensitivity open nodes. (Defined in Architecture.md Stage 2.)

Which anchors run is decided by the trigger, as a table rather than a chain of conditions:

| Trigger | Anchors followed |
|---|---|
| `NAMED_PERSON` | everything mentioning them, plus the patterns and beliefs those notes became |
| `HISTORICAL_ERA` | everything tagged with that era, in the graph's own spelling |
| `OPEN_LOOP_MATCH` | the open-loop table |
| `PROGRESS_CLAIM` | open loops **and** the current standing records in the trigger's domain |
| `BELIEF_CHALLENGE` | the current beliefs in the trigger's domain |
| `PATTERN_MENTION`, `SOMATIC_MARKER`, `IDENTITY_STATEMENT` | none — semantic answers them |

**`PROGRESS_CLAIM` → "closure detection" is defined here for the first time.** The document
named the behaviour without saying what it looks up. A claim that something has changed is a
claim about a *specific* standing record, and the only way to judge it is to have that record
and the questions left open beside it — so both are fetched.

A trigger with no anchor half leaves this pass with nothing to run, which is recorded as *not
having run* rather than as having run and found nothing. The distinction matters downstream:
"the graph was asked and said nothing" and "the graph was never asked" are different facts.

### Pass C — Session Continuity Retrieval (NEW)

Pass C is unique to Conversational RAG Mode. It maintains a `SessionContextBuffer` — a running list of the top-5 most thematically relevant nodes already surfaced in the current session. On each new turn, Pass C checks whether the new trigger is semantically adjacent to something already in the buffer.

**Why it matters:** In Session B, the afternoon discussion is about "peace without the critic," and the evening is about "the struggle began / high school origin." These are causally linked. Pass C ensures that when the evening turn fires a `HISTORICAL_ERA: HIGH_SCHOOL` trigger, the afternoon's `PATTERN_MENTION: critic_brain` nodes are already in the buffer and get re-injected with a boost — connecting the afternoon insight to the evening root cause. Without Pass C, each turn retrieves independently and the session loses its narrative thread.

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

**Where the relevance number comes from.** Previously unstated, which made the `<20ms, always
succeeds` budget unbuildable — a comparison needs something to compare. Each node caches its
own stored vector when it enters the buffer (one index read per newly-admitted node), and each
turn compares those against Pass A's query vector. That is arithmetic, hence the budget. When
either side has no vector — Pass A could not run, or the node was written before the index
existed — the comparison falls back to word overlap against the node's preview. Blunter, and
used rather than skipped, because a conversation losing its thread is a worse failure than a
slightly wrong relevance.

**The deadlock the two rules produce together.** Five slots and "CRITICAL is never evicted"
means a session can fill entirely with protected nodes and be unable to admit anything new.
The shipped rule: protected entries are never removed, and a new node that cannot get a slot
is still returned to the AI on this turn — it simply does not join the buffer. Nothing is
lost; the buffer stops growing.

---

## Stage 3: Context Assembly

Candidates from Passes A, B, C are merged and ranked. The final injection block has a hard cap of **400 tokens**. This is non-negotiable.

### Ranking Formula (Conversational Mode)

```
conv_score = cosine_similarity × signal_weight × recency_weight × session_relevance_boost
```

Where `session_relevance_boost` = 1.3× if the node is already in the `SessionContextBuffer`.

> **Split by layer, as the extraction-side formula was.** Retrieval (Goal 14) produces a
> *provisional* ordering — `cosine × signal_weight × session_relevance_boost` — used only to
> rank and cut its own candidate list. `recency_weight` is **not** in it: temporal decay is
> Goal 19's, and inventing a decay curve early would mean building it twice. Final ranking and
> the ≤400-token compression are Goal 15's. A node found by an anchor carries no cosine at all
> — an exact name match is not a measurement — so it is ordered by a configured base value
> (`LUMEN_ANCHOR_BASE_SCORE`) while its `similarity` field stays unset, so nothing downstream
> can mistake a policy number for a measured one.

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

Pattern: Fear of Alone Exploration. Triggered when attempting unstructured solo activities. Linked to childhood belief: "Outside world is dangerous, I cannot handle it alone." Fades after 10–15 minutes of actual exposure (observed in Session A).

Active belief: "I am the kind of person who falls short." Originated in HIGH_SCHOOL era. Partially superseded by recent reframe but still active.

Unresolved question from Session A: "Is the resistance about leaving, or about being out there alone?"
[END LUMEN CONTEXT]
```

---

## Stage 4: High-Signal Sensitive Node Handling in Live Chat

Nodes with `signal_strength: CRITICAL` that cover deeply sensitive domains (e.g. identity, trauma, intimate relationships) are **never auto-injected**. Their `CRITICAL` signal strength reflects their importance to the user's psychological history — it does not mean they are freely surfaced. The opposite is true: the higher the signal, the more deliberately the injection must be gated.

### Unlock Signal

A `CRITICAL` signal-strength node in a sensitive domain can be injected only if the user explicitly introduces that topic in the current session. The Query Formulation Layer detects this as a `CRITICAL_DOMAIN_OPENED` event. Once opened, `CRITICAL` nodes linked to that domain become eligible for injection for the rest of the session.

**Example:** When the user says *"a teenager trying to figure out what is going on"* — this is `CRITICAL_DOMAIN_OPENED` for `ADOLESCENT_TRAUMA`. From that point, `CRITICAL` signal-strength nodes linked to identity confusion and adolescent isolation are unlocked for injection. The unlock expires at session end and must be re-triggered in a future session.

### Which domains are sensitive, and what about nodes that have none

Two rules this section needed and did not state. Both are enforced at the retrieval boundary
(Goal 14) rather than at injection, so a gated node never leaves the search at all.

**The sensitive domains are four:** `SELF_CONCEPT`, `RELATIONAL`, `HEALTH`, `SPIRITUALITY`.
`EMOTIONAL` is deliberately excluded — in a therapeutic conversation nearly everything is
emotional, and gating that would gate the entire graph, which is useless rather than careful.

**A `CRITICAL` node with no domain at all is treated as sensitive.** This is the common case,
not an edge case: only the standing records (patterns, beliefs, lessons, principles) carry a
domain, while individual observations carry none. Such a node stays locked until the user has
opened *some* sensitive domain in the session. The safe reading of "we do not know what this
is about" is caution, since by definition it is the heaviest material in the graph.

Gated nodes are **named** on the retrieval result rather than silently dropped. A system that
quietly withholds things is one nobody can debug, and "why did it not mention the obvious
thing?" is a question somebody will eventually ask of a graph they know holds the answer.

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
| Query Formulation | **600ms hard deadline** (configurable) | Must complete before retrieval starts. See correction below. |
| Pass A (Semantic) | <2s (see below) | Contains a model call |
| Pass B (Structural) | <200ms (graph lookup, no embedding) | Faster — no vector math |
| Pass C (Buffer) | <20ms (in-memory) | Always succeeds |
| Assembly + Compression | <200ms | |
| **Total wait window** | **≤3 seconds** | Enforced as a shared wall clock, not per pass |

> **Correction to Pass A's budget.** `<800ms` cannot hold: Pass A contains a HyDE model call,
> which this same document prices at 300–800ms, *plus* an embedding call and an index search.
> The shipped budget is 2s for Pass A within the unchanged 3s total
> (`LUMEN_PASS_A_TIMEOUT_SECONDS`), and the 3s is enforced from outside as one shared deadline
> across A and B — three seconds means three seconds to the person waiting, not three seconds
> each. A pass that misses it is abandoned and reported as abandoned; the pass that finished
> still answers.

> **Correction to the formulation budget.** This document previously specified `<100ms` for the formulation call. That figure is not reachable: a real call to a hosted fast model takes 300–800ms end to end, so a 100ms budget would be missed on essentially every turn and the number would describe nothing. The shipped behaviour is a **configurable hard deadline, defaulting to 600ms** (`LUMEN_FORMULATION_TIMEOUT_SECONDS`), after which the call is abandoned and the turn proceeds with no retrieval. The measured latency is recorded on every `RetrievalSignal`, so the real distribution is observable rather than assumed.
>
> Two consequences worth stating. The abandoned call is **not cancelled** — Python cannot stop a running thread — so it completes on its own and its answer is discarded; the thread pool is bounded and late arrivals are logged. And the formulation model is built with **retries disabled** (`max_attempts=1`), unlike every other model call in the system: a call that has already missed a sub-second deadline gains nothing from being tried again, and retrying only guarantees the wait is spent twice.
>
> A missed formulation deadline does **not** trigger the carry-forward policy. Carry-forward exists for retrieval that arrived late but is still worth having; a classification that arrived late describes a turn the conversation has already moved past. |

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
- Opening the chat on Tuesday creates (or resumes) the Tuesday session.
- Sending a message at 11:58 PM and another at 12:02 AM means the 12:02 AM message belongs to the **next day's session** — not to the previous one.
- The `SessionContextBuffer` is tied to a specific day. Crossing midnight flushes the buffer.
- **Previous day navigation:** Users can open any past day's session in read-only mode, or optionally query it ("what did we talk about last Tuesday?"). This works like navigating to a past chat — the past day's context is available but does not pollute the current day's `SessionContextBuffer`.

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

### Example 1 — Session A: Resistance to going out alone

**Without RAG:** AI derives fear from scratch using generic therapeutic knowledge.

**With RAG:** Pass A retrieves `PatternNode: Avoidance_Solo_Exploration`. Pass B retrieves `BELIEF: Outside world is dangerous (childhood origin)`. The AI immediately knows this is not social anxiety — it is an old childhood belief embedded at the nervous system level that fades after the threshold is crossed. It can ask the precise question: *"When you went to the store in your hometown alone — after 10–15 minutes, did the resistance stay, or did it fade?"* — because that question was explored before and the answer is in the graph.

### Example 2 — Session B evening: "The struggle began"

**Without RAG:** The AI hears about academic pressure, aspiration, isolation, and identity all in one dense turn. It responds from that turn alone.

**With RAG:** Pass B fires `HISTORICAL_ERA: HIGH_SCHOOL`. Pass C re-injects the afternoon session's node about "peace without struggle — first time growth came from curiosity, not fear." The AI receives: *"This user spent today experiencing effort without an internal struggle for the first time. They are now discovering where that struggle started."* This enables the connection: *"What you described this afternoon — running, working, feeling at peace — was your first glimpse of growth without the struggle. And tonight you're seeing where the struggle started."* That is a single profound therapeutic moment made possible only by cross-session memory.

### Example 3 — Session B: The Adler book debate

**Without RAG:** The AI explains Adler generically.

**With RAG:** Pass A retrieves `PatternNode: Critic Brain` + `META_BELIEF: Gap = Character flaw (high school origin)` + the afternoon's `CONCEPTUAL_REFRAME: healthy inferiority vs. inferiority complex`. The AI can immediately map Adler's three-way distinction onto the user's own graph: *"Your critic brain isn't Adler's healthy inferiority feeling. It's the complex — the moment the gap stopped being 'I haven't reached my goal' and became 'I am the gap.'"* The specificity is only possible with the graph.

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
