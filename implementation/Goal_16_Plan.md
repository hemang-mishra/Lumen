# Goal 16: The Conversation Itself — Streaming, Voice, and Continuity Across Days

**Branch:** `goal16`
**Depends on:** Goal 13 (reading a turn), Goal 14 (fetching what it points at), Goal 15
(building the prompt), Goal 4 (the model providers), Goal 3 (the operational store)
**Spec:** `docs/Query/Conversational_RAG_Mode.md`, `docs/hld/Technical_HLD.md` §3.1 and §6

---

# SECTION A — LOGIC (please verify)

## Objective

Goal 15 ends with *exactly what the assistant would be sent* and stops there. Nothing
generates a reply, nothing streams one, nothing speaks or listens, and every conversation
opens with no memory of the day before.

This goal closes all of that. At the end of it Lumen is a thing you can talk to.

## A1. What Gets Built

| | What it is |
|---|---|
| **The turn** | One object that owns the whole sequence — read the sentence, fetch the history, build the prompt, write the reply, store it, tidy up afterwards. The web layer and the command line both drive the same object. |
| **Streaming** | The reply appears as it is written. Our model connectors learn a "send it as you go" capability they do not currently have. |
| **Voice** | Speak instead of typing, and hear the reply back. |
| **Continuity across days** | Today's conversation opens knowing what the last few days were about. |
| **Editing, honestly** | Rewrite something while the day is still open. Once a day has become history, it is frozen — and you are offered the thing you actually want instead. |
| **Past days** | Open an earlier day and read it back. |

## A2. The Decisions Taken

**1. Real streaming, at the connector layer** (per explicit user decision). The alternative
was to wait for the finished reply and then reveal it gradually, which looks identical and
helps nobody — the person still waits the whole time before the first word. So the model
connectors gain a genuine streaming capability. The cost lands in one place and everything
above it just reads words as they arrive.

**Consequence worth stating: a streaming reply cannot be retried.** Every other model call
in the system retries quietly on failure. Once words are on someone's screen they cannot be
un-said, so a failure halfway through a reply ends the turn with what was written plus an
honest error. This is the same reasoning that switched retries off for the turn-reader in
Goal 13, arrived at from the other direction.

**2. The chat model is configured on its own** (per explicit user decision). A fifth job
description joins the existing four. The model that writes a warm reply in under a second
and the model that does the overnight extraction reasoning have genuinely different
requirements, and locking them together would mean every improvement to one is a regression
to the other.

**3. Continuity across days is the last three days' own summaries** (per explicit user
decision). Every day already writes a summary of itself; today's prompt opens with the last
few. No model call, no new storage, three extra database reads.

Two clarifications that matter:

- **"The last three days" means the last three days that have a conversation**, looking back
  up to a fortnight — not the last three squares on the calendar. Somebody who journals
  twice a week would otherwise get nothing, which is the exact case the continuity is for.
- **This does not weaken the day boundary.** The day still decides what gets extracted
  overnight and what gets locked away again. What crosses is reading material for the
  assistant, and nothing structural.

**4. A day's summary carries over even when the day was a hard one** (per explicit user
decision). The system deliberately re-locks its most guarded *stored records* at midnight
until the person reopens the subject themselves. That protection is unchanged. But a summary
of a conversation the person themselves had is their own words about their own day, and
withholding it would make the continuity vanish on exactly the days it mattered most.

**5. A day is frozen once it has become history** (per explicit user decision). While a
conversation is still open, editing works as it already does — the rewrite is stored beside
the original and the thread simply reads from the new one. Once the day has been handed to
the extraction pipeline, editing is refused.

The reason is that the alternative is silently broken today. An episode is identified in the
graph by its **date and position**, not by what it says — so a re-run of an edited day finds
that identifier already present and skips the whole episode without a word. The conversation
and the graph would disagree permanently with nothing anywhere reporting it.

