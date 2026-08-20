# Goal 20: The Gateway — Nothing Waits on Somebody Remembering to Press a Button

**Branch:** `goal20`
**Depends on:** Goal 10 (the pipeline and its one write path), Goal 16 (the conversation and
its buffers), Goal 17 (reports and the shadow scan), Goal 18 (the review queue's sweep),
Goal 19 (the maintenance jobs), Goal 3 (`find_decayed`, shipped and never called)
**Spec:** `docs/hld/Technical_HLD.md` §3.1 (the service registry — *Pipeline Orchestrator:
background thread in BFF; Scheduler: APScheduler in BFF*), §7.3 (real-time updates),
`docs/hld/Interface_Architecture.md` §"Session Decay Trigger",
`docs/hld/HLDv2.md` (the seven-step journey)

---

# SECTION A — LOGIC (please verify)

## Objective

Every part of Lumen works. Almost nothing runs on its own.

**A conversation held in Lumen never becomes part of the person's history.** Goal 16 built
the conversation and stores every turn. Goal 10 built the pipeline that turns a finished
conversation into knowledge. Goal 3 shipped the query that finds conversations which have
gone quiet — and wrote in its own plan that "the background watcher that calls it is Goal
10's". Goal 10 moved it here. **Nothing has ever called it.** Today the only way a
conversation becomes history is to export it and upload it back to yourself.

The same gap runs through everything built since. Reports know when they are due and
nothing asks. The review queue has housekeeping and nothing runs it. The shadow scan can
notice something shifting in real time and nobody is told. Each of those goals shipped an
endpoint and wrote "Goal 20 gets one endpoint to call".

This goal is the caller. It is also the last goal of the phase, so it is where the API stops
being a collection of routes and becomes a gateway: one process that runs the product,
tells the person what is happening while it happens, and can be started and stopped
cleanly.

## A1. What Gets Built

| | What it is |
|---|---|
| **The watcher** | The missing half of the product. A conversation that has gone quiet for two hours is handed to the pipeline automatically, so talking to Lumen is enough to build a history. |
| **The scheduler** | One clock owning every recurring job: the watcher, reports that are due, the two-day shadow scan, and the review queue's housekeeping. One thread, not four. |
| **Live updates** | A second WebSocket that says what is happening as it happens — a run starting and finishing, the review count changing, the day's nudge. The reply stream already exists; this is everything else. |
| **Shadow alerts reaching the conversation** | When the scan notices several beliefs moving at once, the assistant is told, inside the same token budget as everything else. |
| **A clean start and stop** | The whole product from one command, and a stop that lets what is mid-write finish. |

## A2. The Decisions Taken

**1. One clock, not four.** Every recurring job goes through a single background thread that
wakes on an interval and asks each job whether it is due. Four independent timers would be
four things to start, four to stop, four to reason about when the machine sleeps, and four
ways for two jobs to touch the graph at the same moment. The jobs stay exactly as they are —
each is already a method on a service that can be called by hand — and the scheduler only
decides *when*.

**2. A conversation is claimed before it is dispatched, in one statement.** The watcher looks
for conversations that have gone quiet, and so far so simple. But an imported conversation
sits in the same table in the same state while the import worker is running it, and a
check-then-act watcher would hand the same conversation to the pipeline twice — two runs,
two sets of history, from one evening. Claiming is a single conditional write: mark it
dispatched *only if* it is still open. Whoever loses the race sees it is no longer open and
moves on.

**3. The scheduler never runs two jobs at once, and skips rather than queues.** If a report
is still being written when the next tick arrives, that tick does nothing rather than
starting a second one. Jobs here are minutes long and none of them is urgent; a queue that
grows while the machine is busy is how a laptop waking from sleep starts nine reports.

**4. A job that throws costs that job one turn.** Each is run inside its own guard and its
failure is logged and counted. The alternative — one bad job killing the thread — is a
system that silently stops doing everything because one thing broke once.

**5. Nothing is scheduled by default in a test, and everything is by default in the app.**
The scheduler is built stopped. It runs when the application starts and not when a test
constructs one, so no test ever races a background thread it did not ask for.

**6. The live updates are a separate socket from the reply stream** (per the spec's own
split). They have different lifetimes and different failure meanings: a dropped reply
stream loses a sentence somebody is waiting for, a dropped event stream loses a
notification. Putting both on one socket would mean a reply stream that has to stay open
between conversations, and a notification that can interrupt a sentence mid-word.

**7. Events are broadcast and not delivered.** Anybody connected gets what happens from the
moment they connect; nothing is stored and nothing is replayed. A person who was not looking
is not owed a backlog — the queue count, the runs and the reports are all readable from
their own endpoints, and the socket is for watching, not for record-keeping.

**8. A shadow alert is one line in the briefing, inside the same budget.** Not appended
after it, not exempt from the token allowance, and not shown at all when the person sounds
like they are in distress — a system that interrupts a hard moment to report on itself has
misread what it is for.

## A3. Judgement Calls (flagging, not asking)

- **The watcher only dispatches conversations Lumen itself holds.** An imported one is the
  import worker's, and two owners for one conversation is how it gets processed twice.
- **A pipeline run is dispatched to the existing import worker's thread**, rather than being
  run on the scheduler's. That thread already exists, already holds the models, and already
  serialises runs so that two entries are never written at once. A second worker doing the
  same job differently is the thing to avoid.
- **The nudge is an event, not a notification.** Lumen sends nothing to anybody: it says on
  the socket that the day has something worth closing, and a client decides whether that
  becomes a banner, a badge, or nothing at all.

## A4. What Is Deliberately Not Built

| Not built | Why |
|---|---|
| Redis / RQ, and moving the worker out of the web process | The personal build is one process by design — `Technical_HLD.md` puts a background thread in the BFF and reserves Celery for production. Adding a broker and a second process for a single user builds the production topology to serve one person, and every job here is minutes long and nobody waits on it. It is a deployment change and it belongs with one. |
| Kuzu or Qdrant in server mode, and therefore replicas | Same reasoning, and both are embedded stores holding a file lock. This is what makes the single process correct rather than a compromise. |
| Semantic day grouping, multi-day import splitting | Routed here from Goal 5 as "the ingestion layer", which this goal is not. They are extraction features about how an entry is cut up, and they belong with the ingestion work rather than with the thing that presses the buttons. |
| Retention-policy erasure on a timer | Goal 19 left the initiator value in place and no policy. Deciding that a person's data expires is a product decision nobody has made; the scheduler is ready for it the day somebody does. |

## A5. How You'll Know It Works

1. **The named test.** Upload a conversation → the pipeline runs → ask a question → the
   answer is shaped by what was just imported. One test, the whole product.
2. Talk to Lumen, leave it two hours, and the conversation becomes history with nobody
   pressing anything.
3. That same conversation is picked up exactly once, even with the import worker running.
4. Watch the events socket during a run and see the run start and finish.
5. Answer a review item and see the count change on the socket.
6. Stop the application mid-run and find the run finished rather than half-written.

---

# SECTION B — LOW-LEVEL DESIGN

## B1. Module Layout

```
lumen/scheduling/__init__.py        ← exports Scheduler only
lumen/scheduling/contracts.py       ← Job, JobOutcome, SchedulerReport
lumen/scheduling/jobs.py            ← the four jobs, each a small object
lumen/scheduling/watcher.py         ← finding and claiming quiet conversations
lumen/scheduling/scheduler.py       ← the one thread and its clock
lumen/api/events.py                 ← the broadcaster, and what an event is
lumen/api/routes/events.py          ← GET /events/ws, GET /events/recent
```

Modified: `lumen/api/main.py` (lifespan starts and stops it), `lumen/api/deps.py`,
`lumen/operational/repositories.py` + `sqlalchemy_impl.py` (`claim_for_processing`),
`lumen/config.py` (`SchedulerConfig`), `lumen/query/assembly/` (the shadow line),
`lumen/api/static/` (a page showing what the clock is doing).

## B2. `contracts.py`

```python
class JobOutcome(BaseModel):     # frozen
    name: str
    ran: bool = True             # False when it was not due
    did: int = 0                 # how many things it acted on
    duration_ms: int = 0
    failure: str | None = None

class SchedulerReport(BaseModel):  # frozen
    at: datetime
    outcomes: tuple[JobOutcome, ...] = ()
    skipped: bool = False          # a tick that arrived while one was running

class Job(Protocol):
    name: str
    every: timedelta
    def run(self, now: datetime) -> int: ...
```

`Job` is a Protocol so a test supplies a counter and the scheduler is exercised with no
stores at all. Each real job is a small class holding the service it drives — a Strategy,
so adding a fifth job is a new class and a registry line rather than a change to the loop.

## B3. `watcher.py` — the missing half

```python
class DecayedConversationWatcher:
    name = "session-decay"
    def __init__(self, *, ops, worker, config) -> None
    def run(self, now: datetime) -> int
```

1. `ops.buffers.find_decayed(cutoff=now - session_decay_minutes, limit=…)`.
2. For each, skip anything an import owns — read off the buffer's own `source`
   (`IMPORT_JSON`/`IMPORT_MARKDOWN` belong to the import worker; `NATIVE_CHAT` and
   `VOICE_NOTE` are Lumen's own). A fact already on the row beats a second table lookup.
   Then `ops.buffers.claim_for_processing(session_id)`; a false answer means somebody else
   got there first.
3. Hand the claimed session to the ingest worker's queue.
4. Return how many were dispatched.

**Store change.** `SessionBufferRepository.claim_for_processing(session_id) -> bool` —
`UPDATE session_buffers SET status='DISPATCHED', decayed_at=… WHERE session_id=? AND
status='OPEN'`, answering on `rowcount`. The only new write, and the only way two owners
for one conversation is made impossible rather than unlikely.

**Worker change.** `IngestWorker.submit_session(session_id)` — the existing queue, carrying
a session rather than an import id. `run_once` grows a sibling `run_session` that skips the
import bookkeeping and goes straight to `build_decay_event` → `run_pipeline`.

## B4. `jobs.py` — the other three

| Job | `every` | What it calls |
|---|---|---|
| `ReportsDue` | 1 hour | `MacroextractionService.run_due(now)` |
| `ShadowScan` | 1 hour | `MacroextractionService.run_shadow(now)` |
| `ReviewSweep` | 6 hours | `ReviewService.sweep(user_id)` |

Each returns a count and nothing else; each is constructed with the service it drives, so
none of them knows what a scheduler is.

## B5. `scheduler.py`

```python
class Scheduler:
    def __init__(self, jobs: Sequence[Job], *, config, on_report=None) -> None
    def start(self) -> None                 # one daemon thread, safe twice
    def stop(self, timeout: float = 30.0)   # lets the running job finish
    def tick(self, now: datetime) -> SchedulerReport   # what a test drives
```

`tick` is the whole of the behaviour and takes its clock as an argument, so every rule —
due, not due, skipped, failed — is testable with no thread and no sleeping. The thread is a
`while not stopped.wait(poll_seconds)` loop calling `tick`, which is also what makes `stop`
immediate rather than up to one poll long.

Not APScheduler. The registry names it, and what it would contribute here is cron parsing
for four fixed intervals, against a dependency that brings its own executors and job stores
and a second idea of what "running" means. The loop above is twenty lines and every rule in
it is one we chose.

## B6. `events.py` — the broadcaster

```python
class Event(BaseModel):        # frozen: kind, at, payload
class EventBus:
    def publish(self, kind: str, **payload) -> Event
    def recent(self, limit: int = 50) -> list[Event]
    async def listen(self) -> AsyncIterator[Event]      # per-subscriber queue
```

Subscribers get a bounded queue each; a slow one drops its oldest rather than holding up
the publisher, because a browser left open on a sleeping laptop must not be able to stall
the pipeline. `recent` is a small ring buffer, for the page to draw on connect. Published
kinds: `run_started`, `run_finished`, `report_written`, `review_count`, `day_nudge`,
`erasure_finished`.

Publishing is a call the *services* never make. The scheduler publishes what its jobs did
and the worker publishes what a run did, both through a callback handed in at construction,
so nothing in `lumen/pipeline/` or `lumen/review/` learns that a socket exists.

## B7. The shadow line in the briefing

`AssembledContext` gains `alert: str | None`, and the assembler takes it as an argument
rather than fetching it. The assembler and the composer are pure over what they are handed
and must stay that way — a store reached from inside either of them would be the first one.

So the reading happens where a graph is already in hand: a small `ShadowAlertReader` in
`lumen/query/alerts.py` (`current(now) -> str | None`, reading the most recent `SHADOW`
report through `find_reports` and ignoring anything stale), injected into `ChatEngine`
exactly as the hit recorder is, and threaded `engine → compose → assemble`. A deployment
given no reader simply has no alerts, which changes nothing else.

The line is charged to the same token budget as everything else and is suppressed entirely
when the register is `CRISIS`, by the rule that already suppresses the rest.

## B8. Config

```python
@dataclass(frozen=True)
class SchedulerConfig:
    enabled: bool = _env_bool("LUMEN_SCHEDULER_ENABLED", True)
    poll_seconds: float = _env_float("LUMEN_SCHEDULER_POLL_SECONDS", 60.0)
    watch_every_seconds: int = _env_int("LUMEN_WATCH_EVERY_SECONDS", 300)
    reports_every_seconds: int = _env_int("LUMEN_REPORTS_EVERY_SECONDS", 3600)
    shadow_every_seconds: int = _env_int("LUMEN_SHADOW_EVERY_SECONDS", 3600)
    sweep_every_seconds: int = _env_int("LUMEN_SWEEP_EVERY_SECONDS", 21600)
    max_dispatch_per_tick: int = _env_int("LUMEN_MAX_DISPATCH_PER_TICK", 5)
    event_history: int = _env_int("LUMEN_EVENT_HISTORY", 50)
```

## B9. Tests

| File | Covers |
|---|---|
| `test_scheduling_scheduler.py` | Due and not due; a tick during a running tick is skipped and says so; a throwing job costs one turn and the others still run; start/stop is idempotent. |
| `test_scheduling_watcher.py` | A quiet conversation is dispatched; a busy one is not; an imported one is left to its owner; **claiming twice dispatches once**; the cutoff is honoured exactly. |
| `test_scheduling_jobs.py` | Each job calls its service and reports a count; a service failure becomes a failure on the outcome. |
| `test_api_events.py` | The socket sends what is published; a slow subscriber drops rather than blocking; `recent` draws the backlog. |
| `test_api_lifecycle.py` | **The named test:** ingest → pipeline → query → chat with the imported history in the briefing. |

## B10. Build Order

1. `claim_for_processing` and its test — the race is the hard part and everything else
   assumes it.
2. `contracts.py`, `scheduler.py` and its tests, with no real jobs at all.
3. `watcher.py`, `IngestWorker.submit_session`.
4. `jobs.py`.
5. `events.py` and the routes.
6. The shadow line.
7. `main.py` lifespan, the page, the end-to-end test.
8. `Master_Plan.md`, and Section C here.

---

# SECTION C — WHAT WAS ACTUALLY BUILT

Section B held up better than Goal 19's did. Five things came out differently.

## C1. The bus learns its loop from whoever subscribes

B6 had the application bind the event loop to the bus at startup. That is wrong in a way
that only shows up as a hang: the test client runs the application on a portal thread, so a
loop captured at startup is not the loop a WebSocket handler is running on, and every
message is handed to a loop nobody is waiting on. The first socket test hung rather than
failed, which is the worst way to find out.

Subscribing is the only moment the right loop can be known for certain — that is where the
listener is. So `subscribe()` records it and `bind()` is gone. Publishing before anything
subscribes is still fine and still the ordinary state: it fills the backlog and returns.

## C2. The queue carries two kinds of work, not a flag

`IngestWorker` already had a queue of import identifiers. Adding a second kind by
convention — a string that might be either — would mean the drain loop guessing, and the
guess decides which table gets written when something fails. Two small frozen types
(`_Import`, `_Session`) make the queue say what each piece of work is. A bare string is
still read as an import, so nothing that queued one before this change breaks.

## C3. A failed run hands the conversation back

Not in the plan and necessary. `DISPATCHED` means somebody owns it; after a failure nobody
does, and leaving it there is how a conversation is never looked at again. `run_session`
puts it back to `DECAYED` — still finished, so it will be picked up again, but not re-opened
as though somebody were still talking in it. Verified against the running application: with
no model configured the run fails, the conversation goes back to decayed, and the scheduler
reports the pass.

## C4. The alert is charged to the budget by taking room off the top

B7 said "charged to the same token budget" without saying how. Adding it after selection
would have meant a briefing that fits its allowance and then exceeds it, every time the scan
fires. `Policy.with_less_room(spent)` takes the cost out before the records are chosen, so
the ceiling means the same thing whatever the briefing is made of.

The alert also survives a turn that found no history at all: it is a fact about the last two
days rather than about what this turn asked for. And it is suppressed entirely in crisis, by
the rule that already suppresses everything else.

## C5. Not APScheduler, and the reasoning is worth keeping

The service registry names it. What it would have contributed here is cron parsing for four
fixed intervals, in exchange for a dependency with its own executors, its own job stores and
its own idea of what "running" means. `scheduler.py` is ninety lines and every rule in it —
skip rather than queue, a failed job costs one turn, due immediately on a restart — is one
we chose and can point at a test for. Recorded as a divergence from the registry rather than
left as an unexplained absence.

## C6. Smaller corrections

- **`find_decayed` returns open conversations only**, so a second watcher pass never sees a
  claimed one. The claim still matters for the case the watcher cannot see: two things
  reading the same list in the same instant, which is what the losing-side test drives
  directly.
- **The watcher reads ownership off the conversation** (`source`) rather than looking up the
  import table. The fact is already on the row, and one read is better than two.
- **The event bus was made before the stores**, because the importer announces what it does
  and is built early. It needs nothing to start.
- **Tests own their own event loop** with `asyncio.run` rather than adding an async-test
  plugin. The bus is used from ordinary threads *and* from the loop, so a test that owns its
  loop explicitly is a closer match to how it actually runs — including one test that
  publishes from a real second thread.

## C7. Result

4691 passing (125 new), with 100% coverage on every new module
(`scheduling/` and `query/alerts.py`), with `api/events.py` covered through the whole-suite
run for the same numpy-under-coverage reason recorded in Goal 19.

The named test passes: a conversation held in Lumen, left to go quiet, becomes history with
nobody pressing anything — and is picked up exactly once. Verified again against the running
application, where the watcher noticed a quiet conversation, claimed it, dispatched it, and
the whole chain reported itself on the socket.
