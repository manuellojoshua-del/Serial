# CineDrive v12.2 Episode Catalog

Versi ini mempertahankan seluruh fitur CineDrive v12.1 dan mengubah posting katalog serial agar menyerupai contoh katalog Telegram.

## Format katalog serial

Setiap episode baru akan menghasilkan satu posting katalog terbaru:

- poster serial;
- judul dan tahun;
- daftar episode berupa tautan teks yang bisa diketuk;
- maksimal 5 episode per baris;
- tulisan **Tap episode untuk menonton**.

Contoh:

```text
Tobat Jatuh Cinta (2026)

➡️ E.01 | E.02 | E.03 | E.04 | E.05
➡️ E.06 | E.07 | E.08 | E.09 | E.10

👇 Tap episode untuk menonton
```

## Saat episode baru ditambahkan

1. Video episode baru dikirim ke Telegram.
2. CineDrive membuat katalog baru yang berisi seluruh episode lama dan episode terbaru.
3. Katalog lama dihapus setelah katalog baru berhasil dibuat.
4. Video episode lama tidak dihapus.

Semua tautan episode mengarah langsung ke pesan video masing-masing.

## Persyaratan bot Telegram

Semua bot yang digunakan harus menjadi administrator di channel atau supergroup tujuan dan memiliki izin:

- Post Messages;
- Delete Messages.

Untuk multi-bot, penghapusan katalog lama dicoba menggunakan seluruh token bot yang dikonfigurasi.

## Deploy

1. Upload semua file dari ZIP ke repository.
2. Gunakan source yang sama pada seluruh Railway.
3. Jalankan kembali `supabase_setup.sql` bila belum pernah dijalankan.
4. Redeploy seluruh service Railway.
5. Tambahkan satu episode baru untuk membuat katalog dengan format baru.

Versi aplikasi: `12.2.0`.
