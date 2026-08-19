# Lumen Front End — Requirements Specification

**Status:** requirements. This document says *what the front end must do and be*.
[`Design_Language.md`](Design_Language.md) says *how it looks and behaves* (rules `DL-*`
and the review checklist). `implementation/Master_Plan.md` Phases 7–8 (Goals 23–32) say
*in what order*.

**This document will grow.** It is expected to be amended as we think of more. Every
requirement carries an id so that later documents, goals and discussions can point at
one line instead of re-describing it.

**Decisions already taken** (see §9 for the reasoning, §9.2 for what is still open):

| # | Decision |
|---|---|
| DEC-1 | **Inspect surfaces are built first** — Runs, Episode detail, Graph explorer. Chat follows once API-1 and API-2 exist. |
| DEC-2 | **The front end calls FastAPI directly.** There is no Next.js BFF layer. |
| DEC-3 | **One application**, with reflect and inspect as separated sections of the same shell. |
| DEC-4 | **Same repository, its own directory** — `frontend/` beside `lumen/`, with its own build and deploy. |
| DEC-5 | **There is a login, and it is Google.** Lumen is multi-user by design. Sign-in is a real surface (S11), the app is unusable without it, and every screen shows one person's data. See [`docs/hld/Auth_Architecture.md`](file:///Users/hemangmishra/Projects/Lumen/docs/hld/Auth_Architecture.md). |

---

## 1. What we are building

A real front end for Lumen, in its own codebase, that talks to the Python service over
HTTP only.

Lumen already works. There is a pipeline that turns writing into a knowledge graph, a
query layer that decides what to look up mid-conversation, and a read API over both.
What there isn't is a way to *live in* the product. The current pages at `/ui` are a test
harness — deliberately plain, no build step, dark-only, table-heavy, written to make the
machinery visible while it was being built and explicitly meant to be deleted.

This front end replaces it, and has to be two things at once:

- **A place to reflect.** Chat, write, read back your own history. This must feel like a
  real product — calm, fast, good on a phone.
- **A place to inspect.** See what the pipeline made of what you wrote, run by run,
  episode by episode, decision by decision. This is what the harness is good at today,
  and none of it may be lost.

### 1.1 Non-negotiables inherited from the backend

| # | Rule | Why it constrains the UI |
|---|---|---|
| NN1 | The front end never touches Kuzu, Qdrant or SQLite. HTTP only. | Everything on screen must be traceable to an endpoint. A screen with no endpoint is a backend requirement, not a design decision. |
| NN2 | Retrieval during conversation is **invisible by design**. | The chat surface must not show a "here's what I looked up" panel by default. See P3. |
| NN3 | The graph is append-only; nothing is edited in place. | There are no edit-and-save screens for content. Corrections happen through review decisions, which create new records. |
| NN4 | An episode stores a summary and a hash of its text, never the text. | "Read what I actually wrote" is a separate call to the operational store and can legitimately be missing. The UI must degrade, not error. |
| NN5 | A day can hold several independent sessions, keyed `(event_date, session_label)`. | No screen may assume one session per day. |

---

## 2. Who uses it, and on what

**One person at a time, but not only ever one person** (DEC-5). Each user signs in with
Google, and everything on every screen belongs to whoever is signed in. There is still no
sharing, no roles, no teams and no second-person view of anyone's history — those stay out
of scope (§8). What changed is that "which person" is now an answered question rather than
an assumed one.

The backend for this does not exist yet: `AppConfig.user_id` is an environment variable and
every request is the same person. It is specified in `Auth_Architecture.md` and owned by
Goals 21 and 22. Until Goal 21 lands, the front end builds against a service with auth
disabled — which is a deliberate mode (`LUMEN_AUTH_ENABLED=false`), not a workaround. See
FR-S11-8.

That person uses it in two postures, and both are first-class:

- **Phone, one hand, short sessions.** Capturing a thought, reading a reply, clearing the
  review queue while walking. This is the *majority* posture for the reflect surfaces.
- **Desktop, both hands, long sessions.** Importing exports, reading a run top to bottom,
  chasing a decision through the graph. This is the majority posture for the inspect
  surfaces.

**FR-D1** Every reflect surface must be fully usable on a 375px-wide screen with one thumb.
**FR-D2** Every inspect surface must be *readable* and *navigable* on a phone, even where
it is more comfortable on a desktop. "Open it on a laptop" is not an acceptable answer for
any screen.
**FR-D3** No surface may be desktop-only or phone-only. Same routes, same data, different
layout.

---

## 3. Design principles

These are the rules a design proposal gets judged against.

