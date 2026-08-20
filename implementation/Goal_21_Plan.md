# Goal 21: Identity & Access — Who Is Asking

**Branch:** `goal21`
**Depends on:** Goal 3 (every operational table has carried `user_id` since it was built),
Goal 19 (erasure, which account deletion routes into), Goal 20 (the gateway this bolts onto)
**Spec:** `docs/hld/Auth_Architecture.md` — the full design, its decisions (DEC-A1…A7), its
acceptance criteria (AUTH-1…AUTH-9) and the reasoning for each. **Read it before this.**
What follows is the build order and the decisions the spec left open, not the design.

---

# SECTION A — LOGIC (please verify)

## Objective

Lumen is a single-user system in exactly one sense: there is no such thing as *who is
asking*. `AppConfig.user_id` is an environment variable that defaults to `"local"`, every
route reads it, and every request is therefore the same person.

Everything else is further along than that sounds. The operational database has been keyed
by user since Goal 3 — conversations, jobs, imports, the review queue, settings and the
erasure log all carry the column already. What is missing is anybody to put in it.

This goal puts a real person behind every request. It is the smaller half of the phase:
**authentication is a week; isolation is the hard part**, and isolation is Goal 22.

## A0. Something already broken, found while planning this

The system does not agree with itself about who the user is. The conversation surface
writes under the hardcoded string `"debug"`; everything else uses `config.user_id`, which
is `"local"`. They have never been the same person.

That is not cosmetic. Erasure asks the operational database for "every conversation this
person has had", and gets nothing, because the conversations belong to somebody else:

```
chat writes under : debug
everything else   : local
erase-everything  : 200, 0 rows cleared
the message after : 'something private'
```

**"Forget everything" reports success and leaves every word of every conversation on
disk.** Goal 19 built that path correctly and it has been reaching the wrong user's
conversations since the day it shipped, because there was no one place that said who the
user was. This goal creates that place, and closes this on the way past.

## A1. What Gets Built

| | What it is |
|---|---|
| **A user is a row, not a setting** | Three new tables: the person, the accounts they sign in with, and the sessions they hold. A user's identifier is generated and permanent — not their email, which people change, and not Google's, which outlives nothing. |
| **Sign in with Google** | The standard authorization-code exchange. The secret stays on the server, the browser never completes an exchange, and Google's proof is checked properly — signature, issuer, audience, expiry, and that the address is verified — before any row is touched. |
| **Lumen's own sessions** | Google says who somebody is, once. Everything after that is Lumen's own short-lived token and a long-lived refresh token that is rotated on every use, so a stolen one is a detectable event rather than a permanent compromise. |
| **One place that says who is asking** | Every route stops reading configuration and takes a request-scoped identity. Enforced as a router default, so a new endpoint is protected by *forgetting* rather than exposed by it. |
| **An off switch that changes nothing** | With auth disabled the system behaves exactly as it does today, which is what lets the existing deployment and 4,691 tests keep running unchanged. |

## A2. The Decisions Taken

The spec settles most of these (DEC-A1…A7) and I am following it. Four it left open, and
one it did not raise:

