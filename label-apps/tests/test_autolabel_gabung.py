"""
Auto-label kolaboratif -- engine merge (langkah 1): IoU, penggabungan/dedup
lintas sumber dengan prioritas label + geometri, dan koersi ke jenis projek.
"""
from __future__ import annotations

import json

import pytest

from conftest import buat_dataset
from app.services import autolabel, autolabel_gabung as ag, prelabel, tugas
from app.services.autolabel_gabung import Deteksi, _iou, gabung, ke_bentuk


def test_iou():
    assert _iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert _iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert 0.1 < _iou((0, 0, 10, 10), (5, 5, 15, 15)) < 0.2


def test_gabung_objek_sama_jadi_satu_label_prioritas_geometri():
    # Dua sumber menemukan objek yang sama (IoU tinggi). Label dari 'terlatih'
    # (prioritas), geometri poligon dari 'zeroshot' (SAM) -> kelas+bentuk berpadu.
    t = Deteksi(bbox=(10, 10, 50, 50), label="kaleng", skor=0.9, sumber="terlatih")
    z = Deteksi(bbox=(11, 11, 49, 49), label="can", skor=0.8, sumber="zeroshot",
                pts=[[10, 10], [50, 10], [50, 50], [10, 50]])
    out = gabung([z, t])
    assert len(out) == 1
    assert out[0].label == "kaleng"               # label dari terlatih
    assert out[0].pts is not None                 # geometri poligon dari zeroshot
    assert "terlatih" in out[0].sumber and "zeroshot" in out[0].sumber


def test_gabung_objek_beda_union():
    a = Deteksi(bbox=(0, 0, 20, 20), label="botol", skor=0.9, sumber="terlatih")
    b = Deteksi(bbox=(100, 100, 140, 140), label="can", skor=0.7, sumber="zeroshot")
    out = gabung([a, b])
    assert len(out) == 2                          # objek beda -> keduanya disimpan


def test_gabung_zeroshot_menutup_yang_terlewat():
    # Model terlatih cuma menemukan 1; zeroshot menemukan objek LAIN -> union 2.
    t = Deteksi(bbox=(0, 0, 20, 20), label="kaleng", skor=0.9, sumber="terlatih")
    z = Deteksi(bbox=(200, 200, 240, 260), label="carton", skor=0.6, sumber="zeroshot")
    assert len(gabung([t, z])) == 2


def test_ke_bentuk_kotak_dan_poligon():
    d = [Deteksi(bbox=(10, 10, 50, 50), label="botol", sumber="terlatih")]
    kot = ke_bentuk(d, "kotak", 100, 100)
    assert kot[0]["shape_type"] == "rectangle"
    assert kot[0]["points"] == [[10.0, 10.0], [50.0, 50.0]]

    # Poligon: pakai pts kalau ada.
    dp = [Deteksi(bbox=(10, 10, 50, 50), label="botol", sumber="zeroshot",
                  pts=[[10, 10], [50, 10], [50, 50], [10, 50]])]
    pol = ke_bentuk(dp, "poligon", 100, 100)
    assert pol[0]["shape_type"] == "polygon" and len(pol[0]["points"]) >= 4

    # Poligon tapi hanya kotak -> diubah jadi poligon 4 titik (perkiraan).
    pol2 = ke_bentuk(d, "poligon", 100, 100)
    assert pol2[0]["shape_type"] == "polygon" and len(pol2[0]["points"]) >= 4


def test_ke_bentuk_kurung_ke_batas():
    d = [Deteksi(bbox=(-5, -5, 999, 999), label="x", sumber="terlatih")]
    b = ke_bentuk(d, "kotak", 80, 60)
    for x, y in b[0]["points"]:
        assert 0 <= x <= 80 and 0 <= y <= 60


