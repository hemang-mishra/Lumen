# Identity, Authentication & Multi-User Architecture

**Status:** Goals 21 and 22 are **built**. Authentication, identity, enforcement and
isolation all exist and are tested: every person has their own graph directory and their own
search collection, resolved from who is asking, and the adversarial test that asks every read
surface for somebody else's identifiers gets nothing back.

This document says *what* must exist and *why it is shaped this way*;
`implementation/Master_Plan.md` says *when*, and `Goal_21_Plan.md` and `Goal_22_Plan.md` say
what was actually built and where it diverged.

Lumen today is a single-user system in the only sense that matters: `AppConfig.user_id` is
an environment variable that defaults to `"local"`, every route reads it, and every request
is therefore the same person. Everything else is further along than that sounds. The
operational database has been keyed by `user_id` since Goal 3 — session buffers, pipeline
jobs, imports, the review queue, user settings, the erasure audit all carry the column
already. The graph and the vector index carry nothing.

That asymmetry is the whole shape of this work. Authentication is a week. Isolation is the
part that needs a decision, and this document makes it.

---

## 1. Decisions

| # | Decision |
|---|---|
| DEC-A1 | **Lumen issues its own JWTs.** Google is an identity provider, not the session. A Google ID token is exchanged once, at sign-in, for Lumen's own access and refresh tokens; nothing downstream of `/auth/*` ever sees a Google token. |
| DEC-A2 | **Google Sign-In via the authorization-code flow with PKCE.** The client secret stays on the server. The browser never completes a token exchange. |
| DEC-A3 | **Asymmetric signing (EdDSA / Ed25519), published at a JWKS endpoint.** The production topology extracts Graph, Query and HITL into separate services; asymmetric keys let each verify a token without holding the power to mint one. |
| DEC-A4 | **Short access token in memory, long refresh token in an httpOnly cookie, rotated on every use.** Under DEC-2 the browser talks to FastAPI cross-origin with nothing in between, so the refresh token must be unreadable to JavaScript. |
| DEC-A5 | **Per-user stores.** One Kuzu database directory and one Qdrant collection per user, resolved from the authenticated identity. Isolation is structural, not a `WHERE` clause. |
| DEC-A6 | **Identity is request-scoped; `AppConfig.user_id` survives only as the offline default.** The CLI, the simulation runner and the test suite have no HTTP request to carry an identity, and they should not need one. |
| DEC-A7 | **Google is the only sign-in method at Goal 21.** No passwords, no magic links, no MFA. A password store is a liability we have no reason to accept for a product one person can currently use. |

Superseded: `Technical_HLD.md` §11 decision 5 recommended Clerk for the multi-user phase.
That is withdrawn in favour of DEC-A1 — see §11 of this document for the reasoning.

---

## 2. What a user is

Today `user_id` is a string from the environment. After Goal 21 it is a row.

```
users
  user_id          TEXT PK      opaque, generated, never derived from the email
  email            TEXT UNIQUE  as verified by the identity provider
  display_name     TEXT
  avatar_url       TEXT NULL
  created_at       TIMESTAMP
  last_seen_at     TIMESTAMP
  status           TEXT         ACTIVE | SUSPENDED | ERASURE_PENDING
  token_version    INTEGER      bumped to invalidate every outstanding token at once

user_identities                 one row per external identity linked to a user
  provider         TEXT         GOOGLE
  subject          TEXT         the provider's stable id for this person ("sub")
  user_id          TEXT FK
  email_at_link    TEXT
  linked_at        TIMESTAMP
  PRIMARY KEY (provider, subject)

refresh_tokens
  token_id         TEXT PK      the jti; the token itself is never stored
  user_id          TEXT FK
  token_hash       TEXT         SHA-256 of the secret half
  issued_at        TIMESTAMP
  expires_at       TIMESTAMP
  rotated_to       TEXT NULL    the token that replaced this one
  revoked_at       TIMESTAMP NULL
  user_agent       TEXT NULL
  ip_hash          TEXT NULL    hashed, so a session list is possible without storing IPs
```

Three things about `users` are deliberate.

**`user_id` is opaque and generated.** It is not the email, not the Google `sub`, and not
anything a person can change. It becomes a directory name, a Qdrant collection name, and a
foreign key in seven existing tables. Every one of those makes it effectively permanent, and
the two obvious alternatives are both mutable — people change email addresses, and a Google
subject is stable only for as long as the account exists under that provider.

**Identity is a separate table from the user.** A person is not their Google account. Adding
Apple Sign-In, or letting someone move from a personal to a work Google account without
losing five years of history, is a row in `user_identities` rather than a migration. This
costs one join now and saves a rewrite later.

