# Lumen Design Language

**Status:** the standing guideline. Every front-end goal is reviewed against this document.

This is the *how it looks and behaves* companion to
[`Requirements.md`](Requirements.md), which says *what must exist*. Requirements are
numbered `FR-*`; the rules here are numbered `DL-*` so a review comment can point at one
line.

The target is the restraint of ChatGPT with the warmth of Gemini. Both are described below
as observations of the shipped products, not as citations of anything official — the values
in this document are ours, chosen in that spirit.

---

## 1. What we are copying, and what we are not

**What ChatGPT gets right.** Almost no chrome. The conversation is text on a single
background with no boxes around it; the only strong shape on the screen is the composer.
The sidebar is a neighbouring neutral rather than a different material. Accent colour is
nearly absent — the primary button is black on white or white on black. Icons are thin,
monochrome, one weight. Hover states are barely there. The result is that nothing competes
with what you are reading.

**What Gemini gets right, and gets more right.** The same restraint, but it does not read as
*absence*. Three things do that work: surfaces carry a faint tint rather than being pure
grey, so they feel chosen rather than defaulted; corner radii are generous and consistent,
which reads as soft rather than technical; and colour appears at *moments* — a selected nav
pill, the send action — instead of being either everywhere or nowhere. It also leans on state
layers (a translucent overlay on hover and press) rather than per-component hover colours,
which is why it stays coherent across hundreds of controls.

**What we take.** ChatGPT's discipline about chrome and colour. Gemini's tinted surfaces,
generous radii, single confident accent, and state-layer mechanism.

**What we deliberately do not take.**

| Not this | Why |
|---|---|
| Gradients, glows, sparkle effects | One accent, flat. A gradient dates fast and cannot be themed with a token. |
| Hover-to-reveal row actions | They do not exist on touch, and half of Lumen's use is on a phone (FR-D1). |
| Illustrated empty states, mascots | An empty state is a sentence (DL-46). |
| A dashboard of big-number cards | Counts are useful; a wall of them is decoration. |
| Uppercase micro-labels | The `/ui` harness uses them as section eyebrows. They read as an admin console. Sentence case, secondary colour (DL-19). |
| Per-node-type colour | Fifteen node types cannot be fifteen colours. See DL-13. |

---

## 2. The seven rules that produce the look

If a screen feels wrong, it is almost always one of these.

- **DL-1 — Space separates, not lines.** Reach for whitespace first, a background shift
  second, a hairline border only when neither works. Most panels in the harness have a border
  they do not need.
- **DL-2 — One surface family.** A screen is a canvas with at most two raised levels on it.
  No third level, no nested cards inside cards.
- **DL-3 — Colour is spent on meaning, never on decoration.** At most **two non-neutral hues
  visible on any one screen** (§4.3).
- **DL-4 — Type carries the hierarchy.** Size, weight and colour of text — in that order.
  Not borders, not background blocks, not colour.
- **DL-5 — Radii are generous and consistent.** Nothing sharp, nothing inconsistent within a
  screen.
- **DL-6 — Interaction is a state layer.** One translucent overlay mechanism for hover,
  press and selection everywhere (§8).
- **DL-7 — Motion is short, functional, and never bounces.** 120–260ms, ease-out, and
  nothing moves that did not need to.

---

## 3. Tokens are the only way to style

- **DL-8** Every colour, size, space, radius, duration and shadow is a CSS custom property
  defined once. No component holds a literal colour, and no literal `px` for spacing.
- **DL-9** No colour may be defined *only* inside a dark-mode block. The light palette is the
  base definition; dark redefines the same names. This is the single rule that keeps light
  from rotting (FR-XT2).
- **DL-10** Tokens are named for their **role**, not their value or their appearance.
  `--surface-raised`, never `--grey-800`; `--text-secondary`, never `--grey-500`. A theme is
  then free to achieve "raised" by getting lighter (dark) or by getting whiter with a shadow
  (light), which is exactly what these two themes do differently.

