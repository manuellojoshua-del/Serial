# CineDrive v11 Cluster Ready Deploy

Versi ini mempertahankan fitur v10.6.2.2 dan menambahkan sinkronisasi metadata
antar beberapa akun/service Railway menggunakan Supabase.

## Yang tersinkron

- Serial tersimpan dan daftar episode.
- Message ID posting indeks Telegram.
- Topic/thread Telegram hasil scan.
- Metadata TMDB yang disimpan bersama serial.
- Daftar worker aktif melalui heartbeat.
- Cache lokal tetap dibuat pada `/data` sebagai cadangan.

## Batasan penting

Video, subtitle upload, logo upload, file FFmpeg sementara, dan proses encode tetap
berada di worker Railway yang menerima pekerjaan. File besar tidak disalin melalui
Supabase. Karena itu, antrean encode belum dipindahkan otomatis ke worker lain
setelah proses sudah berjalan. Sinkronisasi ini berfokus pada data serial agar
beberapa akun Railway tidak memakai daftar episode yang berbeda.

## Menyiapkan Supabase

1. Buat project Supabase.
2. Buka **SQL Editor**.
3. Jalankan seluruh isi `supabase_setup.sql`.
4. Buka **Project Settings → API**.
5. Salin Project URL dan `service_role` key. Jangan memakai anon key.

## Variables pada setiap Railway

Gunakan nilai Supabase dan namespace yang sama:

```env
SUPABASE_URL=https://PROJECT_ID.supabase.co
SUPABASE_SERVICE_ROLE_KEY=SERVICE_ROLE_KEY
CLUSTER_NAMESPACE=cinemaxx1-production
```

Gunakan Worker ID berbeda:

Railway pertama:

```env
CLUSTER_WORKER_ID=railway-1
```

Railway kedua:

```env
CLUSTER_WORKER_ID=railway-2
```

Railway ketiga:

```env
CLUSTER_WORKER_ID=railway-3
```

Variabel lain seperti `BOT_TOKEN`, `TMDB_API_KEY`, target channel, dan Local Bot
API tetap diisi seperti sebelumnya.

## Memeriksa cluster

Buka:

```text
https://DOMAIN-PANEL/cluster-status
```

Respons menampilkan namespace, Worker ID, dan worker yang masih aktif. Worker
dianggap aktif jika heartbeat diterima dalam dua menit terakhir.

## Cara kerja konflik episode

Setiap perubahan serial disimpan lokal lalu digabungkan dengan dokumen terbaru
di Supabase. Map episode digabung secara rekursif, sehingga Episode 1 yang dibuat
worker A tidak hilang saat worker B menambahkan Episode 2.

Untuk keamanan, `SUPABASE_SERVICE_ROLE_KEY` hanya boleh disimpan di Variables
Railway dan tidak boleh dimasukkan ke GitHub.

---

# Google Drive → Telegram v10.6.2.2 — Smart Watermark v2

Versi ini menambahkan watermark logo yang benar-benar ditanam ke gambar film saat proses FFmpeg. Logo tetap terlihat ketika video diputar, diunduh, atau diteruskan.

## Smart Watermark v2

- Mode **Smart v2**: logo bergerak halus di area aman video.
- Mode **Statis**: logo tetap di kanan atas, kiri atas, kanan bawah, atau kiri bawah.
- Pilihan kecepatan lambat, normal, dan cepat.
- Ukuran logo 5%, 8%, 10%, atau 15% dari lebar video.
- Transparansi 20% sampai 100%.
- Mendukung PNG, WEBP, JPG, JPEG, dan GIF.
- Logo digabung permanen ke hasil video, bukan hanya ditampilkan di panel.
- Berlaku untuk film TMDB, batch episode, mode manual, dan episode serial tersimpan.
- Tetap kompatibel dengan subtitle dan target video Telegram di bawah 1,5 GB.

## Cara memakai

1. Buka film atau serial yang akan diproses.
2. Centang **Aktifkan watermark logo**.
3. Pilih **Smart Watermark v2 — bergerak halus**.
4. Upload logo transparan PNG/WEBP/GIF.
5. Pilih ukuran 8% dan transparansi 35%–50% sebagai pengaturan awal.
6. Tambahkan video ke antrean. Watermark akan menjadi bagian permanen dari video hasil encode.

