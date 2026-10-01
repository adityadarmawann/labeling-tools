"""
Uji ekspor keypoint ke YOLO-pose (Langkah 4).

Kontrak format (dari dataset court user + docs Ultralytics): data.yaml punya
kpt_shape [K,3] + flip_idx; tiap baris label = `cls cx cy w h (px py v)*K`
ternormalisasi; titik absen di-pad `0 0 0`; urutan slot = urutan template.
"""
from __future__ import annotations

import io
import pathlib
import zipfile

import numpy as np
import pytest

from app.services import export, tugas


# ------------------------------------------------------------ unit

def _pt(nm, x, y, v):
    return {"label": nm, "type": "point", "group_id": 0, "flags": {"v": v},
            "pts": np.array([[x, y]], np.float32)}


def test_baris_yolo_pose_format_dan_pad_absen():
    tpl = {"titik": ["a", "b", "c"], "kelas": "botol", "flip_idx": [0, 2, 1]}
    it = {"W": 100, "H": 200, "shapes": [
        _pt("a", 10, 20, 2), _pt("b", 30, 40, 1),   # c tak ditaruh -> absen
        {"label": "botol", "type": "rectangle", "group_id": 0, "flags": {},
         "pts": np.array([[5, 10], [35, 50]], np.float32)}]}
    baris = export.baris_yolo_pose(it, tpl)
    assert len(baris) == 1
    p = baris[0].split()
    assert p[0] == "0"                              # kelas
    assert len(p) == 5 + 3 * 3                       # cls + bbox + K*3
    assert p[1:5] == ["0.200000", "0.150000", "0.300000", "0.200000"]  # bbox
    assert p[5:8] == ["0.100000", "0.100000", "2"]   # a visible
    assert p[8:11] == ["0.300000", "0.200000", "1"]  # b occluded
    assert p[11:14] == ["0.000000", "0.000000", "0"] # c absen -> pad


def test_bbox_dari_hull_kalau_tanpa_rectangle():
    tpl = {"titik": ["a", "b"], "kelas": "x"}
    it = {"W": 100, "H": 100, "shapes": [_pt("a", 10, 10, 2), _pt("b", 30, 50, 2)]}
    p = export.baris_yolo_pose(it, tpl)[0].split()
    # hull (10,10)-(30,50): cx=0.2 cy=0.3 w=0.2 h=0.4
    assert p[1:5] == ["0.200000", "0.300000", "0.200000", "0.400000"]


def test_slot_ikut_template_bukan_urutan_input():
    # Titik diberi TERBALIK; ekspor harus tetap menaruhnya di slot sesuai nama.
    tpl = {"titik": ["a", "b"], "kelas": "x"}
    it = {"W": 100, "H": 100, "shapes": [_pt("b", 30, 30, 2), _pt("a", 10, 10, 2)]}
    p = export.baris_yolo_pose(it, tpl)[0].split()
    assert p[5:8] == ["0.100000", "0.100000", "2"]   # slot 0 = a
    assert p[8:11] == ["0.300000", "0.300000", "2"]  # slot 1 = b


def test_data_yaml_pose():
    y = export.data_yaml_pose(
        {"titik": ["a", "b", "c"], "kelas": "bo'tol", "flip_idx": [0, 2, 1]}, "ds")
    assert "kpt_shape: [3, 3]" in y
    assert "flip_idx: [0, 2, 1]" in y
    assert "names: ['bo''tol']" in y                 # apostrof di-escape YAML


def test_flip_idx_default_identitas():
    y = export.data_yaml_pose({"titik": ["a", "b"], "kelas": "x"}, "ds")
    assert "flip_idx: [0, 1]" in y                   # tanpa flip -> identitas


# ------------------------------------------------------------ e2e lewat HTTP

def test_ekspor_yolo_pose_zip(klien, lingkungan):
    from tests.test_data import masuk, PW_PAUL
    from tests.test_projek import _projek as buat_projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_projek(ruang, "pose-exp", n=1, label=False)
    klien.post(f"/setsrc?path={d}")
    tugas.set_skeleton(d, {"kelas": "botol", "titik": ["a", "b", "c"],
                           "edge": [[0, 1], [1, 2]], "flip_idx": [0, 2, 1]},
                       pemilik="paul")
    g = sorted(str(q) for q in d.glob("*.jpg"))[0]
    pt = lambda nm, x, y, v: {"label": nm, "shape_type": "point",
                              "points": [[x, y]], "group_id": 0,
                              "flags": {"v": v}, "text": "", "titipan": {}}
    shapes = [pt("a", 6, 6, 2), pt("b", 12, 12, 1),   # c absen
              {"label": "botol", "shape_type": "rectangle",
               "points": [[4, 4], [16, 16]], "group_id": 0,
               "flags": {}, "text": "", "titipan": {"kp_auto": True}}]
    assert klien.post("/api/simpan", json={"path": g, "shapes": shapes}).json()["ok"]

    r = klien.get("/ekspor?format=yolo-pose")
    assert r.status_code == 200, r.text[:200]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    yaml = z.read("data.yaml").decode()
    assert "kpt_shape: [3, 3]" in yaml
    assert "flip_idx: [0, 2, 1]" in yaml
    assert "names: ['botol']" in yaml
    lbls = [n for n in z.namelist()
            if "/labels/" in n and n.endswith(".txt") and z.read(n).strip()]
    assert lbls, z.namelist()
    p = z.read(lbls[0]).decode().strip().split()
    assert p[0] == "0" and len(p) == 5 + 3 * 3
    assert p[11:14] == ["0.000000", "0.000000", "0"]  # c absen di-pad


def test_ekspor_pose_tanpa_template_ditolak(klien, lingkungan):
    from tests.test_data import masuk, PW_PAUL
    from tests.test_projek import _projek as buat_projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_projek(ruang, "no-skel", n=1)
    klien.post(f"/setsrc?path={d}")
    r = klien.get("/ekspor?format=yolo-pose")
    assert r.status_code == 409 and "template keypoint" in r.text
