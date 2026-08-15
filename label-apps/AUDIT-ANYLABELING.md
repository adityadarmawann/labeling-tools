# Audit AnyLabeling 0.4.36 — Referensi Utama Pengembangan

Dokumen ini adalah hasil audit **seluruh** basis kode AnyLabeling 0.4.36 yang
terpasang di mesin ini, dan menjadi **rujukan utama** untuk pengembangan
`label-apps`. Tujuannya: setiap keputusan desain di aplikasi web kita bisa
ditelusuri ke perilaku asli AnyLabeling, bukan ke tebakan atau tangkapan layar.

**Basis audit**

| Hal | Nilai |
|---|---|
| Paket | `/home/paul/miniconda3/lib/python3.13/site-packages/anylabeling/` |
| Versi | `0.4.36` (`app_info.py:3`) |
| Lisensi | GPLv3 — lihat [`NOTICE.md`](../NOTICE.md) dan [`LICENSE`](../LICENSE) |
| Metode | Enam agen baca-saja, paralel, masing-masing satu lapisan. Tidak ada berkas AnyLabeling yang diubah. |
| Cakupan | `app.py`, `config.py`, `configs/`, `styles/`, `resources/`, `services/auto_labeling/` (13 berkas), `views/` (seluruhnya, termasuk `label_widget.py` 120 KB dan `canvas.py` 49 KB) |

Semua klaim di bawah memakai rujukan `berkas:baris` relatif terhadap direktori
paket. Bagian yang tidak bisa dipastikan dari kode saja ditandai **TIDAK
PASTI** — sengaja tidak dispekulasikan.

> **Cara memakai dokumen ini.** Bagian A–F adalah deskripsi AnyLabeling apa
> adanya. Bagian G adalah cacat AnyLabeling yang **tidak** boleh kita tiru.
> Bagian H adalah daftar penyimpangan aplikasi kita — bagian itulah yang
> menentukan pekerjaan berikutnya.

---

## Ringkasan untuk yang tidak punya waktu

Enam hal terpenting dari audit ini:

1. **Rasa "ringan" AnyLabeling saat menyunting berasal dari tiga jalur pintas
   mouse**, bukan dari menu: klik di sisi poligon langsung menyisipkan titik
   (`canvas.py:464-465`), Shift+klik pada titik langsung menghapusnya
   (`canvas.py:466-471`), dan klik ulang pada objek terpilih membatalkan
   pilihannya (`canvas.py:503-512`). Ketiganya belum ada di aplikasi kita.
2. **Hasil SAM bersifat sementara sampai ditekan `F`.** Bentuk berlabel
   `AUTOLABEL_OBJECT` dihapus otomatis saat pindah gambar, ganti model, atau
   tekan Clear (`label_widget.py:2110`, `:2775-2810`). Ini bukan bug, ini
   desainnya.
3. **AnyLabeling tidak membuat `data.yaml`** dan pembagian train/val/test-nya
   memakai `random.shuffle` tanpa seed serta mengabaikan `test_ratio`
   (`export_worker.py:147-163`). Ekspor kita sengaja lebih baik di sini.
4. **Rumus luas poligon COCO-nya salah** (`export_formats.py:338-343`), dan
   `category_id` tidak konsisten antar split (`export_worker.py:355-433`).
   Bug pertama **sudah tidak kita tiru lagi** (lihat H.6); yang kedua masih
   kita tanggung.
5. **Pipeline MobileSAM sudah diverifikasi bit-exact** dengan AnyLabeling
   (IoU 1.0000, selisih 0 piksel). Lihat Bagian E dan [`PARITAS.md`](PARITAS.md).
6. **Bagian H disusun dari pengecekan silang, bukan ingatan** — cara Bagian H
   versi pertama disusun manual pernah membuat dua temuan yang sudah tercatat
   di bagian deskriptif tidak pernah dibandingkan dengan kode kita. Lihat H.1.

---

# Bagian A — Arsitektur dan Bootstrap

## A.1 Titik masuk

Hanya satu: console script `anylabeling = anylabeling.app:main`
(`entry_points.txt`). **`__main__.py` tidak ada** — `python -m anylabeling`
tidak jalan. Blok `if __name__ == "__main__"` di `app.py:226-227` hanya untuk
PyInstaller.

## A.2 Urutan inisialisasi (`app.py`)

| # | Baris | Langkah | Catatan penting |
|---|---|---|---|
| 1 | `:6-8` | `MKL/NUMEXPR/OMP_NUM_THREADS = "1"` | **Sebelum import apa pun.** Alasan: bus error di Mac M1 (`:3-5`). Di server web ini justru harus dibalik. |
| 2 | `:21` | `import resources` | Efek samping: `qInitResources()` dijalankan di baris terakhir `resources.py` |
| 3 | `:122-145` | `parse_args`, normalisasi `flags`/`labels`/`label_flags` | |
| 4 | `:154` | `get_config()` | Merge 3 lapis: default YAML → `~/.anylabelingrc` → argumen CLI |
| 5 | `:172-174` | Muat `QTranslator` | **Sebelum `QApplication` dibuat** |
| 6 | `:182-183` | `QApplication(sys.argv)` + `processEvents()` | |
| 7 | `:186-196` | Resolusi tema → env var `DARK_MODE` → `AppTheme.apply_theme(app)` | Env var dipakai sebagai kanal state global (anti-pattern) |
| 8 | `:207-222` | `MainWindow` → `showMaximized()` → `app.exec()` | |

Rantai widget: `MainWindow` (`views/mainwindow.py:9`) → `LabelingWrapper`
(`label_wrapper.py:8`) → `LabelingWidget` (`label_widget.py:52`).

Dua keanehan struktural yang nyata:

- `LabelingWidget` **mewarisi `LabelDialog`** dan memanggil
  `super(LabelDialog, self).__init__()` (`label_widget.py:52`, `:103`),
  sehingga konstruktor `LabelDialog` dilewati dan objeknya efektif `QDialog`
  yang dipakai sebagai widget biasa. Dialog label sesungguhnya adalah instans
  terpisah `self.label_dialog` (`:127-135`).
- Ada **dua `QMainWindow`**: yang terluar memegang menubar + status bar, yang
  di dalam (`label_widget.py:116-124`) hanya untuk area dock.

## A.3 Struktur paket

| Kelompok | Berkas | Ukuran | Peran |
|---|---|---|---|
| Akar | `app.py` | 7,2 KB | bootstrap + CLI |
| | `config.py` | 3,2 KB | muat/gabung/validasi/simpan YAML |
| | `utils.py` | 379 B | `GenericWorker` (pembungkus thread Qt) |
| `configs/` | `anylabeling_config.yaml` | 2,4 KB | 37 kunci konfigurasi bawaan |
| | `auto_labeling/models.yaml` | 5,2 KB | katalog 22 model |
| `resources/` | `resources.py` | **1,73 MiB** | 61 aset gambar + 3 `.qm`, ter-compile `pyrcc` |
| `styles/` | `theme.py` | 9,8 KB | satu-satunya sumber tema (palet + QSS f-string) |
| `services/auto_labeling/` | 13 berkas | ~110 KB | model, registry, cache, 4 backend SAM, 2 YOLO |
| `views/labeling/` | `label_widget.py` | **120 KB** | God object: menu, aksi, dock, state, I/O, tema, ekspor |
| | `canvas.py` | **49 KB** | inti anotasi |
| | `shape.py` | 11,2 KB | model data + rendering bentuk |
| | `label_file.py` | 5,9 KB | baca/tulis JSON |
| `views/labeling/widgets/` | 15 berkas | ~90 KB | dialog dan panel |

## A.4 Tema

Tidak ada berkas `.qss`/`.css`. Semuanya f-string Python di `theme.py:129-327`.
Deteksi gelap: env `DARK_MODE` dulu (`:81-82`), lalu `darkdetect.isDark()`
(`:84`). Penerapan: `app.setStyle("Fusion")` + 15 `ColorRole` `QPalette` +
stylesheet (`:93-127`).

Warna primer: `PRIMARY_LIGHT #2196F3`, `PRIMARY_DARK #1976D2`.
`ACCENT_LIGHT #FFA000` dan `ACCENT_DARK #FF8F00` **didefinisikan tapi tidak
pernah dipakai** (`theme.py:16-17`).

Palet lengkap (24 kunci), yang paling relevan untuk kita:

| Kunci | Light | Dark |
|---|---|---|
| `window` | `#FFFFFF` | `#212121` |
| `base` | `#F5F5F5` | `#303030` |
| `text` | `#212121` | `#EEEEEE` |
| `highlight` | `#2196F3` | `#1976D2` |
| `border` | `#BDBDBD` | `#616161` |
| `toolbar_bg` | `#FFFFFF` | `#333333` |
| `success` / `warning` / `error` | `#4CAF50` / `#FFC107` / `#F44336` | sama |
| `selection` | `#BBDEFB` | `#0D47A1` |

**Warna anotasi terpisah dari tema** — berasal dari
`anylabeling_config.yaml:23-37`, bukan dari `AppTheme`:
`default_shape_color [0,255,0]`, `shape_color: auto`,
`line_color [0,255,0,128]`, `fill_color [220,220,220,150]`,
`vertex_fill_color [0,255,0,255]`, `select_line_color [255,255,255,255]`,
`select_fill_color [0,255,0,155]`, `hvertex_fill_color [255,255,255,255]`,
`point_size: 8`.

**Ganti tema butuh restart** (`label_widget.py:3057-3074`) — nilai warna hanya
dibaca sekali saat konstruksi widget.

## A.5 Bahasa

Hanya **3 bahasa**, semuanya blob di dalam `resources.py`, tanpa berkas `.ts`
maupun `.qm` di disk:

| Berkas | Ukuran | Catatan |
|---|---|---|
| `en_US.qm` | **16 B** | praktis kosong — Inggris memakai string sumber |
| `vi_VN.qm` | 26.818 B | |
| `zh_CN.qm` | 20.068 B | |

Cakupan i18n ±264 string, terbanyak `label_widget.py` (164) dan
`export_dialog.py` (52). Perhatikan: pesan galat dari lapisan *service* juga
di-`tr()` (`model_manager.py` 18×, `model.py` 3×, `yolov5/8.py` 2× masing-masing)
— pelanggaran pemisahan lapisan. **Ganti bahasa menutup aplikasi secara paksa**
(`label_widget.py:1272` → `self.parent.parent.close()`).

## A.6 Aset

61 berkas di `:/images/images/` (59 `.png` + `icon.icns` + `icon.ico`), diakses
lewat `QtGui.QIcon(f":/images/images/{icon}.png")` (`utils/qt.py:10-11`).

**Dua ikon dirujuk kode tapi tidak ada di resources**: `refresh`
(`label_widget.py:668-675`, aksi Reset Views) dan `tools`
(`label_widget.py:897-904`). Qt tidak melempar error — `QIcon` hanya jadi kosong.

## A.7 Logger

Nama `"AnyLabeling"` (`logger.py:57`). Teknik tidak lazim di `logger.py:58`:
`logger.__class__ = ColoredLogger` — menimpa kelas instance **setelah** dibuat,
sehingga `ColoredLogger.__init__` tidak pernah jalan dan `StreamHandler`
(`:51-54`) serta `ColoredFormatter` (`:49`) **tidak pernah terpasang**. Output
kemungkinan jatuh ke `logging.lastResort`. Level hanya dari CLI
`--logger-level`; kunci `logger_level` di YAML (`:14`) **diabaikan**.

Hanya 33 pemanggilan log di seluruh paket ~350 KB — observabilitas nyaris nol.

## A.8 Dependensi

`Requires-Python >=3.11`. Yang wajib dan pemakaiannya:

| Paket | Dipakai untuk |
|---|---|
| `numpy`, `opencv-python-headless` | geometri, mask, pra-proses inferensi |
| `Pillow` | dekode gambar, EXIF, rasterisasi poligon, brightness/contrast |
| `onnx` | inspeksi graf model (deteksi varian SAM) |
| `onnxruntime` | eksekusi inferensi |
| `PyQt6` | seluruh UI — **marker `platform_system != "Darwin"`** (anomali) |
| `qimage2ndarray` | `QImage` ↔ ndarray |
| `imgviz` | colormap label, `lblsave` |
| `darkdetect` | deteksi tema OS |
| `huggingface_hub` | `snapshot_download` untuk model CoreML |
| `natsort` | urut nama berkas natural (1 pemakaian) |
| `termcolor` | warna log (1 pemakaian) |
| `PyYAML` | konfigurasi |
| `osam >=0.4.0` | **hanya** `from osam._models.yoloworld.clip import tokenize` di `sam3_onnx.py:240` — API privat, rapuh |

---

# Bagian B — Lapisan Antarmuka

## B.1 Menu (urutan persis)

**File → Edit → View → Language → Theme → Tools → Help**
(`label_widget.py:917-927`; urutan menubar = urutan literal dict karena
`self.menu()` langsung `menuBar().addMenu()` di `:1299`).

### File (`:941-958`)
`&Open` · `&Next Image` · `&Prev Image` · `&Open Dir` · submenu `Open &Recent` ·
`&Save` · `&Save As` · `Save &Automatically` ☑ · `&Change Output Dir` ·
`Save With Image Data` ☑ · `&Close` · `&Delete File` · separator (di posisi
**terakhir**, tanpa butir sesudahnya).

