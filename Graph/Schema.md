# Knowledge Graph Schema

The knowledge graph is the persistent memory of the Lumen system. It stores every observation, pattern, belief, lesson, person entity, decision, and synthesis report as a typed node, with typed edges representing the relationships between them.

Two rules govern the graph at the implementation layer:

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
source_modality: VOICE_NOTE          # VOICE_NOTE | TEXT_ENTRY
entry_class: REFLECTION              # REFLECTION | RAW_CAPTURE
language_tags: ["en"]                # always English after Stage 0 normalization
episode_summary: "User reflects on a slow, deliberate decision-making approach when confronting a career pivot"
historical_era: "a major entrance exam_PREP"           # Optional. Anchors the episode to a specific past life chapter if explicitly referenced
episode_index: 1                     # ordinal position within the entry
total_episodes_in_entry: 2
coreference_map_id: coref_2026_06_11_001
reconciliation_status: COMPLETE      # COMPLETE | SUSPENDED | PENDING_RERECONCILIATION
raw_text_hash: "sha256:a3f..."       # hash of cleaned episode text for deduplication
```

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
type: BEHAVIORAL_PATTERN_OBSERVATION  # from closed enum (see Extraction/Architecture.md)
content: "User consistently defers major decisions until they have gathered significantly more information than peers deem necessary"
raw_evidence:
  - "I just can't pull the trigger until I feel like I've exhausted every angle"
  - "everyone else was ready to decide weeks ago"
signal_strength: HIGH               # STANDARD | HIGH | CRITICAL
provenance: USER_GENERATED          # USER_GENERATED | AI_GENERATED | CO_CREATED
person_refs: []
open_loop_ref: null
status: ACTIVE                      # ACTIVE | RAW_CAPTURE | EXTRACTION_FAILED | SUSPENDED
extraction_model: gemini-2.0-flash
extraction_attempt: 1               # increments on validation failure re-extract
```

---

### 3. EventNode

A discrete, objective occurrence that anchors psychological shifts. Events are concrete actions or occurrences (e.g., "Ate alone at cafe", "Received promotion", "Moved to the user's work city"). They serve as the causal bridge between beliefs in the bipartite graph schema.

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
signal_strength: HIGH
status: ACTIVE
```

---

### 3.1. SessionNode

A conversational session or period of internal realization that produces a cognitive shift without an external physical event. Used to anchor EVOLVE or CONTRADICT actions driven by internal dialogue or AI-facilitated breakthroughs.

```yaml
node_type: SessionNode
node_id: sess_example_001
episode_id: ep_example_002
occurred_at: "2025-02-08T20:11:00Z"      # Logical Event Date
created_at: "2025-02-08T21:00:00Z"       # System Extraction Date
valid_from: "2025-02-08T21:00:00Z"
session_summary: "Deep conversational breakthrough resolving an identity fusion conflict through dialogue."
participant_entities:
  - user
  - ai_facilitator
signal_strength: HIGH
status: ACTIVE
```

---

### 4. PatternNode

A recurring behavioral or cognitive pattern that has been identified across multiple episodes. PatternNodes are versioned: EVOLVE actions create new versions. Must be linked to an EventNode when evolving.

```yaml
node_type: PatternNode
node_id: pat_decision_saturation
version: 2
previous_version_id: pat_decision_saturation_v1
created_at: "2024-08-01T08:00:00Z"
valid_from: "2025-01-18T10:34:00Z"   # valid_from updates on EVOLVE
last_reinforced_at: "2025-01-18T10:34:00Z"
pattern_name: "Deliberate Information Saturation Before Decision"
pattern_description: "User systematically over-collects information before committing to any significant decision, prioritizing certainty over speed. In v2, pattern extends to interpersonal confrontations, not just strategic decisions."
domain: COGNITIVE_STYLE             # COGNITIVE_STYLE | EMOTIONAL | BEHAVIORAL | RELATIONAL | CAREER | HEALTH
signal_strength: HIGH
provenance: USER_GENERATED
evidence_count: 7                   # count of ObservationNodes linked via reinforces or same_as edges
archetype_tags: ["high_conscientiousness", "risk_averse"]
is_canonical: true
status: ACTIVE                      # ACTIVE | SUPERSEDED | SUPPRESSED
```

---

### 5. BeliefNode

An underlying worldview rule — a first-person statement of how the user believes the world works, how they see themselves, or what they value. Versioned identically to PatternNode. Must be linked to an EventNode when evolving.

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
signal_strength: HIGH
provenance: USER_GENERATED
evidence_count: 5
is_contradicted: true               # true if linked to an active ContradictionNode
contradiction_node_id: con_example_001
version_delta: null                 # plain-language description of change (populated on EVOLVE)
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
signal_strength: HIGH
lesson_confidence: 0.84             # extraction-time confidence in lesson validity
status: ACTIVE
```

