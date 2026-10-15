"""
Pengamanan web dasar untuk paparan internet.

Ketiga hal ini baru penting begitu aplikasi dijangkau dari luar jaringan kantor
lewat reverse proxy HTTPS: header keamanan di tiap jawaban, bendera Secure pada
cookie sesi HANYA saat lewat HTTPS, dan rate-limit login untuk lalu lintas yang
datang lewat proxy (internet publik), bukan akses lokal.
"""
from __future__ import annotations

from conftest import PW_ANGGI


def test_header_keamanan_selalu_ada(klien):
    r = klien.get("/login")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "SAMEORIGIN"
    assert "referrer-policy" in {k.lower() for k in r.headers}


def test_hsts_hanya_saat_https(klien):
    # HTTP polos: tanpa HSTS (kalau tidak, dev/lokal ikut dipaksa HTTPS).
    r = klien.get("/login")
    assert "strict-transport-security" not in {k.lower() for k in r.headers}
    # Lewat proxy HTTPS: HSTS muncul.
    r2 = klien.get("/login", headers={"X-Forwarded-Proto": "https"})
    assert r2.headers.get("strict-transport-security", "").startswith("max-age=")


def test_cookie_sesi_tanpa_secure_di_http(klien):
    # Di HTTP polos cookie TIDAK boleh Secure: kalau ya, peramban membuangnya
    # dan login patah. Jadi di dev/lokal cookie tetap biasa.
    r = klien.post("/login", data={"user": "anggi", "pw": PW_ANGGI},
                   follow_redirects=False)
    assert r.status_code == 303
    sc = r.headers.get("set-cookie", "")
    assert "labelapp_sid=" in sc and "secure" not in sc.lower()


def test_cookie_sesi_secure_di_https(klien):
    r = klien.post("/login", data={"user": "anggi", "pw": PW_ANGGI},
                   headers={"X-Forwarded-Proto": "https"}, follow_redirects=False)
    assert r.status_code == 303
    sc = r.headers.get("set-cookie", "")
    assert "labelapp_sid=" in sc and "secure" in sc.lower()


def test_rate_limit_login_terproksi(klien, monkeypatch):
    from app.routers import auth
    monkeypatch.setattr(auth, "FAILED_LOGIN_DELAY", 0.0)
    auth._gagal_login.clear()
    hdr = {"X-Forwarded-For": "203.0.113.7"}
    # Sejumlah _RL_MAKS gagal berturut dari IP terproksi yang sama -> masih 401.
    for _ in range(auth._RL_MAKS):
        r = klien.post("/login", data={"user": "anggi", "pw": "salahsalah"},
                       headers=hdr, follow_redirects=False)
        assert r.status_code == 401
    # Percobaan berikutnya diblokir sementara.
    r = klien.post("/login", data={"user": "anggi", "pw": "salahsalah"},
                   headers=hdr, follow_redirects=False)
    assert r.status_code == 429
    assert r.headers.get("retry-after")
    auth._gagal_login.clear()


def test_tanpa_proxy_tak_kena_rate_limit(klien, monkeypatch):
    # Tanpa header proxy (akses langsung/lokal/tes), gagal berkali-kali tak
    # pernah memicu 429 -- ancaman publik selalu lewat proxy.
    from app.routers import auth
    monkeypatch.setattr(auth, "FAILED_LOGIN_DELAY", 0.0)
    auth._gagal_login.clear()
    for _ in range(auth._RL_MAKS + 3):
        r = klien.post("/login", data={"user": "anggi", "pw": "salahsalah"},
                       follow_redirects=False)
        assert r.status_code == 401
