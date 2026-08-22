# Goal 22: Per-User Isolation — A Graph Each

**Branch:** `goal22`
**Depends on:** Goal 21 (there is a person behind every request), Goal 19 (erasure, which
becomes per-user for real), Goal 20 (the scheduler, which now has to run for everybody)
**Spec:** `docs/hld/Auth_Architecture.md` §6, §6.1, §6.2, and open questions OQ-A1 and OQ-A2.
DEC-A5 is the decision this goal carries out.

---

# SECTION A — LOGIC (please verify)

## Objective

Goal 21 put a person behind every request and stopped short of the thing that matters:
**everyone who signs in still shares one graph.** Two people signing into Lumen today would
read each other's psychological history. That is not a bug waiting to happen — it is the
current, tested, documented state, and it is why the last goal ended by saying a second
person must not be invited yet.

This goal is the invitation. It is also the larger half of the phase: authentication was a
week, and this is the part with consequences.

## A1. The decision this carries out, and what it was chosen over

There are two ways to keep people's histories apart.

**Put a user column on everything and filter every query.** One graph, one search index, a
condition on every read. It scales further in one process and it is what most systems do.
It also means adding a predicate to every query in the system — the three retrieval passes,
neighbour walks, version chains, decision history, the episode read, the graph explorer, the
report corpus — and getting it right in every one of them, *forever*, including the ones
written under time pressure two years from now. A single missed condition is one person
reading another person's psychological history.

**Give each person their own store.** A directory each, a search collection each. There is
no shared table to forget to filter, so the mistake above cannot be written. Every query in
the system stays exactly as it is and is automatically correct, because correctness moved
into how a store handle is obtained rather than into what every query remembers to say.

The spec chose the second, and this goal implements it. The cost is real and is in A3.

## A2. What Gets Built

| | What it is |
|---|---|
| **A store for each person** | One graph directory and one search collection per user, named from their identifier and nothing else. |
| **A registry that hands them out** | The one place a store handle can be obtained. Opens on demand, keeps a few warm, closes the least recently used when there are too many. |
| **Provisioning that cannot half-happen** | Making somebody's stores is one ordered, repeatable operation, and using them checks they are all really there. A person whose graph exists but whose index does not is a person for whom every write succeeds and nothing is ever findable. |
| **An answer for the work with no request behind it** | A background run has a person on its job record rather than a caller. It resolves stores from that, and holds them for the length of the run. |
| **A migration for the history already on disk** | There is a real graph belonging to `local` with real writing in it. It gets adopted into the first real account, not stranded. |

## A3. The Decisions Taken

**1. A handle in use is never closed.** The registry keeps a few stores warm and closes the
rest, and the one rule that makes that safe is that "least recently used" never means
"currently being used". A background extraction run holds a person's graph for minutes; a
registry that closed it at forty seconds because six other people signed in would corrupt
the run it interrupted. Handles are lent out and returned, and only returned ones can be
closed.

**2. Reopening is cheap and being wrong about that is not.** Closing a store costs a
reopen of a few milliseconds next time. Keeping too many open costs file handles and memory
on an embedded database that takes an exclusive lock per directory. The ceiling is a setting
because the right number depends on the deployment, and the failure it prevents — running
out of file handles — is the kind that takes the whole process down rather than one request.

**3. The single-writer question is answered by the registry rather than by topology.**
*(OQ-A2.)* Today the API reads a graph and the importer writes to it, and that is safe by
accident: one process, one handle, one lock inside it. Per person, the same thing has to be
true deliberately — everybody working on one person's graph must be holding *the same*
handle, because two handles on one directory is not slow, it is refused. The registry is
what guarantees that, and it is why there is exactly one of it.

**A second process is a different matter and is not solved here**, because it cannot be
solved by a registry inside one process. It is named plainly rather than left implied: an
importer running separately must not open a directory the API has open, and the day that
deployment exists it needs a lock somewhere both can see.

**4. A background run resolves its person from the job, and holds on.** *(OQ-A1.)* Every
pipeline job already records whose it is. A run borrows that person's stores once at the
start and returns them at the end, rather than asking per stage — partly because it is
cheaper, and mostly because a run that re-resolved halfway could be handed a *different*
handle after an eviction and write the second half of an entry somewhere else.

**5. A person's identifier is checked before it is ever part of a path.** We generate these
and they cannot contain anything dangerous. They are still validated at the boundary,
because "cannot" is a property of today's generator and a directory traversal is permanent.
Anything that does not match a strict pattern is refused rather than cleaned up — cleaning
produces a valid-looking path for an invalid identifier, which is worse.

**6. The operational database stays shared, and stays keyed by person.** It holds no
history — conversations waiting to be processed, job records, the review queue — and it is
already keyed correctly. Splitting it would break the one place that can answer a question
about the whole deployment, and would gain nothing that the column does not already give.

