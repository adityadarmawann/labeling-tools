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


# ============================================================
# MODE WARNA — SATU ALAT UKUR, DUA PERTANYAAN BERBEDA
# ============================================================
#
# Rancangan awal berkas ini menganggap "warna menentukan kelas" SELALU buruk.
# Itu kesimpulan v14, dan v14 mengklasifikasi MATERIAL: botol tetap botol apa
# pun warnanya, jadi warna memang petunjuk palsu di sana.
#
# Untuk pengenalan produk kesimpulannya terbalik. Dataset paragon-kahf punya
# dua kelas — kahf_extradry_deodorant_45ml dan kahf_skinergizing_facewash_50ml
# — dan yang membedakan keduanya sebagian besar justru warna kemasannya.
# Memvonis "BURUK, model memutuskan lewat warna" di sana adalah vonis yang
# terbalik, dan eval-produksi-paragon.py (salinan skrip v14) memang
# melakukannya.
#
# Yang membuat satu alat ukur bisa melayani keduanya: perlakuan RONA dan
# perlakuan TERANG dinilai terpisah.

from app.services import mode_warna as mw


def _jawaban(asli, rona, terang):
    """Susun satu baris jawaban sesuai urutan PERLAKUAN."""
    return [asli] + [rona] * len(ev.NAMA_RONA) + [terang] * len(ev.NAMA_TERANG)


def test_mode_bentuk_menghukum_ketergantungan_rona():
    """Kasus v14: rona tidak boleh menentukan apa pun."""
    j = [_jawaban("botol", "kaleng", "botol")] * 6
    h = ev.nilai_warna(j, mw.BENTUK)
    assert h["skor_rona"] == 100.0
    assert h["skor_terang"] == 0.0
    assert h["tingkat"] == "buruk"


def test_mode_warna_TIDAK_menghukum_ketergantungan_rona():
    """Jawaban yang sama, mode berbeda, vonis berbeda.

    Inilah seluruh alasan mode ini ada. Untuk pengenal produk, jawaban yang
    berubah saat rona diputar bukan kegagalan — itu yang diminta.
    """
    j = [_jawaban("kahf_deo", "kahf_facewash", "kahf_deo")] * 6
    h = ev.nilai_warna(j, mw.WARNA)
    assert h["skor_rona"] == 100.0, "angkanya tetap dilaporkan"
    assert h["tingkat"] == "baik", "tetapi TIDAK dijadikan vonis"
    assert "memang itu yang diminta" in h["pesan"]


def test_kerapuhan_terhadap_terang_buruk_di_KEDUA_mode():
    """Ruang detektor kadang terang kadang remang.

    Model yang kelasnya berganti hanya karena lampunya diredupkan tidak bisa
    dipakai di mana pun, mode apa pun.
    """
    j = [_jawaban("a", "a", "b")] * 6
    for m in (mw.BENTUK, mw.WARNA):
        h = ev.nilai_warna(j, m)
        assert h["skor_terang"] == 100.0
        assert h["tingkat"] == "buruk", f"mode {m} meloloskan kerapuhan terang"
        assert "TERANG" in h["pesan"]


def test_terang_diperiksa_lebih_dulu_daripada_rona():
    """Model yang rapuh terhadap keduanya divonis atas yang lebih menentukan."""
    j = [_jawaban("a", "b", "c")] * 6
    h = ev.nilai_warna(j, mw.BENTUK)
    assert h["tingkat"] == "buruk"
    assert "TERANG" in h["pesan"], h["pesan"]


def test_mode_warna_yang_sehat_dinilai_baik():
    j = ([_jawaban("deo", "facewash", "deo")] * 5
         + [_jawaban("fw", "deo", "fw")] * 5)
    h = ev.nilai_warna(j, mw.WARNA)
    assert h["tingkat"] == "baik"
    assert h["skor_terang"] == 0.0


def test_mode_tidak_dikenal_jatuh_ke_bawaan():
    j = [_jawaban("a", "b", "a")] * 6
    assert ev.nilai_warna(j, "ngawur")["tingkat"] == \
           ev.nilai_warna(j, mw.BENTUK)["tingkat"]


