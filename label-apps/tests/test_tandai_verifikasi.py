"""Hasil AI yang sudah benar harus bisa ditandai selesai diperiksa.

Dilaporkan dari pemakaian: spanduk verifikasi menyuruh menekan "Simpan",
tetapi tombol itu tidak ada di layar.

Dua sebab bertumpuk. Tombol Simpan disembunyikan SELURUHNYA saat autosave
menyala, dan autosave adalah bawaan. Lalu autosave hanya menyimpan kalau ada
yang BERUBAH, jadi hasil auto-label yang kebetulan sudah benar tak pernah
tersentuh sama sekali: ia menetap sebagai "belum diverifikasi" selamanya
walau sudah dibaca orang satu per satu.

Tombolnya ditaruh di dalam spanduk, bukan di bilah alat. Di bilah alat ia
akan berebut arti dengan "Jadikan objek" yang sudah ada di sana, dan itu
persis ambiguitas yang dulu membuat ikon simpan dihapus.
"""
from __future__ import annotations

import pathlib

HTML = pathlib.Path("app/templates/label.html").read_text()
JS = pathlib.Path("app/static/label.js").read_text()
CSS = pathlib.Path("app/static/label.css").read_text()


def test_tombol_ada_di_dalam_spanduk():
    i = HTML.index('class="lab-verif-belum"')
    j = HTML.index("</span>", i)
    assert 'id="lab-verif-ok"' in HTML[i:j]


def test_tombol_tak_ditaruh_di_bilah_alat():
    """Di sanalah ambiguitas dengan "Jadikan objek" muncul."""
    i = HTML.index('id="btn-simpan"')
    j = HTML.index("lab-verif", i) if "lab-verif" in HTML[i:] else len(HTML)
    bilah = HTML[max(0, i - 2000):i + 800]
    assert 'id="lab-verif-ok"' not in bilah


def test_spanduk_tak_lagi_menyuruh_menekan_simpan():
    """Tombol Simpan disembunyikan saat autosave menyala, dan autosave bawaan,
    jadi kalimat itu menunjuk tombol yang tak ada."""
    i = HTML.index('class="lab-verif-belum"')
    blok = HTML[i:HTML.index("lab-verif-sudah", i)]
    assert "Menekan <b>Simpan</b>" not in blok


def test_tombol_menyimpan_lewat_jalur_yang_sama():
    """Supaya tak ada dua sumber kebenaran: spanduk berpindah keadaan lewat
    hook pasca-simpan yang sudah ada."""
    i = JS.index("function pasangTandaiVerif(")
    blok = JS[i:JS.index("function keadaanVerif(", i)]
    assert "await simpan()" in blok
    assert "b.disabled = true" in blok        # tak bisa ditekan dua kali
    assert "finally" in blok                  # kembali aktif walau gagal


def test_tombol_dipasang_saat_skrip_dimuat():
    assert "pasangTandaiVerif();" in JS


def test_tombol_tak_ada_saat_hanya_bisa_dilihat():
    """Gambar milik orang lain tak boleh ditandai dari sini."""
    i = HTML.index('id="lab-verif-ok"')
    assert "{%- if boleh_ubah %}" in HTML[max(0, i - 400):i]


def test_tombolnya_punya_gaya_sendiri():
    assert ".lab-verif-ok {" in CSS
    i = CSS.index(".lab-verif-ok {")
    blok = CSS[i:i + 400]
    assert "var(--aksi)" in blok              # aksi utama, dari token tema