# ---- orkestrator (langkah 2) ------------------------------------------------
def _siapkan(tmp_path, monkeypatch, n_img, jenis="kotak",
             prediksi_satu=None, dari_teks=None, latih_aktif=False):
    d = buat_dataset(tmp_path / "proj", n_img, 0)
    nama = sorted(p.name for p in d.glob("*.jpg"))
    tugas.tugaskan(d, "darma", "darma", nama)
    data = tugas.baca(d, "darma")
    tid = next(iter(data["tugas"]))

    pt = tmp_path / "best.pt"; pt.write_text("x")
    monkeypatch.setattr(ag.gpu_sinyal, "latih_aktif", lambda: latih_aktif)
    monkeypatch.setattr(tugas, "baca_projek", lambda ds, u: data)
    monkeypatch.setattr(tugas, "tolak_tulis", lambda ds, akun, img: None)
    monkeypatch.setattr(tugas, "jenis_berlaku", lambda data, items=None: jenis)
    monkeypatch.setattr(prelabel.latih, "bobot_training", lambda ds, n, jenis="best": pt)
    monkeypatch.setattr(prelabel, "_muat_model", lambda p: object())
    monkeypatch.setattr(prelabel.latih, "status", lambda ds, n: {"kelas": ["kaleng"]})
    if prediksi_satu is not None:
        monkeypatch.setattr(prelabel, "_prediksi_satu", prediksi_satu)
    if dari_teks is not None:
        monkeypatch.setattr(autolabel, "dari_teks", dari_teks)
    return d, tid


def _box_shape(x1, y1, x2, y2, label):
    return {"label": label, "text": "", "points": [[x1, y1], [x2, y2]],
            "group_id": None, "shape_type": "rectangle", "flags": {}}


def test_jalankan_dua_sumber_merge(tmp_path, monkeypatch):
    # terlatih & zeroshot menemukan objek SAMA -> 1 bentuk, label terlatih.
    pred = lambda m, it, conf, kelas: [_box_shape(10, 10, 50, 50, "kaleng")]
    teks = lambda img, prompt, model="", ambang=0.1: [
        autolabel.Temuan(label="can", points=[[11, 11], [49, 49]],
                         bbox=(11, 11, 49, 49), skor=0.8, shape_type="rectangle")]
    d, tid = _siapkan(tmp_path, monkeypatch, 2, prediksi_satu=pred, dari_teks=teks)
    sumber = [{"jenis": "terlatih", "nomor": 1, "conf": 0.25},
              {"jenis": "zeroshot", "model": "yoloworld:latest", "prompt": ["can"]}]
    hasil = ag.jalankan(d, tmp_path, "darma", 1000, sumber, tid=tid)
    assert hasil["total"] == 2 and hasil["diproses"] == 2
    for ip in d.glob("*.jpg"):
        sh = json.loads(ip.with_suffix(".json").read_text())["shapes"]
        assert len(sh) == 1 and sh[0]["label"] == "kaleng"      # merge + prioritas
        assert json.loads(ip.with_suffix(".json").read_text())["perlu_dicek"] is True


def test_jalankan_sumber_saling_menutup(tmp_path, monkeypatch):
    # terlatih menemukan A, zeroshot menemukan B (beda) -> union 2 bentuk.
    pred = lambda m, it, conf, kelas: [_box_shape(0, 0, 20, 20, "kaleng")]
    teks = lambda img, prompt, model="", ambang=0.1: [
        autolabel.Temuan(label="carton", points=[[40, 40], [58, 58]],
                         bbox=(40, 40, 58, 58), skor=0.7, shape_type="rectangle")]
    d, tid = _siapkan(tmp_path, monkeypatch, 1, prediksi_satu=pred, dari_teks=teks)
    sumber = [{"jenis": "terlatih", "nomor": 1}, {"jenis": "zeroshot",
              "model": "yoloworld:latest", "prompt": ["carton"]}]
    ag.jalankan(d, tmp_path, "darma", 1000, sumber, tid=tid)
    sh = json.loads(sorted(d.glob("*.jpg"))[0].with_suffix(".json").read_text())["shapes"]
    assert len(sh) == 2 and {s["label"] for s in sh} == {"kaleng", "carton"}


def test_jalankan_tanpa_sumber_error(tmp_path, monkeypatch):
    d, tid = _siapkan(tmp_path, monkeypatch, 1)
    with pytest.raises(ValueError):
        ag.jalankan(d, tmp_path, "darma", 1000, [], tid=tid)


