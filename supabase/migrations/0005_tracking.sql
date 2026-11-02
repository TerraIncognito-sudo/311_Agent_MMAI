-- Ticket and handoff tracking in /admin. Safe to re-run.
-- RLS on, no policies: server and dashboard only.

-- Which conversation created a ticket (handoffs already have interaction_id).
alter table tickets add column if not exists interaction_id text;
create index if not exists tickets_interaction on tickets (interaction_id);

-- Handoffs move through review; tickets keep their own status check.
create index if not exists handoffs_status on handoffs (status, created_at desc);

-- Every status change or note made by staff in /admin. The agent never
-- writes here (A7: it can't edit, close or reprioritize anything).
create table if not exists record_updates (
  id         bigint generated always as identity primary key,
  kind       text not null check (kind in ('ticket', 'handoff')),
  ref        text not null,                  -- ticket_id or handoff ref_id
  old_status text,
  new_status text,
  note       text,
  author     text,
  created_at timestamptz not null default now()
);
create index if not exists record_updates_ref on record_updates (kind, ref, created_at);
alter table record_updates enable row level security;

comment on table record_updates is 'Status changes and notes on tickets and handoffs, made by staff in /admin.';
