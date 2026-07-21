# CineDrive v16.2.5 Worker Cleanup Fix

Versi ini mempertahankan Queue Recovery v16.2.4 dan memperbaiki pembersihan heartbeat Railway yang sudah offline.

## Perbaikan

- Variabel `V1622_*` sekarang benar-benar dibaca oleh aplikasi.
- Worker offline disembunyikan dari halaman Status secara default.
- Tombol **Tampilkan Offline** tersedia pada halaman Status.
- Worker offline lebih dari batas waktu dihapus dari Supabase jika tidak mempunyai job aktif.
- Worker yang masih mempunyai job aktif tidak dihapus; failover diberi kesempatan memindahkan job terlebih dahulu.
- Cleanup berjalan di maintenance worker dan juga diverifikasi ketika dashboard status dibuka.

## Variabel Railway

```env
V1622_WORKER_CLEANUP_ENABLED=1
V1622_WORKER_DELETE_AFTER_SECONDS=1800
V1622_SHOW_OFFLINE_WORKERS_DEFAULT=0
V1622_WORKER_CLEANUP_INTERVAL_SECONDS=120
```

`1800` detik berarti 30 menit. Setelah mengubah variabel, lakukan Redeploy semua service yang memakai source ini.

## Pemeriksaan

Buka `/v16.2.5-status`. Nilai `cleanup_enabled` harus `true` dan `delete_after_seconds` harus `1800`.

Log cleanup menggunakan awalan:

```text
[WORKER-CLEANUP]
```