**`token_version` exists so revocation does not require a token blacklist.** Access tokens
are short-lived and unverifiable against a database by design — that is the point of a JWT.
When something needs to end every session immediately (a lost device, an erasure request,
a suspension), bumping `token_version` makes every outstanding access token fail its next
verification without any lookup table.

---

## 3. Signing in

```
  Browser                    FastAPI                     Google
     │                          │                           │
     │ 1. GET /auth/google/start│                           │
     ├─────────────────────────>│                           │
     │<─────────────────────────┤ authorization URL + state │
     │   (state in httpOnly cookie, PKCE verifier alongside) │
     │                          │                           │
     │ 2. redirect to Google, person signs in                │
     ├──────────────────────────────────────────────────────>│
     │<──────────────────────────────────────────────────────┤
     │   back to the frontend's /auth/callback with ?code&state
     │                          │                           │
     │ 3. POST /auth/google/callback {code, state}           │
     ├─────────────────────────>│                           │
     │                          │ 4. exchange code + secret │
     │                          ├──────────────────────────>│
     │                          │<──────────────────────────┤
     │                          │    id_token + access_token│
     │                          │                           │
     │                          │ 5. verify id_token against Google's JWKS:
     │                          │    signature, iss, aud, exp, email_verified
     │                          │ 6. upsert user_identities → users
     │                          │ 7. mint Lumen access + refresh tokens
     │<─────────────────────────┤                           │
     │  access token in the body, refresh token in an httpOnly cookie
```

**Step 5 is the security boundary and must not be shortened.** A Google ID token is only
proof of anything if its signature is checked against Google's published keys, its `aud`
matches our client id, its `iss` is `accounts.google.com` or `https://accounts.google.com`,
it has not expired, and `email_verified` is true. An unverified-email account is a claim
about an address the person may not control, and accepting it would let someone sign in as
a Lumen user who signed up with that address later.

**`state` is checked and is single-use**, held in an httpOnly cookie set at step 1 and
compared at step 3. Without it, a third party can complete an OAuth flow into someone else's
browser session.

**The PKCE verifier is generated and held server-side**, not by the browser. This deployment
is a confidential client — it has a secret and can keep one — so PKCE here is defence in
depth against a leaked authorization code rather than the primary protection.

### 3.1 Endpoints

| Method | Path | Does |
|---|---|---|
| `GET` | `/auth/google/start` | Returns the Google authorization URL; sets `state` and the PKCE verifier as httpOnly cookies. |
| `POST` | `/auth/google/callback` | Exchanges the code, verifies the ID token, upserts the user, issues tokens. Sets the refresh cookie. |
| `POST` | `/auth/refresh` | Rotates the refresh cookie, returns a new access token. The only endpoint that reads the cookie. |
| `POST` | `/auth/logout` | Revokes the presented refresh token and clears the cookie. |
| `GET` | `/auth/me` | The current user. The frontend's session check on load. |
| `DELETE` | `/auth/me` | Requests erasure. Sets `status = ERASURE_PENDING`, bumps `token_version`, hands off to the Goal 19 erasure procedure. |
| `GET` | `/auth/.well-known/jwks.json` | The public half of the signing keys, for services that verify but never mint. |

Everything under `/auth/*` is rate-limited per IP and per email. Sign-in is the one
unauthenticated write surface in the system.

---

## 4. Tokens

**Access token.** A JWT, EdDSA-signed, 15 minutes, held in JavaScript memory only — never
`localStorage`, never `sessionStorage`, both of which are readable by any script that gets
onto the page.

```json
{
  "iss": "lumen",
  "sub": "usr_9f2c...",
  "aud": "lumen-api",
  "iat": 1770000000,
  "exp": 1770000900,
  "jti": "...",
  "email": "person@example.com",
  "tv": 3
}
```

`tv` is `users.token_version`. Verification compares it against the stored value, which is
the single lookup that makes instant revocation possible; it is one indexed read on a small
table and can be cached for the token's own lifetime.

The claim set is deliberately thin. A JWT is not a profile. Anything richer — display name,
avatar, settings — comes from `/auth/me`, where it can change without waiting for a token to
expire.

**Refresh token.** Opaque, 256 bits of entropy, 30 days, stored hashed. Delivered as
`Secure; HttpOnly; SameSite=None; Path=/auth`. `SameSite=None` is required rather than
chosen: under DEC-2 the frontend and the API are different origins, and `Lax` would not send
the cookie at all. `Secure` is therefore mandatory, and so is an exact-origin CORS allowlist
with `allow_credentials=True` — `allow_origins=["*"]` is not merely lax here, it is rejected
by every browser in combination with credentials.

