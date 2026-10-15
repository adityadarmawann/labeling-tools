"""
Alur undangan untuk orang BARU: satu klik, tanpa nyangkut.

Dulu: klik tautan undangan -> login -> daftar (kehilangan tujuan) -> akun
menunggu persetujuan admin -> harus klik tautan LAGI. Sekarang: mendaftar lewat
tautan undangan bertoken yang sah membuat akunnya LANGSUNG aktif (token rahasia
= sudah diizinkan pemilik), lalu diteruskan menerima undangan -> jadi anggota.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

from app.security import COOKIE_NAME
from tests.test_data import masuk, PW_PAUL


def _projek(klien) -> Path:
    ruang = Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = ruang / "pr-undang"
    d.mkdir(parents=True, exist_ok=True)
    (d / "classes.txt").write_text("a\n")
    cv2.imwrite(str(d / "g.jpg"), (np.zeros((60, 80, 3)) + 100).astype(np.uint8))
    (d / "g.json").write_text(json.dumps({
        "version": "0.4.36", "flags": {}, "shapes": [],
        "imagePath": "g.jpg", "imageData": None,
        "imageHeight": 60, "imageWidth": 80}))
    return d


def test_daftar_via_undangan_langsung_aktif_dan_gabung(klien, aplikasi, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    d = _projek(klien)
    klien.post(f"/setsrc?path={d}")
    r = klien.post("/api/tugas/undang-email"
                   "?email=baru@contoh.com&peran=pelabel&akses=semua").json()
    assert r.get("ok") and r.get("tautan"), r
    token = r["tautan"].split("/undangan/")[1]

    # Orang baru (anonim) mendaftar LEWAT tautan undangan.
    anon = TestClient(aplikasi)
    resp = anon.post("/daftar", data={
        "user": "sibaru", "pw": "rahasia123", "pw2": "rahasia123",
        "email": "baru@contoh.com", "next": f"/undangan/{token}"},
        follow_redirects=False)
    # Langsung diteruskan ke halaman terima undangan + sudah login (ada cookie).
    assert resp.status_code == 303, resp.text
    assert resp.headers["location"] == f"/undangan/{token}"
    assert anon.cookies.get(COOKIE_NAME), "harus langsung login"

    # Akun LANGSUNG aktif (bukan menunggu persetujuan).
    users = json.loads(Path(lingkungan["users"]).read_text())
    assert "sibaru" in users and users["sibaru"]["menunggu"] is False, users.get("sibaru")

    # Ikuti redirect -> undangan dipakai -> jadi anggota projek.
    anon.get(f"/undangan/{token}")
    data = json.loads((d / ".tugas.json").read_text())
    assert "sibaru" in (data.get("anggota") or {}), data


def test_daftar_tanpa_undangan_tetap_menunggu(klien, aplikasi, lingkungan):
    """Pendaftaran biasa (tanpa token undangan) TETAP menunggu persetujuan --
    pengaman anti-pendaftaran-liar tidak dilonggarkan."""
    anon = TestClient(aplikasi)
    resp = anon.post("/daftar", data={
        "user": "orangluar", "pw": "rahasia123", "pw2": "rahasia123",
        "email": "luar@contoh.com"}, follow_redirects=False)
    assert resp.status_code == 200          # halaman "selesai, menunggu"
    assert not anon.cookies.get(COOKIE_NAME), "tak boleh langsung login"
    users = json.loads(Path(lingkungan["users"]).read_text())
    assert users["orangluar"]["menunggu"] is True