def test_jalankan_ditolak_saat_training(tmp_path, monkeypatch):
    pred = lambda m, it, conf, kelas: [_box_shape(10, 10, 50, 50, "kaleng")]
    d, tid = _siapkan(tmp_path, monkeypatch, 1, prediksi_satu=pred, latih_aktif=True)
    with pytest.raises(ag.TrainingSedangJalan):
        ag.jalankan(d, tmp_path, "darma", 1000, [{"jenis": "terlatih", "nomor": 1}], tid=tid)
    assert not [p for p in d.glob("*.json") if not p.name.startswith(".")]


def test_jalankan_pose_ditolak(tmp_path, monkeypatch):
    d, tid = _siapkan(tmp_path, monkeypatch, 1, jenis="kerangka")
    with pytest.raises(ValueError):
        ag.jalankan(d, tmp_path, "darma", 1000,
                    [{"jenis": "terlatih", "nomor": 1}], tid=tid)


def test_jalankan_batch_scope(tmp_path, monkeypatch):
    pred = lambda m, it, conf, kelas: [_box_shape(10, 10, 50, 50, "kaleng")]
    d, tid = _siapkan(tmp_path, monkeypatch, 3, prediksi_satu=pred)
    hasil = ag.jalankan(d, tmp_path, "darma", 1000,
                        [{"jenis": "terlatih", "nomor": 1}], batch="")
    assert hasil["total"] == 3 and hasil["diproses"] == 3


def test_pt_eksternal_slug():
    p = ag.pt_eksternal(".", "My Model!.pt")
    assert p.name == "My-Model.pt" and ag.DIR_EKSTERNAL in str(p)


# ---- rute (langkah 4) -------------------------------------------------------
def test_rute_info_sumber(klien, lingkungan):
    from conftest import masuk, PW_PAUL
    masuk(klien, "paul", PW_PAUL)
    d = buat_dataset(lingkungan["ruang"] / "info", 2, 0)
    klien.post(f"/setsrc?path={d}")
    j = klien.get("/api/autolabel/info").json()
    assert j["ok"] is True
    for k in ("teks", "terlatih", "eksternal", "claude_aktif"):
        assert k in j
    assert j["terlatih"] == [] and j["eksternal"] == []       # belum ada model


def test_rute_mulai_sumber_ensemble(klien, lingkungan, monkeypatch):
    from conftest import masuk, PW_PAUL
    from app.services import gpu_sinyal
    masuk(klien, "paul", PW_PAUL)
    d = buat_dataset(lingkungan["ruang"] / "ens", 2, 0)
    klien.post(f"/setsrc?path={d}")
    monkeypatch.setattr(gpu_sinyal, "latih_aktif", lambda: False)
    tangkap = {}

    def fake(ds, up, akun, batas, sumber, tid=None, batch=None, iou_ambang=0.6, batal=None):
        tangkap["sumber"] = sumber
        return {"total": 0, "diproses": 0, "objek": 0, "dilewati": 0}
    monkeypatch.setattr(ag, "jalankan", fake)

    r = klien.post("/api/autolabel/mulai", json={
        "batch": "", "sumber": [
            {"jenis": "zeroshot", "model": "yoloworld:latest", "prompt": "botol"},
            {"jenis": "terlatih", "nomor": 1}]})
    j = r.json()
    assert j["ok"] is True and j["sumber"] == 2


def test_rute_model_unggah(klien, lingkungan, monkeypatch):
    from conftest import masuk, PW_PAUL
    masuk(klien, "paul", PW_PAUL)
    d = buat_dataset(lingkungan["ruang"] / "up", 1, 0)
    klien.post(f"/setsrc?path={d}")
    monkeypatch.setattr(tugas, "boleh_unggah", lambda data, akun: True)
    r = klien.put("/api/autolabel/model-unggah?nama=best%20model.pt", content=b"PKfake-weights")
    j = r.json()
    assert j["ok"] is True and j["nama"] == "best-model"
    assert ag.pt_eksternal(d, "best model.pt").exists()


def test_rute_model_unggah_gerbang(klien, lingkungan, monkeypatch):
    from conftest import masuk, PW_PAUL
    masuk(klien, "paul", PW_PAUL)
    d = buat_dataset(lingkungan["ruang"] / "up2", 1, 0)
    klien.post(f"/setsrc?path={d}")
    monkeypatch.setattr(tugas, "boleh_unggah", lambda data, akun: False)
    r = klien.put("/api/autolabel/model-unggah?nama=x.pt", content=b"x")
    assert r.json()["ok"] is False
