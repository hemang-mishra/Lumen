# Goal 23: Front-End Foundation & Design System

**Branch:** `goal23`
**Depends on:** Goal 21 (CORS and the sign-in endpoints already exist, so the client can be
built against something real), Goal 11 (the read API the generated types come from)
**Spec:** `docs/frontend/Requirements.md` — DEC-1…DEC-5, FR-D1…FR-D3, FR-XT*, FR-XM*,
FR-XA*, FR-XS*, FR-XC*, FR-XI*, FR-XL*. `docs/frontend/Design_Language.md` — DL-1…DL-58 and
the §13 review checklist. `implementation/Master_Plan.md` Phase 7.

---

# SECTION A — LOGIC (please verify)

## Objective

Everything anybody has seen of Lumen so far is a test harness: seven hand-written HTML pages
at `/ui`, dark-only, table-heavy, written to make the machinery visible while it was being
built and always intended to be deleted. Nine real screens arrive over Goals 24–32.

This goal builds **none of those screens**. It builds the thing they are all made of: the
project, the vocabulary of colour and space and type, the dozen or so parts every screen
reuses, the frame they sit in, and the one piece of code that talks to the Python service.

That is worth its own goal for the same reason Goal 1 was: nine screens built on nine
slightly different foundations is nine times the work and one inconsistent product. The
cost of getting this wrong is not visible until Goal 28, which is exactly when it is
expensive to fix.

## A1. What Gets Built

| | What it is |
|---|---|
| **The project itself** | `frontend/` beside `lumen/`, with its own build, its own tests, its own dependencies. One repository, two codebases (DEC-4). |
| **A vocabulary of style** | Every colour, size, space, corner, shadow and duration named once, in both themes. A screen cannot invent a colour; it can only ask for one by role — "the raised surface", not "grey number 800". |
| **Two themes that stay equal** | Light and dark, following the person's system setting until they choose, remembered afterwards, and never the wrong one for a flicker on load. Light is written first and dark redefines the same names, which is the one rule that stops light quietly rotting while everyone develops in dark. |
| **Two densities** | Comfortable for reflect surfaces, compact for reading a thirty-stage pipeline trace — and compact never on a touch screen, whatever the screen is showing. |
| **The reusable parts** | Three kinds of button, inputs, chips, the list container that knows all four ways a list can be empty, the table that becomes cards on a phone without dropping a column, disclosure, the payload block, the sheet-or-dialog, and the **record line** — the pattern that answers "an id is not an answer" everywhere in the app. Each designed for a phone at the same moment as for a desktop, not afterwards. |
| **The frame** | Navigation that keeps reflecting and inspecting apart, a phone form of it, and a single list of sections that later goals add to rather than restructure. |
| **The one way to talk to the service** | A typed client whose types are **generated from the Python service's own schema**, so the two codebases cannot drift apart silently. It holds the session, renews it quietly when it expires, and turns the service's errors into things a screen can say in words. |
| **A room where everything is on display** | One page showing every part in every state, which is how a foundation goal proves itself when it has no product screen to point at. |

## A2. The Decisions You Took

**1. Plain React with Vite, not Next.js.** Next.js was chosen when a JavaScript server was
going to sit between the browser and Python. DEC-2 removed that server, and every screen in
Lumen is one person's private history behind a login, so there is nothing a server could
usefully render ahead of time either. What is left is a build tool, and a simpler one does
that job with no server to run in production. `Technical_HLD.md` is amended to say so.

**2. Radix for behaviour, our own components on top.** We take the accessible, unstyled
behaviour — a dialog that traps focus properly, a menu that works on a keyboard — and write
the ten or so components the design document actually names ourselves. shadcn/ui would give
us a faster first draft and its own competing vocabulary of colour names, which we would
then spend the rest of the project renaming.

**3. TanStack Query for anything that comes from the service, Zustand for local state.**
Caching, background refresh while an import runs, retry, and the single instruction that
throws away every cached response at sign-out are all things this is for. Hand-writing them
across nine screens is right eight times and wrong on the ninth, and the ninth is one person
seeing another person's journal.

