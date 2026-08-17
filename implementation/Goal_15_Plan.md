# Goal 15: Context Assembly, the Voice, and Conversation Memory

**Branch:** `goal15`
**Depends on:** Goal 13 (reading a turn), Goal 14 (fetching what it points at), Goal 3 (the operational store)
**Spec:** `docs/Query/Conversational_RAG_Mode.md` Stage 3 + Stage 5, `docs/hld/Technical_HLD.md` §6

---

## Objective

Goal 14 ends with a ranked list of records and nothing that reads them. This goal turns
that list — plus the conversation so far — into **exactly what the model will be sent**:
a system prompt in a warm, plain therapist's voice, the person's own history compressed
into a short briefing, and a transcript that stays coherent after two hours of talking.

Goal 16 then streams it, lets you edit a message, and takes voice.

---

# SECTION A — LOGIC (please verify)

## A1. What Gets Built

Four things, and they are all about *what the model sees*:

| | What it is |
|---|---|
| **The briefing** | Goal 14's records, compressed into one or two plain sentences each, ranked, and cut to fit a budget. "Pattern: avoiding solo trips. Seen 4 times. Usually fades after the first 15 minutes." |
| **The voice** | The system prompt. Who the assistant is, how it should speak, and what to do with the briefing — written the way ChatGPT's is: short, second-person, human, not a wall of policy. |
| **The memory** | The last dozen turns verbatim, and everything older folded into a running summary. This is what makes a three-hour conversation still make sense at the end. |
| **The chat itself** | Conversations stored properly, so a summary survives a restart, yesterday's chat can be reopened, and Goal 16 can edit a message without losing what was there before. |

The output is one object: **everything the model would be sent for this turn**. That is
the whole deliverable, and it is inspectable by hand before any chat UI exists.

## A2. The Decisions Taken

**1. Chat lives in the buffer the pipeline already reads** (rather than a new chat store).
The operational store has held a `NATIVE_CHAT` source since Goal 3, and Goal 13 built the
live session's identity to match the buffer's key. Following that closes the loop the
product is actually about: **today's conversation becomes tomorrow's graph** with nothing
to copy across. A second store would mean the chat and the history it produces were
different things that happened to look alike.

**2. Editing a message branches; nothing said is ever destroyed.**
Messages get a parent, so a conversation is a tree and the visible thread is one path
through it. Editing starts a sibling branch from the same point — ChatGPT's arrows — and
the old branch stays reachable. This is the same instinct as the graph's append-only rule,
applied to the conversation.

**Consequence worth stating:** the extraction pipeline reads the **active thread only**.
A message you edited away was said, but it is not what you settled on, and letting
abandoned branches become permanent history would record arguments you took back.

**3. The 400-token cap is replaced by a budget that fits the moment** (per explicit user
decision). The spec called 400 non-negotiable when the constraint was a 3-second wait.
That constraint is gone — you have said 5–10 seconds is fine and recall matters more — and
400 tokens throws away most of what Goal 14 worked to find. What ships instead:

| How the person sounds | Budget | Records | Quotes? |
|---|---|---|---|
| In crisis | nothing at all | 0 | — |
| Raw, exposed | ~400 tokens | 2 | No — patterns only, nothing quoted back |
| Ordinary | ~800 tokens | 4 | Yes |
| Thinking it through | ~1500 tokens | 6 | Yes, including cause-and-effect chains |

A light turn still gets a light briefing. The reason is not cost — it is that a wall of
history in front of a simple question makes the answer worse, not better.

**4. Retrieval gets longer to work in** (per explicit user decision). Goal 14's three-second
wall clock rises to eight, and its semantic pass from two seconds to six. A pause of a few
seconds before a thoughtful reply is normal in this kind of conversation; an answer that
missed the one relevant thing is not.

**5. The same insight twice is worse than two insights.** Records that say nearly the same
thing are collapsed, and no more than three of any one kind get through. Without this, a
strong theme fills the entire budget with variations on itself.

**6. The prompt tells the model to absorb the history, not recite it.** Most of what is
injected should never be visible to the person — it should show up as the assistant simply
understanding them. Explicit reference is allowed at most once every few turns, when the
connection is genuinely striking. That is a behavioural instruction, not a code gate, which
is what the spec settled on and what ChatGPT does.

