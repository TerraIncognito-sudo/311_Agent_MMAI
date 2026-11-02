# Decisions

Every choice of model, prompt, data or architecture: what we chose, the
alternatives, and why. The report draws on this file directly.

## D-001 Supabase for tickets, handoffs, event log and knowledge base (2026-11-02)

* **Chose:** Supabase (hosted Postgres + pgvector), accessed from the server with the service key over its REST API.
* **Alternatives:** `data/tickets.db` SQLite as in the original plan; a local Postgres.
* **Why:** collaborators can inspect and edit tickets and knowledge-base documents in the Supabase dashboard without running the server; pgvector gives vector search in the same database. An in-memory store with the same methods keeps tests and CI key-free.
* **Affects:** W2 seeds tickets into Supabase (`supabase/seed.sql` is a 5-row placeholder) instead of SQLite; W4 tools read and write through `src/tools/store.py` only.

## D-002 Policy store stays in `config/policy.yaml` (2026-11-02, superseded by D-012)

* **Chose:** the service catalogue and rule IDs stay in YAML in the repo, read by `policy_lookup`.
* **Alternatives:** a `policy` table in Supabase next to the knowledge base.
* **Why:** the policy is versioned, reviewed through pull requests, and every test cites it; a dashboard edit would bypass review. The knowledge base is for facts the agent may quote (C5), not routing rules.

## D-003 Web front end instead of Streamlit/Gradio for the demo (2026-11-02)

* **Chose:** FastAPI serving a static HTML/JS chat page (`src/web/`).
* **Alternatives:** Streamlit or Gradio, as listed in W5.
* **Why:** the site is public through a Cloudflare tunnel and should look like a plain chat app; a static page also lets the OpenRouter key stay in page memory only. The five scripted demo cases can still run through the same `/api/chat`.

## D-004 Swappable LLM backbone: Ollama or OpenRouter (2026-11-02)

* **Chose:** one OpenAI-compatible `chat()` for both providers; the user picks provider and model in the page.
* **Why:** lets the team compare small local models against hosted ones without code changes, and the event log records `provider:model` for every call so W6 ablations can be filtered by model.
* **Key handling:** OpenRouter keys are never stored server-side or in browser storage; the page sends the key per request and it is discarded after the call (test: `tests/test_api.py`).

## D-005 Hybrid retrieval with full-text fallback (2026-11-02)

* **Chose:** Postgres full-text search always, plus optional pgvector search (Ollama `nomic-embed-text`, 768 dims) fused by RRF.
* **Why:** works with zero setup (no embedding model needed) and improves when embeddings are enabled. The embedding size is fixed in the migration; changing models means changing `vector(768)` and re-ingesting.

## D-006 kb\_search added as a tool (proposed, 2026-11-02)

* **Chose:** a seventh tool, `kb_search`, alongside the policy's six actions.
* **Why:** C5 allows facts about City services only when a tool returned them; retrieval needs to be a tool for that to hold. Needs B's and D's sign-off at the Nov 5 interface session (A9 lists permitted actions).

## D-007 Docker on the team server (2026-11-02)

* **Chose:** run the site as a Docker container (`Dockerfile`, `docker-compose.yml`), built on the server from the repo with `docker compose up -d --build`.
* **Alternatives:** a native Python process started by a Windows scheduled task (used briefly on 2026-11-02, then removed).
* **Why:** matches how the other services on that server run, one command to update, and the same image runs on any teammate's machine. Ollama stays on the host and is reached via `host.docker.internal`. (Config was mounted from `config/` until D-012 moved it to Supabase.)

## D-008 Supabase security hardening (2026-11-02)

* **Chose:** `supabase/migrations/0002_security_hardening.sql`: pgvector moved to the `extensions` schema, `kb_documents_stale` made `security_invoker`, fixed `search_path` on functions, `match_kb_chunks` executable by `service_role` only.
* **Why:** fixes every Supabase security-advisor finding except "RLS enabled, no policy", which is intended: only the server's secret key and the dashboard may access the tables.