**4. The client handles the session now, the login screen comes in Goal 31.** The client
holds the short-lived token in memory only, and when the service says it has expired it
renews once and retries — with every other request that failed at the same moment waiting on
that single renewal rather than starting its own. Building this later would mean changing
how every request is made after ten screens already depend on it.

**5. The app follows the system's light-or-dark setting.** Requirements contradicted
themselves here; the answer is that a person whose laptop is in light mode opens Lumen in
light mode. "Dark is the reference" survives as a design instruction — it is the theme we
design against first — rather than as a forced starting state. `FR-XT1` is amended.

**6. Navigation shows only what exists.** The whole map of sections is written once in a
single file with a "ready yet" mark against each. On day one the navigation is nearly empty,
which is honest; each later goal flips one mark and its screen appears in the right place.

## A3. Judgement Calls (flagging, not asking)

- **The kitchen-sink page ships in every build, unlisted.** It is how the design system is
  reviewed, and a page that only exists in development is a page that breaks in production
  without anyone noticing. It is not in the navigation and is not linked from anywhere.
- **No screen in this goal fetches anything real.** The client is built and fully tested
  against a fake service; the first screen to make a real request is Goal 25's import
  surface. This keeps a foundation goal from quietly becoming the first product goal.
- **Icons come from one existing set** rather than being drawn, configured once to the
  single weight and size the design document specifies, so no component can choose its own.
- **The address of the service is a build-time setting**, one build per environment. A
  runtime lookup would buy one build for all environments and cost a request before the app
  can start, which is the opposite of what FR-XP1 asks for.
- **Live updates over a socket are not built here.** The typed *shape* of what a socket
  sends is generated alongside everything else, so Goal 25 has it waiting; the connecting,
  reconnecting and degrading-to-polling belongs with the first screen that needs it.

## A4. Discrepancies Found in the Documents

Per the project convention these are raised, not silently resolved — each is amended in this
goal's change.

0. **The palette had text below the contrast floor, and one rule admitted it.** Found while
   building: `DL-12` said outright that `--text-tertiary` "does not meet AA", which
   contradicts `FR-XA2` — and an id nobody can read is not quiet, it is missing. The same
   pass found `--positive` and `--caution` failing in light once a filled chip put them on
   their own faint tint, where a word and its background are the same hue. Both greys and
   both colours were darkened until they clear 4.5:1 everywhere they are used.
   **DL-12 is amended**, and the palette is now checked by a test rather than by eye.

1. **The design language names two different things `--text-*`.** §4.1 uses `--text` and
   `--text-secondary` for *colours*; DL-19 uses `--text-body` and `--text-reading` for *font
   sizes*. In one stylesheet these collide. The colour names stay as they are — they are used
   far more often — and the type scale is renamed to `--type-*`. **DL-19 is amended.**
2. **`Technical_HLD.md` still describes a front end we have decided not to build** —
   Next.js as a full-stack tier with `app/api/*` route handlers (§2.6, §7.1, §11). The
   Requirements document already flagged this as unresolved and asked the design work to
   answer it rather than inherit it. Amended: Vite + React, no server tier, and the stack
   table gains TanStack Query and loses shadcn/ui.
3. **FR-XT1 and FR-XT3 disagree about the default theme.** Resolved above; FR-XT1 amended.
4. **`Technical_HLD.md` §7.2 colour-codes four node types where the schema has fifteen**,
   which DL-16 already answers with a rule (colour encodes state, never taxonomy). The HLD
   line is withdrawn so two documents stop giving opposite instructions.

## A5. What Is Deliberately Not Built

| Not built | Why |
|---|---|
| Any product screen | Goals 24–32 own them. A foundation that also ships a surface is a foundation designed around one surface. |
| The login screen | Goal 31. The *session handling behind it* is built here (A2.4); the screen is not. |
| The graph visualisation | Goal 27, including its phone form. Nothing about it changes the foundation. |
| Charts | Goal 30. |
| Deleting `/ui` | Goal 32, and only once the surfaces that replace it exist. |
| A deployment pipeline | The build produces static files; where they are hosted is a deployment decision this goal does not need to make. |
| Offline use, installability | Out of scope by Requirements §8. |

