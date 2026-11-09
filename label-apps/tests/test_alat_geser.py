"""Alat Geser (pan) di kanvas anotasi.

Saat gambar diperbesar, memindahkan sudut pandang DULU hanya bisa lewat tombol
tengah tetikus atau tahan Spasi. Keduanya tak terlihat di mana pun, jadi pada
tetikus biasa (yang rodanya tidak punya gulir mendatar seperti touchpad)
gambar terasa tidak bisa digeser ke kiri atau kanan sama sekali. Alat ini
menjadikan kemampuan itu kelihatan, di tempat pertama yang dilihat orang.
"""
import pathlib

from app.services import tugas                              # noqa: F401

AKAR = pathlib.Path(__file__).resolve().parents[1]
JS = (AKAR / "app" / "static" / "label.js").read_text()
HTML = (AKAR / "app" / "templates" / "label.html").read_text()
IKON = (AKAR / "app" / "templates" / "_ikon.html").read_text()
PANDUAN = (AKAR / "app" / "templates" / "_panduan_kanvas.html").read_text()


def test_tombol_geser_paling_atas_di_palet():
    """Urutan itu bagian dari permintaannya: alat geser harus yang PERTAMA,
    di atas 'Titik +', bukan terselip di antara alat menggambar."""
    palet = HTML.split('id="lab-palet"', 1)[1]
    i_geser = palet.index('data-mode="geser"')
    i_pertama_lain = min(palet.index('data-mode="p+"'),
                         palet.index('data-mode="poly"'),
                         palet.index('data-mode="edit"'))
    assert i_geser < i_pertama_lain


def test_tombol_geser_memakai_ikon_tangan():
    assert "{{ ikon('tangan', 21) }}" in HTML
    assert "nama == 'tangan'" in IKON


def test_mode_geser_menarik_gambar_dengan_tombol_kiri():
    """Jalur geser yang sudah ada (tombol tengah / Spasi) dipakai ulang, kini
    juga oleh klik kiri biasa selama mode geser aktif."""
    assert "ev.button === 1 || spasi || S.mode === 'geser'" in JS


def test_mode_geser_tidak_membuang_pilihan():
    """Geser itu navigasi, bukan menggambar. Kehilangan objek terpilih cuma
    karena melihat sisi lain gambar adalah kemunduran."""
    assert "if (m !== 'edit' && m !== 'geser') {" in JS


def test_mode_geser_punya_nama_dan_pintasan():
    assert "geser: 'Geser'" in JS
    assert "k === 'h') setMode('geser')" in JS


def test_pintasan_geser_tertulis_di_panduan():
    assert "<kbd>H</kbd>" in PANDUAN
    assert "tombol tengah tetikus" in PANDUAN


def test_kursor_tangan_di_mode_geser():
    css = (AKAR / "app" / "static" / "label.css").read_text()
    buka = css.index('.lab-canvas[data-mode="geser"] canvas')
    kepal = css.index(".lab-canvas[data-geser] canvas")
    assert "cursor: grab" in css[buka:buka + 90]
    # kepalan harus DI BAWAH telapak: kekhususannya sama, jadi urutan yang
    # menentukan siapa yang menang saat keduanya menempel bersamaan.
    assert kepal > buka
