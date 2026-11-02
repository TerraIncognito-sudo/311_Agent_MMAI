-- Fixes from the Supabase security advisor (2026-11-02). Safe to re-run.
--
-- Note: "RLS enabled, no policy" on every table is intentional. Only the
-- server (secret/service key, which bypasses RLS) and the dashboard may read
-- or write; the public anon/publishable key gets nothing.

-- Move pgvector out of the exposed public schema.
create schema if not exists extensions;
do $$ begin
  if (select extnamespace::regnamespace::text from pg_extension where extname = 'vector') = 'public' then
    alter extension vector set schema extensions;
  end if;
end $$;

-- Views run with the caller's permissions, so RLS applies (lint 0010).
alter view public.kb_documents_stale set (security_invoker = true);

-- Pin search_path on functions (lint 0011). `extensions` holds the vector type.
alter function public.kb_touch() set search_path = public, pg_catalog;
alter function public.match_kb_chunks(text, extensions.vector, int) set search_path = public, extensions, pg_catalog;

-- Retrieval is server-only: don't expose it as an RPC to anon/authenticated.
revoke execute on function public.match_kb_chunks(text, extensions.vector, int) from public, anon, authenticated;
do $$ begin
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant execute on function public.match_kb_chunks(text, extensions.vector, int) to service_role;
  end if;
end $$;