---

### 7. AdoptedPrincipleNode

A prescriptive commitment the user has explicitly chosen to follow — a rule, framework, or operating principle they intend to practice. Unlike a `BeliefNode` (which is *descriptive* — "I tend to do X"), an `AdoptedPrincipleNode` is *prescriptive* — "I am committing to doing Y."

This distinction enables the query: *"At a specific point in time, what principles was I actively trying to follow?"* and supports fetching the logs around that period.

```yaml
node_type: AdoptedPrincipleNode
node_id: prin_work_relationship_001
created_at: "2025-01-15T10:00:00Z"
valid_from: "2025-01-15T10:00:00Z"        # when the user first articulated this commitment
adopted_at: "2025-01-15T10:00:00Z"        # same as valid_from on first adoption
principle_statement: "Before every work session, perform an autotelic shift: ask why this work is worth doing in itself, and what my current relationship with it is."
principle_name: "Autotelic Shift + Relationship Check"
domain: PRODUCTIVITY                        # PRODUCTIVITY | HEALTH | RELATIONAL | COGNITIVE | IDENTITY
lifecycle_state: TRYING                     # TRYING | INTERNALIZED | SUSPENDED | ABANDONED
lifecycle_updated_at: "2025-01-15T10:00:00Z"
lifecycle_history:                          # append-only log of state transitions
  - state: TRYING
    at: "2025-01-15T10:00:00Z"
    reason: "User explicitly committed to this in a recent journal entry"
source_session_id: "session-id-example"
provenance: CO_CREATED                      # USER_GENERATED | CO_CREATED (adopted from AI framing)
parent_belief_ids: []                       # BeliefNodes or ConceptualReframes this principle operationalizes
supersedes_id: null                         # ID of a prior AdoptedPrincipleNode this replaces
last_referenced_at: "2025-01-15T10:00:00Z" # updated when user mentions/applies this principle
evidence_count: 1
status: ACTIVE
```

#### Lifecycle States

| State | Meaning | Transition Triggers |
|---|---|---|
| `TRYING` | User has committed to this principle but hasn't internalized it. Following it requires conscious effort. | Initial adoption |
| `INTERNALIZED` | User describes applying it naturally, without deliberate effort. Appears in logs as implicit behavior. | Macroextraction or explicit journal statement |
| `SUSPENDED` | User has acknowledged they stopped following it temporarily (not abandonment). | Journal entry saying "I've been neglecting this" |
| `ABANDONED` | User explicitly says this principle didn't work or they've moved on. | EVOLVE action on the node, or explicit journal disavowal |

#### Query Pattern — Principles Active at Time T

```cypher
-- "What principles was I following in early 2025?"
MATCH (p:AdoptedPrincipleNode)
WHERE p.valid_from <= "2026-06-30"
  AND (p.lifecycle_state IN ["TRYING", "INTERNALIZED"]
       OR p.lifecycle_updated_at > "2026-06-01")
RETURN p.principle_name, p.lifecycle_state, p.adopted_at
ORDER BY p.adopted_at

-- "Fetch episodes from around the same period"
MATCH (e:EpisodeNode)
WHERE e.occurred_at >= "2026-06-01" AND e.occurred_at <= "2026-06-30"
RETURN e.episode_summary, e.occurred_at
ORDER BY e.occurred_at

-- "Which episodes directly reference this principle?"
MATCH (p:AdoptedPrincipleNode {node_id: "prin_work_relationship_001"})
      <-[:adopted_as]-(obs:ObservationNode)
      <-[:contains]-(ep:EpisodeNode)
RETURN ep.episode_summary, ep.occurred_at, obs.content
ORDER BY ep.occurred_at
```

The third query is the most powerful: traverse from the principle backward through the observations that reinforced, suspended, or abandoned it — showing you the full life of that principle through your own logs.

---

### 8. PersonEntityNode

