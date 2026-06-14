# Privacy Architecture

*Privacy in Smriti is a technical guarantee, not a policy document. This file defines the concrete implementation rules for each privacy tier.*

## The Three-Tier Routing Model

| Tier | LLM Routing | Embedding Model | Notifications | RAG Inclusion | Storage Location | Backup Policy |
|---|---|---|---|---|---|---|
| **STANDARD** | Gemini Flash | `text-embedding-004` | Yes | Always included | Local DB | Optional Cloud |
| **ELEVATED** | Gemini Flash | `text-embedding-004` | Masked | Included | Local DB | Optional Cloud |
| **CRITICAL** | Local (Ollama) | Local (`nomic-embed-text`) | Never | Explicit request only | Local DB | Disabled by default |

## Local LLM Setup (CRITICAL Tier)
- Required runtime: **Ollama**
- Recommended models: Llama 3.3 8B, Gemma 3 4B, Mistral 7B
- `CRITICAL` content **never** leaves the device at any stage (extraction, embedding, RAG generation)
- If local LLM is unavailable: `CRITICAL`-tier observations are queued locally and not processed until the local LLM is available. They are never routed to the cloud as a fallback.

## Embedding Privacy
- `CRITICAL` observation content must use a local embedding model.
- Recommended: `nomic-embed-text` (via Ollama), `mxbai-embed-large`.
- Even if the LLM used for extraction is local, using a cloud embedding API (e.g., Gemini Embeddings) for `CRITICAL` content breaks the privacy guarantee.
- `CRITICAL` embeddings are stored in a separate, isolated vector store partition.

## Output-Layer Guardrails
- The RAG retrieval layer restricts `CRITICAL` content from context by default (see `Architecture.md`).
- Additionally: all generated responses pass through an **output scrubbing pass** before display.
- Output scrubbing checks: does the response imply or infer `CRITICAL` content from `STANDARD` context? (LLM-based check using a local model).
- If scrubbing detects potential leakage: the response is blocked and the user is shown a generic "I don't have enough safe context to answer this" message.

## Backup & Sync Encryption
- All local database files (SQLite, vector store partitions) are encrypted at rest using AES-256 with a user-controlled key derived from device biometrics or a passphrase.
- OS-level backup (iCloud, Google Drive) is disabled by default for the Smriti data directory.
- User can optionally enable encrypted cloud backup with explicit consent.

## Prompt Injection Protection
- All journal content passes through an input sanitization layer before reaching any LLM prompt.
- Sanitization: strips known injection patterns (e.g., "ignore previous instructions", role-play overrides, markdown link injection).
- Content is wrapped in explicit XML delimiters in all prompts: `<journal_entry>...</journal_entry>`.

## DPDP Act & GDPR Compliance
- **Right to erasure:** via soft-delete + anonymization (see `Graph/Schema.md` for mechanism).
- **Data localization:** all data is stored locally on device by default (DPDP requirement for sensitive personal data).
- **Consent log:** every processing run logs: timestamp, model used, tier processed, consent version.
- **Right to portability:** export API produces a portable JSON/Markdown bundle of all nodes (excluding erased content).

## Person Entity Privacy Rules
- Person Entity nodes always inherit the highest sensitivity tier of any linked observation.
- A `PersonEntityNode` with any `CRITICAL`-linked observation is itself treated as `CRITICAL`.
- Person Entity nodes are never included in anonymized aggregate statistics (even for opt-in cross-user features).
