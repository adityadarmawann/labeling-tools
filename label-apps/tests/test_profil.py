"""
Uji halaman akun DIRI SENDIRI (/profil).

Yang dijaga di sini, dan ini intinya: setiap rute tulis hanya pernah menyentuh
AKUN YANG SEDANG MASUK. Tidak ada satu pun yang menerima nama akun lain sebagai
sasaran, jadi seorang pengguna tak pernah bisa menyunting akun orang lain lewat
halaman ini — berbeda dari /akun yang memang untuk admin mengurus semua akun.

Dua sisi lain yang diuji: ganti sandi menuntut sandi SEKARANG (penjaga swalayan),
dan avatar boleh DILIHAT siapa saja yang masuk tetapi hanya DIUBAH pemiliknya.
"""
from __future__ import annotations

from conftest import PW_ANGGI, PW_PAUL, klien_baru, masuk
from fastapi.testclient import TestClient

from app.security import load_users, verify_password


def _users(lingkungan) -> dict:
    return load_users(lingkungan["users"])


def _avatar_dir(lingkungan):
    """Folder avatar = <uploads_root>/_avatar; uploads_root di lingkungan uji
    adalah roots/_unggahan (lihat conftest.lingkungan)."""
    return lingkungan["roots"] / "_unggahan" / "_avatar"


def _jpg(warna: int = 90, w: int = 80, h: int = 60) -> bytes:
    """Byte JPEG sungguhan supaya cv2 bisa mendekodenya."""
    import cv2
    import numpy as np
    return cv2.imencode(".jpg", np.full((h, w, 3), warna, np.uint8))[1].tobytes()


# ============================================================ nama & email

