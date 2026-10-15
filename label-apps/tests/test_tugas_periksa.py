"""
Endpoint /api/tugas/periksa-akun untuk dialog "Tugaskan ulang".

Dijaga: memeriksa SATU username/email dan mengembalikan info akun itu saja,
tanpa membocorkan daftar akun atau email tim. Untuk yang tak ada, menandai
apakah yang diketik email (supaya bisa diundang).
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from tests.test_data import masuk, PW_PAUL


def _projek(klien) -> Path:
    ruang = Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = ruang / "pr"
    d.mkdir(parents=True, exist_ok=True)
    (d / "classes.txt").write_text("a\n")
    im = (np.zeros((60, 80, 3)) + 100).astype(np.uint8)
    cv2.imwrite(str(d / "g.jpg"), im)
    (d / "g.json").write_text(json.dumps({
        "version": "0.4.36", "flags": {}, "shapes": [],
        "imagePath": "g.jpg", "imageData": None,
        "imageHeight": 60, "imageWidth": 80}))
    return d


def _periksa(klien, q):
    return klien.get(f"/api/tugas/periksa-akun?q={q}").json()


def test_periksa_akun_terdaftar_dan_tidak(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    d = _projek(klien)
    klien.post(f"/setsrc?path={d}")

    # Diri sendiri = pemilik projek ini.
    r = _periksa(klien, "paul")
    assert r["ok"] and r["ada"] and r["anggota"] and r["peran"] == "pemilik", r

    # Akun lain yang terdaftar tapi BUKAN anggota projek ini.
    r = _periksa(klien, "anggi")
    assert r["ok"] and r["ada"] and not r["anggota"], r

    # Username yang tak terdaftar -> tak ada, bukan email.
    r = _periksa(klien, "bukansiapa")
    assert r["ok"] and r["ada"] is False and r["email"] == "", r

    # Email yang tak terdaftar -> tak ada, tapi ditandai email (bisa diundang).
    r = _periksa(klien, "baru@contoh.com")
    assert r["ok"] and r["ada"] is False and r["email"] == "baru@contoh.com", r


def test_periksa_akun_butuh_sesi(klien, lingkungan):
    # Tanpa login -> 401 (seperti endpoint JSON lain).
    assert klien.get("/api/tugas/periksa-akun?q=paul").status_code == 401


def test_undang_email_pakai_nama_projek_asli(klien, lingkungan, monkeypatch):
    """Nama proyek di email = nama projek HIGOLAB yang sedang dibuka (folder),
    adaptif -- bukan nilai tetap. Kirim email dipalsukan; yang diperiksa argumen
    projek yang diteruskan."""
    from app.services import surel
    dicatat = {}

    def _fake(settings, ke, tautan, projek):
        dicatat.update(ke=ke, tautan=tautan, projek=projek)
        return True
    monkeypatch.setattr(surel, "aktif", lambda s: True)
    monkeypatch.setattr(surel, "kirim_undangan", _fake)

    masuk(klien, "paul", PW_PAUL)
    ruang = Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = ruang / "projek-frision-flag"          # nama folder = nama projek
    d.mkdir(parents=True, exist_ok=True)
    (d / "classes.txt").write_text("a\n")
    im = (np.zeros((60, 80, 3)) + 100).astype(np.uint8)
    cv2.imwrite(str(d / "g.jpg"), im)
    (d / "g.json").write_text(json.dumps({
        "version": "0.4.36", "flags": {}, "shapes": [],
        "imagePath": "g.jpg", "imageData": None,
        "imageHeight": 60, "imageWidth": 80}))
    klien.post(f"/setsrc?path={d}")

    r = klien.post("/api/tugas/undang-email"
                   "?email=orangbaru@contoh.com&peran=pelabel&akses=semua").json()
    assert r.get("ok") and r.get("terkirim"), r
    assert dicatat["projek"] == "projek-frision-flag", dicatat
    assert dicatat["ke"] == "orangbaru@contoh.com"
    assert "/undangan/" in dicatat["tautan"]
