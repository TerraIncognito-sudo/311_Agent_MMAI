-- 311 Intake Agent: initial schema.
-- Run in the Supabase SQL editor (or `supabase db push`). Safe to re-run.
-- All data is synthetic. RLS is on and no policies are granted to anon/
-- authenticated: only the server (service key) and the dashboard can read or
-- write. Collaborators edit tables and the knowledge base in the dashboard.

create extension if not exists vector;

-- ---------------------------------------------------------------- tickets
create sequence if not exists ticket_seq;

create table if not exists tickets (
  ticket_id     text primary key
                default '311-' || to_char(now(), 'YYYY') || '-' || lpad(nextval('ticket_seq')::text, 6, '0'),
  code          text not null,
  unit          text,
  status        text not null default 'open' check (status in ('open', 'in_progress', 'closed')),
  location      text,
  location_norm text generated always as (lower(regexp_replace(coalesce(location, ''), '\s+', ' ', 'g'))) stored,
  postal_code   text,
  lat           double precision,     -- optional: for radius duplicate windows (W2 seeds)
  lng           double precision,
  fields        jsonb not null default '{}'::jsonb,
  contact       jsonb,                -- D1: only if the resident wants updates; never returned by search (D3)
  opened_at     timestamptz not null default now()
);
create index if not exists tickets_lookup on tickets (code, status, opened_at desc);
create index if not exists tickets_location on tickets (location_norm);

-- ---------------------------------------------------------------- handoffs
create sequence if not exists handoff_seq;

create table if not exists handoffs (
  ref_id              text primary key
                      default 'HO-' || to_char(now(), 'YYYY') || '-' || lpad(nextval('handoff_seq')::text, 6, '0'),
  interaction_id      text,
  rule_id             text not null,
  code                text,
  confidence          real,
  fields              jsonb not null default '{}'::jsonb,   -- masked (D6)
  transcript          jsonb not null default '[]'::jsonb,   -- masked (D6)
  possible_duplicates jsonb not null default '[]'::jsonb,
  reason              text,
  status              text not null default 'pending',
  created_at          timestamptz not null default now()
);

-- ---------------------------------------------------------------- event log
-- Contract (docs/interfaces.md): event_id, interaction_id, step, model,
-- tokens_in, tokens_out, cost_usd, latency_ms, rule_id, outcome, text (masked)
create table if not exists interaction_events (
  event_id       uuid primary key default gen_random_uuid(),
  interaction_id text not null,
  step           text not null,
  model          text,
  tokens_in      integer default 0,
  tokens_out     integer default 0,
  cost_usd       numeric(12, 6),
  latency_ms     integer,
  rule_id        text,
  outcome        text,
  text           text,
  created_at     timestamptz not null default now()
);
create index if not exists events_interaction on interaction_events (interaction_id, created_at);

-- ---------------------------------------------------------------- knowledge base
-- kb_documents: one row per source document. Edit content here or in data/kb/,
-- then run `python run.py ingest` to re-chunk changed documents.
create table if not exists kb_documents (
  id           uuid primary key default gen_random_uuid(),
  source       text not null unique,      -- e.g. data/kb/waste_collection.md, or any label
  title        text not null,
  content      text not null,
  content_hash text,
  tags         text[] default '{}',
  active       boolean not null default true,
  updated_at   timestamptz not null default now(),
  chunked_at   timestamptz
);

create or replace function kb_touch() returns trigger language plpgsql as $$
begin
  if new.content is distinct from old.content or new.title is distinct from old.title then
    new.updated_at := now();
  end if;
  return new;
end $$;
drop trigger if exists kb_documents_touch on kb_documents;
create trigger kb_documents_touch before update on kb_documents
  for each row execute function kb_touch();

-- Documents whose chunks are missing or out of date.
create or replace view kb_documents_stale as
  select * from kb_documents
  where active and (chunked_at is null or updated_at > chunked_at);

-- Embedding size must match config/app.yaml retrieval.embedding_dimensions.
create table if not exists kb_chunks (
  id          bigint generated always as identity primary key,
  document_id uuid not null references kb_documents (id) on delete cascade,
  chunk_index integer not null,
  content     text not null,
  fts         tsvector generated always as (to_tsvector('english', content)) stored,
  embedding   vector(768)
);
create index if not exists kb_chunks_fts on kb_chunks using gin (fts);
create index if not exists kb_chunks_embedding on kb_chunks using hnsw (embedding vector_cosine_ops);

-- Hybrid retrieval: full-text always; vector too when an embedding is passed.
-- Results are fused with reciprocal rank fusion (k = 60).
create or replace function match_kb_chunks(
  query_text text,
  query_embedding vector(768) default null,
  match_count int default 4
) returns table (id bigint, document_id uuid, title text, source text, content text, score float)
language sql stable as $$
  -- OR together the query's stemmed words: resident questions are long and
  -- an AND query would rarely match. 'simple' keeps the stems as they are.
  with q as (
    select to_tsquery('simple', nullif(array_to_string(array(
             select quote_literal(lexeme)
             from unnest(tsvector_to_array(to_tsvector('english', query_text))) as lexeme
           ), ' | '), '')) as tsq
  ),
  fts as (
    select c.id, row_number() over (order by ts_rank_cd(c.fts, q.tsq) desc) as rnk
    from kb_chunks c
    join kb_documents d on d.id = c.document_id and d.active
    cross join q
    where q.tsq is not null and c.fts @@ q.tsq
    order by rnk limit match_count * 4
  ),
  vec as (
    select c.id, row_number() over (order by c.embedding <=> query_embedding) as rnk
    from kb_chunks c join kb_documents d on d.id = c.document_id and d.active
    where query_embedding is not null and c.embedding is not null
    order by c.embedding <=> query_embedding limit match_count * 4
  ),
  fused as (
    select coalesce(f.id, v.id) as id,
           coalesce(1.0 / (60 + f.rnk), 0) + coalesce(1.0 / (60 + v.rnk), 0) as score
    from fts f full outer join vec v on f.id = v.id
  )
  select c.id, c.document_id, d.title, d.source, c.content, fused.score
  from fused join kb_chunks c on c.id = fused.id join kb_documents d on d.id = c.document_id
  order by fused.score desc
  limit match_count;
$$;

-- ---------------------------------------------------------------- security
alter table tickets            enable row level security;
alter table handoffs           enable row level security;
alter table interaction_events enable row level security;
alter table kb_documents       enable row level security;
alter table kb_chunks          enable row level security;
