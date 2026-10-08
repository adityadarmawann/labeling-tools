"""
Ekspor sebuah VERSI HIGOLAB (YOLO txt per split) ke COCO PER-SPLIT —
tiap folder split berisi gambarnya + satu `_annotations.coco.json`. Inilah
bentuk yang dibutuhkan RF-DETR (`dataset_dir` dengan train/valid/test).

TERPISAH dari export.py (yang membuat satu instances.json dari shapes untuk
rute /ekspor). Di sini sumbernya YOLO txt milik versi — sudah ter-split,
ter-augmentasi, dan beku — jadi konversinya langsung txt->COCO.

Konvensi kategori: 1-indexed dari daftar kelas PENUH di data.yaml, SAMA di
semua split (category_id konsisten lintas train/valid/test — lihat catatan
pitfall di export.coco_dict). RF-DETR membaca jumlah kelas dari categories.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..config import IMG_EXT

SPLIT = ("train", "valid", "test")


def _names(versi_dir: Path) -> list[str]:
    """Daftar nama kelas urut indeks dari data.yaml versi."""
    import yaml

    y = yaml.safe_load((Path(versi_dir) / "data.yaml").read_text(encoding="utf-8")) or {}
    n = y.get("names") or []
    if isinstance(n, dict):                       # {0:'a',1:'b'} -> list urut
        n = [n[k] for k in sorted(n, key=lambda x: int(x))]
    return [str(x) for x in n]


def _ukuran(p: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(p) as im:
        return int(im.width), int(im.height)


def _bbox_area_poligon(koord: list[float]) -> tuple[list[float], float]:
    """koord = [x1,y1,x2,y2,...] absolut -> (bbox xywh, luas shoelace)."""
    xs = koord[0::2]
    ys = koord[1::2]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    a = 0.0
    n = len(xs)
    for i in range(n):
        j = (i + 1) % n
        a += xs[i] * ys[j] - xs[j] * ys[i]
    return [xmin, ymin, xmax - xmin, ymax - ymin], abs(a) / 2.0


def _anotasi_dari_label(baris: str, W: int, H: int, n_kelas: int):
    """Satu baris YOLO -> (category_id, bbox xywh abs, area, segmentation) atau
    None kalau tak sah. Dukung deteksi (cls cx cy w h) & poligon (cls x1 y1 …)."""
    t = baris.split()
    if len(t) < 5:
        return None
    try:
        cls = int(float(t[0]))
        vals = [float(x) for x in t[1:]]
    except ValueError:
        return None
    cid = cls + 1                                 # 1-indexed
    if cid < 1 or cid > n_kelas:
        return None
    if len(vals) == 4:                            # deteksi: cx cy w h ternormalisasi
        cx, cy, w, h = vals
        x, y, bw, bh = (cx - w / 2) * W, (cy - h / 2) * H, w * W, h * H
        return cid, [x, y, bw, bh], bw * bh, []
    if len(vals) >= 6 and len(vals) % 2 == 0:     # poligon: pasangan ternormalisasi
        koord = [vals[k] * (W if k % 2 == 0 else H) for k in range(len(vals))]
        bbox, area = _bbox_area_poligon(koord)
        return cid, bbox, area, [[round(v, 2) for v in koord]]
    return None


def versi_ke_coco(versi_dir: Path, out_dir: Path, *, salin_gambar: bool = True) -> dict:
    """Konversi versi -> COCO per-split di out_dir. Kembalikan ringkasan
    {split: {gambar, anotasi}, kelas: [...]}.

    salin_gambar=True menyalin gambar ke out_dir/<split>/ (RF-DETR mencari
    gambar di samping _annotations.coco.json). False hanya menulis json (dipakai
    saat gambar sudah di tempat / untuk uji cepat)."""
    versi_dir, out_dir = Path(versi_dir), Path(out_dir)
    names = _names(versi_dir)
    kategori = [{"id": i + 1, "name": n, "supercategory": "none"}
                for i, n in enumerate(names)]
    ringkas: dict = {"kelas": names}

    for split in SPLIT:
        imdir = versi_dir / split / "images"
        lbdir = versi_dir / split / "labels"
        if not imdir.is_dir():
            continue
        tujuan = out_dir / split
        tujuan.mkdir(parents=True, exist_ok=True)
        images, anns = [], []
        gid = aid = 0
        for ip in sorted(imdir.iterdir()):
            if ip.suffix.lower() not in IMG_EXT:
                continue
            try:
                W, H = _ukuran(ip)
            except Exception:                     # noqa: BLE001 — gambar rusak dilewati
                continue
            gid += 1
            if salin_gambar:
                shutil.copy2(ip, tujuan / ip.name)
            images.append({"id": gid, "file_name": ip.name,
                           "width": W, "height": H, "license": 1})
            lp = lbdir / f"{ip.stem}.txt"
            if not lp.exists():
                continue                          # gambar tanpa label = contoh negatif (sah)
            for baris in lp.read_text().splitlines():
                hasil = _anotasi_dari_label(baris, W, H, len(names))
                if hasil is None:
                    continue
                cid, bbox, area, seg = hasil
                aid += 1
                anns.append({
                    "id": aid, "image_id": gid, "category_id": cid,
                    "bbox": [round(v, 2) for v in bbox], "area": round(area, 2),
                    "iscrowd": 0, "segmentation": seg})
        doc = {"info": {"description": f"HIGOLAB versi {versi_dir.name}"},
               "licenses": [{"id": 1, "name": "Unknown", "url": ""}],
               "images": images, "annotations": anns, "categories": kategori}
        (tujuan / "_annotations.coco.json").write_text(
            json.dumps(doc, ensure_ascii=False))
        ringkas[split] = {"gambar": len(images), "anotasi": len(anns)}
    return ringkas
