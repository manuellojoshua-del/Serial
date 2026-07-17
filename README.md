# Google Drive → Telegram v10.6 — Smart Watermark v2

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

## v10.6.1 — Smart Watermark Safe Area

- Logo dibakar permanen ke gambar video hasil encode.
- FFmpeg mendeteksi area gambar aktif dengan `cropdetect` pada beberapa bagian video.
- Posisi watermark dihitung terhadap area film, bukan seluruh frame, sehingga tidak masuk ke bar hitam sinematik/letterbox.
- Logo tetap berada pada sudut yang dipilih: kanan atas, kiri atas, kanan bawah, atau kiri bawah.
- Mode Smart hanya memberi gerakan kecil dan halus di sekitar sudut pilihan; logo tidak berganti sudut.
- Ukuran logo dihitung dari lebar area gambar aktif agar konsisten pada film 16:9, 21:9, 2.35:1, 4:3, dan format lain.
- Jika bar hitam tidak dapat dideteksi dengan yakin, aplikasi memakai seluruh frame agar proses encode tetap berjalan.

## v10.6.1 Turbo

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
