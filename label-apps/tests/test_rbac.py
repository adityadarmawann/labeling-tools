"""
Uji model peran & akses anggota projek (RBAC) di services/tugas.py.

Yang dijaga: Editor boleh unggah + label semua tapi tak mengelola; Labeler
menyeluruh label semua; Labeler spesifik hanya batch dalam scope-nya (plus
gambar yang ditugaskan eksplisit lewat papan); anggota LAMA tanpa kolom akses
tetap berperilaku warisan (hanya jatah papan). Semuanya tanpa HTTP.
"""
from __future__ import annotations

import pytest

from app.services import tugas


def _projek(tmp_path, nama="p"):
    d = tmp_path / nama
    d.mkdir(parents=True, exist_ok=True)
    return d


# ----------------------------------------------------------- validasi kecil

def test_sah_peran_dan_akses():
    assert tugas.sah_peran("editor") == "editor"
    assert tugas.sah_peran("PELABEL") == "pelabel"
    assert tugas.sah_peran("ngaco") == tugas.PERAN_BAWAAN
    assert tugas.sah_akses("spesifik") == "spesifik"
    assert tugas.sah_akses("x") == tugas.AKSES_BAWAAN


def test_bersih_batch_rapi_unik_urut():
    assert tugas._bersih_batch("b, a, a ,") == ["a", "b"]
    assert tugas._bersih_batch(["z", "z", "A"]) == ["A", "z"]
    assert tugas._bersih_batch(None) == []


# ------------------------------------------------------------ peran_anggota

def test_peran_anggota(tmp_path):
    d = _projek(tmp_path)
    tugas.undang(d, "own", "ed", peran="editor")
    tugas.undang(d, "own", "lab", peran="pelabel", akses="semua")
    data = tugas.baca(d, "own")
    assert tugas.peran_anggota(data, "own") == "pemilik"
    assert tugas.peran_anggota(data, "ed") == "editor"
    assert tugas.peran_anggota(data, "lab") == "pelabel"
    assert tugas.peran_anggota(data, "asing") == ""


# ------------------------------------------------------------ boleh_unggah

def test_boleh_unggah_owner_dan_editor_saja(tmp_path):
    d = _projek(tmp_path)
    tugas.undang(d, "own", "ed", peran="editor")
    tugas.undang(d, "own", "lab", peran="pelabel", akses="semua")
    data = tugas.baca(d, "own")
    assert tugas.boleh_unggah(data, "own") is True
    assert tugas.boleh_unggah(data, "ed") is True
    assert tugas.boleh_unggah(data, "lab") is False
    assert tugas.boleh_unggah(data, "asing") is False


def test_boleh_unggah_projek_warisan_owner_saja(tmp_path):
    d = _projek(tmp_path)
    data = tugas.baca(d, "own")          # tanpa berkas tugas = warisan
    assert tugas.boleh_unggah(data, "own") is True
    assert tugas.boleh_unggah(data, "siapa") is False


# ------------------------------------------------------------ boleh_labeli

def test_editor_boleh_label_semua(tmp_path):
    d = _projek(tmp_path)
    tugas.undang(d, "own", "ed", peran="editor")
    data = tugas.baca(d, "own")
    assert tugas.boleh_labeli(data, "ed", "img1", batch="apa pun") is True
    assert tugas.boleh_labeli(data, "ed", "img2") is True


def test_labeler_menyeluruh_boleh_semua(tmp_path):
    d = _projek(tmp_path)
    tugas.undang(d, "own", "lab", peran="pelabel", akses="semua")
    data = tugas.baca(d, "own")
    assert tugas.boleh_labeli(data, "lab", "img1", batch="b1") is True
    assert tugas.boleh_labeli(data, "lab", "img2", batch="") is True


def test_labeler_spesifik_hanya_batch_dalam_scope(tmp_path):
    d = _projek(tmp_path)
    tugas.undang(d, "own", "lab", peran="pelabel", akses="spesifik",
                 batch=["dataset-a", "dataset-c"])
    data = tugas.baca(d, "own")
    assert tugas.boleh_labeli(data, "lab", "g1", batch="dataset-a") is True
    assert tugas.boleh_labeli(data, "lab", "g2", batch="dataset-c") is True
    assert tugas.boleh_labeli(data, "lab", "g3", batch="dataset-b") is False
    # Tanpa batch yang diberikan, spesifik jatuh ke penugasan papan (tak ada).
    assert tugas.boleh_labeli(data, "lab", "g1") is False


def test_labeler_spesifik_tetap_boleh_gambar_yang_ditugaskan(tmp_path):
    d = _projek(tmp_path)
    tugas.undang(d, "own", "lab", peran="pelabel", akses="spesifik",
                 batch=["dataset-a"])
    tugas.tugaskan(d, "own", "lab", ["luar1"])      # di luar scope batch
    data = tugas.baca(d, "own")
    # Penugasan eksplisit pemilik menang meski batch di luar scope.
    assert tugas.boleh_labeli(data, "lab", "luar1", batch="dataset-z") is True


