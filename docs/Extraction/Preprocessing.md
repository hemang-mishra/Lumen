# Stage 0: Preprocessing Pipeline

Real journal inputs are not clean documents. A voice-to-text transcript from Whisper.cpp will contain hesitation fillers (`uh`, `um`, `like, you know`), self-corrections (`wait, no, I meant`), incomplete sentences, mid-thought pivots, and code-mixed language segments where Hindi and English alternate within a single sentence. Feeding this raw to an extraction LLM produces noisy, sometimes contradictory observations that pollute the knowledge graph. Stage 0 exists to normalize input into a clean, structured form before any semantic extraction occurs — not by "fixing" what the user said (except translating to a standard language), but by making their actual meaning legible to downstream models.

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
- Language confidence scores per segment (used to detect non-English spans for translation)
- Speaker diarization output if multi-speaker mode is enabled (stored as metadata, not processed by Stage 0)

Voice notes are expected to be the noisiest input class. All ASR-specific cleaning rules apply only when the input originated as voice audio, but the overall preprocessing stage runs on every entry, including pasted transcripts and typed text.

### Multilingual & Code-Mixed Entries (English Default)

Both voice and pasted/text entries may contain code-mixed language (e.g., Hindi + English). To avoid complicating the downstream extraction and embedding models, Lumen keeps English as the default. If any non-English language or code-mixed sentence is identified, the preprocessing stage translates it into English before passing it to Stage 1.

By standardizing all records into English as early as possible, we drastically simplify the vector embeddings and semantic search (Step 2) because the system won't have to map Hindi synonyms to English synonyms.

### Conversational Chat & The Daily Session Buffer

Lumen fundamentally supports two sources of conversational input via the **Ingestion Layer**:
1. **Native Active Chat Interface:** A built-in multi-turn chat application for querying and reflection.
2. **External Log Importer:** For third-party chat exports (e.g., ChatGPT JSON/Text).

⚠️ **DEPRECATION NOTICE**: Real-time, message-by-message extraction is fully deprecated. Extracting mid-conversation pollutes the graph with unresolved cognitive distortions (e.g., extracting "I am the problem" before the AI helps the user reframe it).

Instead, Lumen uses **Session-Level Extraction (Delayed Stage 1)**. Inputs from these sources are appended to a **Session Buffer**, with each payload carrying its Logical Event Date.

**Session Decay:** For the Active Chat Interface, the system waits for a conversation session to decay (defined as 2 hours of user inactivity, configurable via `LUMEN_SESSION_DECAY_MINUTES`) to prevent extracting mid-thought.

> **Scope boundary — buffer composition is the Ingestion Layer's job, not Stage 0's.**
> Two related behaviours belong to whatever creates buffers, not to the stage that consumes
> one:
> - **Semantic Day Grouping:** if a user returns hours later but explicitly references the
>   same ongoing day (e.g., "So, talking about today..."), that message is appended to the
>   day's existing meta-session so it extracts as one cohesive unit, rather than opening a
>   new buffer purely because the inactivity window elapsed.
> - **Bulk-import splitting:** a set of imported logs spanning multiple past days is split
>   into one buffer per historical `event_date` at ingestion, so each is processed
>   independently as a complete transcript for that date.
>
> Both require reading and merging buffers *other than the one being processed*.
> Preprocessing is a pure function of a single decayed buffer — it holds no database
> handle and cannot see its siblings. A buffer that nonetheless arrives spanning several
> `event_date`s is still processed, using the buffer's own date, and logs a warning naming
> every date it saw.

