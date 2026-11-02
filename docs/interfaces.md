# Interfaces

The joins between workstreams. Change a contract here only with the affected
owners agreeing (team work plan, "Interfaces to agree by Nov 5"). The first
six are the plan's contracts; the rest are additions for the website and
Supabase. They are marked **proposed** until D's Nov 5 interface session.

| Interface | Drafted by | Lives in | Used by |
|---|---|---|---|
| Interaction record | A | `data/*.jsonl` | W3, W4, W6 |
| Policy file | B | `config/policy.yaml` | W4, W1 tests |
| Tool functions | D | `src/tools/` | W4, W1, W5 |
| Classifier | C | `src/models/classifier.py` | W4, W6 |
| Event log | D | Supabase `interaction_events` | W6, W7 |
| Results file | F | `results/<run_id>.csv`, `results/summary.json` | W6, W7, report |
| LLM backbone (proposed) | D/C | `src/models/llm.py` | W4, W5, W6 |
| Knowledge base (proposed) | D/A | Supabase `kb_documents`, `kb_chunks` | W4, RAG team |
| Web API (proposed) | E | `src/web/server.py` | W5 demo |

## Interaction record (A)

JSONL, one object per line:

`interaction_id, split, channel, persona_id, template_id, message, expected_code, expected_action, required_fields_present, hazard_flag, difficulty, source (llm | hand), notes`

## Policy file (B)

`config/policy.yaml`. Per code under `service_catalogue`: `unit`,
`required_fields`, `escalation_triggers`, `duplicate_window` (plus
`category`, optional `field_options`). Global rules by ID under `rules`
(`hard: true` = enforced in code). Also `thresholds` (E3 confidence 0.70,
margin 0.10, field attempts 2, ...) and approved `wording`.

## Tool functions (D)

Each returns a dict with `ok` (bool) and `error` (str or None).

| Function | Returns when ok | Rule |
|---|---|---|
| `policy_lookup(code)` | code, category, unit, required_fields, field_options, escalation_triggers, duplicate_window | A2 |
| `search_tickets(location, code=None, days=None)` | tickets: [{ticket_id, code, status, date_opened}] (nothing else, D3) | A3 |
| `create_ticket(code, fields)` | ticket_id, code, category, unit, location. On missing fields: ok False, `missing: [...]` | A4 |
| `get_status(ticket_id, postal_code)` | ticket_id, code, status, unit, date_opened | A5, D5 |
| `handoff(rule_id, package)` | ref_id, rule_id. Package: interaction_id, code, confidence, fields, transcript, possible_duplicates, reason (masked on write) | A6 |
| `kb_search(query, top_k=None)` **proposed** | passages: [{title, source, text}] | C5 |

Infrastructure failures return `ok: False` plus private `_failure: True` /
`_detail` keys; the orchestrator strips `_` keys before the model sees the
result, retries once (F1) and then hands off (E9).

The model sees these as tools named `classifier`, `policy_lookup`,
`search_tickets`, `create_ticket`, `get_status`, `handoff`, `kb_search`
(`src/tools/registry.py`). There is no edit/close/cancel tool (A7).

## Classifier (C)

`classify(text) -> {code, confidence, top2}`; `top2` is
`[{code, confidence}, ...]`. Same call for prompted and fine-tuned models.
`src/models/classifier.py` is a keyword stub until the real model lands.

## Event log (D)

Supabase table `interaction_events`:

`event_id, interaction_id, step, model, tokens_in, tokens_out, cost_usd, latency_ms, rule_id, outcome, text (masked)` (+ `created_at`)

`step` values written today: `input_screen`, `model`, `tool:<name>`,
`output_check`, `rule`. `model` is `provider:model_id`. `cost_usd` is
OpenRouter's reported cost, 0 for Ollama.

## Results file (F)

`results/<run_id>.csv`, one row per test case per setup, plus
`results/summary.json`.

## LLM backbone (proposed)

`chat(LLMSelection(provider, model, api_key), messages, tools) -> LLMResponse(message, tokens_in, tokens_out, cost_usd, latency_ms)`.
Providers: `ollama` (local, `OLLAMA_BASE_URL`) and `openrouter` (key supplied
per request by the user, never stored). Both use the OpenAI chat-completions
format with tool calling. `LLMError.retryable` separates outages (retry,
then E9) from setup problems (bad key, unknown model; shown to the user).

## Knowledge base (proposed)

- `kb_documents(id, source unique, title, content, content_hash, tags, active, updated_at, chunked_at)`
- `kb_chunks(id, document_id, chunk_index, content, fts, embedding vector(768))`
- RPC `match_kb_chunks(query_text, query_embedding default null, match_count)`
  returns `(id, document_id, title, source, content, score)`; full-text always,
  plus vector search fused by RRF when an embedding is passed.
- Supabase is the only source of KB content. The server re-chunks new and
  edited `kb_documents` rows every `retrieval.sync_interval_s` seconds.
- `python run.py ingest` re-chunks now; `--import DIR` bulk-uploads a folder
  of `.md`/`.txt` files first.

## Web API (proposed)

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/config` | | site text, providers, default models |
| GET | `/api/models` | `?provider=ollama\|openrouter` | models with tool support |
| POST | `/api/chat` | `{interaction_id?, message, provider, model}` + header `X-OpenRouter-Key` (OpenRouter only) | `{interaction_id, reply, rule_id}` |
| GET | `/api/health` | | `{ok, store: supabase\|memory}` |