**7. The existing history is adopted by a command, not by instructions.** There is real
writing on disk under the old single-user arrangement. Moving it is easy to get right once
and easy to forget entirely, so it ships as something that can be run and tested rather than
as a paragraph in a readme. It is safe to run twice, and it refuses rather than merging if
the destination already holds anything.

## A4. Judgement Calls (flagging, not asking)

- **The search index is copied rather than renamed**, because there is no rename. The points
  are read out and written into the new collection through the same interface everything else
  uses; going underneath it to move files would tie this to one vendor's storage layout,
  which is the thing the whole provider arrangement exists to avoid.
- **The recurring jobs now run for everybody.** Reports, the shadow scan and the review sweep
  were written for the one configured person. They now iterate over the people who exist,
  one at a time, holding one person's stores at a time.
- **Somebody with no stores yet is provisioned on their first use**, not at sign-up. A person
  who signs in and never comes back should not have left a database behind.

## A5. What Is Deliberately Not Built

| Not built | Why |
|---|---|
| A lock two processes can see | The single-writer constraint across processes cannot be fixed by anything inside one process. Naming it is the honest thing; solving it belongs with the deployment that first needs it. |
| Moving off embedded stores | The whole per-user arrangement is what makes swapping in a server-backed graph *easier* later — the registry then resolves a database name instead of a directory. Doing it now would be solving a scale problem this deployment does not have. |
| Per-user model budgets or quotas | Named as out of scope by the spec. A person's stores being separate says nothing about what they may spend. |
| Sharing a graph with somebody else | Out of scope, and the thing the entire design makes structurally hard on purpose. |

## A6. How You'll Know It Works

1. **The adversarial test, which is the point.** Two people, each with their own history, and
   then every read endpoint in the API asked for the *other* person's identifiers. Nothing
   comes back.
2. Two people writing at the same time do not collide, and neither sees the other's records.
3. The registry, pushed past its ceiling, closes something and reopens it, and a handle that
   is in use is never the one closed.
4. Provisioning interrupted between the graph and the index is detected at first use and
   reported as broken rather than served as an empty history.
5. An identifier with a traversal sequence in it is refused rather than cleaned.
6. The migration runs, the existing history ends up under a real account, and running it a
   second time changes nothing.
7. With sign-in off, the single-user deployment still works — one person, one store,
   everything where the migration put it.

---

# SECTION B — LOW-LEVEL DESIGN

## B1. Module Layout

```
lumen/stores/__init__.py       ← exports StoreRegistry and UserStores
lumen/stores/keys.py           ← user_key validation, paths, collection names
lumen/stores/contracts.py      ← UserStores, StoreError tree
lumen/stores/provision.py      ← making a person's stores, and checking them
lumen/stores/registry.py       ← the one place a handle comes from
lumen/stores/adopt.py          ← the one-time migration of the existing data
lumen/api/routes/admin.py      ← (only if needed) provisioning status
```

Modified: `lumen/config.py` (`db_root`, `max_open_graphs`), `lumen/api/deps.py`
(`get_graph`/`get_vectors` become identity-scoped), `lumen/api/main.py`,
`lumen/ingest/worker.py`, `lumen/scheduling/*`, and the three services that hold a graph
(`MacroextractionService`, `ReviewService`, `ErasureService`).

## B2. `keys.py` — the only place an identifier becomes a path

```python
USER_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

def user_key(user_id: str) -> str        # validates; raises UnsafeUserKey
def graph_dir(root: str, user_id: str) -> Path
def collection_name(user_id: str) -> str  # f"lumen_{key}"
```

Refuses rather than sanitises. A cleaned-up identifier is a valid path for an invalid
person, which is the failure that looks like it worked.

## B3. `registry.py` — handles, lent and returned

```python
class StoreRegistry:
    def __init__(self, config: AppConfig, *, open_graph=..., open_vectors=...)
    @contextmanager
    def lease(self, user_id: str) -> Iterator[UserStores]
    def close(self) -> None
    @property
    def open_count(self) -> int
```

One lock, a dict of open stores, and a borrow count per entry. `lease` provisions if needed,
increments the count, yields, decrements. Eviction runs after a return and only considers
entries with a count of zero, oldest-used first, until the count is under the ceiling.

The openers are injected so a test drives the whole registry with stand-ins and no disk.

## B4. `provision.py` — ordered, repeatable, checked

```python
def provision(user_id, *, config, open_graph, open_vectors) -> None
def verify(user_id, *, ...) -> None    # raises HalfProvisioned
```

Graph directory and schema first, then the collection — in that order, because a graph
without an index is a person whose writes land and cannot be found, and detecting that is
the whole of AUTH-8. `verify` runs on the first lease of a handle and confirms both halves
exist and the collection is the configured width.

