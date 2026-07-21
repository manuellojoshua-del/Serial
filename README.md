# CineDrive v16.2 Enterprise Serial Manager Final

Versi ini mempertahankan **Smart Catalog v16** dan **Automatic Failover v16.1**, lalu menambahkan pengelolaan serial langsung dari panel web.

## Fitur v16.2

- Menu **Tambah Episode ke Serial Tersimpan** sekarang menampilkan tindakan pengelolaan pada setiap kartu serial.
- Tombol **🔄 Reset ke E01** mengosongkan episode dan referensi katalog sehingga episode berikutnya kembali E01.
- Tombol **🗑 Hapus Serial** menghapus record serial dari database canonical Supabase.
- Opsi menghapus katalog Telegram aktif saat reset/hapus.
- Backup otomatis dibuat sebelum perubahan.
- Reset/hapus ditolak ketika serial masih mempunyai tugas aktif.
- Tugas lama yang masih QUEUED/ERROR dibatalkan.
- Perubahan memakai operasi replace canonical agar data yang dihapus tidak muncul kembali akibat merge.
- Endpoint status terbaru: `/v16.2-status`.

## Letak menu

1. Buka `/panel?key=SECRET_KEY`.
2. Pilih **Pengelolaan Serial**.
3. Buka **Tambah Episode ke Serial Tersimpan**.
4. Cari serial. Tombol **Reset ke E01** dan **Hapus Serial** berada di bawah kartu serial.

## Deploy Railway

1. Ganti seluruh isi repository dengan paket ini.
2. Gunakan source code yang sama pada semua Railway worker.
3. Pastikan masing-masing service mempunyai `CLUSTER_WORKER_ID` berbeda.
4. Pastikan `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `CLUSTER_NAMESPACE`, Telegram, dan TMDB tersedia.
5. Redeploy seluruh worker.
6. Buka `/v16.2-status` dan pastikan versi `16.2.0` tampil.