**Rotation with reuse detection.** Every `/auth/refresh` issues a new refresh token and
marks the old one `rotated_to`. If a token that has already been rotated is presented again,
that is either a stolen token or a race, and the response is the same in both cases: revoke
the entire chain and force a fresh sign-in. This is the one mechanism that turns a stolen
refresh token from a permanent compromise into a detectable event.

---

## 5. How identity reaches the code

Today, thirteen call sites read `config.user_id`. After Goal 21 they read a request-scoped
identity instead, resolved by one FastAPI dependency:

```python
# lumen/api/deps.py

def get_identity(request: Request) -> Identity:
    """
    Who is asking.

    Every route that touches a person's data depends on this rather than on
    configuration. A route that forgot to would be serving whoever the process
    was configured as, which in a multi-user deployment is a data leak that
    looks exactly like working software — so this is also what the router-level
    default dependency enforces.
    """
```

Three rules make that enforceable rather than aspirational:

1. **Authentication is a router-level default, not a per-route decorator.** Routers are
   mounted with `dependencies=[Depends(require_identity)]`, and the small set of public
   endpoints (`/health`, `/auth/*`, the JWKS document) opt *out* explicitly. A new endpoint
   added without thinking about auth is protected; the failure mode of forgetting is a 401,
   not a leak.
2. **`AppConfig.user_id` becomes `AppConfig.default_user_id`, and nothing under `lumen/api/`
   may read it.** The rename is the point — it makes every remaining reader visible in a
   grep, and a test asserts the API package contains none. The simulation runner, the CLI
   and the test fixtures keep using it, because they have no request to carry an identity
   and inventing one for them would be ceremony.
3. **`Identity` is a Pydantic model**, like every other boundary crossing in this system.

---

## 6. Isolation: one store per person

**Built in Goal 22.** What follows is the reasoning it was built from; the divergences are
in `Goal_22_Plan.md`, and the largest is that the search index turned out to allow only one
*connection* per process even though it allows many collections on it.

This is the decision with consequences, so here is the alternative it was chosen over.

The other option is a shared graph and a shared collection with `user_id` on every record,
filtered at the provider boundary. It scales further in one process and matches the line
already in `Technical_HLD.md` §9 about a "user_id prefix on node_ids". It also means adding
a column to all fifteen node tables, a payload filter to every vector search, and a
predicate to every Cypher query in the system — retrieval passes A/B/C, neighbour walks,
version chains, decision history, the episode read, the graph explorer. Getting that right
in every path is achievable. Getting it right in every path *forever*, including the ones
written under time pressure two years from now, is not, and a single missed predicate is one
person reading another person's psychological history.

**Per-user stores make that class of bug unrepresentable.** There is no shared table to
forget to filter. The isolation lives in one place — how a store handle is obtained — and
every query written above it is automatically correct without knowing anything about
tenancy.

```
LUMEN_GRAPH_DB_ROOT=./data/graphs          →  ./data/graphs/<user_key>/
LUMEN_VECTOR_LOCATION=./data/vectors       →  collection "lumen_<user_key>"
LUMEN_OPS_DB_PATH=./lumen_ops.db           →  unchanged, shared, already user_id-keyed
```

*(Goal 22. None of this section is built.)* `user_key` is a filesystem-safe encoding of `user_id`, validated against a strict pattern
before it is ever concatenated into a path or a collection name. `user_id` is generated by
us and cannot contain a traversal sequence — and it is still validated at the boundary,
because "cannot" is a property of today's generator and path traversal is permanent.

The operational database stays shared. It is already keyed by `user_id` on every table, it
holds no graph content, and splitting it would fragment the one place that can answer
questions across the whole deployment.

### 6.1 What this costs, stated plainly

**Kuzu is embedded and takes an exclusive lock.** One process may hold one database open;
N users is N open databases in one process. This is the real ceiling, and it is a file-handle
and memory ceiling rather than a correctness one.

The answer is a **store registry with LRU eviction**: handles are opened on demand, kept
warm, and closed when the registry exceeds a configured size (`LUMEN_MAX_OPEN_GRAPHS`,
default 32). Reopening a Kuzu database costs milliseconds. A deployment past a few hundred
concurrent users is the deployment that swaps `KuzuGraphProvider` for a Neo4j
implementation — which the `GraphProvider` Protocol has always existed to make possible, and
which the per-user model does not obstruct: the same registry then resolves a database name
inside one Neo4j server instead of a directory on disk.

