"""
Pre-label POSE: model pose terlatih mengisi keypoint (bentuk point + rectangle
bergabung lewat group_id) di gambar BELUM teranotasi projek kerangka.

Yang dijaga: keypoint diberi nama dari template, visibilitas dari keyakinan,
instance tanpa titik terlihat dilewati, dan output HANYA labelme .json (projek
pose datar, tak ada YOLO .txt per gambar).
"""
from __future__ import annotations

import json

from conftest import buat_dataset
from app.services import prelabel, tugas

TEMPLATE = {"kelas": "pemain", "titik": ["a", "b", "c"],
            "edge": [], "flip_idx": [0, 1, 2]}


# ---- hasil ultralytics pose palsu -------------------------------------------
class _T:
    def __init__(self, v):
        self.v = v

    def tolist(self):
        return self.v


class _Kpts:
    def __init__(self, xy, conf):
        self.xy = _T(xy)
        self.conf = _T(conf) if conf is not None else None


class _Boxes2:
    def __init__(self, xyxy):
        self.xyxy = _T(xyxy)


class _RP:
    def __init__(self, kpts, boxes=None):
        self.keypoints = kpts
        self.boxes = boxes


def _model_pose(*_a, **_k):
    # 1 instance, 3 keypoint; titik ke-3 keyakinan rendah (absen, v=0).
    return [_RP(_Kpts([[[10, 10], [20, 20], [30, 30]]], [[0.9, 0.8, 0.05]]),
                _Boxes2([[5, 5, 35, 35]]))]


def _model_pose_kosong(*_a, **_k):
    # Semua keypoint keyakinan rendah -> tak ada titik terlihat -> instance lewat.
    return [_RP(_Kpts([[[10, 10], [20, 20], [30, 30]]], [[0.01, 0.02, 0.0]]),
                _Boxes2([[5, 5, 35, 35]]))]


# ---- konversi ----------------------------------------------------------------
def test_shapes_pose_dari_hasil():
    r = _model_pose()[0]
    sh = prelabel._shapes_pose_dari_hasil(r, ["a", "b", "c"], "pemain", 80, 60)
    # 1 rectangle + 3 point
    rect = [s for s in sh if s["shape_type"] == "rectangle"]
    pts = [s for s in sh if s["shape_type"] == "point"]
    assert len(rect) == 1 and rect[0]["label"] == "pemain"
    assert rect[0]["group_id"] == 0
    assert len(pts) == 3
    assert [s["label"] for s in pts] == ["a", "b", "c"]
    assert all(s["group_id"] == 0 for s in pts)
    # visibilitas: 2, 2, 0 (keyakinan 0,9 / 0,8 / 0,05)
    assert [s["flags"]["v"] for s in pts] == [2, 2, 0]


def test_shapes_pose_instance_tanpa_titik_terlihat_dilewati():
    r = _model_pose_kosong()[0]
    sh = prelabel._shapes_pose_dari_hasil(r, ["a", "b", "c"], "pemain", 80, 60)
    assert sh == []


def test_shapes_pose_kurung_ke_batas():
    r = _RP(_Kpts([[[999, 999], [-5, -5], [10, 10]]], [[0.9, 0.9, 0.9]]),
            _Boxes2([[0, 0, 999, 999]]))
    sh = prelabel._shapes_pose_dari_hasil(r, ["a", "b", "c"], "pemain", 80, 60)
    for s in sh:
        for x, y in s["points"]:
            assert 0 <= x <= 80 and 0 <= y <= 60


# ---- loop jalankan -----------------------------------------------------------
def _siapkan(tmp_path, monkeypatch, n_img, model):
    d = buat_dataset(tmp_path / "proj", n_img, 0)
    tugas.set_skeleton(d, TEMPLATE, pemilik="darma")
    nama = sorted(p.name for p in d.glob("*.jpg"))
    tugas.tugaskan(d, "darma", "darma", nama)
    data = tugas.baca(d, "darma")
    tid = next(iter(data["tugas"]))
    assert tugas.skeleton_aktif(data)            # projek memang pose

    pt = tmp_path / "best.pt"
    pt.write_text("x")
    monkeypatch.setattr(prelabel, "_muat_model", lambda p: model)
    monkeypatch.setattr(prelabel.latih, "bobot_training",
                        lambda ds, n, jenis="best": pt)
    monkeypatch.setattr(prelabel.latih, "status",
                        lambda ds, n: {"kelas": ["pemain"]})
    monkeypatch.setattr(prelabel.tugas, "baca_projek", lambda ds, u: data)
    monkeypatch.setattr(prelabel.tugas, "tolak_tulis", lambda ds, akun, img: None)
    return d, tid


def test_jalankan_pose_isi_keypoint(tmp_path, monkeypatch):
    d, tid = _siapkan(tmp_path, monkeypatch, 3, _model_pose)
    hasil = prelabel.jalankan(d, tmp_path, tid, 1, "darma", 1000, 0.25)
    assert hasil["total"] == 3 and hasil["diproses"] == 3
    assert hasil["objek"] == 3                   # 1 instance per gambar
    for ip in d.glob("*.jpg"):
        sh = json.loads(ip.with_suffix(".json").read_text())["shapes"]
        rect = [s for s in sh if s["shape_type"] == "rectangle"]
        pts = [s for s in sh if s["shape_type"] == "point"]
        assert len(rect) == 1 and len(pts) == 3
        assert rect[0]["label"] == "pemain"
        assert [s["label"] for s in pts] == ["a", "b", "c"]
        # TIDAK ada YOLO .txt untuk projek pose datar.
        assert not ip.with_suffix(".txt").exists()


def test_jalankan_pose_tanpa_deteksi_tak_jadi_latar(tmp_path, monkeypatch):
    d, tid = _siapkan(tmp_path, monkeypatch, 2, _model_pose_kosong)
    hasil = prelabel.jalankan(d, tmp_path, tid, 1, "darma", 1000, 0.25)
    assert hasil["diproses"] == 0 and hasil["dilewati"] == 2
    assert not [p for p in d.glob("*.json") if not p.name.startswith(".")]
