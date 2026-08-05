# Interface & Ingestion Architecture

The Lumen architecture decouples its underlying semantic extraction engine (the core Graph and Pipeline) from the interfaces used to capture data. The **Ingestion Layer** handles all conversational and textual inputs, normalizes them, and prepares them for batch extraction.

## The Ingestion Layer

The Ingestion Layer sits above Step 0 (Preprocessing) and introduces two distinct conversational interfaces.

### 1. Native Active Chat Interface
A built-in multi-turn chat application designed for reflection and retrieval. It supports two modes:
- **Reflection Mode**: The user types thoughts or responds to system-generated reflection prompts. These messages are appended to the Daily Session Buffer.
- **Query Mode**: The user asks factual or analytical questions about their past entries. This bypasses the extraction pipeline and directly queries the Knowledge Graph via Step 5 (Multi-Hop GraphRAG).

### 2. External Log Importer
An ingest mechanism for third-party conversational data. Supports two sub-formats:

#### 2a. Markdown Export (e.g., ChatGPT `.md` export)
- Normalizes the dialogue into standard turn-by-turn JSON.
- Identifies speaker roles via section headers (`**You**`, `**ChatGPT**`).
- **Logical Event Date** extracted from the markdown `# Header` date, filename, or manual UI input on upload.
- Strips AI-generated assistant turns from the extraction payload (only user turns are extracted).

#### 2b. Native JSON Export (e.g., direct API export or app-native format)
Lumen recognizes the following JSON structure as a first-class import format:
```json
{
  "id": "<uuid>",
  "title": "<session label>",
  "lastUpdated": "<ISO timestamp>",
  "messages": [
    { "id": "<uuid>", "role": "user" | "assistant", "content": "...", "timestamp": "<ISO timestamp>" }
  ]
}
```
Field mapping rules:
- `id` → `session_id` (used directly, already a UUID)
- `title` → `session_label` (e.g., `"June 27 B"` → `session_label: "B"`). If no title, defaults to `""`.
- `messages[*].role` → speaker label. `"user"` = User turn (extract). `"assistant"` = AI turn (strip — never extract as user content).
- **`event_date` derivation rule:** Use the date of the **first message's `timestamp`** field, not `lastUpdated`. `lastUpdated` may reflect an export timestamp rather than when the conversation occurred.
- `ingested_at` = system import time (set at ingest, not from file).

All imported payloads carry both `ingested_at` and `event_date` fields regardless of format.

## Daily Session Buffer (Batch Processing)
⚠️ **DEPRECATION NOTICE**: Real-time, message-by-message extraction is fully deprecated. Extracting mid-conversation pollutes the graph with unresolved cognitive distortions.

To prevent this graph pollution and avoid high computational costs, Lumen uses **Session-Level Extraction (Delayed Stage 1)** via an asynchronous batch model.

1. **Buffering**: All inputs are appended to a `Session Buffer` keyed by `(event_date, session_label)`. A day can have multiple concurrent sessions (e.g., `June 27 / "A"` and `June 27 / "B"`). These are kept as independent buffers — they do NOT merge into a single daily transcript, because the user has intentionally separated them by theme or context.

2. **Session Decay Trigger**: The system waits for a session to decay (2 hours of inactivity within that `session_label`, configurable via `LUMEN_SESSION_DECAY_MINUTES` — see `lumen.config.OperationalConfig`). If a user returns after decay and continues on the **same `session_label`**, new turns append to that buffer. If the user opens a new chat or imports a file with a different `session_label` on the same day, a new buffer is created. After final decay or manual "End Session", the buffer is sent to Step 0.

3. **Multi-Session Same Day**: A day may produce multiple independent sessions. Each session is independently preprocessed and extracted. The `SessionNode` in the graph carries `(event_date, session_label)` as a composite key. When querying "what did we discuss today?", the UI enumerates all `session_label` values for that `event_date`.

4. **Extraction**: Step 0 runs Dialogue Act Classification on each session buffer independently. Episodes are segmented within the buffer and fed through Steps 1–4. Cross-session connections (e.g., June 27 B referencing June 21 content) are resolved at the Retrieval and Reconciliation layers — not at the buffer level.

This decoupled architecture ensures the core graph remains pristine and batch-optimized while enabling fluid, near-real-time conversational interfaces.
