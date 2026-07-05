# Macroextraction Schema

Macroextraction is the *periodic* layer of insight generation. Where Microextraction asks **"What happened today?"**, Macroextraction asks **"What keeps happening?"**

Many of the most important patterns in a person's life are invisible in any single entry — they only emerge across months. Macroextraction is what surfaces those cross-entry truths.

---

## When Does Macroextraction Run?

Macroextraction is **scheduled**, not triggered by individual entries. It runs:
* **Shadow (48h):** An asynchronous job scanning only `DecisionAuditNodes` over the last 48 hours to detect immediate micro-shifts (dense `BRANCH` or `CONTRADICT` clusters).
* **Weekly:** A lightweight pass over the last 7 days.
* **Monthly:** A full pass over the last 30 days (the primary report).
* **Quarterly:** A deep pass over the last 90 days — also triggers the re-embedding job (see `Architecture.md`).

Each run produces a **Macroextraction Report** node in the graph, linked to the episodes it analyzed. Reports are immutable once written (append-only, same as episodes and beliefs).

---

## The Macroextraction Schema

### Shadow Micro-Shift Alerts (48h Window)
```yaml
shadow_micro_shift:
  # Generated asynchronously to provide near-real-time context to the chat interface.
  # Triggers when a sudden density of BRANCH or CONTRADICT actions occurs in a 48h window.
  
  detected: true
  shift_type: "Sudden behavior anomaly"
  trigger_nodes:
    - d_2026_06_20_001
    - d_2026_06_20_002
  summary: "User successfully engaged in multiple independent outings (Cafe, Shopping) today, branching away from historical fear patterns."
```

---

### Aggregation Window
```yaml
window_start: "2026-05-01"
window_end:   "2026-05-31"
window_type:  "monthly"          # weekly | monthly | quarterly
episodes_analyzed: 28            # count of episodes in this window
```

---

### Pattern Analytics
```yaml
top_patterns:
  # The top N patterns (by frequency) that fired in this window.
  # Each entry links to the pattern node ID and its episode count.
  - pattern_id: "pat_social_comparison_001"
    label: "Seeking validation through others' perception"
    episode_count: 6
    first_seen: "2026-05-04"
    last_seen: "2026-05-29"

pattern_frequency:
  # A ranked list of all patterns with their frequency score.
  # Frequency = (episode_count / total_episodes_in_window) × 100
  # This normalizes across windows of different lengths.
  - pattern_id: "pat_example_001"
    frequency_pct: 21.4

emerging_patterns:
  # Patterns that appear for the FIRST TIME in this window.
  # These are newly branched nodes with no prior history.
  - pattern_id: "pat_new_002"
    label: "Comfort enabling authenticity in group settings"
    first_episode: "ep_2026_05_18"

disappearing_patterns:
  # Patterns that were frequent in the previous window but
  # did NOT fire at all in this window. Signals possible resolution.
  - pattern_id: "pat_old_003"
    label: "Procrastination under task ambiguity"
    last_seen_window: "2026-04"
    previous_frequency_pct: 18.0
```

---

### Belief & Wisdom Tracking
```yaml
belief_changes:
  # Beliefs that received an EVOLVE decision in this window.
  # Links old version and new version for diff viewing.
  - belief_id: "bel_001"
    old_version: "v1"
    old_content: "Sleeping is the only solution to low-energy states."
    new_version: "v2"
    new_content: "Low-energy states can be navigated with slow, progressive re-engagement."
    evolved_on: "2024-12-05"

repeated_lessons:
  # Lessons that have appeared in 3+ episodes across the window.
  # These are the highest-confidence wisdom nodes — proven repeatedly.
  - lesson_id: "les_001"
    content: "Consistency over intensity — environment matters more than will."
    appearance_count: 4

ignored_lessons:
  # Lessons extracted in previous windows that have NOT been reinforced
  # or acted upon. These are candidates for surfacing in a reflection prompt.
  - lesson_id: "les_old_004"
    content: "Filter before asking — not every question needs to be spoken."
    last_seen: "2026-04-22"
    days_since_last_seen: 21
```

---

### Growth & Struggle Mapping
```yaml
biggest_growth_area:
  # The single pattern or belief that shows the most positive delta
  # in this window (e.g., frequency dropping for a negative pattern,
  # or a positive new belief being reinforced multiple times).
  pattern_or_belief_id: "pat_007"
  label: "Handling low-energy states with self-compassion"
  evidence: "Pattern fired 3x this month; EVOLVE decision on 2024-12-05 shows resolution mechanism."

biggest_struggle:
  # The single pattern that fired most frequently AND caused the most
  # negative emotional downstream effects (measured by linked EMOTION
  # observations tagged as negative valence).
  pattern_id: "pat_example_001"
  label: "Social performance anxiety in formal group settings"
  episode_count: 5
  avg_negative_emotion_intensity: 0.72   # 0.0 to 1.0 scale
```

