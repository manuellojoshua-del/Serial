# CineDrive v11.4 Global Database

Versi ini menjadikan satu dokumen kanonis di Supabase sebagai sumber utama data serial. File `/data/telegram-series.json` hanya cache lokal dan cadangan saat Supabase tidak tersedia.

## Perubahan utama

- Kedua Railway membaca data serial dari dokumen Supabase `series` yang sama.
- Data lama setiap volume dipublikasikan sebagai snapshot migrasi `series-source:<worker_id>`.
- Snapshot semua Railway digabung secara deterministik ke database kanonis.
- Menu **Serial → Tambah Episode** pada semua domain memakai data yang sama.
- Episode baru dari bot/Railway mana pun ditulis kembali ke database kanonis.
- Endpoint `/global-sync-status` menampilkan `database_mode`, fingerprint, dan jumlah sumber migrasi.
- Endpoint POST `/global-database-converge?key=SECRET_KEY` memaksa penggabungan saat diperlukan.

## Variabel yang sama di semua Railway

```env
SUPABASE_URL=https://PROJECT.supabase.co
SUPABASE_SERVICE_ROLE_KEY=...
CLUSTER_NAMESPACE=cinemaxx1-production
CHANNEL_ID=-100xxxxxxxxxx
GLOBAL_SYNC_ENABLED=1
GLOBAL_SYNC_INTERVAL=15
GLOBAL_SYNC_BOOTSTRAP_LOCAL=1
GLOBAL_DATABASE_PUBLISH_LOCAL=1
GLOBAL_DATABASE_REFRESH_SECONDS=10
```

Yang harus berbeda pada setiap Railway:

```env
CLUSTER_WORKER_ID=railway-1
BOT_TOKEN=TOKEN_BOT_1
```

Railway kedua:

```env
CLUSTER_WORKER_ID=railway-2
BOT_TOKEN=TOKEN_BOT_2
```

## Urutan deploy migrasi

1. Upload ZIP yang sama ke semua Railway.
2. Jalankan `supabase_setup.sql` satu kali.
3. Redeploy semua Railway.
4. Tunggu 30–60 detik.
5. Buka `/global-sync-status` di setiap domain.
6. `series_fingerprint`, `series_count`, dan `episode_count` harus sama.
7. Setelah sama, ubah `GLOBAL_SYNC_BOOTSTRAP_LOCAL=0` dan `GLOBAL_DATABASE_PUBLISH_LOCAL=0` pada semua Railway, lalu redeploy. Ini mencegah snapshot volume lama dipublikasikan lagi.

## Pemeriksaan

```text
https://domain-1/global-sync-status
https://domain-2/global-sync-status
```

Hasil normal:

```json
{
  "success": true,
  "version": "11.4.0",
  "database_mode": "supabase-canonical",
  "series_fingerprint": "nilai-yang-sama-di-semua-domain",
  "last_error": ""
}
```