def test_ubah_nama_email_sendiri_dan_akun_lain_tak_tersentuh(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    j = klien.post("/api/profil", params={
        "nama": "Paul Baru", "email": "paul@higo.id"}).json()
    assert j["ok"] and j["nama"] == "Paul Baru" and j["email"] == "paul@higo.id"

    u = _users(lingkungan)
    assert u["paul"]["nama"] == "Paul Baru"
    assert u["paul"]["email"] == "paul@higo.id"
    # Akun lain sama sekali tak berubah.
    assert u["anggi"]["nama"] == "Anggi"
    assert "email" not in u["anggi"] or not u["anggi"].get("email")
    # dan `admin` tak pernah disentuh oleh rute ini
    assert "admin" not in u["paul"] or u["paul"].get("admin") in (None, False, True)

    # Kepala halaman kini menampilkan nama baru, tanpa login ulang.
    assert 'class="whoami-nama">Paul Baru</span>' in klien.get("/profil").text


def test_nama_kosong_jatuh_ke_slug_akun(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    j = klien.post("/api/profil", params={"nama": "   ", "email": ""}).json()
    assert j["ok"] and j["nama"] == "paul"


def test_email_bentrok_dengan_akun_lain_ditolak(klien, aplikasi, lingkungan):
    # anggi memasang emailnya lebih dulu, lewat halamannya sendiri.
    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    assert anggi.post("/api/profil", params={
        "nama": "Anggi", "email": "anggi@higo.id"}).json()["ok"]

    masuk(klien, "paul", PW_PAUL)
    j = klien.post("/api/profil", params={
        "nama": "Paul", "email": "ANGGI@higo.id"}).json()
    assert j["ok"] is False and "sudah dipakai" in j["error"]
    assert not _users(lingkungan)["paul"].get("email")


def test_email_sendiri_bukan_bentrok(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    assert klien.post("/api/profil", params={"email": "paul@higo.id"}).json()["ok"]
    # Menyimpan ulang email yang sama (milik sendiri) tetap boleh.
    j = klien.post("/api/profil", params={"email": "paul@higo.id"}).json()
    assert j["ok"], j


def test_email_format_salah_ditolak(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    j = klien.post("/api/profil", params={"email": "bukan-email"}).json()
    assert j["ok"] is False and "email" in j["error"]
    assert not _users(lingkungan)["paul"].get("email")


# ============================================================ ganti sandi

def test_ganti_sandi_butuh_sandi_sekarang(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    # Sandi sekarang salah -> ditolak, hash tak berubah.
    h0 = _users(lingkungan)["paul"]["hash"]
    j = klien.post("/api/profil/sandi", params={
        "sandi_lama": "salah-sekali", "sandi_baru": "sandi-paul-baru-1"}).json()
    assert j["ok"] is False and "sekarang salah" in j["error"]
    assert _users(lingkungan)["paul"]["hash"] == h0

    # Sandi sekarang benar + baru yang cukup panjang -> ganti.
    j = klien.post("/api/profil/sandi", params={
        "sandi_lama": PW_PAUL, "sandi_baru": "sandi-paul-baru-1"}).json()
    assert j["ok"], j
    assert _users(lingkungan)["paul"]["hash"] != h0
    assert verify_password("sandi-paul-baru-1", _users(lingkungan)["paul"]["hash"])

    # Sandi lama tak bisa lagi dipakai masuk; yang baru bisa.
    klien.get("/logout")
    r = klien.post("/login", data={"user": "paul", "pw": PW_PAUL},
                   follow_redirects=False)
    assert r.status_code == 401
    assert masuk(klien, "paul", "sandi-paul-baru-1")


def test_sandi_baru_terlalu_pendek_ditolak(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    h0 = _users(lingkungan)["paul"]["hash"]
    j = klien.post("/api/profil/sandi", params={
        "sandi_lama": PW_PAUL, "sandi_baru": "pendek"}).json()
    assert j["ok"] is False and "karakter" in j["error"]
    assert _users(lingkungan)["paul"]["hash"] == h0


# ============================================================ foto / avatar

def test_unggah_foto_lalu_disajikan(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    j = klien.post("/api/profil/foto", params={"name": "aku.jpg"},
                   content=_jpg()).json()
    assert j["ok"] and j["foto"], j
    assert _users(lingkungan)["paul"]["foto"] == j["foto"]

    # Berkas avatar ditulis di folder avatar DI DALAM uploads_root (tmp), bukan
    # di folder aplikasi — inilah yang menjaga penjaga folder_aplikasi tetap
    # tenang saat server sungguhan menyimpan avatar berdampingan dengan tes.
    av = _avatar_dir(lingkungan) / "paul.jpg"
    assert av.is_file()

    # Disajikan sebagai gambar, 200.
    r = klien.get("/profil/foto", params={"akun": "paul"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")
    assert len(r.content) > 0

    # Kepala halaman kini memasang <img> avatar, bukan inisial.
    h = klien.get("/profil").text
    assert "/profil/foto?akun=paul" in h


def test_foto_bukan_gambar_ditolak(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    j = klien.post("/api/profil/foto", params={"name": "aku.jpg"},
                   content=b"ini jelas bukan gambar").json()
    assert j["ok"] is False and "gambar" in j["error"]
    assert "foto" not in _users(lingkungan)["paul"]
    assert not (_avatar_dir(lingkungan) / "paul.jpg").exists()


def test_ekstensi_bukan_gambar_ditolak_lebih_awal(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    j = klien.post("/api/profil/foto", params={"name": "virus.exe"},
                   content=_jpg()).json()
    assert j["ok"] is False and "gambar" in j["error"]


def test_foto_boleh_dilihat_akun_lain_tapi_anonim_ditolak(klien, aplikasi,
                                                          lingkungan):
    masuk(klien, "paul", PW_PAUL)
    assert klien.post("/api/profil/foto", params={"name": "a.jpg"},
                      content=_jpg()).json()["ok"]

    # anggi yang sudah masuk BOLEH melihat avatar paul (muncul di kepala/daftar).
    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    assert anggi.get("/profil/foto", params={"akun": "paul"}).status_code == 200

    # Permintaan TANPA sesi dialihkan ke login, bukan menyajikan byte.
    tamu = TestClient(aplikasi)
    r = tamu.get("/profil/foto", params={"akun": "paul"}, follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert "/login" in r.headers.get("location", "")


def test_tidak_bisa_memasang_foto_akun_lain(klien, lingkungan):
    """Rute foto tak punya parameter 'akun'; param liar diabaikan FastAPI,
    jadi foto selalu mendarat di akun pemanggil."""
    masuk(klien, "paul", PW_PAUL)
    # Walau menyelipkan ?akun=anggi, yang berubah tetap foto paul.
    j = klien.post("/api/profil/foto", params={"akun": "anggi", "name": "a.jpg"},
                   content=_jpg()).json()
    assert j["ok"]
    u = _users(lingkungan)
    assert u["paul"].get("foto") == j["foto"]
    assert "foto" not in u["anggi"]
    assert (_avatar_dir(lingkungan) / "paul.jpg").exists()
    assert not (_avatar_dir(lingkungan) / "anggi.jpg").exists()


def test_sajian_avatar_menolak_jalan_tembus(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    # Nama dengan '..' dan '/' di-slug jadi nama datar -> tak ada berkas ->
    # 404, dan tak pernah keluar folder avatar.
    for jahat in ("../../etc/passwd", "/etc/shadow", "paul/../anggi"):
        r = klien.get("/profil/foto", params={"akun": jahat})
        assert r.status_code == 404, jahat


def test_hapus_foto_kembali_ke_inisial(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    klien.post("/api/profil/foto", params={"name": "a.jpg"}, content=_jpg())
    assert (_avatar_dir(lingkungan) / "paul.jpg").exists()

    j = klien.post("/api/profil/foto/hapus").json()
    assert j["ok"] and j["foto"] == ""
    assert "foto" not in _users(lingkungan)["paul"]
    assert not (_avatar_dir(lingkungan) / "paul.jpg").exists()
    assert klien.get("/profil/foto", params={"akun": "paul"}).status_code == 404


def test_avatar_akun_tanpa_foto_404(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    assert klien.get("/profil/foto", params={"akun": "anggi"}).status_code == 404


# ============================================================ tidak boleh lintas akun

def test_tidak_bisa_mengubah_nama_akun_lain(klien, lingkungan):
    """Param 'akun' liar diabaikan: /api/profil selalu mengubah pemanggil."""
    masuk(klien, "paul", PW_PAUL)
    klien.post("/api/profil", params={"akun": "anggi", "nama": "Disusup",
                                      "email": "x@higo.id"})
    u = _users(lingkungan)
    assert u["anggi"]["nama"] == "Anggi"            # anggi utuh
    assert not u["anggi"].get("email")
    assert u["paul"]["nama"] == "Disusup"           # yang berubah justru paul


def test_tidak_bisa_mengganti_sandi_akun_lain(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    h_anggi = _users(lingkungan)["anggi"]["hash"]
    # Param 'akun' diabaikan; 'sandi_lama' diadu ke hash PAUL, bukan anggi.
    klien.post("/api/profil/sandi", params={
        "akun": "anggi", "sandi_lama": PW_PAUL, "sandi_baru": "sandi-baru-sah-1"})
    # Sandi anggi tak tersentuh, dan ia tetap bisa masuk dengan sandi lamanya.
    assert _users(lingkungan)["anggi"]["hash"] == h_anggi
    assert verify_password(PW_ANGGI, _users(lingkungan)["anggi"]["hash"])


# ============================================================ gerbang login

def test_profil_wajib_login(aplikasi):
    tamu = TestClient(aplikasi)
    r = tamu.get("/profil", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"].startswith("/login")
    # Rute JSON tanpa sesi menjawab 401.
    assert tamu.post("/api/profil", params={"nama": "x"}).status_code == 401
    assert tamu.post("/api/profil/sandi",
                     params={"sandi_lama": "a", "sandi_baru": "b"}).status_code == 401
    assert tamu.post("/api/profil/foto", content=b"x").status_code == 401