**And a refusal is not the end of it.** The person is offered the thing they actually
wanted: say it again today. The correction becomes a new entry, and the graph has known how
to handle somebody changing their mind since Goal 9 — that is what contradiction and
belief-evolution are for. You do not rewrite your past; you revise your view of it, and the
revision is itself worth recording.

**6. Regenerating reuses the prompt, and only re-asks the model.** Nothing about the turn
changed, so re-running the search would pay for a second lookup to get the same answer. The
new reply is stored beside the old one, which the branching from Goal 15 already supports.

**7. The searching side's working memory still resets at midnight** (per explicit user
decision). That is a short-term working set — the handful of records already surfaced today,
kept so the same five are not re-fetched every turn. Carrying it across days would quietly
narrow what the person can be reminded of tomorrow. The continuity lives in the summaries
and in the graph, which is where it belongs.

**8. Voice input has no local option, and this is said out loud.** Every other job in the
system can be pointed at a model running on your own machine. Speech-to-text cannot: the
local runtime the project already uses does not do it at all. So this is the one place where
the promise of a fully local deployment does not hold, and a deployment that requires it
simply does not get voice. Better stated in the plan than discovered in production.

**9. A spoken turn is marked as spoken.** The extraction pipeline has had a "was this voice
or typed?" flag since Goal 5, precisely so its transcript cleaning fires on speech and not
on typing — and nothing has ever set it, because nothing ever spoke. Now something does.

## A3. What One Turn Costs

| | Model calls | Notes |
|---|---|---|
| Reading the turn | 0–1 | Unchanged. Shortcuts skip it entirely. |
| Fetching the history | 0–2 | Unchanged. |
| Carrying the last three days | **0** | Rows already written |
| **Writing the reply** | **1** | New, and it is the one the person is waiting on |
| Speaking it | 0–1 | Only in voice mode |
| Summarising afterwards | ~⅛ | Already built, now actually called |

## A4. The Last Two Decisions

**10. The assistant speaks once the reply is finished** (per explicit user decision). One
piece of audio per reply rather than sentence-by-sentence as it streams. The text still
streams; the voice arrives at the end. Far simpler, one request per reply, and no
sentence-boundary detection on a live stream.

**11. A late lookup is carried into the next turn** (taken as my call, easily reversed).
On the rare turn where fetching the history runs past its 8 seconds, that turn is answered
with no history — and the lookup, which is still running and cannot be stopped, is caught
when it lands and held. The next turn opens with it, marked as slightly stale and ranked
below anything fresh. Nothing found is thrown away.

This is the spec's own "carry-forward" rule, and Goal 15 already built the machinery for
injecting a briefing marked stale and ranked lower — it has simply been unreachable, because
nothing ever produced one. Two things make it honest rather than merely thrifty:

- **A carried result is re-checked before it is used, not just re-ranked.** It was fetched
  against the previous turn and never passed the sensitivity gate, because the pass it came
  from never finished. It goes through that gate against the *current* state of the
  conversation, which may have changed in between.
- **It is only ever carried one turn.** A finding about a question the conversation has
  already moved past twice is worth less than nothing.

*If you had something else in mind here, say so — it is one branch in the turn sequence and
swapping it costs nothing.*

## A5. What This Goal Leaves Undone

| Deferred | To |
|---|---|
| Automatically handing a finished day to the extraction pipeline — the storage and the trigger point are here, the background watcher is not | Goal 20 |
| Time decay in the ranking | Goal 19 |
| The review queue's cap, snooze and auto-resolve | Goal 18 |
| A real front end — the pages here stay deliberately plain | later |

## A6. The Risks Worth Naming

1. **The reply is the product, and no test can judge it.** A test proves the right history
   reached the model and that it arrived in time. Whether the answer is any good is a
   judgement made by reading, which is why the command-line simulation ships with this goal
   and prints what went in alongside what came out.
2. **Three days of summaries is a lot of words in front of every turn** — potentially as
   much as the history briefing itself. It gets its own allowance and its own line in the
   token count, and it is the first thing to look at if replies start feeling unfocused.
