-- CineDrive v11 Cluster — jalankan di Supabase SQL Editor.
create table if not exists public.cinedrive_cluster (
  namespace text not null,
  bucket text not null,
  item_key text not null,
  value jsonb not null default '{}'::jsonb,
  worker_id text,
  updated_at timestamptz not null default now(),
  primary key (namespace, bucket, item_key)
);

create index if not exists cinedrive_cluster_bucket_idx
  on public.cinedrive_cluster(namespace, bucket, updated_at desc);

alter table public.cinedrive_cluster enable row level security;

-- Service role key bypasses RLS. Jangan gunakan anon key pada Railway.
revoke all on public.cinedrive_cluster from anon, authenticated;
grant all on public.cinedrive_cluster to service_role;