def test_anggota_lama_tanpa_akses_tetap_warisan(tmp_path):
    d = _projek(tmp_path)
    # Undangan polos (rute lama): tak ada pilihan akses -> kolom akses absen.
    tugas.undang(d, "own", "lab")
    data = tugas.baca(d, "own")
    assert "akses" not in data["anggota"]["lab"]
    # Belum ditugaskan apa pun -> tak boleh melabeli, meski ada batch.
    assert tugas.boleh_labeli(data, "lab", "g1", batch="apa") is False
    # Setelah ditugaskan lewat papan -> boleh gambar itu saja.
    tugas.tugaskan(d, "own", "lab", ["g1"])
    data = tugas.baca(d, "own")
    assert tugas.boleh_labeli(data, "lab", "g1") is True
    assert tugas.boleh_labeli(data, "lab", "g2") is False


def test_boleh_kelola_tetap_owner_saja(tmp_path):
    d = _projek(tmp_path)
    tugas.undang(d, "own", "ed", peran="editor")
    data = tugas.baca(d, "own")
    assert tugas.boleh_kelola(data, "own") is True
    assert tugas.boleh_kelola(data, "ed") is False      # editor TAK mengelola


# ------------------------------------------------------------ undang / atur

def test_undang_editor_tak_menyimpan_scope(tmp_path):
    d = _projek(tmp_path)
    # Mula-mula labeler spesifik, lalu dinaikkan jadi editor: scope harus bersih.
    tugas.undang(d, "own", "x", peran="pelabel", akses="spesifik", batch=["a"])
    tugas.undang(d, "own", "x", peran="editor")
    a = tugas.baca(d, "own")["anggota"]["x"]
    assert a["peran"] == "editor" and "akses" not in a and "batch" not in a


def test_atur_anggota_ubah_peran_dan_scope(tmp_path):
    d = _projek(tmp_path)
    tugas.undang(d, "own", "x", peran="pelabel", akses="semua")
    r = tugas.atur_anggota(d, "own", "x", akses="spesifik", batch=["a", "b"])
    assert r["ok"] and r["akses"] == "spesifik" and r["batch"] == ["a", "b"]
    # Kembali ke menyeluruh membuang daftar batch.
    r = tugas.atur_anggota(d, "own", "x", akses="semua")
    assert r["batch"] == []
    a = tugas.baca(d, "own")["anggota"]["x"]
    assert "batch" not in a


def test_atur_anggota_bukan_anggota_ditolak(tmp_path):
    d = _projek(tmp_path)
    r = tugas.atur_anggota(d, "own", "hantu", peran="editor")
    assert r["ok"] is False


# ------------------------------------------------- undangan email bawa peran

def test_undangan_email_membawa_peran_dan_scope(tmp_path):
    d = _projek(tmp_path)
    r = tugas.undang_email(d, "own", "a@b.co", peran="pelabel",
                           akses="spesifik", batch=["dataset-a"])
    tok = r["token"]
    assert tugas.pakai_undangan(d, tok, "andi")["ok"]
    a = tugas.baca(d, "own")["anggota"]["andi"]
    assert a["peran"] == "pelabel" and a["akses"] == "spesifik"
    assert a["batch"] == ["dataset-a"]


def test_undangan_email_polos_jadi_warisan(tmp_path):
    d = _projek(tmp_path)
    tok = tugas.undang_email(d, "own", "a@b.co")["token"]
    tugas.pakai_undangan(d, tok, "andi")
    a = tugas.baca(d, "own")["anggota"]["andi"]
    assert a["peran"] == "pelabel" and "akses" not in a


# ------------------------------------------------- end-to-end lewat HTTP

def test_labeler_spesifik_simpan_hanya_dalam_scope(klien, aplikasi, lingkungan):
    """Rantai penuh: batch gambar -> tolak_tulis -> scope, lewat /api/simpan."""
    import pathlib

    from conftest import klien_baru
    from tests.test_data import masuk, PW_ANGGI, PW_PAUL
    from tests.test_projek import _projek as buat_projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_projek(ruang, "scoped", n=2, label=False)
    klien.post(f"/setsrc?path={d}")
    g = sorted(str(q) for q in d.glob("*.jpg"))

    # Pemilik memberi batch berbeda ke tiap gambar, lalu menambah anggi sebagai
    # labeler spesifik yang hanya berhak atas 'dataset-a'. (Rute UI peran dibuat
    # di langkah berikut; di sini scope-nya disetel lewat service.)
    klien.post("/api/tag/pasang", json={"paths": [g[0]], "batch": "dataset-a"})
    klien.post("/api/tag/pasang", json={"paths": [g[1]], "batch": "dataset-b"})
    tugas.undang(d, "paul", "anggi", peran="pelabel", akses="spesifik",
                 batch=["dataset-a"])

    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    anggi.get("/?ds=paul/scoped")
    simpan = lambda p: anggi.post("/api/simpan", json={"path": p, "shapes": [
        {"label": "x", "shape_type": "rectangle",
         "points": [[1, 1], [5, 5]]}]}).json()

    assert simpan(g[0]).get("ok") is True          # dalam scope
    r = simpan(g[1])                               # luar scope
    assert r.get("ok") is False and "ditugaskan" in (r.get("error") or "")
