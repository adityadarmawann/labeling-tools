"""Gerbang login, isolasi antar akun, dan penjagaan endpoint desktop."""
from __future__ import annotations

import re
from pathlib import Path

from conftest import PW_ANGGI, PW_PAUL, ROOT, klien_baru, masuk


# ---------------------------------------------------------------- gerbang login

def test_halaman_tanpa_sesi_dialihkan_ke_login(klien):
    for jalan in ("/", "/pilih", "/view?path=/x.jpg"):
        r = klien.get(jalan, follow_redirects=False)
        assert r.status_code == 303, jalan
        assert r.headers["location"] == "/login"


def test_endpoint_json_tanpa_sesi_menjawab_401(klien):
    assert klien.post("/rescan").status_code == 401
    assert klien.post("/setsrc?path=/tmp").status_code == 401
    assert klien.put("/upload?ds=x&name=a.png", content=b"x").status_code == 401


def test_password_salah_ditolak(klien):
    r = klien.post("/login", data={"user": "paul", "pw": "ngawur"},
                   follow_redirects=False)
    assert r.status_code == 401


def test_nama_akun_tidak_peka_huruf_besar(aplikasi):
    for tulisan in ("paul", "Paul", "PAUL", " paul "):
        klien_baru(aplikasi, tulisan, PW_PAUL)      # assert ada di masuk()


