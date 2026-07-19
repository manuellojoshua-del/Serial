# CineDrive v11.3 Global Sync

Versi ini membuat data serial pada beberapa Railway menggunakan satu sumber utama di Supabase. File JSON di volume Railway tetap dipakai sebagai cache dan sebagai sumber migrasi data lama, bukan sebagai sumber data yang berdiri sendiri.

## Fitur v11.3

- Menu **Serial → Tambah Episode** membaca data global dari Supabase.
- Data lama dari volume setiap Railway digabung otomatis ke penyimpanan global.
- Serial duplikat dinormalisasi berdasarkan `TMDB ID + season`.
- Episode dari bot/Railway berbeda digabung, sehingga episode berikutnya sama pada semua panel.
- Topic dan hasil scan Telegram ikut disinkronkan.
- Sinkronisasi otomatis setiap 15 detik.
- Cache `/data/telegram-series.json` diperbarui dari Supabase sebagai fallback.
- Multi-bot dan heartbeat cluster v11.2 tetap tersedia.

## Variabel yang wajib sama di semua Railway

```env
SUPABASE_URL=https://PROJECT_ID.supabase.co
SUPABASE_SERVICE_ROLE_KEY=SERVICE_ROLE_KEY
CLUSTER_NAMESPACE=cinemaxx1-production
CHANNEL_ID=-100xxxxxxxxxx
GLOBAL_SYNC_ENABLED=1
GLOBAL_SYNC_BOOTSTRAP_LOCAL=1
GLOBAL_SYNC_INTERVAL=15
```

Variabel yang harus berbeda:

```env
# Railway pertama
CLUSTER_WORKER_ID=railway-1
BOT_TOKEN=TOKEN_BOT_1

# Railway kedua
CLUSTER_WORKER_ID=railway-2
BOT_TOKEN=TOKEN_BOT_2
```

Semua bot harus menjadi admin pada channel/supergroup yang sama dan memiliki izin mengirim serta menghapus pesan.

## Deploy dan migrasi

1. Jalankan `supabase_setup.sql` sekali di Supabase SQL Editor.
2. Deploy ZIP yang sama pada seluruh Railway.
3. Pastikan `CLUSTER_NAMESPACE` sama persis, termasuk huruf besar/kecil dan tanda hubung.
4. Tunggu 30–60 detik. Masing-masing Railway akan mengimpor cache serial lamanya ke Supabase dan menggabungkan episode.
5. Muat ulang kedua panel.

Endpoint pemeriksaan:

```text
/global-sync-status
/cluster-status
/bot-status
```

Nilai `series_fingerprint` pada `/global-sync-status` harus sama di seluruh domain. Jika sama, menu serial memakai data yang identik.

## Setelah migrasi stabil

Setelah kedua panel sudah sama, ubah pada semua Railway:

```env
GLOBAL_SYNC_BOOTSTRAP_LOCAL=0
```

Ini mencegah data lokal lama yang tidak diperlukan ikut diimpor lagi. Supabase tetap menjadi sumber utama dan file lokal hanya menjadi cache.

## Catatan antrean

v11.3 menyinkronkan data serial, episode, topic, dan hasil scan. Proses encode yang sudah berjalan tetap dijalankan oleh Railway tempat antrean dibuat. Jangan mengirim episode yang sama secara bersamaan dari dua panel.
