# Knowledge Graph Schema

The knowledge graph is the persistent memory of the Smriti system. It stores every observation, pattern, belief, lesson, person entity, decision, and synthesis report as a typed node, with typed edges representing the relationships between them.

Two rules govern the graph at the implementation layer:

1. **Content nodes are immutable (append-only).** Once written, a node's `content` fields are never updated. Changes produce new versioned nodes with `evolved_from` edges.
2. **Only connections are modifiable — and only via the Decision Audit Trail.** Edges carry `invalidated_at` timestamps. An invalidated edge is a soft-delete: it is retained in the graph for audit purposes but excluded from active traversal.

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
created_at: "2026-06-11T10:30:00Z"
valid_from: "2026-06-11T10:30:00Z"
source_modality: VOICE_NOTE          # VOICE_NOTE | TEXT_ENTRY
entry_class: REFLECTION              # REFLECTION | RAW_CAPTURE
language_tags: ["en", "hi"]
episode_summary: "User reflects on a slow, deliberate decision-making approach when confronting a career pivot"
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
created_at: "2026-06-11T10:31:45Z"
valid_from: "2026-06-11T10:31:45Z"
type: BEHAVIORAL_PATTERN_OBSERVATION  # from closed enum (see Extraction/Architecture.md)
content: "User consistently defers major decisions until they have gathered significantly more information than peers deem necessary"
raw_evidence:
  - "I just can't pull the trigger until I feel like I've exhausted every angle"
  - "everyone else was ready to decide weeks ago"
signal_strength: HIGH               # STANDARD | HIGH | CRITICAL
sensitivity_tier: STANDARD          # STANDARD | ELEVATED | CRITICAL
person_refs: []
open_loop_ref: null
status: ACTIVE                      # ACTIVE | RAW_CAPTURE | EXTRACTION_FAILED | SUSPENDED
extraction_model: gemini-2.0-flash
extraction_attempt: 1               # increments on validation failure re-extract
```

---

### 3. PatternNode

A recurring behavioral or cognitive pattern that has been identified across multiple episodes. PatternNodes are versioned: EVOLVE actions create new versions.

```yaml
node_type: PatternNode
node_id: pat_slow_progressive_approach
version: 2
previous_version_id: pat_slow_progressive_approach_v1
created_at: "2026-01-15T08:00:00Z"
valid_from: "2026-06-11T10:34:00Z"   # valid_from updates on EVOLVE
last_reinforced_at: "2026-06-11T10:34:00Z"
pattern_name: "Deliberate Information Saturation Before Decision"
pattern_description: "User systematically over-collects information before committing to any significant decision, prioritizing certainty over speed. In v2, pattern extends to interpersonal confrontations, not just strategic decisions."
domain: COGNITIVE_STYLE             # COGNITIVE_STYLE | EMOTIONAL | BEHAVIORAL | RELATIONAL | CAREER | HEALTH
signal_strength: HIGH
sensitivity_tier: STANDARD
evidence_count: 7                   # count of ObservationNodes linked via reinforces or same_as edges
archetype_tags: ["high_conscientiousness", "risk_averse"]
is_canonical: true
status: ACTIVE                      # ACTIVE | SUPERSEDED | SUPPRESSED
```

---

### 4. BeliefNode

An underlying worldview rule — a first-person statement of how the user believes the world works, how they see themselves, or what they value. Versioned identically to PatternNode.

```yaml
node_type: BeliefNode
node_id: bel_introverted_001
version: 1
previous_version_id: null
created_at: "2025-11-03T14:22:00Z"
valid_from: "2025-11-03T14:22:00Z"
last_reinforced_at: "2026-03-17T09:10:00Z"
belief_statement: "I am an introvert who needs solitude to recharge after social interaction"
belief_source_summary: "Expressed explicitly in entry e_2025_11_03 and reinforced in 4 subsequent entries"
domain: SELF_CONCEPT
signal_strength: HIGH
sensitivity_tier: CRITICAL
evidence_count: 5
is_contradicted: true               # true if linked to an active ContradictionNode
contradiction_node_id: con_2026_06_11_001
version_delta: null                 # plain-language description of change (populated on EVOLVE)
status: ACTIVE
```

---

### 5. LessonNode

Extracted wisdom — a distilled takeaway that the user has explicitly or implicitly drawn from an experience. Unlike a belief, a lesson is epistemically bounded ("I learned that...") rather than a generalized worldview rule.

```yaml
node_type: LessonNode
node_id: les_2026_04_20_001
created_at: "2026-04-20T17:05:00Z"
valid_from: "2026-04-20T17:05:00Z"
lesson_statement: "Volunteering for high-visibility projects before I feel fully ready consistently accelerates my growth more than waiting for readiness"
evidence_episodes:
  - ep_2026_04_20_001
  - ep_2026_01_08_003
