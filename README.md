# CineDrive v12.3 — Supabase Canonical Data Fix

Versi ini memperbaiki menu Serial dan Data agar memakai Supabase sebagai sumber utama.

## Perubahan
- Menu Serial selalu membaca dokumen kanonis `series` di Supabase.
- Snapshot lama dari volume Railway tidak digabung ulang setelah migrasi dimatikan.
- Menu Data menampilkan statistik global Supabase, fingerprint, dan cache lokal secara terpisah.
- Upload/restore JSON sekarang menulis ke database global.
- Tombol **Bersihkan serial duplikat** menormalkan data kanonis Supabase.
- Cache `/data/*.json` hanya digunakan untuk fallback dan backup.

## Variabel wajib di semua Railway
```env
GLOBAL_SYNC_ENABLED=1
GLOBAL_SYNC_BOOTSTRAP_LOCAL=0
GLOBAL_DATABASE_PUBLISH_LOCAL=0
GLOBAL_DATABASE_REFRESH_SECONDS=5
```

Gunakan `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `CLUSTER_NAMESPACE`, dan `CHANNEL_ID` yang sama di semua Railway. `CLUSTER_WORKER_ID` harus berbeda.

Setelah deploy, buka menu **Data** dan tekan **Bersihkan serial duplikat** satu kali. Kemudian refresh kedua panel dan bandingkan `/global-sync-status`; fingerprint harus sama.