### Edit (`:1325-1343`, dibangun ulang oleh `populate_mode_actions()`)
6 aksi Create (Polygons, Rectangle, Circle, Line, Point, LineStrip) ·
`Edit Object` · `&Edit Label` · `Duplicate Polygons` · `Delete` · — ·
`Undo` · `Undo last point` · — · `Remove Selected Point` · — ·
`Keep Previous Annotation` ☑ · `Auto Use Last Label` ☑

### View (`:983-1013`) — 26 butir
5 toggle dock (Text Editor `Ctrl+T`, Flags, Labels, Objects, Files) ·
`&Reset Views` · — · `Fill Drawing Polygon` ☑ · — · `Hide/Show Polygons` · — ·
`Zoom In/Out`, `Original size`, `Keep Previous Scale` ☑ · — ·
`Fit Window` ☑, `Fit Width` ☑ · — · `Brightness Contrast` ·
`Show Cross Line` ☑ · `Show Texts` ☑ · `Show Groups` ☑ ·
`Group Selected Shapes` · `Ungroup Selected Shapes`

### Language (`:970-973`) / Theme (`:974-981`) / Tools (`:966-969`) / Help (`:959-965`)
Language: `English` · `Tiếng Việt` · `中文`.
Theme: `System` · `Light` · `Dark` — **tetapi aksinya ditambahkan 3× ke menu**
(`:930-933`, `:936-939`, `:974-981`) sehingga isinya jadi **9 butir duplikat**;
karena `QActionGroup` eksklusif, menandai satu entri menandai ketiga kembarannya.
Tools: hanya `Export Annotations`. Help: `&Documentation` · `&Contact me`.

### Menu konteks kanvas
- **`menus[0]`** (`:861-877`) — 15 aksi: 6 Create · `Edit Object` ·
  `&Edit Label` · `Duplicate` · `Copy Object` · `Paste Object` · `Delete` ·
  `Undo` · `Undo last point` · `Remove Selected Point`
- **`menus[1]`** (`:1019-1025`) — hanya 2 aksi: **`&Copy here`** dan
  **`&Move here`**. Muncul setelah seret klik-kanan (lihat C.2.3).
- Menu konteks **panel Objects** (`:798-801`) — hanya `&Edit Label` + `Delete`.

## B.2 Pintasan bawaan (`anylabeling_config.yaml:77-117`)

| Aksi | Pintasan | Aksi | Pintasan |
|---|---|---|---|
| open | `Ctrl+O` | create_polygon | `P`, `Ctrl+N` |
| open_dir | `Ctrl+U` | create_rectangle | `R`, `Ctrl+R` |
| save | `Ctrl+S` | create_circle/line/point/linestrip | `null` |
| save_as | `Ctrl+Shift+S` | edit_polygon | `Ctrl+J` |
| save_to | `null` | delete_polygon | `Delete` |
| close | `Ctrl+W` | duplicate_polygon | `Ctrl+D` |
| quit | `Ctrl+Q` | copy / paste | `Ctrl+C` / `Ctrl+V` |
| delete_file | `Ctrl+Delete` | undo | `Ctrl+Z` |
| open_next | `D`, `Ctrl+Shift+D` | undo_last_point | `Ctrl+Z` ⚠ sama |
| open_prev | `A`, `Ctrl+Shift+A` | add_point_to_edge | `Ctrl+Shift+P` |
| zoom_in | `Ctrl++`, `Ctrl+=` | edit_label | `Ctrl+E` |
| zoom_out | `Ctrl+-` | remove_selected_point | `Backspace` |
| zoom_to_original | `Ctrl+0` | group / ungroup | `G` / `U` |
| fit_window | `Ctrl+F` | toggle_keep_prev_mode | `Ctrl+P` |
| fit_width | `Ctrl+Shift+F` | toggle_auto_use_last_label | `Ctrl+Y` |
| auto_label | **`Ctrt+A`** ⚠ salah tulis | | |

Tiga catatan yang penting untuk kita:

- `undo` dan `undo_last_point` **berbagi `Ctrl+Z`**; pemisahannya lewat
  enable/disable timbal-balik di `toggle_drawing_sensitive` (`:1433-1441`):
  saat menggambar `undo_last_point` aktif dan `undo` mati, dan sebaliknya.
- `add_point_to_edge: Ctrl+Shift+P` **tidak pernah dipasang ke `QAction`
  mana pun**. Fungsinya dijangkau lewat klik kiri biasa di sisi poligon
  (`canvas.py:465`). Jadi pintasan itu hanya sisa konfigurasi.
- `quit: Ctrl+Q` juga tidak punya `QAction`.

## B.3 Aturan enable/disable aksi

Ini yang membuat toolbar AnyLabeling tidak pernah menawarkan aksi mustahil.

| Pemicu | Fungsi | Efek |
|---|---|---|
| Gambar dibuka/ditutup | `toggle_actions(value)` `:1382-1387` | Mengatur `zoom_actions` + `on_load_active` (15 aksi) serentak |
| Ada perubahan | `set_dirty()` `:1345-1361` | `undo` mengikuti `canvas.is_shape_restorable`; **kalau autosave aktif, langsung simpan dan `return`** |
| Bersih | `set_clean()` `:1363-1380` | `save` mati; semua Create dihidupkan; `delete_file` mengikuti `has_label_file()` |
| Sedang menggambar | `toggle_drawing_sensitive(drawing)` `:1433-1441` | `edit_mode`/`undo`/`delete` mati, `undo_last_point` hidup — dan sebaliknya |
| Mode gambar aktif | `toggle_draw_mode(...)` `:1443-1511` | Aksi Create yang **sedang aktif dimatikan** sebagai penanda mode |
| Jumlah seleksi | `shape_selection_changed(...)` `:1637-1654` | `delete`/`duplicate`/`copy` aktif bila ≥1; **`&Edit Label` hanya bila tepat 1** |
| Clipboard | `:1883-1885` | `paste` aktif bila ada salinan (sekali aktif, tidak pernah mati lagi) |
| Ada bentuk | `add_label()` `:1682-1683` | `save_as`, `hide_all`, `show_all` |
| Vertex ter-hover | sinyal `vertex_selected` `:894` | `remove_point` langsung mengikuti |

## B.4 Dock

| Dock | Judul | Isi | Area | Features |
|---|---|---|---|---|
| `shape_text_dock` | Text Editor | `QLabel` + `QPlainTextEdit` | Right | Closable\|Floatable\|Movable |
| `flag_dock` | Flags | `QListWidget` checkable | Right | idem — **disembunyikan bila `config["flags"]` kosong** (`:202-205`), dan bawaannya `null` |
| `shape_dock` | Objects | `LabelListWidget` | Right | idem |
| `label_dock` | Labels | `UniqueLabelQListWidget` | Right | idem |
| `file_dock` | Files | `QLineEdit` + `QListWidget` | Right | idem |
| `tools_dock` | *(kosong)* | `ToolBar` vertikal | **Left** | Movable\|Floatable — **tidak Closable** |

`tools_dock` lebarnya dikunci 40 px (`:1079-1080`), berubah jadi tinggi 65 px
bila dipindah ke atas/bawah (`:3221-3222`), dan orientasi toolbar mengikuti area
dock (`:3209-3245`).

Persistensi layout: `saveState()` → Base64 → `config["ui"]["dock_state"]`
(`:3079-3117`), dengan throttle 2 detik dan timer autosave **60 detik**
(`:1257-1260`). `reset_dock_layout()` (`:2966-3055`) menutup semua dock,
menambahkannya ulang, `resizeDocks([...], [40,300,300,300,300,300])`, lalu
menghapus `dock_state`.

**Kunci konfigurasi `flag_dock`/`label_dock`/`shape_dock`/`file_dock` dengan
sub-kunci `show`/`closable`/`movable`/`floatable`
(`anylabeling_config.yaml:40-59`) tidak dibaca kode sama sekali** — fitur dock
di-hardcode di `:140-144`. Hanya `config["flags"]` yang berpengaruh.

## B.5 Toolbar

`self.tools = ToolBar("Tools")` (`:1072`), vertikal, `ToolButtonIconOnly`, ikon
24×24. Urutan tombol (`self.actions.tool`, `:1028-1049`):

`Open Dir` · `Next Image` · `Prev Image` · `Save` · `Delete File` · **—** ·
`Create Polygons` · `Create Rectangle` · `Create Circle` · `Create Line` ·
`Create Point` · `Create LineStrip` · `Edit Object` · `Delete` · `Undo` · **—** ·
`zoom` (spinbox) · `Fit Width` · `Auto Labeling`

Catat: `open_` **dikomentari** di `:1029` — jadi tombol Open tidak ada di
toolbar, hanya Open Dir.

**`ToolBar.add_action()` tidak pernah dipanggil** (`toolbar.py:35-51`).
Pengisian memakai `utils.add_actions()` → `widget.addAction()`, sehingga
pembungkusan `QToolButton` dan perataan tengah yang ditulis di kelas itu tidak
pernah aktif. Metode `LabelingWidget.toolbar()` (`:1308-1317`) dan tuple
`actions.file_menu_actions` (`:844`) juga kode mati.

## B.6 Panel Objects (`LabelListWidget`)

`QListView` + `QStandardItemModel` + `HTMLDelegate` (`label_list_widget.py`).

| Aspek | Detail |
|---|---|
| Rendering | Teks item dirender sebagai **HTML** via `QTextDocument` (`:14-61`) |
| Item | `LabelListWidgetItem` checkable, **default Checked**, tidak editable, align `AlignBottom` (`:78-81`); objek `Shape` di `UserRole` |
| Seleksi | `ExtendedSelection` (`:121-123`) |
| Drag-drop | `InternalMove` + `MoveAction` (`:124-125`) → **urutan objek bisa diubah dengan menggeser** |
| Isi teks tanpa `group_id` | `'<label> <font color="#rrggbb">●</font>'` dengan `html.escape` (`label_widget.py:1686-1690`) |
| Isi teks dengan `group_id` | `f"{shape.label} ({shape.group_id})"` — **tanpa titik warna dan tanpa `html.escape`** (`:1610-1611`, `:2911-2912`) |

Interaksi: ubah seleksi → `canvas.select_shapes` · klik dua kali →
`edit_label` · ubah centang → `canvas.set_shape_visible` (tidak menandai dirty,
karena visibilitas memang tidak disimpan) · drag-drop → `label_order_changed`
+ `set_dirty` · klik kanan → menu Edit/Delete.

Warna dari `_get_rgb_by_label()` (`:1711-1741`): mode `auto` memakai
`LABEL_COLORMAP` imgviz dengan indeks = baris di unique list + 1 +
`shift_auto_shape_color`; `LABEL_COLORMAP[1]` diganti hijau `[0,180,33]`
(`:45-49`). Label auto-labeling punya warna tetap: **OBJECT cyan `(0,255,255)`,
ADD hijau `(0,255,0)`, REMOVE merah `(255,0,0)`** (`:1714-1724`).

## B.7 Panel Labels (`UniqueLabelQListWidget`)

`EscapableQListWidget` → **Esc membatalkan seleksi** (`:7-10`); klik area kosong
juga (`:11-14`). Item menyimpan nama label di `UserRole`, ditampilkan lewat
`QLabel` sebagai item widget dengan HTML titik warna (`:29-41`).

**Tidak ada checkbox, tidak ada double-click handler, tidak ada menu konteks.**
Tooltipnya: *"Select label to start annotating for it. Press 'Esc' to
deselect."* (`label_widget.py:227-229`).

Fungsinya: label yang terpilih menjadi **label default** untuk bentuk baru
(`:1914-1917`), dan bila `display_label_popup` False, dialog label dilewati
seluruhnya (`:1926-1930`).

## B.8 Dialog label (`label_dialog.py`)

| Elemen | Detail |
|---|---|
| `edit` | `LabelQLineEdit` — meneruskan Up/Down ke daftar riwayat (`:22-26`); validator regex `^[^ \t].+` → **tidak boleh mulai spasi, minimal 2 karakter** (`utils/qt.py:66-69`) |
| `edit_group_id` | validator `\d*` (hanya digit); stretch 6:2 terhadap `edit` |
| `button_box` | Ok (ikon `done`) / Cancel (ikon `undo`) |
| `label_list` | daftar riwayat; **klik dua kali = tekan OK** (`:103`, `:158-159`) |
| Flags | `QCheckBox` per flag, dibangun ulang setiap teks berubah |
| Auto-complete | `startswith` → InlineCompletion; `contains` → PopupCompletion + MatchContains; lain → `ValueError` (`:115-129`) |

`validate()` hanya `accept()` bila teks setelah `strip()` tidak kosong
(`:149-156`) — **kalau kosong, dialog diam saja tanpa pesan**.

`pop_up()` (`:215-248`) memilih teks penuh (`setSelection(0, len(text))`),
memindahkan dialog ke posisi kursor bila `move=True`, dan mengembalikan
`(text, flags, group_id)`.

## B.9 Zoom

`ZoomWidget(QSpinBox)`: rentang **1–1000**, sufiks `%`, tanpa tombol naik/turun.

