# 311 Intake Agent: City of Gilead

A RAG-backed chatbot that takes non-emergency 311 service requests for the
fictional City of Gilead. It records and routes requests, it decides nothing
on the City's behalf. The rules it follows are in the operating policy
(`config/policy.yaml`); every guardrail and test cites a rule ID from it.

**All data is synthetic.** No real residents, addresses or City systems.

- Chat site: plain chat page, choose the model in the top-right corner.
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
4. Load the knowledge base: `python run.py ingest`
   (in Docker: `docker compose run --rm app python run.py ingest`).

Tables: `tickets`, `handoffs`, `interaction_events` (event log),
`kb_documents`, `kb_chunks`. Collaborators can browse and edit them in the
Supabase Table Editor.

## Working on the knowledge base (RAG)

- Add or edit Markdown files in `data/kb/` (see [data/kb/README.md](data/kb/README.md)),
  or edit rows in `kb_documents` in the Supabase dashboard.
- Run `python run.py ingest` (or `--skip-files` after dashboard-only edits).
  Only changed documents are re-chunked.
- Retrieval is Postgres full-text search by default. For hybrid vector
  search: `ollama pull nomic-embed-text`, set
  `retrieval.embedding_provider: ollama` in `config/app.yaml`, and re-ingest.
- Tuning knobs (`top_k`, chunk size, overlap) are in `config/app.yaml`.

## Working on the LLM / prompts

- Agent system prompt: `config/prompts/agent/`. Classifier, judge and
  data-generation prompts go in their own folders under `config/prompts/`.
  Prompts and policy live in `config/`, never in code.
- Record every prompt change in [docs/prompt_changelog.md](docs/prompt_changelog.md).
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
src/rag/           knowledge-base ingestion and embeddings
src/web/           FastAPI server + static chat page
src/data_gen/      synthetic data generation (W2)
src/eval/          evaluation harness (W5/W6)
supabase/          SQL migrations and seed data
data/kb/           knowledge-base source documents
data/test/         locked test sets (read only by the eval harness)
tests/             pytest suite (no keys or network needed)
results/           run outputs
docs/              interfaces, architecture, decisions, prompt changelog, deployment
```

## Commands

| Command | Make | What it does |
|---|---|---|
| `python run.py serve` | `make serve` | Start the chat site |
| `python run.py ingest` | `make ingest` | Load `data/kb/` into Supabase and re-chunk changes |
| `python run.py test` | `make test` | Run the tests (in-memory store, scripted model) |
| `docker compose up -d --build` | | Build and start the site in Docker |

## Team docs

- [docs/interfaces.md](docs/interfaces.md): the shared contracts. Changing one needs the affected owner's review.
- [docs/architecture.md](docs/architecture.md): how a turn flows through the system.
- [docs/decisions.md](docs/decisions.md): what we chose and why.
- [docs/deployment.md](docs/deployment.md): running behind Cloudflare Tunnel.

Keys only in `.env` (git-ignored). Never commit real personal data.
