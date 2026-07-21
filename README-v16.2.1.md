# CineDrive v16.2.1 Final Serial Manager

Perubahan utama:

- Tombol **Reset ke E01** dan **Hapus Serial** tampil langsung di bawah setiap kartu serial pada menu **Tambah Episode**.
- Menu **Reset / Hapus Serial** juga tetap tersedia sebagai halaman pengelolaan khusus.
- Reset mengosongkan episode dan katalog aktif, tetapi mempertahankan metadata serial.
- Hapus menghapus record serial dari database canonical Supabase.
- Operasi memakai exact replacement agar episode yang dihapus tidak muncul kembali karena merge.
- Endpoint status baru: `/v16.2.1-status`.

Deploy: ganti `app.py` lama dengan file ini, lalu redeploy seluruh service Railway yang memakai source yang sama.