A named person who appears across one or more journal entries. PersonEntityNodes are the target of `mentions` edges from ObservationNodes. Cross-entry coreference produces `alias_of` edges between variant PersonEntityNodes.

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
relationship_to_user: COLLEAGUE     # COLLEAGUE | FRIEND | FAMILY | MANAGER | PARTNER | OTHER | UNKNOWN
relationship_sentiment_trend: NEUTRAL_TO_NEGATIVE   # aggregated across mentions
is_canonical: true
merged_from: []                     # list of node_ids that alias_of to this node
status: ACTIVE
```

---

### 8. DecisionAuditNode

A first-class node recording every Reconciliation action. Every edge in the graph is traceable to exactly one `DecisionAuditNode`. See [Reconciliation.md](../Extraction/Reconciliation.md) for full behavioral documentation.

```yaml
node_type: DecisionAuditNode
node_id: d_2026_06_11_001
created_at: "2025-01-18T10:34:17Z"
action: MERGE                       # MERGE | REINFORCE | EVOLVE | BRANCH | CONTRADICT | AMBIGUOUS
source_observation_id: obs_2026_06_11_004
target_node_id: pat_decision_saturation
edge_type_created: same_as
edge_id: edge_2026_06_11_009
confidence: 0.91
confidence_runner_up: 0.83
runner_up_action: REINFORCE
delta_description: null             # required and non-null only for action == EVOLVE
model_used: gemini-2.0-flash
routing_tier: STANDARD
hitl_resolved: false
hitl_resolution_timestamp: null
hitl_resolution_user_choice: null   # "ACTION_A" | "ACTION_B" | "CREATE_NEW" | "AUTO_BRANCH_AFTER_SNOOZE"
rollback_pointer:
  edge_to_invalidate: edge_2026_06_11_009
  nodes_to_requeue:
    - obs_2026_06_11_004
status: ACTIVE                      # ACTIVE | ROLLED_BACK
```

---

### 9. ContradictionNode

Represents two simultaneously-held, logically incompatible beliefs. Created by the CONTRADICT Reconciliation action. Persists until explicitly resolved.

```yaml
node_type: ContradictionNode
node_id: con_example_001
created_at: "2025-01-18T11:02:00Z"
valid_from: "2025-01-18T11:02:00Z"
belief_a_id: bel_introvert_001
belief_b_id: bel_2026_06_11_expressive_social
contradiction_summary: "User holds simultaneous beliefs about being introverted and thriving in expressive, high-attention social environments"
decision_id: d_2026_06_11_003
resolution_status: UNRESOLVED       # UNRESOLVED | RESOLVED_EVOLVE | RESOLVED_USER | RESOLVED_MACRO
resolved_at: null
resolution_decision_id: null
```

---

### 10. MacroextractionReportNode

An immutable synthesis report produced by a Periodic Intelligence (Macroextraction) job. Covers a defined time window and scope.

```yaml
node_type: MacroextractionReportNode
node_id: macro_2026_06_01_weekly
created_at: "2026-06-01T06:00:00Z"
report_type: WEEKLY                 # WEEKLY | MONTHLY | QUARTERLY
period_start: "2026-05-25T00:00:00Z"
period_end: "2026-06-01T00:00:00Z"
episodes_analyzed: 14
patterns_referenced:
  - pat_decision_saturation
  - pat_conflict_avoidance_007
beliefs_referenced:
  - bel_introvert_001
open_loops_status:
  - { open_loop_id: loop_2026_04_15_001, status: STILL_OPEN }
unresolved_contradictions:
  - con_example_001
behavioral_delta_summary: "Conflict avoidance pattern showed reduced frequency this week; slow decision pattern remains stable"
archetype_shift_detected: false
model_used: gemini-2.0-pro
status: IMMUTABLE
```

---

### 11. OpenLoopNode

An unresolved psychological investigation — a question the user is actively working through, a commitment without resolution, or a recurring theme that hasn't crystallized into a stable belief or pattern. This node can be explicitly stated by the user, or automatically generated by the AI if a conversational session ends with a profound, unanswered question (`provenance: AI_GENERATED`).

```yaml
node_type: OpenLoopNode
node_id: loop_2026_04_15_001
created_at: "2026-04-15T20:14:00Z"
valid_from: "2026-04-15T20:14:00Z"
loop_description: "Am I staying in this role because I genuinely find meaning in it, or because I'm avoiding the uncertainty of a transition?"
loop_category: CAREER_IDENTITY      # CAREER_IDENTITY | RELATIONSHIP | SELF_CONCEPT | VALUES | HEALTH | OTHER
provenance: AI_GENERATED            # USER_GENERATED | AI_GENERATED | CO_CREATED
source_episode_id: ep_2026_04_15_002
linked_patterns:
  - pat_decision_saturation
  - pat_conflict_avoidance_007