Tiga mode: `FIT_WINDOW, FIT_WIDTH, MANUAL_ZOOM = 0, 1, 2` (`:55`), awal
`FIT_WINDOW`.

| Aksi | Perilaku |
|---|---|
| `add_zoom(1.1)` / `add_zoom(0.9)` | `math.ceil` bila naik, `math.floor` bila turun |
| Ctrl+wheel | `zoom_request` → faktor 1.1/0.9 + **kompensasi scroll agar titik di bawah kursor tetap** (`:1989-2010`) |
| wheel biasa | `scroll_request` dengan `units = -delta * 0.1` ("natural scroll") |
| `scale_fit_window()` | epsilon `e=2.0`, bandingkan rasio aspek widget vs pixmap |
| `scale_fit_width()` | `(central_width - 2.0) / pixmap.width()` |

Zoom, scroll, dan brightness/contrast **disimpan per nama berkas** di dict
in-memory (`:1207-1212`), dipulihkan di `load_file` (`:2199-2235`).

## B.10 Status bar

Milik `MainWindow` terluar; `LabelingWidget.statusBar()` mendelegasikan ke
`self.parent.parent.statusBar()` (`:1319-1320`). Helper `status(message,
delay=5000)`.

| Pesan | Kapan | Durasi |
|---|---|---|
| `Loading %s...` | awal `load_file` | 5 s |
| `Error reading %s` | JSON/gambar gagal | 5 s |
| `Loaded %s` | sukses | 5 s |
| `Change Annotations Dir . ...` | setelah ganti output dir | **permanen** (tanpa timeout) |
| `Dock layout reset to default` | reset dock | 5 s |

Setiap `QAction` yang punya `tip` memakai teks itu **sekaligus** sebagai
`toolTip` dan `statusTip` (`utils/qt.py:44-46`).

Judul jendela: `AnyLabeling - <file>*` saat dirty (`:1358-1361`) — **tanda
bintang di akhir**.

## B.11 Brightness/Contrast

`BrightnessContrastDialog` modal, 2 slider `range(0,150)` nilai awal `50`.
Faktor efektif = `value / 50.0` → **0.0–3.0, netral di 1.0**. Selalu diterapkan
ke citra **asli** (`self.img`) sehingga tidak akumulatif (`:36-38`). Callback →
`canvas.load_pixmap(..., clear_shapes=False)` supaya bentuk tidak hilang.

Menarik: di `load_file` dialog ini **dibuat tanpa ditampilkan** hanya untuk
menerapkan nilai tersimpan (`:2213-2235`).

---

# Bagian C — Kanvas dan Bentuk (paling penting)

Ini lapisan yang menentukan rasa memakai aplikasi. Bagian C.2 adalah inti dari
keluhan "belum friendly buat hapus objek".

## C.1 Konstanta

| Konstanta | Nilai | Rujukan | Pengaruh |
|---|---|---|---|
| `epsilon` | `10.0` | `canvas.py:46` | Ambang deteksi vertex/edge dan snapping. **Selalu dibagi `self.scale`** → makin zoom-in makin presisi |
| `MOVE_SPEED` | `5.0` | `canvas.py:21` | Geser per tekan panah di mode EDIT. **Satuannya koordinat GAMBAR**, bukan piksel layar |
| `num_backups` | `10` | `canvas.py:52` | Kedalaman undo — tapi lihat C.6 |
| `double_click` | `"close"` | `canvas.py:47` | Hanya `None` atau `"close"`, lain → `ValueError` |
| `point_size` | `4` (kelas `Shape`) | `shape.py:43` | Diameter vertex, dibagi `scale` |
| `_update_interval` | `0.016` | `canvas.py:96-97` | Throttle `update()` ~60 Hz |

Kursor: `ArrowCursor` default · `PointingHandCursor` saat hover vertex, hover
sisi, dan saat snapping · `CrossCursor` saat menggambar · `ClosedHandCursor`
saat menggeser · `OpenHandCursor` saat hover di dalam bentuk.

Highlight vertex: `NEAR_VERTEX` → 4× bulat, `MOVE_VERTEX` → 1,5× kotak
(`shape.py:67-70`).

## C.2 Model peristiwa mouse

### C.2.1 `mousePressEvent` — klik kiri saat menggambar (`canvas.py:432-462`)

| Mode | Perilaku | Jumlah klik |
|---|---|---|
| `point` | `finalise()` langsung | **1** |
| `rectangle` / `circle` / `line` | klik ke-2 → `finalise()` | **2** |
| `polygon` | tambah titik; `finalise()` bila `is_closed()` | sampai tertutup |
| `linestrip` | tambah titik; **`finalise()` hanya bila Ctrl ditekan** | Ctrl+klik mengakhiri |

Klik pertama **di luar pixmap tidak melakukan apa pun** (`:450`).

### C.2.2 `mousePressEvent` — klik kiri saat menyunting (`canvas.py:463-478`)

Urutannya penting, dan **tiga baris ini yang membuat penyuntingan terasa ringan**:

```
1. selected_edge()?                      -> add_point_to_edge()        :464-465
2. selected_vertex() dan modifier == Shift -> remove_selected_point()  :466-471
3. group_mode = (modifiers == Control)                                 :473-475
4. select_shape_point(pos, multiple_selection_mode=group_mode)         :476
```

Artinya:

- **Klik kiri biasa di sisi poligon = titik baru tersisip.** Begitu kursor
  mendekat, `h_edge` terisi (hover, `:369-380`) dan klik langsung menyisipkan.
  Tidak ada pintasan yang perlu diingat.
- **Shift+klik pada titik = titik itu terhapus.** Satu tindakan, satu gerakan.
- **Ctrl+klik = seleksi ganda.**
- Langkah 4 dijalankan **selalu** (bukan `elif`), jadi penambahan/penghapusan
  titik langsung diikuti proses seleksi.

Perbandingan modifier bersifat **eksak** (`==`), jadi Ctrl+Shift+klik tidak
memicu hapus titik maupun seleksi ganda.

### C.2.3 Klik kanan — seret duplikat-dan-pindah

Ini fitur yang paling mudah terlewat.

| Tahap | Baris | Perilaku |
|---|---|---|
| Tekan | `:479-486` | Hanya diproses bila `editing()`. Selalu menyetel `prev_point = pos` |
| Seret | `:328-330` | Bila ada seleksi tapi belum ada salinan → `[s.copy() for s in selected_shapes]` |
| Seret lanjut | `:324-327` | Kursor `CURSOR_MOVE`, `bounded_move_shapes(selected_shapes_copy, pos)` |
| Gambar | `:810-812` | Salinan digambar sebagai "bayangan" |
| Lepas | `:493-502` | `menu = self.menus[len(selected_shapes_copy) > 0]` → **`menus[1]`** = `&Copy here` / `&Move here` |
| Batal | `:499-502` | Bila menu tidak mengembalikan aksi → salinan dibatalkan |

`&Copy here` → `canvas.end_move(copy=True)` (salinan ditambahkan ke `shapes`);
`&Move here` → `end_move(copy=False)` (hanya `points` dipindahkan ke bentuk asli).

Catat asimetri: blok tombol kanan di `mouseReleaseEvent` **tidak memeriksa
mode**, jadi menu konteks juga terbuka di mode CREATE, padahal
`mousePressEvent` membatasi klik kanan ke `editing()`.

### C.2.4 `mouseReleaseEvent` klik kiri — klik ulang membatalkan pilihan

`canvas.py:503-512`: bila `h_hape` ada, `h_shape_is_selected` benar, dan
`not moving_shape` → emit `selection_changed` dengan daftar seleksi **tanpa**
bentuk itu. Efeknya: **klik lagi pada objek yang sudah terpilih = pilihannya
dibatalkan**. `h_shape_is_selected` disetel di `select_shape_point` (`:591-593`).

### C.2.5 Hover (`canvas.py:346-398`)

Iterasi `reversed(shapes yang visible)` — bentuk paling atas menang. Prioritas
per bentuk **vertex → sisi → interior**:

| Prioritas | Uji | Kursor | Tooltip |
|---|---|---|---|
| 1 | `nearest_vertex(pos, epsilon/scale)` | `CURSOR_POINT` | "Click & drag to move point" |
| 2 | `nearest_edge(...)` **dan** `can_add_point()` | `CURSOR_POINT` | "Click to create point" |
| 3 | `contains_point(pos)` | `CURSOR_GRAB` | "Click & drag to move shape '<label>'" |
| — | tidak ada | — | "Image" |

`can_add_point()` hanya `True` untuk `polygon` dan `linestrip`
(`shape.py:116-118`) — jadi sisi rectangle tidak bisa disisipi titik.

Terakhir selalu `vertex_selected.emit(h_vertex is not None)` (`:398`) →
mengaktifkan aksi Remove Point secara langsung.

### C.2.6 Klik ganda dan wheel

Klik ganda menutup poligon bila: `double_click == "close"`,
`can_close_shape()` (`drawing()` dan `len(current) > 2`), **dan**
`len(current) > 3` (`:563-567`). Aksinya `pop_point()` lalu `finalise()` —
titik terakhir dibuang karena `mousePressEvent` sudah menambah satu titik
ekstra sebelum event ini.

Wheel: **tepat** `Ctrl` → zoom; lainnya (termasuk Ctrl+Shift) → scroll.

## C.3 Keyboard di kanvas

| Mode | Tombol | Efek |
|---|---|---|
| CREATE | `Esc` | Buang bentuk yang sedang digambar |
| CREATE | `Enter` | `finalise()` bila `can_close_shape()` |
| CREATE | **`Alt` (tepat)** | **`snapping = False`** — mematikan tarik-magnet ke titik awal |
| CREATE | lepas modifier | `snapping = True` |
| EDIT | `↑↓←→` | `move_by_keyboard(±5)` — **hanya di mode EDIT** |

Tidak ada `super().keyPressEvent(ev)`; tombol lain ditangani sebagai `QAction`
di `label_widget.py`.

Geser-dengan-keyboard hanya tercatat ke riwayat undo **saat tombol dilepas**,
dan hanya berdasarkan bentuk pertama dalam seleksi (`:1119-1126`).

## C.4 Snapping

Aktif hanya bila **semua** terpenuhi (`canvas.py:292-302`):
`self.snapping` benar · `len(current) > 1` · **`create_mode == "polygon"`** ·
`close_enough(pos, current[0])` yaitu `distance < epsilon / scale`.

Efeknya: `pos` dipaksa = titik awal, kursor jadi `CURSOR_POINT`, dan **vertex 0
di-highlight `NEAR_VERTEX`** (4× bulat) sebagai umpan balik visual.

## C.5 Seleksi dan penyuntingan

| Fungsi | Perilaku |
|---|---|
| `select_shape_point` `:577-596` | Bila ada vertex ter-hover → hanya highlight, **tidak** mengubah seleksi. Bila tidak → iterasi `reversed(shapes)` cari yang visible dan `contains_point` |
| `calculate_offsets` `:598-619` | Simpan dua vektor offset (kiri-atas, kanan-bawah relatif kursor) untuk pembatasan geser |
| `bounded_move_shapes` `:629-653` | Bila `pos` di luar pixmap → `return False`. Koreksi dengan `offsets[0]` dan `offsets[1]` agar bentuk **tidak bisa keluar gambar** |
| `bounded_move_vertex` `:621-627` | Bila `pos` di luar pixmap, ganti dengan `intersection_point` |
| `duplicate_selected_shapes` `:684-690` | Salin → geser offset tetap `(2.0, 2.0)` → `end_move(copy=True)` |
| `finalise()` `:900-926` | Bila auto-labeling → label dipaksa `edit_mode`; `current.close()`; **untuk `rectangle` titik diurutkan ke (xmin,ymin)-(xmax,ymax)**; `store_shapes()`; emit `new_shape` |

`contains_point` memakai `make_path().contains(point)` — untuk `line`, `point`,
`linestrip` path-nya tanpa area sehingga hampir selalu `False`. **Bentuk seperti
itu praktis hanya bisa dipilih lewat vertex-nya.**

`nearest_edge` mengiterasi tepi `[points[i-1], points[i]]` untuk `i = 0..n-1`,
sehingga **tepi penutup (titik terakhir → pertama) selalu ikut diuji**, termasuk
untuk bentuk terbuka.

## C.6 Riwayat undo

`store_shapes()` (`:154-161`) membuat `copy.deepcopy` setiap bentuk. Pemangkasan:
`shapes_backups[-num_backups - 1:]` → menyisakan **11** entri, lalu satu entri
baru ditambahkan → panjang maksimum **12 snapshot ≈ 11 langkah undo**, bukan 10
seperti tertulis di komentar konfigurasi.

`is_shape_restorable` `False` bila `len(shapes_backups) < 2` — karena snapshot
disimpan **setelah** tiap edit, jadi perlu kondisi sekarang + sebelumnya.

Dipanggil dari: `finalise` · `end_move` · `delete_selected` · `delete_shape` ·
`set_last_label` (sesudah `pop()`, jadi menimpa snapshot terakhir) ·
`load_shapes` · rilis mouse · rilis tombol.

## C.7 Urutan penggambaran (`paintEvent`, `:707-875`)

