# CineDrive v11.0.3 Cluster Final

Versi ini memperbaiki heartbeat Supabase untuk struktur tabel `cinedrive_cluster` yang memiliki kolom lama dan baru sekaligus.

## Perbaikan utama

- Heartbeat mengisi semua kolom wajib: `namespace`, `bucket`, `item_key`, `value`, `updated_at`, `record_type`, `record_key`, dan `data`.
- UPSERT memakai identitas `namespace,bucket,item_key`.
- Worker disimpan dengan `bucket=workers` dan `item_key=CLUSTER_WORKER_ID`.
- Dokumen sinkron disimpan dengan `bucket=documents`.
- Heartbeat otomatis dijalankan saat startup dan setiap 30 detik.
- Endpoint `/cluster-heartbeat` dan `/cluster-status` tetap tersedia.

## Pemasangan

1. Upload semua file ZIP ini ke root repository GitHub.
2. Di Supabase buka **SQL Editor** dan jalankan seluruh isi `supabase_setup.sql`.
3. Pastikan Railway Variables berisi:

```env
SUPABASE_URL=https://PROJECT_ID.supabase.co
SUPABASE_SERVICE_ROLE_KEY=ISI_SERVICE_ROLE_KEY
CLUSTER_NAMESPACE=cinemaxx1-production
CLUSTER_WORKER_ID=railway-1
```

4. Redeploy Railway.
5. Periksa Deploy Logs. Hasil normal:

```text
[CLUSTER] heartbeat OK worker=railway-1 namespace=cinemaxx1-production
```

6. Buka:

```text
https://domain-anda/cluster-heartbeat
https://domain-anda/cluster-status
```

Status normal menampilkan `version: 11.0.3`, `heartbeat_ok: true`, dan `active_worker_count: 1`.

Untuk Railway kedua gunakan Supabase dan namespace yang sama, tetapi ubah:

```env
CLUSTER_WORKER_ID=railway-2
```
