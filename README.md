# CineDrive v16.1 Enterprise Automatic Failover

Versi ini mempertahankan **Smart Catalog v16** dan menambahkan failover otomatis lintas Railway.

## Fitur v16.1

- Heartbeat worker diperiksa dari Supabase.
- Worker dianggap offline setelah 90 detik tanpa heartbeat, lalu menunggu grace period 30 detik.
- Job portabel berbasis Google Drive yang masih `QUEUED`, `CLAIMED`, `DOWNLOADING`, `PROCESSING`, `READY`, atau `UPLOADING` dikembalikan ke antrean global.
- Railway lain yang online dan tidak memiliki tugas dapat mengklaim job tersebut.
- Lock scheduler dan lock media milik worker offline dilepas sebelum klaim ulang.
- Panel mencatat `failover_from`, `failover_at`, `failover_count`, dan tahap terakhir sebelum gagal.
- Job dengan subtitle/logo upload lokal tidak dapat dipindahkan karena berkas hanya ada di volume Railway asal; job tersebut ditandai `ERROR` dengan penjelasan.
- Endpoint status: `/v16.1-status` (endpoint lama `/v16-status` dan `/v15-status` tetap tersedia).

## Perilaku saat failover

Jika Railway mati ketika encode atau upload belum selesai, Railway lain memulai ulang job dari sumber Google Drive. File sementara hasil encode tidak dapat dilanjutkan karena storage antar Railway tidak dibagikan.

## Variabel Railway

```env
V161_FAILOVER_ENABLED=1
V161_WORKER_OFFLINE_SECONDS=90
V161_FAILOVER_GRACE_SECONDS=30
V161_FAILOVER_PROCESSING_JOBS=1
```

Gunakan `CLUSTER_WORKER_ID` berbeda pada setiap service, misalnya `railway-1` dan `railway-2`. Semua service harus memakai `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `CLUSTER_NAMESPACE`, dan source code yang sama.

## Deploy

1. Ganti isi repository dengan file versi ini.
2. Pastikan variabel Supabase dan Telegram tersedia di seluruh Railway.
3. Atur `CLUSTER_WORKER_ID` berbeda untuk masing-masing Railway.
4. Redeploy semua worker.
5. Buka `/v16.1-status` dan pastikan kedua worker berstatus online.