## A6. How You'll Know It Works

1. Every part of the design system is visible on one page, and that page is asserted in
   **both themes, at both densities, and at 375 pixels wide** — the review checklist run as
   a test rather than as a promise.
2. Switching theme changes every surface; reloading keeps the choice; and there is no flash
   of the wrong theme in between, proven rather than eyeballed.
3. A phone-width run shows every table as cards with **no column missing**, and nothing on
   the page scrolls sideways.
4. Keyboard alone reaches everything, focus is always visible, and an automated
   accessibility pass finds nothing on the kitchen-sink page in either theme.
5. With motion reduced, nothing animates.
6. **The drift check does its job:** change a response model in the Python service without
   regenerating, and the test suite fails. This is the one guarantee that keeps two codebases
   honest with each other, so it is proven by breaking it on purpose.
7. Ten requests failing at once because a session expired produce **one** renewal, and all
   ten then succeed. A renewal that fails signs the person out once, not ten times.
8. Signing out leaves nothing behind: no cached response, no token, nothing on screen.

---

# SECTION B — LOW-LEVEL DESIGN

## B1. Directory Layout

```
frontend/
├── index.html                   ← the pre-paint theme script lives here
├── package.json  vite.config.ts  tsconfig.json  .env.example
├── eslint.config.js  prettier.config.js  playwright.config.ts
├── openapi.json                 ← committed; dumped from the Python app
├── src/
│   ├── main.tsx  App.tsx
│   ├── styles/
│   │   ├── tokens.css           ← DL-8…DL-10: light base, dark override
│   │   ├── density.css          ← DL-40…DL-43
│   │   └── base.css             ← reset, :focus-visible, reduced motion
│   ├── api/
│   │   ├── schema.d.ts          ← generated, never edited
│   │   ├── sockets.d.ts         ← generated from x-lumen-socket-events
│   │   ├── client.ts            ← openapi-fetch instance + middleware
│   │   ├── session.ts           ← in-memory token, single-flight refresh
│   │   ├── errors.ts            ← envelope → LumenError
│   │   └── query.ts             ← QueryClient, key factory, cache reset
│   ├── theme/                   ← ThemeProvider, useTheme, density resolution
│   ├── state/                   ← Zustand stores (ui only)
│   ├── components/              ← the primitives (B5)
│   ├── patterns/                ← RecordLine, PayloadBlock, StateBoundary
│   ├── shell/                   ← AppShell, Nav, MobileNav, sections.ts
│   ├── lib/                     ← formatters, ids, copy-to-clipboard, cn()
│   ├── routes/                  ← router config + KitchenSink route
│   └── test/                    ← msw handlers, render helpers, fixtures
└── e2e/                         ← Playwright journeys + axe
```

Backend files touched: `lumen/api/schema_dump.py` (new), `lumen/api/events.py` and
`lumen/api/routes/chat.py` (export their frame kinds as constants),
`lumen/tests/test_api_openapi.py` (new), `.env.example`, plus the four doc amendments in A4.

## B2. Stack

| Concern | Choice | Note |
|---|---|---|
| Build | Vite 6 + React 19 + TypeScript (`strict`, `noUncheckedIndexedAccess`) | A2.1 |
| Routing | React Router 7, library mode | No loaders/actions — data is TanStack Query's job, one mechanism not two |
| Styling | Tailwind CSS v4 | `@theme inline` maps our CSS variables into utilities, so runtime theme swapping and utility classes are the same tokens |
| Behaviour | Radix primitives, per-package | Dialog, Popover, DropdownMenu, Tooltip, Collapsible, Tabs, Switch, VisuallyHidden |
| Server state | TanStack Query v5 | A2.3 |
| Local state | Zustand v5 | theme choice, nav open, kitchen-sink controls |
| HTTP | `openapi-fetch` + `openapi-typescript` | Types generated, no generated client code to review; middleware is the seam for auth |
| Icons | `lucide-react`, wrapped | One wrapper fixes size 20 / stroke 1.5 / `currentColor` (DL-51) |
| Unit tests | Vitest + Testing Library + jsdom + MSW | |
| Journeys | Playwright + `@axe-core/playwright` | |

