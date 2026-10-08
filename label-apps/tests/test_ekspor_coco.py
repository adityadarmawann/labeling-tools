"""Eksportir versi -> COCO per-split (untuk RF-DETR). Tanpa jaringan/GPU."""
from __future__ import annotations

import json
from pathlib import Path

from app.services import ekspor_coco


def _gambar(p: Path, w: int, h: int):
    from PIL import Image
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (w, h), (30, 30, 30)).save(p, "JPEG")


def _versi(tmp: Path, names) -> Path:
    vd = tmp / ".versi" / "v1"
    (vd).mkdir(parents=True, exist_ok=True)
    (vd / "data.yaml").write_text(
        "train: ../train/images\nval: ../valid/images\n"
        f"nc: {len(names)}\nnames: [{', '.join(repr(n) for n in names)}]\n")
    # train: satu deteksi (kelas 0) + satu gambar TANPA label (negatif)
    _gambar(vd / "train" / "images" / "img0.jpg", 100, 200)
    (vd / "train" / "labels").mkdir(parents=True, exist_ok=True)
    (vd / "train" / "labels" / "img0.txt").write_text("0 0.5 0.5 0.4 0.2\n")
    _gambar(vd / "train" / "images" / "img1.jpg", 100, 200)  # tanpa label
    # valid: satu poligon (kelas 1)
    _gambar(vd / "valid" / "images" / "img2.jpg", 100, 200)
    (vd / "valid" / "labels").mkdir(parents=True, exist_ok=True)
    (vd / "valid" / "labels" / "img2.txt").write_text(
        "1 0.1 0.1 0.9 0.1 0.9 0.9 0.1 0.9\n")
    return vd


def test_coco_struktur_dan_kategori_1_indexed(tmp_path):
    vd = _versi(tmp_path, ["a", "b"])
    out = tmp_path / "coco"
    r = ekspor_coco.versi_ke_coco(vd, out)
    assert r["kelas"] == ["a", "b"]
    assert r["train"] == {"gambar": 2, "anotasi": 1}   # img1 negatif -> 0 anotasi
    assert r["valid"] == {"gambar": 1, "anotasi": 1}

    tr = json.loads((out / "train" / "_annotations.coco.json").read_text())
    assert [(c["id"], c["name"]) for c in tr["categories"]] == [(1, "a"), (2, "b")]
    # gambar tersalin ke samping json
    assert (out / "train" / "img0.jpg").exists()
    assert (out / "train" / "img1.jpg").exists()
    # img1 (negatif) ada di images tapi tak punya anotasi
    assert len(tr["images"]) == 2 and len(tr["annotations"]) == 1


def test_coco_bbox_deteksi_benar(tmp_path):
    vd = _versi(tmp_path, ["a", "b"])
    out = tmp_path / "coco"
    ekspor_coco.versi_ke_coco(vd, out)
    tr = json.loads((out / "train" / "_annotations.coco.json").read_text())
    a = tr["annotations"][0]
    # W=100,H=200; cx.5 cy.5 w.4 h.2 -> x=30 y=80 w=40 h=40
    assert a["category_id"] == 1
    assert a["bbox"] == [30.0, 80.0, 40.0, 40.0]
    assert a["area"] == 1600.0
    assert a["iscrowd"] == 0 and a["segmentation"] == []


def test_coco_poligon_bbox_dan_segmentation(tmp_path):
    vd = _versi(tmp_path, ["a", "b"])
    out = tmp_path / "coco"
    ekspor_coco.versi_ke_coco(vd, out)
    va = json.loads((out / "valid" / "_annotations.coco.json").read_text())
    a = va["annotations"][0]
    # poligon (0.1..0.9) pada 100x200 -> extent x10 y20 w80 h160
    assert a["category_id"] == 2            # kelas 1 -> category_id 2 (konsisten lintas split)
    assert a["bbox"] == [10.0, 20.0, 80.0, 160.0]
    assert a["segmentation"] == [[10.0, 20.0, 90.0, 20.0, 90.0, 180.0, 10.0, 180.0]]
    assert a["area"] > 0


def test_coco_kategori_konsisten_lintas_split(tmp_path):
    """category_id untuk kelas yang sama HARUS sama di train & valid (pitfall
    COCO per-split)."""
    vd = _versi(tmp_path, ["a", "b"])
    out = tmp_path / "coco"
    ekspor_coco.versi_ke_coco(vd, out)
    tr = json.loads((out / "train" / "_annotations.coco.json").read_text())
    va = json.loads((out / "valid" / "_annotations.coco.json").read_text())
    assert tr["categories"] == va["categories"]


def test_coco_names_dict_terurut(tmp_path):
    """data.yaml names bisa berupa dict {0:'a',1:'b'} — harus terurut indeks."""
    vd = _versi(tmp_path, ["a", "b"])
    (vd / "data.yaml").write_text(
        "train: ../train/images\nnc: 2\nnames:\n  0: a\n  1: b\n")
    out = tmp_path / "coco"
    ekspor_coco.versi_ke_coco(vd, out)
    tr = json.loads((out / "train" / "_annotations.coco.json").read_text())
    assert [c["name"] for c in tr["categories"]] == ["a", "b"]