**Dialogue Act Classification:** Unlike monologues, chat introduces conversational noise: greetings, clarification loops, factual queries ("What did I say yesterday?"), and system generation. Stage 0 runs a classification pass over the buffer:
1. **Factual/Task-oriented turns** (`OPERATIONAL_REQUEST`) are filtered out completely. *Note: Emotionally charged or rhetorical questions (e.g., "What is wrong with my brain?") must be classified as `EXPRESSIVE` rather than `OPERATIONAL_REQUEST` to prevent dropping vulnerable reflections.*
2. **CO_CREATED Marker Detection:** The layer scans for explicit adoption markers in user turns following an AI response (e.g., *"I love the narrative you gave for this"*, *"I'm going to use that framing"*). When detected, these markers are flagged so that downstream processes assign `provenance: CO_CREATED` to the resulting framework nodes.

   Flagging the *turn* is not enough for Microextraction to act on, because the rollup below replaces the dialogue with a summary and the attribution disappears with it. The classification pass therefore also returns the **adopted framings themselves** — the assistant's phrasings the person took up, verbatim — on `PreprocessingResult.co_created_spans`. Those spans are session-scoped, exactly like the coreference map: episode boundaries are decided several steps later, so a span cannot be attributed to one episode at the point it is found. Microextraction marks any observation resting on one of them as `CO_CREATED`.

   When the classification pass fails, the span list is empty and everything downstream reads as `USER_GENERATED`. That is the safe direction — `CO_CREATED` content carries a 0.5 trust weight, so wrongly marking a person's own words as assistant-derived would quietly demote their own history in retrieval.

**Stage 0.5: Session-Level Rollups (Conversational Chat Only):**
Do not stream raw dialogue directly to Microextraction. Because conversational interfaces involve active hypothesis generation, testing, and eventual realizations, streaming raw dialogue leads to intra-session graph fragmentation and Epistemic Churn (e.g., extracting a discarded hypothesis as a concrete belief node).

