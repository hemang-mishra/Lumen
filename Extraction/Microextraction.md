# Microextraction Schema

Microextraction is the process of breaking down a single journal entry (or a conversational Session Summary from Stage 0.5) into structured elements. To capture the true complexity of human thought without oversimplifying or losing nuance, an entry shouldn't just be flattened into generic lessons.

## The Segmentation Problem
**Critical Insight:** A single journal log is rarely about *just one thing*. Furthermore, **stream-of-consciousness journaling is non-linear.** You might jump from a morning event to a late-night thought, and then back to the morning.

If we extract at the "Document Level," the data becomes a noisy soup. If we extract by strict linear time blocks (e.g., "Morning", "Afternoon"), we orphan connected thoughts across the text.

Therefore, Microextraction must happen in a nested structure: **Log Level** -> **Conceptual Episodes**.

---

### Cross-Entry Coreference Limitation

The coreference map produced in Stage 0 (Preprocessing) is scoped to the **current document only.** It resolves aliases that appear within a single entry (e.g., "Alex" → "Alex" within today's entry), but it cannot resolve aliases across entries.

Cross-entry alias resolution — where *"my mentor"* in a June 10th entry refers to the same person as *"Alex"* in a June 12th entry — is handled by the **Reconciliation layer** via Person Entity `same-as` edges. This is intentional: the blind Microextraction step must remain context-free. Introducing cross-entry knowledge at this stage would re-introduce the Anchoring Bias the Late Binding model exists to prevent.

See [`Reconciliation.md`](Reconciliation.md) for the cross-entry person resolution mechanism.

---

### Level 1: Log-Level Metadata
Things that apply to the entire entry:
* **`date`**: Timestamp (Specifically, the **Logical Event Date**, not the system ingestion/processing date)
* **`overarching_themes`**: High-level tags spanning multiple episodes (e.g., "Social Dynamics", "Work Satisfaction")

---

### Level 2: Conceptual Episode Extraction (The Core Schema)
The system must first cluster the input text (either a raw monologue or the Stage 0.5 Session Summary) by *Topic or Concept* into "Episodes" (e.g., Episode 1: The Workout Struggle, Episode 2: The Mentor Conflict, Episode 3: Career Realization) rather than linear time blocks.

To combat **Schema Bloat** (having dozens of empty keys) and **Fragmentation** (AI hallucinating random category names), we do not use flat attributes. Instead, we use flexible arrays tied to a strict **Enum Dictionary**.

For *each* Conceptual Episode, extract data into two dynamic arrays:

#### 1. Observations Array (`observations`)
An array of standalone data points. The AI is ONLY allowed to tag the `type` using the fixed dictionary below to prevent fragmentation.

Every observation must also carry these four required metadata fields:

```json
{
  "type": "EMOTION",
  "content": "...",
  "provenance": "USER_GENERATED",
  "extraction_signal_strength": "STANDARD",
  "person_ref": null
}
```

* **`provenance`** — `USER_GENERATED` | `AI_GENERATED` | `CO_CREATED`. The Microextraction LLM populates this by parsing the Dialogue Act Classification generated in Stage 0. If the user explicitly affirmed an AI-facilitated insight (e.g., "Yes, exactly!"), the provenance must be recorded as `CO_CREATED`.
* **`extraction_signal_strength`** — `STANDARD` | `HIGH` | `CRITICAL`. Marks observations that are disproportionately valuable (e.g., involuntary emotional reactions during recording). HIGH/CRITICAL observations receive a weighted boost in vector retrieval and are always included in Macroextraction.
* **`person_ref`** — If the observation involves a named person, set this to their canonical name. Used to link the observation to a Person Entity node during Reconciliation. Set to `null` if no person is involved.

**The Enum Dictionary (Structural Anchor):**

*— Core Experience Types —*
* `CONTEXT`: Environmental factors, what happened, and where.
* `CONTEXT_SEVERANCE`: The psychological shock of abruptly losing access to an environment, community, or structural system (distinct from a standard transition).
* `EMOTION`: Raw feelings mapped directly to this episode.
* `SOMATIC_STATE`: Physical body sensations and energy levels (e.g., "heavy body", "lack of activation energy").
* `SOMATIC_CATHARSIS`: A delayed, intense physical and emotional collapse (e.g., "came in the room, didn't put the card in... cried for five minutes"). Distinct from `EMOTION` as it captures the severity of a neurological/physiological emotional release.
* `ANTICIPATORY_ANXIETY`: Explicitly tags fears, traumas, or intense anxieties that are projected into a future timeline or hypothetical event rather than occurring in the present (e.g., projecting current stress into an upcoming "placement season").
* `COGNITIVE_FRICTION`: Distinguishing between physical "fatigue", "attention drift", and psychological "escape/avoidance" during cognitive tasks.
* `TRIGGER_CATALYST`: The specific trigger that led to an emotional/somatic state.
* `PROSODY_SIGNAL`: A signal derived from the **paralinguistic features** of a voice recording — pitch variation, vocal tension, speech rhythm, mid-sentence breaks — that indicates emotional state **independent of transcript content.** Only applicable to voice entries. **Always set `extraction_signal_strength: HIGH`.** Distinct from `EMOTION` (which captures content-derived feelings). A `PROSODY_SIGNAL` represents what the body communicated before the words caught up. If the voice audio is unavailable (text-only entry), this type is skipped entirely.

*— Coping & Response Types —*
* `ENVIRONMENTAL_REANCHORING`: Deliberate actions taken to overwrite the historical emotional association or neural map of a physical space (e.g., cooking a complex meal in a childhood home specifically to break a historical pattern of passivity in that house).
* `COGNITIVE_DEFENSE_MECHANISM`: An active mental strategy or shape-shifting pattern used to maintain a specific psychological state or avoid discomfort (e.g., the critic brain shifting targets to "keep you under pressure").
* `INTERVENTION_APPLIED`: Actions taken to cope or regulate state, and their success level (e.g., "listened to music to isolate, highly effective").
* `ENERGY_SPIKE_EVENT`: A sudden, notable surge in positive energy, motivation, or emotional activation — especially when the cause is unclear to the person. Captures the context, the magnitude, and the person's own theory of cause. Distinct from a gradual mood improvement.
* `SUPPRESSED_EMOTION_SURFACING`: An emotion that emerges unexpectedly *during the act of reflection or recording* — not during the original experience. The person is typically surprised by the reaction (e.g., "I don't know why I'm crying while saying this"). **Always set `extraction_signal_strength: HIGH`** — this marks the location of unprocessed emotional depth. Distinct from `EMOTION`, which captures feelings as experienced in-the-moment.

*— Self-Model Types —*
* `SUBPERSONALITY_ACTION`: Activity of an internal actor or "part" (e.g., the "critic brain") that operates with its own behaviors and motivations, independent of the core identity. Captures internal conflict and the actions of defensive/adaptive parts.
* `ERA_INTEGRATION_STATE`: How the current user relates to a specific historical era or past version of themselves (e.g., rejecting, grieving, integrating, or thanking a past self).
* `RUMINATION_LOOP`: Involuntary cognitive replaying or being trapped in a cycle of repeated thoughts. Distinguishes passive psychological re-living from active, deliberate thought processes or problem-solving.
* `PHYSIOLOGICAL_CAPACITY_STATE`: A structural, energetic, or neurological constraint (e.g., "I can only do heavy thinking work in the morning"). Distinct from a temporary `SOMATIC_STATE` or a behavioral `PATTERN`; this defines a hard biological or cognitive boundary.
* `IDENTITY_AFFINITY`: Self-concept discoveries, natural inclinations, or preferences (e.g., "I prefer management over technical execution").
* `ACCEPTANCE_ACKNOWLEDGEMENT`: A moment of explicit, effortful acceptance of a difficult truth — about identity, a limitation, a fear, or a recurring failing — despite discomfort. Captures the *object* of acceptance and the subjective resistance level (e.g., "I'm gay, and this has been since childhood — difficult to accept, but fine").
* `CORE_WOUND`: A profound root cause revelation spanning decades, attributing intense pain or behavior to a lifelong pattern (e.g., "This is how I've been wired since childhood, to attach identity to things").
* `SYSTEM_DESIGN_ITERATION`: The user is actively building and versioning personal frameworks (e.g., "flow framework"). Changes to this framework are deliberate personal protocols, not standard behaviors.
* `SELF_NARRATION_PATTERN`: Moments where the person catches themselves constructing or performing a version of themselves — for an imagined audience, past self, or future self. Captures the act of *storytelling about oneself* rather than the content of the story (e.g., "I was trying to show off, not actually present in the moment").
* `SOCIAL_PERFORMANCE_STATE`: The specific internal state that arises when the person perceives themselves through the imagined gaze of observers. Captures audience-consciousness, performance anxiety, and the subjective shift between "being" vs. "being seen" (e.g., "A kind of feeling of superiority because people are looking at you talking with a girl — seeing yourself from someone else's lens").
* `BIOGRAPHICAL_GAP`: A retrospective observation about something structurally absent across a significant phase of the person's life — a type of relationship, experience, or developmental support that most people have but this person did not. Not a behavioral pattern, but a life-structure void (e.g., "Never had a one-on-one mentor figure in school or college — always navigated alone"). High value for root-cause analysis and longitudinal identity tracking.
* `INAUTHENTICITY_STATE`: The experience of existing in a relationship, group, or social context while concealing a fundamental aspect of identity — where disclosure carries real perceived risk. Distinct from `CORE_CONFLICT` (internal tension) and `SOCIAL_PERFORMANCE_STATE` (audience-consciousness). Captures a safety-constrained structural reality, not just a momentary feeling (e.g., "They're homophobic — they hate who I actually am, but I'm closeted so they don't know me").

*— Cognitive & Belief Types —*
* `EPISTEMIC_SHIFT`: Captures when the user's fundamental understanding of a concept (like "limits" or "focus") changes, rather than just their behavior.
* `CONCEPTUAL_REFRAME`: A fundamental semantic shift in how the person defines a concept or views the world (e.g., redefining "confidence" from "not feeling scared" to "feeling scared and showing up anyway"). Distinct from BELIEF; this is a definitional update.
* `LEXICON_UPDATE`: The explicit introduction or adoption of a new semantic primitive to categorize experience (e.g., splitting "Work" into "Execution vs Thinking", or "Stress" into "Threat vs Focused"). Essential for ontological mapping during future retrievals.
* `META_BELIEF`: A foundational philosophy about *how* to grow, acquire beliefs, or operate (e.g., "Growth requires self-hatred"). A higher-order rule for the user's psychological operating system.
* `COGNITIVE_DISTORTION`: Instances where a false belief was caught in real-time (`distortion` vs `reality_check`).
* `METACOGNITIVE_INTERRUPT`: A moment where the person catches themselves *actively executing* a cognitive pattern or distortion in real time — mid-sentence or mid-reflection — rather than identifying it retrospectively. The hallmark is present-tense self-correction during articulation (e.g., "I shouldn't overthink this — and that's exactly what I'm doing right now as I say it"). Distinct from `COGNITIVE_DISTORTION` (past-tense catch) and `PATTERN` (historical observation). Tracks depth of integration of a lesson. **Always set `extraction_signal_strength: HIGH`.**
* `PERSPECTIVE_SHIFT`: How a view on a past event or person has evolved.
* `CORE_CONFLICT`: Conflicting desires or states held concurrently (e.g., wanting independence vs. validation).
* `BELIEF`: Underlying rules or worldviews you operate by.
* `LESSON`: Extracted wisdom grounded in proof from this episode.

*— Pattern & Environment Types —*
* `PATTERN`: Recurring behaviors or thought loops. **CRITICAL:** Must use formula `[Behavior] + [Trigger/Context] + [Internal State]`.
* `OPEN_LOOP`: Unresolved psychological investigations.
* `ENVIRONMENTAL_CONTEXT`: Captures crucial distinctions the user makes between different environments based on cognitive load or constraints (e.g., "Office" meaning low decision fatigue vs "Home" meaning high decision fatigue).
* `ENVIRONMENTAL_DEPENDENCY`: An identified rule about how a specific environment, social context, or physical setting modulates capability, motivation, or emotional state. Not just what the environment was, but what it *enables or disables* (e.g., "The gym enables workout consistency that home cannot — the environment is the forcing function, not the will").

*— Relational Types —*
* `OTHER_PERSON_MODEL`: A hypothesis or updated belief about an external entity's mental model, operating system, or behavioral traits (e.g., "His default language is improvement, not appreciation"). Set `person_ref` to the person's canonical name.
* `RELATIONAL_DYNAMIC`: An observation about the perceived quality, power structure, emotional safety, or reciprocity of a specific relationship. Includes first assessments of new people. Distinct from the emotion felt *toward* someone (e.g., "Income and lifestyle gap makes genuine conversation with seniors difficult — different reference frames"). Set `person_ref` to the person's canonical name.
* `GRATITUDE_APPRECIATION`: Explicit acknowledgement of value received from a person, situation, or one's own actions. Captures the *object* of appreciation and the *depth* of feeling. Distinct from general positive emotion (e.g., "Grateful for Alex — first person who has taken mentorship initiative for me in my entire life"). Set `person_ref` to the person's canonical name.

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

A chain can have multiple `INTERNAL_STATE` steps (e.g., before and after an action), and can branch if one action leads to two different outcomes at different times. In that case, use a `branch_id` field to distinguish parallel paths from the same action step. Additionally, include an `is_anticipatory: true` flag on the chain if the sequence describes a *hypothetical or feared* outcome loop rather than an event that physically occurred today.

---

#### 3. Episode-Level Metadata

**Historical Era Anchoring:** If the user explicitly anchors the reflection or psychological struggle to a specific past life chapter (e.g., "During my a major entrance exam prep days..."), the LLM must extract this as the `historical_era` attribute on the Episode. This allows the graph to link beliefs and traumas to specific epochs for semantic retrieval.

The **coreference map** is produced in Stage 0 (Preprocessing) and is passed to Microextraction as an input artifact. The Microextraction LLM does **not** need to re-derive it — it should consume the pre-computed map directly. This ensures the coreference pass runs exactly once per entry and that Microextraction remains a pure extraction step with no preprocessing side-effects.

```json
{
  "coreference_map": [
    { "canonical": "Alex", "aliases_in_document": ["Alex", "my mentor", "him"] },
    { "canonical": "Rohan",  "aliases_in_document": ["him", "this guy"] }
  ]
}
```

This is safe for the blind Microextraction step — it only uses names and pronouns within the *current entry*, requires no historical context, and prevents the same person from being extracted as multiple different actors during episode segmentation.

---

### Entry Type Routing

Microextraction behaves differently based on the `entry_type` classification produced by Stage 0 (Preprocessing):

- **`REFLECTION` entries:** Full extraction. All observation types are eligible. Causal mechanism arrays are expected and required for episodic content. `extraction_signal_strength` is assessed per-observation by the LLM.

- **`RAW_CAPTURE` entries:** Lightweight extraction only. Only `CONTEXT` and `EMOTION` observations are extracted. Causal mechanism arrays are not produced. `extraction_signal_strength` defaults to `STANDARD` for all observations unless an involuntary signal (e.g., `PROSODY_SIGNAL` from voice) is detected. After basic extraction, the system generates 3 reflection questions surfaced to the user to invite deeper processing.

⚠️ The schema note below still applies to both entry types: `extraction_signal_strength` is a required field on every observation, regardless of entry type. A missing field is a hard validation failure.

---

*Note: By restricting the observation `type` to the Enum Dictionary, we prevent database fragmentation. By using the dynamic `observations` and `causal_mechanisms` arrays, we prevent schema bloat. By modelling causal mechanisms as linked chains (not flat structs), we preserve the true complexity of multi-step and feedback-loop experiences. The `extraction_signal_strength` field ensures the pipeline treats high-value observations with appropriate priority.*