3. **Failure halfway through a reply is a new kind of failure**, and it is visible to the
   person in a way no other failure in this system is.
4. **The speaking models are preview-grade** and produce audio in a raw form that needs
   assembling by hand. They can change under us.
5. **A summary of a day is not a transcript of it.** Carrying three of them forward carries
   three compressions, each of which was already a compression. Errors do not get corrected
   by being carried.

## A7. Definition of Done

1. You can hold a conversation — typed or spoken — and the reply streams back, with the
   person's own history behind it and no sign of the machinery.
2. Opening a chat today, the assistant knows what the last few days were about.
3. A day still open can be edited; a day already turned into history refuses, and says what
   to do instead.
4. A past day can be opened and read.
5. The whole thing runs from a command line, printing what was injected and how long each
   part took, against a real graph.
6. ≥90% coverage on new code (the repo's working standard is 100%).

---

# SECTION B — LOW-LEVEL DESIGN

## B1. Files

**New — `lumen/query/chat/`:** `engine.py` (`ChatEngine` — the turn, end to end),
`events.py` (what a turn emits as it happens), `contracts.py` (`TurnResult`,
`ReplyChunk`), `errors.py` (`ConversationFrozen`).

**New — `lumen/api/routes/chat.py`:** the WebSocket and the handful of POSTs and GETs
around it.

**New — `lumen/chat/__main__.py`:** the command-line conversation, interactive and scripted.

**New — providers:** `lumen/providers/audio.py` (transcription and speech, Gemini-backed),
plus streaming added to `base.py`, `gemini.py`, `ollama.py`, `fake.py`, `protocols.py`.

**Amended:** `lumen/query/memory/stage.py` (previous days), `lumen/query/memory/contracts.py`
(`Recollection.previous_days`), `lumen/query/prompting/system.py` (rendering them),
`lumen/query/conversation.py` (the freeze rule), `lumen/operational/{models,schemas,
repositories,sqlalchemy_impl}.py` + migration `0005_chat_voice`, `lumen/schemas/enums.py`
(`ModelRole.CONVERSATION`), `lumen/config.py`, `lumen/providers/factory.py`,
`lumen/api/{deps,main,schemas}.py`, `lumen/pipeline/orchestration/runner.py` (the
divergence warning), `lumen/api/static/`.

## B2. The turn, end to end

`ChatEngine.say(user_id, text, *, at, modality) -> Iterator[TurnEvent]`

Built as an object holding the formulator, retriever, composer, memory and the reply model —
the same reasoning that made `QueryFormulator` and `ConversationalRetriever` objects. It owns
no store of its own and reaches for no clock; everything is injected.

```
 1. session   = registry.open(user_id, at=now)        # rolls over at midnight
 2. buffer    = memory.store.open(user_id, on=session.event_date)
 3. on roll-over: force one summary refresh on the day just closed   (B5)
 4. store the user's turn                             # before anything can fail
 5. signal    = formulator.formulate(turn, session)   # 600ms deadline
 6. bundle    = retriever.retrieve(signal, session)   # 8s shared deadline
 7.            → anything carried from last turn is gated, ranked ×0.9, merged in;
 8.              anything that misses the deadline is caught on landing and held (B2a)
 8. recall    = memory.recall(buffer.session_id)      # + the last three days
 9. prompt    = composer.compose(bundle, signal, recall)
10. stream the reply, emitting each piece as it arrives
11. store the assistant's turn once the stream ends
12. after the reply has gone out: memory.refresh(buffer.session_id)
```

Steps 4 and 11 bracket everything that can fail, so a turn that dies mid-reply leaves the
person's own words stored and the partial reply recoverable. Step 12 is the call Goal 15
built and deliberately left uncalled — it belongs to whatever owns the turn, and this is it.

The engine yields events rather than returning a value, so the WebSocket, the command line
and the tests all consume the same sequence:

| Event | Carries |
|---|---|
| `turn.accepted` | the stored message id |
| `context.ready` | register, triggers, what was injected, latencies — for the debug surfaces |
| `reply.delta` | one piece of text |
| `reply.done` | the whole reply, the stored message id, token counts, time-to-first-token |
| `audio.delta` | one piece of speech (voice mode) |
| `error` | what failed, and whether anything was already said |

## B2a. Catching a lookup that arrived too late

`DeadlineRunner.run_all` abandons a future when the clock runs out and attaches a callback
that only logs. Carry-forward needs that callback to do one more thing: hand the result
somewhere. `run_all` gains an optional `on_late(name, value)`, called from the pool thread
when an abandoned piece eventually finishes.

The retriever deposits into a one-slot mailbox on the `ChatSession`:

```python
@dataclass
class LateArrival:
    turn_index: int
    candidates: tuple[RetrievedNode, ...]
```

Written from a pool thread, read from the request thread, so the slot is guarded by a lock
and a newer arrival replaces an older one. On the next turn the retriever drains it, drops it
if `signal.turn_index - arrival.turn_index > 1`, and otherwise passes the candidates through
`gate.apply` against the *current* unlocked domains before `merge.merge` with a 0.9
multiplier. `AssembledContext.deferred` is set, which `block.py` already renders.

Two things this must not do. It must not treat a carried candidate as fresh — the gate check
is the reason, not the ranking. And it must not carry across a day boundary: the session
object is replaced at midnight and the mailbox goes with it, which is the existing behaviour
and wants no special case.

## B3. Streaming through the provider layer

`LLMProvider` gains one method:

```python
def stream_text(
    self, messages, *, system_instruction=None, temperature=None
) -> Iterator[TextChunk]: ...
```

`TextChunk` carries the text and, on the final chunk, the usage and finish reason — so
telemetry survives a shape that has no single response object to read them off.

- **Gemini:** `models.generate_content_stream`. The blocked-content check that `_read_response`
  performs on a finished reply has to run on the first chunk instead.
- **Ollama:** `chat(..., stream=True)`.
- **Fake:** slices a scripted reply into pieces, so every test above this layer exercises the
  real streaming path offline.

`BaseLLMProvider._send` wraps retry, timing, unpacking and logging around a call that returns
one finished thing, and streaming does not have that shape. A sibling `_stream` is added
rather than bending it: **no retry** (A2 §1), timing recorded as two numbers — time to first
chunk, which is what the person experiences, and total duration — and the usage log emitted
when the stream closes.

A failure after the first chunk raises a `StreamInterrupted` carrying what had already been
said, so the layer above can store the partial reply rather than losing it.

## B4. The conversation model role

`ModelRole.CONVERSATION` joins the enum; `ProviderConfig` gains its `(provider, model)` pair
and `LUMEN_CONVERSATION_PROVIDER` / `LUMEN_CONVERSATION_MODEL`; the factory resolves it.

Adding an enum member is additive — `DecisionAuditNode.model_role` accepts it without change —
but Goal 4's tests assert over the full set of roles and each will need the new member.
`TRANSCRIPTION` and `TTS` stop being Protocol-only in this goal (B7, B8), so after it every
declared role has an implementation for the first time.

## B5. Continuity across days

`SessionBufferRepository` gains one read:

```python
def recent_buffers(
    self, user_id: str, *, before: date, limit: int, label: str = "",
    lookback_days: int = 14,
) -> list[SessionBufferRecord]
```

Newest first, excluding `before` itself, skipping buffers with no summary and no messages.
The lookback ceiling is what makes "the last three days" mean the last three days that hold a
conversation (A2 §3).

`Recollection` gains `previous_days: tuple[DaySummary, ...]` — a date and a summary each,
oldest first. `build_system_prompt` renders them in their own short block above today's
summary, each labelled by how long ago it was in the same humanised form the briefing already
uses ("yesterday", "three days ago"), never as a raw date.

Withheld entirely in crisis, alongside today's summary and the briefing — Goal 15 settled
that somebody in the middle of a bad ten minutes does not need the last hour reflected back
at them, and the last three days even less so.

**Every past day needs a summary for this to work.** A short conversation never accumulates
enough turns to trigger a refresh, so it would carry over as nothing. The fix is at the day
boundary: `SessionRegistry.open` already detects a roll-over and logs it; the engine now also
calls `refresh(force=True)` on the day just closed. One cheap call, once a day, at the moment
nobody is waiting.

Budget: three summaries at ~200 words is roughly 800 tokens, comparable to the whole briefing
allowance. It gets `LUMEN_CHAT_PREVIOUS_DAY_TOKENS` and is counted in `ChatPrompt.estimated_tokens`,
oldest day dropped first when it does not fit.

## B6. Editing, freezing, regenerating

`ConversationStore.revise()` refuses when the buffer's status is anything but `OPEN`:

```python
raise ConversationFrozen(session_id, status, "say it again today")
```

The status enum — `OPEN → DECAYED → DISPATCHED → PROCESSED → DISCARDED` — has existed since
Goal 3, so "the window has passed" is a fact the system already holds. The API turns this
into `409` carrying the reason and the suggested alternative.

`regenerate(session_id, message_id)` re-composes nothing: it re-asks the model with the prompt
already built for that turn and stores the new reply as a sibling of the old one (A2 §6).

**The divergence warning.** `_run_one_episode` currently skips when an episode id is already
in the graph. It now also compares the episode's stored `raw_text_hash` against the text it
was handed, and logs a warning naming both when they differ. This does not fix anything — the
freeze rule is what prevents the situation — but a re-upload or an import correction can reach
the same state by another road, and a silent disagreement between the conversation and the
graph is the single worst failure mode in this system.

## B7. Voice in

`AudioTranscriptionProvider.transcribe` is amended from `(audio_file_path: str) -> str` to
`(audio: bytes, *, mime_type: str) -> Transcript`. A web upload arrives as bytes; writing it
to disk to read it straight back would be pointless and would put somebody's voice on the
filesystem for no reason. `Transcript` carries the text plus the detected language and
duration, both of which the pipeline already has fields for.

Implemented against Gemini, whose models take audio directly and whose SDK is already a
dependency. There is no local implementation and there cannot be one (A2 §8).

`POST /chat/transcribe` rather than binary frames on the WebSocket: audio is a bulk upload,
the socket is for conversational back-and-forth, and a failed upload should not take the
conversation down with it. The client sends the returned text as an ordinary turn.

A spoken turn is stored with `modality: VOICE`, which needs a column on `buffer_messages`
(migration `0005`). Per-message rather than per-conversation, because a day where somebody
typed some turns and spoke others is the normal case, and marking the whole day as spoken
would run transcript-cleaning over text that was typed.

## B8. Voice out

`TTSProvider.synthesize` is amended from `(text, output_path) -> str` to
`(text) -> Speech` — bytes and a mime type, no file. Gemini-backed; its speech models return
raw PCM, so a WAV header is assembled at the provider boundary, which is exactly the kind of
vendor detail that must not leak upwards.

Called once, at `reply.done`, on the finished text (A4 §10). The audio is emitted as a single
`audio.delta` followed by the terminal event; nothing detects sentence boundaries and nothing
queues clips.

## B9. The API surface

New `lumen/api/routes/chat.py`:

| | |
|---|---|
| `WS /chat/ws` | the conversation; emits the B2 events |
| `POST /chat/transcribe` | audio in |
| `POST /chat/regenerate` | another reply to the same turn |
| `POST /chat/messages/{id}/revise` | edit, or 409 with the alternative |
| `GET /chat/days` | which days hold a conversation, and whether each is still editable |
| `GET /chat/days/{date}` | one day's thread, read-only |

Goal 13b pinned an allow-list of the POSTs that exist and why. It grows here and gains its
first WebSocket, so the guarantee needs restating rather than quietly widening: **no chat
route can reach a graph write.** The chat layer writes conversations, into the same buffer the
pipeline reads — which is the entire point of Goal 15's storage decision. The graph stays
reachable only through the importer's own thread.

A `/ui/talk.html` page carries the conversation with the injected context shown beside it,
plus past-day navigation. Deliberately plain, like the rest.

## B10. Config

`LUMEN_CONVERSATION_PROVIDER` / `_MODEL`, `LUMEN_TRANSCRIPTION_PROVIDER` / `_MODEL`,
`LUMEN_TTS_PROVIDER` / `_MODEL`; `LUMEN_CHAT_PREVIOUS_DAYS` (3),
`LUMEN_CHAT_PREVIOUS_DAY_LOOKBACK` (14), `LUMEN_CHAT_PREVIOUS_DAY_TOKENS`;
`LUMEN_VOICE_ENABLED` (false — voice needs a vendor that may not be configured),
`LUMEN_MAX_AUDIO_BYTES`.

A missing conversation model follows the pattern Goal 13 set: the service still starts, still
serves the graph, and refuses only the surface that needs it, with a message naming the fix.

## B11. Storage — migration `0005_chat_voice`

| Table | Added |
|---|---|
| `buffer_messages` | `modality` (`TEXT` / `VOICE`) |

Nothing else. The branching and summary columns landed in `0004`, and the previous-days read
needs no new storage at all — which is the whole reason that option was the cheap one.

## B12. Docs to amend ahead of coding

`Conversational_RAG_Mode.md`: continuity across days and why it does not weaken the day
boundary; the freeze rule and what is offered instead; streaming and the fact that a streamed
reply cannot be retried; voice. Its "Day Boundary & Wake Nudge" section describes a new-day
sequence that loads baseline patterns and generates a reflection prompt — **neither exists,
and this goal implements a different thing** (carried summaries); the section is corrected
rather than left describing unbuilt behaviour.
`Technical_HLD.md` §3.1 and §6: the fifth model role, the chat surface, and voice.
`LLM_Abstraction_Architecture.md`: streaming, the new role, and the honest note that one role
has no local implementation.

## B13. Test plan (~230 tests)

Streaming against mocked vendor SDKs, including a failure after the first chunk; the fake
provider's streaming; the engine's full sequence with every stage stubbed, and the ordering
guarantee that the user's turn is stored before anything can fail; the previous-days read,
including a person who journals twice a week and a day with no summary; the roll-over forcing
a refresh; the freeze rule at each buffer status; regenerate reusing the prompt; the
divergence warning; transcription and speech against mocked vendors; the WebSocket through
`TestClient`; the day-browsing endpoints.

The end-to-end test is the Master Plan's stated one and it runs the **real** stack against
Goal 12's five-day corpus: a scripted conversation, asserting that the history reaches the
model, that it is the history the corpus predicts, and that it arrives inside the budget.

## B14. Build order

1. `ModelRole.CONVERSATION`, config, factory.
2. Streaming: Protocol, base, fake, Gemini, Ollama.
3. `ChatEngine` and its events, against the fake — the whole turn, offline.
4. Previous days: repository read, `Recollection`, the prompt block, the roll-over refresh.
5. The freeze rule, regenerate, the divergence warning.
6. `routes/chat.py` and the WebSocket.
7. Voice in, then voice out (pending A4).
8. The command-line conversation and the end-to-end simulation.
9. Docs, Master Plan, Section C.

---

# SECTION B2 — SIX FIXES TO EARLIER GOALS, SHIPPED FIRST

A review of the whole query-side pipeline before building on it turned up six real
defects. All predate this goal; two of them Goal 16 would have built directly on top of.
They are fixed and tested ahead of the build rather than inherited.

## The two that mattered most, and they are one story

**1. A search that failed reported as "found nothing."** Each pass contained its own
failures so that one broken lookup would not cost the others — correct on its own. But a
contained failure returns an empty list, and a pass where *everything* was refused returned
an empty list too, with no error anywhere. `consulted_nothing` only counts a pass as
unavailable when it has a recorded failure, so **a dead index or an erroring graph was
reported as `NOTHING` — "this person has no relevant history."**

Both `semantic.py` and `structural.py` carried docstrings asserting the opposite guarantee.
Containment and the four-way outcome distinction were each sound, and together they
cancelled.

Fixed by counting: each pass now tallies the store calls it made and the ones that refused,
and raises `SearchUnavailable` when it has **nothing to show and something broke**.
Deliberately not "everything broke" — three searches where two refuse and the third honestly
finds nothing would pass that stricter test and give the same wrong answer, just harder to
reach. Nobody can say whether the refused call would have found something, so the honest
report is that the history could not be looked up.

**2. The distinction was then discarded one layer later.** `RetrievalBundle` carried
`search_failed`, `outcome` and per-pass failures; `ContextAssembler` read none of them. An
unreachable store and a person with no history produced **byte-identical system prompts**,
so an assistant handed one behaved as though it had never met them — the exact failure the
retrieval layer exists to prevent, reintroduced at the last step.

Fixed by carrying `search_failed` onto `AssembledContext` and rendering a short block that
tells the assistant its notes could not be reached, that this does not mean there are none,
and not to mention it. Withheld in crisis, where the instruction is replaced wholesale.

## The other four

**3. The 600ms formulation deadline covered only the model call.** The graph reads on either
side of it — era names before, the checks on the model's claims after — sat outside any
budget, and since Goal 13b those reads serialise against the importer's write transaction.
The stage is now one deadline-guarded unit.

That move surfaced a second problem worth naming: an abandoned reading goes on running with
nobody waiting for it, and it was calling `session.unlock()`. A turn the conversation never
used could unlock the most guarded records for the rest of the day. The work now returns a
`Reading` and **only the calling thread touches the session**, so an abandoned reading can
reach nothing.

**4. A transient graph blip disabled era retrieval for the whole day.** `era_vocabulary`
returned `()` on failure and the caller cached it, and `()` is not `None` — so one bad read
on the first turn silently switched off every era lookup until midnight, with nothing ever
trying again. It now returns `None` on failure and only a real answer is remembered.

**5. The buffer round-trip changed what a record was.** `BufferEntry` stored no `domain`,
`occurred_at` or `era_tag`, so a record re-offered from today's thread arrived claiming to
belong nowhere. The sensitivity gate reads `domain`, and treats "no area of life" as
sensitive-until-invited — so a CRITICAL record in an ordinary area was offered on one turn
and withheld on the next, for no reason the person could see. It also lost its date, so it
rendered undated and lost every tie. The three fields are now carried.

**6. Two different scales were held to one threshold.** Buffer relevance is a cosine when
both vectors exist and a word-overlap fraction otherwise, both compared against
`session_boost_threshold`. A cosine of 0.35 is a real resemblance; a word overlap of 0.35
is two of five keywords appearing as substrings, which happens by accident. The fallback was
therefore far more permissive than the measurement it stands in for, on exactly the turns
where the search had already failed to produce a vector. The stand-in gets its own harder
bar (`LUMEN_SESSION_BOOST_KEYWORD_THRESHOLD`, 0.6).

## What was deliberately not changed

`RetrievalBundle.search_failed` still requires *every* pass that consulted a store to have
failed. One working search is enough to say the graph was asked, and that is Goal 14's
deliberate rule rather than an oversight — the fix above is about making each pass tell the
truth, which is what any aggregate judgement has to be built on.

The `VULNERABLE` register restricts the briefing to standing records (patterns, beliefs,
lessons, principles). Observations vastly outnumber those in a real graph, so the register
that most needs gentle continuity is the most likely to get an empty briefing. Left alone
and recorded: it is a judgement about what helps somebody who is raw, and the way to settle
it is to read real briefings, not to change the rule on a hunch.

*Result: 3462 tests passing (3433 before, +29), 100% coverage on `lumen/query/` retained.*

---

# SECTION C — WHAT WAS ACTUALLY BUILT

*(filled in as the goal is built)*
