"""
Membaca folder dataset dan menilai anotasinya.

Mendukung dua tata letak:
  - labelme / AnyLabeling : <nama>.jpg + <nama>.json bersebelahan
  - YOLO                  : images/ + labels/ (+ classes.txt)

Penilaian di inspect() sengaja konservatif: yang dilaporkan hanya hal yang
hampir pasti salah (mask terlalu kecil, titik di luar gambar, poligon dengan
titik terlalu sedikit), bukan selera. Temuan palsu membuat orang berhenti
mempercayai papan periksa.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import cv2
import numpy as np

from ..config import IMG_EXT


def item_key(item: dict) -> str:
    """Kunci stabil per gambar untuk penamaan berkas thumbnail."""
    return str(abs(hash(str(item["img"].resolve()))))


def poly_area(p: np.ndarray) -> float:
    x, y = p[:, 0], p[:, 1]
    return abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))) / 2.0


def read_json(jp: Path):
    """Baca anotasi labelme/AnyLabeling. Rectangle diubah jadi 4 titik."""
    d = json.loads(Path(jp).read_text(encoding="utf-8"))
    W, H = d.get("imageWidth"), d.get("imageHeight")
    shapes = []
    for s in d.get("shapes", []):
        pts = s.get("points") or []
        if s.get("shape_type") == "rectangle" and len(pts) == 2:
            (x1, y1), (x2, y2) = pts
            pts = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
        if len(pts) < 3:
            continue
        shapes.append({"label": s.get("label"),
                       "type": s.get("shape_type", "polygon"),
                       "pts": np.array(pts, np.float32)})
    return shapes, W, H


def read_yolo(tp: Path, W: int, H: int, names: dict):
    """Baca satu berkas label YOLO, baik format bbox (5 kolom) maupun poligon."""
    shapes = []
    if not os.path.exists(tp):
        return shapes
    for line in Path(tp).read_text().strip().splitlines():
        p = line.split()
        if len(p) < 5:
            continue
        cid = int(float(p[0]))
        v = [float(x) for x in p[1:]]
        if len(v) == 4:
            cx, cy, bw, bh = v
            x1, y1 = (cx - bw / 2) * W, (cy - bh / 2) * H
            x2, y2 = (cx + bw / 2) * W, (cy + bh / 2) * H
            pts = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
        else:
            pts = [[v[i] * W, v[i + 1] * H] for i in range(0, len(v) - 1, 2)]
        if len(pts) < 3:
            continue
        shapes.append({"label": names.get(cid, str(cid)), "type": "polygon",
                       "pts": np.array(pts, np.float32)})
    return shapes


def inspect(shapes, W, H, has_ann=False) -> list[str]:
    if not shapes:
        return ["latar (tanpa objek)"] if has_ann else ["belum dilabeli"]
    out, frame = [], (W or 1) * (H or 1)
    for s in shapes:
        if s["label"] is None:
            out.append("label kosong")
        if len(s["pts"]) < 8 and s["type"] != "rectangle":
            out.append(f"hanya {len(s['pts'])} titik")
        a = poly_area(s["pts"])
        if a / frame < 0.002:
            out.append("mask sangat kecil")
        if a / frame > 0.92:
            out.append("mask memenuhi frame")
        x, y = s["pts"][:, 0], s["pts"][:, 1]
        if (x < -1).any() or (y < -1).any() or (x > W + 1).any() or (y > H + 1).any():
            out.append("titik di luar gambar")
    return sorted(set(out))


def severity(it: dict) -> str:
    """ok | warn | stop | bg — menentukan warna rel di kartu dan strip atas."""
    if not it["shapes"]:
        return "bg" if "latar (tanpa objek)" in it["issues"] else "stop"
    return "warn" if it["issues"] else "ok"


def _scan_yolo(src: Path):
    items, names = [], {}
    cf = src / "classes.txt"
    if cf.exists():
        names = {i: n.strip() for i, n in enumerate(cf.read_text().splitlines()) if n.strip()}
    for ip in sorted(p for p in (src / "images").iterdir() if p.suffix.lower() in IMG_EXT):
        im = cv2.imread(str(ip))
        if im is None:
            continue
        H, W = im.shape[:2]
        tp = src / "labels" / (ip.stem + ".txt")
        sh = read_yolo(tp, W, H, names)
        items.append({"img": ip, "shapes": sh, "W": W, "H": H,
                      "issues": inspect(sh, W, H, tp.exists())})
    return items, names


def _scan_labelme(src: Path):
    items, seen, broken = [], set(), set()
    for jp in sorted(src.rglob("*.json")):
        try:
            sh, W, H = read_json(jp)
        except Exception:
            broken.add(jp.stem)
            continue
        ip = None
        for e in IMG_EXT:
            for c in (jp.with_suffix(e), jp.with_suffix(e.upper())):
                if c.exists():
                    ip = c
                    break
            if ip:
                break
        if not ip:
            continue
        seen.add(ip.resolve())
        items.append({"img": ip, "shapes": sh, "W": W, "H": H,
                      "issues": inspect(sh, W, H, True)})

    # gambar yang belum punya anotasi sama sekali
    for ip in sorted(p for p in src.rglob("*") if p.suffix.lower() in IMG_EXT):
        if ip.resolve() in seen:
            continue
        im = cv2.imread(str(ip))
        if im is None:
            continue
        H, W = im.shape[:2]
        iss = ["berkas anotasi rusak"] if ip.stem in broken else ["belum dilabeli"]
        items.append({"img": ip, "shapes": [], "W": W, "H": H, "issues": iss})
    return items, {}


def scan(src: Path):
    """Pindai folder dataset -> (items, names)."""
    src = Path(src)
    if (src / "images").is_dir() and (src / "labels").is_dir():
        items, names = _scan_yolo(src)
    else:
        items, names = _scan_labelme(src)
    items.sort(key=lambda it: it["img"].name)
    return items, names


def count_images(d: Path, cap: int = 2000) -> tuple[int, bool]:
    """
    Hitung gambar di sebuah folder, berhenti di cap. Dipakai halaman pemilih
    dataset; tanpa batas, satu folder raksasa membuat halaman itu lambat.
    """
    n = 0
    for p in d.rglob("*"):
        if p.suffix.lower() in IMG_EXT:
            n += 1
            if n >= cap:
                return n, True
    return n, False


def list_dirs(root: Path | None) -> list[dict]:
    """Subfolder berisi gambar di dalam root, untuk daftar pilihan dataset."""
    if not root or not Path(root).is_dir():
        return []
    out = []
    for d in sorted(Path(root).iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        n, more = count_images(d)
        if n:
            out.append({"nama": d.name, "path": str(d.resolve()),
                        "jumlah": n, "lebih": more})
    return out