def test_perlakuan_terang_tidak_menyentuh_rona():
    """Kalau perlakuan TERANG diam-diam menggeser rona, seluruh pemisahan
    rona/terang runtuh dan mode `warna` jadi tidak bisa dinilai."""
    im = _gambar()
    h0 = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)[..., 0].astype(int)
    for nama, fn in ev.TERANG:
        out = fn(im, np.random.default_rng(0))
        h1 = cv2.cvtColor(out, cv2.COLOR_BGR2HSV)[..., 0].astype(int)
        beda = np.abs((h1 - h0 + 90) % 180 - 90)
        # Iluminan acak mengalikan tiap kanal berbeda, jadi ronanya bergeser
        # sedikit; yang dilarang pergeseran BESAR seperti putaran hue.
        assert np.median(beda) < 12, f"{nama} menggeser rona {np.median(beda)}"


def test_perlakuan_rona_benar_benar_menggeser_rona():
    im = _gambar()
    h0 = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)[..., 0].astype(int)
    banyak = 0
    for nama, fn in ev.RONA:
        out = fn(im, np.random.default_rng(0))
        h1 = cv2.cvtColor(out, cv2.COLOR_BGR2HSV)[..., 0].astype(int)
        if np.median(np.abs((h1 - h0 + 90) % 180 - 90)) > 15:
            banyak += 1
    assert banyak >= 2, "kelompok RONA harus benar-benar memutar rona"


# ============================================================
# MODE DI RESEP DAN DI AUGMENTASI
# ============================================================

def test_mode_warna_mematikan_operasi_penggeser_rona():
    """Dipaksa di satu tempat, bukan diserahkan ke orang yang mengisi form.

    Mode `warna` dengan hue_sat yang tertinggal menyala adalah dataset yang
    labelnya diam-diam salah: objek berwarna biru diberi label produk yang
    kemasannya hijau.
    """
    r = mw.terap_ke_aug({"aug": {}}, mw.WARNA)
    for oid in mw.OP_GESER_RONA:
        assert r["aug"][oid]["aktif"] is False, oid


def test_mode_warna_TIDAK_mematikan_operasi_terang():
    """Ruang detektor tetap kadang terang kadang remang."""
    r = mw.terap_ke_aug({"aug": {}}, mw.WARNA)
    for oid in mw.OP_TERANG:
        assert r["aug"].get(oid, {}).get("aktif") is not False, oid


def test_mode_bentuk_tidak_mengubah_resep():
    asal = {"aug": {"hue_sat": {"aktif": True}}, "volume": {"per_gambar": 3}}
    assert mw.terap_ke_aug(asal, mw.BENTUK) == asal


def test_mode_tersimpan_dan_terbaca_dari_resep():
    r = mw.tulis_ke_resep({"volume": {"per_gambar": 2}}, mw.WARNA)
    assert mw.dari_resep(r) == mw.WARNA
    assert r["volume"] == {"per_gambar": 2}, "bagian lain resep tidak boleh hilang"
    assert mw.dari_resep({}) == mw.BAWAAN


def test_par_latih_berlawanan_antar_mode():
    b, w = mw.par_latih(mw.BENTUK), mw.par_latih(mw.WARNA)
    assert b["hsv_h"] == 0.030 and b["bgr"] == 0.10      # angka v14
    assert w["hsv_h"] == 0.0 and w["bgr"] == 0.0         # rona dikunci
    # hsv_v dibuka lebar di KEDUA mode: terang harus selalu jadi petunjuk
    # yang tidak bisa diandalkan.
    assert b["hsv_v"] == w["hsv_v"] == 0.50


# ============================================================
# KALIMAT DI LAYAR HARUS SESUAI KENYATAAN
# ============================================================
#
# Halaman Training tidak lagi menampilkan hsv_h / hsv_s / hsv_v / bgr sebagai
# angka; yang tersisa satu kalimat yang menjelaskan AKIBATNYA. Kalimat seperti
# itu hanya berguna kalau benar, dan kalimat yang salah lebih buruk daripada
# angka mentah — orang tidak bisa memeriksanya sendiri.
#
# Versi pertama kalimat itu SALAH, dan salahnya baru ketahuan setelah diukur:
#   "saat melatih ... dalam warna-warna berbeda" -> padahal hsv_h 0,030 cuma
#   menggeser rona 5,5 derajat rata-rata (terburuk 12 dari 360). Merah tetap
#   merah. Yang benar-benar menggeser warna lebar adalah augmentasi VERSINYA,
#   bukan setelan waktu-latih.
#   "warnanya dibiarkan apa adanya" -> padahal kepekatan warnanya tetap
#   berubah sekitar 20%.
#
# Tes ini mengukur pipeline yang sebenarnya dan menjatuhkan diri kalau
# klaimnya berhenti benar.