Versi ini memperbaiki kegagalan FFmpeg/H.265 pada Railway dan VPS dengan resource terbatas.

## Perbaikan utama

- H.265 dibatasi ke 2 thread dan 1 frame thread secara default.
- WPP x265 dinonaktifkan untuk menekan penggunaan RAM.
- Log FFmpeg ditulis ke file sehingga pipe stderr tidak penuh atau macet.
- Pesan error menampilkan exit code dan bagian akhir log FFmpeg.
- Fallback otomatis ke H.264 jika H.265 gagal.
- Target ukuran tetap maksimal 1,49 GB, default 1,45 GB.
- Navigasi bawah v10.5 tetap tersedia.
- Setelah **Tambahkan Episode Baru**, panel langsung membuka **Antrean & status**.
- Subtitle, watermark, batch episode, mode manual, dan serial tersimpan tetap tersedia.

## Variabel Railway/VPS yang disarankan

```env
TELEGRAM_TARGET_GB=1.45
TELEGRAM_AUDIO_KBPS=128
TELEGRAM_VIDEO_CODEC=libx265
TELEGRAM_X265_PRESET=veryfast
TELEGRAM_X265_THREADS=2
TELEGRAM_X265_FRAME_THREADS=1
TELEGRAM_X265_WPP=0
TELEGRAM_FALLBACK_H264=1
```

Untuk VPS dengan RAM 8 GB atau lebih, thread dapat dinaikkan menjadi:

```env
TELEGRAM_X265_THREADS=4
TELEGRAM_X265_FRAME_THREADS=2
```

## Deploy Railway

1. Ganti file project dengan isi ZIP ini.
2. Pastikan volume tetap di-mount ke `/data`.
3. Tambahkan variabel di atas.
4. Pilih **Redeploy** agar image dibangun ulang.

## Deploy VPS Ubuntu

Jalankan ulang build container:

```bash
docker compose up -d --build
```

Data serial tetap aman selama folder `/data` atau volume host tidak dihapus.

## Fitur baru v10.5.4 — Manajemen Data

Buka menu **Data** pada navigasi panel untuk:

- melihat isi `telegram-series.json`, `telegram-topics.json`, dan `telegram-scan-results.json`;
- mengunduh masing-masing file JSON;
- upload/restore file JSON dengan validasi struktur;
- export seluruh data dan backup menjadi satu ZIP;
- import seluruh data dari ZIP;
- membuat backup manual;
- melihat, memulihkan, dan menghapus backup;
- melihat jumlah serial, episode, topic, backup, dan sisa ruang volume;
- membersihkan hasil scan setelah backup otomatis dibuat.

Semua operasi perubahan data membuat backup terlebih dahulu. Volume Railway harus tetap di-mount ke `/data`.


## Fitur baru v10.5.4 — Landing Page CINEMAXX1

- Route `/` sekarang menampilkan landing page CINEMAXX1 responsif, bukan JSON mentah.
- Menampilkan status server, jumlah serial, jumlah episode, antrean aktif, dan versi aplikasi.
- Form masuk panel menggunakan `SECRET_KEY` dan mengarah ke `/panel`.
- Tombol status API tersedia melalui `/health`.
- Panel dan seluruh fitur v10.5.3 tetap dipertahankan.

## v10.6.2 — Smart Watermark Safe Area

- Logo dibakar permanen ke gambar video hasil encode.
- FFmpeg mendeteksi area gambar aktif dengan `cropdetect` pada beberapa bagian video.
- Posisi watermark dihitung terhadap area film, bukan seluruh frame, sehingga tidak masuk ke bar hitam sinematik/letterbox.
- Logo tetap berada pada sudut yang dipilih: kanan atas, kiri atas, kanan bawah, atau kiri bawah.
- Mode Smart hanya memberi gerakan kecil dan halus di sekitar sudut pilihan; logo tidak berganti sudut.
- Ukuran logo dihitung dari lebar area gambar aktif agar konsisten pada film 16:9, 21:9, 2.35:1, 4:3, dan format lain.
- Jika bar hitam tidak dapat dideteksi dengan yakin, aplikasi memakai seluruh frame agar proses encode tetap berjalan.

