"""
Uji halaman kelola akun.

Dua hal yang dijaga di sini, dan keduanya soal tidak mengunci orang di luar
aplikasinya sendiri:

1. Hanya admin yang boleh mengelola akun, diperiksa di rute — bukan sekadar
   disembunyikan dari menu.
2. Admin terakhir tidak bisa dihapus atau diturunkan. Tanpa itu, satu klik
   keliru menyisakan satu-satunya jalan pulang berupa menyunting users.json
   lewat SSH.
"""
from __future__ import annotations

import json

import pytest

from app.security import is_admin, load_users
from tests.test_data import PW_PAUL, masuk


def _users(lingkungan) -> dict:
    return load_users(lingkungan["users"])


def _jadikan_admin(lingkungan, akun: str, nilai: bool = True) -> None:
    p = lingkungan["users"]
    d = json.loads(p.read_text())
    d[akun]["admin"] = nilai
    p.write_text(json.dumps(d, indent=2))


# ============================================================
# PERAN
# ============================================================

def test_akun_tunggal_dianggap_admin_supaya_pemiliknya_tidak_terkunci(tmp_path):
    """Memasang pembaruan ini tidak boleh mengunci pemilik servernya sendiri.

    Berkas akun lama tidak punya kolom "admin" sama sekali. Kalau ketiadaan
    itu berarti "bukan admin", pemilik server kehilangan akses ke halaman
    kelola akun dan harus kembali ke terminal — padahal justru terminal yang
    hendak ditinggalkan.
    """
    satu = {"darma": {"hash": "x", "nama": "darma"}}
    assert is_admin(satu, "darma") is True

    dua = {"darma": {"hash": "x"}, "rizky": {"hash": "y"}}
    assert is_admin(dua, "darma") is False, "dua akun tanpa admin: jangan menebak"

    ada = {"darma": {"hash": "x", "admin": True}, "rizky": {"hash": "y"}}
    assert is_admin(ada, "darma") is True
    assert is_admin(ada, "rizky") is False


def test_bukan_admin_ditolak_di_rutenya_bukan_cuma_disembunyikan(klien,
                                                                 lingkungan):
    masuk(klien, "paul", PW_PAUL)
    # Buat akun kedua supaya "akun tunggal" tidak lagi berlaku, dan pastikan
    # paul bukan admin.
    _jadikan_admin(lingkungan, "paul", False)
    p = lingkungan["users"]
    d = json.loads(p.read_text())
    d["orang2"] = {"hash": "x", "nama": "orang2"}
    p.write_text(json.dumps(d))
    klien.get("/logout")
    masuk(klien, "paul", PW_PAUL)

    for rute, params in (("/api/akun/daftar", None),
                         ("/api/akun/tambah", {"nama": "z", "sandi": "12345678"}),
                         ("/api/akun/ubah", {"akun": "orang2", "admin": 1}),
                         ("/api/akun/hapus", {"akun": "orang2"})):
        r = (klien.get(rute) if params is None
             else klien.post(rute, params=params)).json()
        assert r["ok"] is False, rute
        assert "admin" in r["error"], (rute, r)
    assert "orang2" in _users(lingkungan), "akun ikut terhapus padahal ditolak"


# ============================================================
# TAMBAH
# ============================================================

