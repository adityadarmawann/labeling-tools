"""GroundingDINO: label hasil harus istilah yang diminta, bukan pecahannya.

Dilaporkan dari pemakaian di dev. Satu nama kelas dicari,
`kahf_skinergizing_facewash_50ml`, dan daftar kelas projek pulang berisi
tiga belas "kelas" baru: `kahf _ skiner facewash _ml`, `kaf skin face`,
bahkan `ka`.

Sebabnya bukan salah ketik. GroundingDINO melakukan phrase grounding: untuk
tiap kotak ia mengembalikan potongan token prompt yang cocok, bukan istilah
yang kita kirim. Nama kelas ber-garis-bawah dipecah tokenizer jadi subkata
lalu dirangkai kembali apa adanya, lengkap dengan spasi di sekitar "_".

Ditulis mentah sebagai nama kelas, tiap pecahan harus dihapus dengan tangan
satu per satu. Padahal tiap deteksi PASTI berasal dari salah satu istilah
yang kita kirim.
"""
from __future__ import annotations

import pytest

from app.services.autolabel import cocokkan_istilah

KELAS = ["kahf_skinergizing_facewash_50ml", "kahf_extradry_deodorant_45ml"]

# Pecahan NYATA yang tertulis di dev-data, bukan contoh karangan.
PECAHAN_FACEWASH = [
    "kahf _ skin facewash", "kaf skin face", "kahf skin facewash",
    "kahf _ skiner facewash _ml", "kahf _ skiner _ facewash _ml",
    "kahf _ skinergizing _ facewash _ml", "kahf _ skiner facewash",
    "kahf _ skiner facewashml", "kahf skin face", "ka skin",
]


@pytest.mark.parametrize("frasa", PECAHAN_FACEWASH)
def test_pecahan_kembali_ke_kelasnya(frasa):
    assert cocokkan_istilah(frasa, KELAS) == KELAS[0]


def test_frasa_utuh_tetap_utuh():
    for k in KELAS:
        assert cocokkan_istilah(k, KELAS) == k


def test_tak_pernah_melahirkan_kelas_baru():
    """Inti perbaikannya: apa pun yang dikembalikan model, yang ditulis selalu
    salah satu istilah yang diminta. Termasuk pecahan dua huruf yang ambigu:
    pilihannya memang tebakan, tetapi tebakan di antara kelas yang NYATA jauh
    lebih baik daripada kelas karangan yang harus dihapus tangan."""
    for frasa in PECAHAN_FACEWASH + ["ka", "", "   ", "entah apa"]:
        assert cocokkan_istilah(frasa, KELAS) in KELAS


def test_istilah_tunggal_selalu_dipakai():
    satu = ["kahf_extradry_deodorant_45ml"]
    for frasa in ("ka", "kahf _ skiner", "apa pun", ""):
        assert cocokkan_istilah(frasa, satu) == satu[0]


def test_ejaan_asli_dipertahankan():
    """Prompt dikirim huruf kecil (format GroundingDINO), tetapi nama kelas
    yang ditulis harus ejaan yang diketik orang. Tanpa ini "Botol PET" pulang
    jadi "botol pet" tanpa ada yang memintanya."""
    assert cocokkan_istilah("botol pet", ["Botol PET", "Kaleng"]) == "Botol PET"


def test_tanpa_istilah_tak_meledak():
    assert cocokkan_istilah("apa pun", []) == "apa pun"


def test_adapter_memakai_pemeta_itu():
    """Penjaga supaya pemetaannya tak terlepas lagi dari jalur nyatanya."""
    import inspect
    from app.services import autolabel
    src = inspect.getsource(autolabel.dari_groundingdino)
    assert "cocokkan_istilah(l, asli)" in src
    assert "str(l).strip() or teks[0]" not in src
