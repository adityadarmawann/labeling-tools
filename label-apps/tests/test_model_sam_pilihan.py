"""Pilihan model SAM untuk klik, dan kenapa SAM 3 tak ada di antaranya.

Ditanyakan dari pemakaian: "sam2:small kok jelek, bisa ditambahkan SAM 3
original seperti Roboflow?"

Dua temuan, keduanya diukur bukan ditebak.

SATU: SAM 3 di osam TIDAK PUNYA jalur prompt titik sama sekali. Kata
"points" dan "point_labels" nol kali di modulnya; ia murni prompt teks, dan
memang dipakai begitu di fitur auto-label. Memasukkannya ke daftar klik cuma
akan membuat tiap klik gagal. Di mesin ini ia bahkan tak bisa dimuat:
alokasi 1,64 GB ditolak, di GPU maupun CPU.

DUA: pada 8 gambar berlabel manusia di projek coca-cola, satu klik di tengah
objek, dibanding kotak buatan orang:

    sam2:tiny    IoU rata 0,97   8/8 di atas 0,7   0,21 dtk
    sam2:small   IoU rata 0,99   8/8 di atas 0,7   0,17 dtk
    sam2:large   IoU rata 0,98   8/8 di atas 0,7   0,61 dtk

Jadi yang lebih besar tidak otomatis lebih baik. Keduanya tetap ditawarkan
supaya bentuk yang sulit bisa dicoba sendiri, tetapi bawaannya tak diubah
atas dasar tebakan.
"""
from __future__ import annotations

import pathlib

from app.services import autolabel


def test_varian_lebih_besar_bisa_dipilih():
    assert "sam2:large" in autolabel.MODEL_DIIZINKAN
    assert "sam2:latest" in autolabel.MODEL_DIIZINKAN


def test_bawaannya_tak_diubah_tanpa_bukti():
    """Yang terukur paling baik DAN paling cepat tetap jadi bawaan."""
    assert autolabel.MODEL_DEFAULT == "sam2:small"


def test_sam3_tak_ditawarkan_untuk_klik():
    """Ia tak punya jalur titik; menawarkannya cuma membuat klik gagal."""
    assert "sam3:latest" not in autolabel.MODEL_DIIZINKAN
    assert "sam3:latest" in autolabel.MODEL_TEKS      # di sana ia memang berguna


def test_alasannya_tertulis_di_sebelah_daftarnya():
    """Angka hasil pengukuran disimpan di tempat keputusannya diambil, supaya
    orang berikutnya tak mengulang perbandingan yang sama."""
    src = pathlib.Path("app/services/autolabel.py").read_text()
    i = src.index("MODEL_DIIZINKAN = {")
    blok = src[i:src.index("}", i)]
    assert "IoU rata 0,99" in blok
    assert "TIDAK otomatis lebih baik" in blok


def test_semua_pilihan_memang_dikenal_mesinnya():
    """Nama yang salah ketik baru ketahuan saat orang mengkliknya."""
    dikenal = {"mobilesam", "efficientsam:10m", "efficientsam:latest",
               "sam2:tiny", "sam2:small", "sam2:latest", "sam2:large"}
    assert autolabel.MODEL_DIIZINKAN <= dikenal


def test_pemilih_di_kanvas_memakai_daftar_itu():
    h = pathlib.Path("app/templates/label.html").read_text()
    assert "sam.tersedia" in h
    assert autolabel.info()["tersedia"] == sorted(autolabel.MODEL_DIIZINKAN)
