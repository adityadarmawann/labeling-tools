"""
Uji alur unggah video -> ekstrak frame pada projek IMAGE, lewat HTTP.

Yang dijaga: video hanya diterima di projek image, /ekstrak memotongnya jadi
gambar lalu membuang videonya, dan projek VIDEO menolak video (jalurnya belum
ada) alih-alih menerimanya diam-diam.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from tests.conftest import PW_PAUL, masuk


def _video_bytes(tmp_path, n=60, fps=30.0, ukuran=(320, 240)):
    p = tmp_path / "klip.mp4"
    vw = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"mp4v"), fps, ukuran)
    assert vw.isOpened()
    w, h = ukuran
    for i in range(n):
        g = np.tile(np.linspace(0, 255, w, dtype=np.uint8), (h, 1))
        f = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
        rng = np.random.default_rng(i)
        for _ in range(6):
            x, y = int(rng.integers(0, w - 40)), int(rng.integers(0, h - 40))
            c = tuple(int(v) for v in rng.integers(0, 255, 3))
            cv2.rectangle(f, (x, y), (x + 40, y + 40), c, -1)
        cv2.putText(f, str(i), (30, 170), cv2.FONT_HERSHEY_SIMPLEX, 4,
                    (255, 255, 255), 6)
        vw.write(f)
    vw.release()
    return p.read_bytes()


def _buat_projek(klien, nama, jenis):
    r = klien.post(f"/api/projek/baru?nama={nama}&jenis={jenis}")
    assert r.status_code == 200, r.text[:300]
    j = r.json()
    assert j["ok"], j
    return j


def test_video_ke_projek_image_diekstrak_dan_dihapus(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "vid-img", "image")
    data = _video_bytes(lingkungan["tmp"])

    r = klien.put("/upload?ds=vid-img&name=klip.mp4", content=data)
    j = r.json()
    assert j["ok"] and j["video"] is True, j

    r = klien.post("/ekstrak?ds=vid-img&name=klip.mp4&preset=sedang")
    j = r.json()
    assert j["ok"], j
    assert j["n"] > 0 and j["video_dihapus"] is True
    assert j["preset"] == "sedang"

    proj = lingkungan["ruang"] / "vid-img"
    assert not (proj / "klip.mp4").exists()                 # video dibuang
    assert len(list(proj.glob("*.jpg"))) == j["n"]          # frame mendarat


def test_preset_tak_sah_jatuh_ke_bawaan(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "vid-preset", "image")
    data = _video_bytes(lingkungan["tmp"])
    klien.put("/upload?ds=vid-preset&name=klip.mp4", content=data)

    r = klien.post("/ekstrak?ds=vid-preset&name=klip.mp4&preset=ngaco")
    j = r.json()
    assert j["ok"] and j["preset"] == "sedang", j


def test_video_ke_projek_video_ditolak_di_upload(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "vid-vid", "video")
    data = _video_bytes(lingkungan["tmp"])

    r = klien.put("/upload?ds=vid-vid&name=klip.mp4", content=data)
    j = r.json()
    # Projek video belum punya jalur unggah: ekstensi video tak diizinkan.
    assert j["ok"] is False, j


def test_ekstrak_pada_projek_video_ditolak(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "vid-vid2", "video")
    # Taruh video langsung di foldernya (lewati /upload) untuk menguji guard
    # /ekstrak-nya sendiri.
    proj = lingkungan["ruang"] / "vid-vid2"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "klip.mp4").write_bytes(_video_bytes(lingkungan["tmp"]))

    r = klien.post("/ekstrak?ds=vid-vid2&name=klip.mp4")
    j = r.json()
    assert j["ok"] is False and "image" in j["error"], j


def test_ekstrak_tanpa_video_memberi_pesan_jelas(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "vid-kosong", "image")
    r = klien.post("/ekstrak?ds=vid-kosong&name=tidakada.mp4")
    j = r.json()
    assert j["ok"] is False and "tidak ada" in j["error"], j
