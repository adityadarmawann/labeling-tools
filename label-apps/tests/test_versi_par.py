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
    """Sidik jari transform: seluruh argumen yang benar-benar dipakainya."""
    return {"kelas": type(t).__name__, "p": t.p, **t.get_transform_init_args()}


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
