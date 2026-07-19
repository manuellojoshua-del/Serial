# CineDrive v13.1 Enterprise — Global Unassigned Queue

Versi ini memperbaiki scheduler agar pekerjaan Google Drive tidak lagi terkunci ke Railway pengirim.

## Perubahan utama

- Job portable dibuat sebagai `QUEUED` dengan `assigned_worker` kosong.
- Railway yang sedang kosong melakukan claim atomik melalui Supabase.
- Jika `railway-2` sedang encode, `railway-1` dapat mengambil episode berikutnya.
- Dashboard menampilkan **Menunggu worker** sebelum job diklaim.
- Upload subtitle/logo dari HP tetap diproses pada Railway asal karena file tersebut hanya tersedia secara lokal.
- Batas proses per worker mengikuti `SCHEDULER_MAX_JOBS_PER_WORKER`.
- Job QUEUED dari worker yang mati dikembalikan ke antrean global, bukan dipindahkan secara statis.

## Variabel Railway

Gunakan pada semua Railway:

```env
SCHEDULER_ENABLED=1
SCHEDULER_POLL_SECONDS=5
SCHEDULER_MAX_JOBS_PER_WORKER=1
SCHEDULER_CLAIM_TTL_SECONDS=21600
ENTERPRISE_CLUSTER_ENABLED=1
GLOBAL_SYNC_ENABLED=1
GLOBAL_SYNC_BOOTSTRAP_LOCAL=0
GLOBAL_DATABASE_PUBLISH_LOCAL=0
V13_QUEUE_FAILOVER_ENABLED=1
V13_QUEUE_FAILOVER_SECONDS=120
```

Variabel Supabase, namespace, dan channel harus sama. `CLUSTER_WORKER_ID` dan `BOT_TOKEN` harus berbeda pada tiap Railway.

## Pengujian

1. Pastikan kedua worker terlihat aktif pada `/enterprise-status`.
2. Masukkan dua episode berbasis Google Drive secara berurutan.
3. Dengan batas satu job per worker, episode pertama dan kedua seharusnya diklaim worker yang berbeda.
4. Periksa `/scheduler-status` atau menu **Status**.

Versi: `13.1.0`
