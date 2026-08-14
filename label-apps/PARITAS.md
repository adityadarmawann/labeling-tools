# Paritas dengan AnyLabeling

Daftar ini disusun dari **source AnyLabeling 0.4.36**, bukan dari tangkapan
layar atau dugaan. Sumbernya disebut supaya setiap baris bisa diperiksa ulang.

Aturan yang dipegang: kalau AnyLabeling dan aplikasi ini berbeda, **AnyLabeling
yang benar** kecuali ada alasan yang ditulis terang-terangan.

## Sudah sama

| Hal | Acuan | Catatan |
|---|---|---|
| Format `.json` | `label_file.py` | `version, flags, shapes[label, text, points, group_id, shape_type, flags], imagePath, imageData, imageHeight, imageWidth` |
| Field asing dipertahankan | `label_file.py` `other_data` | Diuji: `difficult`, `attributes`, kunci kustom, `imageData` selamat saat disimpan ulang |
| Pipeline MobileSAM | `sam_onnx.py` | Kanvas `(684,1024)` + `warpAffine`, `orig_im_size` = ukuran kanvas, mask di-warp balik, titik pengisi `[0,0]`/`-1` selalu ada, skala prompt = skala warp. **IoU 1.0000, nol piksel berbeda** pada 7 kasus |
| Mask → poligon | `segment_anything.py` | `epsilon = 0.001 * arcLength`, `CHAIN_APPROX_NONE`, kontur >90% gambar dibuang |
| Alur Auto Labeling | `auto_labeling.py` | Prompt menumpuk → pratinjau → `+Point (Q)` / `−Point (E)` / `+Rect` → `Finish Object (F)`, `Clear (C)` |
| Peta tombol | `~/.anylabelingrc` | `A`/`D`, `P`, `R`, `Delete`, `Backspace` (satu titik), `Ctrl+S`, `Ctrl+Z`, `Ctrl+F`, `Ctrl+E`, `Ctrl+D`, `Ctrl+0`, `Ctrl±`, `Ctrl+Shift+P`, panah |
| `epsilon = 10.0` | `canvas.py:46` | Ambang sentuh vertex & sisi, dibagi zoom |
| `MOVE_SPEED = 5.0` | `canvas.py:21` | Langkah panah |
| `close_enough` + `can_close_shape` | `canvas.py:985,552` | Klik dekat titik pertama menutup poligon, butuh >2 titik |
| `add_point_to_edge` | `canvas.py:400` | Sisip titik di sisi, vertex baru langsung aktif |
| `remove_selected_point` | `canvas.py:414` | Backspace |
| `move_by_keyboard` | `canvas.py:1081` | Panah menggeser bentuk, terkurung, **hanya di mode Sunting** seperti `elif self.editing():` di `keyPressEvent` |
| `out_off_pixmap` + `bounded_move_*` | `canvas.py` | Bentuk & vertex terkurung di dalam gambar |
| `set_shape_visible` | `canvas.py` | Centang tampil/sembunyi per objek |
| `set_show_cross_line` | `canvas.py` | Garis bantu silang |
| `set_show_texts` / `set_show_groups` | `canvas.py` | Tulis kelas / grup di atas bentuk |
| `fill_drawing` | `canvas.py` | Isi poligon saat digambar |
| `highlight` / `un_highlight` | `canvas.py` | Sorotan saat kursor mendekat |
| `select_shape_point(multiple_selection_mode)` | `canvas.py:577` | **Ctrl+klik** memilih banyak bentuk; hapus, duplikat, geser, dan ubah kelas berlaku untuk semuanya |
| Menu klik kanan | `canvas.py:479,493` + `label_widget.py:861,1650` | Klik kanan memilih bentuk di bawah kursor lalu membuka menu. Aturan aktif sama: hapus & duplikat butuh ≥1 terpilih, ubah kelas tepat 1 |
| Menu View: 5 dock | `label_widget.py` | Text Editor, Flags, Labels, Objects, Files bisa disembunyikan |
| `auto_save` | `~/.anylabelingrc` | Bawaan aktif, pindah gambar menyimpan sendiri |
| Gaya seleksi | `~/.anylabelingrc` `shape` | `select_line_color`, `vertex_fill_color`, `hvertex_fill_color`, `point_size` |
| Ikon | `resources.py` | 30 dari 61 ikon dipakai |
| Ekspor COCO | `export_formats.py` `export_to_coco` | `categories`, `images`, `annotations`, `licenses` **identik**. `info` sengaja beda (nama aplikasi). `area` poligon ditiru apa adanya walau bukan luas geometris — lihat "Ditiru walau keliru" |
| Ekspor Pascal VOC | `export_formats.py` `export_to_pascal_voc` | XML **identik**, termasuk `toprettyxml(indent="  ")` dan koordinat `int()` yang dipotong |
| Ekspor YOLO | `export_formats.py` `export_to_yolo` | Mode segmentation & detection. **Keluaran byte-identik** dengan FormatExporter: `%.6f`, rectangle jadi 4 sudut (KA, KaA, KaB, KB), poligon diringkas jadi bbox, peta kelas dari label terurut |

