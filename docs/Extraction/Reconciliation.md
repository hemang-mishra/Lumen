# Reconciliation Layer

Reconciliation is the system's Decision Maker. It sits at the boundary between a fresh, history-blind Microextraction and the user's accumulated knowledge graph. Its job is to answer a single question for each newly extracted observation: *How does this relate to what already exists?*

The answer takes the form of one of eight typed actions: **MERGE**, **REINFORCE**, **EVOLVE**, **BRANCH**, **CONTRADICT**, **DIALECTIC**, **REGULATE**, or **AMBIGUOUS**. Each action has a specific semantic, a confidence threshold, a risk profile, and a defined graph write output. Every action — without exception — is recorded as a `DecisionAuditNode`, making the entire decision history queryable, auditable, and reversible.

Reconciliation prevents two failure modes simultaneously:
- **Fragmentation:** Without Reconciliation, every entry creates isolated nodes. The graph grows flat and wide, with no connections — useless as a knowledge base.
- **Anchoring Bias:** If extraction had seen the history, new observations would be shaped toward existing patterns. Reconciliation happens *after* extraction, so the history only influences *linking*, not *perception*.

---

## Table of Contents

1. [The Seven Actions](#the-seven-actions)
2. [The MERGE Model — Same-As Edges (Not Node Collapse)](#the-merge-model--same-as-edges-not-node-collapse)
3. [The CONTRADICT Action](#the-contradict-action)
4. [The DIALECTIC Action](#the-dialectic-action)
4. [Tie-Breaking & The AMBIGUOUS Escalation](#tie-breaking--the-ambiguous-escalation)
5. [Decision Audit Trail](#decision-audit-trail)
6. [Per-Action Confidence Thresholds](#per-action-confidence-thresholds)
7. [Schema Validation Rules](#schema-validation-rules)
8. [HITL Review Queue](#hitl-review-queue)

---

## The Eight Actions

| Action | Confidence Threshold | Risk Level | What It Does | When to Use |
|---|---|---|---|---|
| **MERGE** | ≥ 0.88 | High | Links the new episode's node to an existing canonical node via a `same_as` edge. Does **not** collapse or delete nodes. Creates a traceable canonical reference. | The new observation is substantively the same as an existing pattern or node — same domain, same behavioral signature, same meaning. |
| **REINFORCE** | ≥ 0.80 | Medium | Adds a `reinforces` edge from the new observation to an existing node. Increases evidential weight. The new episode remains structurally distinct. | The new observation is consistent with and supportive of an existing pattern, but represents a distinct instance rather than identity. |
| **EVOLVE** | ≥ 0.93 | Highest | Creates a new version of an existing belief or pattern node. The prior version is preserved immutably. Requires a mandatory `delta_description` field. | A clear, explicit shift in a previously stable belief or pattern is detected — the user has *changed*, not just confirmed. |
| **BRANCH** | ≥ 0.75 | Low | Creates a new, independent node in the graph with no linking edge to any candidate. The safest action when novelty is genuine. | The new observation is semantically related to candidate nodes but meaningfully distinct — a genuinely new pattern, belief, or domain. |
| **CONTRADICT** | ≥ 0.85 | Medium-High | Flags two logically incompatible beliefs that are simultaneously active. Creates a `ContradictionNode` linking both. Neither belief is evolved, merged, or removed. | The new observation asserts something directly incompatible with an existing, still-active belief — not as a temporal shift (use EVOLVE) but as simultaneous contradiction. |
| **DIALECTIC** | ≥ 0.88 | Medium-High | Creates a `dialectic` edge between the new node and historical candidate node. Neither node is overwritten. | The new observation opposes an existing belief/pattern, but both are fundamentally true and exist in a state of permanent tension or paradox. |
| **REGULATE** | ≥ 0.82 | Medium | Creates a `regulates` edge between the new observation/session and an existing pattern. | The user actively catches and interrupts a negative pattern or cognitive distortion in real-time, without necessarily permanently evolving the underlying pattern yet. |
| **AMBIGUOUS** | Auto-escalate | N/A | Top-2 candidates are within ±0.05 confidence of each other. Action is suspended. Always escalates to HITL queue. Never auto-executes. | Any situation where the model cannot clearly distinguish between two plausible actions. |

> ⚠️ **Implementation Rule:** AMBIGUOUS is not a "low-confidence" action — it is a structural detection of a tie. A model that is 0.91/0.88 confident in two different actions must output AMBIGUOUS. A model that is 0.65 confident in a single action outputs that action (and it enters the HITL queue for being below threshold, but via a different path).

---

## The MERGE Model — Same-As Edges (Not Node Collapse)

A common misconception about MERGE is that it "collapses" the new node into the existing one — deleting the new extraction and redirecting pointers to the canonical node. **This is not how Lumen's MERGE works.**

MERGE creates a directed `same_as` edge from the **newly extracted node** to the **canonical historical node**. Both nodes persist independently. The new episode node retains its full provenance (source entry, timestamp, raw evidence). The canonical node retains its own full history. The `same_as` edge is the relationship record between them.

This preserves three things:
1. **Provenance:** You can always trace which journal entry produced which node, regardless of how many MERGE actions link it to canonical nodes.
2. **Rollback:** To undo a MERGE, you invalidate the `same_as` edge. The nodes continue to exist independently.
3. **Append-only integrity:** No content node is ever deleted or overwritten as a result of Reconciliation.

**JSON schema for a `same_as` edge:**

```json
{
  "edge_type": "same_as",
  "source_node_id": "pat_2026_06_11_slow_pace_new",
  "target_node_id": "pat_decision_saturation",
  "confidence": 0.91,
  "decision_id": "d_2026_06_11_001",
  "valid_from": "2025-01-18T10:34:00Z",
  "invalidated_at": null,
  "created_by_model": "gemini-2.0-flash",
  "model_role": "LIGHTWEIGHT"
}
```

**What the canonical node represents:** The `target_node_id` in a `same_as` edge is the node that was created *earliest* in the chain (or the one that was manually designated canonical by the user via HITL). It acts as the anchor for graph traversal and retrieval scoring. All nodes that `same_as` to a canonical node contribute their evidence and timestamps to that canonical node's aggregate retrieval weight.

---

## The CONTRADICT Action

CONTRADICT handles a specific psychological reality: humans often hold logically incompatible beliefs simultaneously, and neither belief is "wrong" in the context of the user's lived experience. CONTRADICT makes this explicit and visible rather than forcing a false resolution.

**When to use CONTRADICT vs. EVOLVE:**
- **EVOLVE:** The user *previously* held Belief A and has *now* shifted to Belief B. The old belief is superseded. Use EVOLVE to version the belief node.
- **CONTRADICT:** The user holds *both* Belief A and Belief B *right now*. Neither is superseded. Both are active, and they are logically incompatible. Use CONTRADICT.

**Example:**
- Belief A (established 3 months ago): `"I am an introvert and need solitude to recharge"`
- New extraction today: `"I thrive when I'm the loudest person in the room and everyone is paying attention to me"`

These are not a temporal shift (the user hasn't said they no longer need solitude). They are simultaneous. CONTRADICT is the correct action.

**JSON schema for a CONTRADICT action:**

```json
{
  "action": "CONTRADICT",
  "belief_a_id": "bel_introvert_001",
  "belief_b_content": "I thrive when I'm the loudest person in the room",
  "belief_b_node_id": "bel_2026_06_11_expressive_social",
  "contradiction_summary": "User holds simultaneous beliefs about being introverted and thriving in expressive social environments",
  "confidence": 0.87,
  "decision_id": "d_2026_06_11_003",
  "contradiction_node_id": "con_example_001",
  "valid_from": "2025-01-18T11:02:00Z"
}
```

**What CONTRADICT produces in the graph:**
1. A new `BeliefNode` is written for Belief B (the newly extracted belief), if it doesn't already exist.
2. A `ContradictionNode` is written linking both belief nodes.
3. Both `contradicts` edges (from `ContradictionNode` to Belief A and Belief B) are written.
4. A `DecisionAuditNode` records the full decision.

**Resolution of contradictions:** The `ContradictionNode` persists until either:
- The user explicitly resolves it via HITL (selecting which belief to retain, EVOLVE the other away, or mark as "I hold both intentionally")
- A future Macroextraction identifies closure (one belief stops appearing and the other dominates)
- The user directly addresses the contradiction in a journal entry, producing an EVOLVE on one side

Contradictions that remain unresolved appear in Macroextraction reports as `UNRESOLVED_CONTRADICTION` items.

---

## The DIALECTIC Action

While `CONTRADICT` flags a logical error that demands resolution, `DIALECTIC` maps a psychological truth that requires tension. Humans frequently hold two opposing truths simultaneously (e.g., "Critical feedback is genuinely helpful" vs. "I need emotional appreciation").

When the model detects this state:
1. It validates that both the new extraction and historical node assert competing but valid truths about the same subject.
2. Instead of forcing an `EVOLVE` (which deletes the emotional truth) or `BRANCH` (which disconnects them), it creates a `dialectic` edge between the nodes.
3. This allows the graph to act as a "tension tracker," explicitly mapping areas of unresolved cognitive dissonance for longitudinal analysis.
4. **Auto-Detection for Emotional Venting:** If an observation contains an explicit metacognitive invalidation of a deeply felt emotional truth (e.g., "I know this isn't true but it still hurts"), it automatically defaults to `DIALECTIC` rather than `CONTRADICT`.

---

## Tie-Breaking & The AMBIGUOUS Escalation

### Detection

After the Reconciliation model scores all candidate actions against all retrieved candidates, it outputs a ranked list of `(action, confidence)` pairs. AMBIGUOUS is triggered when:

```
|confidence(rank_1) - confidence(rank_2)| < 0.05
```

This applies regardless of the absolute confidence values. A model that is 0.92/0.90 confident in two different actions must output AMBIGUOUS just as a 0.61/0.59 model must.

### Escalation

AMBIGUOUS items are never auto-executed. The action is immediately written to the HITL queue with `status: PENDING_HITL`. The graph write for this episode is **suspended** until the user resolves the queue item. The episode's `EpisodeNode` is written with `reconciliation_status: SUSPENDED`.

### HITL Presentation for AMBIGUOUS Items

In the HITL queue, AMBIGUOUS items are presented with:
- Both candidate actions displayed side-by-side
- The candidate nodes for each action shown (with their content, dates, and signal strength)
- The specific difference highlighted (e.g., "The new observation shares the behavioral domain but adds a new trigger context — is this the same pattern or a branch?")
- Three resolution options:
  1. **Choose Action A** → executes the top-ranked action (MERGE/REINFORCE/EVOLVE/BRANCH/CONTRADICT)
  2. **Choose Action B** → executes the second-ranked action
  3. **Create New** → executes BRANCH regardless of the original candidates

---

## Handling Structural Retrieval Candidates (Pass B)

Stage 2 runs two parallel retrieval passes (see [`Architecture.md`](Architecture.md)). Pass B candidates arrive in the Stage 3 context tagged with `retrieval_source: STRUCTURAL`. These candidates were surfaced via named-entity or era anchors, **not** semantic similarity. Their embedding distance to the current observation may be very high.

**The Stage 3 LLM must not discard structural candidates based on low cosine similarity.**

The full evaluation rule for structural candidates is:

1. **Do not use similarity score as a gating criterion.** A structural candidate with 0.4 cosine similarity to the current observation is still a legitimate reconciliation target. Similarity scores only apply to Pass A candidates.

2. **Evaluate structural candidates on relational relevance, not semantic distance.** Ask: *"Does the current observation describe a change, resolution, continuation, or contradiction of the state captured in this historical node — given that they involve the same person, relationship, or life era?"* If yes, the candidate is eligible for action.

3. **The EVOLVE action is the primary expected outcome for structural candidates in closure contexts.** When a user expresses resolution, detachment, or closure toward a person or relationship (e.g., "I don't want that person anymore," "I've grown out of it," "I had to leave for my own survival"), and the structural pass surfaces an `IDENTITY_FUSION_STATE`, `SUPPRESSED_EMOTION_SURFACING`, or `EXISTENTIAL_REFLECTION` node linked to that person, the default action is `EVOLVE` — not `BRANCH`. `BRANCH` is only correct if the current observation describes an entirely new dimension of the relationship not previously captured.

4. **Record structural source in the audit node.** The `DecisionAuditNode` must include a `candidate_retrieval_source` field set to `STRUCTURAL` for any action that was decided based on a structural candidate. This allows the audit trail to reflect that the decision was anchor-driven, not similarity-driven, and provides full rollback traceability.

```json
{
  "node_type": "DecisionAuditNode",
  "action": "EVOLVE",
  "candidate_retrieval_source": "STRUCTURAL",
  "structural_anchor_type": "NAMED_PERSON",
  "structural_anchor_value": "heartbreak_person_canonical_id",
  "delta_description": "User explicitly states full emotional detachment and closure from this relationship. Prior IDENTITY_FUSION_STATE and SUPPRESSED_EMOTION_SURFACING nodes superseded."
}
```

5. **If no structural candidate is clearly actionable, BRANCH normally.** Structural surfacing is a guarantee that history is *considered* — not a guarantee that it must be linked. If none of the structural candidates are actually relevant to the current observation, the Reconciliation model should output `BRANCH` as it would for any novel node.

> ⚠️ **Silent failure prevention:** The primary risk this section addresses is the *silent EVOLVE failure* — where an emotionally significant node (heartbreak wound, identity-crisis belief, era-linked trauma) remains permanently `reconciliation_status: ACTIVE` in the graph long after the user has resolved it, simply because the closure entry's vocabulary did not semantically match the wound's vocabulary. Pass B + this rule together prevent that failure mode.

---

## Decision Audit Trail

Every Reconciliation action — including AMBIGUOUS resolutions, CONTRADICT creations, and HITL-resolved items — creates a `DecisionAuditNode` in the graph. This node is itself a first-class graph citizen, queryable like any other node.

### DecisionAuditNode Full JSON Schema

```json
{
  "node_type": "DecisionAuditNode",
  "node_id": "d_2026_06_11_001",
  "created_at": "2025-01-18T10:34:17Z",
  "action": "MERGE",
  "source_node_id": "obs_2026_06_11_004",
  "target_node_id": "pat_decision_saturation",
  "edge_type_created": "same_as",
  "edge_id": "edge_2026_06_11_009",
  "confidence": 0.91,
  "confidence_runner_up": 0.83,
  "runner_up_action": "REINFORCE",
  "delta_description": null,
  "model_used": "gemini-2.0-flash",
  "model_role": "LIGHTWEIGHT",
  "hitl_resolved": false,
  "hitl_resolution_timestamp": null,
  "hitl_resolution_user_choice": null,
  "rollback_pointer": {
    "edge_to_invalidate": "edge_2026_06_11_009",
    "nodes_to_requeue": ["obs_2026_06_11_004"]
  },
  "status": "ACTIVE"
}
```

**Field notes:**
- `delta_description` — required and non-null only when `action == EVOLVE`. Contains a plain-language description of what changed between the old and new version.
- `confidence_runner_up` and `runner_up_action` — always recorded, even when AMBIGUOUS is not triggered. Enables retroactive analysis of close decisions.
- `rollback_pointer` — specifies exactly what to invalidate and re-queue if this decision is reversed.
- `status` — `ACTIVE | ROLLED_BACK`. Status changes do not delete the node (append-only).

### Rollback Procedure

```
DELETE /decisions/{decision_id}
```

1. The `DecisionAuditNode` `status` field is updated to `ROLLED_BACK` (a new version of the node is written; the original is preserved).
2. The edge specified in `rollback_pointer.edge_to_invalidate` has its `invalidated_at` timestamp set.
3. All node IDs in `rollback_pointer.nodes_to_requeue` are added to the Reconciliation queue with `status: PENDING_RERECONCILIATION`.
4. The user is notified that the rollback is complete and that affected items are in the HITL queue.

> ⚠️ Rollback does not delete any content node. It only invalidates edges. The graph's content layer remains immutable throughout.

---

## Per-Action Confidence Thresholds

| Action | Minimum Confidence | Model Role |
|---|---|---|
| `MERGE` | 0.88 | `LIGHTWEIGHT` |
| `REINFORCE` | 0.80 | `LIGHTWEIGHT` |
| `EVOLVE` | 0.93 | `THINKING` |
| `BRANCH` | 0.75 | `LIGHTWEIGHT` |
| `CONTRADICT` | 0.85 | `THINKING` |
| `DIALECTIC` | 0.88 | `THINKING` |
| `REGULATE` | 0.82 | `LIGHTWEIGHT` |
| `AMBIGUOUS` | N/A (tie detection) | N/A — always HITL |

> **Model roles:** `LIGHTWEIGHT` is used for low-to-medium-risk actions where speed matters more than deep reasoning. `THINKING` is used for high-consequence or nuanced-judgment actions. Which actual provider and model back each role — cloud or local — is a single maintainer-configured deployment choice (`ProviderConfig`, see `docs/hld/LLM_Abstraction_Architecture.md`), not a decision the pipeline makes per observation based on content sensitivity, and not something the end user selects.

### The "Trial vs. Trait" Rule (Temporal Frequency Multiplier)

A core psychological principle of Lumen is that a single successful deviation from a long-held belief does not constitute a permanent identity shift. Overcoming a 10-year fear of going out alone for *one day* is a "Trial", not a "Trait".

To enforce this, Reconciliation applies a **Confidence Threshold Multiplier** based on the temporal frequency of the conflicting evidence:
- If a new observation contradicts a long-held belief (e.g., age > 180 days) for the *first time*, the system enforces a strict penalty on the `EVOLVE` and `CONTRADICT` thresholds (e.g., effective threshold becomes 0.98, effectively unachievable).
- This forces the Reconciliation model to default to `BRANCH`, creating a new, independent node (e.g., "Instances of independence").
- Only when these branched nodes reach a critical mass or frequency over time does the Macroextraction layer (or a subsequent high-confidence Reconciliation) trigger the full `EVOLVE` or `CONTRADICT` action on the canonical belief.
- **Metacognitive Bypass:** If a new observation is explicitly typed as `METACOGNITIVE_BREAKTHROUGH` and carries a `HIGH` or `CRITICAL` signal strength, it bypasses the temporal frequency penalty entirely. The user's explicit self-awareness overrides the structural skepticism, allowing an immediate `EVOLVE` or `CONTRADICT`. Note: `CRITICAL` here refers to the observation's `signal_strength` value (the 2.0× retrieval multiplier), not a routing tier.
- **Active Regulation:** If the user actively catches and interrupts an ongoing pattern (e.g., catching a "critic brain" spiral), the model outputs the `REGULATE` action instead of `BRANCH` or `EVOLVE`. This creates a `regulates` edge to the canonical pattern, successfully tracking the nascent behavioral change without requiring a full identity `EVOLVE` or causing a fragmented `BRANCH`.

### Era Baseline Protection (Local Extremum vs Baseline Shift)

When processing observations that reflect intense burnout, psychological shock, or severe performance degradation, the Reconciliation engine must evaluate the temporal scope of the candidate era. High-intensity, short-duration states (like finals week or project crunch time) are tagged as `LOCAL_EXTREMUM`. These states must not overwrite or trigger an `EVOLVE` action on the macro-baseline (`BASELINE_SHIFT`) of a long-running era (e.g., a multi-month internship). 
- If an observation indicates an extreme deviation from a previously stable baseline within the same era, the action defaults to `BRANCH` (to record the local extremum) rather than `EVOLVE`. 
- This prevents recency bias from corrupting the graph during high-stress transitional periods.

**Below-threshold behavior:** When a model outputs an action but its confidence falls below the threshold for that action, the item is routed to the HITL queue as `status: BELOW_THRESHOLD`. It is **not** auto-promoted to BRANCH. It waits in the queue.

---

## Schema Validation Rules

These rules are enforced in code at the point of the Reconciliation response parsing. Validation failures trigger re-extraction or rejection, not silent override.

| Rule | Condition | Enforcement Action |
|---|---|---|
| **R1** | `observation.type == SUPPRESSED_EMOTION_SURFACING` AND `signal_strength != HIGH` | Reject extraction response. Re-extract with error context. |
| **R2** | `observation.type IN [METACOGNITIVE_INTERRUPT, METACOGNITIVE_BREAKTHROUGH]` AND `signal_strength NOT IN [HIGH, CRITICAL]` | Reject extraction response. Re-extract with error context. (`CRITICAL` is a valid `signal_strength` value — the 2.0× retrieval multiplier — distinct from routing tier.) |
| **R3** | `observation.provenance == CO_CREATED` AND `reconciliation.action == EVOLVE` | **Ownership transfer rule.** Allow EVOLVE normally. Set the new version node's `provenance = USER_GENERATED` and `verification_status = VERIFIED`. The user has taken ownership of the framework they are refining. Record `co_created_origin: true` in the `DecisionAuditNode` for lineage tracing. |

> ⚠️ Rule R5 is the only rule that *overrides* rather than *rejects*. This is intentional: if the model failed to detect a tie but the scores reveal one, the system corrects automatically without burning an additional LLM call. All other rules reject and re-extract.

**Re-extraction limit:** A single observation may be re-extracted at most **3 times** due to validation failures. On the third failure, the observation is written as `status: EXTRACTION_FAILED`, linked to the episode with a `failed_extraction` edge, and surfaced in the next HITL queue session.

---

## CO_CREATED Provenance Rules

Lumen tracks three provenance states for `ObservationNode` content:

| Provenance | Meaning |
|---|---|
| `USER_GENERATED` | The user articulated this observation organically, without AI framing. |
| `AI_GENERATED` | The AI generated this as a response or theory. Never extracted as user content. |
| `CO_CREATED` | The user **explicitly adopted** an AI-generated framework, vocabulary, or insight as their own. |

### How CO_CREATED Is Detected

The Preprocessing layer looks for explicit adoption markers in user turns following an AI response. Examples:
- *"I love the narrative you gave for this."*
- *"Yes, that's exactly it — meaning and relationship."*
- *"I'm going to use that framing going forward."*

When these markers appear and the user incorporates the AI's framework into their subsequent reflection, the extracted node carries `provenance: CO_CREATED`.

### Ownership Transfer on EVOLVE (Rule R6)

When a user later refines, extends, or applies a `CO_CREATED` node in a new session, the refinement is extracted as `provenance: USER_GENERATED`. The EVOLVE action links the new node to the CO_CREATED ancestor. The `DecisionAuditNode` records `co_created_origin: true` to preserve the lineage.

**Rationale:** Once a user refines a framework, they have taken full intellectual ownership. The system should not perpetually mark an evolving concept as CO_CREATED simply because it started as an AI suggestion. The user's ongoing engagement transforms it into lived knowledge.

### CO_CREATED Retrieval Behavior

CO_CREATED nodes carry `verification_status: UNVERIFIED` by default and receive a **0.5× trust_weight penalty** in the retrieval score formula (see `Architecture.md`). This means they are still retrievable and injectable in Conversational RAG, but ranked significantly lower than `USER_GENERATED` (IMPLICIT, 1.0×) or user-confirmed (VERIFIED, 1.0×) nodes.

**Promotion to VERIFIED:** A CO_CREATED node's `verification_status` promotes from `UNVERIFIED` to `VERIFIED` only through explicit user action:
1. User confirms accuracy in HITL review queue.
2. User independently re-articulates the concept in a later session, triggering EVOLVE (Rule R3 ownership transfer also sets `verification_status = VERIFIED`).

There is no automatic promotion based on reinforcement count.

## HITL Review Queue

The HITL queue is the human-in-the-loop interface for all items that require user judgment. It is designed for quick, low-friction resolution — one tap per item — rather than deep cognitive engagement.

### Entry Conditions

Items enter the queue under three conditions:

| Condition | Entry Type | Priority |
|---|---|---|
| `action == AMBIGUOUS` (tie detected) | `AMBIGUOUS_TIE` | 1 (highest) |
| Action confidence below threshold | `BELOW_THRESHOLD` | 2 |
| Repeated extraction failure (3rd failure) | `EXTRACTION_FAILED` | 3 |

### Queue Priority Order

Items are ordered within the queue as follows:
1. `AMBIGUOUS_TIE` items first (regardless of signal strength)
2. Within each entry type: `signal_strength` descending (`CRITICAL` > `HIGH` > `STANDARD`)
3. Within same entry type and signal strength: entry age ascending (oldest first)

> `CRITICAL` signal strength (2.0× retrieval multiplier) here determines priority within the queue, not a routing tier.

### Queue Capacity & Hard Cap

**Maximum queue size: 40 items.** (Configurable via `LUMEN_HITL_QUEUE_CAP` — see `lumen.config.OperationalConfig`.)

When the queue reaches the cap:
- New items that would enter the queue are written with `status: SUSPENDED_QUEUE_FULL`.
- `SUSPENDED_QUEUE_FULL` items do **not** auto-BRANCH. They wait.
- When queue size drops below the cap, suspended items enter in priority order.
- The user is notified when the queue is full and when items are unblocked.

### SNOOZE Behavior

- The user may SNOOZE any queue item to defer it to the next weekly session.
- A snoozed item retains its `status: PENDING_HITL` and its position in queue (re-inserted at the same priority).
- Snoozed items carry a `snooze_count` counter and a `last_snoozed_at` timestamp.

### Auto-Resolve Policy

Auto-resolve is strictly limited to the following condition:

```
item.snooze_count >= 1
AND item.last_snoozed_at < (NOW - 7 days)
→ action = BRANCH (auto-executed)
→ DecisionAuditNode written with hitl_resolved: true, hitl_resolution_user_choice: "AUTO_BRANCH_AFTER_SNOOZE"
```

Items that have **never been viewed or snoozed** do **not** auto-resolve. They remain `SUSPENDED` or `PENDING_HITL` indefinitely.

> ⚠️ This policy exists to prevent the system from making permanent graph decisions on behalf of the user without any signal of intent. A snoozed item has been acknowledged; a never-viewed item has not.

### UI Requirements

- **Mobile-first:** Designed for one-thumb operation during a 2-minute session.
- **One-tap actions:** Approve (execute the recommended action), Reject (execute BRANCH instead), Snooze (defer 7 days).
- **AMBIGUOUS layout:** Side-by-side comparison of both candidates with difference summary highlighted. Three-button resolution (Action A / Action B / Create New).
- **Context strip:** Each item shows the source entry date, the episode summary (≤ 2 sentences), and the existing candidate node content. No scrolling required to make a judgment.
- **Badge count:** App badge shows unresolved HITL count. Weekly session nudge if count > 3.

---

*See also: [HLDv2.md](../hld/HLDv2.md) for the complete data journey, [Graph/Schema.md](../Graph/Schema.md) for node and edge type definitions, [Extraction/Architecture.md](Architecture.md) for the Microextraction enum taxonomy.*
