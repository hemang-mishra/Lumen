# Goal 17: Periodic Macroextraction — What Keeps Happening

**Branch:** `goal17`
**Depends on:** Goal 12 (a populated graph to read), Goal 4 (model providers),
Goal 3 (the operational store, for the review-queue count), Goal 2 (`MacroextractionReportNode`)
**Spec:** `docs/Extraction/Macroextraction.md`, `docs/Graph/Schema.md` §11,
`docs/hld/Technical_HLD.md` §2.7 and §3.1

---

# SECTION A — LOGIC (please verify)

## Objective

Everything Lumen has built so far answers **"what happened today?"** — one entry in,
observations and patterns out. Nothing in the system has ever stepped back and asked
**"what keeps happening?"**

Some of the most important things about a person are invisible in any single entry. A
pattern that fires six times in May is not visible on any of those six days. A belief that
quietly changed shape is only a change if you remember its old shape. A lesson learned in
April and never touched again is only "ignored" from a distance.

This goal builds that distance. On a clock — daily, weekly, monthly, quarterly — Lumen
reads back a stretch of its own history and writes a **report**: a permanent, dated
document saying what recurred, what appeared for the first time, what stopped, what
changed, and what is still open.

## A1. What Gets Built

| | What it is |
|---|---|
| **The window** | A stretch of the person's life — a week, a month, a quarter — and the set of episodes that belong to it. |
| **The arithmetic** | Everything countable, counted in plain Python: how often each pattern fired, which are new, which went quiet, which beliefs changed, which lessons repeated, which are being ignored, who mattered, what is still unresolved. |
| **The narration** | One call to the deep-thinking model, given those counts, which writes the sentences a person actually reads — the arcs, the framing, the reflection prompts. It never produces a number. |
| **The shadow scan** | A separate, cheap, 48-hour check for a sudden burst of change — several beliefs branching or contradicting at once. Meant to notice a shift while it is still happening. |
| **The report** | An immutable node in the graph, linked to every episode it drew on, holding the whole thing as structured content. |
| **"What's due?"** | A pure calculation: given today's date and the reports already written, which periods are overdue — including ones missed while the app was switched off. |
| **A way to read them** | Two API routes and a plain inspection page, so a report can be opened the day it is written. |

## A2. The Decisions Taken

**1. Python counts, the model narrates** (per explicit user decision). Every number in a
report — episode counts, percentages, first-seen and last-seen dates, days open, days
dormant — is computed by ordinary code from graph queries. The model is handed those
finished facts and asked only for prose: the arc summaries, the growth and struggle
framing, the archetype-shift label, the reflection prompts. Two consequences worth stating:
the numbers are reproducible and testable with no model in the loop at all, and **if the
model call fails the report is still written** — with its arithmetic intact and its prose
marked unavailable.

**2. A report covers when things happened, not when they were written** (per explicit user
decision). An entry written on June 3rd about May 28th belongs to May. Because reports are
immutable, this means a report can be slightly incomplete forever, so periodic reports do
not run the instant a period ends — a short grace period (3 days for monthly and quarterly,
1 for weekly) lets late entries land first. That grace is a configured number, not a
hardcoded habit.

**3. Running the same period twice is safe** (per explicit user decision). Asking for a
report that already exists returns the existing one and spends nothing. An explicit
"force" re-run writes a second report for the same period; both are kept, because the graph
is append-only, and anything reading takes the newest. This is what makes a scheduler safe
to fire repeatedly.

**4. No live clock in this goal** (per explicit user decision). Goal 17 ships the two halves
a scheduler needs — "run this specific period" and "which periods are overdue right now" —
plus a way to fire one by hand. Goal 20 attaches APScheduler to the second half, as the
HLD already specifies. Nothing here starts a background thread.

**5. The shadow alert is written but not yet spoken** (per explicit user decision). Shadow
scans run and store their alerts; feeding them into the live conversation touches Goal 15's
context assembly and its token budgets, which belongs with Goal 20 where the scheduler that
produces them also lands.

**6. Three sections are deferred to Goal 19** (per explicit user decision), with the reason
recorded rather than left as a silent gap:

