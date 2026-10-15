"""
/label tanpa projek terbuka -> pemilih projek, bukan 404 buntu.

Sesudah restart prod / login ulang, sesi belum punya dataset terbuka. Dulu
/label (dan refresh tautan labeler) menjawab 404 "tidak ditemukan"; sekarang
diarahkan ke /pilih seperti route lain.
"""
from __future__ import annotations

from tests.test_data import masuk, PW_PAUL


def test_label_tanpa_projek_ke_pilih(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)        # login, TAPI belum /setsrc
    r = klien.get("/label", follow_redirects=False)
    assert r.status_code == 303, r.status_code
    assert r.headers["location"] == "/pilih", r.headers.get("location")


def test_label_tanpa_sesi_ke_login(klien, lingkungan):
    # Tanpa login: dialihkan ke /login (bawa next), bukan 404.
    r = klien.get("/label?path=/x.jpg", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["location"].startswith("/login")
