# Stage 0: Preprocessing Pipeline

Real journal inputs are not clean documents. A voice-to-text transcript from Whisper.cpp will contain hesitation fillers (`uh`, `um`, `like, you know`), self-corrections (`wait, no, I meant`), incomplete sentences, mid-thought pivots, and code-mixed language segments where Hindi and English alternate within a single sentence. Feeding this raw to an extraction LLM produces noisy, sometimes contradictory observations that pollute the knowledge graph. Stage 0 exists to normalize input into a clean, structured form before any semantic extraction occurs — not by "fixing" what the user said, but by making their actual meaning legible to downstream models.

Stage 0 also performs the critical routing decision: is this entry rich enough for full extraction (`REFLECTION`), or is it a low-coherence voice dump that should receive only minimal metadata capture (`RAW_CAPTURE`)?

---

## Table of Contents

1. [Input Modalities](#input-modalities)
2. [Pipeline Steps](#pipeline-steps)
   - [1. ASR Post-Processing](#1-asr-post-processing)
   - [2. Entry Completeness Scoring](#2-entry-completeness-scoring)
   - [3. Quality Gate Routing](#3-quality-gate-routing)
   - [4. Coreference Pre-Pass](#4-coreference-pre-pass)
3. [What This Protects](#what-this-protects)

---

## Input Modalities

### Voice Notes (ASR Output from Whisper.cpp)

The primary input modality. Whisper.cpp runs on-device and produces a raw transcript. The transcript may include:
- Punctuation inferred by Whisper (often inconsistent at clause boundaries)
- Word-level timestamps (used for self-correction detection)
- Language confidence scores per segment (used for code-mix tagging)
- Speaker diarization output if multi-speaker mode is enabled (stored as metadata, not processed by Stage 0)

Voice notes are expected to be the noisiest input class. All ASR-specific cleaning rules apply only when the input originated as voice audio, but the overall preprocessing stage runs on every entry, including pasted transcripts and typed text.

### Code-Mixed Entries (Hindi + English and Other Combinations)

Both voice and pasted/text entries may contain code-mixed language. Smriti does **not** attempt to translate code-mixed segments. Instead, it detects language boundaries, tags each segment with a BCP-47 language code, and passes the tagged text to the LLM with explicit language metadata. The extraction LLM is instructed to treat each segment in its original language and is not asked to produce a monolingual normalized form.

Supported detection pairs (v2):
- Hindi (`hi`) + English (`en`) — primary use case
- Marathi (`mr`) + English (`en`)
- Bengali (`bn`) + English (`en`)
- Other pairs: detected as `und` (undetermined) + `en`; passed as-is with a `mixed_language` flag.

---

## Pipeline Steps

### 1. ASR Post-Processing

**Applies to:** All entries. Voice-specific cleanup only activates when the source input is a voice transcript.

#### Filler Removal

Fillers are removed before extraction to prevent the LLM from treating hesitation as semantic content. Removal is subject to **semantic preservation rules** to avoid destroying meaning.

Filler patterns that are always removed:
```
uh, um, uh-huh (standalone), hmm (standalone), like (when used as filler, not verb/noun),
you know (when used as filler), right (when used as trailing filler), basically (standalone opener),
literally (when modifying a filler sequence, not modifying a content word)
```

**Semantic preservation rule:** A token is classified as a filler candidate only if it is (a) surrounded by content words with no syntactic dependency on either neighbor, or (b) immediately followed by a restart of the same clause. Fillers that carry discourse structure (e.g., `right` in `right, so the issue was...`) are preserved.

**Example:**

| Raw ASR | After Filler Removal |
|---|---|
| `So um, I was like, really frustrated with uh the whole situation you know` | `So I was really frustrated with the whole situation` |
| `Right so the issue was basically that nobody told me` | `Right so the issue was basically that nobody told me` *(preserved — discourse-structural)* |

#### Self-Correction Detection

Self-corrections follow recognizable linguistic patterns. When detected, only the **correction** is retained; the false-start is removed entirely.

Detection patterns:
```
"wait, [no/actually], [I meant / the point was / what I said was]..."
"or rather..."
"actually no, ..."
"I mean, ..." (when used to replace prior clause)
"let me rephrase..."
```

**Example:**

| Raw ASR | After Self-Correction Handling |
|---|---|
| `He was really supportive, wait no actually he wasn't, he just stayed quiet` | `He just stayed quiet` |
| `I think I was angry — or rather, I was scared` | `I was scared` |

> ⚠️ **Semantic preservation:** Self-correction detection never removes a segment that introduces *new* information not present in the correction. If the false-start and the correction carry different semantic content, both are preserved with a `[CORRECTED_FROM]` annotation for the LLM.

#### Code-Mixed Normalization

1. Segment the transcript into language-homogeneous spans using a lightweight language-ID model (e.g., `fastText lid.176.ftz` running on-device).
2. Tag each span with its BCP-47 code.
3. Reconstruct the transcript as a tagged sequence:

```
[en] I had a meeting with Rahul today and [hi] मुझे समझ नहीं आया कि [en] he was being passive-aggressive or genuinely confused.
```

4. Pass this tagged form to the extraction LLM with the system instruction:
   > "The input contains language-tagged segments. Process each segment in its original language. Do not translate. Extract meaning across language boundaries."

---

### 2. Entry Completeness Scoring

Every entry (after ASR post-processing, if applicable) is scored for completeness before extraction is attempted. This is a two-gate check: structural (word count) followed by semantic (LLM-scored coherence).

#### Structural Gate: Word Count Threshold

| Entry Class Candidate | Minimum Word Count | Behavior if Below |
|---|---|---|
| `REFLECTION` candidate | 30 words | Immediately classified as `RAW_CAPTURE`; skip LLM coherence scoring |
| `RAW_CAPTURE` | No minimum | Accepted as-is |

> ⚠️ Word count is computed **after** filler removal and self-correction handling. A 40-word raw entry that reduces to 22 clean words is classified as `RAW_CAPTURE`.

#### Semantic Gate: LLM Coherence Scoring

Entries that pass the structural gate are sent to a lightweight LLM call (Gemini Flash or local equivalent for CRITICAL content) with a single scoring task:

**Prompt structure:**
```
Given the following journal entry excerpt, score its coherence as a personal reflection 
on a scale of 0.0 to 1.0, where:
  1.0 = Clear, complete thought with subject, emotional signal, and context
  0.5 = Partial thought — some context missing but core meaning is legible
  0.0 = Incoherent, no extractable reflection

Output ONLY a JSON object: {"coherence_score": <float>, "reason": "<one sentence>"}

Entry: {cleaned_entry_text}
```

| Coherence Score | Classification | Action |
|---|---|---|
| ≥ 0.4 | `REFLECTION` | Proceed to full Microextraction pipeline |
| < 0.4 | `RAW_CAPTURE` | Minimal extraction + reflection prompts |

---

### 3. Quality Gate Routing

The output of Step 2 is a routing decision that determines which downstream pipeline runs.

#### REFLECTION Path

Entries classified as `REFLECTION` proceed to the full pipeline:

```
REFLECTION → Microextraction (Step 1) → Candidate Retrieval (Step 2) → Reconciliation (Step 3) → Graph Write (Step 4)
```

All observation types from the full enum taxonomy are eligible. Sensitivity tier is determined during Microextraction.

#### RAW_CAPTURE Path

Entries classified as `RAW_CAPTURE` receive:

1. **Minimal extraction:** Only a `CONTEXT` observation type is extracted — a single sentence describing the surface topic of the entry, no emotional inference, no pattern detection.
2. **Reflection prompts:** The system generates 3 targeted reflection questions based on the `CONTEXT` observation and stores them as a `pending_reflections` record linked to the entry.
3. **No Reconciliation:** The `CONTEXT` observation is written directly to an `ObservationNode` with `status: RAW_CAPTURE` and is not routed through Reconciliation. It does not participate in candidate retrieval.

**Reflection prompt generation example:**

```json
{
  "entry_id": "e_2026_06_11_001",
  "entry_class": "RAW_CAPTURE",
  "context_observation": "User mentioned a disagreement with a colleague about project timelines",
  "pending_reflections": [
    "What was it about the timeline disagreement that bothered you most — the outcome, or how it was handled?",
    "Did you feel heard during that conversation?",
    "Is this a recurring pattern with this colleague, or was this situation different?"
  ]
}
```

The user may respond to any reflection prompt; a response creates a new entry that is independently run through Stage 0.

---

### 4. Coreference Pre-Pass

**Applies to:** All entries classified as `REFLECTION`, before episode segmentation.

The coreference pre-pass resolves pronouns and name variants **within the single document** to produce a `coreference_map`. This map is passed alongside the cleaned text to the Microextraction LLM, so that entity references within the episode are coherent even if the LLM processes it as a short context window.

> ⚠️ **Scope boundary:** The coreference pre-pass operates strictly within one document. Cross-entry coreference (e.g., "Rahul" in today's entry referring to the same Rahul as three months ago) is handled during Reconciliation via `PersonEntityNode` matching, not here.

**Resolution rules:**
1. Pronoun → most recent unambiguous named referent (standard coref)
2. Name variant → canonical form (`Rax` → `Rahul`, `my manager` → `Neha` if established earlier in same doc)
3. Ambiguous referent (pronoun with multiple plausible antecedents) → preserved as `[AMBIGUOUS_REF: {span}]` rather than resolved; passed to LLM with the ambiguity flag

**Output: `coreference_map` JSON object**

```json
{
  "entry_id": "e_2026_06_11_002",
  "resolved_entities": [
    {
      "span": "he",
      "resolved_to": "Rahul",
      "confidence": 0.94,
      "resolution_basis": "most_recent_named_antecedent"
    },
    {
      "span": "Rax",
      "resolved_to": "Rahul",
      "confidence": 0.88,
      "resolution_basis": "phonetic_nickname_variant"
    },
    {
      "span": "my manager",
      "resolved_to": "Neha",
      "confidence": 0.91,
      "resolution_basis": "role_established_in_document"
    }
  ],
  "ambiguous_refs": [
    {
      "span": "she",
      "candidates": ["Neha", "Priya"],
      "reason": "two female referents introduced within 3 sentences"
    }
  ],
  "language_segments": [
    { "span_start": 0, "span_end": 47, "lang": "en" },
    { "span_start": 48, "span_end": 91, "lang": "hi" },
    { "span_start": 92, "span_end": 145, "lang": "en" }
  ]
}
```

This object is attached to the episode payload and consumed by the Microextraction model. It is also stored as part of the `EpisodeNode` metadata for audit purposes.

---

## What This Protects

### Prevents Garbage Extraction from Incomplete Voice Dumps

Without the quality gate, a 20-word voice note like `"ugh yeah I don't know I was just like really off today"` would produce an attempted extraction with fabricated observation content. The RAW_CAPTURE routing prevents any pattern or belief inference from low-signal inputs.

### Prevents Self-Corrections from Creating False Observations

Consider: `"I think I'm really confident in this decision — wait no, actually I have no idea what I'm doing."` Without self-correction handling, the extraction LLM may produce two observations: one about confidence and one about uncertainty. The correct single observation is about uncertainty. Stage 0 ensures only the correction survives.

### Enables Honest Episode Segmentation Downstream

Episode segmentation (which divides a single entry into multiple conceptual episodes) depends on coherent, clean text. Coreference resolution within the document ensures that when a single entry is split into episodes, pronoun references do not become dangling pointers. The `coreference_map` travels with each episode, regardless of which segment it was extracted from.

---

*See also: [HLDv2.md](../hld/HLDv2.md) for the full data journey, [Extraction/Architecture.md](Architecture.md) for Microextraction schema and enum taxonomy, [Extraction/Reconciliation.md](Reconciliation.md) for cross-entry entity resolution.*