| Deferred | Why |
|---|---|
| Emotional valence time-series | The spec charts mood from −1.0 to +1.0 per week. No such number exists anywhere in Lumen — no observation carries a valence or arousal score. Producing the chart would mean inventing every point with a model, and a model-invented number plotted as a measurement is the one thing this design refuses. |
| Proof chains | Needs a pattern with 10+ instances across *all time*, which is a whole-history scan rather than a window read. It belongs with Goal 19's maintenance jobs, which already walk everything. |
| Prospective memory (predicted triggers) | Predicting next week's trigger events is a forecasting feature, not a summary of a window, and it is the one section with no verifiable ground truth. |

**7. One honest substitution.** The spec's "biggest struggle" section asks for
`avg_negative_emotion_intensity`, a 0.0–1.0 number. That number depends on the same missing
valence data. Rather than have a model invent it, the report reports what can actually be
counted: how many difficult-feeling observations accompanied that pattern in the window.
The field is named for what it is (`negative_observation_count`), not dressed as an
intensity score. **This is a documented divergence from `Macroextraction.md`**, recorded here
per the doc-first rule.

## A3. Judgement Calls (flagging, not asking)

- **A window with no episodes produces no report.** A month you did not write in should not
  leave behind a document saying nothing happened. The run records that it found nothing and
  stops. Same for a shadow scan that finds no burst — silence is the correct output, and a
  daily stream of "no shift detected" nodes would bury the ones that matter.
- **Reports are not searchable content.** They are written to the graph but not to the
  vector index, so a report can never come back as "history" inside a conversation. Reports
  are *about* the history; letting them retrieve alongside it would let the system quote its
  own summary back as if it were something the person said.
- **Archetype shift is only computed for monthly and quarterly reports.** Detecting it means
  comparing a window against the one before it over roughly 90 days; a week is too short for
  the comparison to mean anything.

## A4. How You'll Know It Works

- Seed a graph with a month of episodes, run the monthly report, and the report node comes
  back linked to exactly the right episodes, with pattern counts you can verify by hand.
- Force the same period again with no model available: the report is still written, numbers
  and all, with its prose marked unavailable.
- Run the same period twice without forcing: the second run writes nothing and returns the
  first report.
- Turn the clock forward six months with no reports written, ask what's due, and get the
  missed weeks, months and the quarter back in order.
- Open `/reports` in a browser and read the month.

---

# SECTION B — LOW-LEVEL DESIGN

## B1. Module Layout

```
lumen/pipeline/macroextraction/
├── __init__.py        ← public surface: run_report, reports_due, scan_shadow
├── contracts.py       ← Pydantic DTOs (window, corpus, facts, narrative, outcome)
├── windows.py         ← period arithmetic + the "what's due" calculator (pure)
├── corpus.py          ← the ONLY reader: graph → WindowCorpus
├── analytics.py       ← pure counting → ComputedFacts (patterns, beliefs, lessons, people…)
├── aging.py           ← pure: cooling / dormant pattern classification
├── shifts.py          ← pure: archetype-shift trend detection
├── shadow.py          ← 48h DecisionAudit density scan
├── prompts.py         ← the narrative prompt text
├── narrative.py       ← the single LLM call + id validation (pure over facts)
├── assemble.py        ← facts + narrative → report_content dict + node model
├── commit.py          ← the ONLY writer: node + analyzed_in edges, one transaction
├── runner.py          ← wires read → compute → narrate → assemble → commit
└── service.py         ← the one object the web layer holds; owns the models
```

**`service.py` was added during implementation.** The runner takes a writable graph as a
parameter, which is right for the pipeline and wrong for a route: a route handed a graph can
do anything to somebody's history. `MacroextractionService` is the narrow surface the web
layer gets — name a period, ask what is overdue, ask for the two-day scan — with the store
and the models held inside it. It also builds its models lazily, so a deployment with no
credentials still starts and still serves every read.