## Belum — urutan menurut dampak

| Hal | Acuan | Kenapa penting |
|---|---|---|
| **Ekspor CreateML** | `export_formats.py` `export_to_createml` | Belum dibaca utuh |
| Grup bentuk | `canvas.py` `group_selected_shapes`, `ungroup`, `merge_group_ids`, `gen_new_group_id` | `G` / `U`. Field `group_id` sudah bolak-balik utuh, tinggal aksinya |
| Salin / tempel objek | `label_widget.py` | `Ctrl+C` / `Ctrl+V` |
| Seret kanan = duplikat-dan-pindah | `canvas.py:323` | Klik kanan lalu seret menggandakan bentuk terpilih; menu keduanya berisi "Copy here" / "Move here" |
| `undo_last_point` | `canvas.py` | Urungkan satu titik saat sedang menggambar (sekarang lewat klik kanan) |
| `hide_background_shapes` | `canvas.py` | Sembunyikan objek lain saat menyunting satu objek |
| Bentuk lain | `shape.py` | `circle`, `line`, `linestrip`, `point` |
| `Brightness Contrast` | `brightness_contrast_dialog.py` | Membantu pada gambar gelap |
| `keep_prev` | `~/.anylabelingrc` | Bawa anotasi gambar sebelumnya ke gambar berikutnya |
| `Change Output Dir` | `label_widget.py` | Simpan `.json` ke folder lain |
| Tema Light/Dark, Language | `label_widget.py` | Kenyamanan |

## Sengaja berbeda

| Hal | Di AnyLabeling | Di sini | Alasan |
|---|---|---|---|
| Warna isian bentuk | Hijau seragam (`line_color [0,255,0,128]`) | Per kelas | Dengan banyak kelas jauh lebih terbaca, dan warnanya sama dengan grid QC |
| `Conf 0,50` | Ada di bar Auto Labeling | Tidak ada | Pada prompt titik SAM nilai itu tidak dipakai; kalau dipasang hanya jadi kontrol palsu |
| `Run (i)` | Tombol manual | Tidak ada | SAM jalan otomatis begitu prompt ditambah |
| Panah di luar mode Sunting | Tidak dipakai kanvas | Pindah gambar | Sama seperti panah di grid dan tampilan besar aplikasi ini; `A`/`D` tetap berlaku |
| Kanvas | PyQt6 `QWidget` + `QPainter` | `<canvas>` + JavaScript | Qt tidak bisa digambar di browser; hanya keputusan geometrinya yang di-port |
| Auto-labeling | `services/auto_labeling/` (GPLv3) | `osam` (MIT) + ONNX langsung | Menghindari import kode GPL; mesinnya sama |
| Penilaian "dari mesin server" | Tidak ada — desktop | Alamat soket lokal **dan** tanpa header proxy | Di belakang reverse proxy semua permintaan datang dari 127.0.0.1; tanpa syarat kedua, tombol yang membuka jendela di layar server aktif untuk semua orang |

## Ditiru walau keliru

Satu hal ditiru walau hasilnya bukan yang benar secara matematis, atas
keputusan pemilik proyek: **keluaran harus sama dengan desktop.**

`area` poligon di COCO (`export_to_coco`) menaruh `abs()` di dalam penjumlahan
shoelace, bukan di luar:

```python
area += 0.5 * abs(x1*y2 - x2*y1)      # AnyLabeling
A = |sum(x1*y2 - x2*y1)| / 2          # shoelace baku
```

Akibatnya nilai membengkak makin jauh poligon dari titik-asal — kotak 10x10 di
(100,100) menghasilkan 2100 bukan 100; di (500,300) menghasilkan 8100. Jalur
rectangle di fungsi yang sama memakai `width*height` sehingga bentuk identik
mendapat angka berbeda.

Dampaknya terbatas pada **evaluasi**: `pycocotools` memakai `area` untuk memilah
objek small/medium/large, jadi `mAP_small` / `mAP_medium` / `mAP_large` ikut
bergeser. Pelatihan tidak membaca field ini. Dijaga oleh
`test_coco_area_poligon_mengikuti_anylabeling` supaya tidak berubah tanpa
keputusan.

## Cara memeriksa ulang

```bash
# uji paritas MobileSAM terhadap implementasi AnyLabeling sendiri
.venv/bin/python -m pytest tests/test_sam.py -v

# nilai acuan di test_sam.py berasal dari perbandingan langsung:
#   IoU mask 1.0000, nol piksel berbeda, 7 kasus
```
