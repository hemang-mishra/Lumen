# Lumen — front end

The browser half of Lumen. It talks to the Python service over HTTP and nothing else: no
server of its own, no database, no direct access to Kuzu, Qdrant or SQLite.

## Running it

```bash
npm install
cp .env.example .env
npm run dev
```

The service has to be running too, and has to allow this address to call it:

```bash
LUMEN_ALLOWED_ORIGINS=http://localhost:5173 uv run uvicorn lumen.api.main:create_configured_app --factory
```

## The commands

| Command | What it does |
|---|---|
| `npm run dev` | The dev server, on port 5173. |
| `npm run build` | The production build: static files in `dist/`. |
| `npm test` | The unit tests. |
| `npm run test:coverage` | The same, with the per-directory coverage bar enforced. |
| `npm run e2e` | The browser journeys, on a desktop and at 375px. |
| `npm run types:generate` | Regenerate the types from `openapi.json`. |
| `npm run verify` | Lint, typecheck, type drift, and tests with coverage. |

## Types come from the service

Nothing about a request or a response is written by hand here. The service describes itself,
that description is committed, and the types are generated from it:

```bash
uv run python -m lumen.api.schema_dump   # from the repository root
cd frontend && npm run types:generate
```

Both halves are checked. The Python test suite fails if `openapi.json` no longer describes
the service, and `npm run types:check` fails if the generated types no longer match
`openapi.json`. A field renamed in Python breaks one of the two, whichever end the change
was made at.

## Where things are

```
src/styles/      the design tokens, and the only place a colour is defined
src/api/         generated types, the client, the session, the cache
src/theme/       which theme and which density a surface is in
src/components/  the primitives every screen is built from
src/patterns/    the record line and journal text — Lumen's own
src/shell/       the frame, the navigation, and the one list of sections
src/routes/      the router, and the kitchen sink
e2e/             the journeys, run in both themes and at both widths
```

`/kitchen-sink` shows every part of the design system in every state. It is not in the
navigation and is not linked from anywhere, but it ships in every build — a page that only
exists in development is a page that breaks in production without anyone noticing.

## Two rules worth knowing before changing anything

**Every colour and every spacing value comes from a token.** A hex code or an off-scale
spacing utility in a component fails `src/components/styling.test.ts`, and the palette's
contrast is checked in `src/styles/contrast.test.ts` rather than left to review.

**Light is the base definition of every token and dark only redefines them.** A colour that
exists only in the dark block fails `src/styles/tokens.test.ts`. That single rule is what
keeps the light theme from rotting while everyone develops in the dark one.