---

## 4. Colour

### 4.1 The neutral ramp

Near-neutral, faintly tinted. **Dark leans very slightly cool; light leans very slightly
warm.** This is not an aesthetic coin-flip: a warm tint at low lightness reads as brown and
muddy, and a cool tint at high lightness reads as clinical and cheap. Each direction is the
one that survives its own end of the scale.

```
                       LIGHT                DARK
--canvas               #fbfaf9              #131416
--surface              #ffffff              #1b1d20
--surface-raised       #ffffff (+shadow)    #24262a
--surface-sunken       #f3f1ef              #0e0f11
--border-hairline      rgba(0,0,0,.07)      rgba(255,255,255,.07)
--border               rgba(0,0,0,.13)      rgba(255,255,255,.13)
--text                 #1c1d20              #e7e8ea
--text-secondary       #5c5f66              #9ba0a8
--text-tertiary        #8a8e96              #6c7076
```

- **DL-11** `--surface-sunken` is for content that is *inside* something — payload blocks,
  code, transcripts. It is the only place a container may be darker than its parent in dark
  mode.
- **DL-12** `--text-tertiary` is for metadata and ids only, and must never carry a sentence
  a person needs to read.

  *Amended in Goal 23.* This rule previously said the token "does not meet AA", which
  contradicted `FR-XA2` outright — and an id nobody can read is not quiet, it is missing.
  The values were darkened until they clear 4.5:1 on all three surfaces in both themes,
  which they now do; the rule about what it may carry stands, as a matter of hierarchy
  rather than of legibility. The same pass moved `--positive` and `--caution` in light,
  which failed the floor when a chip put them on their own faint tint. Enforced by a test
  over the palette, not by review.

### 4.2 Accent

One accent, one token.

```
--accent               #4a5bd4              #a8b6f8
--accent-contrast      #ffffff              #191b22
--accent-quiet         rgba(74,91,212,.10)  rgba(168,182,248,.14)
```

- **DL-13** The accent appears in at most **three places on a screen**: the primary action,
  the current navigation item, and the focus ring. Links inside prose are the one exception.
- **DL-14** No gradients. No accent-tinted backgrounds beyond `--accent-quiet` for selection.

### 4.3 Semantic colour, and the colour budget

Four semantic roles. Not five, not eleven.

```
--positive             #1e7d4f              #7fc79b     settled, succeeded, active
--caution              #a35a00              #e0b071     needs attention, consequential
--critical             #b3261e              #f2b8b5     failed, refused, withheld
--info                 = --accent                       neutral information
```

**DL-15 — The colour budget.** At most two non-neutral hues visible at once, counting the
accent. If a screen wants three, one of them is decoration and comes out.

This collides head-on with Lumen's data, and the collision is worth stating plainly. The
graph has 15 node types, 8 reconciliation actions, 3 signal strengths and several status
enums. That is dozens of things a naive design would colour. The resolution is a single
principle:

> **DL-16 — Colour encodes *state*, never *taxonomy*.**
> What kind of thing something is gets a word and a glyph. What condition it is in may get a
> colour.

Applied:

| What | How it is distinguished | Colour? |
|---|---|---|
| 15 node types | Label + monochrome glyph + grouping | **No** |
| 8 reconciliation actions | The action's name, always spelled out | Only via consequence, below |
| Consequence of an action | `MERGE`/`REINFORCE`/`BRANCH`/`REGULATE` → neutral; `EVOLVE`/`CONTRADICT`/`DIALECTIC` → caution; `AMBIGUOUS` → caution outline + "needs you" | Yes, 3 steps |
| Signal strength | Text weight + the word. `CRITICAL` also gets a marker, because it is gated | **No** |
| Run / episode status | positive / caution / critical | Yes |
| `EXTRACTION_FAILED`, `SUSPENDED`, withheld, `truncated` | critical or caution + a sentence | Yes |