## B3. Tokens — `styles/tokens.css`

Two layers, and the separation is what makes DL-9 mechanically enforceable:

```css
:root {                       /* light: the base definition of every name */
  --canvas:#fbfaf9; --surface:#fff; --surface-raised:#fff; --surface-sunken:#f3f1ef;
  --border-hairline:rgba(0,0,0,.07); --border:rgba(0,0,0,.13);
  --text:#1c1d20; --text-secondary:#5c5f66; --text-tertiary:#8a8e96;
  --accent:#4a5bd4; --accent-contrast:#fff; --accent-quiet:rgba(74,91,212,.10);
  --positive:#1e7d4f; --caution:#a35a00; --critical:#b3261e; --info:var(--accent);
  --state-hover:rgba(0,0,0,.04); --state-press:rgba(0,0,0,.08);
  --radius-chip:6px; --radius-control:10px; --radius-card:14px;
  --radius-sheet:20px; --radius-pill:999px;
  --shadow-1:…; --shadow-2:…;
  --space-1:4px … --space-24:96px;          /* 2 4 8 12 16 20 24 32 40 56 72 96 */
  --type-meta:12px/1.4 … --type-page:24px/1.25;   /* renamed per A4.1 */
  --dur-micro:120ms; --dur-standard:180ms; --dur-large:260ms;
  --ease-enter:cubic-bezier(.2,0,0,1); --ease-exit:cubic-bezier(.3,0,.8,.15);
}
:root[data-theme="dark"] { /* redefines the same names, defines no new ones */ }
```

Enforced, not trusted:

- A **Vitest token test** parses `tokens.css` and asserts (a) every name defined under
  `[data-theme="dark"]` also exists on bare `:root` — DL-9 as a failing test — and (b) the two
  blocks define exactly the same set of names.
- An **ESLint rule** (`no-restricted-syntax` on raw colour literals plus a Tailwind arbitrary-
  value ban) fails any component containing `#hex`, `rgb(`, or an off-scale `px` spacing value.
- `--shadow-*` resolves to `none` in dark (DL-31); elevation there is surface lightness plus
  an optional hairline.

## B4. Theme and Density at Runtime

**No flash (FR-XT4).** A ~10-line inline script in `index.html`, before any stylesheet, reads
`localStorage["lumen.theme"]` (`system` | `light` | `dark`), resolves `system` against
`matchMedia`, and sets `data-theme` plus `color-scheme` on `<html>`. React later adopts that
value rather than re-deciding it. Playwright asserts the attribute is correct on the very
first paint by evaluating before `load`.

**Density (DL-40…DL-43).** `data-density` on the shell container, inherited by CSS. Resolved
by a rule, not a preference: reflect sections are always `comfortable`; inspect sections are
`compact` only when `matchMedia('(pointer: fine)')` matches — a phone rendering a run trace
stays comfortable (FR-D2, FR-XM3). Not a user setting (DL-43). The rule lives in one function
with its own unit tests over stubbed media queries.

## B5. The Primitives

Each is one file, one export, a narrow-screen form designed in the same commit, and a
kitchen-sink entry covering every state.

