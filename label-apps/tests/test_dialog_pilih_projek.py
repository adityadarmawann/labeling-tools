"""Pemilih projek tujuan: satu dialog, dua tempat, tanpa prompt() peramban.

Dua tempat menyalin isi projek ke projek lain, dan keduanya dulu memakai
prompt(). prompt() tak bisa dua hal yang justru paling dibutuhkan di sini:
menampilkan projek mana saja yang ADA (namanya harus diketik ulang, salah
satu huruf saja berarti gagal) dan menanyakan apakah labelnya ikut. Ia juga
lahir dengan gaya peramban, bukan gaya aplikasi ini, dan tak kenal tema.
"""
from __future__ import annotations

import pathlib
import re

APP_JS = pathlib.Path("app/static/app.js").read_text()
ANOT_JS = pathlib.Path("app/static/anotasi.js").read_text()
CSS = pathlib.Path("app/static/app.css").read_text()


def _blok(teks, mulai, selesai):
    i = teks.index(mulai)
    return teks[i:teks.index(selesai, i)]


# ------------------------------------------------------- prompt() hilang
def test_tak_ada_lagi_prompt_untuk_memilih_projek():
    for js in (APP_JS, ANOT_JS):
        for baris in js.splitlines():
            if "prompt(" in baris and "projek mana" in baris.lower():
                raise AssertionError("masih memakai prompt(): " + baris.strip())


def test_kedua_tempat_memanggil_dialog_yang_sama():
    assert "await pilihProjek({" in APP_JS
    assert "await pilihProjek({" in ANOT_JS
    # didefinisikan SEKALI, di app.js yang dimuat setiap halaman
    assert APP_JS.count("const pilihProjek = (() => {") == 1
    assert "pilihProjek = " not in ANOT_JS


# ------------------------------------------------------------- isi dialog
def test_dialog_punya_daftar_bukan_ketikan():
    blok = _blok(APP_JS, "const pilihProjek = (() => {", "const Progres = (()")
    assert 'role="radiogroup"' in blok
    assert 'class="sp-baris"' in blok and 'role="radio"' in blok
    # nama projek dipasang sebagai data, bukan diminta diketik ulang
    assert 'data-nama="${esc(x.nama)}"' in blok


def test_dialog_punya_centang_salin_dengan_label():
    blok = _blok(APP_JS, "const pilihProjek = (() => {", "const Progres = (()")
    assert 'id="sp-label"' in blok
    assert "Salin beserta label" in blok
    assert "label: akar.querySelector('#sp-label').checked" in blok


def test_nama_projek_di_escape():
    """Nama projek dibuat orang dan masuk ke innerHTML."""
    blok = _blok(APP_JS, "const pilihProjek = (() => {", "const Progres = (()")
    assert "esc(x.nama)" in blok
    assert "${x.nama}" not in blok


def test_bisa_dipakai_tanpa_tetikus():
    blok = _blok(APP_JS, "const pilihProjek = (() => {", "const Progres = (()")
    for tombol in ("ArrowDown", "ArrowUp", "Enter", "Escape"):
        assert tombol in blok, tombol
    assert "focus()" in blok


def test_pilihan_yang_tersaring_hilang_tak_ikut_terkirim():
    """Kalau tidak, tombolnya mengirim nama yang tak kelihatan di layar."""
    blok = _blok(APP_JS, "function saring()", "function selesai(")
    assert "t.hidden" in blok and "pilih(" in blok


# --------------------------------------------------------------- dua jalur
def test_gabung_mengirim_pilihan_labelnya():
    blok = _blok(APP_JS, "if (a === 'gabung') {", "if (a === 'sampah')")
    assert "label: pil.label ? 1 : 0" in blok
    assert "pil.tujuan" in blok


def test_kirim_ke_projek_mengirim_pilihan_labelnya():
    blok = _blok(ANOT_JS, "async function kirimKeProjek(", "// ---- dialog")
    assert "label: !!pil.label" in blok
    assert "tujuan: pil.tujuan" in blok


def test_hasilnya_menyebut_label_ikut_atau_tidak():
    assert "label_ikut" in ANOT_JS
    blok = _blok(APP_JS, "if (a === 'gabung') {", "if (a === 'sampah')")
    assert "beserta label" in blok and "gambar saja" in blok


# ------------------------------------------------------------------ tema
def test_warnanya_dari_token_tema():
    """Terang dan gelap ikut sendiri hanya kalau tak ada warna harfiah."""
    i = CSS.index(".sp-baris{")
    blok = CSS[i:CSS.index("@media (max-width:560px)", i)]
    assert "var(--" in blok
    harfiah = re.findall(r"#[0-9a-fA-F]{3,8}\b|\brgb\(", blok)
    assert not harfiah, harfiah


def test_memakai_tata_letak_popup_yang_sama():
    """Judul tetap, isi menggulung, kaki tetap -- seperti popup operasi."""
    # Daftar selektornya tumbuh seiring dialog baru ikut memakainya, jadi
    # yang diuji keanggotaannya, bukan susunan persisnya.
    sel = next(b for b in CSS.split("}") if "#sp-dlg .dlg-kotak" in b)
    assert "#op-dlg .dlg-kotak" in sel
    assert "flex-direction:column" in sel
    blok = _blok(APP_JS, "const pilihProjek = (() => {", "const Progres = (()")
    for kelas in ("op-dlg-atas", "op-gulung", "op-dlg-bawah", "wz-tbl-pri"):
        assert kelas in blok, kelas
