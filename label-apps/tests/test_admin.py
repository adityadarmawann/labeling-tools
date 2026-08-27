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

def test_hak_admin_tidak_hilang_saat_orang_lain_mendaftar(tmp_path):
    """Regresi atas bug yang terukur di server sungguhan.

    Aturan pertamanya "kalau akunnya cuma satu, dia admin". Aturan itu gugur
    seketika begitu orang pertama mendaftar sendiri: darma admin saat
    sendirian, lalu kehilangan haknya tanpa pernah menyerahkannya — dan
    tidak ada satu pun akun yang bisa memperbaikinya dari halaman web.
    """
    from app.security import calon_admin

    satu = {"darma": {"hash": "x", "dibuat": "2026-08-01"}}
    assert is_admin(satu, "darma") is True

    # Orang lain mendaftar sendiri: hak darma HARUS bertahan.
    dua = dict(satu)
    dua["rizky"] = {"hash": "y", "dibuat": "2026-08-27", "oleh": "daftar sendiri"}
    assert is_admin(dua, "darma") is True, "hak admin hilang saat ada pendaftar"
    assert is_admin(dua, "rizky") is False
    assert calon_admin(dua) == "darma"

    # Yang dibuat lewat terminal didahulukan daripada yang mendaftar sendiri,
    # walau pendaftarnya lebih tua.
    campur = {
        "pendaftar": {"hash": "a", "dibuat": "2026-01-01", "oleh": "daftar sendiri"},
        "dibuatkan": {"hash": "b", "dibuat": "2026-06-01"},
    }
    assert calon_admin(campur) == "dibuatkan"

    # Begitu ada admin sungguhan, tidak ada lagi yang diangkat diam-diam.
    ada = {"darma": {"hash": "x", "admin": True}, "rizky": {"hash": "y"}}
    assert calon_admin(ada) is None
    assert is_admin(ada, "rizky") is False


def test_hak_admin_ditulis_ke_berkas_bukan_cuma_disimpulkan(tmp_path):
    """Menyimpulkannya saat dibaca tidak cukup.

    Aturan apa pun yang bergantung pada isi berkas bisa gugur begitu isinya
    berubah, dan yang berubah di sini adalah orang lain mendaftar. Karena itu
    haknya ditulis sekali saat server menyala.
    """
    import json as _json

    from app.security import load_users, pastikan_ada_admin

    f = tmp_path / "users.json"
    f.write_text(_json.dumps({
        "darma": {"hash": "x", "dibuat": "2026-08-01"},
        "rizky": {"hash": "y", "dibuat": "2026-08-27", "oleh": "daftar sendiri"},
    }))

    assert pastikan_ada_admin(f) == "darma"
    assert load_users(f)["darma"]["admin"] is True

    # Dipanggil ulang tidak mengangkat siapa-siapa lagi.
    assert pastikan_ada_admin(f) is None
    assert load_users(f)["rizky"].get("admin") in (None, False)


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
    # Server yang menyala mengangkat satu admin kalau belum ada, dan di
    # lingkungan uji yang terangkat anggi. Ia diturunkan di sini supaya paul
    # benar-benar jadi admin TERAKHIR — itu yang sedang diuji.
    _jadikan_admin(lingkungan, "anggi", False)
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


# ============================================================
# DAFTAR SENDIRI + PERSETUJUAN
# ============================================================

def test_daftar_sendiri_belum_bisa_masuk_sebelum_disetujui(klien, lingkungan):
    """Ini yang menggantikan verifikasi email.

    Tanpa pembuktian alamat, tidak ada yang menjamin email yang diketik milik
    pendaftarnya. Persetujuan admin yang menggantikannya — dan itu pula yang
    membuat rute pendaftaran aman kalau portnya ternyata terbuka ke luar
    jaringan kantor.
    """
    r = klien.post("/daftar", data={
        "user": "rizky", "email": "rizky@higo.id",
        "pw": "sandi-rizky-1", "pw2": "sandi-rizky-1"})
    assert r.status_code == 200 and "menunggu persetujuan" in r.text

    rec = _users(lingkungan)["rizky"]
    assert rec["menunggu"] is True
    assert rec["admin"] is False, "pendaftar tidak boleh langsung jadi admin"
    assert rec["oleh"] == "daftar sendiri"

    # Sandinya benar, tapi belum boleh masuk.
    r = klien.post("/login", data={"user": "rizky", "pw": "sandi-rizky-1"},
                   follow_redirects=False)
    assert r.status_code == 401
    assert "belum disetujui" in r.text, "pesannya harus membedakan dari sandi salah"