## B5. Identity reaching a store

```python
def get_stores(connection, identity=Depends(get_identity)) -> Iterator[UserStores]
def get_graph(stores=Depends(get_stores)) -> ReadOnlyGraph
def get_vectors(stores=Depends(get_stores)) -> VectorProvider
```

Generator dependencies, so the lease is returned when the request ends, whatever happened.
Every route keeps its existing signature; what changes is where the object comes from.

**The three services take the registry rather than a graph.** Each already receives a
`user_id` on the calls that need one (`ReviewService.list_queue(user_id)`,
`ErasureService.erase(request.user_id)`); `MacroextractionService` gains one.

## B6. The worker and the scheduler

`IngestWorker.run_once`/`run_session` read the person off the import or the buffer, lease
once for the whole run, and release at the end. `IngestResources` loses its `graph`/`vectors`
fields and keeps the models — the models are shared, the stores are not.

`ReportsDue`, `ShadowScan` and `ReviewSweep` iterate `identities.list_users()` and lease per
person. One person's stores at a time, so the ceiling still means something.

## B7. `adopt.py` — the existing history

```python
def adopt(old_graph_path, old_collection, *, user_id, config) -> AdoptionReport
```

1. Refuse if the destination directory exists and is not empty.
2. Move the graph directory.
3. Copy the points into the new collection, in batches, through `VectorProvider`.
4. Verify both halves.

Needs one new capability: `VectorProvider.iter_points(batch)` yielding stored points, since
there is no rename and reaching into the storage layout would tie this to one vendor. Vectors
come back normalised, which is what a cosine collection stores and all it uses.

Exposed as `python -m lumen.stores.adopt --user <email>` and covered by tests, including
running it twice.

## B8. Config

```python
db_root: str = _env("LUMEN_GRAPH_DB_ROOT", "./data/graphs")
max_open_graphs: int = _env_int("LUMEN_MAX_OPEN_GRAPHS", 32)
```

`LUMEN_GRAPH_DB_PATH` goes. Leaving it would be a second answer to "where is the graph", and
the migration exists precisely so there is only one.

## B9. Tests

| File | Covers |
|---|---|
| `test_stores_keys.py` | Traversal, empty, over-long and odd-character identifiers all refused; a good one produces the expected path and collection name. |
| `test_stores_registry.py` | Lease and return; the ceiling evicts; **an in-use handle is never evicted**; two leases of one person share one handle; concurrent leases from threads. |
| `test_stores_provision.py` | Ordered and idempotent; interrupted between halves is detected at first use; a collection of the wrong width is refused. |
| `test_stores_adopt.py` | The history ends up under the account; run twice changes nothing; refuses a non-empty destination. |
| `test_auth_isolation.py` (extend) | **The adversarial one.** Two people with real, different histories; every read endpoint asked for the other's identifiers; nothing comes back. Replaces the honest "one graph is shared" test from Goal 21. |

## B10. Build Order

1. `keys.py` and its tests — everything else concatenates what it returns.
2. `contracts.py`, `provision.py`, `registry.py` and their tests, with stand-in openers.
3. Config, and the API dependencies.
4. The three services.
5. The worker and the scheduler.
6. `VectorProvider.iter_points`, then `adopt.py`.
7. The adversarial test, rewritten from Goal 21's placeholder.
8. `Auth_Architecture.md` (§6 built, OQ-A1 and OQ-A2 answered), `Master_Plan.md`, Section C.

---

# SECTION C — WHAT WAS ACTUALLY BUILT

The plan held. Seven things came out differently, and two of them were constraints the
plan had not discovered — both found by running the thing rather than by reading it.

## C1. A second connection to the vector store is refused, not queued

The plan treated the search index the way it treated the graph: one collection per person,
opened when their stores are leased. That is right for the collections and wrong for the
connection. Qdrant in local mode holds an exclusive lock on its storage folder, and the
second client constructed against it does not wait — it raises:

```
RuntimeError: Storage folder ... is already accessed by another instance
```

So `lease()` for a second person, while the first was still held, could never have worked
as written.

The fix separates the two things the plan had conflated. There is **one connection** to the
index for the whole process, and **many collections** on it. `QdrantVectorProvider` gained
an `open_client(location)` function and an optional `client=` argument: given a client it
borrows it and does not close it (`_owns_client` is False), and given none it opens and owns
one as before. The registry holds a `_VectorSource` that owns the single client and hands
out a provider per collection.

Ownership had to be explicit rather than implied, because the first version leaked: the
provider handed the shared client closed it on the way out and took every other person's
index down with it. `_owns_client` is what makes "who closes this" a property of how the
object was built rather than of who happens to call `close()` first.

The graph is unaffected — one Kuzu handle per person is exactly what the file lock wants.

## C2. A graph is a single file, not a directory