| Component | Shape | Rules it carries |
|---|---|---|
| `Button` | `variant: primary｜secondary｜ghost`, `tone: default｜critical`, `size` | DL-44; destructive is never a red fill; 44/32px by density (DL-35) |
| `IconButton` | icon + required `label` | never hover-only (DL-36) |
| `Input` / `Textarea` / `Field` | label, description, error, `aria-describedby` wiring | FR-XA3 |
| `Chip` | outline, optional `tone` from DL-16; `filled` only for "needs you" | DL-45 |
| `StateBoundary` | `{ status, empty, filteredEmpty, failed, children }` | DL-46 / FR-XS1 — **four distinct sentences, required by the type**; a missing one is a compile error |
| `DataTable` | columns declared once; renders `<table>` ≥768px, a card list below | DL-47 / FR-XM2 — the card form is derived from the same column list, so a column cannot be dropped |
| `Disclosure` | Radix Collapsible, chevron, 180ms, label states what is inside | DL-48 |
| `PayloadBlock` | sunken, mono 12px, scrolls in place, caps ~340px with an expand | DL-49 / FR-XM5 |
| `Overlay` | Radix Dialog; bottom sheet <768px, centred dialog above | DL-50 |
| `Icon` | lucide wrapper, fixed size/stroke | DL-51 |
| `Note` | one line of caution/critical text with a word beside the colour | DL-57 / FR-XS3 / P7 |

**`RecordLine` (DL-52)** is the pattern with the most product value and gets the most care:

```tsx
<RecordLine
  says={string}                    // the record's own words — the heading, always
  meta={Array<string｜undefined>}   // middot-joined, undefined dropped
  id={string}                      // last, quiet, mono, click-to-copy
  href?={string}
/>
```

Its type makes `says` required and non-empty, so an id can never become the primary label —
the exact failure P6 exists to prevent. Copy uses `navigator.clipboard` with a
`document.execCommand` fallback and an accessible confirmation.

## B6. The Shell

`shell/sections.ts` is the single list (A2.6):

```ts
type Section = {
  id: string; label: string; icon: IconName;
  group: 'reflect' | 'inspect' | 'system';
  path: string; ready: boolean; goal: number;   // goal that turns it on
};
```

Navigation renders only `ready` entries, grouped, with reflect and inspect visually separated
(DEC-3, P2) and a `system` group pinned at the bottom for settings. A test asserts every
`ready: false` entry has no route registered — so an unbuilt section cannot be reached by
typing its URL. Desktop is a persistent sidebar; below 768px it is a bottom bar for the
reflect group plus a sheet for the rest, with safe-area insets honoured (FR-XM4). Room for a
review count badge (FR-S7-4) and the signed-in person's name (FR-S11-9) exists as slots that
render nothing until their goals fill them (FR-XL5).

Content widths: 720px reading, 1200px inspect (DL-26), gutters 16/24/32 (DL-25).

## B7. Types Generated From the Service

**Step 1 — the schema comes out of Python.** `lumen/api/schema_dump.py`:

```python
def build_schema() -> dict      # create_app(canonical_config()).openapi()
def canonical_config() -> AppConfig
```

`canonical_config()` constructs `AppConfig` with every `LUMEN_*` variable cleared from the
environment and ingest explicitly enabled, so the schema is a function of the code alone —
`create_app` mounts the ingest router conditionally, and a developer's local `.env` must not
change what the front end is typed against. `create_app()` opens nothing: the stores are
opened in the lifespan, so dumping needs no databases.

It also writes `x-lumen-socket-events`: the frame kinds for `/chat/ws` and `/events/ws`,
exported as constants from the two route modules so there is one source of truth. FastAPI
does not describe websockets, and hand-copying those strings into TypeScript is precisely
how the two sides drift.

```bash
uv run python -m lumen.api.schema_dump --out frontend/openapi.json
```

**Step 2 — TypeScript comes out of the schema.** `npm run types:generate` runs
`openapi-typescript` over the committed `openapi.json` into `src/api/schema.d.ts` and
`sockets.d.ts`. Both are committed and marked generated; ESLint forbids editing them.

**Step 3 — the drift check, in two halves (FR-XC1).**

- `lumen/tests/test_api_openapi.py` compares `build_schema()` against the committed
  `frontend/openapi.json` and fails with the regeneration command in the message. A response
  model changed in Python now breaks the **Python** suite, which is where the person who
  changed it is already looking.
- `npm run types:check` regenerates into a temp file and diffs against the committed types.

Together the loop is closed: schema ↔ code, and types ↔ schema. A6.6 proves it by breaking it.

## B8. The Client and the Session

`api/client.ts` — one `openapi-fetch` client, `baseUrl` from `VITE_LUMEN_API_URL`, with three
middlewares in order:

1. **Auth.** Attaches `Authorization: Bearer` from the in-memory token (FR-XI1). Requests to
   `/auth/*` are sent with `credentials: 'include'` — the refresh cookie is httpOnly, path-
   scoped to `/auth`, and application code never reads it (FR-XI2, FR-XC3). Nothing else sends
   credentials.
2. **Refresh on 401 (FR-XI3).** `session.ts` holds `refreshing: Promise<Session> | null`. The
   first 401 starts `POST /auth/refresh`; every concurrent 401 awaits the same promise, then
   retries **once** each. Failure clears the token, empties every cache (B9) and emits one
   `session-ended` event — once, not once per in-flight request (FR-XI8 carries the reason:
   expired session versus unreachable service). With `VITE_AUTH_ENABLED=false` the whole
   middleware is inert and a 401 is an ordinary error, which is what makes FR-S11-8 a real
   supported mode rather than dead code.
3. **Errors.** Parses the service's envelope — `{error, detail, kind?, id?, what?}` — into a
   `LumenError` with a discriminated `kind` (`not_found` | `bad_request` | `unavailable` |
   `not_authenticated` | `forbidden` | `too_many_attempts` | `conflict` | `server` |
   `network`), so a screen can say what failed and what still works (FR-XS4) instead of
   printing a status code. A network failure and a 503 are different objects, because they
   are different sentences.

Nothing from a journal ever reaches a URL: list parameters are allow-listed by the generated
types, and a lint rule bans building query strings by hand (FR-XV3, FR-XI9).

## B9. Cache, Scoped to a Person

`api/query.ts` owns the `QueryClient`, sensible defaults (`staleTime` 30s, one retry, no
retry on 4xx, refetch on reconnect), and a key factory so every key starts with the
signed-in user's id. `resetForUser(userId)` calls `queryClient.clear()` and remounts the
provider by key — cache clearing on sign-out and on user change, including the fraction of a
second before a refetch (FR-XI6, FR-XI7). Under `VITE_AUTH_ENABLED=false` the scope is the
literal `"local"`, so the mechanism is exercised in every test run rather than only after
Goal 31.

## B10. Backend Changes

Small and mostly already done: Goal 21 shipped CORS with exact origins and credentials, so
this goal **verifies** it rather than building it (a test asserting a wildcard is impossible
and that an unconfigured deployment allows nothing). New: `schema_dump.py`, the drift test,
the socket-kind constants, and `.env.example` gaining `LUMEN_ALLOWED_ORIGINS` with the Vite
dev-server origin as the documented example.

## B11. Testing

| Layer | Tool | Bar |
|---|---|---|
| `src/api`, `src/lib`, `src/theme`, `src/state`, view-model logic | Vitest + MSW | **≥90% lines and branches**, enforced as per-glob thresholds so a well-covered component file cannot mask an untested client |
| Components | Testing Library | every state rendered; `StateBoundary`'s four states asserted to differ in wording |
| Journeys | Playwright | the kitchen sink in **light and dark × comfortable and compact × 375px and 1440px**, plus keyboard traversal, a reduced-motion run, and `axe` in both themes |
| Drift | pytest + npm script | B7 step 3 |

Explicit anti-tests, because these are the failures the specification is most afraid of: no
page-level horizontal scroll at 375px (`scrollWidth === clientWidth`); every `DataTable`
column present in the card form; the theme attribute correct before first paint; ten
simultaneous 401s producing exactly one refresh request (asserted by counting MSW hits).

## B12. Order of Work

1. Scaffold, TypeScript config, lint, Tailwind v4, CI-equivalent npm scripts.
2. `tokens.css` + the token tests + the ESLint colour ban. **Nothing else starts before this**
   — it is the constraint every later file is written under.
3. Theme provider, pre-paint script, density resolution.
4. `schema_dump.py`, the pytest drift test, generated types. Early, because the client's shape
   depends on it.
5. Client, session, errors, query client — the half of the front end where coverage means
   something, so it is written test-first.
