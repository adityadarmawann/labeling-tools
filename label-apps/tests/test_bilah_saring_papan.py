"""Bilah saringan papan Anotasi: satu baris, bukan dua ratus piksel.

Dilaporkan dari pemakaian: bloknya memakan ruang jauh melebihi kepentingannya,
dan yang justru butuh ruang adalah tiga kolom di bawahnya.

Sebabnya bukan sekadar boros. Bloknya memakai ulang .an-kendali-baris, grid
TIGA kolom yang ditulis untuk pola "label | kendali | kalimat bantuan", satu
kendali per baris. Diisi dua belas anak, penempatan otomatis grid menaruh
label "Keadaan" dan "Urutkan" di kolom ketiga baris SEBELUMNYA, dan dua select
mendarat di kolom label selebar 96px lalu terpotong di tengah kata. Jadi ia
terbaca rusak, bukan cuma besar.
"""
from __future__ import annotations

import pathlib
import re

HTML = pathlib.Path("app/templates/anotasi.html").read_text()
AKSI = pathlib.Path("app/templates/aksi_papan.html").read_text()
CSS = pathlib.Path("app/static/app.css").read_text()


def _bilah() -> str:
    i = HTML.index('<div class="an-kendali')
    return HTML[i:HTML.index("tiga kolom", i)]


def _tanpa_komentar(t: str) -> str:
    return re.sub(r"\{#-?.*?-?#\}", "", t, flags=re.S)


# --------------------------------------------- grid lama benar-benar hilang
def test_grid_tiga_kolom_tak_dipakai_lagi():
    """Kalau kelasnya tinggal sementara aturannya dibuang, tata letaknya
    runtuh tanpa ada yang terlihat salah di templat."""
    sisa = [f for f in ("app/templates/anotasi.html",
                        "app/templates/aksi_papan.html")
            if "an-kendali-baris" in _tanpa_komentar(pathlib.Path(f).read_text())]
    assert not sisa, sisa
    assert ".an-kendali-baris{" not in CSS


def test_variabel_lebar_kolomnya_ikut_dibuang():
    """--an-label dan --an-pilih hanya hidup untuk grid itu."""
    assert "--an-label" not in CSS and "--an-pilih" not in CSS


# ------------------------------------------------------- bentuk barunya
def test_bilahnya_memakai_geometri_bar_yang_sudah_ada():
    """Tak ada tata letak baru yang perlu dijaga sendiri di sini."""
    b = _bilah()
    assert 'class="an-kendali bar bar-saring"' in b


def test_form_dan_pembungkusnya_tak_punya_kotak_sendiri():
    """Anaknya yang harus jadi item flex bilahnya, supaya semuanya satu baris."""
    assert ".an-saring,.an-saring-isi{display:contents}" in CSS


def test_tetap_satu_form():
    """Dua form berarti tiap sisi harus menitipkan nilai sisi lain sebagai
    input tersembunyi, dan satu yang terlupa membuat mencari diam-diam
    membuang saringan pelabel."""
    b = _bilah()
    assert b.count("<form") == 1


def test_setelan_projek_di_luar_form_saringan():
    """Format anotasi bukan saringan; kalau ikut di dalam form, ia akan
    terkirim sebagai parameter saringan."""
    b = _bilah()
    assert b.index("</form>") < b.index('id="an-jenis"')


def test_tombol_terapkan_sudah_tak_perlu():
    """Tiap select mengirim formnya sendiri saat diubah."""
    b = _tanpa_komentar(_bilah())
    assert ">Terapkan<" not in b
    assert b.count("onchange=\"this.form.submit()\"") >= 2
    assert ">Cari<" in b                 # mengetik tetap butuh pemicunya


# ------------------------------------------------- parameter tetap jalan
def test_semua_parameter_lama_dipertahankan():
    """Satu pun hilang berarti tautan dan bookmark yang sudah beredar rusak."""
    b = _bilah()
    for nama in ('name="ds"', 'name="q"', 'name="pl"', 'name="kd"', 'name="urut"'):
        assert nama in b, nama


def test_pil_hasil_mempertahankan_urutan():
    b = _bilah()
    i = b.index("saring-pil")
    assert "urut={{ urut }}" in b[i:i + 400]


# --------------------------------------------------- menyala saat menyaring
def test_pil_saringan_menyala_hanya_saat_menyaring():
    b = _bilah()
    assert "{% if pelabel_f %} data-on{% endif %}" in b
    assert "{% if keadaan_f %} data-on{% endif %}" in b
    # mengurutkan tidak menyempitkan apa pun, jadi pil urut tak pernah menyala
    i = b.index('for="an-urut"')
    assert "data-on" not in b[max(0, i - 200):i]


def test_warnanya_dari_token_tema():
    i = CSS.index(".saring-sel[data-on]{")
    blok = CSS[i:CSS.index(".menu-baris{", i)]
    assert "var(--" in blok
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b|\brgb\(", blok)


def test_token_hantu_garis_diperbaiki():
    """--garis tak pernah didefinisikan, jadi deklarasinya tak sah dan dibuang
    peramban tanpa satu pun tanda."""
    assert "var(--garis)" not in CSS


# ----------------------------------------------------------- aksesibilitas
def test_tiap_kendali_tetap_punya_label():
    b = _bilah()
    for id_ in ("an-cari", "an-pl", "an-kd", "an-urut", "an-jenis"):
        assert f'for="{id_}"' in b, id_


def test_papan_aksi_ikut_disamakan():
    """Ia memakai kelas yang sama, jadi membuang CSS-nya tanpa mengubahnya
    akan meruntuhkan halaman yang tak diminta siapa pun untuk diubah."""
    assert "an-kendali bar bar-saring" in AKSI
    assert 'class="saring-urut"' in AKSI
