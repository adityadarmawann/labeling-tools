"""
Uji unggah video ke projek VIDEO (Langkah 1), lewat HTTP.

Yang dijaga: video yang diunggah ke projek jenis "video" mendarat UTUH di
_sumber/ — tidak diekstrak jadi frame dan tidak dibuang (bedanya dengan projek
image) — sementara perilaku projek image tak berubah, dan Labeler (anggota yang
tak berhak mengunggah) ditolak alih-alih diam-diam menulis ke folder pemilik.
"""
from __future__ import annotations

from app.services import tugas
from tests.conftest import PW_ANGGI, PW_PAUL, klien_baru, masuk
from tests.test_video_unggah import _buat_projek, _video_bytes


def test_video_ke_projek_video_mendarat_di_sumber(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "aksi", "video")
    data = _video_bytes(lingkungan["tmp"])

    r = klien.put("/upload?ds=aksi&name=klip.mp4", content=data)
    j = r.json()
    assert j["ok"] and j["video"] is True, j
    # JS diberi tahu videonya mendarat di _sumber/ (jangan panggil /ekstrak).
    assert j["sumber"] == "_sumber/klip.mp4", j

    proj = lingkungan["ruang"] / "aksi"
    # Video UTUH di _sumber/, bukan di akar; tidak ada frame yang diekstrak.
    assert (proj / "_sumber" / "klip.mp4").exists()
    assert (proj / "_sumber" / "klip.mp4").read_bytes() == data   # tak tersentuh
    assert not (proj / "klip.mp4").exists()
    assert list(proj.rglob("*.jpg")) == []                        # tak diekstrak


def test_projek_video_tolak_bukan_video(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "aksi2", "video")
    # Projek video tak punya alur gambar/arsip: hanya ekstensi video diterima.
    r = klien.put("/upload?ds=aksi2&name=foto.jpg", content=b"x" * 50)
    assert r.json()["ok"] is False, r.text


def test_projek_image_tak_berubah(klien, lingkungan):
    # Perilaku lama: video ke projek IMAGE tetap mendarat di AKAR (untuk
    # /ekstrak), tanpa medan "sumber".
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "img", "image")
    data = _video_bytes(lingkungan["tmp"])
    r = klien.put("/upload?ds=img&name=klip.mp4", content=data)
    j = r.json()
    assert j["ok"] and j["video"] is True and "sumber" not in j, j

    proj = lingkungan["ruang"] / "img"
    assert (proj / "klip.mp4").exists()                 # di akar, bukan _sumber
    assert not (proj / "_sumber").exists()


def test_labeler_tak_boleh_unggah_video(klien, aplikasi, lingkungan):
    # Paul (pemilik) membuat projek video lalu mengundang Anggi sebagai Labeler.
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "aksi-rbac", "video")
    proj = lingkungan["ruang"] / "aksi-rbac"
    tugas.undang(proj, "paul", "anggi", peran="pelabel", akses="semua")

    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    data = _video_bytes(lingkungan["tmp"])
    r = anggi.put("/upload?ds=paul/aksi-rbac&name=klip.mp4", content=data)
    j = r.json()
    # Labeler teratasi lewat temukan tapi gagal boleh_unggah -> ditolak, dan
    # videonya tak pernah menyentuh folder pemilik.
    assert j["ok"] is False and "berhak" in j["error"], j
    assert not (proj / "_sumber").exists()