---

### Relational & Environmental Insights
```yaml
key_relational_dynamics:
  # Summary of significant RELATIONAL_DYNAMIC observations in this window.
  # Tracks which relationships are growing, straining, or shifting.
  - person_ref: "Alex (mentor)"
    dynamic_summary: "First genuine mentorship received; high gratitude signal; relationship strengthening."
    observation_count: 3

key_environmental_dependencies:
  # Summary of ENVIRONMENTAL_DEPENDENCY observations.
  # Reveals which environments the person is dependent on for key behaviors.
  - environment: "Office / structured workspace"
    dependency: "Focus and deep work — home environment lacks forcing function."
    confidence: "high"
  - environment: "Gym"
    dependency: "Physical exercise consistency — home workouts regularly abandoned."
    confidence: "high"
```

---

### Open Items
```yaml
unresolved_open_loops:
  # All OPEN_LOOP observations from this window that have not been
  # closed by a subsequent LESSON, PERSPECTIVE_SHIFT, or BELIEF entry.
  - open_loop_id: "ol_001"
    content: "Why do I hesitate to ask questions in public interaction sessions?"
    first_raised: "2024-12-05"
    days_open: 14

pending_hitl_decisions:
  # Count and oldest item in the HITL Review Queue.
  # If this number is high, the system should surface a prompt to clear the queue.
  count: 3
  oldest_item_days: 5
```

---

### High-Signal Observation Highlights
```yaml
high_signal_observations:
  # All observations with extraction_signal_strength: HIGH or CRITICAL in this window.
  # These are always included in the Macroextraction report regardless of frequency.
  # Sensitivity filter applies: CRITICAL-tier content is count-only, not quoted.
  - observation_id: obs_example_001
    type: SUPPRESSED_EMOTION_SURFACING
    signal_strength: HIGH
    summary: "Involuntary emotional response while articulating gratitude — unprocessed depth flagged."
    episode_id: ep_example_004

motif_of_unprocessed_depth:
  # Patterns derived exclusively from SUPPRESSED_EMOTION_SURFACING observations.
  # Reveals which recurring themes the person has not yet consciously integrated.
  - pattern_id: pat_mentorship_example
    label: "Need for one-on-one mentorship / being seen by a senior"
    suppressed_surfacing_count: 2
```

---

### Person Entity Relationship Arcs
```yaml
relationship_arcs:
  # For each Person Entity that appeared in 3+ episodes this window,
  # summarize how the RELATIONAL_DYNAMIC observations have changed across the window.
  # Only includes ELEVATED and STANDARD tier person entities (CRITICAL excluded).
  - person_id: person_alex_001
    canonical_name: "Alex"
    episodes_in_window: 4
    arc_summary: "Relationship deepened from 'professional mentor' to 'first person who took initiative for me' — gratitude intensity increasing."
    dominant_observation_types: [RELATIONAL_DYNAMIC, GRATITUDE_APPRECIATION]
    arc_direction: "strengthening"   # strengthening | stable | straining | fading
```

---

### Biographical Gap Tracking
```yaml
biographical_gaps_raised:
  # All BIOGRAPHICAL_GAP observations logged in this window.
  # Tracks whether a gap is still present, narrowing, or closed.
  - gap_id: bgap_001
    content: "No one-on-one mentorship figure across school and college life."
    first_raised: "2025-01-20"
    status: "narrowing"   # present | narrowing | closed
    closing_evidence: "Alex relationship identified as first instance of mentorship."
```

---

### Temporal Decay & Pattern Aging
```yaml
pattern_aging:
  # Patterns are NOT deleted when they age. They are downweighted in retrieval.
  # This section reports patterns that have crossed a decay threshold.
  
  cooling_patterns:
    # Patterns with last_reinforced_at > 180 days (retrieval weight: 0.85x baseline)
    - pattern_id: "pat_003"
      label: "Procrastination under task ambiguity"
      last_reinforced: "2025-12-01"
      days_since_last_seen: 194
      current_weight_multiplier: 0.85
  
  dormant_patterns:
    # Patterns with last_reinforced_at > 365 days (retrieval weight: 0.5x baseline)
    # These are flagged for re-interrogation: has this pattern resolved, or just gone unlogged?
    - pattern_id: "pat_old_007"
      label: "Identity tied to academic outcomes"
      last_reinforced: "2025-01-15"
      days_since_last_seen: 518
      re_interrogation_prompt: "This pattern hasn't appeared in over a year. Has it resolved, or has it gone unlogged?"
```

