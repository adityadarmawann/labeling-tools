"""
Pre-label batch: prediksi model terlatih mengisi gambar BELUM teranotasi.

Yang dijaga: format tulis sama dengan simpan manual, gambar berlabel manusia
TAK pernah ditimpa, dan gambar tanpa deteksi TIDAK diam-diam jadi latar.
"""
from __future__ import annotations

import json

import numpy as np

from conftest import buat_dataset, PW_PAUL
from app.services import prelabel, scanner, tugas


# ---- hasil ultralytics palsu -------------------------------------------------
class _Boxes:
    def __init__(self, xyxy, cls):
        self.xyxy = np.array(xyxy, float).reshape(-1, 4) if xyxy else np.zeros((0, 4))
        self.cls = np.array(cls, float)


class _Masks:
    def __init__(self, xy):
        self.xy = xy


class _R:
    def __init__(self, names, boxes=None, masks=None):
        self.names, self.boxes, self.masks = names, boxes, masks


def _model_kotak(*_a, **_k):
    """Model palsu: tiap gambar -> satu kotak kelas 0 'botol'."""
    return [_R({0: "botol"}, boxes=_Boxes([[10, 10, 50, 50]], [0]))]


def _model_kosong(*_a, **_k):
    return [_R({0: "botol"}, boxes=_Boxes([], []))]


# ---- konversi ----------------------------------------------------------------
def test_shapes_dari_hasil_kotak():
    r = _R({0: "botol"}, boxes=_Boxes([[10, 10, 50, 50]], [0]))
    sh = prelabel._shapes_dari_hasil(r, ["botol"], 80, 60)
    assert len(sh) == 1
    assert sh[0]["label"] == "botol"
    assert sh[0]["shape_type"] == "rectangle"
    assert sh[0]["points"] == [[10.0, 10.0], [50.0, 50.0]]


def test_shapes_dari_hasil_poligon_dan_kurung():
    # titik di luar gambar harus dikurung ke dalam batas.
    xy = [np.array([[5, 5], [999, 5], [999, 999], [5, 50]], float)]
    r = _R({0: "botol"}, boxes=_Boxes([[5, 5, 70, 50]], [0]), masks=_Masks(xy))
    sh = prelabel._shapes_dari_hasil(r, ["botol"], 80, 60)
    assert len(sh) == 1 and sh[0]["shape_type"] == "polygon"
    for x, y in sh[0]["points"]:
        assert 0 <= x <= 80 and 0 <= y <= 60


# ---- loop jalankan -----------------------------------------------------------
def _siapkan(tmp_path, monkeypatch, n_img, n_ann, model):
    d = buat_dataset(tmp_path / "proj", n_img, n_ann)
    nama = sorted(p.name for p in d.glob("*.jpg"))
    tugas.tugaskan(d, "darma", "darma", nama)
    data = tugas.baca(d, "darma")
    tid = next(iter(data["tugas"]))

    pt = tmp_path / "best.pt"
    pt.write_text("x")
    monkeypatch.setattr(prelabel, "_muat_model", lambda p: model)
    monkeypatch.setattr(prelabel.latih, "bobot_training",
                        lambda ds, n, jenis="best": pt)
    monkeypatch.setattr(prelabel.latih, "status",
                        lambda ds, n: {"kelas": ["botol"]})
    # Pakai data tugas asli; lewati resolusi pemilik/hak tulis di lingkungan tes.
    monkeypatch.setattr(prelabel.tugas, "baca_projek", lambda ds, u: data)
    monkeypatch.setattr(prelabel.tugas, "tolak_tulis", lambda ds, akun, img: None)
    return d, tid


def test_jalankan_isi_prediksi(tmp_path, monkeypatch):
    d, tid = _siapkan(tmp_path, monkeypatch, 3, 0, _model_kotak)
    hasil = prelabel.jalankan(d, tmp_path, tid, 1, "darma", 1000, 0.25)
    assert hasil["total"] == 3
    assert hasil["diproses"] == 3
    assert hasil["objek"] == 3
    for ip in d.glob("*.jpg"):
        jp = ip.with_suffix(".json")
        assert jp.exists(), ip.name
        sh = json.loads(jp.read_text())["shapes"]
        assert len(sh) == 1 and sh[0]["label"] == "botol"
        assert sh[0]["shape_type"] == "rectangle"


def test_jalankan_tanpa_deteksi_tak_jadi_latar(tmp_path, monkeypatch):
    d, tid = _siapkan(tmp_path, monkeypatch, 3, 0, _model_kosong)
    hasil = prelabel.jalankan(d, tmp_path, tid, 1, "darma", 1000, 0.25)
    assert hasil["diproses"] == 0
    assert hasil["dilewati"] == 3
    # Tak ada .json anotasi yang ditulis -> gambar tetap "belum", BUKAN latar
    # (.tugas.json internal diabaikan).
    assert not [p for p in d.glob("*.json") if not p.name.startswith(".")]


def test_jalankan_tak_menimpa_berlabel(tmp_path, monkeypatch):
    # 1 gambar sudah berlabel manusia (poligon), 2 belum.
    d, tid = _siapkan(tmp_path, monkeypatch, 3, 1, _model_kotak)
    berlabel = sorted(d.glob("*.jpg"))[0].with_suffix(".json")
    sebelum = berlabel.read_text()
    hasil = prelabel.jalankan(d, tmp_path, tid, 1, "darma", 1000, 0.25)
    # Hanya 2 yang belum teranotasi diproses.
    assert hasil["diproses"] == 2
    # Yang berlabel manusia TIDAK berubah sama sekali.
    assert berlabel.read_text() == sebelum
    sh = json.loads(berlabel.read_text())["shapes"]
    assert sh[0]["shape_type"] == "polygon"      # label manusia utuh


def test_jalankan_batas(tmp_path, monkeypatch):
    d, tid = _siapkan(tmp_path, monkeypatch, 5, 0, _model_kotak)
    hasil = prelabel.jalankan(d, tmp_path, tid, 1, "darma", batas=2, conf=0.25)
    assert hasil["total"] == 2 and hasil["diproses"] == 2


# ---- rute --------------------------------------------------------------------
def test_rute_kemajuan_dan_model(klien, lingkungan):
    from conftest import masuk
    masuk(klien, "paul", PW_PAUL)
    # kemajuan selalu bisa dijajak (kosong di awal).
    r = klien.get("/api/prelabel/kemajuan")
    assert r.status_code == 200 and r.json()["ok"] is True
    # daftar model butuh projek terbuka.
    d = lingkungan["ruang"] / "mini"
    buat_dataset(d, 2, 0)
    klien.post(f"/setsrc?path={d}")
    r = klien.get("/api/prelabel/model")
    assert r.json()["ok"] is True
    assert r.json()["model"] == []              # belum ada training


def test_rute_mulai_tanpa_model_ditolak(klien, lingkungan):
    from conftest import masuk
    masuk(klien, "paul", PW_PAUL)
    d = lingkungan["ruang"] / "mini2"
    buat_dataset(d, 2, 0)
    klien.post(f"/setsrc?path={d}")
    r = klien.post("/api/prelabel/mulai", json={"tid": "t1"})
    assert r.json()["ok"] is False
    assert "model" in r.json()["error"].lower()