linked_beliefs:
  - bel_introvert_001
resolution_status: OPEN             # OPEN | RESOLVED | DISSOLVED
resolved_at: null
resolution_summary: null
last_referenced_at: "2026-06-01T06:00:00Z"
```

---

## Edge Types

| Edge Type | From | To | Reversible | Description |
|---|---|---|---|---|
| `contains` | `EpisodeNode` | `ObservationNode` / `EventNode` / `SessionNode` | No | An episode structurally contains its observations and events. Written once; never invalidated. |
| `same_as` | `ObservationNode` / `PatternNode` | `PatternNode` (canonical) | Yes (via audit) | MERGE result. Links new node to canonical. Neither node is deleted. |
| `reinforces` | `ObservationNode` / `EventNode` | `PatternNode` / `BeliefNode` | Yes (via audit) | REINFORCE result. Adds evidential weight to existing node. |
| `evolved_from` | `PatternNode` v2 / `BeliefNode` v2 | `PatternNode` v1 / `BeliefNode` v1 | No (append-only) | EVOLVE result. The prior version is immutably preserved. The new version points backward. |
| `caused_by` | `PatternNode` / `BeliefNode` (new version) | `EventNode` / `SessionNode` | Yes (via audit) | Establishes the causal anchor for an EVOLVE or BRANCH action in the bipartite graph. |
| `branches_to` | `ObservationNode` / `EventNode` / `SessionNode` | `PatternNode` (new) | Yes (via audit) | BRANCH result. Documents provenance of the new independent node. |
| `contradicts` | `ContradictionNode` | `BeliefNode` (both sides) | Yes (via audit) | CONTRADICT result. Two edges per ContradictionNode — one to each belief. |
| `dialectic` | `BeliefNode` / `PatternNode` | `BeliefNode` / `PatternNode` | Yes (via audit) | DIALECTIC result. Links two simultaneously true but conflicting nodes. Represents psychological tension/paradox. |
| `mentions` | `ObservationNode` / `EventNode` / `SessionNode` | `PersonEntityNode` | No | Created when an observation, event, or session references a named person. Provenance link; not a Reconciliation product. |
| `decided_by` | any Reconciliation edge above | `DecisionAuditNode` | N/A | Meta-edge linking every Reconciliation-produced edge to its audit record. |
| `analyzed_in` | `EpisodeNode` | `MacroextractionReportNode` | No | Documents which episodes a Macroextraction report drew on. |
| `alias_of` | `PersonEntityNode` (alias) | `PersonEntityNode` (canonical) | No | Cross-entry person merge. Alias node is preserved; canonical node is the traversal target. |
| `investigated_by` | `OpenLoopNode` | `EpisodeNode` | No | Links an open loop to each episode where the loop is explicitly addressed or referenced. |
| `closes` | `EpisodeNode` | `OpenLoopNode` | No | Written when an episode is identified as resolving an open loop. |
| `regulates` | `SessionNode` / `ObservationNode` | `PatternNode` | Yes (via audit) | Marks when a user actively catches and interrupts a negative pattern. Bypasses EVOLVE penalty. |
| `follows_from` | `EpisodeNode` | `EpisodeNode` | No | Links micro-segmented episodes extracted from the same dialogue session to preserve causal conversational flow. |
| `adopted_as` | `ObservationNode` / `SessionNode` | `AdoptedPrincipleNode` | Yes (via audit) | Written when a session or observation is identified as applying, referencing, or reinforcing an adopted principle. Enables traversal from principle to all relevant logs. |
| `superseded_by` | `AdoptedPrincipleNode` (old) | `AdoptedPrincipleNode` (new) | No (append-only) | Written when the user adopts a refined or replacement version of a prior principle. Old node moves to `ABANDONED`; the supersession edge preserves the lineage. |

**Reversibility note:** "Yes (via audit)" means the edge's `invalidated_at` timestamp is set (not deleted), and a new `DecisionAuditNode` with `action: ROLLBACK` is written. The edge record persists in the graph in its invalidated state.

---

## Temporal Model

Every node and every edge carries timestamps that enable time-range queries and drive retrieval decay scoring.

### Node Timestamps

| Field | Present On | Meaning |
|---|---|---|
| `occurred_at` | `EpisodeNode`, `ObservationNode`, `EventNode`, `SessionNode` | Logical Event Time when the thought, event, or session actually happened, independent of system ingestion time |
| `created_at` | All nodes | Wall clock time when the node was written to the graph |
| `valid_from` | All nodes | Effective start of this node's validity (usually equals `created_at`; differs on EVOLVE where `valid_from` is the date the evolved belief became active) |
| `last_reinforced_at` | `PatternNode`, `BeliefNode` | Timestamp of the most recent `reinforces` or `same_as` edge pointing to this node. Derives from the `occurred_at` of the reinforcing observation, not `created_at`. Used in temporal decay scoring. |
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

Nodes with `last_reinforced_at > 365 days` remain fully queryable via explicit time-range queries (`?time_range=2024-01-01:2025-01-01`) and are not affected by decay in that context.

---

## Retrieval Score Formula

The retrieval score for a candidate node during Semantic Candidate Retrieval is computed as:

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

> ⚠️ `cosine_similarity` uses the node's stored embedding vector. CRITICAL-tier nodes are embedded with their configured High-Security Embedding Provider and must only be compared against query vectors produced by the same provider. Mixing embedding spaces invalidates the cosine similarity score.

---

## Version Chain Example

The following YAML illustrates a BeliefNode EVOLVE chain. The user originally believed they needed solitude to make good decisions; after a significant experience, this evolved to a more nuanced belief about environmental flexibility.

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
  source_observation_id: obs_2026_06_11_009
  target_node_id: bel_solitude_decision_v1
  edge_type_created: evolved_from
  confidence: 0.94
  delta_description: "User explicitly acknowledged that they made one of their best decisions this week during a chaotic team meeting — directly contradicting the prior belief about needing solitude. The belief has evolved to: 'I prefer solitude for reflection but can make high-quality decisions in structured group contexts when the stakes are clear.'"
  model_used: gemini-2.0-pro
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

Because content nodes are append-only, standard deletion is not architecturally possible. Erasure under data protection regulations (India's DPDP Act, GDPR) is implemented via **anonymization** — content is replaced; structure is preserved.

### Erasure Procedure

```
DELETE /users/{user_id}/data
```

This triggers an asynchronous anonymization pass over all nodes belonging to the user:

1. **Content node anonymization:** All `content`, `belief_statement`, `pattern_description`, `lesson_statement`, `loop_description`, `raw_evidence`, `episode_summary`, and `contradiction_summary` fields are replaced with `[ERASED: {iso_date}]`.

2. **PersonEntityNode anonymization:** The `canonical_name` field is replaced with `[ERASED_PERSON_{sha256_hash_of_name_truncated_8}]`. All `aliases` entries are replaced with `[ERASED_ALIAS]`.

3. **DecisionAuditNode anonymization:** `delta_description` and `hitl_resolution_user_choice` (where it contains text) are replaced with `[ERASED: {iso_date}]`.

4. **MacroextractionReportNode anonymization:** `behavioral_delta_summary` and all narrative fields are replaced with `[ERASED: {iso_date}]`.

5. **Graph structure is preserved:** Node IDs, edge structure, timestamps, node types, signal strengths, sensitivity tiers, and version chains are all retained. The graph topology is preserved for system integrity auditing.

6. **Embeddings:** All embedding vectors stored for the user's nodes are deleted (not anonymized — vectors are fully reconstructable from content, so content erasure without vector deletion would be incomplete).

7. **Audit log:** A `DataErasureAuditRecord` is written to the system audit log (outside the user's graph) recording the user ID hash, the erasure timestamp, and the count of nodes anonymized. This record itself contains no user content.

> ⚠️ **Irreversibility:** Anonymization is irreversible. The original content cannot be recovered from the graph after this procedure. If the user has a local export (via `GET /users/{id}/export`), that export is not affected by the anonymization pass and must be separately destroyed by the user.

### Partial Erasure (Single Entry)

```
DELETE /users/{user_id}/entries/{entry_id}
```

Anonymizes all nodes whose `entry_id` or `episode_id` traces back to the specified entry. Same anonymization rules apply. Graph structure is preserved. Edges from non-erased nodes to erased nodes are retained; they point to anonymized nodes.

---

*See also: [HLDv2.md](../hld/HLDv2.md) for the complete system overview, [Extraction/Reconciliation.md](../Extraction/Reconciliation.md) for edge creation rules, [Extraction/Architecture.md](../Extraction/Architecture.md) for the ObservationNode type enum.*