---

### Archetype Shift Detection
```yaml
archetype_shift:
  # An Archetype Shift is detected when 5+ distinct patterns all shift in the
  # same directional trend within a 90-day window. It signals an identity-level
  # phase transition, not just individual pattern changes.
  
  detected: true
  shift_label: "Approval-Seeking → Internal-Reference Orientation"
  window: "2026-03-01 to 2026-06-01"
  contributing_patterns:
    - pattern_id: "pat_social_comparison_001"
      trend: "frequency_decreasing"       # fired 6x in Q1, 2x in Q2
    - pattern_id: "pat_social_performance_002"
      trend: "intensity_decreasing"        # avg_negative_emotion_intensity: 0.72 → 0.41
    - pattern_id: "pat_self_acceptance_003"
      trend: "frequency_increasing"        # newly emerging pattern
    - pattern_id: "pat_showing_off_004"
      trend: "awareness_increasing"        # same frequency, but METACOGNITIVE_INTERRUPT count +3
    - pattern_id: "pat_external_validation_005"
      trend: "frequency_decreasing"
  evidence_summary: "Across 12 micro-changes over 90 days, the user's reference frame has been shifting from external audiences toward internal standards."
```

---

### Contradiction Tracking
```yaml
active_contradictions:
  # CONTRADICTION nodes created in this window.
  # These are not errors — they represent genuine psychological tension that has not resolved.
  # Do NOT surface CRITICAL-tier contradictions in automated reports.
  
  - contradiction_id: "con_001"
    belief_a: "I am fundamentally introverted"
    belief_b: "I thrive and feel most alive in loud, expressive social environments"
    first_detected: "2026-05-22"
    still_active: true
    days_open: 22
    reflection_prompt: "Both of these beliefs appeared in your entries this month. Which one is describing your preference, and which is describing your fear of the other?"
```

---

### Emotional Valence Time-Series
```yaml
emotional_valence:
  # Derived from EMOTION, SOMATIC_STATE, and SUPPRESSED_EMOTION_SURFACING observations.
  # No manual mood input required — derived entirely from existing observation data.
  # valence: -1.0 (most negative) to +1.0 (most positive)
  # arousal: 0.0 (deactivated) to 1.0 (highly activated)
  
  weekly_trend:
    - week: "2026-05-05"
      avg_valence: -0.3
      avg_arousal: 0.6
    - week: "2026-05-12"
      avg_valence: -0.5
      avg_arousal: 0.4
    - week: "2026-05-19"
      avg_valence: +0.1
      avg_arousal: 0.7
    - week: "2026-05-26"
      avg_valence: +0.3
      avg_arousal: 0.8
  
  drift_alert:
    # Triggered when avg_valence drops below -0.4 for 3+ consecutive weeks.
    triggered: false
    # If triggered: surfaces a prompt asking the user to reflect on the sustained negative trend.
```

---

### Proof Chains
```yaml
proof_chains:
  # Generated when a lesson or pattern has accumulated 10+ episode instances across all time.
  # A Proof Chain is a curated chronological narrative demonstrating the pattern is real.
  
  - pattern_id: "pat_comparison_001"
    label: "Comparison destroys intrinsic motivation"
    total_instances: 14
    chain_summary: "You have independently rediscovered this lesson 14 times across 5 years — in the gym, during internships, in placements, and in leadership settings."
    key_instances:    # Top 5 most distinct instances
      - episode_id: "ep_2023_03_gym"
        date: "2023-03-14"
        excerpt_summary: "Comparison in gym triggered discouragement mid-workout"
      - episode_id: "ep_2024_07_internship"
        date: "2024-07-22"
        excerpt_summary: "Upward comparison during internship review meeting"
```

---

### Prospective Memory Signals
```yaml
prospective_memory:
  # Predicted trigger events for the next 7 days, based on historical pattern frequency.
  # These are surfaced as proactive nudges, not predictions of failure.
  
  predicted_triggers:
    - pattern_id: "pat_social_performance_002"
      label: "Social performance anxiety in formal group settings"
      historical_instances: 8
      trigger_pattern: "Fires consistently before formal presentations/reviews"
      predicted_event: "Friday team review session"
      predicted_date: "2026-06-14"
      intervention_from_history: "The slowdown + self-compassion approach from ep_2026_06_11 resolved a similar state in 3 hours."
      confidence: 0.72
```