## D-009 Knowledge base lives only in Supabase, synced live (2026-11-02)

* **Chose:** `kb_documents` in Supabase is the single source of knowledge-base content; the `data/kb/` files were removed (their content was already in Supabase). The server re-chunks new and edited rows every `retrieval.sync_interval_s` seconds in a background thread.
* **Alternatives:** Markdown files in the repo loaded by `python run.py ingest` (the first version); chunking inside Postgres with a trigger.
* **Why:** the RAG team can change what the agent knows from the dashboard without touching the repo or the server, and the deployed site only changes for structural changes. Chunking stays in Python so it can also call the embedding model, which a Postgres trigger can't.

## D-010 Admin page; config edits stored in Supabase (2026-11-02, config part superseded by D-012)

* **Chose:** an `/admin` page with a live, step-by-step walkthrough of each conversation from `interaction_events`, and browser editing of `config/` files and the knowledge base. A saved config file goes to Supabase `config_files` and overrides the repo file until reverted; every save is kept in `config_revisions`.
* **Alternatives:** writing edits to the server's checkout (a dirty checkout breaks `git pull`, and edits would vanish on the next clone); editing only through pull requests (D-002).
* **Why:** teammates tuning prompts, policy and guardrails see the effect on the live agent at once, without a redeploy. This amends D-002: the repo files stay the reviewed defaults and the tests run against them; a browser edit worth keeping is downloaded from `/admin` and committed. Saves that would break the agent (bad YAML, a missing key the code reads, a bad regex, a prompt that won't render) are refused.
* **Risk:** no login yet, by request, for the prototype. See docs/deployment.md section 3.

## D-011 D2 card pattern skips ticket numbers (2026-11-02)

* **Chose:** `payment_card` in `config/guardrails.yaml` no longer matches `311-YYYY-NNNNNN`.
* **Why:** ticket numbers have 13 digits, so the card pattern redacted them: in the logs, and in a resident's own status request before the model saw it, which broke A5 status lookups. Found by the admin walkthrough test (`tests/test_guardrails.py::test_d2_keeps_ticket_numbers`).

## D-012 All configuration lives only in Supabase (2026-11-02)

* **Chose:** `app.yaml`, `policy.yaml`, `guardrails.yaml`, `flow.yaml` and the prompts are rows in Supabase `config_files` and nowhere else; `config/` was removed from the repo. The site loads them from Supabase only. The tests use a fixture (`tests/fixtures/config/`) the site never reads; `python run.py config pull|push` copies between the two.
* **Alternatives:** repo files as defaults with Supabase overrides (D-010, the first version of `/admin`), which left two copies that could disagree.
* **Why:** one copy can't go stale, and what the team sees in `/admin` is exactly what the agent runs. Supersedes D-002 and the work plan's "prompts and policy live in config/" convention; review now happens through the save notes and `config_revisions` instead of pull requests.
* **Risk:** the site can't start a conversation without Supabase (it answers 503); once loaded, the config stays in memory through outages.

## D-013 The policy's decision logic is an editable tree (2026-11-02)

* **Chose:** `flow.yaml` holds the decision logic (input screen outcomes, gates before tools, guidance after tools, failure handling, reply checks) as trees of yes/no checks and actions that the agent runs at fixed hooks; `src/agent/flow.py` provides the checks and actions. Every node visited is logged, and `/admin` shows a conversation's path through the tree.
* **Alternatives:** keep the rules in code (`src/agent/rules.py`, the first version) and only document the flow.
* **Why:** policy owners can change the logic and see exactly why the agent did what it did, decision by decision, without code. Saves that would drop a [hard] rule (E1, A2, A8, D5, E6, E9, M3) are refused; D2 redaction and D6 masking stay in code.
* **Behaviour added by the default tree:** E3 (ask one clarifying question when the classifier is unsure; the repeat-handoff step is switched off until the real classifier and threshold land), C6 duplicate guidance, M2 ask-one-field guidance, and M3 confirmation guidance.
