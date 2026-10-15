"""
Format anotasi PER-VERSI (resep.format): satu projek bercampur polygon + kotak
bisa dibangun jadi versi DETEKSI (semua jadi kotak) tanpa mengubah setelan
projek, dan tanpa mengembalikan gambar ber-kotak ke "Belum ditugaskan".

Kasus nyata: projek Frisian Flag punya polygon (tincan, mask objek) DAN kotak
(logo_ff) di tiap gambar. Default mode poligon membuang gambar ber-kotak itu;
memilih "Deteksi (kotak)" per-versi menyimpan semuanya sebagai kotak.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from tests.test_data import masuk, PW_PAUL


def _ds_campur(klien, n=6) -> Path:
    """Dataset: tiap gambar punya 'produk' (polygon) + 'logo' (rectangle)."""
    ruang = Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = ruang / "campur"
    d.mkdir(parents=True, exist_ok=True)
    (d / "classes.txt").write_text("produk\nlogo\n")
    rng = np.random.default_rng(1)
    for i in range(n):
        im = (rng.random((240, 320, 3)) * 80 + 60).astype(np.uint8)
        shapes = [
            {"label": "produk", "shape_type": "polygon",
             "points": [[60, 40], [180, 44], [176, 170], [58, 166]]},
            {"label": "logo", "shape_type": "rectangle",
             "points": [[100, 80], [140, 120]]},
        ]
        cv2.imwrite(str(d / f"g{i:02d}.jpg"), im)
        (d / f"g{i:02d}.json").write_text(json.dumps({
            "version": "0.4.36", "flags": {}, "shapes": shapes,
            "imagePath": f"g{i:02d}.jpg", "imageData": None,
            "imageHeight": 240, "imageWidth": 320}))
    return d


def _tunggu(klien, batas=120):
    for _ in range(batas * 10):
        k = klien.get("/api/versi/kemajuan").json()
        if k.get("selesai") or k.get("batal") or k.get("galat"):
            return k
        time.sleep(0.1)
    pytest.fail(f"versi tak selesai: {k}")


def _mulai(klien, resep, split="100,0,0"):
    return klien.post(f"/api/versi/mulai?split={split}",
                      json={"resep": resep}).json()


def _resep(**x):
    return {"volume": {"per_gambar": 0},
            "fase": {"crop_zoom": {"aktif": False},
                     "balans_skala": {"aktif": False},
                     "balans_kelas": {"aktif": False},
                     "porsi_negatif": {"aktif": False}}, **x}


def _label_lines(v: Path):
    out = []
    for p in (v / "train" / "labels").glob("*.txt"):
        for ln in p.read_text().splitlines():
            if ln.strip():
                out.append(ln.split())
    return out


def test_format_kotak_menyimpan_semua_sebagai_kotak(klien, lingkungan):
    """Deteksi (kotak): semua objek jadi kotak (5 kolom), tidak ada yang
    dikembalikan ke 'Belum ditugaskan'."""
    masuk(klien, "paul", PW_PAUL)
    d = _ds_campur(klien)
    klien.post(f"/setsrc?path={d}")
    assert _mulai(klien, _resep(format="kotak")).get("ok")
    k = _tunggu(klien)
    assert k.get("selesai"), k
    assert not k.get("dipulangkan"), "kotak tak boleh memulangkan gambar apa pun"

    v = d / ".versi" / "v1"
    imgs = list((v / "train" / "images").glob("*.jpg"))
    assert len(imgs) == 6, f"semua gambar harus tersimpan, dapat {len(imgs)}"
    lines = _label_lines(v)
    # Semua baris = kotak YOLO (5 kolom), BUKAN poligon.
    assert lines and all(len(t) == 5 for t in lines), "harus kotak 5-kolom"
    kelas = {t[0] for t in lines}
    assert kelas == {"0", "1"}, f"kedua kelas harus ada, dapat {kelas}"


def test_poligon_menolak_kelas_dominan_kotak(klien, lingkungan):
    """Guard 'tolak campuran': versi segmentasi yang memuat kelas dominan-kotak
    (logo) DITOLAK sebelum dibangun, dengan menyebut kelasnya -- bukan diam-diam
    memulangkan semua gambarnya ke 'Belum ditugaskan'."""
    masuk(klien, "paul", PW_PAUL)
    d = _ds_campur(klien)
    klien.post(f"/setsrc?path={d}")
    r = _mulai(klien, _resep(format="poligon"))
    assert r.get("ok") is False, r
    assert "logo" in (r.get("error") or ""), r
    # Ditolak = tidak ada versi yang dibangun, dataset tak tersentuh.
    assert not (d / ".versi" / "v1").exists()


def test_estimasi_memberi_bentuk_per_kelas(klien, lingkungan):
    """Panel Kelas butuh jumlah kotak & poligon per kelas + totalnya."""
    masuk(klien, "paul", PW_PAUL)
    d = _ds_campur(klien, n=5)
    klien.post(f"/setsrc?path={d}")
    e = klien.post("/api/versi/estimasi?split=100,0,0", json={"resep": {}}).json()
    assert e.get("ok"), e
    bykelas = {k["nama"]: k for k in e["daftar_kelas"]}
    assert bykelas["produk"]["poligon"] == 5 and bykelas["produk"]["kotak"] == 0
    assert bykelas["logo"]["kotak"] == 5 and bykelas["logo"]["poligon"] == 0
    assert e["bentuk_total"] == {"kotak": 5, "poligon": 5, "lain": 0}


def test_poligon_sah_kalau_semua_kelas_poligon(klien, lingkungan):
    """Projek yang semua kelasnya poligon tetap bisa dibangun jadi segmentasi."""
    masuk(klien, "paul", PW_PAUL)
    ruang = Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = ruang / "segonly"
    d.mkdir(parents=True, exist_ok=True)
    (d / "classes.txt").write_text("produk\n")
    for i in range(5):
        im = (np.random.default_rng(i).random((240, 320, 3)) * 80 + 60).astype(np.uint8)
        cv2.imwrite(str(d / f"g{i}.jpg"), im)
        (d / f"g{i}.json").write_text(json.dumps({
            "version": "0.4.36", "flags": {},
            "shapes": [{"label": "produk", "shape_type": "polygon",
                        "points": [[60, 40], [180, 44], [176, 170], [58, 166]]}],
            "imagePath": f"g{i}.jpg", "imageData": None,
            "imageHeight": 240, "imageWidth": 320}))
    klien.post(f"/setsrc?path={d}")
    assert _mulai(klien, _resep(format="poligon")).get("ok")
    assert _tunggu(klien).get("selesai")
    v = d / ".versi" / "v1"
    lines = _label_lines(v)
    assert lines and all(len(t) > 5 for t in lines), "harus poligon (>5 kolom)"