| # | Langkah |
|---|---|
| 0 | Guard: `pixmap` None / 0×0 → `super().paintEvent()` |
| 1 | `Antialiasing` + `SmoothPixmapTransform` |
| 2 | `p.scale(self.scale)` lalu `p.translate(offset_to_center())` |
| 3 | `drawPixmap(0,0)` + **`Shape.scale = self.scale`** (menyinkronkan ketebalan garis/vertex ke zoom) |
| 4 | Bila `is_loading`: overlay hitam alpha 20 + roda pemutar + teks → **`return`, bentuk tidak digambar sama sekali** |
| 5 | Bila `show_shape_groups`: titik pusat berwarna per anggota + kotak pembungkus `DashLine` |
| 6 | Semua bentuk visible; `shape.fill = shape.selected or shape == h_hape` → **hover ⇒ terisi** |
| 7 | `self.current` lalu `self.line` (pratinjau) |
| 8 | `selected_shapes_copy` (bayangan seret-kanan) |
| 9 | `fill_drawing` + polygon + ≥2 titik → salinan `current` + `line[1]`, `fill=True` |
| 10 | `show_texts`: font `max(6, round(8/scale))`; **dua putaran** — kotak latar `#00FF00` dulu, lalu teks hitam |
| 11 | `show_cross_line`: `#00FF00`, `DashLine`, `setOpacity(0.5)` |

Semua ukuran garis dan vertex memakai pola `max(1, round(n / scale))` sehingga
**tampak konstan secara visual pada zoom berapa pun**.

## C.8 Transformasi koordinat

```
transform_pos(point)  = point / scale - offset_to_center()      canvas.py:877-879
offset_to_center()    = ((area_w - pixmap_w*s) / (2*s), sama untuk y)  :881-891
```

Offset sudah dibagi `s` sehingga siap dipakai di ruang gambar. Urutan di painter
(`scale` dulu, lalu `translate`) konsisten dengan `transform_pos` (bagi skala
dulu, lalu kurangi offset).

## C.9 Grup

| Fungsi | Perilaku |
|---|---|
| `gen_new_group_id()` | `max(group_id bukan None) + 1`, minimum hasil `1` |
| `group_selected_shapes()` `:1236-1267` | `new_group_id = min(group_ids)` bila ada, jika tidak `gen_new_group_id()`; bila `len(group_ids) > 1` → `merge_group_ids` (berlaku **global**, bukan hanya seleksi) |
| `ungroup_selected_shapes()` `:1269-1285` | **Semua** bentuk di kanvas dengan id tersebut di-set `None` — ikut melepas bentuk yang tidak terpilih tetapi satu grup |

## C.10 Tipe bentuk

Enam, tervalidasi di dua tempat identik (`shape.py:89-103`, `canvas.py:143-151`):
`polygon`, `rectangle`, `point`, `line`, `circle`, `linestrip`. `None` menjadi
`"polygon"`; selain daftar itu → `ValueError`.

Cara menggambar per tipe (`shape.py:148-213`):

| Tipe | Garis | Vertex |
|---|---|---|
| `rectangle` | `addRect` bila 2 titik | hanya bila `selected` |
| `circle` | `addEllipse`, pusat = titik pertama, radius = jarak Euclid | hanya bila `selected` |
| `linestrip` | polyline tidak ditutup | hanya bila `selected` |
| `point` | tidak ada | **selalu** |
| `polygon`, `line` | polyline, `closeSubpath()` bila `is_closed()` | vertex 0 **selalu**, sisanya bila `selected` |

Lebar pen: `max(1, round(2.0 / scale))`.

---

# Bagian D — Penyimpanan dan Konfigurasi

## D.1 Format `.json` (skema labelme)

Suffiks tetap `.json` (`label_file.py:28`). Deteksi berkas label hanya dari
ekstensi lowercase, tanpa memeriksa isi (`:185-187`).

### Field tingkat atas saat menulis (`label_file.py:166-174`)

| Field | Tipe | Asal | Bawaan |
|---|---|---|---|
| `version` | str | `"0.4.36"` | selalu ditulis |
| `flags` | dict[str,bool] | dock Flags | `{}` |
| `shapes` | list[dict] | daftar bentuk | — |
| `imagePath` | str | **relatif** dari direktori JSON: `osp.relpath(...)` | — |
| `imageData` | str\|null | base64 bila `store_data` | `null` |
| `imageHeight` / `imageWidth` | int\|null | `self.image.height()/width()` | `null` |
| *kunci lain* | apa saja | seluruh `other_data` disalin ke tingkat atas | `{}` |

Penulisan: `json.dump(data, f, ensure_ascii=False, indent=2)`, UTF-8.

### Field tingkat atas saat membaca (`label_file.py:60-127`)

**Wajib** (akses subskrip → `KeyError` → `LabelFileError`): `imageData` (boleh
bernilai `null` tapi kuncinya harus ada), `imagePath`, `shapes`.
**Opsional**: `version` (hanya peringatan bila tak ada), `flags`,
`imageHeight`, `imageWidth`.

### Field per-bentuk

`shape_keys = ["label", "text", "points", "group_id", "shape_type", "flags"]`.
Saat menulis, dimulai dari `s.other_data.copy()` lalu **di-overwrite** oleh 6
kunci itu (`label_widget.py:1808-1820`) — jadi kunci asing per-bentuk
dipertahankan.

| Field | Tipe | Bawaan saat baca |
|---|---|---|
| `label` | str | **wajib** |
| `text` | str | `""` |
| `points` | list[[float,float]] | **wajib** |
| `group_id` | int\|null | `None` |
| `shape_type` | str | `"polygon"` |
| `flags` | dict\|null | `{}` |

Dua hal yang mudah terlewat:

- Bentuk dengan `points` kosong **dilewati** saat dimuat (`:1767-1769`) →
  hilang permanen pada penyimpanan berikutnya.
- `other_data` **selalu** dipaksa punya kunci `text` (`label_file.py:119`),
  sehingga setiap berkas yang pernah dibuka lalu disimpan ulang memperoleh
  field tingkat atas `"text": ""`.
- Bentuk `AUTOLABEL_OBJECT/ADD/REMOVE` **dikecualikan** dari penyimpanan
  (`:1824-1833`).

## D.2 Alur menyimpan

Pemicu: autosave setiap `set_dirty` · `Ctrl+S` · `Ctrl+Shift+S` · pindah gambar ·
klik berkas di dock · close file · drop berkas · buka folder · recent file ·
tutup aplikasi.

Urutan `save_labels` (`:1805-1870`): serialisasi bentuk → kumpulkan flag gambar
→ `relpath` → base64 bila perlu → `os.makedirs` → `label_file.save()` →
centang item di dock Files.

**Tidak ada atomic write dan tidak ada backup.** `LabelFile.save` menulis
langsung dengan mode `"w"` (`label_file.py:179`) — tanpa `.tmp`, tanpa
`os.replace`, tanpa `fsync`. Proses mati saat menulis → JSON terpotong dan
versi lama sudah hilang.

**`num_backups` bukan backup berkas** — itu kedalaman undo di memori, hilang
saat aplikasi ditutup.

Galat yang **tidak** tertangkap: `os.makedirs` gagal, `AssertionError` tabrakan
kunci `other_data` (`label_file.py:176`, di luar blok `try`),
`RuntimeError("There are duplicate files.")`.

## D.3 Alur memuat

Pemasangan gambar ↔ JSON **murni berdasarkan nama dasar identik**:
`label_file = osp.splitext(filename)[0] + ".json"` (`:2135`). Bila `output_dir`
diset, hanya basename yang dipakai (`:2136-2138`) → **struktur subfolder
diratakan**, risiko tabrakan nama antar subfolder.

`imageData` tertanam vs berkas terpisah (`label_file.py:85-90`):

- `imageData is not None` → `base64.b64decode`. **Gambar di disk tidak dibaca**
  — JSON dengan imageData akan menampilkan gambar tertanam meski berkas
  aslinya sudah diganti.
- Sebaliknya → gambar dibaca relatif terhadap direktori JSON.

`imageHeight`/`imageWidth` yang tidak cocok **hanya dicatat sebagai error log**
(`:130-144`); nilai koreksi yang di-`return` **diabaikan** pemanggilnya.

`PIL.Image.MAX_IMAGE_PIXELS = None` (`label_file.py:13`) — proteksi
decompression bomb dimatikan.

## D.4 `imageData`

Bawaan `store_data: false`. Bentuk byte yang ditanam (`label_file.py:49-58`):
`.jpg`/`.jpeg` → `convert("RGB")` + simpan ulang sebagai **JPEG** (rekompresi,
kualitas PIL default 75 → **kehilangan kualitas**); **semua ekstensi lain →
dikonversi ke PNG**. Base64 menambah +33%.

Menu "Save With Image Data" **tidak memanggil `save_config`** (`:2295-2297`) →
pilihan ini **tidak persisten** antar sesi, berbeda dari toggle lain.

## D.5 `set_dirty` / clean

`set_dirty` (`:1345-1361`):

1. `undo.setEnabled(canvas.is_shape_restorable)`
2. **Bila autosave aktif → simpan langsung dan `return`.** Dalam mode ini
   `self.dirty` **selalu `False`**, tanda bintang **tidak pernah** muncul, dan
   `may_continue()` selalu langsung `True` — pindah gambar tidak pernah bertanya.
3. Mode manual: `dirty = True`, Save aktif, judul `...*`

Karena `auto_save` bawaannya **`true`**, mode default AnyLabeling adalah
**autosave agresif**.

Pemicu `set_dirty`: 13 titik + 2 sinyal (`flag_widget.itemChanged` dan
`canvas.shape_moved`). Catat: ubah centang visibilitas **tidak** menandai dirty.

`may_continue()` (`:2572-2592`): `QMessageBox` **Save / Discard / Cancel**,
default **Save**.

## D.6 Change Output Dir dan Delete File

`output_dir` **tidak persisten** — hanya atribut runtime (`:1197`), tidak ada
`save_config`. Setelah restart kembali ke `None`.

`delete_file` (`:2531-2553`): `QMessageBox.warning` Yes/No →
`os.remove(label_file)`. **Hapus permanen, tidak ke trash. Berkas gambar tidak
pernah dihapus.**

**`get_label_file()` tidak menghormati `output_dir`** (`:2523-2529`), berbeda
dari `load_file`/`set_dirty`. Jadi saat Change Output Dir aktif, Delete File
menghitung path di samping gambar → biasanya tidak ada yang terhapus, dan
`has_label_file()` ikut salah.

## D.7 Konfigurasi — 37 kunci

Pembuatan awal: bila `~/.anylabelingrc` belum ada, isi bawaan **langsung
ditulis** ke sana (`config.py:52-53`).

Penggabungan `get_config` (`config.py:69-88`), 3 lapis: default paket →
berkas/YAML-string → argumen CLI. `update_dict` (`:19-33`) merge **rekursif**
untuk dict-vs-dict (jadi `shortcuts:` parsial tetap mewarisi sisanya), dan
kunci tak dikenal **dilewati dengan peringatan** — kecuali `theme` dan `ui`
yang di-whitelist manual.

Validasi (`:58-66`): `validate_label ∈ {None, "exact"}` ·
`shape_color ∈ {None, "auto", "manual"}` · `labels` tidak boleh duplikat ·
`theme ∈ {system, light, dark}`. Pelanggaran → `ValueError`, aplikasi gagal start.

Kunci yang **paling penting untuk kita** (daftar penuh 37 kunci ada di
`anylabeling_config.yaml`):

| Kunci | Bawaan | Pengaruh |
|---|---|---|
| `auto_save` | **`true`** | Autosave agresif — lihat D.5 |
| `store_data` | `false` | Tanam base64 ke JSON |
| `keep_prev` | `false` | Salin bentuk gambar sebelumnya **hanya bila gambar baru kosong** (`:2193-2195`) |
| `keep_prev_scale` | `false` | Jangan reset zoom saat pindah gambar |
| `keep_prev_brightness` / `_contrast` | `false` | **Tidak ada aksi UI** — hanya via rc |
| `auto_use_last_label` | `false` | Pakai label terakhir tanpa dialog. **Efek samping: `flags` dan `group_id` tidak bisa diisi** karena dialog dilewati |
| `display_label_popup` | `true` | Bila `false` + ada label terpilih → dialog dilewati |
| `validate_label` | `null` | `"exact"` → label harus sudah ada di daftar; butuh `labels` terisi atau `sys.exit(1)` |
| `label_flags` | `null` | dict `regex → [flag]`; pakai `re.match` (dari awal, **bukan** fullmatch) |
| `epsilon` | `10.0` | Ambang vertex/sisi |
| `shape_color` | `auto` | `auto` → colormap imgviz; `manual` → `label_colors` |
| `sort_labels` | `true` | Bila `false`, daftar label bisa di-drag |
| `label_completion` | `startswith` | Atau `contains` |

`save_config` (`config.py:36-43`) memakai `yaml.safe_dump` → **kunci diurutkan
alfabetis, semua komentar hilang**. Tidak atomic, tidak ada backup.

**`~/.anylabelingrc` ditulis sangat sering**: 17 titik pemanggilan, termasuk
**setiap kali pindah gambar** (`:2367`, `:2401`), timer dock 60 detik, dan
setelah tiap `load_file`. Lebih buruk: `save_dock_state` memanggil
`get_config()` yang **membaca ulang rc dari disk** lalu menulis penuh
(`:3093-3112`) — perubahan in-memory yang belum pernah di-`save_config` tidak
ikut, dan nilai lama dari disk bisa menghidupkan kembali state lama.

