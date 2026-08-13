"""
Uji ekspor YOLO.

Nilai acuan di sini diambil dari perbandingan langsung terhadap
FormatExporter.export_to_yolo milik AnyLabeling: keluarannya byte-identik untuk
mode segmentation maupun detection, termasuk rectangle yang dijadikan 4 sudut
dan poligon yang diringkas menjadi bounding box.
"""
from __future__ import annotations

import io
import json
import zipfile

import cv2
import numpy as np

from app.services import export as ex
from app.services import scanner


def _dataset(d, W=100, H=80):
    d.mkdir(parents=True, exist_ok=True)
    isi = [
        [{"label": "botol", "shape_type": "polygon",
          "points": [[10, 10], [90, 12], [95, 70], [15, 65]]},
         {"label": "kaleng", "shape_type": "rectangle",
          "points": [[20, 20], [60, 55]]}],
        [],                                    # tanpa objek: contoh negatif
    ]
    for i, shapes in enumerate(isi):
        p = d / f"g{i}.jpg"
        cv2.imwrite(str(p), np.full((H, W, 3), 50 + i * 40, np.uint8))
        p.with_suffix(".json").write_text(json.dumps({
            "version": "0.4.36", "flags": {}, "shapes": shapes,
            "imagePath": p.name, "imageData": None,
            "imageHeight": H, "imageWidth": W}))
    return d


def test_peta_kelas_terurut_mulai_nol(tmp_path):
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    assert ex.peta_kelas(items) == {"botol": 0, "kaleng": 1}


def test_baris_segmentasi_sama_dengan_anylabeling(tmp_path):
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    peta = ex.peta_kelas(items)
    baris = ex.baris_yolo(items[0], peta, True)
    assert baris == [
        "0 0.100000 0.125000 0.900000 0.150000 0.950000 0.875000 0.150000 0.812500",
        # rectangle -> kiri-atas, kanan-atas, kanan-bawah, kiri-bawah
        "1 0.200000 0.250000 0.600000 0.250000 0.600000 0.687500 0.200000 0.687500",
    ]


def test_baris_deteksi_sama_dengan_anylabeling(tmp_path):
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    peta = ex.peta_kelas(items)
    assert ex.baris_yolo(items[0], peta, False) == [
        "0 0.525000 0.500000 0.850000 0.750000",     # poligon -> bbox
        "1 0.400000 0.468750 0.400000 0.437500",
    ]


def test_gambar_tanpa_objek_dapat_label_kosong(tmp_path):
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    peta = ex.peta_kelas(items)
    assert ex.baris_yolo(items[1], peta, True) == []


def test_zip_bertata_letak_ultralytics(tmp_path):
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    data = ex.zip_yolo(items, "ds", True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        isi = set(z.namelist())
        assert {"images/g0.jpg", "images/g1.jpg",
                "labels/g0.txt", "labels/g1.txt",
                "classes.txt", "data.yaml", "RINGKASAN.txt"} <= isi
        assert z.read("classes.txt").decode() == "botol\nkaleng\n"
        y = z.read("data.yaml").decode()
        assert "nc: 2" in y and "0: botol" in y and "1: kaleng" in y
        # gambar tanpa objek tetap punya berkas label, isinya kosong
        assert z.read("labels/g1.txt").decode() == ""


def test_zip_tanpa_gambar(tmp_path):
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    with zipfile.ZipFile(io.BytesIO(ex.zip_yolo(items, "ds", True, False))) as z:
        assert not [n for n in z.namelist() if n.startswith("images/")]
        assert "labels/g0.txt" in z.namelist()


def test_ringkasan(tmp_path):
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    r = ex.ringkasan(items, True)
    assert r["gambar"] == 2 and r["objek"] == 2 and r["kelas"] == 2
    assert r["tanpa_objek"] == 1
    assert r["nama_kelas"] == ["botol", "kaleng"]
