#!/usr/bin/env python3
"""
yolo2labelme.py — konversi dataset YOLO-seg (mis. hasil export Roboflow)
menjadi file JSON labelme, supaya bisa dibuka & diedit di AnyLabeling.

Kebalikan dari labelme2yoloseg.py.

Mendukung:
  - Struktur Roboflow  : <root>/{train,valid,test}/{images,labels}
  - Struktur satu split: <root>/{images,labels}
  - Nama kelas diambil dari data.yaml / dataset.yaml / classes.txt (otomatis)
  - Poligon (segmentasi) maupun bbox (5 kolom) -> keduanya jadi poligon

CONTOH PEMAKAIAN
----------------
1) Cara paling cepat — JSON ditulis langsung di samping gambar aslinya.
   Semua split (train/valid/test) diproses sekaligus.

     python yolo2labelme.py --src ~/dataset/botol-kaleng-1

2) Hanya satu split.

     python yolo2labelme.py --src ~/dataset/botol-kaleng-1 --split train

3) DISARANKAN — salin ke folder kerja terpisah, dataset asli tidak tersentuh.
   Berguna kalau dataset asli read-only, dibagi lewat Drive, atau Anda ingin
   bisa membandingkan hasil edit dengan label Roboflow yang asli.

     python yolo2labelme.py --src ~/dataset/botol-kaleng-1 \
                            --dst ~/edit --copy-images --split train

   Hasilnya: ~/edit/train/  berisi gambar + JSON, siap dibuka di AnyLabeling.
   Ulangi dengan --split valid dan --split test bila perlu; tiap split masuk
   ke subfolder sendiri di dalam --dst.

4) Nama kelas tidak terbaca dari data.yaml -> tunjuk manual.

     python yolo2labelme.py --src ~/dataset/x --names classes.txt

5) Menimpa JSON yang sudah ada (default: file yang sudah ada dilewati,
   supaya hasil edit Anda tidak hilang).

     python yolo2labelme.py --src ~/dataset/x --overwrite

CATATAN PENTING SOAL SPLIT
--------------------------
AnyLabeling membuka SATU folder pada satu waktu dan TIDAK membaca subfolder.
Jadi buka folder split-nya langsung, bukan folder induknya:

     BENAR  : ~/edit/train           atau  ~/dataset/botol-kaleng-1/train/images
     SALAH  : ~/edit                 (kosong — isinya cuma subfolder)
     SALAH  : ~/dataset/botol-kaleng-1

Edit tiap split secara terpisah. Ini justru aman: file JSON selalu tersimpan
di sebelah gambarnya, sehingga gambar tidak pernah berpindah split dan
pembagian train/valid/test tetap utuh.
"""

import argparse
import json
import os
import re
import shutil
from pathlib import Path

import cv2

IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff")


def load_names(root: Path):
    """Ambil daftar nama kelas dari data.yaml / dataset.yaml / classes.txt."""
    for cand in ("data.yaml", "dataset.yaml", "data.yml"):
        f = root / cand
        if not f.exists():
            continue
        txt = f.read_text(encoding="utf-8", errors="ignore")

        # gaya lama Roboflow:  names: ['a', 'b', 'c']
        m = re.search(r"^names:\s*\[(.*?)\]", txt, re.S | re.M)
        if m:
            items = re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))
            if items:
                return {i: n for i, n in enumerate(items)}, f.name

        # gaya baru Ultralytics:  names:\n  0: a\n  1: b
        block = re.search(r"^names:\s*$(.*?)(?=^\S|\Z)", txt, re.S | re.M)
        if block:
            pairs = re.findall(r"^\s+(\d+)\s*:\s*(.+?)\s*$", block.group(1), re.M)
            if pairs:
                return {int(k): v.strip().strip("'\"") for k, v in pairs}, f.name

    f = root / "classes.txt"
    if f.exists():
        lines = [l.strip() for l in f.read_text().splitlines() if l.strip()]
        return {i: n for i, n in enumerate(lines)}, "classes.txt"

    return {}, None


def find_splits(root: Path, only=None):
    """Kembalikan list (nama_split, img_dir, lbl_dir)."""
    out = []
    if (root / "images").is_dir() and (root / "labels").is_dir():
        out.append((root.name, root / "images", root / "labels"))
    for sp in ("train", "valid", "val", "test"):
        i, l = root / sp / "images", root / sp / "labels"
        if i.is_dir() and l.is_dir():
            out.append((sp, i, l))
    if only:
        out = [o for o in out if o[0] == only]
    return out