def test_setelah_disetujui_baru_bisa_masuk(klien, lingkungan):
    klien.post("/daftar", data={"user": "rizky", "pw": "sandi-rizky-1",
                                "pw2": "sandi-rizky-1"})
    masuk(klien, "paul", PW_PAUL)
    _jadikan_admin(lingkungan, "paul", True)

    j = klien.get("/api/akun/daftar").json()
    assert j["n_menunggu"] == 1
    per = {a["akun"]: a for a in j["akun"]}
    assert per["rizky"]["menunggu"] is True

    j = klien.post("/api/akun/setujui", params={"akun": "rizky"}).json()
    assert j["ok"], j
    assert "menunggu" not in _users(lingkungan)["rizky"]
    assert _users(lingkungan)["rizky"]["disetujui_oleh"] == "paul"

    klien.get("/logout")
    assert masuk(klien, "rizky", "sandi-rizky-1")


@pytest.mark.parametrize("data, potongan", [
    ({"user": "", "pw": "12345678", "pw2": "12345678"}, "kosong"),
    ({"user": "budi", "pw": "pendek", "pw2": "pendek"}, "8 karakter"),
    ({"user": "budi", "pw": "12345678", "pw2": "berbeda9"}, "tidak sama"),
    ({"user": "budi", "pw": "12345678", "pw2": "12345678",
      "email": "bukan-email"}, "email"),
])
def test_daftar_menolak_isian_yang_salah(klien, lingkungan, data, potongan):
    r = klien.post("/daftar", data=data)
    assert r.status_code == 400 and potongan in r.text, r.text[:200]
    assert "budi" not in _users(lingkungan)


def test_daftar_menolak_nama_yang_sudah_dipakai(klien, lingkungan):
    r = klien.post("/daftar", data={"user": "paul", "pw": "12345678",
                                    "pw2": "12345678"})
    assert r.status_code == 400 and "sudah ada" in r.text
    # dan sandi paul yang lama TIDAK boleh tertimpa
    assert masuk(klien, "paul", PW_PAUL)


def test_menyetujui_hanya_untuk_admin(klien, lingkungan):
    klien.post("/daftar", data={"user": "rizky", "pw": "sandi-rizky-1",
                                "pw2": "sandi-rizky-1"})
    masuk(klien, "paul", PW_PAUL)
    _jadikan_admin(lingkungan, "paul", False)
    p = lingkungan["users"]
    d = json.loads(p.read_text())
    d["lain"] = {"hash": "x", "admin": True}
    p.write_text(json.dumps(d))
    klien.get("/logout")
    masuk(klien, "paul", PW_PAUL)

    j = klien.post("/api/akun/setujui", params={"akun": "rizky"}).json()
    assert j["ok"] is False and "admin" in j["error"]
    assert _users(lingkungan)["rizky"]["menunggu"] is True


def test_pendaftaran_bisa_dimatikan_lewat_setelan(klien, lingkungan, monkeypatch):
    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("LABELAPP_DAFTAR_SENDIRI", "0")
    try:
        r = klien.post("/daftar", data={"user": "rizky", "pw": "12345678",
                                        "pw2": "12345678"})
        assert r.status_code == 403 and "dimatikan" in r.text
        assert "rizky" not in _users(lingkungan)
    finally:
        get_settings.cache_clear()


def test_daftar_langsung_aktif_kalau_setelannya_dinyalakan(klien, lingkungan,
                                                           monkeypatch):
    """Hanya pantas dinyalakan kalau firewall membatasi siapa yang bisa
    menjangkau aplikasi ini; karena itu bawaannya mati."""
    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("LABELAPP_DAFTAR_LANGSUNG", "1")
    try:
        r = klien.post("/daftar", data={"user": "rizky", "pw": "sandi-rizky-1",
                                        "pw2": "sandi-rizky-1"})
        assert r.status_code == 200 and "sudah bisa dipakai" in r.text
        assert "menunggu" not in _users(lingkungan)["rizky"] \
            or _users(lingkungan)["rizky"]["menunggu"] is False
        assert masuk(klien, "rizky", "sandi-rizky-1"), "harusnya langsung bisa masuk"
    finally:
        get_settings.cache_clear()
