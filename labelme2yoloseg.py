#!/usr/bin/env python3
"""
labelme2yoloseg.py — konversi anotasi AnyLabeling/labelme (JSON) ke dataset YOLO-seg.

Fitur:
  - Split 80/10/10 yang GROUP-AWARE: semua foto dari objek fisik yang sama
    dijamin jatuh di split yang sama (mencegah kebocoran train/val).
  - Urutan class_id STABIL antar-run (disimpan di classes.txt).
  - Rectangle otomatis dikonversi jadi poligon 4 titik.
  - Menghasilkan data.yaml siap pakai Ultralytics (YOLOv8/v11/YOLO26-seg).

Contoh:
  python labelme2yoloseg.py --src ~/dataset/raw --dst ~/dataset/yolo
  python labelme2yoloseg.py --src ~/dataset/raw --dst ~/dataset/yolo \
      --group-regex '^(.*?)_\\d+$' --ratios 0.8 0.1 0.1 --seed 42
"""

import argparse
import json
import os
import random
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

IMG_EXT = [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"]


# ---------- util ----------

def find_image(json_path: Path, data: dict) -> Path | None:
    """Cari file gambar pasangan JSON. Prioritas: imagePath, lalu stem+ekstensi."""
    ip = data.get("imagePath")
    if ip:
        cand = (json_path.parent / ip).resolve()
        if cand.exists():
            return cand
    for ext in IMG_EXT:
        for e in (ext, ext.upper()):
            cand = json_path.with_suffix(e)
            if cand.exists():
                return cand
    return None


def shape_to_polygon(shape: dict) -> list | None:
    """Ambil titik poligon. Rectangle (2 titik diagonal) dikonversi jadi 4 titik."""
    st = shape.get("shape_type", "polygon")
    pts = shape.get("points") or []
    if st == "polygon":
        return pts if len(pts) >= 3 else None
    if st == "rectangle" and len(pts) == 2:
        (x1, y1), (x2, y2) = pts
        xa, xb = sorted([x1, x2])
        ya, yb = sorted([y1, y2])
        return [[xa, ya], [xb, ya], [xb, yb], [xa, yb]]
    return None


def group_key(stem: str, pattern: str | None) -> str:
    """Kunci pengelompokan objek fisik. Tanpa regex -> tiap file grup sendiri."""
    if not pattern:
        return stem
    m = re.match(pattern, stem)
    return m.group(1) if m and m.groups() else stem


def resolve_classes(records, dst: Path, explicit: Path | None) -> list:
    """
    Urutan kelas harus STABIL antar-run. Prioritas:
      1. --classes file
      2. classes.txt yang sudah ada di dst (run sebelumnya)
      3. urut abjad dari data, lalu disimpan
    """
    if explicit:
        names = [l.strip() for l in explicit.read_text().splitlines() if l.strip()]
    else:
        existing = dst / "classes.txt"
        found = sorted({s["label"] for r in records for s in r["shapes"]})
        if existing.exists():
            names = [l.strip() for l in existing.read_text().splitlines() if l.strip()]
            new = [n for n in found if n not in names]
            if new:
                print(f"[INFO] Kelas baru ditemukan, ditambahkan di akhir: {new}")
                names += new
        else:
            names = found
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "classes.txt").write_text("\n".join(names) + "\n")
    return names


# ---------- pipeline ----------

