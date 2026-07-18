-- CineDrive v11 Cluster — jalankan sekali di Supabase SQL Editor
create table if not exists public.cluster_documents (
  namespace text not null,
  document_key text not null,
  data jsonb not null default '{}'::jsonb,
  updated_by text,
  updated_at timestamptz not null default now(),
  primary key (namespace, document_key)
);

create table if not exists public.cluster_workers (
  namespace text not null,
  worker_id text not null,
  hostname text,
  version text,
  last_seen timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb,
  primary key (namespace, worker_id)
);

create index if not exists cluster_workers_last_seen_idx on public.cluster_workers(namespace,last_seen desc);

alter table public.cluster_documents enable row level security;
alter table public.cluster_workers enable row level security;
-- Aplikasi memakai service_role key, yang melewati RLS. Jangan simpan key tersebut di GitHub.
