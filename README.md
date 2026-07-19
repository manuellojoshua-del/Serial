# CineDrive v13.2 Enterprise Global Assets

Versi ini meneruskan CineDrive v13.1 Enterprise Global Queue dan menambahkan watermark logo dari Google Drive agar job dapat diproses oleh Railway mana pun.

## Fitur utama

- Pilihan sumber logo pada semua formulir: **Google Drive** atau **Upload dari perangkat**.
- Logo Google Drive disimpan sebagai File ID di job global, bukan sebagai file lokal Railway.
- Worker yang mengklaim job mengunduh logo ke folder sementara sebelum FFmpeg berjalan.
- Job dengan video Google Drive + logo Google Drive + subtitle Google Drive/internal/tanpa subtitle dapat diproses worker kosong mana pun.
- Job dengan logo atau subtitle upload dari HP tetap dikunci ke worker asal.
- Smart Watermark Safe Area, H.265 Turbo, Supabase canonical database, multi-bot, scheduler, dashboard, dan katalog episode tetap tersedia.

## Cara memakai logo Google Drive

1. Upload logo PNG/WEBP/JPG/GIF ke Google Drive.
2. Ubah akses menjadi **Anyone with the link – Viewer**.
3. Pada panel, centang **Aktifkan watermark logo**.
4. Pilih **Google Drive — dapat diproses semua Railway**.
5. Tempel link seperti `https://drive.google.com/file/d/FILE_ID/view` atau File ID saja.
6. Tambahkan job ke antrean.

## Agar job menjadi global

Gunakan:

- Video: Google Drive.
- Logo: Google Drive.
- Subtitle: Google Drive, internal video, otomatis dari folder Drive, atau tanpa subtitle.

Upload logo/subtitle langsung dari perangkat membuat job tetap lokal karena file hanya tersedia pada Railway penerima upload.

## Variabel scheduler

```env
SCHEDULER_ENABLED=1
SCHEDULER_POLL_SECONDS=5
SCHEDULER_MAX_JOBS_PER_WORKER=1
ENTERPRISE_CLUSTER_ENABLED=1
GLOBAL_SYNC_ENABLED=1
GLOBAL_SYNC_BOOTSTRAP_LOCAL=0
GLOBAL_DATABASE_PUBLISH_LOCAL=0
```

Gunakan konfigurasi Supabase dan `CLUSTER_NAMESPACE` yang sama pada seluruh Railway. `CLUSTER_WORKER_ID` dan `BOT_TOKEN` harus berbeda untuk tiap worker.
