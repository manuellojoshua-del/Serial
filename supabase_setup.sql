-- CineDrive v11.0.1 Cluster Fix
-- Jalankan seluruh isi file ini sekali di Supabase SQL Editor.
-- Script ini menggunakan satu tabel: public.cinedrive_cluster.

create table if not exists public.cinedrive_cluster (
  namespace text not null default 'cinemaxx1-production',
  record_type text not null default 'document',
  record_key text not null default '',
  data jsonb not null default '{}'::jsonb,
  updated_by text,
  updated_at timestamptz not null default now()
);

-- Aman untuk tabel cinedrive_cluster yang sudah ada.
alter table public.cinedrive_cluster add column if not exists namespace text not null default 'cinemaxx1-production';
alter table public.cinedrive_cluster add column if not exists record_type text not null default 'document';
alter table public.cinedrive_cluster add column if not exists record_key text not null default '';
alter table public.cinedrive_cluster add column if not exists data jsonb not null default '{}'::jsonb;
alter table public.cinedrive_cluster add column if not exists updated_by text;
alter table public.cinedrive_cluster add column if not exists updated_at timestamptz not null default now();

create unique index if not exists cinedrive_cluster_identity_idx
  on public.cinedrive_cluster(namespace, record_type, record_key);

create index if not exists cinedrive_cluster_updated_idx
  on public.cinedrive_cluster(namespace, record_type, updated_at desc);

alter table public.cinedrive_cluster enable row level security;
-- CineDrive harus memakai SUPABASE_SERVICE_ROLE_KEY. Jangan simpan key di GitHub.