**Dua penyimpanan state terpisah**: YAML `~/.anylabelingrc` dan `QSettings`
(`label_widget.py:113`, menyimpan `filename`, `window/size`, `window/position`,
`window/state`, `recent_files`). **`--reset-config` hanya menghapus QSettings,
bukan rc** (`app.py:215-218`).

## D.8 EXIF dan ekstensi gambar

`apply_exif_orientation` (`utils/image.py:59-96`) menangani **8 nilai** orientasi
lengkap, dipanggil **hanya** di `LabelFile.load_image_file` (`label_file.py:47`).
Jadi byte gambar yang masuk ke `QImage.fromData` sudah ter-rotasi, dan koordinat
anotasi mengikuti orientasi terkoreksi. EXIF itu sendiri tidak ikut tersimpan.

**Tidak ada daftar ekstensi hard-coded** — semuanya runtime dari
`QImageReader.supportedImageFormats()`. Pemindaian folder membuang `svg`
(`:2736`) tapi **`svgz` tetap ikut**, dan **`pdf` ikut terdaftar sebagai
"gambar"**. Daftar ini bergantung plugin Qt yang terpasang.

---

# Bagian E — Auto-Labeling

## E.1 Katalog model (`models.yaml`, 22 model)

17× `segment_anything`, 5× `yolov8`, **0× `yolov5`** (kelasnya tetap terdaftar
dan diizinkan sebagai model kustom).

| Kelompok | Model |
|---|---|
| SAM 2.1 | Hiera-Tiny / Small / Base+ / Large, plus CoreML Large |
| SAM 3 | `sam3_vit_h_20260220` (`input_size: 1008`, ada `language_encoder_path`) |
| SAM 2 | Hiera-Tiny / Small / Base+ / Large |
| SAM 1 | **`mobile_sam_20230629`**, ViT-B/L/H + varian Quant |
| YOLOv8 | n / s / m / l / x |

Model `.zip` **tidak** mencantumkan `encoder_model_path`, `decoder_model_path`,
`input_size`, `max_width`, `max_height` di `models.yaml` — kunci itu datang dari
`config.yaml` di dalam zip yang **menimpa** konfigurasi lokal
(`model_manager.py:114-119`).

## E.2 Cache dan pengunduhan

Direktori: `~/anylabeling_data/models/<name>/`. Manager **membuat direktori dan
menulis `config.yaml` per model saat startup** (`:73-86`) — efek samping
filesystem hanya karena mendaftar model.

Dua jalur unduh: `.zip` via `urllib.request.urlretrieve` + cari folder yang
berisi `config.yaml`; repo Hugging Face via `snapshot_download`. Pemindahan akhir
`shutil.rmtree(extract_dir)` lalu `shutil.move` — **seluruh isi direktori model
dihapus lalu diganti**.

Progress: `.zip` ada throttle 0,2 s ≈ 5 FPS; **Hugging Face tidak ada sinyal
progress sama sekali** → status bar membeku pada "Loading model...".

Model kustom maksimum 5 (`MAX_NUM_CUSTOM_MODELS = 5`, `model_manager.py:33`);
yang terlama dibuang beserta foldernya.

## E.3 Alur prompt: klik kanvas → model

```
Tombol +Point/-Point/+Rect
  -> AutoLabelingMode(edit_mode, shape_type)              types.py:22-31
  -> canvas.set_auto_labeling_mode                        canvas.py:114-125
     (is_auto_labeling = True, create_mode = point|rectangle)
  -> klik -> finalise(): label DIPAKSA jadi AUTOLABEL_ADD/REMOVE   canvas.py:903-904
  -> update_auto_labeling_marks(): kumpulkan SELURUH shapes berlabel ADD/REMOVE
                                                          canvas.py:928-983
  -> auto_labeling_marks_updated -> on_new_marks
  -> set_auto_labeling_marks(marks) LALU LANGSUNG run_prediction()
                                                          auto_labeling.py:388-391
```

**Setiap klik memicu inferensi penuh secara otomatis** — tidak ada debounce.

Struktur mark:

| `type` | `data` | `label` | Sumber |
|---|---|---|---|
| `point` | `[int x, int y]` | `1` | shape `AUTOLABEL_ADD` |
| `rectangle` | `[x1,y1,x2,y2]` (tl, br) | `1` | shape `AUTOLABEL_ADD` |
| `point` | `[int x, int y]` | `0` | shape `AUTOLABEL_REMOVE` |
| `rectangle` | `[x1,y1,x2,y2]` | `0` | **tidak dapat dicapai dari UI** (tidak ada tombol −Rect) |

Koordinat dibulatkan ke `int` — presisi sub-piksel hilang.
`label: 1` pada mark rectangle **diabaikan** SAM1/SAM2; keduanya menimpanya
dengan label **2** (kiri-atas) dan **3** (kanan-bawah) sesuai konvensi SAM.

## E.4 Pipeline MobileSAM/SAM1 — matematika persis

Ini bagian yang paritasnya sudah diverifikasi di aplikasi kita.

| Atribut | Nilai | Rujukan |
|---|---|---|
| `target_size` | `1024` | `sam_onnx.py:12` |
| `input_size` | **`(684, 1024)` = (H, W)** — kanvas tetap, tidak bergantung gambar | `sam_onnx.py:13` |

### `encode(cv_image)` (`:135-172`)

1. `original_size = (H0, W0)`
2. `scale = min(1024 / W0, 684 / H0)` → **aspek dipertahankan**
3. `transform_matrix = [[s,0,0],[0,s,0],[0,0,1]]` — hanya skala, **tanpa
   translasi/pemusatan**
4. `cv2.warpAffine(..., (1024, 684), INTER_LINEAR)` → sisa area = 0 (padding
   kanan/bawah)
5. dtype `float32` bila `tensor(float)`, jika tidak `uint8`. **Tanpa normalisasi
   mean/std, tanpa transpose HWC→CHW, tanpa dimensi batch** → tensor `(684,1024,3)`

### `run_decoder` (`:68-114`)

1. `get_input_points`: point → apa adanya; rectangle → 2 titik label **2** dan **3**
2. **Titik pad selalu ditambahkan**: `concat([points, [[0,0]]])` dan
   `concat([labels, [-1]])`
3. `apply_coords(...)` — **secara matematis no-op** di jalur ini: dipanggil dengan
   `original_size = (684,1024)` dan `target_length = 1024`, sehingga
   `scale = 1024/1024 = 1.0`. Seluruh transformasi koordinat sesungguhnya
   dikerjakan `transform_matrix`
4. Homogenisasi + `onnx_coord @ transform_matrix.T` → setara `[x*s, y*s]`
5. `mask_input = zeros((1,1,256,256))`, `has_mask_input = zeros(1)` → **mask
   iteratif tidak pernah dipakai ulang antar klik**
6. `orig_im_size = np.array((684,1024), float32)` — **ukuran kanvas, bukan ukuran
   gambar asli**
7. `masks, _, _ = session.run(...)` → **skor IoU dibuang**
8. `inv = np.linalg.inv(transform_matrix)` → `warpAffine` mask kembali ke `(H0,W0)`
   dengan `INTER_LINEAR` **pada nilai logit** (belum diambangkan)

### `post_process` (`segment_anything.py:175-285`)

| # | Operasi |
|---|---|
| 1 | Peras ke 2-D: `while len(masks.shape) > 2: masks = masks[0]` |
| 2 | `astype(np.float32)` |
| 3 | Biner: **`> 0.0` → 255** (ambang mask logit adalah **0.0 keras**) |
| 4 | `cv2.findContours(RETR_EXTERNAL, **CHAIN_APPROX_NONE**)` |
| 5 | `epsilon = 0.001 * cv2.arcLength(contour, True)` + `approxPolyDP` |
| 6 | Buang kontur `area >= 0.9 * (H*W)` — **hanya bila jumlah kontur > 1** |
| 7 | Buang kontur `area <= 0.2 * mean(area)` — **hanya bila jumlah kontur > 1** |
| 8 | Mode polygon: **minimal 3 titik**; titik pertama **diduplikasi di akhir**; tiap titik dibulatkan `int` |
| 9 | Mode rectangle: satu bbox gabungan min/max dari **semua** kontur |

Baris `points[:,0] = points[:,0]` (`:230-231`, `:259-260`) adalah **no-op**,
sisa kode penskalaan lama.

Untuk SAM1/SAM2, hanya mask **pertama** yang dipakai (`:363-365`) — dekoder
memberi 3 kandidat, dua dibuang.

## E.5 Beda antar backend SAM

| Aspek | SAM1/MobileSAM | SAM2/2.1 | SAM3 |
|---|---|---|---|
| Sesi ONNX | 2 | 2 | **3** (image, language, decoder) |
| `providers` | **tidak diberikan** → kemungkinan selalu CPU | `get_available_providers()` | `get_available_providers()` |
| Ukuran input | kanvas tetap `(684,1024)`, **aspek dijaga** + padding | dari shape ONNX, `cv2.resize` **tanpa** jaga aspek | `1008×1008`, tanpa jaga aspek |
| Normalisasi | tidak ada (di dalam graf) | mean `[0.485,0.456,0.406]` std `[0.229,0.224,0.225]` | `(x/255 − 0.5)/0.5` |
| Ruang warna | RGB apa adanya | **`BGR2RGB` pada citra yang sudah RGB → kanal tertukar** | sengaja tanpa konversi (didokumentasikan) |
| Skala prompt | `@ transform_matrix.T` | normalisasi rasio | ternormalisasi `[0,1]` + **cxcywh** |
| Titik pad | ya, `[0,0]` label `-1` | tidak | tidak relevan |
| Prompt negatif | didukung | didukung | **tidak** — `mark["label"]` diabaikan |
| Mark dipakai | semua | semua | **hanya yang pertama** (`break`) |
| Pemilihan mask | mask pertama | `argmax(scores)` | semua instance `scores > confidence_threshold` |
| Kembali ke ukuran asli | `warpAffine` matriks invers | `cv2.resize` | tidak perlu |
| Prompt teks | tidak | tidak | ya, via CLIP tokenize dari `osam` |
| Log waktu | tidak | **`print("infer time: ...")` di jalur panas** | tidak |

## E.6 Widget bar Auto Labeling

| Kontrol | Pintasan | Kondisi tampil |
|---|---|---|
| `Auto` + combobox model | — | selalu; dimatikan saat memuat model dan saat inferensi |
| `Output` (Polygon/Rectangle) | — | hanya bila ada di `Meta.widgets` → **SAM ya, YOLO tidak** |
| `Mode` (Visual/Text/Both) | — | hanya SAM; **diaktifkan hanya untuk SAM3**, SAM1/SAM2 dipaksa "Visual" lalu `setEnabled(False)` |
| `Conf` spinbox (max 1.0, step 0.05, awal 0.5) | — | tampil bersama Mode. **Tidak dimatikan saat inferensi** |
| `Prompt` (QLineEdit) | Enter = jalankan | disembunyikan bila mode `visual` |
| `Run` | **`I`** | semua model. **Tidak dimatikan saat inferensi** |
| `+Point` | **`Q`** | hanya SAM, disembunyikan pada mode text |
| `-Point` | **`E`** | idem |
| `+Rect` | **tidak ada** | idem |
| `Clear` | **tidak ada** | idem |
| `Finish Object` | **`F`** | idem |
| Bar itu sendiri | `Ctrt+A` ⚠ salah tulis | tersembunyi saat start |

Pewarnaan tombol mode aktif: `success` untuk ADD, `error` untuk REMOVE+POINT
(`auto_labeling.py:142-193`).

## E.7 Bagaimana hasil model masuk ke daftar bentuk

**Tidak ada lapisan pratinjau terpisah** — hasil langsung masuk `label_list` dan
kanvas. Yang ada adalah tahap **pengesahan label**, khusus SAM.

| Model | `replace` | Perilaku |
|---|---|---|
| SAM (semua) | `False` | Shape lama berlabel `AUTOLABEL_OBJECT` dihapus, shape baru ditambahkan. **Mark ADD/REMOVE dipertahankan** |
| YOLOv5/v8 | **`True`** | **Seluruh anotasi pada gambar itu terhapus** dan diganti hasil deteksi |

Siklus hidup hasil SAM:

1. Label placeholder `"AUTOLABEL_OBJECT"`, tidak masuk `unique_label_list`
2. Tekan **`F`** → dialog label (atau label terakhir bila
   `auto_use_last_label`) → validasi → timpa `label`/`flags`/`group_id` untuk
   **semua** shape `AUTOLABEL_OBJECT` → `clear_auto_labeling_marks()` →
   `set_dirty()`
3. **Kalau tidak disahkan**, `clear_auto_labeling_marks` akan menghapusnya —
   dipicu oleh tombol Clear, ganti model, ganti mode gambar, atau **pindah
   berkas** (`label_widget.py:2110`)

Jadi hasil SAM **efektif bersifat sementara sampai ditekan `F`**. Ini desain,
bukan bug.

## E.8 Cache embedding