def _geser_rona(mode, bgr, n=60):
    """Rata-rata pergeseran rona (derajat, dari 360) oleh augmentasi versi.

    Gambarnya berwarna RATA satu bidang penuh. Itu bukan kemalasan: augmentasi
    memutar, menggeser, dan memotong, jadi pada gambar berpetak-petak yang
    terukur adalah perpindahan piksel, bukan perubahan warna. Dengan warna
    rata, geometri tidak bisa mencemari pengukurannya.
    """
    import random

    from app.services import olah, mode_warna as mw

    im = np.full((192, 192, 3), bgr, np.uint8)
    h0 = float(cv2.cvtColor(im, cv2.COLOR_BGR2HSV)[0, 0, 0])
    pipa = olah.bangun_pipeline(mw.tulis_ke_resep({"volume": {"per_gambar": 1}},
                                                  mode))
    rng = random.Random(2)
    kumpul = []
    for _ in range(n):
        hasil = olah.augmentasi_sekali(im, [], pipa, rng)
        if hasil is None:
            continue
        hsv = cv2.cvtColor(hasil[0], cv2.COLOR_BGR2HSV)
        # Piksel yang jadi nyaris kelabu atau gelap tidak punya rona yang
        # bermakna; memasukkannya membuat angkanya jadi derau.
        m = (hsv[..., 1] > 40) & (hsv[..., 2] > 40)
        if m.sum() < 100:
            continue
        h1 = hsv[..., 0][m].astype(float)
        kumpul.append(float(np.abs((h1 - h0 + 90) % 180 - 90).mean() * 2))
    assert kumpul, "tidak ada satu pun hasil augmentasi yang terukur"
    return float(np.mean(kumpul))


@pytest.mark.parametrize("bgr", [(40, 40, 220), (60, 180, 60), (220, 80, 40)])
def test_klaim_layar_sesuai_kenyataan_mode_bentuk(bgr):
    """Layar berkata "digeser rata-rata sekitar 50 derajat". Buktikan."""
    g = _geser_rona(mw.BENTUK, bgr)
    assert 30 <= g <= 80, (
        f"rona bergeser {g:.1f} derajat — kalimat di layar menyebut sekitar 50. "
        "Perbarui kalimatnya atau setelannya, jangan biarkan berselisih.")


@pytest.mark.parametrize("bgr", [(40, 40, 220), (60, 180, 60), (220, 80, 40)])
def test_klaim_layar_sesuai_kenyataan_mode_warna(bgr):
    """Layar berkata "ronanya praktis tidak digeser". Buktikan."""
    g = _geser_rona(mw.WARNA, bgr)
    assert g <= 12, (
        f"rona bergeser {g:.1f} derajat di mode warna — layar menjanjikan "
        "warna asli dipertahankan. Ada operasi penggeser rona yang lolos.")


def test_selisih_kedua_mode_besar_dan_jelas():
    """Kalau keduanya hampir sama, salah satu modenya tidak berfungsi.

    Pernah terjadi saat mengukur pertama kali: keduanya terbaca ~80 derajat,
    dan sempat terlihat seperti mode `warna` yang gagal. Yang keliru ternyata
    pengukurannya — gambar ujinya berpetak-petak, jadi yang terukur
    perpindahan piksel oleh rotasi dan crop, bukan perubahan warna.
    """
    b = _geser_rona(mw.BENTUK, (40, 40, 220))
    w = _geser_rona(mw.WARNA, (40, 40, 220))
    assert b > w * 4, f"bentuk {b:.1f} vs warna {w:.1f} — terlalu mirip"


