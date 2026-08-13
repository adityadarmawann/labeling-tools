# Atribusi

Perkakas ini adalah **pengembangan dari [AnyLabeling](https://github.com/vietanhdev/anylabeling)**
oleh Viet-Anh Nguyen, berlisensi GPLv3. Karena itu repo ini juga berlisensi
**GPLv3** — lihat [LICENSE](LICENSE).

## Apa yang diambil dari AnyLabeling

| Bagian | Cara pemakaian |
|---|---|
| Format anotasi `.json` labelme | Skema penuh dipakai apa adanya: `version, flags, shapes[label, text, points, group_id, shape_type, flags], imagePath, imageData, imageHeight, imageWidth`. Field di luar itu ikut dipertahankan supaya berkas bisa dibuka bergantian di web ini dan di AnyLabeling desktop tanpa kehilangan data. |
| Peta tombol | Disamakan dengan `~/.anylabelingrc`: `A`/`D` pindah gambar, `P` poligon, `R` rectangle, `Delete` hapus objek, `Backspace` hapus satu titik, `Ctrl+S` simpan, `Ctrl+Z` urungkan, `Ctrl+F` muat ke jendela, `Ctrl+E` ubah kelas, `Ctrl+D` duplikat, `Ctrl+0`/`Ctrl±` zoom. |
| Alur Auto Labeling | Dua tahap seperti aslinya: prompt menumpuk → pratinjau → `+Point (Q)` / `−Point (E)` untuk memperbaiki → `Finish Object (F)` untuk mengesahkan, dengan `Clear (C)` untuk membatalkan. |
| Tata letak | Toolbar alat di kiri, kanvas di tengah, panel Labels / Objects / Files di kanan. |
| Gaya seleksi bentuk | Nilai dari `~/.anylabelingrc`: `select_line_color`, `vertex_fill_color`, `hvertex_fill_color`, `point_size`. |
| Ikon | Diekstrak dari `anylabeling/resources/resources.py`, dipakai di toolbar. |

## Yang TIDAK diambil

- **Kode kanvas Qt.** `views/labeling/widgets/canvas.py` menggambar ke jendela
  sistem operasi lewat PyQt6 dan tidak punya padanan di browser. Kanvas di sini
  ditulis sebagai `<canvas>` + JavaScript; hanya keputusan geometrinya yang
  dijadikan acuan.
- **Kode auto-labeling.** Segmentasi memakai
  [osam](https://github.com/wkentaro/osam) (MIT) dan ONNX MobileSAM langsung —
  mesin yang sama yang dipakai AnyLabeling, tanpa meng-import kodenya. Kontrak
  ONNX-nya dibaca dari berkas modelnya.

## Bobot model

- **MobileSAM** — Apache-2.0, dari
  [vietanhdev/segment-anything-onnx-models](https://huggingface.co/vietanhdev/segment-anything-onnx-models).
- **SAM / SAM2 / EfficientSAM** — lewat `osam`, mengikuti lisensi masing-masing
  model di hulu.

Bobot model tidak disertakan di repo ini; unduh terpisah ke `models/`.