| Aspek | Nilai |
|---|---|
| Implementasi | `LRUCache` `OrderedDict` + `threading.Lock` |
| Ukuran | `cache_size = 10` |
| Kunci | **`filename`** — bukan hash isi, jadi berkas yang diubah di tempat memberi embedding basi |
| Praunggah | `preloaded_size = 7`, tapi daftar berkas berikutnya hanya 5 → efektif 5 |
| Pembersihan | **hanya** di `set_text_prompt` bila teks berubah |

Masalah nyata: `edit_prompt.textChanged` terhubung ke `set_text_prompt` pada
**setiap ketikan**, dan `set_text_prompt` **mengosongkan seluruh cache**.
Mengetik "bottle" mengosongkan cache 6 kali → encoder citra harus jalan ulang,
padahal itu langkah termahal.

## E.9 `confidence_threshold` dan `iou_threshold`

**`iou_threshold` tidak ada sama sekali** di seluruh paket. Padanan
fungsionalnya bernama `nms_threshold`, hanya di YOLO.

`confidence_threshold` — inilah yang menyesatkan:

| Model | Spinbox berefek? |
|---|---|
| **SAM3** | **YA** — satu-satunya konsumen |
| SAM1/MobileSAM | **TIDAK** — `predict_masks` tidak punya parameter itu |
| SAM2/2.1/CoreML | **TIDAK** — pemilihan mask murni `argmax(scores)` |
| YOLOv5/v8 | Nilai dipakai, **tapi hanya dari berkas config**, bukan dari spinbox (kelasnya tidak punya `set_confidence_threshold`) |

Untuk SAM1/SAM2, spinbox **terlihat dan bisa diubah tetapi tidak berpengaruh
apa pun**. Ambang yang sungguh menentukan bentuk adalah **0.0 keras** di
`segment_anything.py:190-191`.

## E.10 Gambar besar dan kinerja

**Tidak ada tiling, tidak ada penurunan resolusi adaptif, dan `max_width`/
`max_height` dibaca tetapi tidak dipakai di mana pun.**

`qt_img_to_rgb_cv_img` membaca **seluruh berkas dari disk lagi** dengan
`np.fromfile` + `cv2.imdecode` setiap kali `filename` diberikan
(`utils/opencv.py:14-17`) — dekode penuh tambahan di atas `QImage` yang sudah
dimuat UI.

Hambatan kinerja lain: inferensi tidak dapat benar-benar dibatalkan
(`stop_inference` hanya diperiksa di tiga titik, tidak pernah di tengah
`session.run`) · `CHAIN_APPROX_NONE` menyimpan setiap piksel batas ·
`epsilon = 0.001 * arcLength` sangat kecil sehingga poligon bisa punya sangat
banyak titik.

---

# Bagian F — Ekspor

## F.1 Format yang didukung

Empat, satu arah saja. **Tidak ada impor format apa pun** — pembaca anotasi
satu-satunya adalah `LabelFile` untuk skema labelme `.json`.

## F.2 YOLO

Mode `detection` (bawaan) dan `segmentation`.

```
segmentation, polygon:   class x1/W y1/H x2/W y2/H ...      (%.6f)
segmentation, rectangle: 4 sudut (x1,y1),(x2,y1),(x2,y2),(x1,y2)
detection, rectangle:    class xc yc w h  dengan abs()
detection, polygon:      bbox dari min/max seluruh titik
```

Titik **tidak** diputar, tidak dinormalkan arah CW/CCW, tidak ditutup, dan
**tidak ada clamping ke [0,1]** — titik di luar kanvas menghasilkan nilai
negatif atau >1 apa adanya.

Bentuk yang **dilewati**: `point`, `line`, `linestrip`, `circle`.

`"\n".join(results)` → **tanpa newline di akhir berkas**; daftar kosong → berkas
0 byte (ini benar untuk contoh negatif ultralytics).

## F.3 Pascal VOC

Urutan elemen: `annotation` → `folder` (**path direktori lengkap**, bukan nama
folder) → `filename` → `path` → `source/database "Unknown"` → `size/width,
height, depth "3"` (hardcode) → `segmented "0"` (hardcode) → `object`*.

Setiap `object`: `name` · `pose "Unspecified"` · `truncated "0"` ·
`difficult "0"` · `bndbox/xmin,ymin,xmax,ymax`.

Pembulatan `str(int(...))` = **truncation menuju nol**, bukan `round`, bukan
`floor` untuk min / `ceil` untuk max. **Tidak ada `+1` konvensi VOC**, tidak ada
clamping. Poligon selalu direduksi jadi `bndbox`.

## F.4 COCO

`info.year` = **2023 hardcode**, `date_created` selalu kosong. `categories.id`
**1-based** (`i + 1`), urutan `sorted()`. `image_id` = indeks + 1.
`iscrowd` selalu 0. **Informasi `group_id` dan `text` hilang.**

**Rumus luas poligon SALAH** (`export_formats.py:338-343`):

```python
area += 0.5 * abs(x1 * y2 - x2 * y1)   # abs DI DALAM penjumlahan
```

Shoelace yang benar menerapkan `abs()` **setelah** penjumlahan. Akibatnya nilai
itu bukan luas, melainkan jumlah luas segitiga tiap sisi dengan **titik asal
(0,0)** — **tidak invarian terhadap translasi**, dan untuk koordinat gambar
selalu **over-estimate berkali-kali lipat**. Anotasi `rectangle` tidak terkena
(memakai `width * height`), jadi dataset campuran punya skala `area` yang tidak
konsisten antar tipe.

## F.5 CreateML

Keluaran **list**, bukan dict. Setiap anotasi:
`{"label": ..., "coordinates": {"x", "y", "width", "height"}}`.

**Bug**: CreateML mengharap `x`,`y` = **titik tengah** kotak, tetapi yang
ditulis adalah `x_min`,`y_min` (sudut kiri-atas). Parameter `image_heights` dan
`image_widths` diterima tapi tidak pernah dipakai.

## F.6 Tata letak folder keluaran

| Kondisi | Yang dibuat |
|---|---|
| `split_data=True` | `output_dir/train`, `/val`, `/test` — **hanya YOLO** ditambah `labels/` + `images/` per split |
| `split_data=False` | **hanya YOLO** dibuat `labels/` + `images/` |
| VOC/COCO/CreateML | tidak ada subfolder — semuanya langsung di `output_dir` |

- **`classes.txt`**: hanya YOLO, di **root** `output_dir` (bukan per split),
  0-based, tanpa newline akhir.
- **`data.yaml` / `dataset.yaml`: TIDAK ADA.** Tidak ada `import yaml` di
  `export_*.py`. Jadi dataset YOLO hasil ekspor **tidak siap latih** untuk
  ultralytics tanpa membuat `data.yaml` manual.
- COCO/CreateML: `annotations.json` per split, **bercampur dengan salinan
  gambar** di folder yang sama.
- **Gambar selalu disalin** — tidak ada opsi untuk mematikannya.

## F.7 Split

```python
random.shuffle(json_files)               # TANPA seed
n_train = int(n_files * train_ratio)
n_val   = int(n_files * val_ratio)
test    = sisa                            # test_ratio TIDAK dipakai
```

Tidak reprodusibel, dan `int()` truncation membuat sisa bisa lebih besar dari
rasio yang diminta.

Dialog menawarkan: format · YOLO export mode · source (current/select + rekursif,
**tercentang** bawaannya) · output folder (wajib) · UUID4 random names · split
train/val/test (70/20/10, bilangan bulat persen).

**Yang tidak ditawarkan**: opsi sertakan gambar · penyaringan/pengurutan kelas ·
pembuatan `data.yaml` · presisi/clamping koordinat · split deterministik
(seed) · stratifikasi.

## F.8 Pengurutan kelas per format

| Format | Sumber | Indeks |
|---|---|---|
| YOLO (worker) | `all_labels` dari **semua split** → `sorted()` | **0-based**, konsisten lintas split |
| COCO | per split, `sorted()` | **1-based**, **TIDAK konsisten lintas split** |
| VOC / CreateML | tidak ada daftar kelas | nama string saja |

Baik YOLO maupun COCO mengumpulkan label **tanpa menyaring `shape_type`**,
sedangkan penulisan anotasi menyaringnya. Akibatnya kelas yang hanya dipakai
pada `point`/`circle`/`line` tetap masuk `classes.txt`/`categories`,
**menggeser indeks** kelas lain, sekaligus menghasilkan kelas kosong.

---

# Bagian G — Cacat AnyLabeling yang TIDAK boleh kita tiru

Diurutkan dari yang paling berdampak.

## G.1 Fungsional

| # | Cacat | Rujukan |
|---|---|---|
| 1 | Galat unduh model menembus `GenericWorker.run` → `finished` tak pernah diemit → **pemuatan model tersangkut permanen**, semua percobaan berikutnya hanya mencetak "Another model is being loaded" | `model_manager.py:411-416`; `utils.py:15-16` |
| 2 | YOLO memakai `replace=True` → **menghapus seluruh anotasi gambar** saat Run | `yolov8.py:181`; `label_widget.py:2761-2764` |
| 3 | Spinbox `Conf` terlihat dan bisa diubah untuk SAM1/SAM2 tetapi **tidak berpengaruh apa pun** — kontrol yang menyesatkan | `segment_anything.py:358` |
| 4 | SAM3 mengabaikan `mark["label"]` dan hanya memakai mark pertama → `-Point` dan multi-titik tidak berfungsi | `sam3_onnx.py:84-101` |
| 5 | `get_label_file()` mengabaikan `output_dir` → Delete File salah target | `label_widget.py:2523-2529` |
| 6 | Tidak ada atomic write untuk `.json` maupun rc → proses mati saat menulis = data hilang | `label_file.py:179-180`; `config.py:40-41` |
| 7 | `finished` selalu diemit → pesan **"Export Completed" muncul walau ekspor gagal atau dibatalkan** | `export_worker.py:563-564`; `export_dialog.py:467-477` |
| 8 | `ZeroDivisionError` bila split kosong (YOLO/VOC tidak dijaga, COCO/CreateML dijaga) | `export_worker.py:230-233`, `:303-306` |
| 9 | VOC rekursif dengan subfolder → `FileNotFoundError`; logika `makedirs` yang benar ada tapi **kode mati** | `export_worker.py:321-325` vs `:297` |
| 10 | VOC + UUID: `<filename>`/`<path>`/`<folder>` masih memakai path asli, tidak cocok dengan salinan yang di-rename | `export_worker.py:328-334` |
| 11 | `sam2_onnx` melakukan `BGR2RGB` pada citra yang **sudah** RGB → kanal tertukar | `sam2_onnx.py:106` |
| 12 | `output_dir` meratakan subfolder berdasarkan basename → anotasi saling menimpa | `label_widget.py:1350-1353` |
| 13 | Mengetik prompt mengosongkan seluruh cache embedding **tiap karakter** | `auto_labeling.py:71`; `segment_anything.py:156-161` |
| 14 | Shape SAM3 mode teks tidak dibersihkan antar-Run → **duplikasi bentuk** | `label_widget.py:2767-2768` |
| 15 | Aksi Theme ditambahkan **3×** → menu berisi 9 butir duplikat | `label_widget.py:930-933`, `:936-939`, `:974-981` |
| 16 | `import_image_folder()` mengaktifkan Next/Prev **sebelum** validasi | `label_widget.py:2707-2711` |
| 17 | `store_data` tidak persisten (tanpa `save_config`) sementara toggle lain persisten | `label_widget.py:2295-2297` |
| 18 | `save_dock_state` membaca-ulang rc dari disk lalu menulis penuh → bisa me-rollback state in-memory | `label_widget.py:3093-3112` |
| 19 | Item label bergroup_id ditampilkan **tanpa `html.escape`** padahal delegate merender HTML | `label_widget.py:1610-1611`, `:2911-2912` |
| 20 | `itemDropped` juga diemit saat `removeRows` → `set_dirty` terpicu pada penghapusan | `label_list_widget.py:103-106` |

## G.2 Kode mati dan konfigurasi yang diabaikan

| Hal | Rujukan |
|---|---|
| `hide_background_shapes` tidak pernah dipanggil → fitur "sembunyikan bentuk latar" **mati** | `canvas.py:539-550` |
| `export_to_yolo_segmentation` tanpa pemanggil | `export_formats.py:117-141` |
| `ToolBar.add_action()` tidak pernah dipanggil (pembungkusan `QToolButton` tidak aktif) | `toolbar.py:35-51` |
| `LabelingWidget.toolbar()` dan `actions.file_menu_actions` tanpa pemanggil | `label_widget.py:1308-1317`, `:844` |
| Aksi `Tools` dibuat lalu dibuang; argumen ke-3 `"tools"` dipakai sebagai shortcut yang tidak sah | `label_widget.py:897-904` |
| `ColorDialog` tidak diekspor maupun dipakai | `color_dialog.py` |
| `logger_level` di YAML diabaikan | `anylabeling_config.yaml:14` |
| Sub-kunci `*_dock.{show,closable,movable,floatable}` diabaikan | `anylabeling_config.yaml:40-59` |
| `input_size`/`max_width`/`max_height` diwajibkan tetapi tidak pernah dipakai | `segment_anything.py:57-59` |
| `score_threshold` diwajibkan di YOLOv8 tetapi tidak dipakai | `yolov8.py:30` |
| `ACCENT_LIGHT`/`ACCENT_DARK` tidak terpakai | `theme.py:16-17` |
| Ikon `refresh` dan `tools` dirujuk tetapi tidak ada di resources | `label_widget.py:671`, `:900` |
| Logger tidak memasang handler karena `logger.__class__ = ...` | `logger.py:58` |
| `auto_label: Ctrt+A` salah tulis | `anylabeling_config.yaml:117` |
| `prompt_encoder_model_path: sSAM2_1Large...` salah tulis (`sS`) | `models.yaml:23` |
| Fallback `importlib_resources` dead code (Python ≥3.11 dijamin) | `config.py:3-7` |

