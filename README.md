# labeling-tools

Perkakas untuk menyiapkan dan memeriksa dataset anotasi
**AnyLabeling / labelme / YOLO-seg**.

## Isi

| Berkas | Kegunaan |
|---|---|
| **[label-apps/](label-apps/)** | Papan periksa anotasi berbasis web, multi-akun. Aplikasi utama — lihat [README-nya](label-apps/README.md). |
| [qc_web.py](qc_web.py) | Versi papan periksa dalam satu berkas, tanpa dependensi selain OpenCV. Digantikan oleh `label-apps/`, tetap disimpan karena praktis untuk pemakaian cepat satu orang di laptop. |
| [labelme2yoloseg.py](labelme2yoloseg.py) | Konversi anotasi labelme/AnyLabeling `.json` ke format YOLO-seg. |
| [load-dataset-rf-to-anylabel.py](load-dataset-rf-to-anylabel.py) | Ambil dataset dari Roboflow lalu ubah ke format yang bisa dibuka AnyLabeling. |

## Mulai dari mana

Untuk memeriksa dataset bersama tim, pakai aplikasi web-nya:

```bash
cd label-apps
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run.py --adduser <namamu>
.venv/bin/python run.py --host 0.0.0.0 --datasets-root ~/datasets
```

Penjelasan lengkap, struktur kode, dan catatan keamanan ada di
[label-apps/README.md](label-apps/README.md).