## v10.6.2 Turbo

Peningkatan khusus untuk mempercepat encode H.265 1080p di Railway:

- Preset bawaan H.265 berubah menjadi `superfast`.
- Jumlah thread otomatis mengikuti CPU container (`TELEGRAM_X265_THREADS=0`).
- Frame thread otomatis dan dibatasi agar penggunaan RAM tetap stabil.
- WPP diaktifkan untuk meningkatkan paralelisme encode.
- Parameter x265 Turbo memakai lookahead, B-frame, reference frame, dan motion search yang lebih ringan.
- Panel menampilkan jumlah CPU, thread, preset, dan status WPP yang benar-benar digunakan.
- Smart Watermark Safe Area, subtitle, target 1,45 GB, fallback H.264, dan seluruh fitur serial tetap dipertahankan.

Variabel Railway yang direkomendasikan:

```env
TELEGRAM_X265_PRESET=superfast
TELEGRAM_X265_THREADS=0
TELEGRAM_X265_FRAME_THREADS=0
TELEGRAM_X265_WPP=1
TELEGRAM_X265_TURBO=1
TELEGRAM_FALLBACK_H264=1
```

Jika Railway menunjukkan penggunaan RAM tinggi atau service restart, gunakan:

```env
TELEGRAM_X265_THREADS=2
TELEGRAM_X265_FRAME_THREADS=1
```

## v10.6.2 — Detail TMDB Episode & Ganti Postingan Indeks

- Penambahan episode dari menu **Tambah Episode** mengambil ulang detail episode dari TMDB.
- Caption video episode berisi judul resmi, tanggal tayang, rating, genre, pemeran, kru, dan sinopsis TMDB.
- Setelah episode berhasil dikirim, aplikasi membuat posting indeks serial terbaru dengan seluruh tombol episode.
- Postingan indeks lama kemudian dihapus otomatis agar channel tidak memiliki daftar episode ganda.
- Penghapusan dilakukan setelah posting baru berhasil dibuat, sehingga daftar serial tidak hilang jika pengiriman posting baru gagal.
- Untuk serial manual atau ketika TMDB sedang gagal diakses, aplikasi memakai metadata serial tersimpan sebagai fallback.
- Bot harus menjadi administrator dan memiliki izin **Delete Messages** di channel atau supergroup tujuan.

## v10.6.2.1 — Film sebagai satu posting video + detail TMDB

- Saat menambahkan **film**, bot tidak lagi mengirim poster sebagai pesan terpisah.
- Video film menjadi posting utama.
- Detail TMDB ditampilkan langsung sebagai caption di bawah video:
  judul, AKA, durasi, kategori, rating, tanggal rilis, genre, negara,
  bahasa, sutradara, penulis, pemeran, dan sinopsis.
- Thumbnail video tetap dibuat dari cuplikan film agar tampilan Telegram menarik.
- Episode serial dan posting indeks serial tetap menggunakan alur v10.6.2.


## v10.6.2.2 — Format film: Poster + detail TMDB, lalu video

Untuk konten film, bot mengirim dua pesan berurutan dalam topic/channel yang sama:

1. Poster TMDB dengan caption detail lengkap.
2. Video film tepat di bawahnya dengan caption judul film.

Detail poster meliputi judul, AKA, durasi, kategori, rating, tanggal rilis,
genre, negara, bahasa, sutradara, penulis, pemeran, dan sinopsis.

Alur serial/episode, tombol episode, subtitle, Smart Watermark Safe Area,
target ukuran, dan H.265 Turbo tetap dipertahankan.

## Perbaikan paket Ready Deploy

Paket ini tidak lagi mengimpor `cluster_store.py` sebagai modul terpisah.
Kode cluster sudah ditanam langsung ke `app.py`, sehingga error berikut tidak
akan muncul lagi:

```text
ModuleNotFoundError: No module named 'cluster_store'
```

Dockerfile juga menjalankan pemeriksaan sintaks saat build. Upload semua file
di dalam ZIP ke root repository GitHub, bukan hanya `app.py`.
