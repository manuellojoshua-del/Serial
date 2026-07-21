# CineDrive v16.2.2 Worker Cleanup

Fitur baru:
- Daftar Status Railway menampilkan worker ONLINE saja secara default.
- Tombol **Tampilkan Offline** untuk melihat worker yang sedang offline.
- Record worker offline dihapus otomatis dari Supabase setelah 6 jam.
- Worker tidak dihapus jika masih memiliki job aktif.
- Worker yang sedang menjalankan aplikasi tidak pernah menghapus record dirinya sendiri.
- Endpoint status: `/v16.2.2-status`.

Variabel Railway:
```env
V1622_WORKER_CLEANUP_ENABLED=1
V1622_WORKER_DELETE_AFTER_SECONDS=21600
V1622_SHOW_OFFLINE_WORKERS_DEFAULT=0
```

`21600` detik = 6 jam. Deploy source yang sama ke semua service Railway.
