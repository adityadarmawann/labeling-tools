"""Labeler berakses menyeluruh harus punya jalan ke gambar yang belum dibagi.

Haknya sudah ada: boleh_labeli mengembalikan True untuk gambar apa pun di
projek itu. Yang tidak ada jalannya. Papan menggerbangi seluruh aksi kolom
"Belum ditugaskan" dengan boleh_bagi (pemilik/Editor saja), halaman job hanya
memuat yang sudah dibagi, dan kisi Dataset hanya memuat yang sudah dinyatakan
masuk. Pemilik memberi peran "Labeler menyeluruh", orangnya login, dan tidak
menemukan satu gambar pun untuk dikerjakan.
"""
from __future__ import annotations

import pathlib

from conftest import buat_dataset, masuk
from tests.test_data import PW_PAUL, PW_ANGGI
from app.services import tugas as svc


def _data(peran="pelabel", akses="semua"):
    return {"pemilik": "paul", "kurasi": True, "warisan": False,
            "tugas": {}, "dataset": [],
            "anggota": {"anggi": {"peran": peran, "akses": akses}}}


def test_labeler_menyeluruh_boleh_lepas():
    assert svc.boleh_labeli_lepas(_data(), "anggi") is True


def test_labeler_spesifik_tidak_boleh_lepas():
    assert svc.boleh_labeli_lepas(_data(akses="spesifik"), "anggi") is False


def test_labeler_tanpa_akses_tidak_boleh_lepas():
    assert svc.boleh_labeli_lepas(_data(akses=""), "anggi") is False


def test_pemilik_dan_editor_boleh_lepas():
    assert svc.boleh_labeli_lepas(_data(), "paul") is True
    assert svc.boleh_labeli_lepas(_data(peran="editor", akses=""), "anggi") is True


def test_orang_luar_tidak_boleh_lepas():
    assert svc.boleh_labeli_lepas(_data(), "orang-lain") is False


def test_sejalan_dengan_boleh_labeli():
    """Gerbang antarmuka tidak boleh lebih ketat daripada hak tulis nyatanya:
    tombol yang disembunyikan dari orang yang rutenya menerima adalah bug yang
    sama seriusnya dengan tombol yang rutenya menolak."""
    d = _data()
    # Gambar yang belum ditugaskan ke siapa pun.
    assert svc.boleh_labeli(d, "anggi", "a.jpg", batch="") is True
    assert svc.boleh_labeli_lepas(d, "anggi") is True
    d2 = _data(akses="spesifik")
    assert svc.boleh_labeli(d2, "anggi", "a.jpg", batch="") is False
    assert svc.boleh_labeli_lepas(d2, "anggi") is False


def test_papan_menawarkan_jalan_kerja(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_dataset(ruang / "lepas", 3, 0)
    svc.mulai_kurasi(d, "paul")
    klien.post(f"/setsrc?path={d}")
    h = klien.get("/anotasi", params={"ds": d.name}).text
    assert "Labeli sendiri" in h
    # Mengantar ke lingkup yang memang memuat golongan gambar ini.
    assert "f=unlab&amp;dsf=belum" in h or "f=unlab&dsf=belum" in h