domain: CAREER
signal_strength: HIGH
sensitivity_tier: STANDARD
lesson_confidence: 0.84             # extraction-time confidence in lesson validity
status: ACTIVE
```

---

### 6. PersonEntityNode

A named person who appears across one or more journal entries. PersonEntityNodes are the target of `mentions` edges from ObservationNodes. Cross-entry coreference produces `alias_of` edges between variant PersonEntityNodes.

```yaml
node_type: PersonEntityNode
node_id: person_rahul_001
canonical_name: "Rahul"
aliases:
  - "Rax"
  - "R"
  - "my colleague Rahul"
first_mentioned_at: "2025-09-14T00:00:00Z"
last_mentioned_at: "2026-06-11T00:00:00Z"
mention_count: 12
relationship_to_user: COLLEAGUE     # COLLEAGUE | FRIEND | FAMILY | MANAGER | PARTNER | OTHER | UNKNOWN
relationship_sentiment_trend: NEUTRAL_TO_NEGATIVE   # aggregated across mentions
sensitivity_tier: ELEVATED          # PersonEntityNodes are always ELEVATED or CRITICAL
is_canonical: true
merged_from: []                     # list of node_ids that alias_of to this node
status: ACTIVE
```

---

### 7. DecisionAuditNode

A first-class node recording every Reconciliation action. Every edge in the graph is traceable to exactly one `DecisionAuditNode`. See [Reconciliation.md](../Extraction/Reconciliation.md) for full behavioral documentation.

```yaml
node_type: DecisionAuditNode
node_id: d_2026_06_11_001
created_at: "2026-06-11T10:34:17Z"
action: MERGE                       # MERGE | REINFORCE | EVOLVE | BRANCH | CONTRADICT | AMBIGUOUS
source_observation_id: obs_2026_06_11_004
target_node_id: pat_slow_progressive_approach
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

### 8. ContradictionNode

Represents two simultaneously-held, logically incompatible beliefs. Created by the CONTRADICT Reconciliation action. Persists until explicitly resolved.

```yaml
node_type: ContradictionNode
node_id: con_2026_06_11_001
created_at: "2026-06-11T11:02:00Z"
valid_from: "2026-06-11T11:02:00Z"
belief_a_id: bel_introverted_001
belief_b_id: bel_2026_06_11_expressive_social
contradiction_summary: "User holds simultaneous beliefs about being introverted and thriving in expressive, high-attention social environments"
decision_id: d_2026_06_11_003
resolution_status: UNRESOLVED       # UNRESOLVED | RESOLVED_EVOLVE | RESOLVED_USER | RESOLVED_MACRO
resolved_at: null
resolution_decision_id: null
```

---

### 9. MacroextractionReportNode

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
  - pat_slow_progressive_approach
  - pat_conflict_avoidance_007
beliefs_referenced:
  - bel_introverted_001
open_loops_status:
  - { open_loop_id: loop_2026_04_15_001, status: STILL_OPEN }
unresolved_contradictions:
  - con_2026_06_11_001
behavioral_delta_summary: "Conflict avoidance pattern showed reduced frequency this week; slow decision pattern remains stable"
archetype_shift_detected: false
model_used: gemini-2.0-pro
status: IMMUTABLE
```

---

### 10. OpenLoopNode

An unresolved psychological investigation — a question the user is actively working through, a commitment without resolution, or a recurring theme that hasn't crystallized into a stable belief or pattern.

```yaml
node_type: OpenLoopNode
node_id: loop_2026_04_15_001
created_at: "2026-04-15T20:14:00Z"
valid_from: "2026-04-15T20:14:00Z"
loop_description: "Am I staying in this role because I genuinely find meaning in it, or because I'm avoiding the uncertainty of a transition?"
loop_category: CAREER_IDENTITY      # CAREER_IDENTITY | RELATIONSHIP | SELF_CONCEPT | VALUES | HEALTH | OTHER
source_episode_id: ep_2026_04_15_002
linked_patterns:
  - pat_slow_progressive_approach
  - pat_conflict_avoidance_007
