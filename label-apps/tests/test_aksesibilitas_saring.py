"""Aksesibilitas kontrol saringan.

Dropdown saringan tidak punya semantik popup sama sekali: tidak ada
aria-haspopup, tidak ada aria-expanded, tidak ada Escape, tidak ada navigasi
panah. Pengguna papan tik bisa MEMBUKA menu kelas tetapi tidak bisa
menutupnya, dan daftar yang bisa berisi lima puluh baris hanya bisa disusuri
dengan Tab satu per satu. Menu akun sudah punya semuanya sejak dulu; menu
saringan tertinggal.

Halaman job lebih buruk lagi: templatnya MENJANJIKAN aria-expanded tetapi
JS-nya tak pernah memperbaruinya, jadi pembaca layar selalu diberi tahu
menunya tertutup termasuk saat terbuka.
"""
from __future__ import annotations

import pathlib

AKAR = pathlib.Path(__file__).resolve().parents[1]
APPJS = (AKAR / "app" / "static" / "app.js").read_text()
JOBJS = (AKAR / "app" / "static" / "job.js").read_text()
INDEX = (AKAR / "app" / "templates" / "index.html").read_text()
VH = (AKAR / "app" / "templates" / "versi_hasil.html").read_text()
CSS = (AKAR / "app" / "static" / "app.css").read_text()


def test_menu_saringan_menyatakan_dirinya_popup():
    assert "setAttribute('aria-haspopup', 'true')" in APPJS
    assert "aria-expanded" in APPJS


def test_escape_menutup_dan_mengembalikan_fokus():
    blok = APPJS.split("function pasangMenu", 1)[1][:2600]
    assert "'Escape'" in blok
    assert "tombol.focus()" in blok


def test_panah_menyusuri_baris_menu():
    blok = APPJS.split("function pasangMenu", 1)[1][:2600]
    for tombol in ("ArrowDown", "ArrowUp", "Home", "End"):
        assert tombol in blok, tombol


def test_semua_dropdown_memakai_pemasang_yang_sama():
    """Satu pemasang, bukan empat salinan: salinan keempat selalu yang lupa
    diberi Escape."""
    assert APPJS.count("pasangMenu(") >= 3
    assert "menu.toggleAttribute('data-buka');" not in APPJS


def test_job_memperbarui_aria_expanded():
    assert "aria-expanded" in JOBJS
    assert "tombol.focus()" in JOBJS


def test_keadaan_aktif_diumumkan():
    assert 'data-on aria-current="true"' in INDEX


def test_kotak_cari_punya_label():
    assert 'class="sr-only" for="cari"' in INDEX


def test_grup_centang_versi_punya_legend():
    assert "<legend class=\"vh-flabel\">Split</legend>" in VH
    assert "<legend class=\"vh-flabel\">Kelas</legend>" in VH
    assert "fieldset.vh-fgrup{border:0" in CSS      # tanpa bingkai bawaan


def test_fokus_papan_tik_terlihat_pada_chip():
    assert ".chip:focus-visible" in CSS