**DL-17** Colour is never the only carrier of meaning. Every coloured state has a word
beside it (FR-XT6, P4). This is what makes DL-15 affordable — colour is redundant
reinforcement, so we need very little of it.

---

## 5. Type

- **DL-18 — One family, from the system.**
  `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, system-ui, sans-serif`, with
  `ui-monospace, SFMono-Regular, Menlo, monospace` for ids and payloads. No web font: nothing
  to load, nothing to flash, and it reads as native on the phone where most reflect use
  happens. If a display face is ever wanted it arrives as one token, not as a rewrite.

- **DL-19 — The scale, and nothing between.**

  *Amended in Goal 23.* These were originally named `--text-*`, which collided with the
  colour names in §4.1 — `--text` is a colour and `--text-body` was a size, and one
  stylesheet cannot hold both meanings. The colours kept the prefix, since they are used
  far more often; the sizes became `--type-*`.

| Token | Size / line-height | Used for |
|---|---|---|
| `--type-meta` | 12 / 1.4 | ids, timestamps, counts |
| `--type-dense` | 13 / 1.45 | compact-density body, table cells |
| `--type-body` | 15 / 1.5 | default UI text |
| `--type-reading` | 17 / 1.7 | journal text, assistant replies |
| `--type-title` | 20 / 1.35 | section titles |
| `--type-page` | 24 / 1.25 | page titles |

- **DL-20** Weights: **400, 500, 600**. Nothing lighter, nothing heavier, no italics except
  inside quoted journal text.
- **DL-21** Letter spacing: `-0.01em` at 20px and above, `0` below. No tracked-out uppercase
  anywhere.
- **DL-22** Section labels are sentence case in `--text-secondary` at `--type-body`/500 —
  not uppercase eyebrows.
- **DL-23** Reading measure is capped at **68ch**. UI text is not capped but its container
  is (§6).

---

## 6. Space and layout

- **DL-24** 4px base. Scale: `2 4 8 12 16 20 24 32 40 56 72 96`. Nothing off-scale.
- **DL-25** Page gutters: 16px mobile, 24px tablet, 32px desktop.
- **DL-26** Two content widths: **720px** for reading surfaces, **1200px** for inspect
  surfaces. Nothing is full-bleed on a wide monitor.
- **DL-27** Vertical rhythm between blocks is 32px; within a block, 12–16px. Generous
  outside, tight inside — this is most of what makes a screen feel calm.
- **DL-28** Nothing scrolls horizontally at page level. Wide content scrolls in its own
  container with a visible edge (FR-XM1).

---

## 7. Shape and elevation

- **DL-29 — Radii.** `6` chips and tags · `10` buttons, inputs · `14` cards, panels ·
  `20` sheets, dialogs · `999` pills (nav items, filter pills). Leaning generous, Gemini-side.
- **DL-30 — Three levels, no more.** Canvas (0) → surface (1) → overlay (2). A card inside a
  card is a design error; use a hairline or spacing instead.
- **DL-31 — Elevation is achieved differently per theme, by intent.** In light, level 1 is
  white with a soft shadow and no border. In dark, level 1 is a lighter surface with an
  optional hairline and **almost no shadow** — shadows do not read on dark grounds, and
  faking them produces the muddy halo that makes dark themes look cheap.
- **DL-32** Shadows: two tokens only (`--shadow-1`, `--shadow-2`), both soft, both
  low-opacity, neither coloured.

---

## 8. Interaction

- **DL-33 — One state-layer mechanism.** An overlay of neutral at a fixed opacity, on top of
  whatever surface is underneath:

| State | Light | Dark |
|---|---|---|
| hover | black 4% | white 6% |
| pressed | black 8% | white 10% |
| selected | `--accent-quiet` + `--accent` text | same |
| disabled | 38% opacity, no layer, no pointer | same |

  Components do not define their own hover colours. This is why the system stays coherent as
  it grows, and it is the mechanism both reference products use.

