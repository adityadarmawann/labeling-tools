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

**Yang BELUM disentuh (sengaja, fokus gambar dulu).** Projek video memakai
sistem warna TERPISAH untuk kelas aksi klip:

- `app/static/aksi_dataset.js` `warnaKelas(nama, i)` masih memakai
  `D.warna[i]` (array warna template `aksi.warna`) dengan fallback HSL lama,
  bukan `PALET`/`PALET_GRID`.
- Warna kelas aksi disimpan di `aksi.warna` (`_sah_aksi` di `tugas.py`), bukan
  di `kelas_warna`. Belum ada pemilih warna popup untuk kelas aksi.

**Saat dilanjutkan.** Samakan pengalamannya dengan alur gambar:
1. Pakai `PALET`/`PALET_GRID` yang sama sebagai default + grid pemilih.
2. Sediakan pemilih warna popup untuk kelas aksi (padanan `pilihWarnaKelas`),
   menyimpan ke `aksi.warna` lewat rute yang sudah ada / baru.
3. Pastikan warna identik di: grid klip, poster, dan ringkasan versi video.
4. Cerminkan `PALET` di satu tempat kalau bisa (hindari tiga salinan: sekarang
   ada di `render.py`, `label.js`, `versi.js` — semua HARUS sama persis).
