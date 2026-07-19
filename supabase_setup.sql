-- CineDrive v11.0.3 Cluster Final Conflict Fix
-- Jalankan seluruh script ini di Supabase SQL Editor, lalu Redeploy Railway.
-- Struktur ini kompatibel dengan tabel lama (bucket/item_key/value)
-- dan struktur v11 (record_type/record_key/data).

create table if not exists public.cinedrive_cluster (
  namespace text not null default 'cinemaxx1-production',
  bucket text not null default 'documents',
  item_key text not null default '',
  value jsonb not null default '{}'::jsonb,
  worker_id text,
  updated_at timestamptz not null default now(),
  record_type text not null default 'document',
  record_key text not null default '',
  data jsonb not null default '{}'::jsonb,
  updated_by text
);

alter table public.cinedrive_cluster add column if not exists namespace text;
alter table public.cinedrive_cluster add column if not exists bucket text;
alter table public.cinedrive_cluster add column if not exists item_key text;
alter table public.cinedrive_cluster add column if not exists value jsonb;
alter table public.cinedrive_cluster add column if not exists worker_id text;
alter table public.cinedrive_cluster add column if not exists updated_at timestamptz;
alter table public.cinedrive_cluster add column if not exists record_type text;
alter table public.cinedrive_cluster add column if not exists record_key text;
alter table public.cinedrive_cluster add column if not exists data jsonb;
alter table public.cinedrive_cluster add column if not exists updated_by text;

-- Lengkapi baris lama sebelum NOT NULL/default diterapkan.
update public.cinedrive_cluster
set
  namespace = coalesce(nullif(namespace, ''), 'cinemaxx1-production'),
  bucket = coalesce(nullif(bucket, ''), case when record_type = 'worker' then 'workers' else 'documents' end),
  item_key = coalesce(nullif(item_key, ''), nullif(record_key, ''), nullif(worker_id, ''), 'legacy-' || substr(md5(random()::text), 1, 12)),
  value = coalesce(value, data, '{}'::jsonb),
  updated_at = coalesce(updated_at, now()),
  record_type = coalesce(nullif(record_type, ''), case when bucket = 'workers' then 'worker' else 'document' end),
  record_key = coalesce(nullif(record_key, ''), nullif(item_key, ''), nullif(worker_id, ''), 'legacy-' || substr(md5(random()::text), 1, 12)),
  data = coalesce(data, value, '{}'::jsonb),
  updated_by = coalesce(updated_by, worker_id);

alter table public.cinedrive_cluster alter column namespace set default 'cinemaxx1-production';
alter table public.cinedrive_cluster alter column bucket set default 'documents';
alter table public.cinedrive_cluster alter column item_key set default '';
alter table public.cinedrive_cluster alter column value set default '{}'::jsonb;
alter table public.cinedrive_cluster alter column updated_at set default now();
alter table public.cinedrive_cluster alter column record_type set default 'document';
alter table public.cinedrive_cluster alter column record_key set default '';
alter table public.cinedrive_cluster alter column data set default '{}'::jsonb;

alter table public.cinedrive_cluster alter column namespace set not null;
alter table public.cinedrive_cluster alter column bucket set not null;
alter table public.cinedrive_cluster alter column item_key set not null;
alter table public.cinedrive_cluster alter column value set not null;
alter table public.cinedrive_cluster alter column updated_at set not null;
alter table public.cinedrive_cluster alter column record_type set not null;
alter table public.cinedrive_cluster alter column record_key set not null;
alter table public.cinedrive_cluster alter column data set not null;

create unique index if not exists cinedrive_cluster_bucket_identity_idx
  on public.cinedrive_cluster(namespace, bucket, item_key);

create index if not exists cinedrive_cluster_bucket_updated_idx
  on public.cinedrive_cluster(namespace, bucket, updated_at desc);

create unique index if not exists cinedrive_cluster_record_identity_idx
  on public.cinedrive_cluster(namespace, record_type, record_key);

alter table public.cinedrive_cluster enable row level security;
-- Aplikasi wajib menggunakan SUPABASE_SERVICE_ROLE_KEY, bukan anon key.

notify pgrst, 'reload schema';

-- CineDrive v11.5 uses record_type='lock' and document keys prefixed enterprise-job:.
-- Existing cinedrive_cluster schema and identity index are sufficient.
create index if not exists cinedrive_cluster_bucket_item_idx
on public.cinedrive_cluster(namespace, bucket, item_key);