This mirrors the shape of `pipeline/retrieval/` and `pipeline/reconciliation/`. The
architecture rules are honoured the same way retrieval honours them: reads happen through an
injected `ReadOnlyGraph` in exactly one module, every computation between read and write is a
pure function over Pydantic models, and writes live alone in `commit.py`.

## B2. Contracts (`contracts.py`)

```python
class MacroWindow(BaseModel):          # frozen
    report_type: ReportType
    period_start: datetime             # UTC, inclusive
    period_end: datetime               # UTC, exclusive (half-open)

class EpisodeFacts(BaseModel):         # one window episode, flattened
    episode_id: str
    event_date: date
    occurred_at: datetime
    episode_summary: str
    historical_era: str | None
    observations: list[dict]           # cleaned rows from get_episode_contents
    event_ids: list[str]
    session_ids: list[str]

class StandingLink(BaseModel):
    from_id: str; to_id: str; to_type: str; edge_name: str
    valid_from: datetime; decision_id: str | None

class WindowCorpus(BaseModel):
    window: MacroWindow
    episodes: list[EpisodeFacts]
    links: list[StandingLink]                    # finding → pattern/belief/person/principle
    patterns: dict[str, dict]                    # node_id → cleaned pattern row
    beliefs: dict[str, dict]
    people: dict[str, dict]
    lessons: list[dict]                          # window + trailing-lookback lessons
    contradictions: list[dict]
    open_loops: list[dict]
    decisions: list[dict]                        # DecisionAuditNodes in window
    all_patterns: list[dict]                     # every ACTIVE pattern (for aging)
    previous_pattern_frequency: dict[str, float] # from the prior report, or recomputed
    pending_hitl: tuple[int, datetime | None]
    truncated: bool                              # a cap was hit; recorded in the report

class ComputedFacts(BaseModel):        # every number, no prose
    ... one field per report section, all typed ...

class NarrativeDraft(BaseModel):       # every sentence, no numbers
    headline: str
    growth_area_label: str | None
    growth_area_evidence: str | None
    struggle_label: str | None
    relational_summaries: list[RelationalSummary]
    environment_groups: list[EnvironmentGroup]
    relationship_arcs: list[ArcNarrative]
    biographical_gaps: list[GapJudgement]
    contradiction_prompts: list[ContradictionPrompt]
    archetype_shift: ArchetypeNarrative | None

class ReportOutcome(BaseModel):
    status: MacroRunStatus             # WRITTEN | SKIPPED_EXISTING | EMPTY_WINDOW | NOT_DETECTED | FAILED
    report_id: str | None
    window: MacroWindow
    episodes_analyzed: int
    narrative_status: NarrativeStatus  # OK | DEGRADED | UNAVAILABLE
    duration_ms: int
    error: str | None
```

Two new enums in `lumen/schemas/enums.py`: `MacroRunStatus`, `NarrativeStatus`, plus
`ArcDirection` (STRENGTHENING | STABLE | STRAINING | FADING) and `GapStatus`
(PRESENT | NARROWING | CLOSED) for the model's constrained outputs.

## B3. Windows and the Due Calculator (`windows.py`)

All pure, no clock of its own — `now` is a parameter.

```python
def window_for(report_type: ReportType, anchor: date) -> MacroWindow
def previous_window(window: MacroWindow) -> MacroWindow
def reports_due(now: datetime, existing: list[ReportKey], cfg: MacroConfig) -> list[MacroWindow]
```

Boundaries, all UTC, all half-open `[start, end)`:

| Type | Period | Grace before it runs | Due when |
|---|---|---|---|
| SHADOW | trailing 48h from `now` | none | no SHADOW report written in the last 24h |
| WEEKLY | ISO week, Mon 00:00 → next Mon 00:00 | 1 day | period ended + grace, no report for it |
| MONTHLY | calendar month | 3 days | as above |
| QUARTERLY | calendar quarter (Jan–Mar, …) | 3 days | as above |

Catch-up walks back `catchup_periods` (default 6) periods of each type and returns every
period that is past its grace with no report, oldest first, capped at
`max_runs_per_invocation` (default 4) so one scheduler tick can never trigger an unbounded
run of model calls. `ReportKey` is `(report_type, period_start)`, read from
`graph.find_reports()`.

