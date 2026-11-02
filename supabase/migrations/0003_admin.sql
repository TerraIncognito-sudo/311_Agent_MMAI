-- /admin page: browser edits of config/ files, and interaction summaries for
-- the walkthrough. Safe to re-run. RLS on, no policies: server and dashboard only.

-- ---------------------------------------------------------------- config edits
-- A row here overrides the repo file at config/<path> until it is reverted
-- (src/config.py). Every save and revert is kept in config_revisions.
create table if not exists config_files (
  path       text primary key,            -- e.g. policy.yaml, prompts/agent/system_v1.md
  content    text not null,
  updated_at timestamptz not null default now(),
  updated_by text
);

create table if not exists config_revisions (
  id       bigint generated always as identity primary key,
  path     text not null,
  content  text,                          -- null = reverted to the repo version
  note     text,
  author   text,
  saved_at timestamptz not null default now()
);
create index if not exists config_revisions_path on config_revisions (path, saved_at desc);

alter table config_files     enable row level security;
alter table config_revisions enable row level security;

-- ---------------------------------------------------------------- walkthrough
-- One row per conversation, newest activity first in /admin.
create or replace view interaction_summaries with (security_invoker = true) as
  select
    interaction_id,
    min(created_at)                                                        as started_at,
    max(created_at)                                                        as last_at,
    count(*)                                                               as events,
    count(*) filter (where step = 'input_screen')                          as turns,
    string_agg(distinct model, ', ') filter (where model is not null)      as models,
    array_agg(distinct rule_id) filter (where rule_id is not null)         as rule_ids,
    count(*) filter (where step = 'tool:create_ticket' and outcome = 'ok') as tickets,
    count(*) filter (where step = 'tool:handoff' and outcome = 'ok')       as handoffs,
    coalesce(sum(tokens_in), 0)                                            as tokens_in,
    coalesce(sum(tokens_out), 0)                                           as tokens_out,
    sum(cost_usd)                                                          as cost_usd,
    (array_agg(text order by created_at) filter (where step = 'input_screen'))[1] as first_message
  from interaction_events
  group by interaction_id;
