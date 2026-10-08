"""Arsitektur RF-DETR (app/services/latih_rfdetr.py + wiring di latih.py).
Tanpa GPU/rfdetr: training rfdetr SUNGGUHAN dipalsukan lewat seam
_latih_rfdetr_asli, jadi suite tak pernah mengimpor rfdetr."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.services import latih, latih_rfdetr


def _versi(tmp: Path, nomor: int, names, *, dengan_gambar=False) -> Path:
    vd = tmp / ".versi" / f"v{nomor}"
    (vd / "train" / "images").mkdir(parents=True, exist_ok=True)
    (vd / "data.yaml").write_text(
        "train: ../train/images\nval: ../valid/images\n"
        f"nc: {len(names)}\nnames: [{', '.join(repr(n) for n in names)}]\n")
    if dengan_gambar:
        from PIL import Image
        Image.new("RGB", (100, 100), (20, 20, 20)).save(vd / "train" / "images" / "im0.jpg")
        (vd / "train" / "labels").mkdir(parents=True, exist_ok=True)
        (vd / "train" / "labels" / "im0.txt").write_text("0 0.5 0.5 0.4 0.4\n")
    return vd


# ---- siapkan: arsitektur + preset RF-DETR -------------------------------

def test_siapkan_rfdetr_pakai_preset_rfdetr(tmp_path):
    _versi(tmp_path, 1, ["player", "ball"])
    r = latih.siapkan(tmp_path, nama="r", versi_nomor=1, tugas="detect", bobot="",
                      par={}, oleh="u", arsitektur="rfdetr", rfdetr_model="small")
    assert r["arsitektur"] == "rfdetr" and r["rfdetr_model"] == "small"
    assert r["par"]["epochs"] == 100
    assert r["par"]["batch_size"] == 4 and r["par"]["grad_accum_steps"] == 4
    assert r["par"]["lr"] == 0.0001 and r["par"]["lr_encoder"] == 0.00015
    assert "hsv_h" not in r["par"]            # BUKAN par YOLO
    assert r["warna"] == {}                   # rfdetr tak pakai mode_warna


def test_siapkan_rfdetr_clamp_batas(tmp_path):
    _versi(tmp_path, 1, ["a"])
    r = latih.siapkan(tmp_path, nama="x", versi_nomor=1, tugas="detect", bobot="",
                      par={"epochs": 50, "resolution": 728}, oleh="u", arsitektur="rfdetr")
    assert r["par"]["epochs"] == 50 and r["par"]["resolution"] == 728
    with pytest.raises(ValueError):
        latih.siapkan(tmp_path, nama="x", versi_nomor=1, tugas="detect", bobot="",
                      par={"epochs": 9999}, oleh="u", arsitektur="rfdetr")


def test_siapkan_arsitektur_dan_model_asing_jatuh_bawaan(tmp_path):
    _versi(tmp_path, 1, ["a"])
    r = latih.siapkan(tmp_path, nama="x", versi_nomor=1, tugas="detect", bobot="",
                      par={}, oleh="u", arsitektur="ngawur")
    assert r["arsitektur"] == "yolo"          # asing -> yolo (jalur lama)
    r2 = latih.siapkan(tmp_path, nama="x", versi_nomor=1, tugas="detect", bobot="",
                       par={}, oleh="u", arsitektur="rfdetr", rfdetr_model="raksasa")
    assert r2["rfdetr_model"] == "nano"       # asing -> nano


def test_siapkan_yolo_default_tak_tersentuh(tmp_path):
    """Tanpa arsitektur -> yolo, preset RVM, mode_warna tetap berlaku."""
    _versi(tmp_path, 1, ["botol"])
    r = latih.siapkan(tmp_path, nama="x", versi_nomor=1, tugas="detect", bobot="y.pt",
                      par={}, oleh="u")
    assert r["arsitektur"] == "yolo" and r["rfdetr_model"] == ""
    assert r["par"]["imgsz"] == 640 and "hsv_h" in r["par"]


# ---- jalankan memilih worker RF-DETR ------------------------------------

def test_jalankan_rfdetr_pilih_worker(tmp_path, monkeypatch):
    direkam = {}

    class FakePopen:
        def __init__(self, cmd, **kw):
            direkam["cmd"] = cmd
            self.pid = 1

    monkeypatch.setattr(latih.subprocess, "Popen", FakePopen)
    latih._tulis(tmp_path, 1, {"nomor": 1, "versi": 1, "arsitektur": "rfdetr",
                               "par": {}, "keadaan": "antre"})
    latih.jalankan(tmp_path, 1)
    assert "app.services.latih_rfdetr" in direkam["cmd"]


# ---- helper murni + kesiapan --------------------------------------------

def test_snap56():
    assert latih_rfdetr._snap56(560) == 560
    assert latih_rfdetr._snap56(384) == 392   # 384/56=6.857 -> 7*56
    assert latih_rfdetr._snap56(10) == 56      # minimal 56


def test_siap_rfdetr_dua_cabang(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda n: object() if n == "rfdetr" else None)
    assert latih.siap_rfdetr()[0] is True
    monkeypatch.setattr(importlib.util, "find_spec", lambda n: None)
    ok, alasan = latih.siap_rfdetr()
    assert ok is False and "rfdetr" in alasan.lower()


# ---- worker end-to-end dengan training rfdetr DIPALSUKAN ----------------

def test_worker_main_fake_train(tmp_path, monkeypatch):
    _versi(tmp_path, 1, ["player"], dengan_gambar=True)
    latih._tulis(tmp_path, 1, {
        "nomor": 1, "versi": 1, "arsitektur": "rfdetr", "rfdetr_model": "nano",
        "tugas": "detect", "par": dict(latih.PRESET_RFDETR), "keadaan": "antre", "pid": 0})

    def fake_asli(coco_dir, out_dir, model, par, lapor, resume=None):
        # COCO harus sudah ter-ekspor oleh worker SEBELUM ini
        assert (Path(coco_dir) / "train" / "_annotations.coco.json").exists()
        assert resume is None, "training segar tak boleh resume"
        (Path(out_dir) / "checkpoint_best_total.pth").write_bytes(b"PK\x03\x04fake")
        (Path(out_dir) / "last.ckpt").write_bytes(b"PK\x03\x04ckpt")
        (Path(out_dir) / "metrics_plot.png").write_bytes(b"\x89PNG")
        lapor(pesan="fake train")
        return {"epoch": int(par["epochs"]), "map": 0.5}

    monkeypatch.setattr(latih_rfdetr, "_latih_rfdetr_asli", fake_asli)
    monkeypatch.setattr("sys.argv", ["latih_rfdetr", str(tmp_path), "1"])
    rc = latih_rfdetr.main()
    assert rc == 0

    rek = latih.baca(tmp_path, 1)
    assert rek["keadaan"] == "selesai"
    dl = latih.dir_latih(tmp_path, 1)
    assert (dl / "weights" / "best.pt").exists()       # checkpoint -> best.pt
    assert (dl / "rfdetr" / "last.ckpt").exists()      # state penuh disimpan utk Lanjutkan
    assert (dl / "results.csv").exists()               # status bisa baca epoch
    assert (dl / "metrics_plot.png").exists()          # grafik ikut tersalin
    s = latih.status(tmp_path, 1)
    assert s["arsitektur"] == "rfdetr" and s["epoch"] == 100
    assert s["rfdetr_ckpt"] is True                    # kartu tampilkan "Lanjutkan"


def test_worker_main_resume_dari_seed(tmp_path, monkeypatch):
    """Kalau rfdetr/last.ckpt sudah disemai (jalur Lanjutkan), worker RESUME dari
    situ — meneruskan path checkpoint ke training, bukan mulai dari nol."""
    _versi(tmp_path, 1, ["player"], dengan_gambar=True)
    latih._tulis(tmp_path, 1, {
        "nomor": 1, "versi": 1, "arsitektur": "rfdetr", "rfdetr_model": "nano",
        "tugas": "detect", "par": dict(latih.PRESET_RFDETR), "keadaan": "antre", "pid": 0})
    dl = latih.dir_latih(tmp_path, 1)
    (dl / "rfdetr").mkdir(parents=True, exist_ok=True)
    seed = dl / "rfdetr" / "last.ckpt"
    seed.write_bytes(b"PK\x03\x04seed")

    dilihat = {}

    def fake_asli(coco_dir, out_dir, model, par, lapor, resume=None):
        dilihat["resume"] = resume
        (Path(out_dir) / "checkpoint_best_total.pth").write_bytes(b"PK\x03\x04fake")
        return {"epoch": int(par["epochs"]), "map": None}

    monkeypatch.setattr(latih_rfdetr, "_latih_rfdetr_asli", fake_asli)
    monkeypatch.setattr("sys.argv", ["latih_rfdetr", str(tmp_path), "1"])
    assert latih_rfdetr.main() == 0
    assert dilihat["resume"] == str(seed), "worker harus resume dari seed last.ckpt"


def test_serap_simpan_last_ckpt(tmp_path):
    """_serap menyalin last.ckpt output -> rfdetr/last.ckpt (untuk Lanjutkan)."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "checkpoint_best_total.pth").write_bytes(b"PK\x03\x04b")
    (out / "last.ckpt").write_bytes(b"PK\x03\x04c")
    dl = tmp_path / "L1"
    latih_rfdetr._serap(out, dl, {"epoch": 3, "map": 0.4})
    assert (dl / "weights" / "best.pt").exists()
    assert (dl / "rfdetr" / "last.ckpt").exists()
