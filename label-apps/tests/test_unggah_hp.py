"""Unggah dari HP: token + QR + halaman ponsel.

Yang dijaga: tautan bertoken menitipkan SATU hak (menambah gambar ke SATU
projek, atas nama SATU akun) tanpa login, dan tidak lebih dari itu. Jalur
unggahnya sama persis dengan unggah laptop, jadi uji di sini berfokus pada
yang baru: penerbitan token, cakupannya, dan halaman ponsel."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.services import unggah_token
from conftest import PW_ANGGI, PW_PAUL, masuk


# --------------------------------------------------------------- service

def test_token_roundtrip_dan_isolasi():
    t1, exp = unggah_token.buat("paul", "proj-a")
    assert exp > time.time()
    g = unggah_token.ambil(t1)
    assert g and g["user"] == "paul" and g["ds"] == "proj-a"
    # Token lain tak bocor ke yang pertama.
    t2, _ = unggah_token.buat("anggi", "proj-b")
    assert unggah_token.ambil(t2)["user"] == "anggi"
    assert unggah_token.ambil(t1)["ds"] == "proj-a"
    # Palsu/kosong -> None, bukan melempar.
    assert unggah_token.ambil("ngawur") is None
    assert unggah_token.ambil("") is None


def test_token_kedaluwarsa_dibuang():
    t, _ = unggah_token.buat("paul", "p", ttl=1)
    # ttl diklem minimum 60 dtk internal; paksa kedaluwarsa dengan menata ulang
    # waktu exp lewat API publik: terbitkan lalu majukan jam lewat monkey di
    # simpanan. Lebih bersih: terbitkan dengan exp lampau langsung.
    unggah_token._simpan._data[t]["exp"] = time.time() - 1
    assert unggah_token.ambil(t) is None


def test_cabut():
    t, _ = unggah_token.buat("paul", "p")
    unggah_token.cabut(t)
    assert unggah_token.ambil(t) is None


# --------------------------------------------------------------- penerbitan

def test_pemilik_bisa_terbitkan_qr(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    klien.post("/api/projek/baru?nama=lapangan")
    r = klien.post("/api/unggah/token?ds=lapangan").json()
    assert r["ok"], r
    assert "/unggah-hp?t=" in r["url"]
    # Default (tanpa BASIS_PUBLIK) jatuh ke base_url klien uji.
    assert r["url"].startswith("http://testserver/unggah-hp?t=")
    # Token di URL benar-benar sah dan menunjuk projek + akun yang tepat.
    tok = r["url"].split("t=", 1)[1]
    info = unggah_token.ambil(tok)
    assert info["user"] == "paul" and info["ds"] == "lapangan"


def test_token_akun_lain_tak_menyentuh_projek_paul(klien, aplikasi, lingkungan):
    # Properti keamanan yang dijaga: token SELALU terikat ke akun peminta.
    # anggi (bukan anggota projek paul) meminta token "rahasia" -> ia terbit,
    # tetapi terikat ke akun anggi, jadi unggahannya mendarat di RUANG ANGGI,
    # tak pernah menyentuh projek 'rahasia' milik paul. (_folder_unggah
    # membelokkan non-anggota ke ruangnya sendiri; lihat routers/uploads.py.)
    import pathlib
    masuk(klien, "paul", PW_PAUL)
    klien.post("/api/projek/baru?nama=rahasia")
    anggi = masuk(TestClient(aplikasi), "anggi", PW_ANGGI)
    r = anggi.post("/api/unggah/token?ds=rahasia").json()
    assert r["ok"]
    tok = r["url"].split("t=", 1)[1]
    assert unggah_token.ambil(tok)["user"] == "anggi"
    hp = TestClient(aplikasi)
    assert hp.put(f"/unggah-hp/upload?t={tok}&name=sisip.jpg",
                  content=b"x" * 64).json()["ok"]
    # Mendarat di ruang anggi, BUKAN di projek rahasia milik paul.
    root = pathlib.Path(lingkungan["roots"]) / "_unggahan"
    assert (root / "anggi" / "rahasia" / "sisip.jpg").exists()
    assert not (root / "paul" / "rahasia" / "sisip.jpg").exists()


def test_token_butuh_sesi(klien):
    # Tanpa login, menerbitkan token ditolak (401) -- ini bukan jalur publik.
    r = klien.post("/api/unggah/token?ds=apa")
    assert r.status_code == 401


def test_projek_video_tak_ditawari_qr(klien, lingkungan):
    # Ponsel mengunggah GAMBAR; projek video punya alur "video sumber" sendiri.
    masuk(klien, "paul", PW_PAUL)
    klien.post("/api/projek/baru?nama=rekaman&jenis=video")
    r = klien.post("/api/unggah/token?ds=rekaman").json()
    assert not r["ok"] and "gambar" in r["error"].lower()


def test_basis_publik_dipakai_untuk_tautan():
    # Di balik reverse proxy, tautan QR harus memakai alamat publik yang disetel,
    # bukan host loopback yang dilihat uvicorn.
    from types import SimpleNamespace

    from app.routers.uploads import _basis_publik
    req = SimpleNamespace(base_url="http://127.0.0.1:8142/")
    pakai = SimpleNamespace(basis_publik="https://103.182.240.28:8042")
    assert _basis_publik(req, pakai) == "https://103.182.240.28:8042"
    # Kosong -> jatuh ke base_url (benar saat tanpa proxy), tanpa garis miring.
    kosong = SimpleNamespace(basis_publik="")
    assert _basis_publik(req, kosong) == "http://127.0.0.1:8142"


# --------------------------------------------------------------- halaman HP

def test_halaman_hp_sah_menampilkan_form(klien, aplikasi, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    klien.post("/api/projek/baru?nama=sawah")
    tok = klien.post("/api/unggah/token?ds=sawah").json()["url"].split("t=", 1)[1]
    # Ponsel: klien baru TANPA cookie login.
    hp = TestClient(aplikasi)
    h = hp.get(f"/unggah-hp?t={tok}").text
    assert "Ambil foto" in h and "sawah" in h
    assert "unggah_hp.js" in h          # skrip hanya dimuat saat token sah


def test_halaman_hp_token_mati(klien, aplikasi, lingkungan):
    hp = TestClient(aplikasi)
    h = hp.get("/unggah-hp?t=sudah-mati").text
    assert "kedaluwarsa" in h.lower()
    assert "unggah_hp.js" not in h      # tak ada form -> tak ada skrip


# --------------------------------------------------------------- unggah lewat token

def test_unggah_dari_hp_mendarat_di_projek(klien, aplikasi, lingkungan):
    import pathlib
    masuk(klien, "paul", PW_PAUL)
    klien.post("/api/projek/baru?nama=kebun")
    tok = klien.post("/api/unggah/token?ds=kebun").json()["url"].split("t=", 1)[1]

    hp = TestClient(aplikasi)           # tanpa login, hanya token
    r = hp.put(f"/unggah-hp/upload?t={tok}&name=hp-1.jpg", content=b"x" * 128)
    j = r.json()
    assert j["ok"], j
    # Berkasnya benar-benar ada di ruang kerja paul, bukan entah di mana.
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    assert (ruang / "kebun" / "hp-1.jpg").exists()


def test_unggah_token_mati_ditolak(aplikasi, lingkungan):
    hp = TestClient(aplikasi)
    j = hp.put("/unggah-hp/upload?t=palsu&name=a.jpg", content=b"x" * 16).json()
    assert not j["ok"] and "kedaluwarsa" in j["error"].lower()


def _buat_video_kecil(path, n=40, fps=30.0, ukuran=(320, 240)):
    """Video mp4 kecil dengan isi berubah tiap frame (tajam, tak kembar)."""
    import cv2
    import numpy as np
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, ukuran)
    assert vw.isOpened()
    rng = np.random.default_rng(0)
    for _ in range(n):
        vw.write(rng.integers(0, 255, (ukuran[1], ukuran[0], 3), dtype=np.uint8))
    vw.release()
    return path


def test_hp_video_diekstrak_jadi_frame_di_latar(klien, aplikasi, lingkungan):
    """Video dari HP ke projek IMAGE: route balas cepat (ekstrak_latar), lalu
    thread latar mengekstrak jadi frame dan membuang videonya."""
    import pathlib
    import time
    masuk(klien, "paul", PW_PAUL)
    klien.post("/api/projek/baru?nama=klip-hp")
    tok = klien.post("/api/unggah/token?ds=klip-hp").json()["url"].split("t=", 1)[1]
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = ruang / "klip-hp"

    vid = _buat_video_kecil(d.parent / "sumber.mp4")   # bikin di luar projek
    hp = TestClient(aplikasi)
    j = hp.put("/unggah-hp/upload?t=%s&name=rekaman.mp4" % tok,
               content=vid.read_bytes()).json()
    assert j["ok"] and j.get("video") and j.get("ekstrak_latar"), j

    # Thread latar: tunggu frame muncul & video terbuang (maks ~10 dtk).
    frame = []
    for _ in range(100):
        frame = list(d.glob("*.jpg"))
        if frame and not (d / "rekaman.mp4").exists():
            break
        time.sleep(0.1)
    assert frame, "tidak ada frame terekstrak"
    assert not (d / "rekaman.mp4").exists(), "video mentah tak dibuang"


def test_hp_video_rusak_tak_mematikan(tmp_path):
    """Berkas 'video' yang tak bisa dibaca: helper latar menghapusnya & tak
    melempar (tak boleh menjatuhkan server)."""
    from app.routers import uploads
    from app.config import get_settings
    vp = tmp_path / "rusak.mp4"
    vp.write_bytes(b"bukan video")
    uploads._jalankan_ekstrak_latar(vp, tmp_path, get_settings())   # tak boleh raise
    assert not vp.exists()                                    # tetap dibuang


def test_dataset_split_tak_ditawari_qr(klien, lingkungan):
    """Dataset ber-split/YOLO tak boleh terima foto HP: HP mendarat di akar dan
    merusak tata letaknya. Hanya projek gambar DATAR yang ditawari QR."""
    import pathlib
    import cv2
    import numpy as np
    masuk(klien, "paul", PW_PAUL)
    klien.post("/api/projek/baru?nama=rfexport")
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = ruang / "rfexport"
    # Struktur split Roboflow: tiap split = YOLO (images/ + labels/) + gambar.
    for s in ("train", "valid", "test"):
        (d / s / "images").mkdir(parents=True, exist_ok=True)
        (d / s / "labels").mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(d / s / "images" / "a.jpg"),
                    np.zeros((20, 20, 3), np.uint8))
        (d / s / "labels" / "a.txt").write_text("")
    r = klien.post("/api/unggah/token?ds=rfexport").json()
    assert r["ok"] is False, r
    assert "split" in r["error"] or "struktur" in r["error"], r