- **DL-34 — Focus is always visible.** 2px `--accent` ring at 2px offset, on
  `:focus-visible`. Never removed, never replaced by a colour change alone.
- **DL-35 — Touch targets are 44px minimum** in comfortable density, 32px in compact — and
  compact is never used on a touch screen (DL-40).
- **DL-36 — No hover-only affordances.** If an action is reachable only by hovering, it does
  not exist on a phone.

---

## 9. Motion

- **DL-37** Three durations: `120ms` micro (hover, focus, chip), `180ms` standard
  (disclosure, tab, nav, toast), `260ms` large (sheet, dialog, route change).
- **DL-38** Easing: enter/expand `cubic-bezier(0.2, 0, 0, 1)`; exit/collapse
  `cubic-bezier(0.3, 0, 0.8, 0.15)`. Nothing overshoots, nothing bounces, nothing springs.
- **DL-39** `prefers-reduced-motion` collapses every duration to 0 and leaves opacity only.
  Streaming text is exempt — it is content arriving, not animation.

---

## 10. Density

Two modes. This is the one place Lumen genuinely needs more than its references have: neither
ChatGPT nor Gemini has to render a thirty-stage pipeline trace.

- **DL-40 — Two densities, set on a container and inherited.**

| | comfortable | compact |
|---|---|---|
| body text | 15px | 13px |
| control height | 44px | 32px |
| card padding | 16px | 12px |
| table row padding | 12px | 6px |

- **DL-41** Reflect surfaces are **always comfortable**.
- **DL-42** Inspect surfaces default to **compact on pointer devices and comfortable on touch**
  — a phone must not inherit a 32px tap target just because the screen is a run trace
  (FR-D2, FR-XM3).
- **DL-43** There is no third density, and density is not a user setting.

---

## 11. Component conventions

- **DL-44 — Three buttons.** `primary` (accent fill, one per view), `secondary` (surface +
  hairline), `ghost` (text only). Destructive actions are `secondary` or `ghost` with
  `--critical` text — never a large red fill, which shouts on a screen about someone's inner
  life.
- **DL-45 — Chips are outlines.** 6px radius, `--type-meta`, hairline border, colour from
  DL-16. Filled chips are reserved for the single "needs you" state.
- **DL-46 — Four states, one component.** Every list and panel handles loading, empty,
  filtered-to-empty, and failed, with a distinct sentence for each (FR-XS1). One sentence,
  no illustration, and an action where one exists.
- **DL-47 — Tables become cards below 768px.** Each row is a card of label/value pairs. **No
  column is dropped** (FR-XM2). Design the card form at the same time as the table, not after.
