"""
Angka augmentasi yang bisa digeser orang.

Yang dijaga di sini bukan nilai persisnya, melainkan tiga janji yang dibuat
antarmukanya:

  1. Angka bawaan di katalog SAMA dengan angka yang dipakai pembangun kalau
     tidak ada yang menggeser apa pun. Katalog mengaku "bawaan dari v14";
     kalau keduanya berbeda, pengakuan itu bohong dan orang mengira sedang
     memakai v14 padahal tidak.
  2. Menggeser batang benar-benar mengubah transform. Batang yang tidak
     tersambung ke apa pun lebih buruk daripada tidak ada batang, karena ia
     terlihat bekerja.
  3. Angka di luar rentang dijepit, dan pasangan min/maks yang terbalik
     dibetulkan, supaya albumentations tidak melempar di tengah pembuatan
     versi yang sudah berjalan setengah jam.
"""
import random

import cv2
import numpy as np
import pytest

from app.services import olah


def bangun_satu(oid: str, par: dict):
    """Satu transform, lewat jalur yang sama dengan bangun_pipeline."""
    A = olah._A()
    bersih = olah.saring_par(olah.KATALOG_AUG[oid]["param"], par)
    for o, _saklar, b in olah.AUG_V14:
        if o == oid:
            return b(A, bersih)
    for o, b in olah.AUG_TAMBAHAN:
        if o == oid:
            return b(A, bersih)
    raise KeyError(oid)


def sidik(t) -> dict:
    """Sidik jari transform: seluruh argumen yang benar-benar dipakainya.

    A.Lambda perlu penanganan sendiri. Argumen initnya cuma nama dan fungsi,
    jadi dua Lambda dengan setelan yang jauh berbeda punya sidik yang sama
    persis -- dan penjaga di bawah, yang memastikan tiap batang geser benar-
    benar tersambung, akan lolos tanpa menguji apa pun. Setelannya ditempelkan
    ke fungsinya di olah.py justru untuk bisa dibaca di sini.
    """
    dasar = {"kelas": type(t).__name__, "p": t.p, **t.get_transform_init_args()}
    # albumentations menyimpan fungsinya di custom_apply_fns, bukan di .image.
    fn = (getattr(t, "custom_apply_fns", None) or {}).get("image")
    if getattr(fn, "setelan", None) is not None:
        dasar["setelan"] = fn.setelan
    return dasar


def bawaan(oid: str) -> dict:
    return {k: s["bawaan"] for k, s in olah.KATALOG_AUG[oid]["param"].items()}


SEMUA = list(olah.KATALOG_AUG)
ANGKA = [(oid, k) for oid in SEMUA
         for k, s in olah.KATALOG_AUG[oid]["param"].items()
         if s["jenis"] in ("int", "float", "peluang")]


@pytest.mark.parametrize("oid", SEMUA)
def test_bawaan_katalog_sama_dengan_bawaan_pembangun(oid):
    """
    Dibandingkan lewat transform yang jadi, bukan dengan menyalin angka ke
    tes: menyalin angka cuma memindahkan kesempatan salah ketik ke sini.
    """
    assert sidik(bangun_satu(oid, {})) == sidik(bangun_satu(oid, bawaan(oid)))


@pytest.mark.parametrize("oid,kunci", ANGKA)
def test_menggeser_satu_angka_mengubah_transform(oid, kunci):
    s = olah.KATALOG_AUG[oid]["param"][kunci]
    lain = s["maks"] if abs(s["maks"] - s["bawaan"]) > 1e-9 else s["min"]
    par = {**bawaan(oid), kunci: lain}
    assert sidik(bangun_satu(oid, par)) != sidik(bangun_satu(oid, {})), (
        f"{oid}.{kunci} digeser ke {lain} tetapi transformnya tidak berubah")


@pytest.mark.parametrize("oid", SEMUA)
def test_setiap_operasi_punya_peluang_yang_tersambung(oid):
    """Peluang ada di SEMUA operasi, dan ia harus benar-benar jadi `p=`."""
    assert "p" in olah.KATALOG_AUG[oid]["param"]
    assert bangun_satu(oid, {**bawaan(oid), "p": 0.123}).p == pytest.approx(0.123)


@pytest.mark.parametrize("oid", SEMUA)
def test_angka_di_luar_rentang_dijepit_bukan_dilempar(oid):
    liar = {k: (10_000 if i % 2 else -10_000)
            for i, k in enumerate(olah.KATALOG_AUG[oid]["param"])}
    bersih = olah.saring_par(olah.KATALOG_AUG[oid]["param"], liar)
    for k, s in olah.KATALOG_AUG[oid]["param"].items():
        assert s["min"] <= bersih[k] <= s["maks"], f"{oid}.{k} = {bersih[k]}"
    bangun_satu(oid, liar)          # tidak boleh melempar


