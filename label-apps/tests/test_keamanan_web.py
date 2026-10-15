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


def test_mode_undangan_tutup_daftar_publik(klien, monkeypatch):
    # Mode hanya-undangan: tak ada pendaftaran mandiri tanpa tautan undangan
    # bertoken yang sah, dan tautan "Daftar" publik di halaman masuk hilang.
    from app.config import get_settings
    monkeypatch.setenv("LABELAPP_DAFTAR_HANYA_UNDANGAN", "1")
    get_settings.cache_clear()

    r = klien.get("/daftar", follow_redirects=False)
    assert r.status_code == 403
    assert "undangan" in r.text.lower()
    assert 'name="pw"' not in r.text          # form pendaftaran tak tampil

    # POST tanpa token ditolak walau datanya lengkap -- tak ada akun 'menunggu'
    # yang lahir dari internet.
    r = klien.post("/daftar", data={"user": "bot1", "pw": "password1",
                   "pw2": "password1"}, follow_redirects=False)
    assert r.status_code == 403

    # Halaman masuk tak lagi menawarkan tautan daftar terbuka.
    r = klien.get("/login")
    assert "/daftar" not in r.text


def test_default_daftar_publik_tetap_terbuka(klien):
    # Tanpa mode undangan (bawaan), /daftar tetap menampilkan formnya.
    r = klien.get("/daftar")
    assert r.status_code == 200
    assert 'name="pw"' in r.text
