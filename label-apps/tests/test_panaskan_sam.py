"""Klik pertama pada sebuah gambar tak perlu menunggu encoder.

Dilaporkan dari pemakaian: "aku klik objek, tersegmentasinya 3 detik
kemudian". Diukur di prod dengan sam2:small, model sudah termuat:

    gambar 640x640      klik 1  1,6 sampai 1,8 dtk   klik 2  0,04 dtk
    gambar 2296x4080    klik 1  1,8 sampai 2,2 dtk   klik 2  0,09 dtk

Jadi cache embeddingnya memang bekerja; yang mahal adalah satu kali jalan
encoder per gambar. Melabeli berarti terus berpindah gambar, jadi harga itu
dibayar berulang-ulang dan selalu tepat saat orangnya menunggu.

Yang diperbaiki bukan kecepatan encodernya melainkan WAKTUNYA: dikerjakan
begitu gambarnya tampil, selagi orangnya masih memandangi objek. Terukur
sesudahnya: pemanasan 1,9 dtk di latar, klik pertama 0,12 dtk.
"""
from __future__ import annotations

import pathlib

import pytest

from app.services import autolabel, osam_api


@pytest.fixture(autouse=True)
def cache_bersih():
    autolabel._cache.clear()
    yield
    autolabel._cache.clear()


def _catatan():
    """Tempat "gambar ini sudah di-encode" dicatat, sesuai versi osam.

    Di osam 0.5 embeddingnya disimpan sendiri di _cache. Di 0.12 osam yang
    menyimpannya, dan yang ada di sini hanya catatan gambar mana yang sudah
    dilewati, di _panas. Uji ini soal PERILAKU pemanasan, jadi ia mengikuti
    tempat yang berlaku alih-alih memaku salah satu versi.
    """
    return autolabel._panas if osam_api.baru() else autolabel._cache


@pytest.fixture(autouse=True)
def _bersih():
    """Kedua catatan dikosongkan sebelum dan sesudah tiap uji: keduanya milik
    modul, dan uji yang mewarisi isi uji sebelumnya akan lulus atau gagal
    tergantung urutan."""
    autolabel._cache.clear()
    autolabel._panas.clear()
    yield
    autolabel._cache.clear()
    autolabel._panas.clear()


def _palsukan(monkeypatch, catat: list):
    """_segment palsu yang mencatat persis seperti jalur sungguhan."""
    def fake(img, points, labels, model, eps):
        catat.append((str(img), model))
        _catatan()[autolabel._kunci_cache(img, model)] = "emb"
        raise autolabel.TidakAdaObjek("palsu")
    monkeypatch.setattr(autolabel, "_segment", fake)


def test_panaskan_mengisi_cache(tmp_path, monkeypatch):
    ip = tmp_path / "a.jpg"; ip.write_bytes(b"x" * 64)
    catat: list = []
    _palsukan(monkeypatch, catat)
    assert autolabel.panaskan(ip) is True
    assert autolabel._kunci_cache(ip, autolabel.MODEL_DEFAULT) in _catatan()
    assert len(catat) == 1


def test_panaskan_kedua_kali_tak_menghitung_ulang(tmp_path, monkeypatch):
    """Kanvas memanggilnya tiap gambar tampil dan tiap beralih ke alat AI;
    tanpa penjaga ini satu gambar bisa di-encode berkali-kali."""
    ip = tmp_path / "a.jpg"; ip.write_bytes(b"x" * 64)
    catat: list = []
    _palsukan(monkeypatch, catat)
    autolabel.panaskan(ip)
    assert autolabel.panaskan(ip) is True
    assert len(catat) == 1          # tetap satu kali


def test_panaskan_memakai_mesin_yang_sama(tmp_path, monkeypatch):
    """Dua jalur yang menghitung embedding dengan cara sedikit berbeda akan
    menghasilkan dua entri cache untuk satu gambar, dan pemanasannya jadi
    sia-sia tanpa ada yang tahu."""
    ip = tmp_path / "a.jpg"; ip.write_bytes(b"x" * 64)
    catat: list = []
    _palsukan(monkeypatch, catat)
    autolabel.panaskan(ip)
    kunci = autolabel._kunci_cache(ip, autolabel.MODEL_DEFAULT)
    assert kunci in _catatan()
    # kunci yang sama persis dengan yang dipakai segmentasi
    assert catat[0][1] == autolabel.MODEL_DEFAULT


def test_panaskan_tak_menelan_objek_yang_tak_ketemu(tmp_path, monkeypatch):
    """Titik umpan di sudut wajar tak menemukan apa-apa. Itu bukan kegagalan:
    yang dicari embeddingnya, bukan masknya."""
    ip = tmp_path / "a.jpg"; ip.write_bytes(b"x" * 64)
    _palsukan(monkeypatch, [])
    assert autolabel.panaskan(ip) is True      # tak melempar


def test_panaskan_menolak_model_asing(tmp_path):
    ip = tmp_path / "a.jpg"; ip.write_bytes(b"x" * 64)
    with pytest.raises(autolabel.TidakAdaObjek):
        autolabel.panaskan(ip, "model-karangan")


# ------------------------------------------------------------------ rute
def test_rute_panaskan_ada_dan_tak_pernah_meledak(klien, lingkungan):
    from conftest import buat_dataset, masuk
    from tests.test_data import PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_dataset(ruang / "panas", 1, 0)
    klien.post(f"/setsrc?path={d}")

    # berkas asing: dijawab, bukan 500
    r = klien.post("/api/sam/panaskan", json={"path": "/tak/ada.jpg"})
    assert r.status_code == 200
    assert r.json() == {"ok": False, "siap": False}


def test_rute_panaskan_tak_menghasilkan_mask(klien, lingkungan, monkeypatch):
    """Jawabannya cuma "siap atau tidak". Mengembalikan poligon dari sini
    membuat kanvas tergoda memakainya padahal titik umpannya bukan pilihan
    orang."""
    from conftest import buat_dataset, masuk
    from tests.test_data import PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_dataset(ruang / "panas2", 1, 0)
    klien.post(f"/setsrc?path={d}")
    ip = sorted(d.glob("*.jpg"))[0]
    monkeypatch.setattr(autolabel, "panaskan", lambda img, model: True)
    j = klien.post("/api/sam/panaskan", json={"path": str(ip)}).json()
    assert j == {"ok": True, "siap": True}


# ------------------------------------------------------------- antarmuka
def test_kanvas_memanaskan_saat_gambar_tampil():
    js = pathlib.Path("app/static/label.js").read_text()
    i = js.index("praambilTetangga();")
    assert "panaskanSam();" in js[i:i + 120]


def test_kanvas_memanaskan_saat_beralih_ke_alat_ai():
    """Gambar sering dibuka dengan alat manual lebih dulu; tanpa ini
    pemanasannya terlewat sama sekali."""
    js = pathlib.Path("app/static/label.js").read_text()
    i = js.index("function setMode(")
    assert "panaskanSam()" in js[i:i + 400]


def test_pemanasan_hanya_untuk_alat_ai():
    """Orang yang melabeli manual tak perlu membebani GPU untuk sesuatu yang
    tak akan ia pakai, dan GPU itu dipakai bersama training."""
    js = pathlib.Path("app/static/label.js").read_text()
    assert "const MODE_AI = ['p+', 'p-', 'rect']" in js
    i = js.index("async function panaskanSam(")
    blok = js[i:js.index("/* Ambil-dulu", i)]
    assert "MODE_AI.includes(S.mode)" in blok
    assert "_panasUntuk === D.path" in blok      # sekali per gambar
    assert "catch" in blok                        # gagal tak mengganggu