def test_setelan_waktu_latih_TIDAK_menggeser_rona_selebar_versinya():
    """Penjaga bagi kesalahan kalimat yang pertama.

    hsv_h 0,030 hanya menggeser rona ~5 derajat. Kalimat yang menyebut
    "warna-warna berbeda" sambil menunjuk setelan WAKTU-LATIH akan salah,
    berapa pun lebar augmentasi versinya.
    """
    from app.services import mode_warna as mw2

    hg = mw2.par_latih(mw2.BENTUK)["hsv_h"]
    # rumus Ultralytics: pergeseran = uniform(-1,1) * hgain * 180 (skala 0..180)
    maks_derajat = hg * 180 * 2          # -> skala 0..360
    assert maks_derajat < 15, (
        f"hsv_h {hg} menggeser rona sampai {maks_derajat:.1f} derajat; "
        "kalau ini dinaikkan, kalimat di layar harus ikut diperbarui")


# ============================================================
# SAKLAR AUGMENTASI BENAR-BENAR MENGIKUTI MODENYA
# ============================================================
#
# Pilihan mode di wizard versi harus MENYETEL saklar augmentasinya, bukan
# sekadar menandainya. Versi pertama hanya menambahkan penanda visual, dan
# penanda itu tidak pernah mengenai apa pun: operasi yang mati memang tidak
# dirender sama sekali di daftar. Akibatnya pilihan mode terlihat sudah jalan
# padahal resep yang terkirim tetap membawa keenam operasi penggeser rona
# dalam keadaan menyala.
#
# Di sisi server, bangun_pipeline tetap memaksanya mati — jadi datasetnya
# tidak pernah salah. Yang salah adalah APA YANG DILIHAT ORANG: daftar
# langkah yang berselisih dengan pilihannya sendiri.

def test_resep_mode_warna_mematikan_enam_langkah_rona():
    from app.services import olah

    kat = olah.katalog_json()["aug"]
    r = mw.terap_ke_aug({"aug": {}}, mw.WARNA)
    mati = [o for o in mw.OP_GESER_RONA if r["aug"][o]["aktif"] is False]
    assert len(mati) == 6, f"cuma {len(mati)} yang dimatikan: {mati}"
    # Keenamnya memang ada di katalog — kalau ada yang berganti nama, daftarnya
    # harus ikut diperbarui, dan di sinilah ketahuannya.
    for o in mw.OP_GESER_RONA:
        assert o in kat, f"{o} tidak ada di katalog augmentasi"


def test_mode_bentuk_mengembalikan_bawaan_katalog():
    """Berpindah mode harus bisa bolak-balik, bukan pintu satu arah.

    Yang dituntut BUKAN "semuanya menyala": sebagian operasi memang
    bawaan_aktif=False di katalog (saturasi, eksposur) dan harus tetap mati.
    Yang dituntut adalah mode `bentuk` tidak meninggalkan satu pun paksaan
    mati — bawaan katalog kembali berlaku sepenuhnya.
    """
    r = mw.terap_ke_aug({"aug": {}}, mw.WARNA)
    for o in mw.OP_GESER_RONA:
        assert r["aug"][o]["aktif"] is False

    # Wizard membuang entri paksaannya saat kembali ke mode bentuk.
    kembali = {"aug": {k: v for k, v in r["aug"].items()
                       if k not in mw.OP_GESER_RONA}}
    hasil = mw.terap_ke_aug(kembali, mw.BENTUK)
    for o in mw.OP_GESER_RONA:
        assert o not in hasil["aug"], (
            f"{o} masih membawa paksaan dari mode warna — bawaan katalognya "
            "tidak bisa berlaku lagi")


def test_mode_warna_tidak_memaksa_mati_langkah_terang():
    """Ruang detektor tetap kadang terang kadang remang, mode apa pun.

    Yang diperiksa: mode ini tidak MENAMBAHKAN paksaan mati pada langkah
    terang. Apakah sebuah langkah menyala atau tidak tetap ditentukan katalog
    dan pilihan orang — eksposur misalnya memang bawaan_aktif=False dan itu
    bukan urusan mode warna.
    """
    r = mw.terap_ke_aug({"aug": {}}, mw.WARNA)
    for o in mw.OP_TERANG:
        assert o not in r["aug"], (
            f"{o} disentuh mode warna padahal ia cuma mengubah terang")


