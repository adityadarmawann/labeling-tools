"""Perapian tag tak boleh menghapus catatan milik gambar yang MASIH ADA.

Kejadian nyata di prod, projek darma/coca-cola 26 November 2026:

    09:41:32 - 09:46:20   1.420 gambar tiba
    10:18:34              log: "tag dirapikan: 1420 catatan tanpa gambar dibuang"

Sesudah itu 1.420 gambar itu tak punya nama batch, dan job yang dibuat
darinya berjudul "Dibagi 2026-11-26". Projek itu tak punya folder sampah sama
sekali, jadi tak pernah ada gambar terhapus yang catatannya pantas dirapikan:
yang dibuang memang milik gambar yang baru masuk. Empat projek terkena,
4.141 gambar.

Sebabnya satu keputusan: yang dijadikan patokan "gambar mana yang ada" adalah
daftar hasil PINDAI di ingatan sesi, bukan isi disk. Daftar itu bisa
tertinggal, dan ketika tertinggal, sebuah halaman yang sifatnya hanya
menampilkan menghapus data orang secara permanen tanpa cadangan.
"""
from __future__ import annotations

import json

from app.services import tag


def _projek(tmp_path, nama_gambar):
    d = tmp_path / "projek"
    d.mkdir()
    for n in nama_gambar:
        (d / n).write_bytes(b"x")
    return d


def test_catatan_bertahan_kalau_berkasnya_masih_ada(tmp_path):
    """Inti perbaikannya. Daftar pindai yang tertinggal tak boleh menghapus."""
    d = _projek(tmp_path, ["a.jpg", "b.jpg", "baru.jpg"])
    tag.pasang(d, ["a.jpg", "b.jpg", "baru.jpg"], batch="unggahan-pagi")

    # Daftar pindai TERTINGGAL: "baru.jpg" belum terlihat olehnya.
    dibuang = tag.rapikan(d, {"a.jpg", "b.jpg"})

    assert dibuang == 0, "berkasnya ada di disk, catatannya tak boleh dibuang"
    assert tag.untuk(tag.baca(d), "baru.jpg")["batch"] == "unggahan-pagi"


def test_catatan_yatim_tetap_dibuang(tmp_path):
    """Yang memang tak punya berkas tetap harus dibersihkan, kalau tidak nama
    batchnya menetap sebagai pilihan hantu di pemilih /bagi."""
    d = _projek(tmp_path, ["a.jpg"])
    tag.pasang(d, ["a.jpg", "sudah-dihapus.jpg"], batch="lama")

    dibuang = tag.rapikan(d, {"a.jpg"})

    assert dibuang == 1
    data = tag.baca(d)
    assert "sudah-dihapus.jpg" not in data["gambar"]
    assert tag.untuk(data, "a.jpg")["batch"] == "lama"


def test_campuran_hanya_yang_hilang_yang_dibuang(tmp_path):
    d = _projek(tmp_path, ["ada1.jpg", "ada2.jpg"])
    tag.pasang(d, ["ada1.jpg", "ada2.jpg", "hilang1.jpg", "hilang2.jpg"],
               batch="b")

    # Daftar pindai hanya melihat satu gambar, padahal dua gambar ada.
    dibuang = tag.rapikan(d, {"ada1.jpg"})

    assert dibuang == 2
    data = tag.baca(d)
    assert set(data["gambar"]) == {"ada1.jpg", "ada2.jpg"}


def test_tak_menulis_apa_pun_saat_tak_ada_yang_yatim(tmp_path):
    """rapikan dipanggil tiap kali papan dibuka; menulis berkas tiap kali itu
    mahal dan membuat mtime-nya berubah tanpa alasan."""
    d = _projek(tmp_path, ["a.jpg"])
    tag.pasang(d, ["a.jpg"], batch="b")
    berkas = d / tag.BERKAS
    sebelum = berkas.stat().st_mtime_ns

    assert tag.rapikan(d, {"a.jpg"}) == 0
    assert tag.rapikan(d, set()) == 0        # daftar kosong pun tak merusak
    assert berkas.stat().st_mtime_ns == sebelum


def test_daftar_pindai_kosong_tak_menghapus_apa_pun(tmp_path):
    """Keadaan terburuk: sesi yang belum memindai sama sekali. Dulu ini
    menghapus SELURUH nama batch di projek."""
    d = _projek(tmp_path, [f"g{i}.jpg" for i in range(20)])
    tag.pasang(d, [f"g{i}.jpg" for i in range(20)], batch="penting")

    assert tag.rapikan(d, set()) == 0
    data = tag.baca(d)
    assert len(data["gambar"]) == 20
    assert all(tag.untuk(data, f"g{i}.jpg")["batch"] == "penting"
               for i in range(20))


def test_klip_memakai_aturan_yang_sama(tmp_path):
    from app.services import klip_tag

    d = tmp_path / "pv"
    (d / "klip").mkdir(parents=True)
    (d / "klip" / "a.mp4").write_bytes(b"x")
    klip_tag.set_label(d, "klip/a.mp4", "lompat")

    assert klip_tag.rapikan(d, set()) == 0, "berkasnya ada, jangan dibuang"
    (d / "klip" / "a.mp4").unlink()
    assert klip_tag.rapikan(d, set()) == 1, "sesudah berkasnya hilang, dibuang"


# ------------------------------------------------- nol gambar terkena

def test_nama_batch_yang_tak_menempel_dilaporkan(klien, lingkungan):
    """Dulu "nol gambar terkena" lewat sebagai keberhasilan.

    Itu setengah lainnya dari keluhan "nama batchnya ada waktu mengunggah,
    hilang waktu dimuat ulang": namanya memang tak pernah tertulis, dan tak
    ada satu pun yang mengatakannya. Penyebab tersering unggahan video, yang
    frame-nya diekstrak di LATAR sesudah pemasangan nama berjalan.
    """
    from conftest import buat_dataset, masuk
    from tests.test_data import PW_PAUL

    d = buat_dataset(lingkungan["ruang"] / "kosong-batch", 2, 0)
    masuk(klien, "paul", PW_PAUL)
    klien.post("/setsrc", params={"path": str(d)})

    # Semua gambar diberi nama lebih dulu -> panggilan kedua tak kebagian.
    j = klien.post("/api/tag/pasang",
                   json={"tanpa_batch": True, "batch": "pertama"}).json()
    assert j["ok"] is True and j["n"] == 2 and j["kosong"] is False

    j = klien.post("/api/tag/pasang",
                   json={"tanpa_batch": True, "batch": "kedua"}).json()
    assert j["ok"] is True
    assert j["n"] == 0
    assert j["kosong"] is True, "nol gambar terkena harus DIKATAKAN, bukan diam"


def test_peramban_memeriksa_jawabannya():
    """Penjaga: jawaban yang tak diperiksa sama saja dengan tak dikirim."""
    import pathlib as _p

    js = _p.Path("app/static/unggah.js").read_text()
    blok = js.split("async function tandai()", 1)[1].split("\n    }", 1)[0]
    assert "j.kosong" in blok or "j.n === 0" in blok
    assert "toast(" in blok