## B4. Reading the Window (`corpus.py`)

One function, `gather(window, *, graph, ops, config) -> WindowCorpus`, and it is the only
place in the package that touches a store.

1. **Episodes** — `graph.find_episodes_by_event_date(start, end, limit=max_episodes_per_window)`.
   New provider method (see B10); needed because `find_nodes`' date filter targets
   `occurred_at`, and this goal keys on `event_date` per decision A2-2. Hitting the cap sets
   `truncated=True`, which is carried into the report rather than hidden.
2. **Episode contents** — `graph.get_episode_contents(episode_id)` per episode. One query
   each; ≤ ~120 for a quarter, no batching needed.
3. **Standing links** — `graph.find_standing_edges(finding_ids, edge_names=STANDING_EDGES)`.
   New batched provider method (B10): one query per relevant edge table, ~14 queries total
   regardless of how many observations there are. `STANDING_EDGES` is a module constant
   naming the `same_as_*`, `reinforces_*`, `branches_to_*`, `regulates_*`, `mentions_*` and
   `adopted_as_*` names from `EDGE_REGISTRY`.
4. **Pattern / belief / person rows** — `get_nodes_by_ids` over the link targets.
5. **Decisions** — `find_nodes(["DecisionAuditNode"], since=start, until=end)`, which
   requires the one-line `FILTER_COLUMNS` addition in B10.
6. **Lessons, contradictions, open loops** — `find_nodes` per type over the window, plus a
   trailing lookback of `ignored_lesson_lookback_days` (default 180) for the ignored-lesson
   comparison.
7. **All active patterns** — `find_nodes(["PatternNode"], active_only=True)` for the aging
   section, which is deliberately not window-scoped.
8. **Previous frequencies** — `graph.find_reports(report_type, ...)` for the prior period's
   report and its stored `pattern_frequency`. If none exists, a second, lighter pass gathers
   only the previous window's episodes and links (steps 1 and 3) — no contents, no model.
9. **Pending review count** — `ops.hitl.pending_summary(user_id)`, a new repository method
   returning `(count, oldest_created_at)`.

## B5. The Arithmetic (`analytics.py`, `aging.py`, `shifts.py`)

Every function here is `WindowCorpus -> section`, pure, no I/O.

