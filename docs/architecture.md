# Architecture

```
 Browser (chat page)                       Local server (this repo)                         Cloud
 ───────────────────                       ────────────────────────                         ─────
 src/web/static/        HTTPS via          src/web/server.py (FastAPI, 127.0.0.1:8311)
  - model picker   ──► Cloudflare ──────►   │
  - OpenRouter key      Tunnel             src/agent/orchestrator.py
    (page memory only)                       1. src/guardrails/input_screen  (E1 911, E7, E8, S1, D2 redaction)
                                             2. model <-> tools loop ───────────────► src/models/llm.py
                                                 every call checked by                  ├─ Ollama (local)
                                                 src/agent/rules.py                     └─ OpenRouter (user's key)
                                             3. src/guardrails/output_check (M3)
                                             4. every step -> interaction_events (masked, D6)
                                                    │
                                             src/tools/ ── policy_lookup ── config/policy.yaml
                                                         ── classifier ──── src/models/classifier.py
                                                         ── search/create/get_status/handoff ─┐
                                                         ── kb_search ── src/rag/ ────────────┤
                                                                                              ▼
                                                                                  Supabase (Postgres + pgvector)
                                                                                   tickets, handoffs,
                                                                                   interaction_events,
                                                                                   kb_documents, kb_chunks
```

## One turn

1. **Input screen** (`src/guardrails/input_screen.py`). D2 items are redacted
   first. An emergency (E1) returns the approved 911 wording and never calls
   the model. Self-harm (E8) and requests for a person (E7) hand off at once.
   Possible injection (S1/S5) is flagged to the model as data.
2. **Model loop** (`src/agent/orchestrator.py`). The selected LLM gets the
   system prompt (`config/prompts/agent/`) and the seven tools. Each tool
   call passes through the rules layer (`src/agent/rules.py`), which can
   block it (A2 lookup-before-create, A8 one ticket, D5) or force a handoff
   (E6 field attempts, E9 tool failure after one retry). At most
   `llm.max_tool_steps` round trips per turn, then hand off (M4).
3. **Output check** (`src/guardrails/output_check.py`). A ticket ID or
   reference that no tool returned blocks the reply and hands off (M3).
   Commitment phrases (C1, C2, C4) are flagged in the log.
4. **Logging.** Every model call and tool call is written to
   `interaction_events` with masked text, tokens, latency and cost.

## State

Conversation state (messages, rule state) is kept in server memory keyed by
`interaction_id` and expires after `agent.session_ttl_minutes`. The
OpenRouter key is not part of that state: it arrives with each request and
is dropped after the call. Tickets, handoffs, events and the knowledge base
live in Supabase. `USE_MEMORY_STORE=1` swaps Supabase for an in-memory
store (tests, CI, offline dev).

## Retrieval

`kb_search` calls the `match_kb_chunks` RPC. Full-text search
(Postgres `tsvector`) always runs; when `retrieval.embedding_provider` is
`ollama`, a pgvector cosine search runs too and the two rankings are fused
with reciprocal rank fusion. The policy store is NOT in the knowledge base:
routing, required fields and escalation triggers come only from
`config/policy.yaml` via `policy_lookup`.

The knowledge base lives only in Supabase (`kb_documents`); there are no
article files in the repo. A background thread in the server
(`src/rag/ingest.py` `start_sync`) re-chunks new and edited rows every
`retrieval.sync_interval_s` seconds, so dashboard edits reach the agent
without a redeploy. `active = false` hides a document at once.
