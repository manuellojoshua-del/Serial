# CineDrive v16.2.3 Queue Manager

Versi ini mempertahankan Smart Catalog, Automatic Failover, Serial Manager, dan Worker Cleanup, serta menambahkan pengelolaan antrean langsung dari panel web.

## Fitur Queue Manager

Pada kartu di menu **Antrean** tersedia:

- **Proses Sekarang** untuk job `QUEUED`. Permintaan tetap mematuhi urutan episode dan kapasitas worker.
- **Antrekan Ulang** untuk job `ERROR` jika payload Google Drive masih tersedia.
- **Hapus Antrean** untuk job `QUEUED`, `ERROR`, atau `SUCCESS`.

Penghapusan membersihkan record lokal, antrean proses lokal, lock klaim scheduler, dan record `enterprise-job` di Supabase. Job yang sedang `CLAIMED`, `DOWNLOADING`, `PROCESSING`, `PREPARING`, `READY`, atau `UPLOADING` ditolak agar proses aktif tidak rusak.

## Endpoint status

`/v16.2.3-status`

## Deploy Railway

1. Letakkan semua file ini langsung di root repository.
2. Deploy source yang sama ke semua worker Railway.
3. Gunakan `CLUSTER_WORKER_ID` berbeda pada setiap worker.
4. Buka endpoint `/v16.2.3-status` dan pastikan versi `16.2.3`.
5. Buka menu **Antrean** dan refresh halaman.

Tidak diperlukan perubahan tabel Supabase.