def test_daftar_operasi_rona_cocok_dengan_katalog():
    """Kalau ada operasi yang berganti nama, di sinilah ketahuannya.

    Daftar OP_GESER_RONA ditulis tangan; nama yang meleset membuat operasi
    penggeser rona lolos diam-diam di mode warna, dan datasetnya jadi salah
    tanpa ada yang tahu.
    """
    from app.services import olah

    kat = olah.katalog_json()["aug"]
    for o in mw.OP_GESER_RONA:
        assert o in kat, f"OP_GESER_RONA menyebut `{o}` yang tidak ada di katalog"
    for o in mw.OP_TERANG:
        assert o in kat, f"OP_TERANG menyebut `{o}` yang tidak ada di katalog"


# ============================================================
# SAMPEL LATAR YANG TERLALU SEDIKIT
# ============================================================
#
# Form latar hanya menerima 1-3 foto, jadi memakai foto aslinya berarti n=3 —
# dan pada n=3 satu deteksi sudah 33%, persis menyentuh ambang buruk. Terjadi
# sungguhan pada projek paragon: 1 dari 3 foto, lalu vonisnya BURUK atas dasar
# satu kejadian. Sampel sekecil itu tidak bisa membedakan "model bermasalah"
# dari "satu foto yang kebetulan aneh".

def test_satu_deteksi_dari_tiga_latar_tidak_langsung_divonis_buruk():
    h = ev.nilai_default({"kaleng": 1}, 3)
    assert h["tingkat"] == "tipis", h
    assert "terlalu sedikit" in h["pesan"]
    # Angkanya tetap dilaporkan — orang berhak melihatnya.
    assert h["tally"] == {"kaleng": 1} and h["porsi"] == pytest.approx(33.3, abs=0.1)


def test_tanpa_deteksi_pun_sampel_tipis_tidak_dinyatakan_baik():
    """Nol dari tiga bukan bukti bersih.

    Melaporkannya "baik" memberi rasa aman yang tidak berdasar — kesalahan
    yang sama bentuknya dengan 0/0 yang dulu dilaporkan "baik".
    """
    assert ev.nilai_default({}, 3)["tingkat"] == "tipis"
    assert ev.nilai_default({}, ev.MIN_LATAR)["tingkat"] == "baik"


def test_sampel_cukup_tetap_divonis_seperti_biasa():
    assert ev.nilai_default({"kaleng": 9}, 20)["tingkat"] == "buruk"
    assert ev.nilai_default({"botol": 1}, 20)["tingkat"] == "baik"


def test_putusan_tidak_meloloskan_kelas_default_yang_belum_terukur():
    """"Belum bisa dinilai" tidak boleh terbaca sebagai "lolos"."""
    p = ev.putusan({"tingkat": "baik", "skor": 2.0, "pesan": "stabil"},
                   {"n": 5, "persen": 90.0, "benar": 4},
                   ev.nilai_default({}, 3))
    assert p["tingkat"] == "sedang"
    assert "belum bisa dinilai" in p["pesan"].lower()


def test_pelat_didahulukan_daripada_foto_asli(tmp_path):
    """Pelat jauh lebih banyak (9 per foto) DAN lebih tepat.

    Ia persis latar yang dilihat model saat augmentasi menempel objek ke
    ruangan, jadi deteksi palsu di sana terjadi pada gambar yang bentuknya
    sama dengan yang dilihat model sepanjang training.
    """
    from app.services import latar

    akar = tmp_path / latar.FOLDER
    (akar / latar.ASLI).mkdir(parents=True)
    (akar / latar.PELAT).mkdir(parents=True)
    for i in range(3):
        cv2.imwrite(str(akar / latar.ASLI / f"f{i}.jpg"),
                    np.zeros((64, 64, 3), np.uint8))
    for i in range(9):
        cv2.imwrite(str(akar / latar.PELAT / f"p{i}.png"),
                    np.zeros((64, 64, 3), np.uint8))
    p, sumber = ev.foto_latar(tmp_path)
    assert len(p) == 9, "pelat harus didahulukan"
    assert "pelat" in sumber


def test_tanpa_latar_sama_sekali_jatuh_ke_petak_pojok(tmp_path):
    p, sumber = ev.foto_latar(tmp_path)
    assert p == []
    assert "tebakan" in sumber, "cara cadangan harus menyebut dirinya tebakan"