def test_menambah_anggota_lengkap_dengan_email_dan_peran(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _jadikan_admin(lingkungan, "paul", True)
    j = klien.post("/api/akun/tambah", params={
        "nama": "Rizky", "sandi": "rahasia-panjang", "email": "rizky@higo.id",
        "admin": 1}).json()
    assert j["ok"], j

    rec = _users(lingkungan)["rizky"]
    assert rec["email"] == "rizky@higo.id"
    assert rec["admin"] is True
    assert rec["oleh"] == "paul" and rec["dibuat"]
    assert "hash" in rec and "sandi" not in rec and "rahasia" not in json.dumps(rec)

    # dan akunnya benar-benar bisa dipakai masuk
    klien.get("/logout")
    assert masuk(klien, "rizky", "rahasia-panjang")


@pytest.mark.parametrize("params, potongan", [
    ({"nama": "", "sandi": "12345678"}, "kosong"),
    ({"nama": "budi", "sandi": "pendek"}, "8 karakter"),
    ({"nama": "budi", "sandi": "12345678", "email": "bukan-email"}, "email"),
])
def test_tambah_menolak_isian_yang_salah(klien, lingkungan, params, potongan):
    masuk(klien, "paul", PW_PAUL)
    _jadikan_admin(lingkungan, "paul", True)
    j = klien.post("/api/akun/tambah", params=params).json()
    assert j["ok"] is False and potongan in j["error"], j
    assert "budi" not in _users(lingkungan)


def test_nama_dan_email_tidak_boleh_kembar(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _jadikan_admin(lingkungan, "paul", True)
    klien.post("/api/akun/tambah", params={
        "nama": "rizky", "sandi": "12345678", "email": "rizky@higo.id"})

    j = klien.post("/api/akun/tambah", params={
        "nama": "rizky", "sandi": "87654321"}).json()
    assert j["ok"] is False and "sudah ada" in j["error"]

    # Email kembar berbahaya: nanti login Google memetakan email -> akun, dan
    # dua akun dengan email sama berarti pemetaan itu ambigu.
    j = klien.post("/api/akun/tambah", params={
        "nama": "rizky2", "sandi": "12345678",
        "email": "RIZKY@higo.id"}).json()
    assert j["ok"] is False and "sudah dipakai" in j["error"], j


# ============================================================
# PENJAGA ADMIN TERAKHIR
# ============================================================

def test_admin_terakhir_tidak_bisa_diturunkan_atau_dihapus(klien, lingkungan):
    """Satu klik keliru tidak boleh mengunci seluruh tim di luar halaman ini."""
    masuk(klien, "paul", PW_PAUL)
    _jadikan_admin(lingkungan, "paul", True)
    klien.post("/api/akun/tambah", params={"nama": "biasa", "sandi": "12345678"})

    j = klien.post("/api/akun/ubah", params={"akun": "paul", "admin": 0}).json()
    assert j["ok"] is False and "admin terakhir" in j["error"], j
    assert _users(lingkungan)["paul"]["admin"] is True

    # Setelah ada admin kedua, barulah boleh.
    klien.post("/api/akun/ubah", params={"akun": "biasa", "admin": 1})
    j = klien.post("/api/akun/ubah", params={"akun": "paul", "admin": 0}).json()
    assert j["ok"], j
    assert _users(lingkungan)["paul"]["admin"] is False


def test_tidak_bisa_menghapus_akun_sendiri(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _jadikan_admin(lingkungan, "paul", True)
    j = klien.post("/api/akun/hapus", params={"akun": "paul"}).json()
    assert j["ok"] is False and "sendiri" in j["error"], j
    assert "paul" in _users(lingkungan)


def test_menghapus_akun_lain_berhasil_dan_tercatat(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _jadikan_admin(lingkungan, "paul", True)
    klien.post("/api/akun/tambah", params={"nama": "sementara", "sandi": "12345678"})
    assert "sementara" in _users(lingkungan)

    j = klien.post("/api/akun/hapus", params={"akun": "sementara"}).json()
    assert j["ok"], j
    assert "sementara" not in _users(lingkungan)


# ============================================================
# UBAH
# ============================================================

def test_mengganti_sandi_dari_halaman_admin(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _jadikan_admin(lingkungan, "paul", True)
    klien.post("/api/akun/tambah", params={"nama": "rizky", "sandi": "sandi-lama-1"})

    j = klien.post("/api/akun/ubah", params={
        "akun": "rizky", "sandi": "sandi-baru-9"}).json()
    assert j["ok"], j
    klien.get("/logout")
    assert masuk(klien, "rizky", "sandi-baru-9")


def test_email_bisa_dikosongkan_kembali(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _jadikan_admin(lingkungan, "paul", True)
    klien.post("/api/akun/tambah", params={
        "nama": "rizky", "sandi": "12345678", "email": "rizky@higo.id"})

    assert klien.post("/api/akun/ubah",
                      params={"akun": "rizky", "email": ""}).json()["ok"]
    assert _users(lingkungan)["rizky"]["email"] == ""


def test_daftar_menandai_diri_sendiri_dan_peran(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _jadikan_admin(lingkungan, "paul", True)
    klien.post("/api/akun/tambah", params={
        "nama": "rizky", "sandi": "12345678", "email": "rizky@higo.id"})

    j = klien.get("/api/akun/daftar").json()
    per = {a["akun"]: a for a in j["akun"]}
    assert per["paul"]["diri_sendiri"] is True and per["paul"]["admin"] is True
    assert per["rizky"]["diri_sendiri"] is False and per["rizky"]["admin"] is False
    assert per["rizky"]["email"] == "rizky@higo.id"
    assert all("hash" not in a for a in j["akun"]), "hash sandi ikut terkirim"


def test_halaman_akun_menolak_dengan_sopan_untuk_yang_bukan_admin(klien,
                                                                  lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _jadikan_admin(lingkungan, "paul", False)
    p = lingkungan["users"]
    d = json.loads(p.read_text())
    d["lain"] = {"hash": "x", "admin": True}
    p.write_text(json.dumps(d))
    klien.get("/logout")
    masuk(klien, "paul", PW_PAUL)

    html = klien.get("/akun").text
    assert "Halaman ini untuk admin" in html
    assert "belum punya hak admin" in html
    assert "Tambah anggota" not in html, "borang tambah bocor ke yang bukan admin"
    assert "akun-daftar" not in html, "daftar akun bocor ke yang bukan admin"
