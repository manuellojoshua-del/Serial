# CineDrive v14.2 — Single Serial Catalog

Versi ini mempertahankan **satu posting katalog untuk setiap serial**.

## Alur serial

1. Episode pertama dikirim sebagai video.
2. Bot katalog membuat satu posting poster berisi judul dan tautan `E.01`.
3. Saat Episode 2 selesai, bot mengedit posting katalog yang sama menjadi `E.01 | E.02`.
4. Episode berikutnya terus ditambahkan ke posting yang sama.
5. Mengetuk nomor episode membuka langsung pesan video episode tersebut.

Tidak ada poster katalog baru untuk setiap episode. Video episode tetap dikirim sebagai pesan tersendiri.

## Multi-Railway

Semua Railway harus memakai nilai `CATALOG_BOT_TOKEN` yang sama. Telegram hanya mengizinkan bot pembuat pesan mengedit pesan tersebut.

Jika katalog lama dibuat oleh bot berbeda dan tidak dapat diedit, CineDrive membuat katalog baru sebagai fallback lalu mencoba menghapus katalog lama.

## Variabel penting

```env
CATALOG_BOT_TOKEN=TOKEN_BOT_KATALOG
SERIES_SEQUENTIAL_SCHEDULER=1
SMART_PIPELINE_SCHEDULER=1
SCHEDULER_ENABLED=1
GLOBAL_SYNC_ENABLED=1
GLOBAL_SYNC_BOOTSTRAP_LOCAL=0
GLOBAL_DATABASE_PUBLISH_LOCAL=0
```

`CATALOG_BOT_TOKEN` boleh sama dengan `BOT_TOKEN` jika hanya memakai satu bot.

## Deploy

1. Ganti seluruh file project dengan isi ZIP.
2. Gunakan source yang sama pada semua Railway.
3. Pastikan bot katalog menjadi admin dan memiliki izin kirim, edit, serta hapus pesan.
4. Redeploy semua service.
5. Tambahkan episode baru. Katalog lama akan diedit, bukan dibuat ulang.
