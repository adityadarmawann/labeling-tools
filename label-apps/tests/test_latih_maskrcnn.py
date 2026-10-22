"""Arsitektur Mask R-CNN (app/services/latih_maskrcnn.py + wiring di latih.py).

Pola sama dengan test_latih_rfdetr: SEAM `_latih_maskrcnn_asli` dipalsukan, jadi
suite tak pernah mengimpor torch. baca_coco diuji murni (tanpa torch)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from app.services import latih, latih_maskrcnn


@pytest.fixture(autouse=True)
def _kunci_gpu_terisolasi(tmp_path, monkeypatch):
    """Jangan rebut kunci-antre-GPU global dengan training sungguhan."""
    monkeypatch.setattr(latih_maskrcnn, "KUNCI_GPU", tmp_path / "gpu-uji.lock")


def _versi(tmp: Path, nomor: int, names, *, dengan_gambar=False) -> Path:
    vd = tmp / ".versi" / f"v{nomor}"
    (vd / "train" / "images").mkdir(parents=True, exist_ok=True)
    (vd / "data.yaml").write_text(
        "train: ../train/images\nval: ../valid/images\n"
        f"nc: {len(names)}\nnames: [{', '.join(repr(n) for n in names)}]\n")
    if dengan_gambar:
        from PIL import Image
        Image.new("RGB", (64, 64), (20, 20, 20)).save(vd / "train" / "images" / "im0.jpg")
        (vd / "train" / "labels").mkdir(parents=True, exist_ok=True)
        (vd / "train" / "labels" / "im0.txt").write_text(
            "0 0.3 0.3 0.4 0.3 0.4 0.6 0.3 0.6\n")   # poligon produk
    return vd


# ---- siapkan: preset + model + pemaksaan tugas ----------------------------
def test_siapkan_maskrcnn_pakai_preset():
    import tempfile
    d = Path(tempfile.mkdtemp())
    _versi(d, 1, ["produk", "logo"])
    isi = latih.siapkan(d, nama="t", versi_nomor=1, tugas="segment", bobot="",
                        par={}, oleh="u", arsitektur="maskrcnn",
                        maskrcnn_model="r50v2")
    assert isi["arsitektur"] == "maskrcnn" and isi["maskrcnn_model"] == "r50v2"
    assert isi["par"]["epochs"] == latih.PRESET_MASKRCNN["epochs"]
    assert isi["par"]["lr"] == latih.PRESET_MASKRCNN["lr"]


def test_siapkan_maskrcnn_model_asing_jatuh_bawaan():
    import tempfile
    d = Path(tempfile.mkdtemp())
    _versi(d, 1, ["a"])
    isi = latih.siapkan(d, nama="t", versi_nomor=1, tugas="segment", bobot="",
                        par={}, oleh="u", arsitektur="maskrcnn",
                        maskrcnn_model="raksasa")
    assert isi["maskrcnn_model"] == latih.MASKRCNN_MODEL_BAWAAN == "r50v2"


def test_siapkan_maskrcnn_pose_jadi_segment():
    import tempfile
    d = Path(tempfile.mkdtemp())
    _versi(d, 1, ["a"])
    isi = latih.siapkan(d, nama="t", versi_nomor=1, tugas="pose", bobot="",
                        par={}, oleh="u", arsitektur="maskrcnn")
    assert isi["tugas"] == "segment"                 # pose bukan untuk Mask R-CNN


def test_siapkan_maskrcnn_clamp_batas():
    import tempfile
    d = Path(tempfile.mkdtemp())
    _versi(d, 1, ["a"])
    with pytest.raises(ValueError):
        latih.siapkan(d, nama="t", versi_nomor=1, tugas="segment", bobot="",
                      par={"epochs": 99999}, oleh="u", arsitektur="maskrcnn")


def test_siapkan_yolo_tak_tersentuh():
    import tempfile
    d = Path(tempfile.mkdtemp())
    _versi(d, 1, ["a"])
    isi = latih.siapkan(d, nama="t", versi_nomor=1, tugas="segment",
                        bobot="yolo.pt", par={}, oleh="u")
    assert isi["arsitektur"] == "yolo" and isi["maskrcnn_model"] == ""


def test_jalankan_maskrcnn_pilih_worker(tmp_path, monkeypatch):
    _versi(tmp_path, 1, ["a"])
    latih._tulis(tmp_path, 1, {"nomor": 1, "versi": 1, "arsitektur": "maskrcnn",
                               "maskrcnn_model": "r50v2", "tugas": "segment",
                               "keadaan": "antre", "pid": 0})
    rekam = {}
    class FakePopen:
        def __init__(self, cmd, **k): rekam["cmd"] = cmd
        def poll(self): return None
        @property
        def pid(self): return 4321
    monkeypatch.setattr(latih.subprocess, "Popen", FakePopen)
    latih.jalankan(tmp_path, 1)
    assert "app.services.latih_maskrcnn" in rekam["cmd"]


def test_siap_maskrcnn_dua_cabang(monkeypatch):
    asli = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda n: object() if n == "torchvision" else asli(n))
    assert latih.siap_maskrcnn()[0] is True
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda n: None if n == "torchvision" else asli(n))
    assert latih.siap_maskrcnn()[0] is False


# ---- baca_coco MURNI (tanpa torch): sinyal partial-mask -------------------
def test_baca_coco_partial_mask(tmp_path):
    d = tmp_path / "coco" / "train"
    d.mkdir(parents=True)
    import cv2
    import numpy as np
    cv2.imwrite(str(d / "g0.jpg"), np.zeros((64, 64, 3), np.uint8))
    json.dump({
        "images": [{"id": 0, "file_name": "g0.jpg", "width": 64, "height": 64}],
        "annotations": [
            {"id": 1, "image_id": 0, "category_id": 1, "bbox": [5, 5, 20, 30],
             "area": 600, "iscrowd": 0,
             "segmentation": [[5, 5, 25, 5, 25, 35, 5, 35]]},      # produk: poligon
            {"id": 2, "image_id": 0, "category_id": 2, "bbox": [30, 30, 10, 10],
             "area": 100, "iscrowd": 0, "segmentation": []},       # logo: kotak saja
        ],
        "categories": [{"id": 1, "name": "produk"}, {"id": 2, "name": "logo"}],
    }, open(d / "_annotations.coco.json", "w"))
    contoh, nama = latih_maskrcnn.baca_coco(tmp_path / "coco", "train")
    assert nama == ["produk", "logo"]
    assert len(contoh) == 1
    c = contoh[0]
    assert c["labels"] == [1, 2]
    assert c["segs"][0] is not None, "produk harus punya segmentasi (mask)"
    assert c["segs"][1] is None, "logo kotak-saja -> tak ada mask (partial supervision)"
    assert c["boxes"][1] == [30, 30, 40, 40], "kotak logo xywh -> xyxy"


# ---- SEAM: worker main() tanpa mengimpor torch ---------------------------
def test_worker_main_fake_train(tmp_path, monkeypatch):
    _versi(tmp_path, 1, ["produk", "logo"], dengan_gambar=True)
    latih._tulis(tmp_path, 1, {
        "nomor": 1, "versi": 1, "arsitektur": "maskrcnn", "maskrcnn_model": "r50v2",
        "tugas": "segment", "par": dict(latih.PRESET_MASKRCNN), "keadaan": "antre",
        "pid": 0})

    def fake_asli(coco_dir, dl, model, par, lapor, resume=None):
        assert (Path(coco_dir) / "train" / "_annotations.coco.json").exists()
        assert resume is None, "training segar tak boleh resume"
        (Path(dl) / "weights").mkdir(parents=True, exist_ok=True)
        (Path(dl) / "maskrcnn").mkdir(parents=True, exist_ok=True)
        (Path(dl) / "weights" / "best.pt").write_bytes(b"PK\x03\x04best")
        (Path(dl) / "weights" / "last.pt").write_bytes(b"PK\x03\x04last")
        (Path(dl) / "maskrcnn" / "last.ckpt").write_bytes(b"PK\x03\x04ckpt")
        (Path(dl) / "results.csv").write_text(
            "epoch,time,metrics/mAP50-95(B)\n1,5.0,0.42\n")
        lapor(pesan="fake train")
        return {"epoch": int(par["epochs"]), "map": 0.42}

    monkeypatch.setattr(latih_maskrcnn, "_latih_maskrcnn_asli", fake_asli)
    monkeypatch.setattr("sys.argv", ["latih_maskrcnn", str(tmp_path), "1"])
    rc = latih_maskrcnn.main()
    assert rc == 0

    rek = latih.baca(tmp_path, 1)
    assert rek["keadaan"] == "selesai"
    dl = latih.dir_latih(tmp_path, 1)
    assert (dl / "weights" / "best.pt").exists()
    assert (dl / "maskrcnn" / "last.ckpt").exists()
    assert (dl / "results.csv").exists()
    s = latih.status(tmp_path, 1)
    assert s["arsitektur"] == "maskrcnn" and s["epoch"] == 1
    assert s["maskrcnn_ckpt"] is True                # kartu tampilkan "Lanjutkan"


def test_worker_main_resume_dari_seed(tmp_path, monkeypatch):
    _versi(tmp_path, 1, ["a"], dengan_gambar=True)
    latih._tulis(tmp_path, 1, {
        "nomor": 1, "versi": 1, "arsitektur": "maskrcnn", "maskrcnn_model": "r50v2",
        "tugas": "segment", "par": dict(latih.PRESET_MASKRCNN), "keadaan": "antre",
        "pid": 0})
    dl = latih.dir_latih(tmp_path, 1)
    (dl / "maskrcnn").mkdir(parents=True, exist_ok=True)
    (dl / "maskrcnn" / "last.ckpt").write_bytes(b"PK\x03\x04seed")
    terima = {}

    def fake_asli(coco_dir, d2, model, par, lapor, resume=None):
        terima["resume"] = resume
        (Path(d2) / "weights").mkdir(parents=True, exist_ok=True)
        (Path(d2) / "weights" / "best.pt").write_bytes(b"x")
        (Path(d2) / "results.csv").write_text("epoch\n3\n")
        return {"epoch": 3, "map": None}

    monkeypatch.setattr(latih_maskrcnn, "_latih_maskrcnn_asli", fake_asli)
    monkeypatch.setattr("sys.argv", ["latih_maskrcnn", str(tmp_path), "1"])
    assert latih_maskrcnn.main() == 0
    assert terima["resume"] == str(dl / "maskrcnn" / "last.ckpt")


def test_serap_hitung_epoch(tmp_path):
    dl = latih.dir_latih(tmp_path, 1)
    dl.mkdir(parents=True, exist_ok=True)
    (dl / "results.csv").write_text("epoch,time,metrics/mAP50-95(B)\n7,9.0,0.5\n")
    assert latih_maskrcnn._serap(dl, {"epoch": 7, "map": 0.5}) == 7


def test_raster_melengkapi_poligon_dan_kotak():
    """Inti "melengkapi segmentasi + bbox" dalam satu target Mask R-CNN:
    poligon -> mask BENTUK poligon (bukan kotak penuh); kotak-saja (seg kosong)
    -> mask = KOTAK penuh. Dijalankan dengan numpy+cv2 saja (tanpa torch), jadi
    aman di suite CPU. Lihat juga smoke GPU yang memakai fungsi yang sama."""
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    _raster = latih_maskrcnn._raster

    # Produk: segitiga -> mask segitiga (jauh lebih kecil dari kotak pembungkus).
    tri = [20, 20, 60, 20, 40, 70]
    m_prod = _raster([tri], [20, 20, 60, 70], 128, 128, np, cv2)
    luas_kotak = (60 - 20) * (70 - 20)
    assert 0 < int(m_prod.sum()) < luas_kotak * 0.75
    # Mask produk TIDAK mengisi penuh kotaknya (ada piksel 0 di dalam bbox).
    sub = m_prod[20:70, 20:60]
    assert sub.min() == 0 and sub.max() == 1

    # Logo: seg kosong -> mask = kotak penuh.
    m_logo_kosong = _raster([], [80, 80, 110, 105], 128, 128, np, cv2)
    m_logo_none = _raster(None, [80, 80, 110, 105], 128, 128, np, cv2)
    luas_logo = (110 - 80) * (105 - 80)
    assert int(m_logo_kosong.sum()) == luas_logo
    assert int(m_logo_none.sum()) == luas_logo
    assert m_logo_kosong[80:105, 80:110].min() == 1   # terisi penuh