**P1 — Neat on the surface, complete underneath.**
The default view of anything is calm and shows what matters. Everything else is one
deliberate expansion away. The harness shows every property of every record because the
field that turns out to matter is always the one a curated view left out — that instinct is
correct and must survive, but as *progressive disclosure*, not as the default view.

**P2 — Two audiences, one app, clearly separated.**
Reflect surfaces and inspect surfaces are different products sharing a shell. A person
journalling should never trip over a stage payload. A person debugging should never have to
leave to find one. Separation is by navigation, not by hiding things behind a flag.

**P3 — Retrieval stays invisible where the spec says invisible.**
The conversation must not narrate its own machinery. What was looked up, what was withheld,
what scored what — all of that belongs on the inspect side. A **"show the working" mode**,
off by default, may reveal it inline for development. During a `VULNERABLE` or `CRISIS`
register, even that mode must not decorate the conversation.

**P4 — State is shown by word *and* colour, never colour alone.**
Already true of the harness. Keep it. Applies to every status, every tag, every diff.

**P5 — Never flatten a distinction the backend fought to keep.**
The clearest case: "nothing was found", "the search could not run", "there was nothing
worth looking up", and "this turn reads as distress so we deliberately did not look" are
four different answers. The retrieval layer keeps them apart on purpose. Any UI that renders
all four as an empty list is a wrong answer that looks right. Same for `truncated` on a
graph slice, `search_failed`, `EXTRACTION_FAILED` records and `SUSPENDED` episodes.

**P6 — An id is not an answer.**
`obs_2026_06_11_01_003` tells a person nothing. Wherever a record is referenced, show what
it says and when it was written, with the id available for copying. This is the single
biggest failing of the current run view.

**P7 — Nothing is silently withheld.**
Where the system holds something back — a sensitive record gated until the subject is
raised, a list cut by a limit, a decision waiting for a person — say so in place.

**P8 — Dark is the reference, light is an equal.**
See §5.1.

---

## 4. Required surfaces

Eleven surfaces. For each: what it is for, what it must show, what it must let you do, and
what backs it. "**Needs API**" marks something no current endpoint can answer — collected
in §6.

S11 is numbered last and reached first — the ids follow the order these were written, not
the order a person meets them (§10: never renumber).

### S1 — Today (the chat surface)

The main reflect surface, and the one that must feel like a real product.

**FR-S1-1** A conversation view for today's session: your turns and the assistant's,
streamed as it answers.
**FR-S1-2** A composer that is comfortable one-handed on a phone: fixed to the bottom,
keyboard-aware, multi-line, send without losing the caret.
**FR-S1-3** Show which session of the day this is (`session_label`) and let a new,
separate session be started on the same day without merging into the current one.
**FR-S1-4** Show, quietly, that today's writing has not been extracted yet and roughly
when it will be — extraction fires after the session decays (2h of quiet, configurable) or
on an explicit "end session".
**FR-S1-5** Offer an explicit **End session** action. The spec has always described one; no
endpoint exists. **Needs API.**
**FR-S1-6** Nothing about retrieval appears here by default (P3). Behind the "show the
working" toggle: the turn's register, its triggers, what each pass returned, what was
withheld and why, the context allowance the register earned, the assembled `ChatPrompt`, and
the latency against the **8-second** budget.
**FR-S1-7** Late-night nudge: if the user is active after a configurable hour and writes
something reflective, gently offer to capture anything left before tomorrow.
**FR-S1-8** Voice input is a placeholder in the layout, not a feature yet — the
transcription provider is a Protocol with no implementation. The composer must have room
for it without a redesign.
**FR-S1-9** Editing a turn **branches** rather than overwrites. Goal 15 gave messages a
parent, so an edit writes a sibling and nothing said is destroyed, and the pipeline extracts
the active thread only. The UI must let a turn be edited, let the versions of it be moved
between, and make plain which thread is the live one — otherwise a person cannot tell what
tomorrow's graph will be built from.

**Backed by:** nothing yet for the reply itself (Goal 16). `POST /query/formulate` and
`POST /query/retrieve` back the "show the working" mode today. **Needs API** for sending a
turn, streaming a reply, appending to the session buffer, and ending a session.

### S2 — History (days and sessions)

**FR-S2-1** A list of past days, newest first, each showing its sessions with a one-line
sense of what they were about.
**FR-S2-2** Open a past day to read it back: the conversation as written, plus what was
extracted from it and its extraction status.
**FR-S2-3** A day with several sessions shows them as separate threads, never merged (NN5).
**FR-S2-4** Reading a past day is read-only. Asking questions *about* it is a query, and
belongs to the chat surface.
**FR-S2-5** Where an entry was written in another language, say so — the pipeline records
`language_tags` and translates; a Hindi entry silently shown as English is a lie.