6. Primitives, in the order the kitchen sink displays them; `RecordLine` and `DataTable` last
   because they are the two with real design in them.
7. Shell, section registry, routing.
8. Kitchen sink, Playwright matrix, axe.
9. The four document amendments (A4) and `Goal_23_Plan.md` results.

## B13. Risks

| Risk | Handling |
|---|---|
| Tailwind v4's `@theme` and runtime-swapped variables interacting badly | Proven in step 2 on a throwaway page before any component exists; the fallback is plain CSS variables with Tailwind used only for layout utilities, which costs nothing already written |
| The generated schema being non-deterministic across machines | `canonical_config()` clears the environment; the pytest comparison is on parsed JSON, not text, so key ordering cannot cause a false failure |
| The foundation being shaped by inspect surfaces alone, then asked to hold a conversation (Requirements §9.1 names this) | The kitchen sink includes the reading-width, `--type-reading`, journal-text and streaming-caret cases even though no chat screen exists yet |
| A primitive being designed for desktop and retro-fitted to a phone | Every component's kitchen-sink entry is asserted at 375px in the same commit that adds it, so "retro-fit later" is not reachable |


---

# SECTION C — WHAT SHIPPED

## C1. Result

**226 front-end tests** (Vitest) and **25 browser journeys** (Playwright, desktop and 375px),
all passing. Coverage **97.6% overall**, with the per-directory bar met everywhere it is
enforced: `src/api` 97.1%, `src/lib` 100%, `src/theme` 100%, `src/state` 100%, `src/shell`
100%. On the Python side, **5,027 tests pass** — the 24 new ones covering the schema dump,
the drift check and the browser's permission to call.

`npm run verify` runs lint, typecheck, the type-drift check and the covered test run in one
command.

## C2. Three things the tests caught that a review would not have

**Unlayered base styles were overriding every colour in the app.** `button { color: inherit }`
sat outside any cascade layer, and an unlayered rule beats every layered one whatever its
specificity — so Tailwind's `text-accent-contrast` lost to it, and the primary button drew
its label in body text on an accent fill: a 3:1 contrast failure on the most prominent
control in the design system. Found by the `axe` pass on the kitchen sink, fixed by moving
the reset into `@layer base`. Nothing about it was visible without measuring.

**Two palette colours failed on their own tint.** See discrepancy 0 above. A caution chip is
caution-coloured text on a caution-coloured background, and that pairing is far closer
together than either colour is to the page.

**A spacing value that was not on the scale.** `gap-12` is 48px, which is not one of the
twelve steps. Caught by the source scan, not by looking at it — which is the point, since
nobody can see 48px versus 40px on a page.

## C3. Departures From the Plan

- **The phone journeys run on Chromium at 375px** rather than on a named iPhone device
  profile, which would have meant a second browser engine and a second download for no
  requirement anybody wrote down. What the specification asks for is 375 pixels, touch, and
  no mouse, and that is what is configured.
- **`Nav` and `MobileNav` take their section list as a parameter.** Not planned, but reading
  a module-level constant made the honest-navigation rule untestable, and a component that
  is handed what it draws is the better shape anyway.
- **Two guards for server-side rendering were removed** rather than tested. There is no
  server rendering in this app — that was the first decision this goal took — so a branch
  for it was a branch that could never be taken.
- **A palette contrast test** was added beyond the plan. `axe` checks the one page every
  component happens to be on; this checks the colours themselves, so a combination is caught
  when it is written rather than when somebody first puts it on screen.

## C4. What the Next Goal Inherits

Goal 24 is a backend goal and needs none of this. **Goal 25** is the first screen, and what
is waiting for it: a typed client whose types cannot drift from the service, a cache already
scoped to whoever is signed in, the four-state list container, the responsive table, the
record line, and one line to change in `src/shell/sections.ts` to put Import and Runs into
the navigation and the router at once.

Left deliberately for the goal that first needs them: the socket client (the message *types*
are generated and waiting), the graph visualisation, charts, and the sign-in screen — whose
session handling is built and tested here, against a service running with sign-in switched
off.