| Section | Rule |
|---|---|
| `top_patterns` | Distinct in-window episodes per linked pattern; `first_seen`/`last_seen` from those episodes' `event_date`; top `top_patterns_limit` (10) by count. |
| `pattern_frequency` | `round(episodes_with_pattern / total_episodes * 100, 1)` for every linked pattern. |
| `emerging_patterns` | Pattern with `version == 1` and `valid_from` inside the window; `first_episode` is its earliest linking episode. |
| `disappearing_patterns` | In `previous_pattern_frequency` with a non-zero rate, absent from this window's links, still ACTIVE. |
| `belief_changes` | In-window `DecisionAuditNode` rows with `action == EVOLVE` and a belief target; old and new statements read from the two versions; `delta_description` used as the diff line when present. |
| `repeated_lessons` | `LessonNode.evidence_episodes` intersected with the window's episode ids, count ≥ `repeated_lesson_min_episodes` (3). |
| `ignored_lessons` | ACTIVE lesson whose newest evidence episode predates the window start by ≥ `ignored_lesson_days` (14) and which no in-window episode references. `days_since_last_seen` from that episode's `event_date`. |
| `biggest_growth_area` | Deterministic pick: the pattern with the largest *drop* in frequency versus the previous window that also carries an in-window EVOLVE or a `regulates_*` link; ties broken by episode count. Label and evidence sentence come from the model. |
| `biggest_struggle` | Pattern with the highest in-window episode count; `negative_observation_count` = observations of `NEGATIVE_AFFECT_TYPES` in the same episodes (module constant: EMOTION, SOMATIC_STATE, ANTICIPATORY_ANXIETY, COGNITIVE_FRICTION, RUMINATION_LOOP, SUPPRESSED_EMOTION_SURFACING, CORE_WOUND, INAUTHENTICITY_STATE, SOCIAL_PERFORMANCE_STATE). See A2-7. |
| `key_relational_dynamics` | RELATIONAL_DYNAMIC observations grouped by `person_refs`, count ≥ `relational_min_observations` (2). Counts here, summary from the model. |
| `key_environmental_dependencies` | ENVIRONMENTAL_DEPENDENCY observation list handed to the model for grouping; `confidence` set in code from the contributing observations' majority `signal_strength` (HIGH/CRITICAL → "high", else "medium"). |
| `unresolved_open_loops` | `OpenLoopNode` with `resolution_status == OPEN`, `valid_from <= period_end`, and no `closes` edge from an in-window episode; `days_open` from `valid_from` to `period_end`. |
| `pending_hitl_decisions` | `{count, oldest_item_days}` straight from step 9. |
| `high_signal_observations` | In-window observations with `signal_strength` HIGH or CRITICAL, newest first, capped at `high_signal_limit` (25). |
| `motif_of_unprocessed_depth` | Patterns reachable from SUPPRESSED_EMOTION_SURFACING observations, with counts. |
| `relationship_arcs` | Person appearing in ≥ `arc_min_episodes` (3) window episodes; `episodes_in_window` and `dominant_observation_types` counted in code; `arc_direction` taken from the person node's stored `sentiment_trend` when set, otherwise from the model; `arc_summary` always from the model. |
| `biographical_gaps_raised` | In-window BIOGRAPHICAL_GAP observations; `status` and `closing_evidence` from the model, constrained to `GapStatus`. |
| `pattern_aging` | `aging.py`: over all ACTIVE patterns, `cooling` when `cooling_days` (180) < age ≤ `dormant_days` (365) → multiplier 0.85; `dormant` when older → 0.5. `re_interrogation_prompt` is a fixed template with the label interpolated — no model. Reported only; Goal 19 owns applying the multipliers to retrieval. |
| `archetype_shift` | `shifts.py`, monthly and quarterly only. For each pattern present in this or the trailing-90-day comparison window, classify a trend: `frequency_increasing`/`frequency_decreasing` from the count delta, `awareness_increasing` from a rise in linked METACOGNITIVE_INTERRUPT observations. Detected when ≥ `archetype_min_patterns` (5) distinct patterns trend in a consistent direction. Contributing patterns and trends are computed; `shift_label` and `evidence_summary` come from the model. Sets the node's `archetype_shift_detected`. |
| `active_contradictions` | `ContradictionNode` rows created in window, unresolved; `days_open` computed; `reflection_prompt` from the model. |

## B6. The Narrative Call (`narrative.py`, `prompts.py`)

One call, `THINKING` role, `generate_structured(NarrativeDraft)`. Not streaming, so the
existing retry helper applies — `narrative_attempts` (default 2).

- **Input** is a rendered facts brief, not raw episodes: labels, ids, counts and a handful of
  short quoted excerpts, hard-capped at `narrative_max_chars` (20 000) with the lowest-count
  items dropped first and the truncation recorded.
- **The prompt states the contract explicitly**: produce no numbers, invent no ids, and use
  only the ids listed. This mirrors the extraction prompts' style.
- **Validation** (`_keep_known_ids`): every id in the draft is checked against the corpus.
  Unknown ids are dropped with a logged warning and the outcome downgrades to
  `narrative_status = DEGRADED`. A model failure or timeout yields `UNAVAILABLE` — the report
  is assembled from `ComputedFacts` alone with the prose fields left null.

Shadow uses a separate, much smaller `LIGHTWEIGHT` call producing only `shift_type` and
`summary`; the same failure handling applies.

## B7. The Shadow Scan (`shadow.py`)

```python
def scan_shadow(now, *, graph, lightweight, config) -> ReportOutcome
```

Reads `DecisionAuditNode` rows created within `shadow_window_hours` (48) whose `action` is
BRANCH or CONTRADICT and whose `status` is committed. Detected when there are
≥ `shadow_min_decisions` (3) of them touching ≥ `shadow_min_targets` (2) distinct target
nodes. Detected → a SHADOW report is written whose `report_content` follows the spec's
shadow schema (`detected`, `shift_type`, `trigger_nodes`, `summary`), with
`episodes_analyzed` set to the distinct episodes behind those decisions and `analyzed_in`
edges to them. Not detected → `MacroRunStatus.NOT_DETECTED`, nothing written (A3).

