# CineDrive v11.0.2 Cluster Fix — Ready Deploy

Dibangun dari CineDrive v10.6.2.2. Semua fitur TMDB, serial, subtitle, Smart Watermark Safe Area, H.265 Turbo, dan Telegram Local Bot API dipertahankan.

## Fitur cluster

- Sinkronisasi `series`, `topics`, dan `scan_results` melalui Supabase.
- Cache lokal `/data` tetap digunakan sebagai fallback saat Supabase tidak dapat dijangkau.
- Metadata episode dari beberapa Railway digabung agar episode yang sudah tersimpan tidak tertimpa.
- Heartbeat worker setiap 30 detik.
- Endpoint `/cluster-status`, `/cluster-workers`, dan `/cluster-sync`.
- `/health` menampilkan versi dan status aktivasi cluster.

> File video, subtitle upload, logo, dan proses FFmpeg tetap dikerjakan oleh Railway yang menerima permintaan. v11.0.0 menyinkronkan data serial, bukan memindahkan proses encode aktif antarserver.

## Instalasi Supabase

1. Buka Supabase → SQL Editor.
2. Jalankan `supabase_setup.sql`.
3. Ambil Project URL dan `service_role` key.
4. Jangan menaruh service role key di GitHub.

## Variables Railway

Tambahkan ke setiap service:

```env
SUPABASE_URL=https://PROJECT_ID.supabase.co
SUPABASE_SERVICE_ROLE_KEY=SERVICE_ROLE_KEY
CLUSTER_NAMESPACE=cinemaxx1-production
CLUSTER_WORKER_ID=railway-1
```

Pada Railway kedua, gunakan `CLUSTER_WORKER_ID=railway-2`. Namespace, URL, dan key harus sama. Variabel CineDrive lama tetap dipakai.

## Deploy

1. Upload seluruh isi ZIP ke root GitHub.
2. Pastikan Railway Volume terpasang pada `/data`.
3. Redeploy.
4. Buka `https://DOMAIN/health`.
5. Buka `https://DOMAIN/cluster-status`.

Saat konfigurasi benar, `enabled` bernilai `true` dan daftar worker muncul. Bila `enabled` bernilai `false`, periksa `SUPABASE_URL` dan `SUPABASE_SERVICE_ROLE_KEY`.


## v11.0.2 Cluster Fix

Versi ini memakai satu tabel Supabase bernama `cinedrive_cluster` untuk dokumen sinkronisasi dan heartbeat worker. Jalankan ulang `supabase_setup.sql`, lalu redeploy Railway. Endpoint `/cluster-status` tidak lagi mengakses tabel `cluster_workers`.

## Verifikasi heartbeat v11.0.2

Setelah deploy, buka `/cluster-heartbeat`. Respons benar harus berisi `heartbeat_ok: true`, `active_worker_count: 1`, dan worker saat ini di dalam `workers`. Deploy Logs juga menampilkan `[CLUSTER] heartbeat OK`.