**7. In crisis the prompt changes, not just the context.** No history is injected — and the
instructions themselves switch to a shorter, plainer set: be present, no analysis, no
pattern-spotting, no history. Withholding the briefing while still telling the model to
"connect this to what you know about them" would be half a decision.

**8. Older turns are summarised, not dropped.** Every few turns a cheap model call folds
everything past the recent window into a running summary of what this conversation has been
about. The summary is stored, so it survives a restart, and it is refreshed *after* a reply
goes out rather than while somebody is waiting.

## A3. What One Turn Costs

| | Model calls | Notes |
|---|---|---|
| Assembling the briefing | 0 | Templates and arithmetic |
| Building the prompt | 0 | Text |
| Refreshing the summary | 1, every ~8 turns | Off the critical path |
| **Total added by this goal** | ~0.1 per turn on average | |

## A4. What This Goal Deliberately Leaves Undone

| Deferred | To |
|---|---|
| The chat endpoint, streaming the reply | Goal 16 |
| Edit and regenerate as user actions (the storage they need ships here) | Goal 16 |
| Voice in and out — Whisper for transcription through the existing role | Goal 16 |
| Time decay in the ranking | Goal 19 |
| Reopening past days in the UI (the storage supports it) | Goal 16 |

## A5. The Risk Worth Naming

The compression templates are the place quality will be won or lost, and they are the one
part no test can really judge. A test can prove a briefing fits its budget and names the
right record; it cannot prove that *"Pattern: avoiding solo trips. Seen 4 times."* is more
useful to a therapist than the two sentences it replaced. That is a judgement to make by
reading real output, which is why the inspection endpoint ships with it.

## A6. Definition of Done

1. Given a turn and what Goal 14 fetched, the exact prompt the model would receive can be
   printed — briefing, voice, summary, recent turns.
2. The budget holds: a reflective turn gets more than an ordinary one, a raw one gets less
   and nothing quoted, a crisis turn gets none and a different instruction.
