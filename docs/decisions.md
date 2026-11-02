# Decisions

Every choice of model, prompt, data or architecture: what we chose, the
alternatives, and why. The report draws on this file directly.

## D-001 Supabase for tickets, handoffs, event log and knowledge base (2026-11-02)
- **Chose:** Supabase (hosted Postgres + pgvector), accessed from the server with the service key over its REST API.
- **Alternatives:** `data/tickets.db` SQLite as in the original plan; a local Postgres.
- **Why:** collaborators can inspect and edit tickets and knowledge-base documents in the Supabase dashboard without running the server; pgvector gives vector search in the same database. An in-memory store with the same methods keeps tests and CI key-free.
- **Affects:** W2 seeds tickets into Supabase (`supabase/seed.sql` is a 5-row placeholder) instead of SQLite; W4 tools read and write through `src/tools/store.py` only.

## D-002 Policy store stays in `config/policy.yaml` (2026-11-02)
- **Chose:** the service catalogue and rule IDs stay in YAML in the repo, read by `policy_lookup`.
- **Alternatives:** a `policy` table in Supabase next to the knowledge base.
- **Why:** the policy is versioned, reviewed through pull requests, and every test cites it; a dashboard edit would bypass review. The knowledge base is for facts the agent may quote (C5), not routing rules.

## D-003 Web front end instead of Streamlit/Gradio for the demo (2026-11-02)
- **Chose:** FastAPI serving a static HTML/JS chat page (`src/web/`).
- **Alternatives:** Streamlit or Gradio, as listed in W5.
- **Why:** the site is public through a Cloudflare tunnel and should look like a plain chat app; a static page also lets the OpenRouter key stay in page memory only. The five scripted demo cases can still run through the same `/api/chat`.

## D-004 Swappable LLM backbone: Ollama or OpenRouter (2026-11-02)
- **Chose:** one OpenAI-compatible `chat()` for both providers; the user picks provider and model in the page.
- **Why:** lets the team compare small local models against hosted ones without code changes, and the event log records `provider:model` for every call so W6 ablations can be filtered by model.
- **Key handling:** OpenRouter keys are never stored server-side or in browser storage; the page sends the key per request and it is discarded after the call (test: `tests/test_api.py`).

## D-005 Hybrid retrieval with full-text fallback (2026-11-02)
- **Chose:** Postgres full-text search always, plus optional pgvector search (Ollama `nomic-embed-text`, 768 dims) fused by RRF.
- **Why:** works with zero setup (no embedding model needed) and improves when embeddings are enabled. The embedding size is fixed in the migration; changing models means changing `vector(768)` and re-ingesting.

## D-006 kb_search added as a tool (proposed, 2026-11-02)
- **Chose:** a seventh tool, `kb_search`, alongside the policy's six actions.
- **Why:** C5 allows facts about City services only when a tool returned them; retrieval needs to be a tool for that to hold. Needs B's and D's sign-off at the Nov 5 interface session (A9 lists permitted actions).

## D-007 Docker on the team server (2026-11-02)
- **Chose:** run the site as a Docker container (`Dockerfile`, `docker-compose.yml`), built on the server from the repo with `docker compose up -d --build`.
- **Alternatives:** a native Python process started by a Windows scheduled task (used briefly on 2026-11-02, then removed).
- **Why:** matches how the other services on that server run, one command to update, and the same image runs on any teammate's machine. Ollama stays on the host and is reached via `host.docker.internal`; `config/` and `data/kb/` are mounted so policy and knowledge-base edits don't need a rebuild.

## D-008 Supabase security hardening (2026-11-02)
- **Chose:** `supabase/migrations/0002_security_hardening.sql`: pgvector moved to the `extensions` schema, `kb_documents_stale` made `security_invoker`, fixed `search_path` on functions, `match_kb_chunks` executable by `service_role` only.
- **Why:** fixes every Supabase security-advisor finding except "RLS enabled, no policy", which is intended: only the server's secret key and the dashboard may access the tables.
