# Catatan pengembangan berikutnya

Daftar hal yang sengaja DITUNDA, bukan terlupa. Tiap butir menyebut kenapa
ditunda dan apa yang harus dikerjakan saat dilanjutkan.

## Warna identitas kelas pada projek VIDEO (sub-jenis aksi)

**Konteks.** Warna identitas kelas (palet kontras-tinggi + pemilih warna
popup + warna pilihan tersimpan per projek) sudah dikerjakan untuk alur
**gambar**:

- `app/services/render.py`: `PALET` (24 warna kontras), `warna_hex/warna_kelas/
  cls_color(nama, peta)`, `warna_override(item)`, sidik warna ikut membatalkan
  cache thumbnail, `RENDER_VERSI` dinaikkan ke 6.
- `app/services/tugas.py`: `kelas_warna` di `.tugas.json`, `set_warna_kelas`,
  `_sah_warna_kelas`, `MAKS_WARNA_KELAS`.
- Rute `POST /api/tugas/warna-kelas` (pemilik/Editor).
- Kanvas (`label.js`): `PALET`, `PALET_GRID` (112 warna teratur per kolom warna
  dasar), popup pemilih warna, `warna()` sadar override.
- Grid Dataset/Job + halaman Versi + `/view`: swatch ikut warna pilihan lewat
  filter `warnakelas(nama, peta)` dan `versi.js`.

**SUDAH (warna bawaan disamakan).** `warnaKelas` di `aksi_dataset.js` dan
`aksi.js` kini memakai **PALET kontras-tinggi yang sama** dengan gambar sebagai
fallback (bukan rona HSL lama); warna pilihan projek (`D.warna[i]` dari
`aksi.warna`) tetap menang. PALET-nya dijaga sama persis di keempat berkas
(`render.py`, `label.js`, `versi.js`, `aksi_dataset.js`, `aksi.js`) oleh
`test_warna_kelas_sama_...`. Kelas aksi SUDAH bisa diberi warna lewat input
warna native di editor kelas aksi.

**Yang masih OPSIONAL (enhancement, bukan bug).** Projek video memakai sistem
warna TERPISAH (`aksi.warna`, index-based) dari gambar (`kelas_warna`,
name-based):

1. Pemilih warna **popup grid** (padanan `pilihWarnaKelas`/`PALET_GRID`) untuk
   kelas aksi — sekarang pakai input warna native (berfungsi, cuma beda gaya).
2. `PALET` masih disalin di beberapa berkas JS (tak ada bundler); dijaga oleh
   test parity. Satukan kalau nanti ada modul bersama.
3. (Verifikasi) warna identik di grid klip, poster, dan ringkasan versi video.

## Simpan keypoint pose ke YOLO .txt (HIGH, butuh uji pose)

Temuan audit: `/api/simpan` menulis label YOLO lewat `scanner.tulis_yolo`, yang
TIDAK sadar-pose (`dipakai` hanya polygon/rectangle/circle; shape `point`
dibuang). Untuk projek YOLO-pose, mengedit keypoint di kanvas lalu menyimpan
membuat `.txt` hanya memuat bbox-nya; keypoint hilang dari label latih & dari
pembacaan ulang (`_gabung_cadangan` membatalkan merge saat jumlah shape beda).

Projek labelme (.json) TIDAK terpengaruh (save non-YOLO melewati tulis_yolo).

BELUM diperbaiki karena berisiko: menulis format pose `cls cx cy w h (px py v)*K`
butuh mengubah `tulis_yolo` (jalur inti yang juga dipakai simpan KOTAK yang
sedang dipakai produksi basket). Harus dikerjakan dengan uji pose end-to-end
(impor YOLO-pose -> edit keypoint di kanvas -> simpan -> baca ulang -> keypoint
utuh -> ekspor yolo-pose cocok) supaya jalur kotak tak ikut rusak. Scope: writer
pose-aware yang mengelompokkan rect+point per group_id, + guard di api_simpan
(`sess.kpt_sesi()` not None -> jalur pose).

## Sisa temuan audit (LOW / ditunda sengaja)

Sudah diperbaiki sesi ini: ds-fallback sistemik (pulih projek sesudah restart),
paritas izin tombol (/bagi kelola anggota, tombol job), batch tanpa-nama
over-insert, cache thumbnail (no-cache), timeout unggah HP/ingest, format versi
Campuran, jejak pelabel saat navigasi, tag yatim, email penugasan-ulang + dedup
undang, auto-login daftar, warna bawaan kelas aksi, QR HP hanya projek datar.

Diperbaiki sesi re-audit (putaran ke-2, sesudah audit menyeluruh):
- **Open-redirect regresi** di cabang auto-login `daftar_langsung` (auth.py):
  `next` kini dibersihkan sama seperti /login ('//' + skema dibuang).
- **Unggah video-saja: nama batch + tag mendarat di 0 gambar.** `tandai()`
  jalan sesudah ekstraksi, tapi `sess.items` dipindai saat projek masih 0
  frame. `/api/tag/pasang?tanpa_batch` kini `periksa_disk` dulu -> frame hasil
  ekstraksi ikut terhitung.
- **`tungguEkstrak` berputar selamanya untuk video unggah-folder.** Status
  dikunci nama-dasar di server; klien kini menyamakan ke nama-dasar juga.
