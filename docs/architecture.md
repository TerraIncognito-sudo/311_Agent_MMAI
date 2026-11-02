# Architecture

```
 Browser                                   Local server (this repo)                         Cloud
 ───────                                   ────────────────────────                         ─────
 chat page  /  /admin   HTTPS via          src/web/server.py (FastAPI, 127.0.0.1:8311)
  - model picker   ──► Cloudflare ──────►   │
  - OpenRouter key      Tunnel             src/agent/orchestrator.py ── src/agent/flow.py
    (page memory only)                       runs the interaction tree      (engine: checks,
                                             (flow.yaml) at every hook       actions, trace)
                                             model <-> tools loop ───────────────► src/models/llm.py
                                                                                    ├─ Ollama (local)
                                                                                    └─ OpenRouter (user's key)
                                             every step and tree node -> interaction_events (masked, D6)
                                                    │
                                             src/config.py ── all config ────────┐
                                             src/tools/ ── policy_lookup         │
                                                         ── classifier           │
                                                         ── search/create/       ▼
                                                            get_status/handoff ─► Supabase (Postgres + pgvector)
                                                         ── kb_search ──────────►  config_files (+ revisions):
                                                                                     app, policy, guardrails,
                                                                                     flow, prompts
                                                                                   tickets, handoffs,
                                                                                   interaction_events,
                                                                                   kb_documents, kb_chunks
```

## Configuration lives only in Supabase

Every file the agent reads (`app.yaml`, `policy.yaml`, `guardrails.yaml`,
`flow.yaml`, `prompts/...`) is a row in Supabase `config_files`; every save
is kept in `config_revisions`. The repo has no copy for the site to fall
back on, so nothing goes stale. `src/config.py` loads them once, keeps them
in memory, and:

- reloads at once when `/admin` saves a change (new conversations use it;
  conversations already open keep the system prompt they started with);
- picks up edits made straight in the Supabase table within
  `retrieval.sync_interval_s` (the server's sync thread);
- keeps using the last version loaded if Supabase is briefly unreachable.
  If it was never loaded, `/api/chat` answers 503 and `/api/health` reports
  `config: unavailable`.

The tests and offline runs (`USE_MEMORY_STORE=1`) use the in-memory store,
which starts from `tests/fixtures/config/`. The site never reads that
folder. `python run.py config pull tests/fixtures/config` refreshes it from
Supabase; `python run.py config push <dir>` seeds a new Supabase project.

## The interaction tree

`flow.yaml` is the operating policy's decision logic as data. The
orchestrator runs one tree at each fixed point of a turn (a "hook"):

| Hook | When | Default tree (rules) |
|---|---|---|
| `message` | a resident message arrives, after D2 redaction | D2 log, E1 911, E8 crisis + handoff, E7 handoff, S1 note to the model |
| `after_classifier` | classifier returned | E3 unsure: ask one clarifying question (repeat handoff switched off until the real classifier) |
| `after_search_tickets` | search returned | C6 duplicate found: use the duplicate wording, don't file a second |
| `before_create_ticket` | before creating | A4 already handed off, A8 one ticket, A2 look up first (block) |
| `after_create_ticket` | create returned | M3 confirm with the exact ID; A4 missing fields: M2 ask one, E6 hand off past the limit |
| `before_get_status` | before a status lookup | D5 both ticket number and postal code (block) |
| `tool_failure` / `model_failure` | still failing after one retry | E9 tool-failure wording + handoff |
| `step_limit` | no answer in `llm.max_tool_steps` | M4 handoff |
| `reply` | the model wrote a reply | D8 strip rule IDs, M3 invented ID / false ticket claim: block + handoff, C1 flag |

A node is a **decision** (`check` + params, `yes` / `no` branches; a missing
branch carries on) or an **action** (`continue`, `guide` the model, `flag`,
`strip_rule_ids`, or `reply` / `handoff` / `block`, which end the hook).
`enabled: false` switches a node off. The checks and actions are the
vocabulary in `src/agent/flow.py`; the tree decides how they are wired, so
policy owners can reorder, rewire, reword and switch steps off without code.
A new kind of check needs code.

Every node visited is logged as a `flow` event: the node, what the check
saw (`observed`), the branch taken, and the rule ID when it fired. `/admin`
replays a conversation step by step with the tree beside it and the path
highlighted.

Guardrails on edits: a tree save is refused if it points to a missing node,
loops, uses an unknown check or action, or leaves a [hard] rule (E1, A2, A8,
D5, E6, E9, M3) without a switched-on action that the agent can reach.
D2 redaction and D6 masking always run in code and can't be switched off.

`src/agent/rules.py` keeps the conversation facts the checks read (codes
looked up, tickets and IDs returned, attempts per missing field, times the
classifier was unsure).

## One turn

1. **Input screen** (`src/guardrails/input_screen.py`) redacts D2 items and
   records which pattern lists matched. Then the `message` tree runs; a
   `reply` or `handoff` there ends the turn without calling the model.
2. **Model loop.** The selected LLM gets the system prompt
   (`prompts/agent/...`) and the seven tools. Each tool call runs the
   `before_<tool>` tree (may block it), the tool (one retry, F1), then the
   `after_<tool>` tree (may hand off, or add `guidance` to the result the
   model sees). At most `llm.max_tool_steps` round trips.
3. **Reply.** The `reply` tree checks the model's reply before the
   resident sees it.
4. **Logging.** Every model call, tool call (arguments and result), tree
   node and the final reply go to `interaction_events` with masked text,
   tokens, latency and cost.

## State

Conversation state (messages, rule state) is kept in server memory keyed by
`interaction_id` and expires after `agent.session_ttl_minutes`. The
OpenRouter key is not part of that state: it arrives with each request and
is dropped after the call. Everything else lives in Supabase.

## Retrieval

`kb_search` calls the `match_kb_chunks` RPC. Full-text search
(Postgres `tsvector`) always runs; when `retrieval.embedding_provider` is
`ollama`, a pgvector cosine search runs too and the two rankings are fused
with reciprocal rank fusion. The policy store is NOT in the knowledge base:
routing, required fields and escalation triggers come only from
`policy.yaml` via `policy_lookup`.

The knowledge base lives only in Supabase (`kb_documents`). The server's
sync thread (`src/rag/ingest.py` `start_sync`) re-chunks new and edited rows
every `retrieval.sync_interval_s` seconds; `active = false` hides a document
at once.