## G.3 Risiko yang belum terbukti terpicu

`intersection_point` mengembalikan `QPoint` (integer) sedangkan `transform_pos`
menghasilkan `QPointF`. Di `bounded_move_vertex` dan `bounded_move_shapes`
terjadi aritmetika campuran. Diverifikasi bahwa `QPoint - QPointF`,
`QPointF - QPoint`, `QPointF += QPoint`, dan `QPointF -= QPoint` **semuanya
melempar `TypeError` di PyQt6 yang terpasang** — jadi menggeser bentuk/vertex
melewati tepi gambar berpotensi `TypeError`. **TIDAK PASTI** apakah jalur itu
benar-benar terpicu di runtime; yang diuji hanya operasi aritmetikanya, bukan
aplikasinya.

Juga: `mouseReleaseEvent` memanggil `self.shapes.index(self.h_hape)` tanpa
penjagaan → `ValueError` bila `h_hape` sudah dihapus (`canvas.py:514-516`).
Jalur pemicunya **TIDAK PASTI**.

---

# Bagian H — Penyimpangan `label-apps` dari AnyLabeling

Inilah bagian yang menentukan pekerjaan berikutnya. Dibagi empat kategori.

## H.1 Cara Bagian H ini disusun

Daftar di bawah **bukan** hasil ingatan. Tiap baris diperoleh dengan memeriksa
perilaku yang dideskripsikan di Bagian A–F terhadap kode aplikasi ini, satu per
satu.

Itu penting karena versi pertama Bagian H disusun manual, dan dua temuan yang
sudah tercatat rapi di bagian deskriptif tidak pernah menyeberang ke sini —
sehingga tidak pernah dibandingkan dan tidak pernah jadi pekerjaan:

- **penutupan cincin poligon** (`segment_anything.py:235`), tercatat di Bagian
  E.4 langkah 8 sejak awal, baru ditindaklanjuti setelah ditanyakan;
- **satu kontur = satu bentuk** (`segment_anything.py:224-252`), tidak pernah
  masuk daftar sama sekali.

Auditnya sendiri tidak bolong — keenam laporan agen mencakup seluruh 40 berkas
`.py` paket itu. Yang bocor adalah penyeberangan dari "AnyLabeling begini" ke
"kita bagaimana".

## H.2 Setara — sudah diperiksa, tidak ada bedanya

| Perilaku | Rujukan |
|---|---|
| `epsilon` 10.0 dibagi skala untuk sentuh vertex/sisi | `canvas.py:46` |
| Panah menggeser 5.0 satuan, **hanya** di mode Sunting | `canvas.py:1103-1110` |
| Klik ganda menutup poligon | `canvas.py:557-569` |
| `Esc` membuang bentuk yang sedang digambar | `canvas.py:1094-1097` |
| `Enter` mengakhiri bentuk | `canvas.py:1098-1099` |
| Ctrl+wheel memperbesar, wheel biasa menggulir | `canvas.py:1067-1079` |
| Bentuk tidak bisa diseret keluar gambar | `canvas.py:629-653` |
| Bentuk tersorot saat kursor di atasnya | `canvas.py:803-806` |
| Prioritas hover: vertex → sisi → bagian dalam | `canvas.py:346-398` |
| Titik hanya bisa disisipkan pada polygon dan linestrip | `shape.py:116-118` |
| Sisi penutup ikut diuji pada bentuk tertutup | `shape.py:247-261` |
| `finalise` mengurutkan rectangle ke (xmin,ymin)-(xmax,ymax) | `canvas.py:900-926` |
| line/point/linestrip hanya bisa dipilih lewat titiknya | `shape.py:263-265` |
| Grup: id terkecil menang; melepas grup melepas semua berid sama | `canvas.py:1236-1285` |
| `imagePath` ditulis relatif terhadap folder JSON | `label_widget.py:1841` |
| Field asing dipertahankan saat menyimpan ulang | `label_file.py:175-177` |
| Flag tingkat gambar | `label_widget.py:1834-1839` |
| Warna kelas otomatis dan konsisten | `label_widget.py:1711-1741` |
| Garis bantu silang, teks kelas, penanda grup, isi saat menggambar | `canvas.py:757-873` |
| Sembunyikan/tampilkan semua poligon | `label_widget.py:2069-2073` |
| Centang di panel Objects mengatur visibilitas | `label_widget.py:1899-1901` |
| Pencarian nama berkas di panel Files | `label_widget.py:1614-1619` |
| `keep_prev`, `keep_prev_scale`, `auto_use_last_label` | `anylabeling_config.yaml:6-10` |
| Enam tipe bentuk beserta cara mengakhirinya | `canvas.py:432-462` |
| Klik di sisi, Shift+klik, klik ulang, seret klik kanan | `canvas.py:463-512` |
| Ctrl+C/Ctrl+V, G/U, Alt mematikan snapping | `label_widget.py:496-511`, `canvas.py:1100` |
| Kecerahan dan kontras, rentang dan titik netral sama | `brightness_contrast_dialog.py` |
| Urutan objek bisa digeser | `label_list_widget.py:124-125` |

## H.3 Sengaja berbeda

| Hal | AnyLabeling | Kita | Alasan |
|---|---|---|---|
| **Satu klik SAM** | satu bentuk per kontur (`segment_anything.py:224-252`) | selalu **satu** bentuk, kontur terbesar | Diminta: satu klik satu objek. Diukur pada 86 klik data nyata: **0%** kehilangan potongan berarti |
| **Cincin poligon di kanvas** | tertutup, dua titik bertumpuk | terbuka di kanvas, tertutup di berkas | Titik bertumpuk membuat menyeret salah satunya meninggalkan duri |
| **Kedalaman urungkan** | ~11 langkah, `deepcopy` tiap bentuk | 40 langkah, snapshot JSON | Lebih murah dan lebih dalam |
| **Rentang zoom** | 1%–1000% | 5%–4000% | Gambar 4080 px butuh perbesaran lebih untuk menaruh titik |
| **Simpan otomatis** | agresif: apa pun, tanpa tanda, tanpa konfirmasi | begitu tiap objek punya kelas; pratinjau tidak ikut | Menyimpan coretan yang belum disahkan itu jebakan |
| **Tulis-aman** | menimpa langsung, proses mati = JSON rusak | `.tmp` lalu `replace` | — |
| **Isolasi pengguna** | satu proses satu orang | per akun: dataset, unggahan, sesi | Dipakai satu tim |
| **Luas poligon COCO** | rumus salah, bergantung posisi | shoelace benar | Lihat H.6 |
| **Indeks kelas ekspor** | — | ikut urutan `data.yaml` sumber | Lihat H.6 |
| **Pembagian split** | acak tanpa seed, `test_ratio` diabaikan | split asli dipertahankan; kalau membagi sendiri, per foto asal | Mencegah kebocoran |

## H.4 Belum ada, dan memang belum diputuskan

Ditemukan lewat pengecekan silang ini, bukan sebelumnya:

| Hal | AnyLabeling | Dampak kalau tidak ada |
|---|---|---|
| **`validate_label: exact`** | label baru ditolak kalau tidak ada di daftar (`label_widget.py:1542-1556`) | Salah ketik nama kelas langsung membuat kelas baru tanpa peringatan. Untuk satu tim, ini penyebab dataset kotor yang paling sering |
| **Flag per bentuk** | dinyalakan lewat `label_flags`, pola regex → daftar flag (`label_dialog.py:169-192`) | Datanya sudah dipertahankan bulat-balik, tetapi belum ada cara mengisinya. Panel Flags kita masih tingkat gambar saja |
| `sort_labels` | daftar kelas bisa diurutkan atau diseret sendiri | Kecil |
| `label_completion` | autocomplete `startswith`/`contains` di dialog label | Tidak berlaku: kelas dipilih dari panel, bukan diketik bebas |
| `label_colors` | warna manual per kelas | Kecil; warna otomatis kita sudah konsisten |
| `display_label_popup` | melewati dialog kalau sudah ada kelas terpilih | Tidak berlaku: alur kita memang memilih kelas lebih dulu |

Dua yang pertama layak dipertimbangkan. Sisanya tidak berlaku atau kecil.

## H.5 Sengaja belum — di luar alur melabeli