def test_pasangan_min_maks_terbalik_dibetulkan():
    """
    Orang menggeser batang bawah melewati batang atas. albumentations
    menjawab itu dengan ValueError di tengah pembuatan versi; kita
    membetulkannya lebih dulu.
    """
    spec = olah.KATALOG_AUG["gamma"]["param"]
    hasil = olah.saring_par(spec, {"min": 100, "maks": 100})
    assert hasil["min"] <= hasil["maks"]
    hasil = olah.saring_par(spec, {"min": 300, "maks": 10})
    assert hasil == {"min": 100, "maks": 150} or hasil["min"] <= hasil["maks"]
    bangun_satu("gamma", {"min": 300, "maks": 10})


@pytest.mark.parametrize("oid,kunci", [
    (oid, k) for oid in olah.KATALOG_AUG
    for bawah, atas in olah.PASANGAN_PAR
    if bawah in olah.KATALOG_AUG[oid]["param"]
    and atas in olah.KATALOG_AUG[oid]["param"]
    for k in (bawah, atas)])
def test_menggeser_satu_batang_saja_tidak_membalik_pasangan(oid, kunci):
    """
    Popup hanya menulis batang yang DISENTUH, jadi pasangannya sering tidak
    ikut disebut sama sekali. derau_gauss.min bisa digeser sampai 0,5
    sementara maks-nya masih di bawaan 0,1, dan yang sampai ke albumentations
    adalah std_range=(0,5, 0,1) — ValueError, di tengah pembuatan versi yang
    sudah berjalan berjam-jam.

    Penjaga lamanya hanya menukar kalau KEDUA kunci ada di resep, jadi ia
    melewatkan justru bentuk yang paling mungkin terjadi.
    """
    s = olah.KATALOG_AUG[oid]["param"][kunci]
    for nilai in (s["min"], s["maks"]):
        bangun_satu(oid, {kunci: nilai})       # tidak boleh melempar


def test_kunci_asing_tidak_dibuang_saring_par():
    """`sisi` dan `n_kelas` dipakai mesin tetapi tidak ditawarkan ke orang."""
    hasil = olah.saring_par(olah.KATALOG_AUG["crop_acak"]["param"],
                            {"sisi": 512, "skala_min": 0.5})
    assert hasil["sisi"] == 512 and hasil["skala_min"] == pytest.approx(0.5)


def test_pipeline_penuh_dengan_angka_geseran_benar_benar_jalan():
    """
    Bukan cuma terbangun: dijalankan pada gambar sungguhan, dengan SELURUH
    operasi menyala dan setiap angka digeser ke ujung rentangnya.
    """
    resep = {"aug": {}}
    for oid, spec in ((o, olah.KATALOG_AUG[o]["param"]) for o in SEMUA):
        par = {"aktif": True}
        for k, s in spec.items():
            par[k] = s["maks"] if s["jenis"] != "pilih" else s["bawaan"]
        par["p"] = 1.0
        resep["aug"][oid] = par
    pipa = olah.bangun_pipeline(resep)
    assert pipa is not None
    rng = np.random.default_rng(7)
    img = (rng.integers(0, 255, (120, 160, 3))).astype(np.uint8)
    hasil = olah.augmentasi_sekali(img, [], pipa, rng)
    assert hasil is not None
    keluar, _label = hasil
    assert keluar.ndim == 3 and keluar.shape[2] == 3


def test_katalog_json_membawa_spesifikasi_angka_ke_peramban():
    """Popup tidak bisa menggambar batang yang rentangnya tidak ia ketahui."""
    j = olah.katalog_json()
    rot = j["aug"]["rotasi"]["param"]["derajat"]
    assert rot["jenis"] == "int" and rot["bawaan"] == 25
    assert rot["min"] == 0 and rot["maks"] == 180 and rot["label"]
    p = j["aug"]["blur"]["param"]["p"]
    assert p["jenis"] == "peluang" and p["bawaan"] == pytest.approx(0.25)


def test_resize_yang_digeser_benar_benar_mengubah_ukuran_gambar():
    """Parameter preprocessing juga, bukan hanya augmentasi."""
    img = np.zeros((200, 300, 3), np.uint8)
    resep = {"pra": {"resize": {"aktif": True, "lebar": 320, "tinggi": 320,
                                "mode": "regang"}}}
    keluar, _ = olah.terapkan_pra(img, [], resep)
    assert keluar.shape[:2] == (320, 320)


def test_resize_menolak_ukuran_liar_tanpa_menggagalkan_versi():
    img = np.zeros((200, 300, 3), np.uint8)
    resep = {"pra": {"resize": {"aktif": True, "lebar": 99_999, "tinggi": 0}}}
    keluar, _ = olah.terapkan_pra(img, [], resep)
    assert keluar.shape[0] == 64 and keluar.shape[1] == 2048