def test_login_sudah_ada_sesi_dialihkan_ke_beranda(klien):
    masuk(klien, "paul", PW_PAUL)
    r = klien.get("/login", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


# ---------------------------------------------------------------- isolasi

def test_dua_akun_punya_dataset_sendiri(aplikasi, lingkungan):
    roots = lingkungan["roots"]
    a = klien_baru(aplikasi, "paul", PW_PAUL)
    b = klien_baru(aplikasi, "anggi", PW_ANGGI)

    assert a.post(f"/setsrc?path={roots / 'ds-alpha'}").json()["n"] == 4
    assert b.post(f"/setsrc?path={roots / 'ds-beta'}").json()["n"] == 3

    ha, hb = a.get("/").text, b.get("/").text
    assert "ds-alpha" in ha and "ds-beta" not in ha
    assert "ds-beta" in hb and "ds-alpha" not in hb
    assert ha.count('class="card"') == 4
    assert hb.count('class="card"') == 3
    assert "<b>paul</b>" in ha
    assert "<b>anggi</b>" in hb


def test_akun_tidak_bisa_melihat_gambar_dataset_akun_lain(aplikasi, lingkungan):
    roots = lingkungan["roots"]
    a = klien_baru(aplikasi, "paul", PW_PAUL)
    b = klien_baru(aplikasi, "anggi", PW_ANGGI)
    a.post(f"/setsrc?path={roots / 'ds-alpha'}")
    b.post(f"/setsrc?path={roots / 'ds-beta'}")

    gambar = roots / "ds-alpha" / "ds-alpha-00.jpg"
    assert a.get(f"/thumb?path={gambar}").status_code == 200
    assert b.get(f"/thumb?path={gambar}").status_code == 404
    assert b.get(f"/view?path={gambar}").status_code == 404


def test_berkas_di_luar_dataset_tidak_bisa_dibaca(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={lingkungan['roots'] / 'ds-alpha'}")
    for jahat in ("/etc/passwd", "/etc/shadow", str(ROOT / "app" / "security.py")):
        assert klien.get(f"/thumb?path={jahat}").status_code == 404, jahat


def test_thumbnail_dipisah_per_akun(aplikasi, lingkungan):
    roots, thumb = lingkungan["roots"], lingkungan["tmp"] / "thumb"
    a = klien_baru(aplikasi, "paul", PW_PAUL)
    b = klien_baru(aplikasi, "anggi", PW_ANGGI)
    a.post(f"/setsrc?path={roots / 'ds-alpha'}")
    b.post(f"/setsrc?path={roots / 'ds-beta'}")
    a.get(f"/thumb?path={roots / 'ds-alpha' / 'ds-alpha-00.jpg'}")
    b.get(f"/thumb?path={roots / 'ds-beta' / 'ds-beta-00.jpg'}")

    assert (thumb / "paul").is_dir()
    assert (thumb / "anggi").is_dir()
    assert list((thumb / "paul").glob("*.jpg"))
    assert list((thumb / "anggi").glob("*.jpg"))


def test_logout_hanya_memutus_akun_itu(aplikasi, lingkungan):
    roots, thumb = lingkungan["roots"], lingkungan["tmp"] / "thumb"
    a = klien_baru(aplikasi, "paul", PW_PAUL)
    b = klien_baru(aplikasi, "anggi", PW_ANGGI)
    a.post(f"/setsrc?path={roots / 'ds-alpha'}")
    b.post(f"/setsrc?path={roots / 'ds-beta'}")
    a.get(f"/thumb?path={roots / 'ds-alpha' / 'ds-alpha-00.jpg'}")

    a.get("/logout", follow_redirects=False)
    assert a.get("/", follow_redirects=False).status_code == 303
    assert b.get("/").status_code == 200
    # thumbnail milik paul ikut dibuang, milik anggi tetap
    assert not (thumb / "paul").exists()
    assert (thumb / "anggi").exists()


# ---------------------------------------------------------------- penjagaan lokal

def test_endpoint_desktop_ditolak_dari_jaringan(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    gambar = lingkungan["roots"] / "ds-alpha" / "ds-alpha-00.jpg"
    klien.post(f"/setsrc?path={lingkungan['roots'] / 'ds-alpha'}")

    for jalan in (f"/open?path={gambar}", "/pickdir"):
        r = klien.post(jalan)
        assert r.status_code == 403, jalan
        assert "mesin server" in r.json()["detail"]


def test_endpoint_desktop_lolos_penjagaan_dari_localhost(klien_lokal, lingkungan):
    masuk(klien_lokal, "paul", PW_PAUL)
    gambar = lingkungan["roots"] / "ds-alpha" / "ds-alpha-00.jpg"
    klien_lokal.post(f"/setsrc?path={lingkungan['roots'] / 'ds-alpha'}")

    r = klien_lokal.post(f"/open?path={gambar}")
    # AnyLabeling belum tentu terpasang; yang diuji adalah tidak lagi 403.
    assert r.status_code == 200
    assert r.json()["ok"] is False or r.json()["ok"] is True


def test_tombol_desktop_disembunyikan_dari_jaringan(klien, klien_lokal, lingkungan):
    src = lingkungan["roots"] / "ds-alpha"
    masuk(klien, "paul", PW_PAUL)
    masuk(klien_lokal, "anggi", PW_ANGGI)
    klien.post(f"/setsrc?path={src}")
    klien_lokal.post(f"/setsrc?path={src}")

    assert "openIn(" not in klien.get("/").text
    assert "openIn(" in klien_lokal.get("/").text
    assert 'onclick="pickdir()"' not in klien.get("/pilih").text
    assert 'onclick="pickdir()"' in klien_lokal.get("/pilih").text


# ---------------------------------------------------------------- penjaga insiden

def test_setelan_uji_seluruhnya_menunjuk_ke_tmp(klien, lingkungan):
    """
    Pendamping fixture folder_aplikasi_tak_berubah: memastikan aplikasi yang
    diuji benar-benar membaca berkas akun, dataset, unggahan, dan thumbnail
    dari tmp_path — bukan dari folder aplikasi yang berisi akun sungguhan.
    """
    from app.config import get_settings

    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={lingkungan['roots'] / 'ds-alpha'}")
    klien.get("/")

    st = get_settings()
    tmp = lingkungan["tmp"]
    for label, p in (("users_file", st.users_file),
                     ("datasets_root", st.datasets_root),
                     ("uploads_root", st.uploads_root),
                     ("thumb_root", st.thumb_root)):
        assert tmp in Path(p).parents or Path(p) == tmp, f"{label} di luar tmp: {p}"
        assert ROOT not in Path(p).parents, f"{label} menunjuk ke folder aplikasi: {p}"