**Backed by:** `GET /graph/nodes?types=EpisodeNode`, `GET /debug/episodes/{id}/source`.
A proper day index is **Needs API** (a calendar-shaped read, not 200 episodes filtered
client-side).

### S3 — Import

The other way in, and it already works end to end.

**FR-S3-1** Drop or choose a chat export (JSON). Show what the file was understood to
contain *before* any work starts: per conversation, its title, the day it will be filed
under, its message count, and whether it is a duplicate.
**FR-S3-2** Follow each conversation from queued to finished, live, without a manual
refresh.
**FR-S3-3** A permanent import history, newest first, with the failure reason in place for
anything that failed, and a link to the run each one caused.
**FR-S3-4** Refuse early and say why: an upload with no model reachable is rejected before
anything is written, and the message must say that plainly rather than reading as a bad file.
**FR-S3-5** Show the derived event date and where it came from (the first message's
timestamp, in the configured timezone), because getting this wrong misfiles a whole
conversation and it is not obvious afterwards.

**Backed by:** `POST /ingest/file`, `POST /ingest/json`, `GET /ingest/imports`,
`GET /ingest/imports/{batch_id}`. Complete today.

### S4 — Runs (the unified run interface)

Today there are effectively two histories: imports on one page, pipeline traces on another.
There is one kind of thing being described, and it must be one list.

**FR-S4-1** One run list covering every pipeline run whatever triggered it. Trigger is a
filter, not a separate page. Triggers to distinguish: **import** (an uploaded export),
**live session** (a chat buffer that decayed or was ended), **replay** (a re-run), and
**simulation** (the built-in corpus).
**FR-S4-2** Each row states what was processed, what triggered it, when it started and
finished, how it ended, and how much it wrote — records, links, indexed.
**FR-S4-3** Each row links back to its source: the import row, or the chat day and session.
**FR-S4-4** A live session that is buffering must be visible here as **pending** — a run
that has not started yet, with what it is waiting for. Today a job appears only once it
starts, so the most common trigger in the finished product is invisible until it is over.
**Needs API.**
**FR-S4-5** Open a run to read it as a story, top to bottom, in the order it happened:
what was processed, then each stage attempt with its status, duration, model, attempt
number and both payloads, then everything it wrote.
**FR-S4-6** Stages are grouped by episode, not listed flat. One entry usually holds several
unrelated topics decided independently; thirty flat stage rows read as one long run instead
of four short ones.
**FR-S4-7** Every failure says what kind of failure it was in words. "Did not validate"
means different things at different stages, and a rate limit must never be described as a
validation problem.
**FR-S4-8** Everything the run wrote is shown as **what the record says**, not as an id
(P6). This is the specific fix for the current view, where reconciliation appears as
`obs_… → same_as → pat_…` and is unreadable.
**FR-S4-9** Both payloads per stage remain available in full, raw, one expansion away (P1).
**FR-S4-10** Where a stage could be re-run, offer it. `rerun_from_stage` is unimplemented
(`Technical_HLD.md` §10) — until it exists, the button must be absent rather than dead.

**Backed by:** `GET /debug/traces`, `GET /debug/traces/{trace_id}`. Trigger attribution,
pending runs, resolved record text and re-run are **Needs API**.

### S5 — Episode detail (writing, findings, and reconciliation)

The most important inspect surface, and the one with the largest gap between what it should
show and what the API can currently answer.

**FR-S5-1** One episode, with the writing it came from and everything made of it, on one
page. The findings come first; the transcript is long and everything worth checking it
against sits below.
**FR-S5-2** The episode's own record in full: summary, entry class, date, which episode of
that day it was, and a link to the run that read it.
**FR-S5-3** Everything extracted, grouped by kind, each showing its own words, its type,
its signal strength, its status and its provenance — with every remaining property one
expansion away.
**FR-S5-4** The verbatim evidence for each finding, so a claim about someone's history can
be checked against the sentence it came from.
**FR-S5-5** The transcript, message by message, rendered as text and never as markup, with
line breaks preserved, scrolling in its own box rather than pushing the page down.
**FR-S5-6** Findings that failed extraction (`EXTRACTION_FAILED`) are shown as such, with
the type that was attempted and the rule that refused it — not hidden, and not mixed in
with real findings.
**FR-S5-7** If the episode is `SUSPENDED`, say what is outstanding.

**Reconciliation — the part that is missing entirely.** For each finding, the page must show:

**FR-S5-8** Which of the eight actions was chosen — MERGE, REINFORCE, EVOLVE, BRANCH,
CONTRADICT, DIALECTIC, REGULATE, AMBIGUOUS — with its confidence and the model that decided.
**FR-S5-9** **What it was connected to, in that record's own words and with its date.** Not
an id. This is the user-facing heart of the requirement: "this observation from Tuesday was
reinforced into a pattern first recorded in March, which says *…*".
**FR-S5-10** Why: the reasoning, and for EVOLVE the mandatory `delta_description` — what
changed, stated as a difference between the old version and the new.
**FR-S5-11** What was created as a consequence: a new pattern or belief from BRANCH, a new
version in a chain from EVOLVE, a contradiction record, a person record.
**FR-S5-12** Which candidates were retrieved and **not** chosen, so a wrong connection can
be understood as a choice between options rather than an inexplicable jump. This is in the
Stage 2 payload today, reachable only through the run.
**FR-S5-13** Where a gate fired in code after the model answered — a tie, a trial-vs-trait
block, a change with no cause, below-threshold — say which gate and what it did.
**FR-S5-14** Where an item was set aside for a person, link to its review queue entry.
**FR-S5-15** Every decision links to its audit record and its rollback pointer.

**Backed by:** `GET /graph/episodes/{id}` and `GET /debug/episodes/{id}/source` cover
FR-S5-1 to FR-S5-7. **FR-S5-8 to FR-S5-15 have no endpoint.** `get_episode_contents`
follows containment edges only, so the response contains no decision records, no
reconciliation edges and none of the historical records that were connected to. This is
the single largest **Needs API** in this document (API-3).

### S6 — Graph explorer

**FR-S6-1** Explore the graph from a starting record: what is within a few steps, and the
links between.
**FR-S6-2** Node kinds are visually distinguishable by a rule that covers all fifteen kinds,
not a hand-listed four. (`Technical_HLD.md` §7.2 colour-codes four; the schema has fifteen
node tables.)
**FR-S6-3** Open any record for its full detail, its version history and its decision
history.
**FR-S6-4** Filter by kind, date range, domain, signal strength and era.
**FR-S6-5** A time control: the graph as it stood on a past date, with links that were live
then still shown even if a later rollback withdrew them.
**FR-S6-6** When a limit or the three-hop depth cap cut the answer short, say so on the
picture (P5). A partial graph drawn as complete is a wrong answer that looks right.
**FR-S6-7** Depth is capped at three hops and the UI must not offer more. Past that a
well-connected graph is mostly reachable from anywhere in it, so a deeper walk is the whole
history fetched by accident.
**FR-S6-8** On a phone, this surface needs a genuinely different presentation — a
force-directed WebGL graph is the least mobile-friendly thing in the spec. A list- or
tree-shaped view of the same slice satisfies FR-D2; a shrunken canvas does not.

**Backed by:** `GET /graph/stats`, `/graph/nodes`, `/graph/nodes/{id}`, `/neighbors`
(with `as_of`, `include_invalidated`, `truncated`), `/versions`, `/decisions`,
`/graph/chains/{id}`. Text search over records is **Needs API**.

### S7 — Review queue

**FR-S7-1** One card per item needing a person, showing what was extracted, what was
retrieved, and what the system proposed.
**FR-S7-2** For an AMBIGUOUS tie: both candidate actions side by side, the candidate
records for each with their own words and dates, the specific difference, and three
resolutions — take the first, take the second, or create new.
**FR-S7-3** Resolvable in one tap on a phone. This is the surface most likely to be used
standing up, and the roadmap has always called it mobile-first, one-tap.
**FR-S7-4** A pending count visible from anywhere in the app.
**FR-S7-5** Snooze, and a visible age — items auto-resolve after 7 days and that must not
be a surprise.
**FR-S7-6** Show the queue cap (default 40) and what happens when it is full.
**FR-S7-7** Items that failed extraction cannot currently be queued at all (the queue
requires an audit record and an extraction failure has none). Until that changes they must
be reachable from the episode instead (FR-S5-6), not silently absent.

**Backed by:** `GET /hitl`, `GET /hitl/count`, `GET /hitl/{id}`,
`POST /hitl/{id}/resolve`, `POST /hitl/{id}/snooze`, `POST /hitl/sweep` (Goal 18). Each
card arrives with its answers already worked out, so the screen chooses a layout and sends
back which button was pressed — it never composes a graph write. FR-S7-5's snooze hides an
item for 24 hours rather than leaving it in place, and a card reports whether its
recommendation has been overtaken since it was raised, which the screen should show rather
than let a person tap into a conflict.

### S8 — Reports

**FR-S8-1** A list of generated reports by period — 48h shadow, weekly, monthly, quarterly.
**FR-S8-2** Read one report, with every claim linked to the episodes and records it was
drawn from.
**FR-S8-3** Trends over time: pattern frequency, belief change, emotional valence.