**The single-writer constraint gets sharper.** The API opens a user's graph read-only; the
ingest worker opens it to write, on its own thread. Both are in one process today, which is
why nothing has broken. Under per-user stores they must be coordinated per user rather than
globally, and a deployment that runs the worker as a separate process must not open the same
user's directory in both. This is a Goal 22 requirement, not a footnote.

*Goal 22 sharpened it as described and did not remove it.* Coordination is now per person —
the registry lends one handle per user and counts who is holding it — so two people can be
written at once and one person cannot be written twice. The lock is still a lock inside one
process. Two processes would still collide, and the first deployment that runs the worker
separately needs something both can see. That is named here rather than half-built.

**A per-user store needs creating, and creation can fail halfway.** A user whose graph
directory exists but whose Qdrant collection does not is a user for whom every write
succeeds and nothing is ever findable — the exact failure Goal 13b already caught once at
the collection-width level. Provisioning is therefore explicit, ordered, idempotent, and
verified at first use, not implied by the first write.

*Built as described.* Provisioning makes the graph first and the collection second, so an
interruption leaves the shape that is detectable rather than the shape that is silent, and
the check at first use raises rather than serving an empty history.

### 6.2 The existing data

There is a real graph on disk today belonging to `LUMEN_USER_ID=local`, with real history
in it. Goal 22 must adopt it, not strand it: a documented one-time migration that creates
the first `users` row, links it to a Google identity, and moves the existing database
directory and collection to that user's key. This is easy to forget until the moment it is
expensive, so it ships as a tested command rather than as instructions in a README.

*Built as `python -m lumen.stores.adopt --user <id>`.* It moves the graph, copies the search
entries through the provider interface rather than by touching files, refuses rather than
merges if the destination already holds a history, and reports "nothing to do" when it has
already run — so an operator who cannot remember whether it ran can find out by running it.

---

## 7. Configuration

Every value is read from the environment at process start, like every other credential in
this system, and none is ever persisted, logged, or allowed into `pipeline_jobs.config_snapshot`.

| Variable | Purpose |
|---|---|
| `LUMEN_AUTH_ENABLED` | Off by default so single-user and test deployments are unchanged. On, every non-public route requires a token. |
| `GOOGLE_OAUTH_CLIENT_ID` | From the Google Cloud console, Web application credential. |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Same credential. Server-side only, never sent to the browser. |
| `GOOGLE_OAUTH_REDIRECT_URI` | The **frontend** callback URL, registered in the console. Must match exactly, including scheme and trailing path. |
| `LUMEN_JWT_PRIVATE_KEY` | Ed25519 private key, PEM. Absent in a verify-only deployment. |
| `LUMEN_JWT_PUBLIC_KEYS` | One or more public keys with their `kid`, so a rotation can verify old and new tokens at once. |
| `LUMEN_JWT_ISSUER` / `LUMEN_JWT_AUDIENCE` | Default `lumen` / `lumen-api`. |
| `LUMEN_ACCESS_TOKEN_TTL` | Default `15m`. |
| `LUMEN_REFRESH_TOKEN_TTL` | Default `30d`. |
| `LUMEN_ALLOWED_ORIGINS` | Exact origins for CORS. No wildcard — credentials require it. |
| `LUMEN_GRAPH_DB_ROOT` | Replaces `LUMEN_GRAPH_DB_PATH` under multi-user. |
| `LUMEN_MAX_OPEN_GRAPHS` | LRU ceiling on warm Kuzu handles. Default 32. |
| `LUMEN_SIGNUP_MODE` | `open`, `invite`, or `allowlist`. Default `allowlist` — see below. |
| `LUMEN_ALLOWED_EMAILS` | The allowlist. |

`LUMEN_SIGNUP_MODE` defaults to `allowlist` rather than `open` on purpose. A publicly
reachable Lumen with open Google sign-in provisions a graph database, a vector collection
and a model budget for anyone who finds the URL. The permissive default is the one that
costs money and disk to the first person who scans the host.

`GOOGLE_OAUTH_CLIENT_ID` is also needed by the frontend, which is fine — a client id is
public by construction. The secret is not, and never appears in a `NEXT_PUBLIC_*` variable.

---

## 8. What must be true before this is done

**AUTH-1** No endpoint that reads or writes a person's data can be reached without a valid
access token, and the enforcement is a router default rather than a per-route decision.

**AUTH-2** A token that is expired, wrongly signed, wrongly audienced, or carrying a stale
`tv` is refused with `401` and a body that says which, in words, without leaking whether the
subject exists.