**1. A conversation checks who is talking at each turn, not at each word.** *(Answers the
spec's own open question OQ-A3.)* A live conversation is a socket held open for an hour, and
a token lasts fifteen minutes. Re-checking on every frame would mean interrupting a sentence
mid-word to argue about credentials; never re-checking would mean a revoked session that
keeps talking until somebody closes the tab. The turn boundary is the natural checkpoint —
it is where the person has stopped and is waiting anyway, and the check is one small
database read. A revoked session gets its current sentence finished and is then told to sign
in again.

**2. Only sign-in is rate-limited, and it is limited in this process.** *(OQ-A4.)* Sign-in is
the one door that is open before anybody has proved anything, so it is the one that gets a
limit — per address and per caller. Limiting the rest of the API is a deployment concern
that wants a proxy in front of the service rather than a counter inside it, and building an
in-process limiter for authenticated routes would be building the wrong thing in the wrong
place.

**3. Nobody gets an account by finding the URL.** Sign-up is restricted to a named list by
default. An open Google sign-in on a reachable host hands a database, a search index and a
model budget to the first person who scans the port — that permissive default costs real
money and disk to whoever is running it.

**4. The credentials are not fields.** This system already keeps its model credentials as
*computed values* rather than stored settings, specifically so that the snapshot of settings
written on every pipeline run cannot carry a key into the database with it. The signing key
and the Google secret follow exactly that existing pattern, which makes "no credential
reaches a log line or a stored record" true by construction rather than by remembering.

**5. Two things named `user` become one.** The conversation surface's hardcoded `"debug"`
goes, and with it the class of bug in A0 — one identity, resolved in one place, for every
surface.

## A3. Judgement Calls (flagging, not asking)

- **Signing out ends one session, not all of them.** Somebody signing out on a library
  computer means *this* device; ending their phone's session too would be surprising. Losing
  a device is the different request, and it is the one that ends everything at once.
- **A refusal says which check failed, and never whether the account exists.** "Your session
  expired" and "that is not a valid token" are different things a person can act on;
  "no such user" tells a stranger which addresses are worth trying.
- **Deleting an account ends every session first, then erases.** In that order — erasing
  while a session is still live means requests arriving for data that is disappearing
  underneath them.
- **The existing single-user graph is not touched by this goal.** It keeps working exactly
  as it does. Adopting it into a real user's account is Goal 22's, along with the per-user
  stores it needs to be adopted *into*.

## A4. What Is Deliberately Not Built

| Not built | Why |
|---|---|
| Per-user graphs and search indexes | Goal 22, and it is the larger half of this phase. Until then every signed-in person shares one graph — which is correct for the single-user deployment this actually is, and is why Goal 22 must land before a second person is ever invited. **This is the sentence to remember from this plan.** |
| Passwords, reset, MFA | The spec rules them out and the reasoning holds: a password store is a liability with no upside for a product one person uses. Everything on that list is a *consequence* of passwords. |
| Roles, teams, sharing, admin surfaces | Named as decisions rather than omissions in the spec's own out-of-scope list. |
| Rate limiting on authenticated routes | See A2.2 — it wants a proxy, not a counter in the process. |
| A session-management screen | Sign-out exists; listing devices is a surface, and surfaces are Phase 8. |

## A5. How You'll Know It Works

1. **The named test.** A whole token lifecycle against a fake Google — no network. Sign in,
   get a session, refresh it, sign out.
2. Four kinds of bad token — expired, wrongly signed, meant for something else, belonging to
   a session that was ended — each refused, each saying which.
3. A refresh token used twice kills the whole chain and forces a fresh sign-in.
4. Ending every session takes effect within fifteen minutes with no restart.
5. **Two users, and every read endpoint asked for the other one's data.** For everything the
   operational database holds, this passes in this goal; for the graph it is honestly
   reported as Goal 22's, because there is one graph until then.
6. Turn auth off and the entire existing test suite passes untouched.
7. Grep every log line, error body and stored settings snapshot produced by a full sign-in:
   no token, no code, no secret, no cookie.
8. **The A0 bug is gone**: erase-everything reaches the conversations.

---

# SECTION B — LOW-LEVEL DESIGN

## B1. Module Layout

```
lumen/auth/__init__.py         ← exports Identity and AuthService only
lumen/auth/contracts.py        ← Identity, TokenPair, SignInResult, AuthError tree
lumen/auth/keys.py             ← Ed25519 key loading, the kid, the JWKS document
lumen/auth/tokens.py           ← mint / verify, no store, no vendor
lumen/auth/provider.py         ← IdentityProvider Protocol (verify an external identity)
lumen/auth/google.py           ← the ONLY module that knows Google exists
lumen/auth/repository.py       ← users, identities, refresh tokens
lumen/auth/sessions.py         ← issue, rotate, revoke, reuse detection
lumen/auth/service.py          ← the narrow surface the web layer holds
lumen/auth/limits.py           ← the sign-in rate limiter
lumen/api/routes/auth.py       ← the seven endpoints
```

Modified: `lumen/api/deps.py` (`get_identity`, `require_identity`), `lumen/api/main.py`
(router defaults, CORS), every route module under `lumen/api/routes/`, `lumen/config.py`
(`AuthConfig`, the `user_id` → `default_user_id` rename), `lumen/operational/` (three tables
+ migration `0007_identity`), `lumen/erasure/service.py` (account deletion).

**Dependencies.** `pyjwt[crypto]` added (`cryptography` is already present); `httpx` moves
from the dev group to the runtime dependencies, since the Google exchange is a real HTTP
call in production. Hand-rolling JWT verification is a well-known way to ship an algorithm-
confusion bug, and this is not the place to save a dependency.

## B2. Contracts

```python
class Identity(BaseModel):        # frozen
    user_id: str
    email: str
    display_name: str = ""
    token_version: int = 0
    authenticated: bool = True    # False for the configured offline default

class TokenPair(BaseModel):       # frozen; the refresh half is returned once and never stored
    access_token: str
    expires_in: int
    refresh_token: str

class AuthError(Exception): ...
class NotAuthenticated(AuthError): ...    # 401, with a `reason` a person can act on
class SignUpRefused(AuthError): ...       # 403, the allowlist
class TokenReused(AuthError): ...         # 401, and the chain is already revoked
```

`Identity.authenticated` is how a route can tell a real person from the configured default
without reading configuration itself.

## B3. Tables and migration `0007_identity`

Exactly the spec's §2 schema. Three notes on the implementation:

- `refresh_tokens.token_hash` is SHA-256 of the secret half; **the token itself is never
  stored**, so a database read cannot produce a usable session.
- `rotated_to` is what makes reuse detectable: presenting a token that already has one means
  either theft or a race, and both get the same answer — revoke the chain.
- `ip_hash` rather than the address, so a session list is possible without keeping IPs.

## B4. `tokens.py` — mint and verify, and nothing else

```python
def mint_access(identity: Identity, *, keys: KeySet, config: AuthConfig, now) -> tuple[str, int]
def verify_access(token: str, *, keys: KeySet, config: AuthConfig, now) -> AccessClaims
```

No store, no clock of its own, no vendor. **The algorithm is pinned to `EdDSA` on both
sides** — accepting whatever the token's own header asks for is the classic way to turn a
public key into a signing key. Issuer and audience are verified, not merely read. `tv` is
returned in the claims and compared against the stored value by the layer above, because
that comparison is a database read and this module does not have one.

## B5. `google.py` — behind a Protocol, like every other vendor

`IdentityProvider` has one method: `verify(code, verifier) -> ExternalIdentity`. `google.py`
is the only file in the system that imports an OAuth vendor's shape, and the tests use a
second implementation rather than a mocked network.

Verification order is the spec's §3 step 5, and it is not shortened: signature against
Google's published keys (cached, with the cache keyed by `kid`), then `iss`, then `aud`
against our client id, then `exp`, then `email_verified`. An unverified address is a claim
about a mailbox somebody may not control.

`state` is single-use, held in an httpOnly cookie set when the flow starts and compared when
it finishes. The PKCE verifier is generated and held server-side — this deployment has a
secret and can keep one, so PKCE here is depth rather than the primary protection.

## B6. `sessions.py` — issue, rotate, revoke

```python
def issue(user, *, agent, ip) -> TokenPair
def rotate(presented: str, *, agent, ip) -> TokenPair      # raises TokenReused
def revoke(presented: str) -> None                         # one session
def revoke_all(user_id: str) -> None                       # bumps token_version
```

`rotate` is the interesting one and is a single transaction: find by hash, check it is not
expired or revoked, check `rotated_to` is null — and if it is not, revoke every token in the
chain and raise.

## B7. Identity reaching the code

```python
def get_identity(request: Request) -> Identity      # who is asking
def require_identity(request: Request) -> Identity  # the router-level default
```

With `LUMEN_AUTH_ENABLED=false`, `get_identity` returns
`Identity(user_id=config.default_user_id, authenticated=False)` and nothing else changes.
That single seam is what makes AUTH-6 true and the whole existing suite keep passing.

Routers are mounted with `dependencies=[Depends(require_identity)]`. `/health`, `/auth/*`
and the JWKS document opt out explicitly, and a test asserts that the set of unauthenticated
paths is exactly those three — so a new public endpoint has to be added to that list
deliberately.

**The rename is the enforcement.** `AppConfig.user_id` → `default_user_id`, and a test
asserts nothing under `lumen/api/` reads it. Fifteen call sites move to `Identity`, plus the conversation surface's hardcoded string; the two
outside the API (the report runner and the pipeline's bookkeeping) keep the default, because
they have no request and inventing one for them would be ceremony.

## B8. Config

`AuthConfig` holds the spec's §7 variables, with the two credentials as **properties rather
than fields**, following `gemini_api_key`'s existing pattern — invisible to `asdict()`,
`replace()`, `repr()` and `==`, so they have no path into `config_snapshot`, a log line or an
error body. That is AUTH-7 satisfied by construction.

CORS is added for the first time: exact origins from `LUMEN_ALLOWED_ORIGINS` with
`allow_credentials=True`. A wildcard is not merely lax in that combination — browsers reject
it outright.

## B9. Tests

| File | Covers |
|---|---|
| `test_auth_tokens.py` | Mint/verify round trip; expired, wrong signature, wrong audience, wrong issuer, `alg` swapped, stale `tv` — each refused with its own reason. |
| `test_auth_google.py` | The full flow against a fake provider with a local key: a good ID token, an unverified email, a wrong audience, a bad signature, a `state` mismatch, a replayed `state`. |
| `test_auth_sessions.py` | Issue, rotate, revoke; **reuse kills the chain**; an expired refresh; revoke-all ends everything. |
| `test_auth_routes.py` | The seven endpoints, cookie flags (`HttpOnly`, `Secure`, `SameSite=None`, `Path=/auth`), and the allowlist refusing a stranger. |
| `test_auth_enforcement.py` | **Every route in the app** enumerated from the OpenAPI document and asked without a token: exactly three answer. Two seeded users and every operational read asked for the other's identifiers. |
| `test_auth_disabled.py` | With auth off, the identity is the configured default and nothing behaves differently. |
| `test_auth_leaks.py` | A whole sign-in with logs captured: no token, code, secret or cookie in any line, error body, or `config_snapshot`. |

## B10. Build Order

1. `contracts.py`, `keys.py`, `tokens.py` + tests — no stores, no vendor, no routes.
2. Migration `0007_identity` and `repository.py`.
3. `sessions.py` + the reuse test, which is the security property worth writing first.
4. `provider.py`, `google.py`, and the fake used to test it.
5. `service.py`, `limits.py`, `routes/auth.py`.
6. `get_identity` / `require_identity`, the router defaults, CORS.
7. The rename, the fifteen call sites, and **the A0 fix** — one identity for the
   conversation surface too.
8. Account deletion into Goal 19's erasure.
9. `Master_Plan.md`, `Auth_Architecture.md` (status: built, with OQ-A3 and OQ-A4 answered),
   and Section C here.

---

# SECTION C — WHAT WAS ACTUALLY BUILT

The plan held up. Six things came out differently, and one of them was a bug
in the plan's own reasoning rather than a change of mind.

## C1. The stored records belong with the other stored records

B1 put `UserRecord` and `StoredSession` in `lumen/auth/repository.py`. That produced an
import cycle the moment the store implemented the Protocol: `auth.repository` imports
`lumen.operational.enums`, which runs the operational package's `__init__`, which imports
the SQLAlchemy implementation, which needed the record types back out of `auth.repository`.

The records moved to `lumen/operational/schemas.py`, where every other stored record already
lives, and `lumen/auth/repository.py` keeps only the Protocol. The dependency now runs one
way — auth knows about storage, storage knows nothing about auth — which is what it should
have been in the plan.

## C2. Identity had to work for sockets as well as requests

Not anticipated. Router-level dependencies apply to WebSocket routes too, and a dependency
annotated `Request` cannot be resolved for one — FastAPI called it with no arguments and the
whole chat surface broke.

Both dependencies now take `HTTPConnection`, the common base of `Request` and `WebSocket`,
so one rule covers both. The socket gets the router-level refusal *and* resolves the identity
itself at every turn, because a router dependency cannot hand its answer to a socket.

## C3. `TokenReused` is a kind of `NotAuthenticated`, not a sibling

Written as a separate branch of the error tree, it escaped the refresh route's `except` and
came back as a 500 — turning the single most security-relevant event in the system into an
unhandled error.

It is now a subclass. Every caller that already handles a refused credential handles this one
correctly by doing nothing, and a caller that wants to distinguish still can. A separate
branch is one somebody has to remember to write.

## C4. The clock is injectable in the way the rest of the codebase means it

B4 said "no clock of its own" and the first implementation still had one: the JWT library
checks `exp` and `iat` against the real clock, so the `now` argument was accepted and
ignored. Every time-related test passed for the wrong reason.

Now the library owns the check when no moment is named — which is production — and the same
two rules are applied against the caller's moment when one is. Never neither: the explicit
checks run on exactly the branch that turns the library's off.

## C5. Three route helpers were given a dependency they cannot have

A mechanical pass added `identity: Identity = Depends(require_identity)` to every function
whose body used `identity`, and three of those were plain helpers rather than routes:
`_as_request` in maintenance, `_accept` in ingest, and a settings handler that calls another
handler directly. Each produced a `Depends` object where a value was expected — a 500 in
three surfaces.

Worth recording because the failure is silent at import time and obvious at request time,
which is the wrong way round. The helpers now take an identity as an ordinary argument and
the routes pass it.

## C6. The Secure cookie only works over https, including in tests

A cookie marked `Secure` is not stored by a client on plain http — including the test client
— so the first cookie tests failed in a way that looked like the flags were wrong. They run
over `https://testserver` now, which also means they exercise the production cookie
combination (`Secure` + `SameSite=None`) rather than a weakened one.

The related setting is worth naming: `LUMEN_COOKIE_SECURE` defaults to true and drags
`SameSite` with it, because browsers refuse `SameSite=None` without `Secure`. A deployment
on plain http for local development gets `lax`, which is the strongest thing that actually
works there, rather than a cookie that is silently never sent.

## C7. The A0 bug, closed

The defect this goal was planned around is gone. Before:

```
chat writes under : debug          erase-everything : 200, 0 rows cleared
everything else   : local          the message after: 'something private'
```

After:

```
chat writes under : local          erase-everything : 200, 1 rows cleared
everything else   : local          the message after: '[ERASED: 2026-08-20]'
```

There is a test for it in two directions: with sign-in on, a conversation belongs to whoever
is talking and one person's erasure leaves the other's alone; with sign-in off, there is
still exactly one answer to "whose is this" and every surface uses it.

## C8. Result

**4904 passing, 0 failures** (213 new). 96% coverage on `lumen/auth/` — the remainder is the
network path in `google.py` and key-loading failure branches, both covered through the route
tests, which cannot run under coverage for the numpy reason recorded in Goal 19.

The named acceptance checks all hold: a whole token lifecycle against a fake Google with no
network; four kinds of bad token each refused with its own reason; a refresh token used twice
killing the chain and invalidating the outstanding access tokens with it; **every endpoint in
the OpenAPI document asked without a token, with exactly the three intended ones answering**;
a whole sign-in with every log line captured and no credential, code, cookie or token in any
of them; and the entire pre-existing suite passing with sign-in switched off.

**What is still shared.** One graph, one search index, for everybody who signs in. Tested and
asserted rather than glossed over, because a test that pretended otherwise would be the most
dangerous kind of passing test. Goal 22 is what makes a second person safe to invite.