**Backed by:** nothing. **Needs API** (Goal 17).

### S9 — Intelligence surfaces

Named here so the shell has room for them, not specified in detail yet: Personal Laws,
Personal Debugger, Decision Simulator, Life Trajectory Viewer, Biographical Gap Detection.

**FR-S9-1** The navigation must accommodate a growing set of these without restructuring.
**FR-S9-2** Each is read-only over the existing graph and needs no new node types.

### S10 — Settings and diagnostics

**FR-S10-1** Theme: system, light, dark. Persisted. (§5.1)
**FR-S10-2** Service health: whether each store answers, shown separately, because a
service that is up but cannot reach its databases is a different problem from one that is
down.
**FR-S10-3** Show the configuration a run actually used, per run — models are a moving
target and a withdrawn model name produces a run that completes having extracted nothing.
**FR-S10-4** Warn about the misconfiguration that costs the most and says nothing: a graph
on disk with an in-memory vector index, which slowly fills with records semantic search can
never find.
**FR-S10-5** Provider and model configuration is **read-only** in the UI. It is a
deployment property read from the environment at process start, never a user setting.

**Backed by:** `GET /health`, and `config_snapshot` on each run. Complete enough today.

### S11 — Sign in

The first screen anybody sees, and the only one reachable without an identity. It is small,
and it is not unimportant: it is the whole first impression of a product whose pitch is that
it can be trusted with the things you would not say out loud.

**FR-S11-1** One route, `/login`, outside the app shell — no sidebar, no navigation, no
review count. A person who is not signed in has nothing to navigate.
**FR-S11-2** **Continue with Google** is the only sign-in method (DEC-A7). No password
field, no email field, no "or sign up with". A second method is a second thing to explain
and there is only one.
**FR-S11-3** Say what Lumen is in a sentence before asking anyone to sign in. A bare Google
button on an unexplained page is where a person stops.
**FR-S11-4** Both themes, phone-first, from the first version. This is the one screen
guaranteed to be seen on an unknown device (FR-XT2, FR-D1).
**FR-S11-5** The round trip to Google and back has a visible in-between state. It is a full
page redirect and a token exchange, and on a slow connection the callback screen is blank
for long enough to look broken.
**FR-S11-6** Sign-in failures are distinguished and said in words, never as "something went
wrong": the person cancelled at Google; Google returned an error; the email is not on the
allowlist (`LUMEN_SIGNUP_MODE`); the `state` did not match; the service is unreachable.
Being turned away by an allowlist is the one most likely to happen and the one a generic
message serves worst — it is not a failure, and it must not read as one.
**FR-S11-7** Where a person was going before being bounced to `/login` is remembered and
returned to after sign-in. **Never** as a URL that carries anything from the journal
(FR-XV3) — a deep link into an episode is fine, a query string with text in it is not.
**FR-S11-8** The whole surface is behind a build-time switch matching the service's
`LUMEN_AUTH_ENABLED`. With auth off, the app opens straight into the shell as it does today.
This is what lets the front end be built before Goal 21 exists, and it must be a real
supported mode rather than dead code — a switch nobody exercises is a switch that does not
work.
**FR-S11-9** Signed in, the current person is visible and unambiguous — name or email in
the shell, not only an avatar. On a system where every screen is one person's private
history, "whose data am I looking at" may never require a hover.
**FR-S11-10** **Sign out** is reachable from the shell, ends the session at the service and
not only in the browser, and clears every cached response on the way out. A cached episode
surviving a sign-out on a shared laptop is the failure this exists to prevent.

**Backed by:** nothing. Entirely **Needs API** (API-11, Goal 21).

---

## 5. Cross-cutting requirements

### 5.1 Theme

**FR-XT1** Two complete themes: dark and light. Dark is the default and the reference —
the look to aim for is a real product's dark mode (ChatGPT's is the stated reference), not
a dimmed light theme.
**FR-XT2** Light is an equal, not an afterthought. Every surface, every state, every chart
and the graph explorer must be designed and reviewed in both. A screen that only works in
one is not done.
**FR-XT3** Follow the system setting by default; an explicit choice overrides it and
persists across sessions and reloads.
**FR-XT4** No flash of the wrong theme on load.
**FR-XT5** Colour is defined once as tokens. No component hard-codes a colour, and no
colour is defined only inside a dark-mode block.
**FR-XT6** Status colours must be distinguishable in both themes and must never be the only
carrier of meaning (P4).

### 5.2 Mobile and responsive

