# Microextraction Schema

Microextraction is the process of breaking down a single, raw journal entry into structured elements. To capture the true complexity of human thought without oversimplifying or losing nuance, an entry shouldn't just be flattened into generic lessons.

## The Segmentation Problem
**Critical Insight:** A single journal log is rarely about *just one thing*. Furthermore, **stream-of-consciousness journaling is non-linear.** You might jump from a morning event to a late-night thought, and then back to the morning.

If we extract at the "Document Level," the data becomes a noisy soup. If we extract by strict linear time blocks (e.g., "Morning", "Afternoon"), we orphan connected thoughts across the text.

Therefore, Microextraction must happen in a nested structure: **Log Level** -> **Conceptual Episodes**.

---

### Cross-Entry Coreference Limitation

The coreference map produced in Stage 0 (Preprocessing) is scoped to the **current document only.** It resolves aliases that appear within a single entry (e.g., "Adit" → "Aditya" within today's entry), but it cannot resolve aliases across entries.

Cross-entry alias resolution — where *"my mentor"* in a June 10th entry refers to the same person as *"Aditya"* in a June 12th entry — is handled by the **Reconciliation layer** via Person Entity `same-as` edges. This is intentional: the blind Microextraction step must remain context-free. Introducing cross-entry knowledge at this stage would re-introduce the Anchoring Bias the Late Binding model exists to prevent.

See [`Reconciliation.md`](Reconciliation.md) for the cross-entry person resolution mechanism.

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

Every observation must also carry these three required metadata fields:

```json
{
  "type": "EMOTION",
  "content": "...",
  "sensitivity_tier": "STANDARD",
  "extraction_signal_strength": "STANDARD",
  "person_ref": null
}
```

* **`sensitivity_tier`** — `STANDARD` | `ELEVATED` | `CRITICAL`. Controls cloud LLM routing, notification visibility, and RAG inclusion (see [`Architecture.md`](Architecture.md) for full rules).
* **`extraction_signal_strength`** — `STANDARD` | `HIGH` | `CRITICAL`. Marks observations that are disproportionately valuable (e.g., involuntary emotional reactions during recording). HIGH/CRITICAL observations receive a weighted boost in vector retrieval and are always included in Macroextraction.
* **`person_ref`** — If the observation involves a named person, set this to their canonical name. Used to link the observation to a Person Entity node during Reconciliation. Set to `null` if no person is involved.

**The Enum Dictionary (Structural Anchor):**

*— Core Experience Types —*
* `CONTEXT`: Environmental factors, what happened, and where.
* `EMOTION`: Raw feelings mapped directly to this episode.
* `SOMATIC_STATE`: Physical body sensations and energy levels (e.g., "heavy body", "lack of activation energy").
* `TRIGGER_CATALYST`: The specific trigger that led to an emotional/somatic state.
* `PROSODY_SIGNAL`: A signal derived from the **paralinguistic features** of a voice recording — pitch variation, vocal tension, speech rhythm, mid-sentence breaks — that indicates emotional state **independent of transcript content.** Only applicable to voice entries. **Always set `extraction_signal_strength: HIGH`.** Distinct from `EMOTION` (which captures content-derived feelings). A `PROSODY_SIGNAL` represents what the body communicated before the words caught up. If the voice audio is unavailable (text-only entry), this type is skipped entirely.

*— Coping & Response Types —*
* `INTERVENTION_APPLIED`: Actions taken to cope or regulate state, and their success level (e.g., "listened to music to isolate, highly effective").
* `ENERGY_SPIKE_EVENT`: A sudden, notable surge in positive energy, motivation, or emotional activation — especially when the cause is unclear to the person. Captures the context, the magnitude, and the person's own theory of cause. Distinct from a gradual mood improvement.
* `SUPPRESSED_EMOTION_SURFACING`: An emotion that emerges unexpectedly *during the act of reflection or recording* — not during the original experience. The person is typically surprised by the reaction (e.g., "I don't know why I'm crying while saying this"). **Always set `extraction_signal_strength: HIGH`** — this marks the location of unprocessed emotional depth. Distinct from `EMOTION`, which captures feelings as experienced in-the-moment.

*— Self-Model Types —*
* `IDENTITY_AFFINITY`: Self-concept discoveries, natural inclinations, or preferences (e.g., "I prefer management over technical execution").
* `ACCEPTANCE_ACKNOWLEDGEMENT`: A moment of explicit, effortful acceptance of a difficult truth — about identity, a limitation, a fear, or a recurring failing — despite discomfort. Captures the *object* of acceptance and the subjective resistance level (e.g., "I'm gay, and this has been since childhood — difficult to accept, but fine"). **Always set `sensitivity_tier: CRITICAL` when identity-related.**
* `SELF_NARRATION_PATTERN`: Moments where the person catches themselves constructing or performing a version of themselves — for an imagined audience, past self, or future self. Captures the act of *storytelling about oneself* rather than the content of the story (e.g., "I was trying to show off, not actually present in the moment").
* `SOCIAL_PERFORMANCE_STATE`: The specific internal state that arises when the person perceives themselves through the imagined gaze of observers. Captures audience-consciousness, performance anxiety, and the subjective shift between "being" vs. "being seen" (e.g., "A kind of feeling of superiority because people are looking at you talking with a girl — seeing yourself from someone else's lens").
* `BIOGRAPHICAL_GAP`: A retrospective observation about something structurally absent across a significant phase of the person's life — a type of relationship, experience, or developmental support that most people have but this person did not. Not a behavioral pattern, but a life-structure void (e.g., "Never had a one-on-one mentor figure in school or college — always navigated alone"). High value for root-cause analysis and longitudinal identity tracking.
* `INAUTHENTICITY_STATE`: The experience of existing in a relationship, group, or social context while concealing a fundamental aspect of identity — where disclosure carries real perceived risk. Distinct from `CORE_CONFLICT` (internal tension) and `SOCIAL_PERFORMANCE_STATE` (audience-consciousness). Captures a safety-constrained structural reality, not just a momentary feeling (e.g., "They're homophobic — they hate who I actually am, but I'm closeted so they don't know me"). **Always set `sensitivity_tier: CRITICAL`.**

*— Cognitive & Belief Types —*
* `COGNITIVE_DISTORTION`: Instances where a false belief was caught in real-time (`distortion` vs `reality_check`).
* `METACOGNITIVE_INTERRUPT`: A moment where the person catches themselves *actively executing* a cognitive pattern or distortion in real time — mid-sentence or mid-reflection — rather than identifying it retrospectively. The hallmark is present-tense self-correction during articulation (e.g., "I shouldn't overthink this — and that's exactly what I'm doing right now as I say it"). Distinct from `COGNITIVE_DISTORTION` (past-tense catch) and `PATTERN` (historical observation). Tracks depth of integration of a lesson. **Always set `extraction_signal_strength: HIGH`.**
* `PERSPECTIVE_SHIFT`: How a view on a past event or person has evolved.
* `CORE_CONFLICT`: Conflicting desires or states held concurrently (e.g., wanting independence vs. validation).
* `BELIEF`: Underlying rules or worldviews you operate by.
* `LESSON`: Extracted wisdom grounded in proof from this episode.

*— Pattern & Environment Types —*
* `PATTERN`: Recurring behaviors or thought loops. **CRITICAL:** Must use formula `[Behavior] + [Trigger/Context] + [Internal State]`.
* `OPEN_LOOP`: Unresolved psychological investigations.
* `ENVIRONMENTAL_DEPENDENCY`: An identified rule about how a specific environment, social context, or physical setting modulates capability, motivation, or emotional state. Not just what the environment was, but what it *enables or disables* (e.g., "The gym enables workout consistency that home cannot — the environment is the forcing function, not the will").

*— Relational Types —*
* `RELATIONAL_DYNAMIC`: An observation about the perceived quality, power structure, emotional safety, or reciprocity of a specific relationship. Includes first assessments of new people. Distinct from the emotion felt *toward* someone (e.g., "Income and lifestyle gap makes genuine conversation with seniors difficult — different reference frames"). Set `person_ref` to the person's canonical name.
* `GRATITUDE_APPRECIATION`: Explicit acknowledgement of value received from a person, situation, or one's own actions. Captures the *object* of appreciation and the *depth* of feeling. Distinct from general positive emotion (e.g., "Grateful for Aditya — first person who has taken mentorship initiative for me in my entire life"). Set `person_ref` to the person's canonical name.

---

#### 2. Causal Mechanisms Array (`causal_mechanisms`)
Instead of leaving data points disconnected, extract the *cause and effect* sequences. This structure perfectly aligns with Semantic Search (saving the "meaning" of an event).

**⚠️ Important:** Real experiences are rarely a single 4-step chain. A single episode may contain multiple chained steps, branching paths, or feedback loops (e.g., an action that produces both a positive *and* a negative downstream state at different time points). Model this as a **linked chain of steps**, not a flat struct.

For each extracted mechanism, structure it as an ordered array of steps:

```json
{
  "causal_chain": [
    { "step": 1, "type": "TRIGGER",        "content": "Headache on a normal workday" },
    { "step": 2, "type": "INTERNAL_STATE", "content": "Pressure, confusion, felt 'fucked'" },
    { "step": 3, "type": "ACTION",         "content": "Relieved all expectations, went at very slow pace" },
    { "step": 4, "type": "INTERNAL_STATE", "content": "Progressive re-engagement, feeling of absorption" },
    { "step": 5, "type": "OUTCOME",        "content": "Energy fully restored in 3 hours — more energetic than usual" },
    { "step": 6, "type": "LESSON",         "content": "Slow progressive steps are a valid alternative to sleeping it off" }
  ]
}
```

**Valid step `type` values:** `TRIGGER` | `INTERNAL_STATE` | `ACTION` | `OUTCOME` | `LESSON`

A chain can have multiple `INTERNAL_STATE` steps (e.g., before and after an action), and can branch if one action leads to two different outcomes at different times. In that case, use a `branch_id` field to distinguish parallel paths from the same action step.

---

#### 3. Episode-Level Metadata

The **coreference map** is produced in Stage 0 (Preprocessing) and is passed to Microextraction as an input artifact. The Microextraction LLM does **not** need to re-derive it — it should consume the pre-computed map directly. This ensures the coreference pass runs exactly once per entry and that Microextraction remains a pure extraction step with no preprocessing side-effects.

```json
{
  "coreference_map": [
    { "canonical": "Aditya", "aliases_in_document": ["Adit", "my mentor", "him"] },
    { "canonical": "Kaval",  "aliases_in_document": ["him", "this guy"] }
  ]
}
```

This is safe for the blind Microextraction step — it only uses names and pronouns within the *current entry*, requires no historical context, and prevents the same person from being extracted as multiple different actors during episode segmentation.

---

### Entry Type Routing

Microextraction behaves differently based on the `entry_type` classification produced by Stage 0 (Preprocessing):

- **`REFLECTION` entries:** Full extraction. All observation types are eligible. Causal mechanism arrays are expected and required for episodic content. `extraction_signal_strength` is assessed per-observation by the LLM.

- **`RAW_CAPTURE` entries:** Lightweight extraction only. Only `CONTEXT` and `EMOTION` observations are extracted. Causal mechanism arrays are not produced. `extraction_signal_strength` defaults to `STANDARD` for all observations unless an involuntary signal (e.g., `PROSODY_SIGNAL` from voice) is detected. After basic extraction, the system generates 3 reflection questions surfaced to the user to invite deeper processing.

⚠️ The schema note below still applies to both entry types: `sensitivity_tier` and `extraction_signal_strength` are required fields on every observation, regardless of entry type. A missing field is a hard validation failure.

---

*Note: By restricting the observation `type` to the Enum Dictionary, we prevent database fragmentation. By using the dynamic `observations` and `causal_mechanisms` arrays, we prevent schema bloat. By modelling causal mechanisms as linked chains (not flat structs), we preserve the true complexity of multi-step and feedback-loop experiences. The `sensitivity_tier` and `extraction_signal_strength` fields ensure the pipeline treats high-risk and high-value observations with appropriate priority.*