def txt_to_shapes(txt_path: Path, W: int, H: int, names: dict, stats: dict):
    shapes = []
    if not txt_path.exists():
        return shapes
    for line in txt_path.read_text().strip().splitlines():
        p = line.split()
        if len(p) < 5:
            continue
        try:
            cid = int(float(p[0]))
            v = [float(x) for x in p[1:]]
        except ValueError:
            stats["baris_rusak"] += 1
            continue

        label = names.get(cid, f"class_{cid}")

        if len(v) == 4:
            # bbox: cx cy w h  ->  poligon 4 titik
            cx, cy, bw, bh = v
            x1, y1 = (cx - bw / 2) * W, (cy - bh / 2) * H
            x2, y2 = (cx + bw / 2) * W, (cy + bh / 2) * H
            pts = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
            stats["dari_bbox"] += 1
        else:
            if len(v) % 2 != 0 or len(v) < 6:
                stats["baris_rusak"] += 1
                continue
            pts = [[v[i] * W, v[i + 1] * H] for i in range(0, len(v), 2)]
            stats["dari_poligon"] += 1

        pts = [[min(max(x, 0.0), float(W)), min(max(y, 0.0), float(H))] for x, y in pts]
        shapes.append({
            "label": label,
            "text": "",
            "points": pts,
            "group_id": None,
            "shape_type": "polygon",
            "flags": {},
        })
    return shapes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, type=Path, help="root dataset YOLO")
    ap.add_argument("--dst", type=Path, default=None,
                    help="folder tujuan. Default: JSON ditulis di samping gambar aslinya.")
    ap.add_argument("--split", default=None, help="hanya proses split ini (train/valid/test)")
    ap.add_argument("--copy-images", action="store_true",
                    help="salin gambar ke --dst (wajib bila dataset asli read-only)")
    ap.add_argument("--names", type=Path, default=None, help="file classes.txt manual")
    ap.add_argument("--overwrite", action="store_true", help="timpa JSON yang sudah ada")
    a = ap.parse_args()

    if not a.src.exists():
        raise SystemExit(f"Folder tidak ada: {a.src}")

    if a.names and a.names.exists():
        names = {i: n.strip() for i, n in enumerate(a.names.read_text().splitlines()) if n.strip()}
        src_names = str(a.names)
    else:
        names, src_names = load_names(a.src)

    if not names:
        print("[!] Nama kelas tidak ditemukan. Label akan memakai 'class_0', 'class_1', ...")
        print("    Beri --names classes.txt agar nama aslinya terpakai.")
    else:
        print(f"Nama kelas dari  : {src_names}")
        print(f"                   {[names[k] for k in sorted(names)]}")

    splits = find_splits(a.src, a.split)
    if not splits:
        raise SystemExit("Struktur images/ + labels/ tidak ditemukan.")

    total = {"gambar": 0, "objek": 0, "dari_poligon": 0, "dari_bbox": 0,
             "baris_rusak": 0, "tanpa_label": 0, "dilewati": 0}

    for name, img_dir, lbl_dir in splits:
        imgs = sorted(p for p in img_dir.iterdir() if p.suffix.lower() in IMG_EXT)
        if not imgs:
            continue
        out_dir = (a.dst / name) if a.dst else img_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        n_ok = n_obj = 0
        for ip in imgs:
            jp = out_dir / (ip.stem + ".json")
            if jp.exists() and not a.overwrite:
                total["dilewati"] += 1
                continue

            im = cv2.imread(str(ip))
            if im is None:
                total["dilewati"] += 1
                continue
            H, W = im.shape[:2]

            shapes = txt_to_shapes(lbl_dir / (ip.stem + ".txt"), W, H, names, total)
            if not shapes:
                total["tanpa_label"] += 1

            if a.dst or a.copy_images:
                tgt_img = out_dir / ip.name
                if not tgt_img.exists():
                    shutil.copy2(ip, tgt_img)

            json.dump({
                "version": "0.4.36",
                "flags": {},
                "shapes": shapes,
                "imagePath": ip.name,
                "imageData": None,
                "imageHeight": H,
                "imageWidth": W,
            }, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

            n_ok += 1
            n_obj += len(shapes)

        total["gambar"] += n_ok
        total["objek"] += n_obj
        print(f"  {name:<8} {n_ok:>6} gambar  {n_obj:>6} objek   -> {out_dir}")

    print("\nRingkasan")
    print(f"  JSON dibuat        : {total['gambar']}")
    print(f"  Objek total        : {total['objek']}")
    print(f"    dari poligon     : {total['dari_poligon']}")
    print(f"    dari bbox        : {total['dari_bbox']}  (dikonversi jadi kotak 4 titik)")
    print(f"  Gambar tanpa label : {total['tanpa_label']}")
    print(f"  Dilewati           : {total['dilewati']}")
    if total["baris_rusak"]:
        print(f"  Baris label rusak  : {total['baris_rusak']}")

    if not a.dst:
        print("\nBuka folder images/ di AnyLabeling — anotasi langsung muncul.")
    else:
        print(f"\nBuka {a.dst}/<split>/ di AnyLabeling.")


if __name__ == "__main__":
    main()