Stage 0.5 introduces a "Session Summary" pass that:
1. Intercepts the classified dialogue buffer.
2. Isolates the final settled conclusions (`REALIZATION`s) of the conversation.
3. Discards the exploratory conversational scaffolding (intermediate hypotheses, `OPERATIONAL_REQUEST` questions, and the AI's leading prompts).
4. Outputs a cohesive **Session Summary** containing only the settled reflections, tagged with any `CO_CREATED` provenance markers, which is then passed to Stage 1 (Microextraction).

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

#### Language Normalization (English Default)

All entries are normalized to English before any downstream step runs.

Language identification and translation happen **inside the same LLM pass that performs
filler removal and self-correction handling**. That pass already holds the text, so a
separate detection step would be a second read of the same string.

1. The pass reports the languages it detected alongside the cleaned text.
2. If every span is English, the text passes through with only the cleaning applied.
3. If any non-English span is present (including code-mixed Hindi/English), it is translated into English. Interleaved code-mixed sentences are translated as a whole so meaning stays coherent.
4. Completeness scoring, segmentation, coreference, and extraction always receive English text.

> **Why not a dedicated language-ID model.** An on-device classifier such as
> `fastText lid.176.ftz` would let pure-English entries skip a call, but it cannot be
> trusted to make that decision here: the canonical code-mixed example below is romanized
> Hindi written in plain ASCII, which is exactly the case a cheap script or encoding check
> reads as English. Since the pass runs on every entry anyway to strip fillers, folding
> detection into it costs nothing and removes a dependency plus a model file.

**Example:**

| Raw | After Language Normalization |
|---|---|
| `I had a meeting with Jordan today and mujhe samajh nahi aaya ki he was being passive-aggressive` | `I had a meeting with Jordan today and I didn't understand whether he was being passive-aggressive` |

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

Entries that pass the structural gate are sent to a call using the maintainer-configured `LIGHTWEIGHT` model role (see `docs/hld/LLM_Abstraction_Architecture.md`) with a single scoring task:

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

#### Ordering: gate, then segment, then score

The two gates in Step 2 sit on opposite sides of episode segmentation, because they answer
questions at different levels:

1. **The structural gate runs on the whole session, before segmentation.** If the entire
   cleaned entry falls under the word threshold, it is classified `RAW_CAPTURE` as a single
   episode. No segmentation and no coherence call happen — there is nothing to segment and
   nothing worth paying a reasoning model to score.
2. **The semantic gate runs per episode, after segmentation.** Each conceptual episode is
   scored independently, so a session holding one deep reflection and one throwaway aside
   produces two episodes with different classifications rather than one blanket verdict.
3. **The session-level decision aggregates upward.** The session is `REFLECTION` if *any*
   of its episodes scored at or above the threshold, otherwise `RAW_CAPTURE`.

#### DISCARD

`DISCARD` is the one routing decision that throws user input away, so it is never a model
judgement. It fires on a **structural** condition only: nothing extractable survives.
Specifically, after AI turns are stripped, `OPERATIONAL_REQUEST` turns are filtered out,
and cleaning has run, the remaining text is empty or whitespace-only.

A buffer consisting entirely of factual queries ("What did I say yesterday?") discards. An
empty buffer discards. A low-coherence voice dump **does not** — that is what `RAW_CAPTURE`
is for. No coherence score, however low, can produce a `DISCARD`.

A `DISCARD` result carries zero episodes and no reflection prompts.

#### REFLECTION Path

Entries classified as `REFLECTION` proceed to the full pipeline:

```
REFLECTION → Microextraction (Step 1) → Candidate Retrieval (Step 2) → Reconciliation (Step 3) → Graph Write (Step 4)
```

All observation types from the full enum taxonomy are eligible. Sensitivity tier is determined during Microextraction.

#### RAW_CAPTURE Path

Entries classified as `RAW_CAPTURE` receive:

1. **Minimal extraction:** Only `CONTEXT` and `EMOTION` observation types are extracted — a single sentence describing the surface topic of the entry, and a feeling **only where the person named one in their own words**. No emotional inference, no pattern detection. The stated-feeling rule is enforced in code: Microextraction returns the supporting quote with the emotion, and an emotion whose quote is not present in the episode text is discarded.
2. **Reflection prompts:** Stage 0 generates 3 targeted reflection questions from the cleaned episode text at the same time it scores coherence, and returns them on the preprocessing result. They are written a stage earlier than the `CONTEXT` observation that once sourced them, because that observation is a one-sentence restatement of the text the scoring pass has already read — deriving the questions from the text directly saves a round trip and produces the same questions.
3. **No Reconciliation:** These observations are written directly to `ObservationNode`s with `status: RAW_CAPTURE` and are not routed through Reconciliation. They do not participate in candidate retrieval, and no causal anchor (`SessionNode`) is minted for them.

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

> **Storage:** `pending_reflections` records belong in the **Operational DB** (SQLite/PostgreSQL), not in the knowledge graph. They are linked to the entry by `entry_id` and cleaned up once the user responds to at least one prompt or after a 30-day TTL, whichever comes first.
>
> Preprocessing itself only *returns* the questions on `PreprocessingResult` — it is a pure
> function and writes nothing. Persistence is the orchestrator's job, and the table is
> created alongside the review interface that reads it.

---

### 4. Coreference Pre-Pass

**Applies to:** All entries classified as `REFLECTION`, before episode segmentation.

The coreference pre-pass resolves pronouns and name variants **within the single document** to produce a `coreference_map`. This map is passed alongside the cleaned text to the Microextraction LLM, so that entity references within the episode are coherent even if the LLM processes it as a short context window.

> ⚠️ **Scope boundary:** The coreference pre-pass operates strictly within one document. Cross-entry coreference (e.g., "Jordan" in today's entry referring to the same Jordan as three months ago) is handled during Reconciliation via `PersonEntityNode` matching, not here.

**Resolution rules:**
1. Pronoun → most recent unambiguous named referent (standard coref)
2. Name variant → canonical form (`J` → `Jordan`, `my manager` → `Neha` if established earlier in same doc)
3. Ambiguous referent (pronoun with multiple plausible antecedents) → preserved as `[AMBIGUOUS_REF: {span}]` rather than resolved; passed to LLM with the ambiguity flag

**Output: `coreference_map` JSON object**

```json
{
  "entry_id": "e_2026_06_11_002",
  "resolved_entities": [
    {
      "span": "he",
      "resolved_to": "Jordan",
      "confidence": 0.94,
      "resolution_basis": "most_recent_named_antecedent"
    },
    {
      "span": "J",
      "resolved_to": "Jordan",
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