## B8. Assembly and the Write (`assemble.py`, `commit.py`)

`assemble.build(facts, draft, *, window, model_used) -> tuple[MacroextractionReportNode, list[str]]`
merges the numbers and the prose into the `report_content` dict laid out exactly as
`Macroextraction.md` names its sections, plus a small `meta` block recording
`narrative_status`, `truncated`, the deferred sections, and the schema version
(`report_schema_version: 1`). It returns the node and the episode ids for the coverage edges.

**Node id:** `make_slug_node_id("macro", f"{report_type.lower()}_{period_start:%Y_%m_%d}")` →
`macro_monthly_2026_05_01`. A forced re-run appends `_r2`, `_r3`, … based on how many already
exist, so both survive and the newest wins for readers.

**`commit.write(node, episode_ids, *, graph)`** — inside `graph.transaction()`: one
`write_node`, then one `analyzed_in` edge per episode. Nothing else writes. No vector upsert
(A3). No new operational table: the report node is its own record and `find_reports` is the
history.

## B9. The Runner (`runner.py`)

```python
def run_report(window, *, graph, ops, thinking, config, force=False) -> ReportOutcome
def run_due(now, *, graph, ops, thinking, lightweight, config) -> list[ReportOutcome]
```

`run_report`: duplicate check via `find_reports` → `SKIPPED_EXISTING` unless `force`;
gather → `EMPTY_WINDOW` if no episodes; compute → narrate → assemble → commit. Every path
logs one structured line with `trace_id`, window, episode count, section sizes, model, and
duration. `run_due` calls `windows.reports_due` and runs each, shadow included, never
exceeding the per-invocation cap.

## B10. Changes Outside the New Package

| File | Change |
|---|---|
| `lumen/graph/provider.py` | Three new `ReadOnlyGraph` methods: `find_episodes_by_event_date`, `find_standing_edges` (batched, one query per edge table), `find_reports`. |
| `lumen/graph/kuzu_impl.py` | Their implementations. `find_standing_edges` iterates the matching `EDGE_REGISTRY` entries with `WHERE a.node_id IN $ids`; `find_reports` filters on `report_type`/`period_start`, newest first. |
| `lumen/graph/queries.py` | **The plan was wrong about how this works.** `build_filters` bounds dates on `valid_from` directly, not through `FILTER_COLUMNS`, so adding an entry there would have changed nothing. What was added instead is `date_column(table)`: `valid_from` where a table has one, `created_at` for the two tables in the new `DATED_BY_CREATION` opt-in set, and `None` for the rest. A step in a sequence and a person still cannot be asked about by date, because neither has a moment of its own. `_newest_first` in the Kuzu store reads the same function, which also gives decisions and reports a real ordering they did not have. |
| `lumen/operational/repositories.py` + `sqlalchemy_impl.py` | `HitlQueueRepository.oldest_pending_at(user_id) -> datetime \| None`. `count_pending` already existed, so only the age was missing; two methods read more plainly than one returning a pair. |
| `lumen/schemas/enums.py` | `MacroRunStatus`, `NarrativeStatus`, `ArcDirection`, `GapStatus`. |
| `lumen/config.py` | New `MacroConfig`, wired into `AppConfig`, every threshold above as a `LUMEN_MACRO_*` env override. |
| `lumen/api/routes/reports.py` | `GET /reports` (list of envelopes), `GET /reports/{id}` (envelope + full content), `GET /reports/due`, `POST /reports/run` (body: type, period_start, force — synchronous). Read routes take `ReadOnlyGraph`; only the run route takes a writer. |
| `lumen/api/schemas.py` | `ReportEnvelopeView`, `ReportDetailView`, `ReportRunRequest`, `ReportDueView`. |
| `lumen/api/deps.py` | `get_reporter`, handing routes the report builder rather than a graph. |
| `lumen/api/main.py`, `static/reports.html`, all four existing pages | Router registration, the service opened at startup, a plain inspection page in the style of `episodes.html`, and the new page added to every page's navigation. |
| `lumen/tests/test_api_app.py` | The guard test listing every POST the API is allowed to have now names `/reports/run`, with the reason written beside it. That test exists so adding a write is a deliberate act; updating it is how the act is recorded. |