def collect(src: Path, group_regex: str | None):
    records, skipped = [], []
    for jf in sorted(src.rglob("*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as e:
            skipped.append((jf, f"JSON rusak: {e}")); continue
        if "shapes" not in data:
            skipped.append((jf, "bukan format labelme")); continue

        img = find_image(jf, data)
        if img is None:
            skipped.append((jf, "gambar tidak ditemukan")); continue

        W, H = data.get("imageWidth"), data.get("imageHeight")
        if not W or not H:
            skipped.append((jf, "imageWidth/Height kosong")); continue

        shapes = []
        for sh in data["shapes"]:
            poly = shape_to_polygon(sh)
            if poly is None:
                continue
            shapes.append({"label": sh["label"], "points": poly})

        if not shapes:
            # JSON ada tapi shapes kosong = gambar LATAR yang sengaja ditandai
            # (setara "Mark Null" di Roboflow). Diteruskan sebagai contoh negatif
            # dengan berkas .txt kosong, bukan dibuang.
            records.append({
                "json": jf, "image": img, "w": W, "h": H, "shapes": [],
                "group": group_key(jf.stem, group_regex), "background": True,
            })
            continue

        records.append({
            "json": jf, "image": img, "w": W, "h": H, "shapes": shapes,
            "group": group_key(jf.stem, group_regex), "background": False,
        })
    return records, skipped


VAL = "val"  # diubah jadi "valid" bila --roboflow-style


def split_groups(records, ratios, seed):
    by_group = defaultdict(list)
    for r in records:
        by_group[r["group"]].append(r)

    keys = sorted(by_group)
    random.Random(seed).shuffle(keys)

    n = len(keys)
    n_tr = int(round(n * ratios[0]))
    n_va = int(round(n * ratios[1]))
    # sisanya ke test, supaya total selalu pas
    parts = {"train": keys[:n_tr], VAL: keys[n_tr:n_tr + n_va], "test": keys[n_tr + n_va:]}
    return {k: [r for g in v for r in by_group[g]] for k, v in parts.items()}, by_group


def write_split(name, recs, dst, names, copy_mode):
    img_dir = dst / name / "images"
    lbl_dir = dst / name / "labels"
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)

    counter = Counter()
    clipped = 0
    for r in recs:
        # nama unik: cegah tabrakan kalau ada subfolder dengan nama file sama
        stem = r["json"].stem
        target_img = img_dir / (stem + r["image"].suffix.lower())
        i = 1
        while target_img.exists():
            target_img = img_dir / f"{stem}__{i}{r['image'].suffix.lower()}"
            i += 1

        if copy_mode == "symlink":
            os.symlink(r["image"].resolve(), target_img)
        else:
            shutil.copy2(r["image"], target_img)

        lines = []
        W, H = r["w"], r["h"]
        for sh in r["shapes"]:
            cid = names.index(sh["label"])
            coords = []
            for x, y in sh["points"]:
                nx, ny = x / W, y / H
                if not (0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0):
                    clipped += 1
                nx = min(max(nx, 0.0), 1.0)
                ny = min(max(ny, 0.0), 1.0)
                coords += [f"{nx:.6f}", f"{ny:.6f}"]
            lines.append(str(cid) + " " + " ".join(coords))
            counter[sh["label"]] += 1

        # Gambar latar -> berkas benar-benar kosong (0 byte), bukan berisi baris kosong.
        (lbl_dir / (target_img.stem + ".txt")).write_text(
            ("\n".join(lines) + "\n") if lines else "")

    return counter, clipped


def main():
    ap = argparse.ArgumentParser(description="labelme/AnyLabeling JSON -> YOLO-seg dataset")
    ap.add_argument("--src", required=True, type=Path, help="folder berisi gambar + JSON")
    ap.add_argument("--dst", required=True, type=Path, help="folder output dataset")
    ap.add_argument("--ratios", nargs=3, type=float, default=[0.8, 0.1, 0.1],
                    metavar=("TRAIN", "VAL", "TEST"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--group-regex", default=None,
                    help=r"Regex dengan 1 grup, dicocokkan ke nama file tanpa ekstensi. "
                         r"Contoh: '^(.*?)_\d+$' -> botol_a_01, botol_a_02 jadi satu grup.")
    ap.add_argument("--classes", type=Path, default=None,
                    help="File berisi urutan kelas (1 per baris). Kalau ada, ini yang dipakai.")
    ap.add_argument("--symlink", action="store_true",
                    help="Symlink gambar, bukan copy (hemat disk).")
    ap.add_argument("--roboflow-style", action="store_true",
                    help="Pakai layout ala Roboflow: folder 'valid' (bukan 'val') "
                         "dan data.yaml gaya lama (nc + names list).")
    ap.add_argument("--dry-run", action="store_true", help="Tampilkan rencana saja.")
    a = ap.parse_args()

    if abs(sum(a.ratios) - 1.0) > 1e-6:
        sys.exit(f"[ERROR] Rasio harus berjumlah 1.0, sekarang {sum(a.ratios)}")
    if not a.src.exists():
        sys.exit(f"[ERROR] Folder tidak ada: {a.src}")

    print(f"[1/4] Memindai {a.src} ...")
    records, skipped = collect(a.src, a.group_regex)
    if not records:
        sys.exit("[ERROR] Tidak ada anotasi valid ditemukan.")

    for jf, why in skipped:
        print(f"  [SKIP] {jf.name}: {why}")
    print(f"  {len(records)} gambar berlabel, {len(skipped)} dilewati")

    n_bg = sum(1 for r in records if r.get("background"))
    if n_bg:
        pct = n_bg / len(records) * 100
        note = "  <- Ultralytics menyarankan sekitar 0-10%" if pct > 10 else ""
        print(f"  {n_bg} gambar latar (tanpa objek) = {pct:.1f}% dari dataset{note}")

    names = resolve_classes(records, a.dst, a.classes)
    print(f"[2/4] {len(names)} kelas: {names}")

    global VAL
    if a.roboflow_style:
        VAL = "valid"
    splits, by_group = split_groups(records, a.ratios, a.seed)
    print(f"[3/4] {len(by_group)} grup objek -> split (seed={a.seed})")
    for k, v in splits.items():
        ng = len({r['group'] for r in v})
        print(f"  {k:5s}: {ng:4d} grup | {len(v):5d} gambar")

    if a.dry_run:
        print("\n[DRY RUN] Tidak ada file ditulis.")
        return

    print("[4/4] Menulis dataset ...")
    mode = "symlink" if a.symlink else "copy"
    per_split = {}
    total_clipped = 0
    for k, v in splits.items():
        c, clipped = write_split(k, v, a.dst, names, mode)
        per_split[k] = c
        total_clipped += clipped

    hdr = f"# labelme2yoloseg.py | seed={a.seed} | group_regex={a.group_regex!r}\n"
    if a.roboflow_style:
        yaml = (hdr +
                f"train: ../train/images\n"
                f"val: ../{VAL}/images\n"
                f"test: ../test/images\n"
                f"nc: {len(names)}\n"
                f"names: {names}\n")
    else:
        yaml = (hdr +
                f"path: {a.dst.resolve()}\n"
                f"train: train/images\n"
                f"val: {VAL}/images\n"
                f"test: test/images\n\n"
                f"nc: {len(names)}\n"
                f"names:\n" + "".join(f"  {i}: {n}\n" for i, n in enumerate(names)))
    (a.dst / "data.yaml").write_text(yaml)

    print("\n=== Distribusi kelas per split ===")
    w = max(len(n) for n in names) + 2
    print(f"{'kelas':<{w}}{'train':>8}{'val':>8}{'test':>8}{'total':>8}")
    for n in names:
        tr, va, te = (per_split[s][n] for s in ("train", VAL, "test"))
        flag = "  <-- val/test kosong" if (va == 0 or te == 0) else ""
        print(f"{n:<{w}}{tr:>8}{va:>8}{te:>8}{tr+va+te:>8}{flag}")

    if total_clipped:
        print(f"\n[WARN] {total_clipped} titik di luar batas gambar, sudah di-clamp ke 0-1.")

    print(f"\nSelesai. data.yaml -> {a.dst / 'data.yaml'}")
    print(f"Latih dengan:\n  yolo segment train data={a.dst/'data.yaml'} model=yolo26n-seg.pt imgsz=640")


if __name__ == "__main__":
    main()
