# CineDrive v11.5 Enterprise Cluster

Versi ini melanjutkan Global Database v11.4 dan menambahkan koordinasi lintas Railway.

## Fitur utama

- Supabase tetap menjadi sumber utama data serial, episode, topic, dan scan.
- Distributed lock mencegah episode/konten yang sama diproses bersamaan oleh dua Railway.
- Status pekerjaan dipublikasikan ke Supabase agar dapat dilihat dari semua worker.
- Setiap worker memakai `CLUSTER_WORKER_ID` berbeda dan dapat memakai bot Telegram berbeda.
- Endpoint `/enterprise-status` menampilkan worker, bot aktif, pekerjaan lokal, dan pekerjaan bersama.
- Heartbeat, Global Database, Smart Watermark, H.265 Turbo, TMDB, dan format katalog serial tetap dipertahankan.

## Variabel yang sama di semua Railway

```env
SUPABASE_URL=https://PROJECT.supabase.co
SUPABASE_SERVICE_ROLE_KEY=...
CLUSTER_NAMESPACE=cinemaxx1-production
CHANNEL_ID=-100xxxxxxxxxx
GLOBAL_SYNC_ENABLED=1
GLOBAL_SYNC_BOOTSTRAP_LOCAL=0
GLOBAL_DATABASE_PUBLISH_LOCAL=0
ENTERPRISE_CLUSTER_ENABLED=1
ENTERPRISE_LOCK_TTL_SECONDS=21600
```

## Variabel yang harus berbeda

Railway pertama:

```env
CLUSTER_WORKER_ID=railway-1
BOT_TOKEN=TOKEN_BOT_1
```

Railway kedua:

```env
CLUSTER_WORKER_ID=railway-2
BOT_TOKEN=TOKEN_BOT_2
```

## Pemeriksaan

Buka:

- `/global-sync-status`
- `/cluster-status`
- `/bot-status`
- `/enterprise-status`

`/enterprise-status` seharusnya menampilkan `version: 11.5.0` dan `enterprise_cluster_enabled: true`.

## Catatan pembagian beban

Pekerjaan diproses oleh Railway tempat pengguna menambahkannya. Distributed lock mencegah duplikasi lintas worker. Status pekerjaan dibagikan secara global, tetapi versi ini tidak memindahkan file upload atau direktori kerja secara otomatis dari satu Railway ke Railway lain.

## CineDrive v12 — Enterprise Scheduler

Versi v12 menambahkan pembagian pekerjaan otomatis antar worker Railway yang aktif.
Pekerjaan yang sumbernya sepenuhnya dari Google Drive dapat dijadwalkan ke worker dengan beban paling rendah. Pekerjaan yang memakai file upload lokal, seperti subtitle upload atau logo watermark upload, tetap diproses oleh Railway tempat file tersebut diunggah karena file lokal tidak tersedia di worker lain.

Variabel:

```env
SCHEDULER_ENABLED=1
SCHEDULER_POLL_SECONDS=5
SCHEDULER_MAX_JOBS_PER_WORKER=1
SCHEDULER_CLAIM_TTL_SECONDS=21600
```

Status scheduler:

```text
/scheduler-status
```

Untuk semua Railway, gunakan `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `CLUSTER_NAMESPACE`, dan `CHANNEL_ID` yang sama. Gunakan `CLUSTER_WORKER_ID` berbeda pada setiap Railway.