- **DL-48 — Disclosure is the workhorse.** Everything from P1's "complete underneath" is a
  disclosure: chevron, 180ms, label states what is inside ("Everything it holds", "What went
  in") rather than saying "more".
- **DL-49 — Payload blocks** sit on `--surface-sunken`, mono at 12px, scroll in place, cap at
  ~340px with an expand.
- **DL-50 — One overlay component**: bottom sheet on mobile, centred dialog on desktop.
- **DL-51 — Icons** are line-style, 20px, 1.5px stroke, `currentColor`, one set. No filled
  icons, no two-tone, no emoji as UI.

---

## 12. Lumen's own patterns

These are specific to this product and are where most of the value is. They exist because the
harness got them wrong in ways that are now understood.

### DL-52 — The record line

The canonical way *any* graph record appears anywhere in the app. It is the direct answer to
principle P6 ("an id is not an answer").

```
  What it says, in its own words.                    ← --type-body, --text
  observation · 11 Jun 2026 · high · active          ← --type-meta, --text-secondary
  obs_2026_06_11_01_003                        ⧉     ← --type-meta, mono, --text-tertiary, copy
```

- The record's own words are the heading. Always.
- The meta row is middot-separated, never a row of chips.
- The id is last, quiet, monospace, and copyable on click.
- **An id is never the primary label of anything.** The run view's
  `obs_… → same_as → pat_…` is the exact failure this pattern exists to prevent.

### DL-53 — The decision card

How one reconciliation reads (FR-S5-8…FR-S5-15). Direction is **always new → existing**.

```
┌─────────────────────────────────────────────────────────────┐
│ reinforced          confidence 0.86 · lightweight model      │
│                                                              │
│  This finding                                                │
│  "I put off the review again and told myself it was timing."  │
│  observation · 11 Jun 2026 · high                            │
│                                                              │
│         ↓ reinforces                                         │
│                                                              │
│  An existing pattern, first recorded 14 Mar 2026             │
│  "Avoids feedback conversations by reframing them as badly   │
│   timed rather than unwanted."                               │
│  pattern · seen 4 times · active                             │
│                                                              │
│  Why  The same avoidance with a new justification, not a     │
│       change in what is believed.                            │
│                                                              │
│  Also considered: 2 candidates  ▸                            │
│  Audit  d_2026_06_11_01_004  ⧉                               │
└─────────────────────────────────────────────────────────────┘
```

- The action is a word, first, always spelled out.
- **The older record appears in its own words with its own date.** This is the single most
  important sentence in this document — it is the whole point of the reconciliation surface.
- `EVOLVE` shows the `delta_description` as a stated difference, old above new.
- Candidates retrieved and not chosen live behind a disclosure, so a wrong connection reads
  as a choice among options rather than an inexplicable jump.
- A gate that fired in code after the model answered is named on the card, in caution.

### DL-54 — Journal text

`--type-reading` (17/1.7), capped at 68ch, `white-space: pre-wrap`, `overflow-wrap: anywhere`.
**Rendered as text and never as markup** — an export can contain anything, and this is a
safety rule as much as a typographic one (FR-XA5).

### DL-55 — Streaming

Reserve the line before the first token so nothing reflows. A single caret. No per-token
fade, no typewriter effect on top of real streaming — the text is already arriving, and
animating it twice makes it feel slower.

### DL-56 — The four empty answers

Retrieval's outcomes are four different facts (P5, FR-XS2) and each gets its own sentence:

| Outcome | What it says |
|---|---|
| nothing worth looking up | "Nothing here needed looking up." |
| ran, found nothing | "Looked, and there is nothing on this yet." |
| could not run | "The search could not run." + the reason, in caution |
| suppressed | "Reasons to search were found and deliberately set aside." |

Never an empty list for any of them.

### DL-57 — Say what is held back

A gated record, a cut-short list, a `truncated` graph slice, an item waiting for a person: all
stated in place, in caution, with the reason (P7, FR-S6-6). A persistent marker, not a toast
that disappears.

### DL-58 — Quiet in the hard moments

During a `CRISIS` or `VULNERABLE` register the interface adds nothing: no annotations, no
chips, no "show the working" affordance, no nudge (FR-XV2). The most important design decision
on the chat surface is the one where it does less.

---

## 13. Review checklist

A front-end change is not done until all of these are yes.

1. Does it work at 375px with one thumb, and at 1440px?
2. Was it reviewed in **both** themes, including every state?
3. Are all four of loading / empty / filtered-empty / failed handled with distinct wording?
4. Is every colour from a token, and is the light value the base definition?
5. Are there at most two non-neutral hues on screen?
6. Does every coloured state have a word beside it?
7. Are records shown by what they say, with ids quiet and copyable?
8. Do tables have a defined card form that drops no column?
9. Keyboard reachable, visible focus, AA contrast, labelled for a screen reader?
10. Is anything reachable only by hover?
11. Does `prefers-reduced-motion` calm it?
12. Is anything the backend distinguishes being flattened on screen?

---

## 14. How this document changes

Add rules with new numbers; never renumber. A rule that turns out to be wrong is marked
withdrawn with the reason kept. When a design decision is made in a goal that contradicts a
rule here, the rule is amended in the same change — a guideline the code has quietly outgrown
is worse than no guideline.
