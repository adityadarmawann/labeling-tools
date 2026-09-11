"""
Uji evaluasi produksi.

Alasan keberadaan pengukuran ini: v13 melaporkan mAP50-95 0,9499 lalu benar
0 dari 7 pada foto RVM sungguhan. Yang menangkapnya bukan mAP melainkan angka
ketergantungan warna. Jadi yang dijaga di sini adalah hal-hal yang kalau
bergeser membuat alat ukurnya sendiri berbohong.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.services import evaluasi as ev


def _gambar(benih=1, h=120, w=160):
    rng = np.random.default_rng(benih)
    im = rng.integers(30, 220, (h, w, 3), dtype=np.uint8)
    cv2.ellipse(im, (w // 2, h // 2), (w // 5, h // 4), 20, 0, 360, (40, 90, 200), -1)
    return im


# ============================================================
# PERLAKUAN WARNA TIDAK BOLEH MENYENTUH BENTUK
# ============================================================

@pytest.mark.parametrize("nama,fn", ev.PERLAKUAN)
def test_perlakuan_tidak_mengubah_ukuran(nama, fn):
    im = _gambar()
    out = fn(im, np.random.default_rng(0))
    assert out.shape == im.shape, f"{nama} mengubah bentuk gambar"
    assert out.dtype == np.uint8


@pytest.mark.parametrize("nama,fn", ev.PERLAKUAN)
def test_perlakuan_tidak_memindahkan_piksel(nama, fn):
    """Dasar seluruh pengukuran ini: hanya WARNA yang boleh berubah.

    Diuji lewat siluet — piksel mana yang lebih terang dari latarnya. Kalau
    sebuah perlakuan menggeser, memutar, atau memotong gambar, siluetnya ikut
    bergeser dan perubahan jawaban model tidak lagi membuktikan apa pun
    tentang warna.
    """
    im = np.zeros((80, 100, 3), np.uint8)
    cv2.rectangle(im, (20, 15), (70, 60), (200, 120, 60), -1)
    out = fn(im, np.random.default_rng(0))

    def siluet(x):
        g = cv2.cvtColor(x, cv2.COLOR_BGR2GRAY)
        return (g > g.min() + 8)

    a, b = siluet(im), siluet(out)
    if not b.any():
        pytest.skip(f"{nama} meratakan gambarnya; bentuk tidak bisa dibandingkan")
    # Kotak pembatas objeknya harus sama persis.
    assert cv2.boundingRect(a.astype(np.uint8)) == \
           cv2.boundingRect(b.astype(np.uint8)), f"{nama} memindahkan piksel"


def test_grayscale_benar_benar_menghapus_warna():
    out = ev.t_grayscale(_gambar(), np.random.default_rng(0))
    assert np.array_equal(out[..., 0], out[..., 1])
    assert np.array_equal(out[..., 1], out[..., 2])


def test_hue90_benar_benar_menggeser_rona():
    im = _gambar()
    out = ev.t_hue90(im, np.random.default_rng(0))
    h0 = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)[..., 0].astype(int)
    h1 = cv2.cvtColor(out, cv2.COLOR_BGR2HSV)[..., 0].astype(int)
    # Pergeseran 90 dari 180 adalah rona berlawanan — perlakuan paling keras.
    beda = np.abs((h1 - h0) % 180)
    assert np.median(beda) > 60, f"rona hampir tidak bergeser: {np.median(beda)}"


def test_semua_perlakuan_dimulai_dari_asli():
    """Perlakuan pertama HARUS `asli`.

    nilai_warna memakai vals[0] sebagai penanda gambar yang terbaca, dan
    tabel di layar membandingkan tiap sel dengan kolom pertama. Menukar
    urutannya diam-diam mengubah arti keduanya.
    """
    assert ev.PERLAKUAN[0][0] == "asli"


# ============================================================
# PENILAIAN KETERGANTUNGAN WARNA
# ============================================================

def test_model_yang_konsisten_dinilai_baik():
    j = [["botol"] * 6, ["kaleng"] * 6, ["botol"] * 6]
    h = ev.nilai_warna(j)
    assert h["berubah"] == 0 and h["skor"] == 0.0
    assert h["tingkat"] == "baik"


def test_model_yang_berganti_kelas_dinilai_buruk():
    """Ini bentuk kegagalan v13, dan pesannya harus menyebut akibatnya."""
    j = [["botol", "plastic-cup", "botol", "kaleng", "kaleng", "botol"]] * 4
    h = ev.nilai_warna(j)
    assert h["skor"] == 100.0
    assert h["tingkat"] == "buruk"
    assert "lampu" in h["pesan"].lower(), h["pesan"]


def test_ambang_sedang_dan_buruk_sesuai_v14():
    # 2 dari 10 = 20% -> tepat di ambang sedang.
    j = [["a"] * 6] * 8 + [["a", "b", "a", "a", "a", "a"]] * 2
    assert ev.nilai_warna(j)["tingkat"] == "sedang"
    # 5 dari 10 = 50% -> tepat di ambang buruk.
    j = [["a"] * 6] * 5 + [["a", "b", "a", "a", "a", "a"]] * 5
    assert ev.nilai_warna(j)["tingkat"] == "buruk"


def test_gambar_yang_tidak_terbaca_tidak_ikut_dihitung():
    j = [["botol"] * 6, [None, None, None, None, None, None], ["kaleng"] * 6]
    h = ev.nilai_warna(j)
    assert h["total"] == 2, "gambar yang gagal dibaca mengubah penyebutnya"


def test_indeks_gambar_yang_goyah_dilaporkan():
    """Persentase tanpa contohnya tidak bisa ditindak."""
    j = [["a"] * 6, ["a", "b", "a", "a", "a", "a"], ["c"] * 6]
    assert ev.nilai_warna(j)["goyah"] == [1]


def test_jawaban_kosong_tetap_dihitung_sebagai_jawaban():
    """`none` di satu perlakuan dan kelas di perlakuan lain ITU perubahan.

    Model yang berhenti mendeteksi objek begitu warnanya digeser sama
    rapuhnya dengan model yang berganti kelas.
    """
    j = [["botol", None, "botol", "botol", "botol", "botol"]]
    assert ev.nilai_warna(j)["berubah"] == 1


# ============================================================
# KELAS DEFAULT
# ============================================================

def test_latar_kosong_bersih_dinilai_baik():
    h = ev.nilai_default({}, 20)
    assert h["tingkat"] == "baik"


def test_kelas_yang_sering_muncul_di_latar_kosong_dinilai_buruk():
    """Ini persis gejala v13: `kaleng` jadi jawaban default."""
    h = ev.nilai_default({"kaleng": 9}, 20)
    assert h["tingkat"] == "buruk"
    assert h["teratas"] == "kaleng"
    assert "pelarian" in h["pesan"]


def test_satu_deteksi_dari_dua_puluh_itu_derau():
    assert ev.nilai_default({"botol": 1}, 20)["tingkat"] == "baik"


# ============================================================
# AKURASI
# ============================================================

def test_akurasi_menghitung_dan_mencatat_kebingungan():
    h = ev.nilai_akurasi([("botol", "botol"), ("botol", "kaleng"),
                          ("kaleng", "kaleng"), ("tetra", None)])
    assert h["benar"] == 2 and h["n"] == 4 and h["persen"] == 50.0
    assert h["bingung"]["botol"] == {"botol": 1, "kaleng": 1}
    assert h["bingung"]["tetra"] == {"(tidak ada)": 1}


def test_gambar_tanpa_label_tidak_ikut_penyebut():
    h = ev.nilai_akurasi([("botol", "botol"), (None, "kaleng")])
    assert h["n"] == 1 and h["persen"] == 100.0


# ============================================================
# PUTUSAN
# ============================================================

def test_warna_buruk_mengalahkan_akurasi_tinggi():
    """Inti pelajaran v13: akurasi tinggi TIDAK menyelamatkan model yang
    memutuskan lewat warna."""
    p = ev.putusan({"tingkat": "buruk", "skor": 80.0},
                   {"n": 10, "persen": 95.0, "benar": 9},
                   {"tingkat": "baik"})
    assert p["tingkat"] == "buruk"
    assert "JANGAN" in p["pesan"]


def test_semua_bersih_dinilai_baik():
    p = ev.putusan({"tingkat": "baik", "skor": 5.0},
                   {"n": 10, "persen": 90.0, "benar": 9},
                   {"tingkat": "baik"})
    assert p["tingkat"] == "baik"


def test_akurasi_rendah_walau_stabil_belum_baik():
    p = ev.putusan({"tingkat": "baik", "skor": 3.0},
                   {"n": 10, "persen": 40.0, "benar": 4},
                   {"tingkat": "baik"})
    assert p["tingkat"] == "sedang"
    assert "datanya" in p["pesan"]


def test_tanpa_label_putusan_tetap_bisa_diambil():
    """Ketergantungan warna tidak butuh label — itu keunggulannya."""
    p = ev.putusan({"tingkat": "baik", "skor": 2.0}, {"n": 0, "persen": 0.0},
                   {"tingkat": "baik"})
    assert p["tingkat"] == "baik"


# ============================================================
# SUMBER FOTO DAN LABEL
# ============================================================

def test_label_diambil_dari_objek_terbesar(tmp_path, monkeypatch):
    """Satu foto bisa memuat beberapa objek; yang menentukan jawaban model
    adalah yang paling menonjol."""
    from app.services import buatversi

    d = buatversi.dir_versi(tmp_path, 1)
    (d / "test" / "labels").mkdir(parents=True)
    (d / "data.yaml").write_text("nc: 2\nnames: ['botol', 'kaleng']\n")
    (d / "test" / "labels" / "x.txt").write_text(
        "0 0.5 0.5 0.10 0.10\n"      # kecil
        "1 0.5 0.5 0.60 0.60\n")     # besar -> inilah yang dipakai
    assert ev.label_uji(tmp_path, 1, tmp_path / "x.jpg") == "kaleng"


def test_label_poligon_juga_terbaca(tmp_path):
    from app.services import buatversi

    d = buatversi.dir_versi(tmp_path, 1)
    (d / "test" / "labels").mkdir(parents=True)
    (d / "data.yaml").write_text("nc: 2\nnames: ['botol', 'kaleng']\n")
    (d / "test" / "labels" / "y.txt").write_text(
        "0 0.1 0.1 0.9 0.1 0.9 0.9 0.1 0.9\n")
    assert ev.label_uji(tmp_path, 1, tmp_path / "y.jpg") == "botol"


def test_tanpa_berkas_label_mengembalikan_none(tmp_path):
    assert ev.label_uji(tmp_path, 1, tmp_path / "z.jpg") is None


def test_petak_kosong_memakai_pojok_dan_ukuran_inferensi():
    im = _gambar(h=400, w=600)
    p = ev.petak_kosong(im)
    assert p.shape[:2] == (ev.IMGSZ, ev.IMGSZ)


def test_setelan_inferensi_sama_dengan_v14():
    """Angka yang berbeda membuat hasilnya tidak bisa dibandingkan dengan
    angka v14 yang sudah ada."""
    assert (ev.CONF, ev.IOU, ev.IMGSZ) == (0.25, 0.45, 640)
    assert (ev.AMBANG_BURUK, ev.AMBANG_SEDANG) == (50.0, 20.0)


# ============================================================
# JAMINAN PALSU — CACAT YANG DITEMUKAN SAAT MENJALANKANNYA SUNGGUHAN
# ============================================================
#
# Ditemukan bukan dari membaca kode, melainkan dari menjalankan evaluasi pada
# model yang baru dilatih 4 epoch. Model itu tidak mendeteksi apa pun, dan
# hasilnya dilaporkan "ketergantungan warna 0% — BAIK, keputusan model stabil".
#
# eval-produksi-v14.py punya cacat yang sama (`if vals[0] == "-": continue`),
# tetapi di sana orang membaca tabelnya dan melihat "none" di mana-mana. Di
# sini angkanya jadi lencana hijau, dan lencana hijau pada model yang buta
# jauh lebih berbahaya daripada tidak ada angka sama sekali.

def test_model_yang_tidak_mendeteksi_apa_pun_tidak_dinilai_baik():
    j = [[None] * 6 for _ in range(8)]
    h = ev.nilai_warna(j)
    assert h["tingkat"] == "tak-terukur", h
    assert h["kosong"] == 8
    assert "tidak bisa diukur" in h["pesan"]
    # Dan putusannya harus ikut menolak, bukan meloloskannya.
    p = ev.putusan(h, {"n": 8, "persen": 0.0, "benar": 0}, {"tingkat": "baik"})
    assert p["tingkat"] == "buruk"


def test_terdeteksi_hanya_di_sebagian_perlakuan_itu_ketergantungan_warna():
    """Kasus yang DILEWATKAN v14, dan justru bentuk paling telanjangnya.

    Jawaban aslinya kosong tetapi objeknya muncul begitu ronanya diputar:
    model itu hanya melihat objeknya di bawah warna tertentu.
    """
    j = [[None, None, None, None, "botol", None]] * 5
    h = ev.nilai_warna(j)
    assert h["total"] == 5, "gambar seperti ini harus IKUT dihitung"
    assert h["berubah"] == 5
    assert h["tingkat"] == "buruk"


def test_sedikit_deteksi_membuat_skornya_tidak_dipakai():
    """Dua gambar terdeteksi dari dua puluh: angkanya tidak bermakna."""
    j = [["botol"] * 6, ["kaleng"] * 6] + [[None] * 6 for _ in range(18)]
    h = ev.nilai_warna(j)
    assert h["tingkat"] == "tak-terukur"


def test_deteksi_cukup_banyak_tetap_dinilai_seperti_biasa():
    """Ambangnya tidak boleh menelan kasus yang normal."""
    j = [["botol"] * 6 for _ in range(7)] + [[None] * 6 for _ in range(3)]
    h = ev.nilai_warna(j)
    assert h["tingkat"] == "baik", h
    assert h["total"] == 7 and h["kosong"] == 3
