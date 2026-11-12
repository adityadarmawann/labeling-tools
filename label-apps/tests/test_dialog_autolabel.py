"""Dialog auto-label: aturan kombinasinya terbaca dari bentuknya.

Dilaporkan dari pemakaian: "aku bisa aja ceklis semua, tapi kan ada yang mana
yang bisa di ceklis barengan, atau mana yang bisa sendiri."

Empat sumber dulu berjajar sebagai empat centang setara, dan tak ada apa pun
di layar yang mengatakan bahwa dua di antaranya berbagi satu kolom nama objek
dan satu ambang. Menata ulangnya jadi dua KELUARGA memindahkan jawaban itu ke
struktur, bukan ke paragraf bantuan.

Di balik keluhan tampilan itu ada tiga cacat FUNGSI yang diuji di sini juga:
kolom nama objek yang tak bisa diketik, dua pemilih model yang mati permanen,
dan GroundingDINO yang diam-diam memakai ambang milik Zero-shot.
"""
from __future__ import annotations

import pathlib
import re

HTML = pathlib.Path("app/templates/anotasi.html").read_text()
JS = pathlib.Path("app/static/anotasi.js").read_text()
CSS = pathlib.Path("app/static/app.css").read_text()
# Komentar dibuang lebih dulu: aturan yang diperiksa adalah yang BERLAKU,
# dan komentar yang menceritakan aturan lama memuat kata-kata yang sama.
CSS_ATURAN = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)


def _dlg() -> str:
    i = HTML.index('<div class="dlg" id="az-dlg"')
    return HTML[i:HTML.index("</main>", i)]


def _tanpa_komentar(t: str) -> str:
    return re.sub(r"\{#-?.*?-?#\}", "", t, flags=re.S)


# ------------------------------------------------- cacat fungsi yang nyata
def test_kolom_nama_objek_tak_terkunci_oleh_centang_zero_shot():
    """Kolom ini dibaca Zero-shot DAN GroundingDINO. Dulu ia tinggal di dalam
    blok Zero-shot, dan blok yang tak dicentang diberi pointer-events:none,
    jadi mematikan Zero-shot membuatnya tak bisa diketik sama sekali."""
    d = _tanpa_komentar(_dlg())
    i = d.index('id="az-ko-zeroshot"')
    j = d.index("</div>", i)
    assert 'id="az-prompt"' not in d[i:j]
    # dan ia memang naik ke kotak keluarganya
    g = d.index('id="az-grup-teks"')
    assert g < d.index('id="az-prompt"') < d.index('id="az-ko-zeroshot"')


def test_blok_sumber_tak_dikunci_dari_tetikus():
    """Tak satu pun aturan .az-src-ko.mati boleh memasang pointer-events.

    Aturan barunya cuma menimpa opacity, jadi aturan LAMA yang masih tinggal
    di berkas yang sama tetap mengunci bloknya dari tetikus.
    """
    ada = False
    for m in re.finditer(r"\.az-src-ko\.mati\{([^}]*)\}", CSS_ATURAN):
        ada = True
        assert "pointer-events" not in m.group(1), m.group(0)
    assert ada, "aturannya hilang sama sekali"


def test_pemilih_model_tidak_mati_permanen():
    """Keduanya ditulis disabled di markup dan tak pernah dinyalakan, jadi
    memilih latihan mana atau model .pt mana yang dipakai tak pernah bisa."""
    d = _dlg()
    for baris in d.splitlines():
        if 'id="az-terlatih"' in baris or 'id="az-eksternal"' in baris:
            assert "disabled" not in baris, baris.strip()
    # dan azToggle benar-benar menyetel disabled, bukan cuma kelas
    i = JS.index("function azToggle(")
    blok = JS[i:JS.index("function azIsiInfo(", i)]
    assert "k.disabled = !nyala" in blok


def test_groundingdino_punya_ambang_sendiri():
    """Dulu ia memakai #az-conf (0,1 milik zero-shot), bukan 0,25 yang
    diasumsikan server, dan itulah sebabnya ia terasa penuh derau."""
    assert 'id="az-conf-gdino"' in _dlg()
    i = JS.index("if (azEl('az-s-gdino').checked)")
    blok = JS[i:i + 600]
    assert "az-conf-gdino" in blok
    assert "azEl('az-conf')" not in blok


