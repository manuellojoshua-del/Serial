# Google Drive → Telegram v10.5.4

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
