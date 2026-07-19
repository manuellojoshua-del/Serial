# CineDrive v14 Enterprise Smart Pipeline Scheduler

Versi v14 menambahkan pipeline khusus serial pada Global Queue CineDrive.

## Cara kerja serial

- Episode diklaim menurut urutan E01, E02, E03, dan seterusnya.
- Setelah E01 sudah diklaim, Railway lain yang kosong dapat mulai mengunduh dan meng-encode E02.
- Hasil encode E02 masuk status `READY` dan belum diunggah ke Telegram.
- E02 baru diunggah setelah E01 berstatus `SUCCESS`.
- E03 dapat dipersiapkan oleh worker lain dengan aturan yang sama.
- Urutan posting Telegram tetap benar, tetapi waktu download/encode antar-episode dapat tumpang tindih.

## Film

Smart Pipeline hanya berlaku untuk episode serial (`episode_number >= 1`). Film tetap memakai scheduler global paralel dan tidak menunggu film lain.

## Status pipeline

Status tambahan:

- `PREPARING` / `PROCESSING`: sedang download atau encode.
- `READY`: encode selesai dan menunggu episode sebelumnya berhasil.
- `UPLOADING`: giliran upload sudah tersedia.
- `SUCCESS`: episode telah diposting dan katalog diperbarui.

Endpoint pemeriksaan:

```text
/v14-status
/scheduler-status
/scheduler-dashboard-data?key=SECRET_KEY
```

## Variabel Railway

Pasang pada semua worker:

```env
SCHEDULER_ENABLED=1
SCHEDULER_POLL_SECONDS=5
SCHEDULER_MAX_JOBS_PER_WORKER=1
ENTERPRISE_CLUSTER_ENABLED=1
SMART_PIPELINE_SCHEDULER=1
SMART_PIPELINE_POLL_SECONDS=5
SMART_PIPELINE_WAIT_TIMEOUT_SECONDS=86400
GLOBAL_SYNC_ENABLED=1
GLOBAL_SYNC_BOOTSTRAP_LOCAL=0
GLOBAL_DATABASE_PUBLISH_LOCAL=0
```

Variabel Supabase, namespace, channel, dan `CATALOG_BOT_TOKEN` harus sama. `CLUSTER_WORKER_ID` dan `BOT_TOKEN` worker harus berbeda pada setiap Railway.

## Aset global

Agar episode bisa dipindahkan ke worker lain, video, subtitle, dan logo harus dapat diunduh semua Railway. Gunakan Google Drive publik atau URL publik. Upload file langsung dari HP tetap menjadi job lokal.

## Deploy

1. Upload seluruh isi ZIP ke repository.
2. Jalankan `supabase_setup.sql` bila belum pernah dijalankan.
3. Pasang variabel di atas pada semua Railway.
4. Redeploy semua service.
5. Buka `/v14-status` dan pastikan `smart_pipeline_scheduler` bernilai `true`.