| Hal | Alasan menunda |
|---|---|
| Ekspor CreateML | Formatnya pun salah di AnyLabeling (F.5) |
| SAM2/SAM2.1/SAM3, YOLOv5/v8 | Fokus MobileSAM; SAM2 punya bug kanal warna (G.1 #11) |
| Prompt teks | Butuh CLIP tokenizer dari API privat `osam` (`sam3_onnx.py:240`) |
| Ganti bahasa dan tema | Bukan bagian melabeli |

## H.6 Bug AnyLabeling — status di aplikasi kita

| # | Bug | Status |
|---|---|---|
| 1 | Rumus luas poligon COCO salah (F.4) | ✅ **sudah dibetulkan.** `_luas_poligon_coco` memakai shoelace dengan `abs()` di luar penjumlahan. Diuji: kotak yang sama digeser ke empat posisi tetap 100, dan poligon 4 sudut dari sebuah rectangle kini sama nilainya dengan jalur `width*height` — di AnyLabeling justru tidak |
| 2 | `category_id` COCO tidak konsisten antar split (F.8) | **Masih ada** |
| 3 | Tidak ada atomic write saat menyimpan `.json` (G.1 #6) | ✅ **tidak pernah kita tanggung.** Sudah `.json.tmp` + `replace` sejak awal (`routers/annotate.py:225-231`); catatan sebelumnya di dokumen ini keliru |
| 4 | Pemindai tidak memahami tata letak `train/valid/test` bersarang | ✅ **sudah** — ekspor Roboflow dibuka dari akarnya, seluruh split terbaca sekaligus dan tiap gambar diberi tanda asalnya |

### Bug milik kita sendiri yang ikut ditemukan dan dibetulkan

**Rectangle rusak setiap kali disimpan ulang.** `read_json` memekarkan rectangle
2 titik menjadi 4 untuk keperluan menggambar, kanvas menerima yang sudah
dimekarkan, lalu menyimpannya kembali apa adanya — sehingga rectangle di berkas
menjadi 4 titik. Berkas seperti itu **tidak bisa dibuka lagi di AnyLabeling
desktop**: `shape.py:160` di sana menuntut rectangle punya tepat 1 atau 2 titik.

Perbaikannya memisahkan dua hal yang sebelumnya tercampur: `pts` adalah bentuk
siap gambar, `pts_asli` adalah titik seperti di berkas, dan yang dikirim ke
kanvas adalah yang kedua. Penyimpanan juga menormalkan sendiri sebagai jaring
pengaman. Dijaga oleh `test_rectangle_tetap_dua_titik_setelah_disimpan_ulang`
dan `test_rectangle_dimekarkan_dari_kanvas_dikembalikan_jadi_dua_titik`.

**Menyunting dataset YOLO kehilangan pekerjaan diam-diam.** Penyimpanan hanya
menulis `.json` di sebelah gambar, sementara pemindai membaca `labels/*.txt` —
jadi aplikasi melaporkan "Tersimpan", lalu suntingannya lenyap begitu dataset
dipindai ulang, dan tidak pernah ikut ke hasil ekspor.

Sekarang menyimpan dataset YOLO menulis **keduanya**: `labels/<nama>.txt` yang
memang dibaca saat melatih, plus `.json` di sebelah gambar sebagai cadangan
untuk `group_id`, catatan teks, dan flag yang tidak punya tempat di format YOLO.
Tiga aturan yang dijaga tes:

- **Jenis berkas tidak berubah sendiri.** Dataset bbox tetap 5 kolom; hanya
  kalau ada bentuk yang bukan rectangle berkasnya naik ke format segmentasi,
  karena menulisnya sebagai bbox akan membuang maskny.
- **Cadangan tidak pernah menebak.** Kalau jumlah bentuk di `.txt` sudah tidak
  cocok dengan cadangan, seluruh cadangan diabaikan — memasangkan catatan ke
  bentuk yang salah lebih berbahaya daripada kehilangan catatan.
- **Yang tidak muat dilaporkan.** Bentuk `point`/`line` dan kelas yang belum ada
  di daftar kelas tidak ditulis ke `.txt`, dan pengguna diberi tahu — termasuk
  saat autosave, karena justru itu yang tidak boleh lewat tanpa terlihat.
- **Baris yang tidak disunting tidak tersentuh.** Berkas YOLO di lapangan sering
  menyimpan lebih dari 6 desimal (`0.144853125`). Menulis ulang seluruh berkas
  dengan 6 desimal akan memangkas ketelitian objek yang tidak disunting siapa
  pun — beda di bawah seperseratus piksel, tetapi berkasnya berubah tanpa ada
  yang meminta. Baris yang angkanya sama dalam 1e-6 ditulis kembali persis
  seperti aslinya. Diuji pada **900 berkas label sungguhan** dari ekspor
  Roboflow milik pengguna: buka lalu simpan tanpa mengubah apa pun menghasilkan
  berkas yang **byte-nya identik**, di ketiga split.

**Ekspor membagi ulang dataset yang sudah terbagi, dan memecah augmentasi.**
Ditemukan begitu ekspor bersplit bisa dibuka. Dua masalah sekaligus:

1. `bagi_split` selalu membagi sendiri berdasarkan nama berkas, sehingga split
   asli Roboflow dibuang. Gambar yang tadinya di `valid` bisa pindah ke `train`,
   dan perbandingan dengan hasil latihan sebelumnya jadi tidak berarti.
2. Membagi per **nama berkas** memecah augmentasi dari foto yang sama. Roboflow
   menempelkan akhiran sesudah `.rf.<hash>`, jadi satu foto bisa punya puluhan
   berkas. Diukur pada dataset pengguna: **6.137 dari 11.319 foto asal (54,2%)**
   tersebar ke lebih dari satu split — model dinilai memakai versi lain dari
   gambar yang sudah dia pelajari.

Sekarang split bawaan dataset dipertahankan, dan saat membagi sendiri yang
diundi adalah **foto asalnya**, bukan tiap berkas. Diukur ulang pada dataset
yang sama: **0 dari 11.319** foto asal terpecah. Ringkasan ekspor juga
mengatakan kalau split bawaan yang dipakai, supaya rasio yang diketik tidak
diam-diam diabaikan.

## Audit kesetaraan keluaran dengan Roboflow

Dilakukan atas pertanyaan: *"outputnya dipastikan sama atau tidak?"* — bukan
soal kemampuan SAM, melainkan soal isi baris labelnya. Dibandingkan 40 objek
yang sama: poligon Roboflow vs poligon yang dihasilkan pipeline kita dari
prompt kotak yang diturunkan dari poligon itu.

| Aspek | Roboflow | Kita, sebelum | Kita, sesudah |
|---|---|---|---|
| Struktur baris | `kelas x y x y …` ternormalisasi | sama | sama |
| Jumlah titik (median) | 66 | 67 | 67 |
| Arah putaran | 40/40 searah jarum jam | 40/40 sama | sama |
| Desimal | 17 (repr float) | 6 tetap | 6 tetap |
| **Cincin tertutup** | 40/40 | **0/40** | **24/24** |
| **Koordinat di luar [0,1]** | 0 dari 3.905 | bisa terjadi | **0** |
| **Titik kembar beruntun** | 0 dari 3.905 | bisa lolos | **0** |
| IoU pada 640×640 | — | 0,969 | — |
| IoU pada 160×160 (resolusi mask latihan) | — | **0,960** | — |

Tiga hal dibetulkan, dan satu hal dibuktikan tidak penting.

**Kerapatan titik terbukti tidak berpengaruh.** Poligon Roboflow disederhanakan
sampai jumlah titiknya sama dengan punya kita, lalu dibandingkan dengan
aslinya: **IoU 1,000** pada resolusi mask latihan. Jadi perbedaan kerapatan —
kalaupun ada — tidak mengubah apa pun.

**Cincin sekarang ditutup di berkas, tetap terbuka di kanvas.** AnyLabeling
menutupnya (`segment_anything.py:235`) dan Roboflow juga, tetapi menutupnya
juga di kanvas berarti dua titik bertumpuk persis — menyeret salah satunya
meninggalkan duri yang tidak terlihat asalnya. Jadi penutupan dipasang di batas
tulis (`scanner.tutup_cincin`) dan dilepas di batas baca (`buka_cincin`).

**Koordinat dikurung dan kembaran beruntun dibuang** sebelum ditulis
(`scanner.rapikan_titik`), menyamakan keluaran dengan Roboflow yang tidak punya
satu pun dari keduanya di 3.905 poligon.

Yang penting: **jaminan lama tetap berlaku.** Buka-simpan tanpa mengubah apa pun
pada 900 berkas label Roboflow nyata masih **byte-identik semua**, karena
pembanding baris kini mengabaikan perbedaan penutupan cincin — menambahkan satu
titik yang tidak mengubah bentuk bukan alasan sah untuk menulis ulang berkas
orang lain.

### Indeks kelas bergeser saat sebagian kelas kosong

Ditemukan dalam audit yang sama, dan ini yang paling berbahaya karena tidak
meninggalkan jejak. `peta_kelas` dulu selalu menurunkan indeks dari label yang
**kebetulan ada** di seleksi, diurutkan abjad. Selama seluruh kelas terwakili
dan namanya memang urut abjad — kebetulan berlaku untuk 12 dataset pengguna,
karena Roboflow mengurutkannya — hasilnya sama.

Tetapi begitu satu kelas tidak punya objek di seleksi yang diekspor:

```
data.yaml sumber : ['botol','kaleng','mlp','plastic-cup','tetra']   0..4
hanya 3 berobjek : {'botol': 0, 'mlp': 1, 'tetra': 2}
                    'mlp' 2->1,  'tetra' 4->2
```

Berkasnya tetap konsisten dengan `data.yaml` barunya, jadi tidak ada yang
tampak salah — padahal labelnya tidak lagi cocok dengan dataset asal maupun
model yang sudah dilatih. Sekarang urutan `names` dataset sumber yang dipakai,
kelas tanpa objek tetap disertakan, dan label yang tidak ada di daftar resmi
ditambahkan di belakang alih-alih dibuang.

### Perbaikan lain yang ikut terbawa

- **Nama kelas ekspor Roboflow** dibaca dari `data.yaml` (`names:`), ditelusuri
  sampai dua tingkat ke atas. Sebelumnya hanya `classes.txt` yang dicari, jadi
  membuka satu split menampilkan kelas sebagai `0`, `1`, `2`.
- **Pemindaian jauh lebih cepat.** Dimensi gambar dibaca dari header, bukan
  dengan mendekode seluruh piksel. Pada dataset 55.665 gambar milik pengguna,
  memindai turun dari **72 detik menjadi 20**.

### Kenapa luas COCO dibetulkan, bukan ditiru

Tiga alasan, diputuskan setelah diperiksa:

1. **Terbukti salah, bukan sekadar berbeda.** Kotak 10×10 yang sama memberi 100
   di titik-asal, 2.100 di (100,100), dan 8.100 di (500,300). Luas tidak boleh
   bergantung pada posisi.
2. **Kompatibilitas tidak membeli apa pun.** Tidak ada konsumen yang membaca
   nilai itu sambil mengharapkan bug-nya; AnyLabeling sendiri tidak pernah
   membacanya kembali.
3. **Ada yang rusak diam-diam kalau dibiarkan.** `pycocotools` memakai `area`
   untuk memilah objek small/medium/large pada ambang 32² dan 96²; nilai yang
   membengkak puluhan kali mendorong hampir semua objek ke bucket "large"
   sehingga AP_small dan AP_medium jadi tidak bermakna.

Pelatihan YOLO tidak terpengaruh sama sekali — format YOLO tidak punya field
`area`.

## H.7 Keputusan yang sudah diambil

1. **Luas poligon COCO** → dibetulkan (alasannya di atas).
2. **Autosave** → menyimpan **begitu tiap objek sudah punya kelas**, seperti
   Roboflow, dan semuanya tetap bisa disunting sesudahnya. Pratinjau SAM yang
   belum disahkan dengan `F` tidak pernah ikut tersimpan; objek tanpa kelas
   tidak memicu penyimpanan sama sekali. Riwayat urungkan 40 langkah tetap utuh
   supaya salah klik selalu bisa dibatalkan.
3. **`AUTOLABEL_OBJECT` sementara** → tetap seperti sekarang (lebih pemaaf dari
   AnyLabeling). Karena pratinjau tidak pernah ikut tersimpan, tidak ada risiko
   coretan yang menetap di berkas.

---

# Lampiran A — Yang tidak dapat dipastikan dari kode

Dicatat supaya tidak ada yang menganggapnya sudah terverifikasi:

- Nilai `input_size`/`max_width`/`max_height` di dalam `config.yaml` model
  `.zip` — `~/anylabeling_data/models/` kosong, belum ada model terunduh.
- Apakah `QPoint`/`QPointF` campuran (G.3) benar-benar terpicu di runtime.
- Apakah `FileDialogPreview.ExistingFile` / `.Detail` bergaya Qt5
  (`label_widget.py:2415`, `:2421`) masih berfungsi di PyQt6 terpasang.
- Apakah Qt menolak shortcut `Ctrt+A` secara diam-diam atau memetakannya ke
  sesuatu.
- Ukuran memori nyata cache embedding SAM3 (6 tensor backbone pada 1008×1008).
- Tata letak visual dock setelah `restoreState()` dari rc pengguna.
- Apakah galat slot pada jalur `[]` YOLO (`yolov8.py:162`) ditelan Qt atau
  dilaporkan.

---

*Disusun dari enam audit paralel baca-saja atas AnyLabeling 0.4.36. Tidak ada
berkas AnyLabeling yang diubah. Dokumen pendamping:
[`PARITAS.md`](PARITAS.md) (verifikasi bit-exact MobileSAM),
[`NOTICE.md`](../NOTICE.md) (atribusi GPLv3).*

---

# Lampiran B — Berkas yang detailnya sempat hilang

Dokumen ini memampatkan enam laporan agen, dan pemampatan itu **lossy**: 12
berkas sempat tidak disebut sama sekali di sini walaupun seluruhnya sudah
diaudit. Bagian ini mengembalikannya, supaya tidak ada berkas yang tampak
belum diperiksa padahal sudah.

| Berkas | Isi | Yang perlu diketahui |
|---|---|---|
| `services/auto_labeling/lru_cache.py` | `LRUCache` `OrderedDict` + `threading.Lock` (`:7-39`) | Cache embedding SAM, muat 10 entri, kunci = **nama berkas** (bukan hash isi) sehingga berkas yang diubah di tempat memberi embedding basi |
| `services/auto_labeling/registry.py` | `ModelRegistry` (`:4-7`) | Peta `type` di `models.yaml` → kelas model. Dekorator `@ModelRegistry.register("yolov8")` |
| `services/auto_labeling/sam2_coreml.py` | Backend SAM2 lewat CoreML | macOS saja, import lazy. Memakai `PIL.resize((1024,1024), LANCZOS)` — aspek tidak dijaga. `original_size` di sini `(W,H)`, backend lain `(H,W)` — tidak konsisten |
| `services/auto_labeling/yolov5.py` | Inferensi YOLOv5 | Tidak ada entri bawaannya di `models.yaml`; hidup hanya sebagai model kustom. Menyaring dua tahap (`obj_conf` lalu `score_threshold`), berbeda dari YOLOv8 |
| `views/common/toaster.py` | `QToaster` (9,5 KB) | Notifikasi toast buatan sendiri di Qt. Tidak perlu diporting — pustaka toast web mana pun cukup |
| `views/labeling/testing.py` | `assert_labelfile_sanity()` | **Kontrak paling eksplisit** untuk skema JSON: `image_path` wajib, `image_data` opsional, dimensi harus cocok, tiap `points` dibatasi `0 <= x <= W` |
| `views/labeling/utils/_io.py` | `lblsave()` | Simpan PNG berpalet; rentang nilai harus `[-1,254]`, di luar itu `ValueError` |
| `views/labeling/widgets/brightness_contrast_dialog.py` | Dialog kecerahan/kontras | 2 slider `range(0,150)` awal `50`; faktor `nilai/50` → 0–3, netral di 1. Selalu diterapkan ke citra **asli**, jadi tidak menumpuk |
| `views/labeling/widgets/escapable_qlist_widget.py` | `EscapableQListWidget` (`:5-10`) | `Esc` membatalkan seleksi daftar. Dipakai panel Labels |
| `views/labeling/widgets/file_dialog_preview.py` | `FileDialogPreview` | Dialog buka berkas dengan pratinjau 300×300; `.json` ditampilkan sebagai teks ber-indent. Memakai enum bergaya Qt5 — kompatibilitasnya **TIDAK PASTI** |
| `views/labeling/widgets/unique_label_qlist_widget.py` | Panel **Labels** | Item menyimpan nama kelas di `UserRole`, dirender `QLabel` HTML dengan titik warna. **Tidak ada checkbox, tidak ada klik-ganda, tidak ada menu konteks.** Kelas terpilih jadi kelas bawaan bentuk baru |
| `views/labeling/widgets/zoom_widget.py` | `ZoomWidget(QSpinBox)` | Rentang **1–1000%**, sufiks `%`, tanpa tombol naik/turun. `setWhatsThis` memberi repr list ke `fmt_shortcut` sehingga teksnya rusak |

Dari kedua belas ini, yang benar-benar berpengaruh pada aplikasi kita cuma dua:
`testing.py` karena memuat kontrak skema JSON yang kita ikuti, dan
`brightness_contrast_dialog.py` yang rentang serta titik netralnya kita tiru
persis.
