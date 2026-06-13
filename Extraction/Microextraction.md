# Microextraction Schema

Microextraction is the process of breaking down a single, raw journal entry into structured elements. To capture the true complexity of human thought without oversimplifying or losing nuance, an entry shouldn't just be flattened into generic lessons.

## The Segmentation Problem
**Critical Insight:** A single journal log is rarely about *just one thing*. Furthermore, **stream-of-consciousness journaling is non-linear.** You might jump from a morning event to a late-night thought, and then back to the morning.

If we extract at the "Document Level," the data becomes a noisy soup. If we extract by strict linear time blocks (e.g., "Morning", "Afternoon"), we orphan connected thoughts across the text. 

Therefore, Microextraction must happen in a nested structure: **Log Level** -> **Conceptual Episodes**.

---

### Level 1: Log-Level Metadata
Things that apply to the entire entry:
* **`date`**: Timestamp
* **`overarching_themes`**: High-level tags spanning multiple episodes (e.g., "Social Dynamics", "Work Satisfaction")

---

### Level 2: Conceptual Episode Extraction (The Core Schema)
The system must first cluster the raw text by *Topic or Concept* into "Episodes" (e.g., Episode 1: The Workout Struggle, Episode 2: The Mentor Conflict, Episode 3: Career Realization) rather than linear time blocks.

To combat **Schema Bloat** (having dozens of empty keys) and **Fragmentation** (AI hallucinating random category names), we do not use flat attributes. Instead, we use flexible arrays tied to a strict **Enum Dictionary**.

For *each* Conceptual Episode, extract data into two dynamic arrays:

#### 1. Observations Array (`observations`)
An array of standalone data points. The AI is ONLY allowed to tag the `type` using the fixed dictionary below to prevent fragmentation.

**The Enum Dictionary (Structural Anchor):**
* `CONTEXT`: Environmental factors, what happened, and where.
* `EMOTION`: Raw feelings mapped directly to this episode.
* `SOMATIC_STATE`: Physical body sensations and energy levels (e.g., "heavy body", "lack of activation energy").
* `TRIGGER_CATALYST`: The specific trigger that led to an emotional/somatic state.
* `INTERVENTION_APPLIED`: Actions taken to cope or regulate state, and their success level (e.g., "listened to music to isolate, highly effective").
* `IDENTITY_AFFINITY`: Self-concept discoveries, natural inclinations, or preferences (e.g., "I prefer management over technical execution").
* `COGNITIVE_DISTORTION`: Instances where a false belief was caught in real-time (`distortion` vs `reality_check`).
* `PERSPECTIVE_SHIFT`: How a view on a past event or person has evolved.
* `CORE_CONFLICT`: Conflicting desires or states held concurrently (e.g., wanting independence vs. validation).
* `OPEN_LOOP`: Unresolved psychological investigations.
* `PATTERN`: Recurring behaviors or thought loops. **CRITICAL:** Must use formula `[Behavior] + [Trigger/Context] + [Internal State]`.
* `BELIEF`: Underlying rules or worldviews you operate by.
* `LESSON`: Extracted wisdom grounded in proof from this episode.

#### 2. Causal Mechanisms Array (`causal_mechanisms`)
Instead of leaving data points disconnected, extract the *cause and effect* sequences. This structure perfectly aligns with Semantic Search (saving the "meaning" of an event).

For each extracted mechanism, include:
* `trigger`: The catalyst (e.g., "Friction in starting home workout")
* `internal_state_shift`: Somatic & emotional change (e.g., "Physical heaviness, lack of motivation")
* `action_taken`: Coping mechanism or resulting action (e.g., "Gave up after 10 pushups")
* `underlying_cause`: Deep reasoning (e.g., "Environmental context lacks forcing function")

---
*Note: By restricting the observation `type` to the Enum Dictionary, we prevent database fragmentation. By using the dynamic `observations` and `causal_mechanisms` arrays, we prevent schema bloat.*