- **undang-email**: guard `sudah_anggota` disamakan dengan /undang (tak kirim
  ulang email "ditambahkan" ke anggota lama).
- **versi.js**: panel detail tak lagi salah melabeli versi `campuran`/`kerangka`
  sebagai "Poligon (segmentasi)".
- **Editor "Tugaskan ulang" ke orang baru**: dialog board kini menyembunyikan
  jalur undang (owner-only) dari Editor + beri penjelasan, bukan tombol yang
  pasti ditolak. (anotasi.html `data-kelola`, anotasi.js `KELOLA`.)
- **`_sesi.json.tmp` nama unik per tulis** (session.py): catat()-saat-pindah
  membuat tulisan serempak lebih sering; tmp bersama bisa saling menimpa.

Masih TERBUKA (LOW/edge; ditunda supaya tak menambah risiko ke jalur produksi):
- **Batch training abaikan arsitektur per-item** (latih.py ~191, latih.js ~1527)
  -- MED, BUTUH PERSETUJUAN sebelum disentuh (jalur luncur training). Desain:
  arsitektur "sekali untuk SELURUH kiriman". Tapi antrean BOLEH dicampur
  (tiap item simpan `arsitektur` saat ditambah, selektor bisa berubah sesudah),
  lalu server pakai arsitektur top-level saat klik Jalankan -> item YOLO bisa
  jalan sebagai Mask R-CNN diam-diam. Perbaikan aman (client-only): larang
  menambah item berarsitektur beda ke antrean, dan di Jalankan ambil arsitektur
  dari kepala antrean (bukan selektor hidup). Tak disentuh sesi ini sesuai
  permintaan (ada training 521 berjalan; tunggu restu).
- **"Bagi tugas" pada kartu "Tanpa nama unggahan" over-scope** (tugas.py ~131):
  `_belum_ditugaskan` memperlakukan batch `""` sebagai "semua batch", padahal
  Hapus/Masukkan memperlakukannya ketat (hanya tanpa-nama). Query string tak
  bisa membedakan ""=tanpa-nama vs unset; butuh flag `semua` eksplisit seperti
  dataset-siap. Dampak: over-scope/kejutan, bukan korupsi.
- **ffmpeg gagal-keras tak jatuh ke OpenCV** (ekstraksi.py): `_ekstrak_ffmpeg`
  melempar `EkstrakTolak` pada returncode!=0, dan `ekstrak()` me-reraise
  EkstrakTolak (hanya galat tak-terduga yang jatuh ke OpenCV) -> video yang
  ffmpeg tak bisa decode tapi OpenCV bisa malah dihapus. Perbaiki: bedakan
  "tak terbaca" (hapus) dari "ffmpeg error" (coba OpenCV dulu).
- **"Coba lagi sisanya" mati** sesudah ekstraksi video-saja gagal (unggah.js):
  tombol memanggil `kirim(true)` yang menyaring yang sudah di `berhasil` (unggah
  video SUDAH sukses) -> daftar kosong -> "Semua berkas gagal" menyesatkan;
  ekstraksi tak pernah dipicu ulang. Perbaiki: untuk video, "coba lagi" memicu
  ulang `/ekstrak`, bukan unggah ulang.
- **Thumbnail yatim saat warna kelas berubah** (review.py / warna-kelas): ganti
  warna mengubah `fp` thumbnail tapi tak `drop_thumbs_for`; thumbnail lama
  menumpuk di `_bersama/` sampai restart. Disk-only; ETag baru tetap benar.
- **Estimasi versi ≠ build** saat Modify Classes mengubah gambar jadi latar
  (split neg didorong ke train di build, disebar di estimasi). Dampak terbatas:
  output berlabel "Perkiraan, bukan janji". Perbaiki dengan menjalankan
  `saring_untuk_split` juga di `versi_estimasi`.
- **Hitung kelas langkah-1 ≠ langkah-6** (buatversi.py ~1762 vs datasets.py
  ~404): langkah-1 pakai kelas yang PUNYA objek, langkah-6 ikut kelas
  dideklarasi-tapi-kosong. Preview saja; data.yaml build otoritatif.
- **Email training bisa dobel** antar-proses (subprocess + web) pada balapan
  sempit; flag `surel_terkirim` yang dipersist menutup kasus umum.
- **Batch training gagal sebagian** meninggalkan training yang sudah diluncurkan;
  klien tak menghapus antrean -> bisa resubmit ganda.
- **Build versi in-process** tak pulih dari restart (beda dari training yang
  subprocess); `_maju` in-memory -> guard "sedang jalan" tak macet permanen.
- **Popen training tak di-wait** -> zombie PID menumpuk sampai uvicorn keluar
  (tak ganggu logika; `hidup()` baca zombie sebagai mati).
- **/api/versi/buat** (endpoint lama) menyimpang dari wizard, tanpa pemanggil UI;
  pensiunkan atau arahkan ke jalur yang sama.
- **Video gagal-transien** dipertahankan di akar tanpa UI retry.
- **Email di-await inline** -> klik Undang/Bagi menunggu SMTP (sampai timeout 20
  dtk) sebelum toast; tak memblok event loop. Fire-and-forget bila mau.
- **Keypoint pose ke YOLO .txt** (HIGH) -- lihat bagian khusus di atas.
