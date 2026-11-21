"""Pencarian kelas di kanvas mencocokkan DI MANA SAJA, bukan dari huruf awal.

Dilaporkan dari pemakaian: taksonomi projek berawalan seragam
("pet_fanta", "pet_frestea", "pet_sprite"), jadi pencocokan berawalan menuntut
orang mengetik ulang awalan yang sama berkali-kali sebelum daftarnya
menyempit. Untuk sampai ke sprite harus mengetik "pet_s" dulu, padahal yang
ada di kepala orang cuma "sprite".

Pelengkapan sebaris SENGAJA tetap berawalan, dan itu bukan kelalaian:
melengkapi sebaris berarti menambahkan huruf di belakang yang sudah diketik,
dan dari "s" tak ada cara menambah huruf sampai menjadi "pet_sprite" tanpa
mengubah yang sudah diketik orang.
"""
from __future__ import annotations

import pathlib
import re

JS = pathlib.Path("app/static/label.js").read_text()


def _gambar_daftar() -> str:
    i = JS.index("function gambarDaftar(")
    return JS[i:JS.index("hapusTerakhir", i)]


def test_daftar_disaring_di_mana_saja():
    blok = _gambar_daftar()
    assert "toLowerCase().includes(ketik)" in blok
    # pencocokan berawalan tak lagi jadi SATU-SATUNYA penyaring
    assert ".filter(k => !ketik || k.toLowerCase().startsWith(ketik))" not in blok


def test_yang_berawalan_tetap_didahulukan():
    """Tanpa ini, mengetik "pet" bisa memberi "sample_pet" lebih dulu, dan
    pencocokan di mana saja terasa lebih berantakan daripada berawalan."""
    blok = _gambar_daftar()
    assert "const awalan" in blok and "const sisa" in blok
    assert "[...awalan, ...sisa]" in blok


def test_pelengkapan_sebaris_tetap_berawalan():
    """Diuji supaya tak ikut "diperbaiki" jadi includes oleh orang yang
    melihat penyaring di atasnya memakai includes."""
    i = JS.index("Pelengkapan sebaris")
    blok = JS[i:i + 900]
    assert "startsWith(v.toLowerCase())" in blok
    assert "SENGAJA tetap berawalan" in blok


def test_tanpa_ketikan_daftarnya_utuh():
    blok = _gambar_daftar()
    # ketik kosong -> semua kelas, tanpa dipecah jadi dua kelompok
    assert "ketik ? cocok.filter" in blok
    assert ": cocok;" in blok


def test_pencocokannya_tak_peka_huruf_besar():
    blok = _gambar_daftar()
    assert blok.count("toLowerCase()") >= 3
