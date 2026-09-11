"""
Proses yang menjalankan evaluasi produksi.

    python -m app.services.evaluasi_jalan <folder-projek> <nomor-latih>

Subproses, dengan alasan yang sama seperti latih_jalan: ultralytics dan torch
berat diimpor, dan server tidak perlu membayar ongkos itu maupun memegang
memorinya selamanya hanya karena sekali waktu ada orang menekan tombol Uji.

Hasilnya ditulis ke .latih/L<n>.eval.json. Tidak ada kemajuan bertahap yang
dilaporkan: satu evaluasi selesai dalam hitungan belasan detik, dan bilah
kemajuan untuk pekerjaan sependek itu cuma menambah bagian yang bisa rusak.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from . import evaluasi as ev
from . import latih


def _tulis(ds: Path, nomor: int, isi: dict) -> None:
    p = latih._dir(ds) / f"L{nomor}.eval.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(isi, indent=1))
    os.replace(tmp, p)


def baca(ds, nomor: int) -> dict | None:
    p = latih._dir(Path(ds)) / f"L{nomor}.eval.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def _utama(model, im, conf):
    """Kelas dengan confidence tertinggi, atau (None, 0)."""
    r = model(im, imgsz=ev.IMGSZ, conf=conf, iou=ev.IOU, verbose=False)[0]
    if r.boxes is None or len(r.boxes) == 0:
        return None, 0.0
    j = int(np.argmax(r.boxes.conf.cpu().numpy()))
    return r.names[int(r.boxes.cls[j])], float(r.boxes.conf[j])


def main() -> int:
    if len(sys.argv) < 3:
        print("pemakaian: evaluasi_jalan <folder-projek> <nomor>", file=sys.stderr)
        return 2
    ds, nomor = Path(sys.argv[1]), int(sys.argv[2])
    t0 = time.time()

    isi = latih.baca(ds, nomor)
    if isi is None:
        print(f"training L{nomor} tidak ada", file=sys.stderr)
        return 2
    bobot = latih.dir_latih(ds, nomor) / "weights" / "best.pt"
    if not bobot.exists():
        _tulis(ds, nomor, {"keadaan": "gagal", "galat":
                           "best.pt belum ada — trainingnya belum selesai"})
        return 1

    _tulis(ds, nomor, {"keadaan": "jalan",
                       "mulai": datetime.now().strftime("%Y-%m-%d %H:%M")})
    try:
        os.environ.setdefault("YOLO_VERBOSE", "False")
        from ultralytics import YOLO

        versi = int(isi["versi"])
        files = ev.foto_uji(ds, versi)
        if not files:
            _tulis(ds, nomor, {"keadaan": "gagal", "galat":
                               f"versi v{versi} tidak punya split test — "
                               "tidak ada foto yang bisa diuji"})
            return 1

        model = YOLO(str(bobot))
        rng = np.random.default_rng(0)
        print(f"  model : {bobot}")
        print(f"  foto  : {len(files)} dari test v{versi}")

        # ---- 1 & 2: satu kali baca gambar, dipakai kedua pengukuran ----
        pasangan, jawaban, rinci = [], [], []
        for p in files:
            im = cv2.imread(str(p))
            if im is None:
                continue
            kelas_asli = ev.label_uji(ds, versi, p)
            baris = []
            for _nama, fn in ev.PERLAKUAN:
                c, _cf = _utama(model, fn(im, rng), ev.CONF)
                baris.append(c)
            jawaban.append(baris)
            pasangan.append((kelas_asli, baris[0]))
            rinci.append({"berkas": p.name, "sebenarnya": kelas_asli,
                          "jawaban": baris})
        print(f"  selesai {len(jawaban)} gambar x {len(ev.PERLAKUAN)} perlakuan")

        # ---- 3: kelas default pada latar kosong ----
        latar, sumber_latar = ev.foto_latar(ds)
        tally: dict[str, int] = {}
        if latar:
            n_latar = 0
            for p in latar:
                im = cv2.imread(str(p))
                if im is None:
                    continue
                n_latar += 1
                c, _ = _utama(model, im, ev.CONF)
                if c:
                    tally[c] = tally.get(c, 0) + 1
        else:
            n_latar = 0
            for p in files[:20]:
                im = cv2.imread(str(p))
                if im is None:
                    continue
                n_latar += 1
                c, _ = _utama(model, ev.petak_kosong(im), ev.CONF)
                if c:
                    tally[c] = tally.get(c, 0) + 1

        # Mode dibaca dari MANIFES TRAININGNYA, bukan dari versinya sekarang:
        # versinya bisa saja sudah dibuat ulang dengan mode berbeda, dan yang
        # menentukan cara model ini dinilai adalah mode yang berlaku saat ia
        # dilatih.
        from . import mode_warna as mw

        mode = mw.sah((isi.get("warna") or {}).get("mode"))
        w = ev.nilai_warna(jawaban, mode)
        a = ev.nilai_akurasi(pasangan)
        d = ev.nilai_default(tally, n_latar)
        hasil = {
            "keadaan": "selesai",
            "selesai": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "detik": round(time.time() - t0, 1),
            "versi": versi, "n_foto": len(jawaban),
            "perlakuan": [n for n, _ in ev.PERLAKUAN],
            "rona": list(ev.NAMA_RONA), "terang": list(ev.NAMA_TERANG),
            "mode": mode, "mode_ket": mw.KETERANGAN[mode],
            "warna": w, "akurasi": a, "default": d,
            "sumber_latar": sumber_latar,
            "putusan": ev.putusan(w, a, d),
            # Disimpan supaya orang bisa melihat gambar MANA yang goyah, bukan
            # cuma persentasenya. Angka tanpa contohnya tidak bisa ditindak.
            "rinci": rinci[:60],
        }
        _tulis(ds, nomor, hasil)
        print(f"\n  mode                 : {mode}")
        print(f"  berubah karena rona  : {w['skor_rona']}%")
        print(f"  berubah karena terang: {w['skor_terang']}%  ({w['tingkat']})")
        if a["n"]:
            print(f"  akurasi              : {a['benar']}/{a['n']} ({a['persen']}%)")
        print(f"  kelas default        : {d['tingkat']}")
        print(f"  PUTUSAN              : {hasil['putusan']['tingkat']}")
        return 0
    except Exception as e:                       # noqa: BLE001
        _tulis(ds, nomor, {"keadaan": "gagal", "galat": str(e)[:300]})
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
