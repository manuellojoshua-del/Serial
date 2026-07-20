# CineDrive v15 Enterprise Stable

Versi ini menggabungkan **Single Serial Catalog**, **Supabase Canonical Database**, dan scheduler cluster yang lebih tahan terhadap job/claim lama.

## Perbaikan utama

- Scheduler hanya memakai record terbaru untuk setiap serial, season, dan episode. Riwayat `ERROR` atau `SUCCESS` lama tidak lagi memblokir job baru.
- Claim scheduler kedaluwarsa dan riwayat terminal dibersihkan otomatis.
- Upload Telegram dicoba ulang sampai 3 kali.
- Jika video episode sudah berhasil dikirim tetapi pembaruan katalog gagal, job tetap `SUCCESS`; katalog dicatat sebagai peringatan sehingga episode berikutnya tidak tertahan.
- Scheduler menggunakan event lokal untuk bangun segera setelah job dibuat, dengan polling Supabase sebagai fallback lintas Railway.
- Endpoint status: `/v15-status`.

## Konfigurasi pusat GitHub

Pengaturan non-rahasia berada di `config.json`. Ubah file tersebut di GitHub dan redeploy source yang sama pada semua Railway. Environment variable Railway tetap menang jika nilainya dipasang.

Rahasia berikut **tetap wajib di Railway** dan jangan dimasukkan ke GitHub:

```env
BOT_TOKEN=...
CATALOG_BOT_TOKEN=...
SECRET_KEY=...
TMDB_API_KEY=...
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
CHANNEL_ID=...
CLUSTER_WORKER_ID=railway-1
```

`CLUSTER_WORKER_ID` harus berbeda pada setiap Railway. `CATALOG_BOT_TOKEN` boleh sama dengan `BOT_TOKEN`.

## Config URL opsional

Selain `config.json` dalam repository, Anda dapat menyimpan JSON publik/privat yang dapat diakses server lalu memasang satu kali:

```env
CONFIG_URL=https://raw.githubusercontent.com/USER/REPO/main/config.json
```

Konfigurasi dibaca saat startup. Untuk menerapkan perubahan, lakukan redeploy; Railway yang terhubung ke GitHub biasanya redeploy otomatis setelah commit.

## Deploy

1. Upload semua file ZIP ke repository GitHub.
2. Jalankan `supabase_setup.sql` jika tabel belum ada.
3. Pastikan rahasia di atas tersedia pada setiap Railway.
4. Redeploy semua worker.
5. Periksa `/v15-status` pada setiap domain.