linked_beliefs:
  - bel_introverted_001
resolution_status: OPEN             # OPEN | RESOLVED | DISSOLVED
resolved_at: null
resolution_summary: null
last_referenced_at: "2026-06-01T06:00:00Z"
```

---

## Edge Types

| Edge Type | From | To | Reversible | Description |
|---|---|---|---|---|
| `contains` | `EpisodeNode` | `ObservationNode` | No | An episode structurally contains its observations. Written once; never invalidated. |
| `same_as` | `ObservationNode` / `PatternNode` | `PatternNode` (canonical) | Yes (via audit) | MERGE result. Links new node to canonical. Neither node is deleted. |
| `reinforces` | `ObservationNode` | `PatternNode` / `BeliefNode` | Yes (via audit) | REINFORCE result. Adds evidential weight to existing node. |
| `evolved_from` | `PatternNode` v2 / `BeliefNode` v2 | `PatternNode` v1 / `BeliefNode` v1 | No (append-only) | EVOLVE result. The prior version is immutably preserved. The new version points backward. |
| `branches_to` | `ObservationNode` | `PatternNode` (new) | Yes (via audit) | BRANCH result. Documents provenance of the new independent node. |
| `contradicts` | `ContradictionNode` | `BeliefNode` (both sides) | Yes (via audit) | CONTRADICT result. Two edges per ContradictionNode — one to each belief. |
| `mentions` | `ObservationNode` | `PersonEntityNode` | No | Created when an observation references a named person. Provenance link; not a Reconciliation product. |
| `decided_by` | any Reconciliation edge above | `DecisionAuditNode` | N/A | Meta-edge linking every Reconciliation-produced edge to its audit record. |
| `analyzed_in` | `EpisodeNode` | `MacroextractionReportNode` | No | Documents which episodes a Macroextraction report drew on. |
| `alias_of` | `PersonEntityNode` (alias) | `PersonEntityNode` (canonical) | No | Cross-entry person merge. Alias node is preserved; canonical node is the traversal target. |
| `investigated_by` | `OpenLoopNode` | `EpisodeNode` | No | Links an open loop to each episode where the loop is explicitly addressed or referenced. |
| `closes` | `EpisodeNode` | `OpenLoopNode` | No | Written when an episode is identified as resolving an open loop. |

**Reversibility note:** "Yes (via audit)" means the edge's `invalidated_at` timestamp is set (not deleted), and a new `DecisionAuditNode` with `action: ROLLBACK` is written. The edge record persists in the graph in its invalidated state.

---

## Temporal Model

Every node and every edge carries timestamps that enable time-range queries and drive retrieval decay scoring.

### Node Timestamps

| Field | Present On | Meaning |
|---|---|---|
| `created_at` | All nodes | Wall clock time when the node was written to the graph |
| `valid_from` | All nodes | Effective start of this node's validity (usually equals `created_at`; differs on EVOLVE where `valid_from` is the date the evolved belief became active) |
| `last_reinforced_at` | `PatternNode`, `BeliefNode` | Timestamp of the most recent `reinforces` or `same_as` edge pointing to this node. Used in temporal decay scoring. |
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

> ⚠️ `cosine_similarity` uses the node's stored embedding vector. CRITICAL-tier nodes are embedded with local models (`nomic-embed-text` or `mxbai-embed`) and must only be compared against query vectors produced by the same local model. Mixing embedding spaces invalidates the cosine similarity score.

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
  valid_from: "2026-06-11T10:55:00Z"
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
  created_at: "2026-06-11T10:55:00Z"
  valid_from: "2026-06-11T10:55:00Z"
  belief_statement: "I prefer solitude for reflection but can make high-quality decisions in structured group contexts when the stakes are clear"
  version_delta: "Extended belief from 'solitude only' to include structured group contexts. Triggered by explicit counter-evidence in ep_2026_06_11_003."
  status: ACTIVE
  last_reinforced_at: "2026-06-11T10:55:00Z"
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