**AUTH-3** No request can read or write another user's graph, vectors, buffers, jobs,
imports, review items or settings. This is tested adversarially: two seeded users, and every
read endpoint in the API asked for the other user's identifiers.

**AUTH-4** A refresh token presented twice revokes its whole chain.

**AUTH-5** Bumping `token_version` ends every outstanding session within one access-token
lifetime, with no deploy and no restart.

**AUTH-6** `LUMEN_AUTH_ENABLED=false` reproduces today's behaviour exactly, so the existing
single-user deployment and the whole test suite keep working unchanged.

**AUTH-7** No credential, token, cookie or authorization code reaches a log line, a config
snapshot, an error body, or a URL query string.

**AUTH-8** A half-provisioned user is detected at first use and reported as such, never
silently served as an empty graph.

**AUTH-9** Account deletion routes into the existing erasure procedure and is auditable in
`data_erasure_audit`, which already stores a hashed user id rather than the id itself.

---

## 9. Out of scope

Named as decisions rather than omissions: passwords and password reset; MFA; roles,
permissions or admin surfaces; organisations and teams; sharing a graph with another person;
exporting to another person; per-user model budgets and quota enforcement; email
verification flows of our own (Google's `email_verified` is what we accept); session
management UI beyond sign-out.

---

## 10. Open questions

**OQ-A1 — Where does the pipeline get its identity?** **Answered in Goal 22: per job, from
the `user_id` the job already carries, through the same registry a request uses.** Holding
one person's handles for the length of a run would have meant a second lease path with its
own rules about when things are released, and two ways to get a store is how one of them
ends up wrong. A job leases, works, and releases — which is also what lets a sweep run for
everybody without pinning every person's database open at once.

**OQ-A2 — Does the ingest worker stay in-process under multi-user?** **Answered in Goal 22:
yes, for now, and the constraint is now per person rather than global.** Two people can be
written at the same time; one person cannot be written by two processes. The lock lives
inside one process, so nothing here makes a separate worker safe — the deployment that first
wants one needs a lock both processes can see, and that belongs with the deployment that
needs it rather than being guessed at now.

**OQ-A3 — What happens to a signed-in session when a user is suspended mid-conversation?**
**Answered in Goal 21: a conversation re-checks at each turn boundary.** Every frame would
mean interrupting a sentence mid-word to argue about credentials; never re-checking would
mean a session somebody ended carrying on until they close the tab. The turn boundary is
where the person has stopped and is waiting anyway, and the check is one indexed read. A
revoked session finishes its current sentence and is then closed with a policy code, so a
client can tell "sign in again" from "the network dropped".

A socket also cannot carry a dependency, so identity is resolved from the connection — from
the header where there is one, and otherwise from a query parameter, because a browser's
WebSocket API cannot set headers. That is a real cost, named rather than hidden: a token in
a query string can reach an access log. It is the short-lived half only, never the renewable
one.

**OQ-A4 — Rate limiting lives where?** **Answered in Goal 21: sign-in only, in this
process.** Sign-in is the one door open to somebody who has proved nothing, so it is the one
with a limit — counted per caller *and* per address, because the first catches one machine
working through a list and the second catches many machines working on one account, which
looks like nothing at all if only callers are counted. A successful sign-in clears the
count, so somebody who mistyped their way through four attempts is not one attempt from
being locked out.

Limiting authenticated routes is **not** built and is not a gap: it wants a proxy in front
of the service rather than a counter inside it, and an in-process limiter for authenticated
traffic would be the wrong thing in the wrong place.

---

## 11. Why not Clerk

`Technical_HLD.md` §11 decision 5 recommended Clerk — turnkey, social login included, good
developer experience. All true, and it is withdrawn for three reasons specific to this
system.

**The product is a psychological history.** Introducing a third party that holds the
identity of everyone using it, on a system whose entire value proposition is that it knows
things about you nobody else does, is a large trust decision to make for developer
convenience. The self-hosted, single-tenant deployment story that a personal build implies
also stops being available.

**The work Clerk saves is not the work that is hard here.** Token issuance and Google
verification are a few hundred well-understood lines. Per-user store isolation, provisioning,
the single-writer constraint and the migration of existing data are the difficult parts, and
Clerk does not touch any of them.

**Asymmetric keys under our own control are what the production topology needs.** Services
get extracted into separate processes that must verify tokens without being able to mint
them (DEC-A3). That is straightforward with our own JWKS and a negotiation with somebody
else's roadmap otherwise.

The counter-argument is real and worth writing down: password reset, MFA, device management
and session listing are all future work we now own. DEC-A7 keeps that bill small by shipping
exactly one sign-in method — everything on that list is a consequence of passwords, which we
do not have.
