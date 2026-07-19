# CineDrive v11.2 Multi-Bot Cluster

Versi ini meneruskan CineDrive v11.1 dan menambahkan kelanjutan serial lintas bot Telegram melalui Supabase.

## Cara kerja multi-bot

Semua Railway memakai `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `CLUSTER_NAMESPACE`, `CHANNEL_ID`, dan topic tujuan yang sama. Setiap Railway boleh memakai `BOT_TOKEN` yang berbeda. Ketika bot kedua menambah episode, aplikasi membaca daftar episode yang dibuat bot pertama dari Supabase, menambahkan episode baru, membuat katalog terbaru, lalu menghapus katalog lama.

Data setiap episode menyimpan worker dan identitas bot pengunggah. Katalog serial juga menyimpan identitas bot yang terakhir memperbaruinya.

## Konfigurasi yang disarankan: satu bot per Railway

Railway pertama:

```env
BOT_TOKEN=TOKEN_BOT_PERTAMA
CLUSTER_WORKER_ID=railway-1
```

Railway kedua:

```env
BOT_TOKEN=TOKEN_BOT_KEDUA
CLUSTER_WORKER_ID=railway-2
```

Variabel berikut harus sama pada semua Railway:

```env
SUPABASE_URL=https://PROJECT_ID.supabase.co
SUPABASE_SERVICE_ROLE_KEY=SERVICE_ROLE_KEY
CLUSTER_NAMESPACE=cinemaxx1-production
CHANNEL_ID=-1001234567890
```

## Beberapa bot dalam satu Railway

Opsional, masukkan token dipisahkan koma:

```env
BOT_TOKENS=TOKEN_BOT_1,TOKEN_BOT_2,TOKEN_BOT_3
BOT_TOKEN_INDEX=1
```

`BOT_TOKEN_INDEX` dimulai dari 1. Jika tidak diisi, token dipilih stabil berdasarkan `CLUSTER_WORKER_ID`.

## Izin Telegram yang wajib

Semua bot harus menjadi administrator di channel atau supergroup yang sama dan mempunyai izin:

- Post Messages
- Edit Messages
- Delete Messages

Saat menghapus katalog lama, v11.2 dapat mencoba bot aktif lalu token lain yang tersedia dalam `BOT_TOKENS`.

## Format serial

- Video episode dikirim sebagai posting tersendiri.
- Poster, detail TMDB, dan tombol episode dibuat sebagai satu katalog.
- Maksimal lima tombol episode per baris.
- Ketika episode baru masuk, katalog terbaru dibuat dahulu, kemudian katalog lama dihapus.
- Daftar episode disinkronkan melalui Supabase agar dapat diteruskan bot lain.

## Format film

1. Poster TMDB beserta detail lengkap.
2. Video film tepat di bawahnya.

## Endpoint pemeriksaan

- `/health`
- `/bot-status`
- `/cluster-status`
- `/cluster-heartbeat`

`/bot-status` menampilkan jumlah bot yang dikonfigurasi dan bot aktif pada worker tersebut.

## Deploy

1. Upload semua file ke root repository GitHub.
2. Jalankan `supabase_setup.sql` jika tabel belum tersedia.
3. Atur Variables Railway.
4. Pastikan semua bot telah menjadi admin pada target Telegram.
5. Redeploy.
