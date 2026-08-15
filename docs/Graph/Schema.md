# Knowledge Graph Schema

The knowledge graph is the persistent memory of the Lumen system. It stores every observation, pattern, belief, lesson, person entity, decision, and synthesis report as a typed node, with typed edges representing the relationships between them.

Three rules govern the graph at the implementation layer:

1. **Content nodes are immutable (append-only).** Once written, a node's `content` fields are never updated. Changes produce new versioned nodes with `evolved_from` edges.
2. **Only connections are modifiable — and only via the Decision Audit Trail.** Edges carry `invalidated_at` timestamps. An invalidated edge is a soft-delete: it is retained in the graph for audit purposes but excluded from active traversal.
3. **Bipartite Causal Graph:** `BeliefNode` and `PatternNode` instances cannot directly mutate or evolve out of nowhere. A new version (`EVOLVE`) or a new conflict (`CONTRADICT`) must be anchored by an intervening `EventNode` or `SessionNode` to guarantee precise causal chains.

Together these rules make the graph a complete, replayable record of the user's psychological history.

---

## Table of Contents

1. [Node Types](#node-types)
2. [Edge Types](#edge-types)
3. [Temporal Model](#temporal-model)
4. [Retrieval Score Formula](#retrieval-score-formula)
5. [Version Chain Example](#version-chain-example)
6. [Soft Delete / Erasure (DPDP/GDPR Compliance)](#soft-delete--erasure-dpdpgdpr-compliance)

---

## Node Types

---

### 1. EpisodeNode

A single conceptual episode extracted from a journal entry. One journal entry may produce multiple `EpisodeNode` instances if the entry covers distinct conceptual domains.

```yaml
node_type: EpisodeNode
node_id: ep_2026_06_11_001
entry_id: entry_2026_06_11_raw
occurred_at: "2025-01-18T10:30:00Z"      # Logical Event Date
created_at: "2025-01-18T10:31:00Z"       # System Extraction Date
valid_from: "2025-01-18T10:31:00Z"
event_date: "2025-01-18"                 # Calendar date of the session (part of composite key)
session_label: "A"                       # Sub-day session identifier (part of composite key with event_date)
source_modality: VOICE_NOTE              # VOICE_NOTE | TEXT_ENTRY
entry_class: REFLECTION                  # REFLECTION | RAW_CAPTURE
language_tags: ["en"]                    # always English after Stage 0 normalization
episode_summary: "User reflects on a slow, deliberate decision-making approach when confronting a career pivot"
historical_era: "a major entrance exam_PREP"   # Optional. Anchors the episode to a specific past life chapter
overarching_themes: ["career_decision", "information_gathering"]   # High-level tags spanning the entry
episode_index: 1                         # ordinal position within the entry
total_episodes_in_entry: 2
coreference_map_id: coref_2026_06_11_001
reconciliation_status: COMPLETE          # COMPLETE | SUSPENDED | PENDING_RERECONCILIATION
raw_text_hash: "sha256:a3f..."           # hash of cleaned episode text for deduplication
```

**Composite key:** `(event_date, session_label)` uniquely identifies the originating session. A single calendar day can have sessions `"A"`, `"B"`, etc. Episodes from the same session share the same `(event_date, session_label)` and are linked via `follows_from` edges to preserve intra-session narrative flow.

---

### 2. ObservationNode

A single structured observation extracted within an episode. One episode produces one or more `ObservationNode` instances. This is the atomic unit of extraction.

```yaml
node_type: ObservationNode
node_id: obs_2026_06_11_004
episode_id: ep_2026_06_11_001
occurred_at: "2025-01-18T10:30:00Z"      # Logical Event Date inherited from Episode
created_at: "2025-01-18T10:31:45Z"       # System Extraction Date
valid_from: "2025-01-18T10:31:45Z"
type: BEHAVIORAL_PATTERN_OBSERVATION     # from closed enum (see Extraction/Microextraction.md)
content: "User consistently defers major decisions until they have gathered significantly more information than peers deem necessary"
raw_evidence:
  - "I just can't pull the trigger until I feel like I've exhausted every angle"
  - "everyone else was ready to decide weeks ago"
signal_strength: HIGH                    # STANDARD | HIGH | CRITICAL
provenance: USER_GENERATED               # USER_GENERATED | AI_GENERATED | CO_CREATED
verification_status: IMPLICIT            # IMPLICIT | UNVERIFIED | VERIFIED — gates retrieval trust_weight (see Architecture.md)
person_refs: []                          # array of PersonEntityNode IDs referenced by this observation
open_loop_ref: null                      # ID of an OpenLoopNode this observation directly addresses or raises
extraction_confidence: STANDARD          # STANDARD | RECONSTRUCTIVE
status: ACTIVE                           # ACTIVE | RAW_CAPTURE | EXTRACTION_FAILED | SUSPENDED
extraction_model: gemini-2.0-flash
extraction_attempt: 1                    # increments on validation failure re-extract
```

**`person_refs`:** An array of `PersonEntityNode` IDs. Each entry generates a `mentions` edge during graph write.

**`extraction_confidence`:** Set to `RECONSTRUCTIVE` when the user narrates an event from memory more than 90 days after it occurred with no direct log evidence. `RECONSTRUCTIVE` nodes are valid but tagged for potential revision.

**`open_loop_ref`:** Set to an `OpenLoopNode` ID when this observation directly addresses or raises a specific open psychological question. Null when no open loop is implicated.

---

### 3. EventNode

A discrete, objective occurrence that anchors psychological shifts. Events are concrete actions or occurrences (e.g., "Ate alone at cafe", "Received promotion"). They serve as the causal bridge between beliefs in the bipartite graph schema.

```yaml
node_type: EventNode
node_id: evt_example_001
episode_id: ep_example_001
occurred_at: "2025-02-10T15:00:00Z"      # Logical Event Date
created_at: "2025-02-10T16:00:00Z"       # System Extraction Date
valid_from: "2025-02-10T16:00:00Z"
event_summary: "Went to a local cafe alone to eat, breaking a long-standing pattern of avoidance."
raw_evidence:
  - "I just went out to a local cafe alone without the fear"
person_refs: []
signal_strength: HIGH                    # STANDARD | HIGH | CRITICAL
status: ACTIVE
```

---

### 3.1. SessionNode

A conversational session or period of internal realization that produces a cognitive shift without an external physical event. Used to anchor EVOLVE or CONTRADICT actions driven by internal dialogue or AI-facilitated breakthroughs.

**One `SessionNode` is minted per `REFLECTION` episode by Stage 1 (Microextraction), in code rather than by the extraction model.** Rule 3 above forbids a belief or pattern from evolving without an intervening `EventNode` or `SessionNode`, so an anchor must always exist; leaving that to a model's judgement about what counts as an event would make a structural guarantee probabilistic. `RAW_CAPTURE` episodes get no anchor, since they bypass Reconciliation entirely.

```yaml
node_type: SessionNode
node_id: sess_example_001
episode_id: ep_example_002
occurred_at: "2025-02-08T20:11:00Z"      # Logical Event Date
created_at: "2025-02-08T21:00:00Z"       # System Extraction Date
valid_from: "2025-02-08T21:00:00Z"
event_date: "2025-02-08"                 # Calendar date (part of composite key)
session_label: "A"                       # Sub-day session identifier (part of composite key)
session_summary: "Deep conversational breakthrough resolving an identity fusion conflict through dialogue."
participant_entities:
  - user
  - ai_facilitator
signal_strength: HIGH                    # STANDARD | HIGH | CRITICAL
status: ACTIVE
```

---

### 3.2. CausalChainNode

A first-class node representing a multi-step causal sequence extracted from an episode. Causal chains capture the "how" of an experience — the sequence of trigger, internal states, actions, outcomes, and lessons. One episode may contain multiple distinct causal chains.

```yaml
node_type: CausalChainNode
node_id: chain_2026_06_11_001
episode_id: ep_2026_06_11_001
created_at: "2025-01-18T10:32:00Z"
valid_from: "2025-01-18T10:32:00Z"
chain_summary: "Headache-triggered slowdown leading to full energy restoration"
is_anticipatory: false                   # true if the chain describes a hypothetical/feared outcome, not an event that actually occurred
step_count: 6
status: ACTIVE
```

The actual steps are stored as `CausalStepNode` instances linked via `chain_contains` edges, making individual steps traversable for counterfactual retrieval queries.

---

### 3.3. CausalStepNode

A single typed step within a `CausalChainNode`. Steps are ordered. A chain may branch (one action producing two different outcomes at different time points), in which case parallel steps carry a `branch_id`.

```yaml
node_type: CausalStepNode
node_id: step_2026_06_11_001_s3
chain_id: chain_2026_06_11_001
step_index: 3                            # ordinal position within the chain (1-indexed)
step_type: ACTION                        # TRIGGER | INTERNAL_STATE | ACTION | OUTCOME | LESSON
content: "Relieved all expectations, went at very slow pace"
branch_id: null                          # non-null when this step is part of a parallel branch from the same action step
created_at: "2025-01-18T10:32:00Z"
```

#### Cypher — Counterfactual Retrieval Example

```cypher
-- "What worked before when I was overwhelmed?"
MATCH (ep:EpisodeNode)-[:contains]->(chain:CausalChainNode)
      -[:chain_contains]->(s_state:CausalStepNode {step_type: "INTERNAL_STATE"})
WHERE s_state.content CONTAINS "overwhelmed"
WITH chain
MATCH (chain)-[:chain_contains]->(s_action:CausalStepNode {step_type: "ACTION"})
MATCH (chain)-[:chain_contains]->(s_outcome:CausalStepNode {step_type: "OUTCOME"})
RETURN s_state.content, s_action.content, s_outcome.content
ORDER BY ep.occurred_at DESC
```

---

### 4. PatternNode

A recurring behavioral or cognitive pattern identified across multiple episodes. PatternNodes are versioned: EVOLVE actions create new versions. Must be anchored to an EventNode when evolving.

```yaml
node_type: PatternNode
node_id: pat_decision_saturation
version: 2
previous_version_id: pat_decision_saturation_v1
created_at: "2024-08-01T08:00:00Z"
valid_from: "2025-01-18T10:34:00Z"       # valid_from updates on EVOLVE
last_reinforced_at: "2025-01-18T10:34:00Z"
pattern_name: "Deliberate Information Saturation Before Decision"
pattern_description: "User systematically over-collects information before committing to any significant decision, prioritizing certainty over speed. In v2, pattern extends to interpersonal confrontations, not just strategic decisions."
domain: COGNITIVE_STYLE                  # COGNITIVE_STYLE | EMOTIONAL | BEHAVIORAL | RELATIONAL | CAREER | HEALTH | SELF_CONCEPT | FINANCIAL | SPIRITUALITY | RECREATIONAL | ENVIRONMENTAL
signal_strength: HIGH                    # STANDARD | HIGH | CRITICAL
provenance: USER_GENERATED
verification_status: IMPLICIT            # IMPLICIT | UNVERIFIED | VERIFIED — gates retrieval trust_weight (see Architecture.md)
evidence_count: 7                        # count of ObservationNodes linked via reinforces or same_as edges
archetype_tags: ["high_conscientiousness", "risk_averse"]
era_tag: null                            # Optional historical era anchor (e.g. "a major entrance exam_PREP"). Used by Pass B structural retrieval.
query_frequency: 0                       # Incremented each time this node is surfaced in a user query. Retrieval boost: +0.1x per query hit, max 1.5x total.
is_canonical: true
status: ACTIVE                           # ACTIVE | SUPERSEDED | SUPPRESSED
```

---

### 5. BeliefNode

An underlying worldview rule — a first-person statement of how the user believes the world works, how they see themselves, or what they value. Versioned identically to PatternNode.

```yaml
node_type: BeliefNode
node_id: bel_introvert_001
version: 1
previous_version_id: null
created_at: "2024-05-10T14:22:00Z"
valid_from: "2024-05-10T14:22:00Z"
last_reinforced_at: "2024-10-05T09:10:00Z"
belief_statement: "I am an introvert who needs solitude to recharge after social interaction"
belief_source_summary: "Expressed explicitly in entry e_2025_11_03 and reinforced in 4 subsequent entries"
domain: SELF_CONCEPT
signal_strength: HIGH                    # STANDARD | HIGH | CRITICAL
provenance: USER_GENERATED
verification_status: IMPLICIT            # IMPLICIT | UNVERIFIED | VERIFIED — gates retrieval trust_weight (see Architecture.md)
evidence_count: 5
era_tag: null                            # Optional historical era anchor for Pass B structural retrieval.
query_frequency: 0                       # retrieval frequency counter (see PatternNode for semantics)
is_contradicted: true                    # true if linked to an active ContradictionNode
contradiction_node_id: con_example_001
version_delta: null                      # plain-language description of change (populated on EVOLVE)
status: ACTIVE
```

---

### 6. LessonNode

Extracted wisdom — a distilled takeaway that the user has explicitly or implicitly drawn from an experience. Unlike a belief, a lesson is epistemically bounded ("I learned that...") rather than a generalized worldview rule.

```yaml
node_type: LessonNode
node_id: les_example_001
created_at: "2024-11-10T17:05:00Z"
valid_from: "2024-11-10T17:05:00Z"
lesson_statement: "Volunteering for high-visibility projects before I feel fully ready consistently accelerates my growth more than waiting for readiness"
evidence_episodes:
  - ep_example_006
  - ep_example_005
domain: CAREER
signal_strength: HIGH                    # STANDARD | HIGH | CRITICAL
lesson_confidence: 0.84                  # extraction-time confidence in lesson validity
status: ACTIVE
```

---

### 7. AdoptedPrincipleNode

A prescriptive commitment the user has explicitly chosen to follow. Unlike a `BeliefNode` (descriptive — "I tend to do X"), an `AdoptedPrincipleNode` is prescriptive — "I am committing to doing Y."

```yaml
node_type: AdoptedPrincipleNode
node_id: prin_work_relationship_001
created_at: "2025-01-15T10:00:00Z"
valid_from: "2025-01-15T10:00:00Z"
adopted_at: "2025-01-15T10:00:00Z"
principle_statement: "Before every work session, perform an autotelic shift: ask why this work is worth doing in itself, and what my current relationship with it is."
principle_name: "Autotelic Shift + Relationship Check"
domain: PRODUCTIVITY                        # PRODUCTIVITY | HEALTH | RELATIONAL | COGNITIVE | IDENTITY
lifecycle_state: TRYING                     # TRYING | INTERNALIZED | SUSPENDED | ABANDONED
lifecycle_updated_at: "2025-01-15T10:00:00Z"
lifecycle_history:
  - state: TRYING
    at: "2025-01-15T10:00:00Z"
    reason: "User explicitly committed to this in a recent journal entry"
source_session_id: "session-id-example"
provenance: CO_CREATED                      # USER_GENERATED | CO_CREATED
parent_belief_ids: []
supersedes_id: null
last_referenced_at: "2025-01-15T10:00:00Z"
evidence_count: 1
status: ACTIVE
```

#### Lifecycle States

| State | Meaning | Transition Triggers |
|---|---|---|
| `TRYING` | User has committed to this principle but has not internalized it. Following it requires conscious effort. | Initial adoption |
| `INTERNALIZED` | User describes applying it naturally, without deliberate effort. | Macroextraction or explicit journal statement |
| `SUSPENDED` | User has acknowledged they stopped following it temporarily (not abandonment). | Journal entry saying "I've been neglecting this" |
| `ABANDONED` | User explicitly says this principle did not work or they have moved on. | EVOLVE action on the node, or explicit journal disavowal |

#### Query Pattern — Principles Active at Time T

```cypher
MATCH (p:AdoptedPrincipleNode)
WHERE p.valid_from <= "2026-06-30"
  AND (p.lifecycle_state IN ["TRYING", "INTERNALIZED"]
       OR p.lifecycle_updated_at > "2026-06-01")
RETURN p.principle_name, p.lifecycle_state, p.adopted_at
ORDER BY p.adopted_at
```

---

### 8. PersonEntityNode

A named person who appears across one or more journal entries.

```yaml
node_type: PersonEntityNode
node_id: person_jordan_001
canonical_name: "Jordan"
aliases:
  - "J"
  - "R"
  - "my colleague Jordan"
first_mentioned_at: "2024-03-01T00:00:00Z"
last_mentioned_at: "2025-01-18T00:00:00Z"
mention_count: 12
relationship_to_user: COLLEAGUE          # COLLEAGUE | FRIEND | FAMILY | MANAGER | PARTNER | OTHER | UNKNOWN
relationship_sentiment_trend: NEUTRAL_TO_NEGATIVE   # POSITIVE | NEUTRAL | NEUTRAL_TO_NEGATIVE | NEGATIVE | MIXED | UNKNOWN
linked_observation_types:               # observation types that have appeared in mentions of this person
  - RELATIONAL_DYNAMIC
  - GRATITUDE_APPRECIATION
  - OTHER_PERSON_MODEL
is_canonical: true
merged_from: []
status: ACTIVE
```

---

### 9. DecisionAuditNode

A first-class node recording every Reconciliation action. Every Reconciliation-produced edge is traceable to exactly one `DecisionAuditNode`. See [Reconciliation.md](../Extraction/Reconciliation.md) for full behavioral documentation.

```yaml
node_type: DecisionAuditNode
node_id: d_2026_06_11_001
created_at: "2025-01-18T10:34:17Z"
action: MERGE                            # MERGE | REINFORCE | EVOLVE | BRANCH | CONTRADICT | DIALECTIC | REGULATE | AMBIGUOUS
source_node_id: obs_2026_06_11_004       # ObservationNode | EventNode | SessionNode — the extracted node this decision concerns
target_node_id: pat_decision_saturation
edge_type_created: same_as
edge_id: edge_2026_06_11_009
confidence: 0.91
confidence_runner_up: 0.83
runner_up_action: REINFORCE
delta_description: null                  # required and non-null only for action == EVOLVE
model_used: gemini-2.0-flash
model_role: LIGHTWEIGHT                  # LIGHTWEIGHT | THINKING | EMBEDDING | TRANSCRIPTION | TTS
hitl_resolved: false
hitl_resolution_timestamp: null
hitl_resolution_user_choice: null        # "ACTION_A" | "ACTION_B" | "CREATE_NEW" | "AUTO_BRANCH_AFTER_SNOOZE"
snooze_count: 0                          # number of times the user has snoozed this HITL item
last_snoozed_at: null
candidate_retrieval_source: SEMANTIC     # SEMANTIC | STRUCTURAL
structural_anchor_type: null            # NAMED_PERSON | HISTORICAL_ERA — populated when candidate_retrieval_source == STRUCTURAL
structural_anchor_value: null           # the anchor value (person node ID or era tag string)
co_created_origin: false                # true when the source node carried provenance: CO_CREATED and action == EVOLVE (Rule R6 ownership transfer)
rollback_pointer:
  edge_to_invalidate: edge_2026_06_11_009
  nodes_to_requeue:
    - obs_2026_06_11_004
status: ACTIVE                           # ACTIVE | ROLLED_BACK | PENDING_HITL | BELOW_THRESHOLD | SUSPENDED_QUEUE_FULL | EXTRACTION_FAILED
```

**`model_role`:** Which model-capability role handled this decision — practically always `LIGHTWEIGHT` (fast, low-risk actions: MERGE, REINFORCE, BRANCH, REGULATE) or `THINKING` (deeper reasoning, high-consequence actions: EVOLVE, CONTRADICT, DIALECTIC). Resolved purely from the operator's provider configuration (`ProviderConfig`, see `docs/hld/LLM_Abstraction_Architecture.md`) — this field carries no information about where the model ran (cloud vs. local). There is no privacy or sensitivity tier: an operator who wants guaranteed-local processing configures every role to a local provider, once, as a deployment choice — the pipeline never inspects content sensitivity to decide routing at runtime.

**`status` lifecycle:**
- `ACTIVE` — decision executed and live in the graph
- `ROLLED_BACK` — edge invalidated; affected nodes re-queued
- `PENDING_HITL` — awaiting user resolution (AMBIGUOUS tie detected)
- `BELOW_THRESHOLD` — model confidence fell below action threshold; in HITL queue
- `SUSPENDED_QUEUE_FULL` — HITL queue at its item cap; item waiting to enter
- `EXTRACTION_FAILED` — observation failed validation 3 times; graph write skipped

---

### 10. ContradictionNode

Represents two simultaneously-held, logically incompatible beliefs. Created by the CONTRADICT Reconciliation action.

```yaml
node_type: ContradictionNode
node_id: con_example_001
created_at: "2025-01-18T11:02:00Z"
valid_from: "2025-01-18T11:02:00Z"
belief_a_id: bel_introvert_001
belief_b_id: bel_2026_06_11_expressive_social
contradiction_summary: "User holds simultaneous beliefs about being introverted and thriving in expressive, high-attention social environments"
decision_id: d_2026_06_11_003
resolution_status: UNRESOLVED            # UNRESOLVED | RESOLVED_EVOLVE | RESOLVED_USER | RESOLVED_MACRO
resolved_at: null
resolution_decision_id: null
```

---

### 11. MacroextractionReportNode

An immutable synthesis report produced by a Periodic Intelligence (Macroextraction) job.

> This node stores the **graph-queryable envelope fields** only. The full report content (pattern analytics, belief changes, archetype shift details, proof chains, emotional valence, prospective memory, etc.) is stored in the `report_content` JSON field. The schema for that JSON blob is defined in [Extraction/Macroextraction.md](../Extraction/Macroextraction.md).

```yaml
node_type: MacroextractionReportNode
node_id: macro_2026_06_01_weekly
created_at: "2026-06-01T06:00:00Z"
report_type: WEEKLY                      # SHADOW | WEEKLY | MONTHLY | QUARTERLY
period_start: "2026-05-25T00:00:00Z"
period_end: "2026-06-01T00:00:00Z"
episodes_analyzed: 14
archetype_shift_detected: false
model_used: gemini-2.0-pro
report_content: { ... }                  # Full report JSON — see Extraction/Macroextraction.md for schema
status: IMMUTABLE
```

---

### 12. OpenLoopNode

An unresolved psychological investigation — a question the user is actively working through, or a recurring theme that has not crystallized into a stable belief or pattern.

```yaml
node_type: OpenLoopNode
node_id: loop_2026_04_15_001
created_at: "2026-04-15T20:14:00Z"
valid_from: "2026-04-15T20:14:00Z"
loop_description: "Am I staying in this role because I genuinely find meaning in it, or because I'm avoiding the uncertainty of a transition?"
loop_category: CAREER_IDENTITY           # CAREER_IDENTITY | RELATIONSHIP | SELF_CONCEPT | VALUES | HEALTH | OTHER
provenance: AI_GENERATED                 # USER_GENERATED | AI_GENERATED | CO_CREATED
source_episode_id: ep_2026_04_15_002
linked_patterns:
  - pat_decision_saturation
  - pat_conflict_avoidance_007
linked_beliefs:
  - bel_introvert_001
resolution_status: OPEN                  # OPEN | RESOLVED | DISSOLVED
resolved_at: null
resolution_summary: null
last_referenced_at: "2026-06-01T06:00:00Z"
```

---

## Edge Types

| Edge Type | From | To | Reversible | Description |
|---|---|---|---|---|
| `contains` | `EpisodeNode` | `ObservationNode` / `EventNode` / `SessionNode` / `CausalChainNode` | No | An episode structurally contains its observations, events, and causal chains. Written once; never invalidated. |
| `chain_contains` | `CausalChainNode` | `CausalStepNode` | No | A causal chain contains its ordered steps. Written once; never invalidated. |
| `same_as` | `ObservationNode` / `PatternNode` | `PatternNode` (canonical) | Yes (via audit) | MERGE result. Links new node to canonical. Neither node is deleted. |
| `reinforces` | `ObservationNode` / `EventNode` | `PatternNode` / `BeliefNode` | Yes (via audit) | REINFORCE result. Adds evidential weight to existing node. |
| `evolved_from` | `PatternNode` v2 / `BeliefNode` v2 | `PatternNode` v1 / `BeliefNode` v1 | No (append-only) | EVOLVE result. The prior version is immutably preserved. The new version points backward. |
| `caused_by` | `PatternNode` / `BeliefNode` (new version) | `EventNode` / `SessionNode` | Yes (via audit) | Causal anchor for EVOLVE or BRANCH in the bipartite graph. |
| `branches_to` | `ObservationNode` / `EventNode` / `SessionNode` | `PatternNode` (new) / `BeliefNode` (new) | Yes (via audit) | BRANCH result. Documents provenance of the new independent node — a genuinely novel pattern or belief with no link to any existing candidate. |
| `contradicts` | `ContradictionNode` | `BeliefNode` (both sides) | Yes (via audit) | CONTRADICT result. Two edges per ContradictionNode — one to each belief. |
| `dialectic` | `BeliefNode` / `PatternNode` | `BeliefNode` / `PatternNode` | Yes (via audit) | DIALECTIC result. Links two simultaneously true but conflicting nodes. |
| `regulates` | `SessionNode` / `ObservationNode` | `PatternNode` | Yes (via audit) | REGULATE result. Marks when a user actively catches and interrupts a negative pattern. Bypasses EVOLVE confidence threshold. |
| `mentions` | `ObservationNode` / `EventNode` / `SessionNode` | `PersonEntityNode` | No | Created when an observation, event, or session references a named person. |
| `decided_by` | any Reconciliation edge above | `DecisionAuditNode` | N/A | Meta-edge linking every Reconciliation-produced edge to its audit record. |
| `analyzed_in` | `EpisodeNode` | `MacroextractionReportNode` | No | Documents which episodes a Macroextraction report drew on. |
| `alias_of` | `PersonEntityNode` (alias) | `PersonEntityNode` (canonical) | No | Cross-entry person merge. Alias node is preserved; canonical node is the traversal target. |
| `investigated_by` | `OpenLoopNode` | `EpisodeNode` | No | Links an open loop to each episode where the loop is explicitly addressed or referenced. |
| `closes` | `EpisodeNode` | `OpenLoopNode` | No | Written when an episode is identified as resolving an open loop. |
| `follows_from` | `EpisodeNode` | `EpisodeNode` | No | Links micro-segmented episodes from the same `(event_date, session_label)` to preserve intra-session narrative flow. |
| `adopted_as` | `ObservationNode` / `SessionNode` | `AdoptedPrincipleNode` | Yes (via audit) | Written when a session or observation applies, references, or reinforces an adopted principle. |
| `superseded_by` | `AdoptedPrincipleNode` (old) | `AdoptedPrincipleNode` (new) | No (append-only) | Written when the user adopts a refined or replacement version of a prior principle. |
| `failed_extraction` | `EpisodeNode` | `ObservationNode` | No | Written when an observation fails validation 3 times. The `ObservationNode` carries `status: EXTRACTION_FAILED` and is linked to its episode for HITL surfacing. |

**Reversibility note:** "Yes (via audit)" means the edge's `invalidated_at` timestamp is set (not deleted), and a new `DecisionAuditNode` with `action: ROLLBACK` is written.

### Edge Schemas for Key Reconciliation Edges

#### `same_as`
```json
{
  "edge_type": "same_as",
  "source_node_id": "pat_2026_06_11_slow_pace_new",
  "target_node_id": "pat_decision_saturation",
  "confidence": 0.91,
  "decision_id": "d_2026_06_11_001",
  "valid_from": "2025-01-18T10:34:00Z",
  "invalidated_at": null
}
```

#### `dialectic`
```json
{
  "edge_type": "dialectic",
  "source_node_id": "bel_introvert_001",
  "target_node_id": "bel_expressive_social_001",
  "tension_summary": "Both truths are simultaneously held: need for solitude and thriving in expressive social environments. Neither supersedes the other.",
  "confidence": 0.89,
  "decision_id": "d_2026_06_11_004",
  "valid_from": "2025-01-18T11:15:00Z",
  "invalidated_at": null
}
```

#### `regulates`
```json
{
  "edge_type": "regulates",
  "source_node_id": "obs_2026_06_11_012",
  "target_node_id": "pat_critic_brain_001",
  "regulation_summary": "User caught critic brain spiral mid-sentence and explicitly interrupted it before it escalated to avoidance behavior.",
  "confidence": 0.83,
  "decision_id": "d_2026_06_11_005",
  "valid_from": "2025-01-18T11:20:00Z",
  "invalidated_at": null
}
```

---

## Temporal Model

Every node and every edge carries timestamps that enable time-range queries and drive retrieval decay scoring.

### Node Timestamps

| Field | Present On | Meaning |
|---|---|---|
| `occurred_at` | `EpisodeNode`, `ObservationNode`, `EventNode`, `SessionNode` | Logical Event Time when the thought, event, or session actually happened |
| `created_at` | All nodes | Wall clock time when the node was written to the graph |
| `valid_from` | All nodes | Effective start of this node's validity (differs on EVOLVE — set to the date the evolved belief became active) |
| `last_reinforced_at` | `PatternNode`, `BeliefNode` | Timestamp of the most recent `reinforces` or `same_as` edge. Derives from the `occurred_at` of the reinforcing observation. Used in temporal decay scoring. |
| `version` | `PatternNode`, `BeliefNode` | Integer version counter. Starts at 1. |
| `previous_version_id` | `PatternNode`, `BeliefNode` | Node ID of the directly prior version. Null for v1. |

### Edge Timestamps

| Field | Present On | Meaning |
|---|---|---|
| `valid_from` | All edges | When the edge was created |
| `invalidated_at` | All edges | Null if active. Set to a timestamp when the edge is rolled back. |
| `decision_id` | All Reconciliation edges | Foreign key to the `DecisionAuditNode` that produced this edge |

### Temporal Decay

Temporal decay applies to `PatternNode` and `BeliefNode` instances during retrieval scoring. Nodes are **never deleted** due to age. They are downweighted.

| `last_reinforced_at` Age | Decay Multiplier |
|---|---|
| < 30 days | 1.0 (no decay) |
| 30 – 180 days | 0.85 |
| 180 – 365 days | 0.70 |
| > 365 days | 0.50 |

---

## Retrieval Score Formula

```
final_score = cosine_similarity(query_vector, node_vector)
            × signal_weight_multiplier
            × recency_weight(last_reinforced_at)
```

**Signal weight multipliers:**

| Signal Strength | Multiplier |
|---|---|
| `STANDARD` | 1.0 |
| `HIGH` | 1.5 |
| `CRITICAL` | 2.0 |

**Recency weight function:**

```python
def recency_weight(last_reinforced_at: datetime, now: datetime) -> float:
    age_days = (now - last_reinforced_at).days
    if age_days < 30:
        return 1.0
    elif age_days < 180:
        return 0.85
    elif age_days < 365:
        return 0.70
    else:
        return 0.50
```

---

## Version Chain Example

```yaml
# Version 1 — original belief (immutable, preserved)
- node_type: BeliefNode
  node_id: bel_solitude_decision_v1
  version: 1
  previous_version_id: null
  created_at: "2025-08-10T09:00:00Z"
  valid_from: "2025-08-10T09:00:00Z"
  belief_statement: "I can only make good decisions when I am alone and have extended uninterrupted time to think"
  status: SUPERSEDED
  last_reinforced_at: "2026-02-14T11:00:00Z"

# EVOLVE edge linking v2 back to v1
- edge_type: evolved_from
  source_node_id: bel_solitude_decision_v2
  target_node_id: bel_solitude_decision_v1
  valid_from: "2025-01-18T10:55:00Z"
  invalidated_at: null
  decision_id: d_2026_06_11_007

# DecisionAuditNode for the EVOLVE action
- node_type: DecisionAuditNode
  node_id: d_2026_06_11_007
  action: EVOLVE
  source_node_id: obs_2026_06_11_009
  target_node_id: bel_solitude_decision_v1
  edge_type_created: evolved_from
  confidence: 0.94
  delta_description: "User explicitly acknowledged that they made one of their best decisions this week during a chaotic team meeting — directly contradicting the prior belief about needing solitude. The belief has evolved to: 'I prefer solitude for reflection but can make high-quality decisions in structured group contexts when the stakes are clear.'"
  model_used: gemini-2.0-pro
  candidate_retrieval_source: SEMANTIC
  co_created_origin: false
  status: ACTIVE

# Version 2 — new evolved belief (immutable once written)
- node_type: BeliefNode
  node_id: bel_solitude_decision_v2
  version: 2
  previous_version_id: bel_solitude_decision_v1
  created_at: "2025-01-18T10:55:00Z"
  valid_from: "2025-01-18T10:55:00Z"
  belief_statement: "I prefer solitude for reflection but can make high-quality decisions in structured group contexts when the stakes are clear"
  version_delta: "Extended belief from 'solitude only' to include structured group contexts. Triggered by explicit counter-evidence in ep_2026_06_11_003."
  status: ACTIVE
  last_reinforced_at: "2025-01-18T10:55:00Z"
```

---

## Soft Delete / Erasure (DPDP/GDPR Compliance)

Because content nodes are append-only, standard deletion is not architecturally possible. Erasure is implemented via **anonymization** — content is replaced; structure is preserved.

### Erasure Procedure

```
DELETE /users/{user_id}/data
```

This triggers an asynchronous anonymization pass:

1. **Content node anonymization:** All `content`, `belief_statement`, `pattern_description`, `lesson_statement`, `loop_description`, `raw_evidence`, `episode_summary`, and `contradiction_summary` fields are replaced with `[ERASED: {iso_date}]`.

2. **PersonEntityNode anonymization:** `canonical_name` → `[ERASED_PERSON_{sha256_hash_8}]`. All `aliases` → `[ERASED_ALIAS]`.

3. **DecisionAuditNode anonymization:** `delta_description` and `hitl_resolution_user_choice` (where it contains text) → `[ERASED: {iso_date}]`.

4. **MacroextractionReportNode anonymization:** `report_content` JSON blob → `[ERASED: {iso_date}]`.

5. **Graph structure is preserved:** Node IDs, edge structure, timestamps, node types, signal strengths, and version chains are all retained.

6. **Embeddings:** All embedding vectors for the user's nodes are deleted (fully reconstructable from content, so content erasure alone is insufficient).

7. **Audit log:** A `DataErasureAuditRecord` is written to the **Operational DB** (SQLite/PostgreSQL). Contains no user content.

### DataErasureAuditRecord (Operational DB)

```yaml
table: data_erasure_audit
record:
  id: era_2026_07_01_001
  user_id_hash: "sha256:b3e..."         # hashed — no plaintext user identifier stored
  erased_at: "2026-07-01T14:22:00Z"
  nodes_anonymized: 847
  embeddings_deleted: 847
  entry_ids_affected:
    - entry_2026_06_11_raw
    - entry_2026_05_20_raw
  initiated_by: USER_REQUEST            # USER_REQUEST | ADMIN_REQUEST | AUTOMATED_RETENTION_POLICY
  status: COMPLETE                      # COMPLETE | IN_PROGRESS | FAILED
```

> ⚠️ **Irreversibility:** Anonymization is irreversible. The original content cannot be recovered after this procedure.

### Partial Erasure (Single Entry)

```
DELETE /users/{user_id}/entries/{entry_id}
```

Anonymizes all nodes whose `entry_id` or `episode_id` traces back to the specified entry. Same anonymization rules apply.

---

*See also: [HLDv2.md](../hld/HLDv2.md) for the complete system overview, [Extraction/Reconciliation.md](../Extraction/Reconciliation.md) for edge creation rules, [Extraction/Microextraction.md](../Extraction/Microextraction.md) for the ObservationNode type enum, [Extraction/Macroextraction.md](../Extraction/Macroextraction.md) for the full MacroextractionReportNode content schema.*