**FR-XM1** Nothing scrolls horizontally at the page level, ever. Wide content — tables,
payloads, graphs, code — scrolls inside its own container.
**FR-XM2** The harness is table-heavy and tables are the main thing that breaks on a phone.
Every table needs a defined narrow-screen form (typically a stacked card per row) that
loses no column.
**FR-XM3** Touch targets are large enough to hit while walking; primary actions sit within
thumb reach.
**FR-XM4** Respect safe-area insets and on-screen keyboards. The composer must never be
covered by the keyboard.
**FR-XM5** Long transcripts and long payloads must not make the page unusable — the harness
already discovered that thirty messages ran to seventy thousand pixels.
**FR-XM6** Layout breakpoints are a design decision, but the *content* at every width is the
same. Nothing is dropped to make a phone fit.

### 5.3 Accessibility

**FR-XA1** Keyboard reachable throughout, with visible focus.
**FR-XA2** Contrast meets WCAG AA in both themes.
**FR-XA3** Screen-reader labels on every control; live regions for streaming replies and
for status that changes on its own.
**FR-XA4** Respect reduced-motion preferences.
**FR-XA5** Journal text is rendered as text, never as markup — a safety requirement as much
as an accessibility one, since an export can contain anything.

### 5.4 The meaning of empty, loading and failed

**FR-XS1** Every list distinguishes: loading, empty because nothing exists yet, empty
because a filter excluded everything, and failed to load. Four states, four messages.
**FR-XS2** Retrieval's four outcomes stay distinguishable (P5).
**FR-XS3** A cut-short answer says it was cut short, with the limit that cut it.
**FR-XS4** An error says what failed and what still works. Losing the transcript must cost
the page its transcript and nothing else.
**FR-XS5** Long operations show real progress, not a spinner. An import is several model
calls and takes minutes.

### 5.5 Live updates

**FR-XR1** Streamed assistant replies, token by token.
**FR-XR2** Pipeline progress without a manual refresh: stage completion, run finished,
review count changed.
**FR-XR3** Polling is acceptable where it is honest and cheap; the import page already does
this well. Live updates must degrade to polling rather than breaking.
**FR-XR4** A dropped connection is visible and recovers on its own.

### 5.6 Performance

**FR-XP1** First meaningful paint fast enough to open on a phone and start writing.
**FR-XP2** The retrieval budget is the backend's — **8 seconds** since Goal 15, which raised
it from 3 deliberately: a brief pause before a considered reply reads as thought, while an
answer that missed the one relevant thing reads as nothing. The UI must not add a felt pause
of its own on top of it, and must make an 8-second wait feel like the assistant thinking
rather than like the app having stalled.
**FR-XP3** Lists are paginated against the API's 200-record cap, never fetched whole.
**FR-XP4** The graph explorer stays interactive at the scale a few years of journalling
produces.

### 5.7 Privacy and sensitivity in the UI

**FR-XV1** Records held back until the subject is raised are named as held back, never
silently dropped (P7).
**FR-XV2** During a `CRISIS` or `VULNERABLE` register the interface does not decorate,
annotate or interrupt the conversation.
**FR-XV3** Nothing sensitive lands in a URL. Journal text is never a query parameter.
**FR-XV4** The "show the working" mode is a development affordance and must be
unmistakably marked as one.

### 5.8 Contracts

**FR-XC1** Types come from the service's OpenAPI schema, generated, not hand-written. The
project's rule that every boundary crossing is schema-validated does not stop at the HTTP
edge.
**FR-XC2** A field the front end needs is added to a response model in the API, never
inferred or reconstructed client-side.
**FR-XC3** CORS on the service and a configurable base URL in the client are both firm
requirements, not niceties — under DEC-2 the browser talks to FastAPI directly from a
different origin, and nothing sits in between to paper over it. DEC-5 sharpens this: the
refresh cookie is cross-origin, so requests carrying it must be sent with credentials and
the service must name exact origins. A wildcard is not merely lax in this combination —
browsers reject it outright.

### 5.9 Identity and session

**FR-XI1** The access token lives in memory only. Never `localStorage`, never
`sessionStorage`, never a non-httpOnly cookie. Anything a script on the page can read is
readable by any script that gets onto the page.
**FR-XI2** The refresh token is never touched by application code at all. It is an httpOnly
cookie; the front end's only involvement is sending credentials with requests to `/auth/*`.
**FR-XI3** A `401` triggers exactly one silent refresh attempt, and concurrent requests that
all `401` at once wait on that single attempt rather than each starting their own. If it
fails, the session ends and the person is sent to `/login` — once, not once per in-flight
request.
**FR-XI4** An expiring token must never interrupt writing. A refresh that lands mid-message
is invisible; a composer that loses a half-written entry to a token expiry is the worst
possible moment for this to be noticeable.
**FR-XI5** Session loss during a streamed reply is handled, not ignored — a WebSocket
outliving its token is a live case (OQ-A3), and the reply must not simply stop with no
explanation.
**FR-XI6** Every cache, store and query client is scoped to the signed-in user and cleared
on sign-out and on user change. Two accounts on one browser may never see each other's
records, including for the fraction of a second before a refetch lands.
**FR-XI7** Signed out — deliberately, or by an expired session — no journal content remains
in memory, in a cache, or in the DOM.
**FR-XI8** Being signed out mid-session is explained. "Your session expired, sign in again"
is a different message from "we could not reach the service", and a person who has just lost
what they were reading deserves to know which.
**FR-XI9** No token, code or `state` value appears in a URL, a log, or an error surface
(FR-XV3).