# ============================================================
# TIGA SAKLAR v14 YANG DULU CUMA ADA DI KOMENTAR
# ============================================================
#
# `cahaya_vignette`, `kamera_downscale` dan `kamera_artefak_jpeg` terdaftar di
# panel saklar v14 tetapi tidak pernah menghasilkan satu berkas pun di sana:
# ketiganya varian Fase 5, dan bawaan PHASE5_VARIANTS_PER_RESULT=1 membuat
# jumlah varian tambahan nol. Di HIGOLAB komentarnya sempat menyatakan
# ketiganya "ADA dan benar-benar bekerja" padahal tidak satu pun terpasang.
#
# Karena itu yang diuji di sini BUKAN bahwa transformnya terbangun -- itu
# sudah dijaga tes di atas -- melainkan bahwa piksel gambarnya benar-benar
# berubah ke arah yang dimaksud, dan bahwa menggeser angkanya mengubah
# seberapa jauh. Sebuah saklar yang menyala tanpa mengubah apa pun persis
# itulah kekeliruan yang mau dicegah.

def _sendiri(oid: str, par: dict):
    """Pipeline yang HANYA menyalakan satu augmentasi.

    Augmentasi v14 bawaannya AKTIF semua, jadi tanpa mematikan sisanya yang
    terukur adalah gabungan flip, rotasi dan pergeseran warna -- bukan operasi
    yang sedang diperiksa.
    """
    aug = {o: {"aktif": False} for o, _s, _b in olah.AUG_V14}
    aug[oid] = {"aktif": True, "p": 1.0, **par}
    return olah.bangun_pipeline({"aug": aug})


def _tekstur(n=512):
    rng = np.random.default_rng(1)
    return np.clip(rng.normal(170, 38, (n, n, 3)), 0, 255).astype(np.uint8)


def _jalan(oid, par):
    img = _tekstur()
    label = [(0, [0.2, 0.2, 0.8, 0.2, 0.8, 0.8, 0.2, 0.8])]
    keluar, lab = olah.augmentasi_sekali(img, label, _sendiri(oid, par),
                                         random.Random(3))
    assert len(lab) == len(label), "label ikut hilang padahal piksel saja yang berubah"
    return img, keluar


def _ketajaman(a):
    return cv2.Laplacian(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()


def test_vignette_menggelapkan_tepi_bukan_seluruh_gambar():
    """Kalau ia menggelapkan rata, yang dibuat bukan vignette melainkan
    pengurang terang -- dan itu sudah ada di terang_kontras."""
    img, keluar = _jalan("vignette", {"kuat_min": 0.80, "kuat_maks": 0.85})
    n = img.shape[0]
    tengah = lambda a: a[n // 2 - 40:n // 2 + 40, n // 2 - 40:n // 2 + 40].mean()
    tepi = lambda a: np.concatenate([a[:40].ravel(), a[-40:].ravel()]).mean()
    turun_tengah = tengah(img) - tengah(keluar)
    turun_tepi = tepi(img) - tepi(keluar)
    assert turun_tepi > turun_tengah * 2, (turun_tepi, turun_tengah)


def test_vignette_makin_kuat_makin_gelap_tepinya():
    tepi = []
    for kuat in (0.05, 0.45, 0.85):
        _img, keluar = _jalan("vignette",
                              {"kuat_min": kuat, "kuat_maks": min(kuat + 0.05, 1.0)})
        tepi.append(np.concatenate([keluar[:40].ravel(),
                                    keluar[-40:].ravel()]).mean())
    assert tepi[0] > tepi[1] > tepi[2], tepi


def test_downscale_menghapus_tekstur_dan_makin_kecil_makin_hilang():
    """Yang hilang tekstur, bukan bentuk: itu gunanya meniru kamera murah."""
    tajam = []
    for a, b in ((0.90, 0.95), (0.45, 0.50), (0.10, 0.15)):
        img, keluar = _jalan("downscale", {"skala_min": a, "skala_maks": b})
        tajam.append(_ketajaman(keluar))
    assert _ketajaman(_tekstur()) > tajam[0] > tajam[1] > tajam[2], tajam


def test_artefak_jpeg_makin_rendah_mutunya_makin_jauh_dari_aslinya():
    beda = []
    for a, b in ((88, 92), (35, 40), (3, 6)):
        img, keluar = _jalan("artefak_jpeg", {"mutu_min": a, "mutu_maks": b})
        beda.append(float(np.abs(keluar.astype(np.float32)
                                 - img.astype(np.float32)).mean()))
    assert beda[0] < beda[1] < beda[2], beda


@pytest.mark.parametrize("oid", ["vignette", "downscale", "artefak_jpeg"])
def test_ketiganya_ditawarkan_ke_peramban_beserta_angkanya(oid):
    """Terpasang di mesin tetapi tidak muncul di panel sama saja tidak ada."""
    kat = olah.katalog_json()["aug"]
    assert oid in kat, f"{oid} tidak muncul di panel augmentasi"
    par = kat[oid]["param"]
    assert "p" in par
    assert [k for k in par if k != "p"], f"{oid} tidak punya angka yang bisa digeser"
