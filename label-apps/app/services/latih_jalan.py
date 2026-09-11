"""
Proses yang benar-benar melatih. Dijalankan latih.jalankan() sebagai
SUBPROSES TERLEPAS, bukan sebagai thread di dalam server.

    python -m app.services.latih_jalan <folder-projek> <nomor>

Kenapa berkas tersendiri, bukan fungsi biasa:

  1. Ia harus hidup melewati matinya server. Training memakan berjam-jam,
     sedangkan server dev menyalakan ulang dirinya tiap kali ada berkas
     berubah. Subproses dengan sesi sendiri tidak ikut terbawa.

  2. Namanya muncul di /proc/<pid>/cmdline, dan latih.hidup() memakainya untuk
     memastikan pid yang tercatat memang masih proses training kita — bukan
     proses lain yang kebetulan mendapat nomor pid yang sama sesudah yang asli
     mati. Tanpa pemeriksaan itu, training yang sudah lama berhenti bisa
     terlihat "berjalan" selamanya di layar.

  3. Ultralytics mengimpor torch, matplotlib, dan lainnya seberat beberapa
     detik. Menaruhnya di proses terpisah membuat server tidak pernah
     membayar ongkos itu.

SATU TRAINING PADA SATU WAKTU
Kuncinya berkas (flock), bukan variabel di memori — justru karena prosesnya
terpisah dan bisa hidup melewati server. Satu training YOLO mengisi hampir
seluruh VRAM; dua sekaligus di kartu 8 GB bukan dua kali lebih cepat melainkan
dua-duanya gagal kehabisan memori. Yang tidak kebagian menunggu, dan selama
menunggu statusnya "antre" supaya terlihat di layar.
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

# Dijalankan sebagai modul dengan cwd di akar aplikasi, jadi impor relatif
# paket tetap berlaku.
from . import latih

KUNCI_GPU = Path(tempfile.gettempdir()) / "labelapp-latih-gpu.lock"


def _sekarang() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _data_yaml(ds: Path, nomor_versi: int) -> Path:
    """data.yaml milik versi yang dipakai sebagai sumber."""
    from . import buatversi

    d = buatversi.dir_versi(ds, nomor_versi)
    p = d / "data.yaml"
    if not p.exists():
        raise FileNotFoundError(
            f"versi v{nomor_versi} belum punya data.yaml di {d} — "
            "versinya mungkin dihapus atau dibuat sebelum ekspor YOLO ada")
    return p


def main() -> int:
    if len(sys.argv) < 3:
        print("pemakaian: latih_jalan <folder-projek> <nomor>", file=sys.stderr)
        return 2
    ds, nomor = Path(sys.argv[1]), int(sys.argv[2])

    isi = latih.baca(ds, nomor)
    if isi is None:
        print(f"training L{nomor} tidak ada di {ds}", file=sys.stderr)
        return 2

    try:
        yaml = _data_yaml(ds, int(isi["versi"]))
    except Exception as e:                       # noqa: BLE001
        latih.perbarui(ds, nomor, keadaan="gagal", galat=str(e)[:300],
                       selesai_pada=_sekarang())
        print(f"GAGAL sebelum mulai: {e}", file=sys.stderr)
        return 1

    # ---- antre: satu training pada satu waktu ----
    KUNCI_GPU.touch(exist_ok=True)
    f = open(KUNCI_GPU, "r+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print("  giliran langsung didapat")
    except OSError:
        print("  menunggu giliran — ada training lain yang memakai GPU")
        latih.perbarui(ds, nomor, keadaan="antre")
        fcntl.flock(f, fcntl.LOCK_EX)            # menunggu sampai dapat
        print("  giliran didapat")

    t0 = time.time()
    latih.perbarui(ds, nomor, keadaan="jalan", mulai_pada=_sekarang(),
                   pid=os.getpid())
    try:
        os.environ.setdefault("YOLO_VERBOSE", "False")
        from ultralytics import YOLO

        par = dict(isi.get("par") or {})
        keluar = latih.dir_latih(ds, nomor)

        print(f"  model  : {isi['bobot']}")
        print(f"  data   : {yaml}")
        print(f"  tugas  : {isi['tugas']}  | epochs {par.get('epochs')}  "
              f"batch {par.get('batch')}  imgsz {par.get('imgsz')}")
        print(f"  warna  : hsv_h={par.get('hsv_h')} hsv_s={par.get('hsv_s')} "
              f"bgr={par.get('bgr')}")
        sys.stdout.flush()

        model = YOLO(isi["bobot"])
        model.train(
            data=str(yaml),
            task=isi["tugas"],
            # project/name diarahkan supaya keluarannya mendarat tepat di
            # .latih/L<n>/, tempat yang dibaca latih.baca_hasil_csv().
            project=str(keluar.parent),
            name=keluar.name,
            exist_ok=True,
            device=0,
            **par,
        )
        latih.perbarui(ds, nomor, keadaan="selesai", selesai_pada=_sekarang(),
                       detik_total=round(time.time() - t0, 1))
        print(f"\n  SELESAI dalam {int(time.time() - t0)} detik")
        print(f"  bobot terbaik: {keluar / 'weights' / 'best.pt'}")
        return 0
    except KeyboardInterrupt:
        latih.perbarui(ds, nomor, keadaan="batal", selesai_pada=_sekarang())
        return 130
    except Exception as e:                       # noqa: BLE001
        # Pesannya dipendekkan untuk layar, jejak lengkapnya tetap masuk log.
        latih.perbarui(ds, nomor, keadaan="gagal", galat=str(e)[:300],
                       selesai_pada=_sekarang())
        traceback.print_exc()
        return 1
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
            f.close()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
