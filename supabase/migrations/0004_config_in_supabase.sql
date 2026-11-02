-- Configuration lives only in Supabase (2026-11-02, docs/decisions.md D-012).
-- config_files is now the single source of every config file the agent reads
-- (app.yaml, policy.yaml, guardrails.yaml, flow.yaml, prompts/...); the repo
-- has no copy. Safe to re-run. Seed a new project with:
--   python run.py config push tests/fixtures/config

comment on table config_files is
  'The agent''s configuration: one row per file (app.yaml, policy.yaml, guardrails.yaml, flow.yaml, prompts/...). The only copy; edit in /admin.';
comment on table config_revisions is
  'Every save of a config file. content null = the file was deleted.';
comment on column config_revisions.content is 'null = the file was deleted';