---

## 6. What the backend must add

Ordered by how much they block. Each needs a decision about which goal owns it.

| # | What is needed | For | Notes |
|---|---|---|---|
| API-1 | Send a turn, get a streamed reply | S1 | Goal 16 / 20. The chat surface does not exist without it. |
| API-2 | Append a live turn to the session buffer; end a session explicitly | S1, S4 | The ingestion layer has always described a manual "End Session"; only file upload has an endpoint today. |
| API-3 | A reconciliation-shaped read of an episode | S5 | Decisions, reconciliation edges, and the historical records that were connected to — with their text. `get_episode_contents` follows containment edges only. |
| API-4 | Trigger attribution on runs, and pending runs | S4 | So one list can cover imports and live sessions, and so a buffering session is visible before it starts. |
| API-5 | Resolve ids to what the record says, in run output | S4 | Or a batch "describe these ids" read the front end can call. Either way, P6 is not satisfiable today. |
| API-6 | Review queue: list, count, detail, resolve, snooze, sweep | S7 | **Built** (Goal 18). |
| API-7 | Reports: list, read, trends | S8 | Goal 17. |
| API-8 | A day/calendar index | S2 | Cheaper and more honest than filtering episode lists client-side. |
| API-9 | Text search over records | S6 | Hybrid search exists inside retrieval; nothing exposes it. |
| API-10 | Re-run a stage | S4 | `rerun_from_stage` is unimplemented. Until then the button is absent, not dead. |
| API-11 | Sign in, refresh, sign out, current user | S11, all | Goal 21. `/auth/google/start`, `/auth/google/callback`, `/auth/refresh`, `/auth/logout`, `GET /auth/me`. Specified in `Auth_Architecture.md` §3.1. |
| API-12 | Every other endpoint scoped to the signed-in user | all | Goal 22. Not a new endpoint — a change to all of them. Today every response is the single configured user's, whoever asked. |

---

## 7. Stack constraints

The library choices are already committed in `Technical_HLD.md` §2.6 and §7.1: Next.js 15
(App Router), TypeScript, shadcn/ui + Radix, Tailwind, Zustand, react-force-graph,
Recharts, WebSockets. Treat that as the standing decision. This document adds the
constraints those choices must satisfy, and flags where the existing spec contradicts
itself.

One consequence of DEC-2 to carry into the design document: with no BFF, Next.js is being
used as a React framework — routing, rendering, build — and not as a server tier. Whether
that still justifies Next.js over a plain React setup is a fair question for the design
document to answer rather than inherit.

**FR-XL1** Whatever is chosen must support both themes as tokens, not as a bolt-on.
**FR-XL2** Component primitives must be unstyled or fully restyleable. We are matching a
specific look, not inheriting a library's.
**FR-XL3** The graph visualisation must have a non-canvas fallback for phones (FR-S6-8).
**FR-XL4** No dependency may make a surface desktop-only (FR-D3).
**FR-XL5** The foundation must absorb S7, S8 and S9 arriving later without restructuring.

### Discrepancies to resolve before the design document

1. ~~**Two BFFs.**~~ **Resolved by DEC-2.** §3.1 named FastAPI as the BFF and gateway while
   §7.1 put BFF logic in Next.js `app/api/*/route.ts`. The front end calls FastAPI
   directly; the `app/api/` route handlers in §7.1 are dropped. `Technical_HLD.md` §7.1
   needs amending to match — a doc still describing a layer we have decided not to build
   is the kind of discrepancy this project treats as a bug report.
2. ~~**Auth that does not exist.**~~ **Resolved by DEC-5.** §7.1's `(auth)/login` route
   stays, and becomes real: S11, backed by API-11, owned by Goal 21, specified in
   `Auth_Architecture.md`. DEC-2 was the sharpening argument — with no BFF in front of it,
   protecting the service is the service's own concern, which is exactly what
   Goals 21 and 22 build. `Technical_HLD.md` §11 decision 5 (Clerk) is withdrawn.