def test_dialog_ikut_tata_letak_tiga_bagian():
    """Tanpa ini judul dan tombol Mulai ikut menggulir keluar layar."""
    assert "#az-dlg .dlg-kotak{" in CSS or "#az-dlg .dlg-kotak," in CSS \
        or ",#az-dlg .dlg-kotak{" in CSS


# ------------------------------------------------------- struktur baru
def test_sumber_dikelompokkan_jadi_dua_keluarga():
    d = _dlg()
    assert 'id="az-grup-model"' in d and 'id="az-grup-teks"' in d
    # tiap sumber tinggal di dalam salah satu keluarga, tak ada yang di luar
    for cb in ("az-s-terlatih", "az-s-eksternal"):
        assert d.index('id="az-grup-model"') < d.index(cb) < d.index('id="az-grup-teks"')
    for cb in ("az-s-zeroshot", "az-s-gdino"):
        assert d.index(cb) > d.index('id="az-grup-teks"')


def test_disebutkan_bahwa_semua_boleh_bersamaan():
    """Jawaban atas keluhannya: memang semuanya boleh dicentang bareng."""
    d = _tanpa_komentar(_dlg())
    assert "Boleh pilih lebih dari satu" in d
    assert "objek yang sama cukup ditulis sekali" in d


def test_ambang_duduk_di_sebelah_sumber_yang_diaturnya():
    """Dulu ketiga ambang berjajar di dasar dialog, jauh dari sumbernya, dan
    namanya menyebut satu dari dua sumber yang diaturnya."""
    d = _dlg()
    assert d.index('id="az-grup-model"') < d.index('id="az-conf-model"') \
        < d.index('id="az-grup-teks"')
    assert d.index('id="az-ko-zeroshot"') < d.index('id="az-conf"') \
        < d.index('id="az-ko-gdino"')
    assert "Ambang zero-shot" not in d and "Ambang model .pt" not in d


def test_setelan_jarang_dipakai_dilipat():
    d = _dlg()
    assert '<details class="az-lanjut">' in d
    i = d.index('class="az-lanjut"')
    assert d.index('id="az-batas"') > i and d.index('id="az-iou"') > i


def test_jumlah_maks_menyebut_satuannya():
    """"Jumlah maks 1000" tak mengatakan 1000 apa. Ia gambar, bukan objek."""
    d = _tanpa_komentar(_dlg())
    i = d.index('id="az-batas"')
    assert "Berhenti setelah" in d[max(0, i - 300):i]
    assert "gambar" in d[i:i + 300]


# ------------------------------------------- dicegah sebelum, bukan sesudah
def test_projek_kerangka_dihalangi_sebelum_disetel():
    """Penolakannya dulu cuma hidup di dalam pekerja, jadi orang menyetel
    semuanya lebih dulu lalu dijawab satu baris merah."""
    assert 'id="az-halang"' in _dlg()
    i = JS.index("function azHalang(")
    blok = JS[i:JS.index("function azAwas(", i)]
    assert "kerangka" in blok and "training_aktif" in blok
    assert "atur.hidden" in blok
    # dan dipanggil sebelum tombol Mulai dinyalakan
    k = JS.index("if (azHalang(j))")
    assert "mulai.disabled = true" in JS[k:k + 120]


def test_peringatan_nama_kelas_menyebut_akibatnya():
    """Satu nama dipakai untuk SEMUA objek dari SEMUA sumber; pada model
    berkelas banyak itu melenyapkan taksonominya."""
    i = JS.index("function azAwas(")
    blok = JS[i:i + 1800]
    assert "az-awas-kelas" in blok and "kelas milik model terlatih" in blok


def test_peringatan_bentuk_kotak_mematikan_sam():
    i = JS.index("function azAwas(")
    blok = JS[i:i + 1800]
    assert "az-awas-bentuk" in blok and "SAM tidak dijalankan" in blok


# ------------------------------------------------------------------ tema
def test_gaya_barunya_dari_token_tema():
    i = CSS.index(".az-lingkup{")
    blok = CSS[i:CSS.index("#az-maju:empty", i)]
    assert "var(--" in blok
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b|\brgb\(", blok)
