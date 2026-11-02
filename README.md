# 311 Intake Agent: City of Gilead

A RAG-backed chatbot that takes non-emergency 311 service requests for the
fictional City of Gilead. It records and routes requests, it decides nothing
on the City's behalf. The rules it follows are in the operating policy
(`config/policy.yaml`); every guardrail and test cites a rule ID from it.

**All data is synthetic.** No real residents, addresses or City systems.

- Chat site: plain chat page, choose the model in the top-right corner.
- Admin page at `/admin`: walk through any conversation step by step
  against the operating policy, and edit config, prompts and the knowledge
  base in the browser (live, with history). No login yet.
- LLM backbone: local **Ollama** or **OpenRouter** (bring your own key, kept in
  the browser tab's memory only and never saved).
- Database and knowledge base: **Supabase** (Postgres + pgvector).
- Public access: Cloudflare Tunnel to the local server
  ([docs/deployment.md](docs/deployment.md)).

## Quick start

Requires Python 3.11+ and, for local models, [Ollama](https://ollama.com)
with at least one tool-capable model (for example `ollama pull qwen3:8b`).

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; on macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in the Supabase values
python run.py serve           # http://127.0.0.1:8311
```

No Supabase yet? Set `USE_MEMORY_STORE=1` in `.env` to run with an in-memory
store (nothing persists across restarts).

With Docker instead (how the team server runs it):

```bash
cp .env.example .env          # fill in the Supabase values
docker compose up -d --build  # http://127.0.0.1:8311
```

See [docs/deployment.md](docs/deployment.md) for updates, logs and the Cloudflare tunnel.

## Supabase setup (once per project)

1. Create a project at supabase.com.
2. SQL editor: run the files in `supabase/migrations/` in order, then `supabase/seed.sql`.
3. Project Settings > API: copy the project URL and the **service_role /
   secret** key into `.env` (`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`).
   The secret key stays on the server; RLS blocks the public anon key.
4. Add knowledge-base articles as rows in `kb_documents` (see below).

Tables: `tickets`, `handoffs`, `interaction_events` (event log),
`kb_documents`, `kb_chunks`, `config_files` and `config_revisions` (browser
edits of `config/`). Collaborators can browse and edit them in the Supabase
Table Editor, or use `/admin`.

## Working on the knowledge base (RAG)

The knowledge base lives only in Supabase; there are no article files in
the repo. The agent may state facts about City services only when `kb_search`
returned them (policy rule C5).

- Add, edit or delete rows in `kb_documents` in the Supabase Table Editor.
  Fill `source` (any unique label), `title` and `content` (Markdown).
  Set `active = false` to hide a document without deleting it.
- The running site re-chunks new and changed rows on its own within
  `retrieval.sync_interval_s` (60 s). No pull, restart or redeploy.
- One topic per document; short paragraphs separated by blank lines (chunks
  are cut on blank lines). Synthetic content only. Nothing here overrides
  `config/policy.yaml`.
- Bulk upload a folder of `.md`/`.txt` files: `python run.py ingest --import DIR`.
- Retrieval is Postgres full-text search by default. For hybrid vector
  search: `ollama pull nomic-embed-text`, set
  `retrieval.embedding_provider: ollama` in `config/app.yaml`, and set
  `chunked_at = null` on every `kb_documents` row so they are re-embedded.
- Tuning knobs (`top_k`, chunk size, overlap) are in `config/app.yaml`.

## Admin page (`/admin`)

- **Interactions:** every conversation, live from `interaction_events`.
  Pick one and press *Walk through* (or use the arrow keys) to step through
  it: input screen, each model and tool call with its arguments and result,
  the rules that fired, the policy stage it belongs to, and the final reply.
  Tickets and handoffs it created are shown from the database. Tick
  *Follow newest* and chat on the main site to watch a conversation live.
- **Files:** edit `config/policy.yaml`, `guardrails.yaml`, `app.yaml` and
  prompts. A save is checked first (YAML, keys the code reads, regexes, the
  system prompt renders), then goes live for new conversations. Every save
  is kept (*History*), *Revert to repo* undoes it, and *Download* gives you
  the file to commit. *New prompt* starts e.g. `prompts/agent/system_v2.md`;
  point `agent.system_prompt` in `app.yaml` at it to switch.
- **Knowledge base:** create, edit, hide or delete documents; they are
  searchable by the agent as soon as you save.

Browser edits live in Supabase (`config_files`) and override the repo files
until reverted; the repo files remain the reviewed defaults the tests use.

## Working on the LLM / prompts

- Agent system prompt: `config/prompts/agent/`. Classifier, judge and
  data-generation prompts go in their own folders under `config/prompts/`.
  Prompts and policy live in `config/`, never in code.
- Record every prompt change in [docs/prompt_changelog.md](docs/prompt_changelog.md),
  including ones made in `/admin` (add a note when you save; the history keeps it too).
- The classifier is a keyword stub behind `classify(text) -> {code, confidence, top2}`
  in `src/models/classifier.py`; swap in the real model without changing the signature.
- Every model call is logged to `interaction_events` with `provider:model`,
  tokens, latency and cost, so runs can be compared by model.

## Repository layout

```
config/            policy.yaml (policy store), app.yaml, guardrails.yaml, prompts/
src/agent/         orchestrator loop, deterministic rules layer
src/tools/         policy_lookup, search_tickets, create_ticket, get_status, handoff, kb_search
src/models/        LLM backbone (Ollama/OpenRouter), classify()
src/guardrails/    input screen, masking/redaction, output check
src/rag/           knowledge-base sync (chunking) and embeddings
src/web/           FastAPI server, static chat page, /admin page and API
src/data_gen/      synthetic data generation (W2)
src/eval/          evaluation harness (W5/W6)
supabase/          SQL migrations and seed data
data/test/         locked test sets (read only by the eval harness)
tests/             pytest suite (no keys or network needed)
results/           run outputs
docs/              interfaces, architecture, decisions, prompt changelog, deployment
```

## Commands

| Command | Make | What it does |
|---|---|---|
| `python run.py serve` | `make serve` | Start the chat site |
| `python run.py ingest` | `make ingest` | Re-chunk changed Supabase KB documents now (the server also does this every 60 s) |
| `python run.py test` | `make test` | Run the tests (in-memory store, scripted model) |
| `docker compose up -d --build` | | Build and start the site in Docker |

## Team docs

- [docs/interfaces.md](docs/interfaces.md): the shared contracts. Changing one needs the affected owner's review.
- [docs/architecture.md](docs/architecture.md): how a turn flows through the system.
- [docs/decisions.md](docs/decisions.md): what we chose and why.
- [docs/deployment.md](docs/deployment.md): running behind Cloudflare Tunnel.

Keys only in `.env` (git-ignored). Never commit real personal data.