No migration is needed: the node table, the `analyzed_in` edge and the `ReportType` /
`ReportStatus` enums have existed since Goal 1–2.

## B11. Tests

`lumen/tests/test_macro_*.py`, one file per module, `Test*` classes grouped by behaviour,
`tmp_path` for the stores, the existing `FakeLLMProvider` for the model.

| File | Tests | Covers |
|---|---|---|
| `test_macro_windows.py` | 36 | Period boundaries incl. year and quarter edges and ISO weeks; grace; catch-up over a six-month gap; the per-invocation cap and which periods win it; UTC arithmetic. |
| `test_macro_corpus.py` | 32 | `event_date` vs `created_at` selection (the backdated-entry case explicitly); the episode cap setting `truncated`; the previous-period comparison read directly rather than from a prior report; link sorting; unreadable stored dates. Against a real Kuzu database. |
| `test_macro_analytics.py` | 57 | Every section rule, each with a hand-checkable corpus; the empty-period and single-episode degenerate cases; what happens when a record was never read. |
| `test_macro_aging.py` | 16 | Threshold boundaries at exactly 180 and 365 days; ageing measured against the period rather than against today. |
| `test_macro_shifts.py` | 16 | Detection at exactly 4 vs 5 trending patterns; a weekly report not pretending to have looked; awareness counted as a change; a fading pattern not counted twice. |
| `test_macro_shadow.py` | 16 | Detection at threshold; nothing written when not detected; a burst against one target not counting; decisions that changed nothing excluded. |
| `test_macro_narrative.py` | 33 | Invented references dropped → DEGRADED; a shift the arithmetic did not find refused outright; model failure → UNAVAILABLE with the numbers intact; the brief respecting its cap. |
| `test_macro_assemble.py` | 25 | Sentences joining figures by identifier; confidence computed from evidence rather than taken from the model; a rebuild getting `_r2`. |
| `test_macro_commit.py` | 10 | Node plus one link per episode; nothing left behind when a write fails; no vector write. |
| `test_macro_runner.py` | 21 | The Master Plan's acceptance test — an end-of-period run producing a report with correct episode coverage; skip-on-duplicate spending no model call; force writing a second report; a report written with no model at all. |
| `test_api_reports.py` | 26 | List, detail, due, run; 404 on an unknown id; every non-writing outcome coming back as an ordinary answer; the read routes asking for a type that cannot write. |
| `test_graph_report_reads.py` | 21 | The three new store reads, against a real Kuzu database: half-open day ranges, withdrawn links, chunked batches, reports narrowed by period. |
| `test_operational_hitl.py` | +4 | How long the review queue's oldest item has been waiting. |

**Result: 313 new tests, 4026 passing overall, 99% coverage on the new package** (the
uncovered lines are defensive branches for records that were never read and timestamps that
cannot be parsed).

Verified end to end in the browser as well: a seeded month of history, built through
`/ui/reports.html` against a live model, produced correct counts, prose carrying no numbers,
and a shift left unnamed because only three patterns trended where five are required.

## B12. Deferred, and to Where

| Deferred | To |
|---|---|
| Emotional valence time-series, proof chains, prospective memory | Goal 19 (A2-6) |
| Applying the cooling/dormant multipliers to retrieval scores | Goal 19 — this goal reports them only |
| The live scheduler (APScheduler) driving `reports_due` | Goal 20 (A2-4) |
| Shadow alerts injected into the conversation briefing | Goal 20 (A2-5) |
| Report rendering as a real surface (S8) | Goal 30 — this goal ships an inspection page, not a product screen |
| `user_id` scoping of every report query | Goal 21/22, along with the rest of the codebase |