3. **Four node colours for fifteen node kinds** (§7.2). Needs a rule, not a list.
4. **`react-force-graph` on a phone.** Named as the graph choice with no mobile story.
5. **Voice.** §11 decision 3 says text-first with voice as progressive enhancement; the
   transcription provider is a Protocol with no implementation. FR-S1-8 treats it as a
   placeholder — confirm that is right.
6. **PWA / offline.** §11 decision 4 defers it. This document keeps it deferred (§8).

---

## 8. Out of scope for now

Named so they are decisions rather than omissions: offline use and PWA installation;
sharing a graph with another person, or export-to-others; roles, permissions, teams or any
admin surface; a second sign-in method, password reset or MFA (DEC-A7); session and device
management beyond sign-out; editing or deleting graph content directly; changing provider or
model configuration from the UI (FR-S10-5); voice input and spoken replies beyond layout
space; native mobile apps; anything that writes to the graph outside the review queue.

*Withdrawn from this list by DEC-5:* multi-user and accounts. They were out of scope when
this document was drafted and are now S11, §5.9, API-11 and API-12.

---

## 9. Decisions and open questions

### 9.1 Decided

**OQ-5 → DEC-1. Inspect surfaces first.** Runs, Episode detail and Graph explorer lead.
Three reasons: they are mostly buildable against the API as it stands today; they are where
the reconciliation requirement lives, which is the sharpest known gap; and reaching parity
with them is what allows `/ui` to be deleted. Chat is gated on API-1 and API-2 and follows.

Consequence for the design document: the foundation gets stressed by the *harder* audience
first. Dense tables, deep payload trees, graph slices and both themes all have to work
before a single chat bubble is drawn. That is the right order for a foundation, and it means
the design document must specify the chat surface anyway — the shell cannot be designed
around inspect alone and then asked to hold a conversation later.

**OQ-2 → DEC-2. The front end calls FastAPI directly.** No Next.js BFF. Consequences:
CORS and a configurable base URL become firm requirements (FR-XC3); every screen's data
must be answerable by a real endpoint, so "the BFF will reshape it" is not available as an
escape hatch — which is why §6 is as long as it is; and `Technical_HLD.md` §7.1 needs
amending to drop `app/api/`.

**OQ-3 → DEC-3. One application, separated navigation.** Reflect and inspect are distinct
sections of one shell. The shared design system, shell and generated types are the win. The
risk this accepts is inspect complexity leaking into the calm surfaces, and P1/P2/P3 are the
defence — the design document has to show the navigation boundary explicitly rather than
letting the two sides interleave.

**OQ-6 → DEC-4. `frontend/` beside `lumen/`, in this repository.** Own build, own deploy,
separate codebase in every sense that matters — but one commit can change an endpoint and
its caller together, and generated OpenAPI types (FR-XC1) need no publishing step.
`Technical_HLD.md` §3.2 already sketches exactly this layout.

**OQ-1 → DEC-5. There is a login, and Lumen is multi-user by design.** Not a login bolted
onto a single-user app — a real identity per person, with a graph and a vector collection
per person behind it. Google is the only sign-in method; Lumen issues its own JWTs and
treats Google purely as an identity provider. The full design, including why Clerk was
withdrawn, is `docs/hld/Auth_Architecture.md`; the build is Goals 21 and 22.

Consequences for this document: a new surface (S11), a new cross-cutting section (§5.9),
two new backend requirements (API-11, API-12), and two removals from §8. Consequence for the
design document: the shell now has a signed-out state and a signed-in identity in it, and
every list, cache and query client is scoped to a person rather than to the process
(FR-XI6). The sequencing is worth stating — auth lands at Goal 21, *after* the surfaces that
consume it, so the front end is built against `LUMEN_AUTH_ENABLED=false` and the login screen
is switched on when the service can answer it (FR-S11-8). That switch is a supported mode,
not scaffolding.

### 9.2 Still open

**OQ-4 — How far does "show the working" go?** A marked developer toggle on the chat
surface, or strictly confined to the inspect surfaces? P3 leans toward the toggle. DEC-3
makes this easier either way, since both live in one app.

**OQ-7 — What replaces `/ui`, and when?** The harness is meant to be deleted. Under DEC-1
that becomes a concrete milestone: parity on S4 and S5 is the condition, and it is worth
naming the goal that deletes it.

---

## 10. How this document changes

Add requirements with new ids; never renumber. When a requirement is dropped, mark it
withdrawn with the reason rather than deleting the line — the reason is usually the useful
part. When a requirement turns out to contradict a backend doc, that is a bug report to
raise, not licence to pick one.