Found by a test, and it had been sitting in `adopt.py` since it was written. `_move_graph`
refused to adopt into an occupied destination by asking whether the directory had anything
in it:

```python
if destination.exists() and any(destination.iterdir()):
```

The database this build of Kuzu creates is one file. Pointed at a person who already had
stores — the case the check exists for — that line raised `NotADirectoryError` and the
adoption crashed mid-way instead of refusing cleanly.

It now asks the question in a way that survives both shapes: a directory counts as occupied
if it holds anything, a file counts if it has any size, and anything empty does not count at
all, because an empty leftover is what an interrupted run leaves behind and refusing to
finish that would strand the history it was halfway through moving. The clean-up before the
move learned the same distinction (`rmdir` for one, `unlink` for the other).

The test that was supposed to cover this passed all along, because it built the occupied
destination by hand as a directory. There is now one that provisions a real store first.

## C3. `/health` cannot ask about a person

`/health` was answered by counting nodes in the graph. With a graph per person there is no
graph to count until somebody has signed in — and `/health` is answered before anyone has,
which is the entire point of it.

It now reports whether the *root* everybody's graphs live under can be read and written.
That is the failure worth a liveness alarm: a missing or read-only storage directory stops
every person at once, where one person's graph failing is their problem and not the
service's. The two-field answer is unchanged; what the graph field means is narrower and
more honest.

## C4. The recurring jobs needed a list of people, not a person

Anticipated in A4, but the shape landed differently. Rather than each job reaching for a
user list, the jobs take a `people` callable and iterate it, so the thing that knows who
exists (`AuthRepository.list_users()`, added here) is injected rather than imported. A job
run for everybody holds **one person's stores at a time** — lease, work, release — so the
registry ceiling still means what it says while a sweep is running.

`MacroextractionService` gained `user_id` as the first argument of `due`, `run`,
`run_shadow` and `run_due`. That is the change that fanned out furthest: the report routes
now take an identity and pass it through, which is also what makes `/reports/run` write into
the right person's history rather than the configured default's.

## C5. `IngestResources` was the wrong name once it stopped holding stores

The importer used to hold a graph, an index and three models together. With the stores
coming from the registry per job, what is left is only the models — so `IngestResources`
became `IngestModels` and `build_resources` became `build_models`. A name that says
"resources" while holding nothing that needs releasing is the kind of thing that gets a
`close()` call added to it later by somebody being careful.

## C6. Where the simulation writes

`lumen/simulation/__main__.py` opened a graph directly. Left alone it would have written a
corpus into a path nothing looks at any more, and reported success. It now leases the
default person's stores from the registry, which is both less code and the only version that
puts the writing where the service will later read it.

The same class of mistake was live in the test suite: the autouse fixture that keeps tests
off the real databases still set `LUMEN_GRAPH_DB_PATH`, a setting that no longer exists.
Every test that built an unconfigured `AppConfig` was quietly provisioning a graph in the
repository's own `data/` directory. Now set to `LUMEN_GRAPH_DB_ROOT`.

## C7. Isolation, demonstrated rather than asserted

Goal 21 shipped a test that honestly recorded that one graph was shared. That test is now
the adversarial one the plan asked for: two people with real, separate stores, and every
read surface asked for the other person's identifiers.

Checked live through the running API as well as in the suite — two signed-in people, Alice
writes a lesson, Alice reads it back with a `200`, Bob asks for the same identifier and gets
`404`, and `/graph/stats` totals `1` for her and `0` for him.

Adoption was run for real on a copy of an existing single-user graph: the graph moved, three
search entries copied, the history readable afterwards under the account — and a second run
reported `already_done=True, graph_moved=False, points_copied=0`.

## C8. Result

**4994 passing, 0 failures.** 96% coverage on `lumen/stores/` — the remainder is defensive
`except` branches and the `__main__` guard.

Every named acceptance check in A6 holds:

1. The adversarial two-person test, across every read surface — one file, 12 tests.
2. Concurrent writers do not collide and do not see each other.
3. The registry past its ceiling evicts and reopens, and never closes a handle in use — the
   borrow count is what makes that true rather than luck about timing.
4. Provisioning interrupted between the graph and the index is caught at first use and
   raised as `HalfProvisioned`, not served as an empty history.
5. A traversal sequence in an identifier is **refused, never sanitised** — `keys.py` has one
   regex and no cleaning path, so there is no version of the input that becomes a path.
6. The migration moves the existing history under a real account, and is idempotent.
7. With sign-in off, the single-user deployment still works: one person, one store,
   everything where the migration put it.

**What is still true and worth naming.** The single-writer constraint is now per person
rather than global — two people can be written to at once, one person cannot be written to
by two processes. That is a real improvement and not a solution; the lock is still inside
one process, and the deployment that first runs two of them will need something both can
see. It is named in A5 rather than half-built here.