3. A conversation of forty turns still carries its opening, through the summary.
4. A chat survives a restart; an edited message leaves the original reachable.
5. The pipeline extracts the active thread and ignores abandoned branches.
6. ≥90% coverage on new code (the repo's working standard is 100%).

---

# SECTION B — LOW-LEVEL DESIGN

## B1. Files

**New — `lumen/query/assembly/`:** `stage.py` (`ContextAssembler`), `select.py` (register
policy, diversity, budget fitting), `templates.py` (per-kind briefings), `budget.py` (token
estimate + per-register budgets), `block.py` (the `[LUMEN CONTEXT]` rendering),
`contracts.py` (`ContextItem`, `AssembledContext`).

**New — `lumen/query/prompting/`:** `persona.py` (the voice, as text), `system.py`
(`build_system_prompt`), `contracts.py` (`ChatPrompt`).

**New — `lumen/query/memory/`:** `transcript.py` (summary + recent turns → messages),
`summary.py` (the rolling refresh), `contracts.py`.

**New — `lumen/query/conversation.py`:** `ConversationStore` — the narrow view of the
operational store that the chat layer uses (append a turn, read the active thread, save a
summary). Injected, so nothing here reaches for a database.

**Amended:** `lumen/operational/{models,schemas,repositories,sqlalchemy_impl}.py` +
migration `0004_chat` (message parents, branch pointer, stored summary);
`lumen/pipeline/orchestration/` (read the active thread); `lumen/config.py` (`ChatConfig`);
`lumen/api/` (`POST /query/prompt`, the inspection surface); Goal 14's budget defaults.

## B2. The output

```python
class ChatPrompt(BaseModel):
    system: str                     # persona + rules + briefing + summary
    messages: tuple[ChatMessage, ...]   # recent turns, oldest first
    context: AssembledContext       # what went in, and what was cut
    estimated_tokens: int
```

## B3. Assembly

```
assemble(bundle, signal, session) -> AssembledContext
 1. policy = POLICIES[signal.emotional_register]        # tokens, count, quotes, kinds
 2. crisis → empty, said out loud
 3. rank on Goal 14's score, newest first on a near-tie   (recency as tie-break only —
                                                           the decay curve is Goal 19's)
 4. drop near-duplicates (lexical overlap ≥ 0.8), cap 3 per kind
 5. render each through its template, quotes on/off per policy
 6. take while it fits the token budget; record what was cut and why
```

Templates keyed by node kind, with observation type as a second key where it matters
(`CONCEPTUAL_REFRAME`, `META_BELIEF`), and a generic fallback so an unusual record still
reads as a sentence. Dates are humanised — "last Tuesday", "three weeks ago", "in June" —
because "2026-06-11T10:30:00Z" in a therapeutic briefing is noise.

Tokens are estimated at 4 characters each rather than counted with a vendor tokeniser: the
number only has to be conservative, and adding a tokeniser would tie the layer to one
model's idea of a token.

## B4. The voice (`prompting/persona.py`)

Structure, in the order the model reads it:

1. **Who you are** — two or three sentences. Warm, direct, not clinical.
2. **How to be** — short imperative lines. Listen before interpreting. Use their words.
   Ask one question, not three. Don't diagnose. Don't perform empathy.
3. **What you know about them** — the briefing, then how to use it: absorb it, don't cite
   it; naming it explicitly is for when the connection is genuinely striking, at most once
   every few turns.
4. **Where the conversation is** — the rolling summary.
5. **If they are in trouble** — plain, human, no scripts.

A separate, shorter set of instructions replaces 2–4 entirely when the register is CRISIS.

## B5. Memory

```python
transcript(session_id, *, recent=12) -> (summary: str | None, turns: tuple[ChatMessage, ...])
refresh(session_id, *, every=8) -> bool     # one LIGHTWEIGHT call, after the reply
```

Summary and `summary_through_seq` live on the buffer row, so a restart resumes rather than
re-reads. The refresh folds *the previous summary plus the turns since it* — a summary of a
summary, which is how a long chat stays inside a fixed cost.

## B6. Storage (`migration 0004_chat`)

| Table | Added |
|---|---|
| `buffer_messages` | `parent_message_id`, `is_active_branch` |
| `session_buffers` | `active_message_id`, `rolling_summary`, `summary_through_seq` |

`active_thread(session_id)` walks parents back from the active leaf. `branch_from(message_id,
content)` writes a sibling and moves the leaf. Abandoned branches are kept and are not
extracted.

## B7. Config (`ChatConfig`)

`LUMEN_CHAT_RECENT_TURNS` (12), `LUMEN_CHAT_SUMMARY_EVERY` (8),
`LUMEN_CONTEXT_TOKENS_{VULNERABLE,STABLE,REFLECTIVE}` (400/800/1500),
`LUMEN_CONTEXT_RECORDS_*` (2/4/6), `LUMEN_CONTEXT_DUPLICATE_THRESHOLD` (0.8),
`LUMEN_CHARS_PER_TOKEN` (4.0). Goal 14's `LUMEN_RETRIEVAL_BUDGET_SECONDS` 3.0 → 8.0 and
`LUMEN_PASS_A_TIMEOUT_SECONDS` 2.0 → 6.0.

## B8. Docs to amend ahead of coding

`Conversational_RAG_Mode.md`: the 400-token cap and the 3-second window are both replaced,
per explicit user decision, with the reasons above; the compression templates as shipped;
the crisis prompt switch. `Technical_HLD.md` §6: memory, storage, and the fact that the
query layer now writes conversation state — while the graph stays read-only to it, which is
the guarantee that actually matters.

## B9. Test plan (~180 tests)

Budget and policy per register; every template including the fallback; date humanising;
duplicate collapsing; the block's shape; the prompt's structure and its crisis variant;
transcript assembly across a long chat; the summary refresh and its cadence; branch storage
and active-thread reads; the pipeline ignoring abandoned branches; the endpoint.

## B10. Build order

1. Storage + migration, then the pipeline's active-thread read.
2. `budget.py`, `templates.py`, `select.py`, `block.py`, `stage.py`.
3. `persona.py`, `system.py`.
4. `memory/`.
5. `ChatPrompt` assembly end to end, then `POST /query/prompt`.
6. Goal 14 budget defaults, docs, Master Plan, Section C.

---

# SECTION C — WHAT WAS ACTUALLY BUILT

## C1. Files

**New — `lumen/query/assembly/`:** `stage.py` (`ContextAssembler`), `budget.py` (the
per-register allowances and the token estimate), `templates.py` (a briefing per kind of
record), `select.py` (repeats, per-kind cap, budget fitting), `block.py` (the notes block),
`contracts.py`.

**New — `lumen/query/prompting/`:** `persona.py` (the voice — the one file here written to
be read by a person), `system.py` (assembling the instruction), `compose.py`
(`PromptComposer` → `ChatPrompt`), `contracts.py`.

**New — `lumen/query/memory/`:** `stage.py` (`ConversationMemory`), `prompts.py` (the
summary instruction), `contracts.py` (`Recollection`).

**New — `lumen/query/conversation.py`:** `ConversationStore` — append, revise, read the
thread, keep the summary.

**Amended:** `lumen/operational/{models,schemas,repositories,sqlalchemy_impl}.py` and
migration `0004_chat`; `lumen/config.py` (`ChatConfig`, and Goal 14's budget raised);
`lumen/api/{deps,main,schemas}.py` and `routes/query.py` (`POST /query/prompt`);
`lumen/api/static/chat.html`; `lumen/query/__init__.py`.

**Tests:** `test_assembly_templates.py`, `test_assembly_stage.py`,
`test_prompting_system.py`, `test_prompting_compose.py`, `test_query_conversation.py`,
`test_query_memory.py`, `test_api_prompt.py`.

## C2. Deviations From the Plan

1. **No `is_active_branch` column.** The plan had one; the thread is derived by walking
   parents back from `active_message_id` instead. One source of truth beats two that can
   disagree, and a conversation is small enough to walk in memory.
2. **`PromptComposer` was added** — not in the plan, which had the route assembling the
   parts itself. The whole deliverable is "exactly what the assistant would be sent", and
   that deserves to be one object rather than something the web layer happens to build.
3. **`ConversationMemory` exposes its store.** Saying something and remembering it are two
   halves of one job; a caller holding one should not need a second handle to the same
   conversation to do the other.

## C3. Things Caught While Implementing

1. **The briefing lowercased the first word, and turned "Alex called about it" into "alex
   called about it".** Caught by a test written on a hunch. There is no reliable way to tell
   a name from an ordinary word at the start of a line, and names are exactly what a
   briefing about somebody's relationships must not mangle — so the record's own
   capitalisation is now left alone. Every line already begins with its own label, so
   nothing was lost.
2. **The repeat check compared finished lines, not records.** Every briefing of a kind
   shares its scaffolding — "Pattern:", "Seen 3 times" — which inflated the similarity of
   two entirely unrelated patterns towards the duplicate threshold. It now compares what the
   records actually say.
3. **A live session and its stored conversation share an identity but not a name.** The
   store names a conversation itself (`sb_2026_08_17_main_a1b2c3d4`), and only that name
   finds it again. Assuming otherwise made the first end-to-end call fail.
4. **The persona ran to 505 words against its own 500-word rule.** The rule won: two
   overlapping instructions were merged rather than the limit raised. It is 280 now.
5. **Recalling a conversation nobody has started is not an error.** It raised, from three
   layers down. "This is the first thing anybody has said" is an ordinary state of a chat.

## C4. Honest Limitations

- **The templates cannot be tested for quality, only for correctness.** A test proves a
  briefing fits its budget and names the right record; it cannot prove the sentence is more
  useful than the two it replaced. `POST /query/prompt` and the panel on `/ui` exist because
  that judgement has to be made by reading.
- **The token estimate is four characters per token.** Deliberately not a real tokeniser —
  that would tie every budget in the system to one model's idea of a token — but it will be
  wrong by some margin on unusual text, always in the direction of over-estimating.
- **The summary is never re-checked.** Once a stretch of conversation is folded in, the
  wording of that fold is what survives; a mistake in it persists for the rest of the day.
- **Nothing yet calls `refresh()` automatically.** It is deliberately off the critical path,
  which means the thing that owns the turn has to call it — and that thing is Goal 16.

## C5. What Is Still Deferred

| Deferred | To |
|---|---|
| The chat endpoint, streaming, and calling `refresh()` after a reply | Goal 16 |
| Edit and regenerate as user actions — the storage and branching ship here | Goal 16 |
| Voice in, via Whisper through the existing `TRANSCRIPTION` role | Goal 16 |
| Time decay in the ranking | Goal 19 |

## C6. Result

3433 tests passing, 20 deselected (the opt-in live suites). **100% coverage** on
`lumen/query/` — every new package — and on `lumen/operational/sqlalchemy_impl.py`, which
the branching work brought from 99% to complete. 99% overall.
