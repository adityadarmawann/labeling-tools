"""
Jalur augmentasi GPU, diuji BERDAMPINGAN dengan jalur CPU.

Yang diuji di sini bukan "pikselnya sama" — memang tidak akan sama, dan itu
dijelaskan panjang di kepala olah_gpu.py. Yang diuji tiga janji yang kalau
dilanggar membuat jalur GPU berbahaya, bukan sekadar berbeda:

  1. SEBARANNYA setara. Derau harus tetap derau dengan kekuatan yang sama,
     terang harus tetap menerangkan sebanyak yang diminta. Kalau sebarannya
     bergeser, model dilatih pada dunia yang berbeda tanpa ada yang tahu.
  2. RENTANG PARAMETERNYA sama persis dengan CPU — angka v14 tidak boleh
     bergeser hanya karena jalurnya pindah.
  3. Benar-benar LEBIH CEPAT. Operasi GPU yang lebih lambat daripada CPU tidak
     boleh ada di sini; ia menambah kode yang harus dipelihara sambil
     memperlambat hasilnya.

Berjalan hanya kalau torch + CUDA ada. Di .venv biasa seluruh berkas ini
di-skip, dan itu memang yang diinginkan: jalur CPU tidak boleh menuntut torch.
"""
import random
import time

import numpy as np
import pytest

torch = pytest.importorskip("torch", reason="jalur GPU butuh .venv-gpu")
pytestmark = pytest.mark.skipif(not torch.cuda.is_available(),
                                reason="CUDA tidak terbaca")

from app.services import olah, olah_gpu           # noqa: E402


def _gambar(n=640, benih=3):
    rng = np.random.default_rng(benih)
    im = (rng.random((n, n, 3)) * 70 + 30).astype(np.uint8)
    a, b = n // 3, n // 3 * 2
    im[a:b, a:b] = np.clip(
        np.array((40, 40, 200)) + rng.integers(-45, 45, (b - a, b - a, 3)),
        0, 255).astype(np.uint8)
    return im


def _bolak_balik(im):
    """CPU -> GPU -> CPU tanpa operasi apa pun."""
    return olah_gpu.ke_cpu(olah_gpu.ke_gpu(im))


# ============================================================
# 1. PINDAH DATA TIDAK MERUSAK APA PUN
# ============================================================

def test_pulang_pergi_gpu_tidak_mengubah_gambar():
    """Kalau sekadar memindahkan sudah mengubah piksel, seluruh perbandingan
    di bawah ini kehilangan artinya."""
    im = _gambar()
    balik = _bolak_balik(im)
    assert balik.shape == im.shape and balik.dtype == im.dtype
    assert np.array_equal(balik, im), np.abs(
        balik.astype(int) - im.astype(int)).max()


def test_urutan_kanal_bgr_tidak_tertukar():
    """Penukaran kanal adalah kekeliruan yang paling gampang lolos: gambarnya
    tetap terlihat wajar, cuma birunya jadi merah."""
    im = np.zeros((32, 32, 3), np.uint8)
    im[:, :, 0] = 200          # B
    im[:, :, 1] = 100          # G
    im[:, :, 2] = 50           # R
    balik = _bolak_balik(im)
    assert tuple(balik[0, 0]) == (200, 100, 50), tuple(balik[0, 0])


# ============================================================
# 2. SEBARANNYA SETARA DENGAN CPU
# ============================================================

def _std_derau(sebelum, sesudah):
    return float((sesudah.astype(np.float64) - sebelum.astype(np.float64)).std())


def test_derau_gauss_kekuatannya_setara_cpu():
    """Yang harus sama bukan butirannya, melainkan seberapa kuat deraunya."""
    im = np.full((512, 512, 3), 128, np.uint8)
    # CPU: albumentations, std 0,05 dari 255 -> ~12,75
    import albumentations as A
    t = A.GaussNoise(std_range=(0.05, 0.05), p=1.0)
    cpu = _std_derau(im, t(image=im)["image"])
    x = olah_gpu.ke_gpu(im)
    gpu = _std_derau(im, olah_gpu.ke_cpu(
        olah_gpu.derau_gauss(x, None, min=0.05, maks=0.05)))
    assert abs(cpu - gpu) / max(cpu, 1e-6) < 0.15, (cpu, gpu)


def test_terang_kontras_menggeser_sebanyak_yang_diminta():
    """Terang +0,3 harus menaikkan rata-rata kira-kira 0,3x255 di kedua jalur."""
    im = np.full((256, 256, 3), 100, np.uint8)
    x = olah_gpu.ke_gpu(im)
    naik = olah_gpu.ke_cpu(olah_gpu.terang_kontras(
        x, None, terang_min=0.30, terang_maks=0.30,
        kontras_min=0.0, kontras_maks=0.0)).mean()
    assert 120 < naik < 145, naik            # 100 + 0,3*100 = 130
    turun = olah_gpu.ke_cpu(olah_gpu.terang_kontras(
        x, None, terang_min=-0.30, terang_maks=-0.30,
        kontras_min=0.0, kontras_maks=0.0)).mean()
    assert 60 < turun < 80, turun


def test_grayscale_sama_dengan_opencv():
    """Bobot BT.601 dan urutan BGR — satu-satunya operasi yang memang HARUS
    hampir identik, karena rumusnya tetap, bukan acak."""
    import cv2
    im = _gambar(128)
    cpu = cv2.cvtColor(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    gpu = olah_gpu.ke_cpu(olah_gpu.grayscale(olah_gpu.ke_gpu(im), None))
    beda = np.abs(cpu.astype(int) - gpu.astype(int))
    assert beda.max() <= 2, beda.max()


GEOMETRI = ("rotasi", "affine", "crop_acak", "flip_h", "flip_v", "rotasi_90",
            "shear")


@pytest.mark.parametrize("oid", GEOMETRI)
def test_operasi_geometri_tidak_boleh_ada_di_jalur_gpu(oid):
    """Aturan yang membuat jalur GPU tidak perlu memindahkan label sama sekali.

    Semua yang ada di OPERASI dijamin TIDAK menggeser piksel, jadi label dari
    tahap geometri di CPU tetap berlaku apa adanya. Begitu satu operasi
    geometri masuk ke sini, jaminan itu batal dan labelnya meleset diam-diam --
    gambarnya tetap terlihat wajar, cuma kotaknya tidak lagi menunjuk objek.

    Geometri juga tidak layak dipindah: terukur cuma 9,8% dari waktu
    augmentasi, sementara yang fotometrik 90,2%.
    """
    assert oid not in olah_gpu.OPERASI


def test_operasi_gpu_tidak_menggeser_piksel():
    """Diperiksa dengan mengukur, bukan dengan percaya pada daftar di atas.

    Pusat massa kecerahan tidak boleh berpindah: operasi fotometrik mengubah
    NILAI piksel, bukan LETAKNYA.
    """
    im = np.zeros((128, 128, 3), np.uint8)
    im[20:50, 80:110] = 220                     # gumpalan terang di kanan atas
    ys, xs = np.mgrid[0:128, 0:128]

    def pusat(a):
        b = a.mean(2).astype(np.float64)
        b = np.clip(b - b.mean(), 0, None)
        t = b.sum()
        return (float((xs * b).sum() / t), float((ys * b).sum() / t)) if t else (0, 0)

    asal = pusat(im)
    for oid, fn in sorted(olah_gpu.OPERASI.items()):
        out = olah_gpu.ke_cpu(fn(olah_gpu.ke_gpu(im), None))
        px, py = pusat(out)
        assert abs(px - asal[0]) < 6 and abs(py - asal[1]) < 6, (
            f"{oid} menggeser pusat terang {asal} -> {(px, py)}")


def test_vignette_menggelapkan_tepi_bukan_seluruhnya():
    """Janji yang sama dengan jalur CPU, diperiksa dengan cara yang sama."""
    im = np.full((256, 256, 3), 180, np.uint8)
    out = olah_gpu.ke_cpu(olah_gpu.vignette(
        olah_gpu.ke_gpu(im), None, kuat_min=0.85, kuat_maks=0.85))
    tengah = out[108:148, 108:148].mean()
    tepi = np.concatenate([out[:20].ravel(), out[-20:].ravel()]).mean()
    assert (180 - tepi) > (180 - tengah) * 2, (tengah, tepi)


def test_downscale_menghapus_tekstur():
    import cv2
    im = _gambar(256)
    tajam = lambda a: cv2.Laplacian(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY),
                                    cv2.CV_64F).var()
    out = olah_gpu.ke_cpu(olah_gpu.downscale(
        olah_gpu.ke_gpu(im), None, skala_min=0.2, skala_maks=0.2))
    assert tajam(out) < tajam(im) / 3, (tajam(im), tajam(out))


def test_gamma_melengkungkan_bukan_menggeser():
    """Gamma < 1 menerangkan bagian gelap lebih banyak daripada bagian terang.
    Kalau ia menggeser rata, yang ditulis bukan gamma melainkan penambah."""
    im = np.zeros((64, 192, 3), np.uint8)
    im[:, :64] = 40
    im[:, 64:128] = 128
    im[:, 128:] = 220
    out = olah_gpu.ke_cpu(olah_gpu.gamma(olah_gpu.ke_gpu(im), None,
                                         min=50, maks=50))
    naik_gelap = out[:, :64].mean() - 40
    naik_terang = out[:, 128:].mean() - 220
    assert naik_gelap > naik_terang * 2, (naik_gelap, naik_terang)


def test_iluminan_menjaga_luminans_rata_rata():
    """Tanpa penjagaan ini, gain jenuh memotong kanal ke 255 dan objek jadi
    bidang polos — label yang menunjuk ke ruang kosong."""
    im = np.full((128, 128, 3), 120, np.uint8)
    for _ in range(12):
        out = olah_gpu.ke_cpu(olah_gpu.iluminan(olah_gpu.ke_gpu(im), None))
        assert 100 < out.mean() < 140, out.mean()


# ============================================================
# 3. BENAR-BENAR LEBIH CEPAT
# ============================================================

def _ms_gpu(fn, n=20, ulang=5):
    """Waktu TERBAIK dari beberapa ulangan, bukan rata-ratanya.

    Beban dari luar — training yang sedang jalan, server yang memakai kartu
    yang sama — hanya bisa membuat sebuah pengukuran LEBIH LAMBAT, tidak
    pernah lebih cepat. Jadi yang paling sedikit tercemar adalah yang
    tercepat, dan itu yang mendekati biaya sesungguhnya.

    Dengan rata-rata, tes ini gagal acak: 11 Sep 2026 color_jitter jatuh satu
    kali di tengah suite penuh lalu lolos tiga kali berturut-turut saat
    dijalankan sendirian. Tes yang gagal acak melatih orang mengabaikan
    kegagalan, dan itu jauh lebih mahal daripada tes yang sedikit longgar.
    """
    for _ in range(3):
        fn()
    torch.cuda.synchronize()
    terbaik = float("inf")
    for _ in range(ulang):
        a = time.perf_counter()
        for _ in range(n):
            fn()
        torch.cuda.synchronize()
        terbaik = min(terbaik, (time.perf_counter() - a) / n * 1000)
    return terbaik


@pytest.fixture(scope="module", autouse=True)
def _panaskan_gpu():
    """Naikkan clock GPU sebelum tes kecepatan mengukur apa pun.

    Kartu yang menganggur berjalan di clock rendah dan baru naik setelah
    dibebani. Tanpa pemanasan ini, tes yang kebetulan berjalan PALING AWAL
    mengukur kartu yang masih lambat lalu gagal — dan yang gagal berpindah-
    pindah mengikuti urutan tes, sehingga terlihat seperti operasi yang
    berbeda-beda yang bermasalah.

    Terukur: color_jitter gagal sebagai tes ke-4 di dalam suite, tetapi
    diukur sendirian ia 0,767 ms lawan lantai 2,305 ms — 3,00x lebih cepat,
    lolos dengan nyaman. Yang salah pengukurannya, bukan operasinya.
    """
    if not olah_gpu.tersedia():
        yield
        return
    x = torch.randn(2048, 2048, device="cuda")
    a = time.perf_counter()
    while time.perf_counter() - a < 0.6:
        (x @ x).sum()
    torch.cuda.synchronize()
    yield


def _gpu_sedang_sibuk(n=16, jeda=0.1) -> int:
    """Pemakaian GPU oleh pekerjaan LAIN, 0 kalau tidak terbaca.

    Tes kecepatan hanya sah di kartu yang menganggur. Kalau ada training atau
    pekerjaan lain memakai kartunya, operasi GPU mana pun akan kalah dari
    lantai CPU — itu FISIKA, bukan cacat kode, dan menuntutnya tetap menang
    berarti menuntut hal yang mustahil.

    Yang diambil nilai TERENDAH dalam satu jendela, bukan rata-ratanya, dan
    itu bukan kerapian: suite ini membebani GPU-nya SENDIRI. Angka utilisasi
    nvidia-smi adalah rata-rata bergulir sekitar satu detik, jadi tepat
    sesudah tes sebelumnya selesai ia masih tinggi walau kartunya sudah
    menganggur. Beban kita sendiri mereda dalam jendela ini dan terbaca lewat
    nilai terendahnya; beban luar yang berkelanjutan tidak pernah mereda.

    Versi pertama memakai median, dan akibatnya enam dari lima belas tes
    melewatkan diri di kartu yang justru sedang menganggur.
    """
    try:
        import pynvml

        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        rendah = 100
        for _ in range(n):
            rendah = min(rendah, pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
            if rendah < 10:
                return rendah              # sudah jelas menganggur
            time.sleep(jeda)
        return rendah
    except Exception:                            # noqa: BLE001
        return 0


# Batas yang dituntut: operasi GPU tidak boleh melebihi lantai CPU. Longgar,
# dan longgarnya DISENGAJA — lihat docstring tesnya.
BATAS_LANTAI = 5.0


@pytest.mark.parametrize("nama", sorted(olah_gpu.OPERASI))
def test_tidak_ada_operasi_gpu_yang_biayanya_pathologis(nama):
    """Penjaga terhadap operasi yang runtuh biayanya, bukan tolok ukur halus.

    NAMA TES INI PERNAH BERBOHONG. Ia bernama "lebih cepat daripada padanan
    CPU", padahal yang dibandingkan bukan padanannya melainkan satu ekspresi
    numpy sembarang seukuran gambar — sebuah LANTAI biaya. Namanya sudah
    diperbaiki supaya tidak menjanjikan hal yang tidak ia periksa.

    KENAPA AMBANGNYA LONGGAR. Lantai CPU-nya sendiri tidak stabil: terukur
    0,756 ms di dalam suite dan 2,380 ms saat diukur sendirian — bergoyang
    tiga kali lipat mengikuti keadaan clock prosesor. Menuntut "GPU < lantai"
    berarti menuntut keputusan yang tajam dari acuan yang goyah, dan
    akibatnya color_jitter gagal-lolos bergantian di mesin yang sama:
    11 Sep 2026 ia jatuh sekali di suite penuh lalu lolos tiga kali berturut-
    turut sendirian.

    Terukur dengan KEDUANYA dipanaskan lebih dulu (lantai 2,380 ms):
        color_jitter   0,767 ms  0,33x lantai   <- yang paling mahal
        hue_sat        0,660 ms  0,29x
        saturasi       0,634 ms  0,28x
        eksposur       0,223 ms  0,10x
        blur           0,175 ms  0,09x
        ...
        iluminan       0,037 ms  0,02x          <- yang paling murah
    Jadi yang termahal pun masih tiga kali di bawah lantai. Ambang 5x lantai
    memberi ruang bagi goyangan acuannya sambil tetap menangkap yang memang
    harus ditangkap: operasi yang tanpa sengaja menyinkronkan tiap piksel,
    jatuh kembali ke CPU, atau menyalin bolak-balik tiap panggilan — semuanya
    melompat jauh di atas 5x, bukan mengambang di sekitar 1x.

    YANG TES INI TIDAK BISA JANJIKAN. Ia tidak akan menangkap kasus seperti
    decode JPEG (nvJPEG 3,75 ms lawan cv2 3,65 ms) yang cuma 3% lebih lambat.
    Perbandingan sedekat itu menuntut padanan CPU yang sesungguhnya, bukan
    lantai — dan sampai itu ada, keputusan seperti "decode tetap di CPU"
    diambil dari pengukuran tangan yang dicatat di olah_gpu.py, bukan dari
    sini.
    """
    sibuk = _gpu_sedang_sibuk()
    if sibuk >= 25:
        pytest.skip(f"GPU sedang dipakai {sibuk}% — pengukuran kecepatan tidak "
                    "sah di kartu yang sedang direbut")
    im = _gambar(640)
    fn = olah_gpu.OPERASI[nama]
    x = olah_gpu.ke_gpu(im).repeat(8, 1, 1, 1)
    per_gambar = _ms_gpu(lambda: fn(x, None)) / 8
    # Pembanding CPU: satu operasi numpy sederhana seukuran gambar yang sama.
    # Bukan padanan persis, melainkan LANTAI biaya -- satu lintasan penuh atas
    # gambarnya. Operasi GPU yang tidak mengalahkan lantai ini tidak layak ada.
    f = im.astype(np.float32)
    lantai = float("inf")
    for _ in range(5):                     # terbaik juga, dengan alasan sama
        a = time.perf_counter()
        for _ in range(10):
            np.clip(f * 1.1 + 3.0, 0, 255)
        lantai = min(lantai, (time.perf_counter() - a) / 10 * 1000)
    assert per_gambar < lantai * BATAS_LANTAI, (
        f"{nama}: GPU {per_gambar:.3f} ms/gambar, lantai CPU {lantai:.3f} ms "
        f"({per_gambar / lantai:.2f}x — batasnya {BATAS_LANTAI}x). Biaya "
        "sebesar itu berarti operasinya jatuh ke CPU atau menyalin bolak-balik "
        "tiap panggilan.")


# ============================================================
# PARAMETER YANG DIKIRIM KE OPERASI GPU
# ============================================================
#
# jalankan_gpu membongkar parameternya dengan **arg, jadi SATU kunci yang
# tidak ada di tanda tangan fungsinya sudah cukup menjatuhkan seluruh
# pembuatan versi. Itu pernah terjadi: `aktif` ikut terbawa dari resep dan
# gamma() menolaknya. Jalur CPU kebal karena ia menyerahkan parameternya
# sebagai satu dict, jadi bug ini TIDAK bisa tertangkap oleh tes jalur CPU.
#
# Yang membuatnya mahal bukan crash-nya, melainkan tempatnya: galat itu muncul
# di thread pembuatan versi, penanganannya di router memanggil nama yang tidak
# ada, dan akibatnya pembuatan versi menggantung sampai batas waktu 180 detik
# tanpa keterangan apa pun. Dari layar itu terbaca "GPU lambat".

def _semua_resep_aktif():
    """Resep yang menyalakan SETIAP operasi GPU secara eksplisit.

    Eksplisit itu intinya: operasi yang tidak disebut memakai bawaan dan
    tidak pernah membawa kunci `aktif`, jadi resep kosong justru melewatkan
    bug ini sepenuhnya.
    """
    from app.services import olah
    kat = olah.katalog_json()["aug"]
    return {"aug": {oid: {"aktif": True} for oid in olah_gpu.DIPEGANG_GPU
                    if oid in kat}}


def test_par_gpu_tidak_meloloskan_kunci_asing():
    """Tiap kunci yang keluar harus benar-benar diterima fungsinya."""
    import inspect

    from app.services import olah
    par = olah_gpu.par_gpu(_semua_resep_aktif(), olah.katalog_json()["aug"])
    assert par, "tidak ada satu pun operasi GPU yang aktif — resepnya salah"
    for oid, p in par.items():
        sah = set(inspect.signature(olah_gpu.OPERASI[oid]).parameters)
        # `p` itu peluang, dibaca jalankan_gpu sendiri dan tidak diteruskan.
        asing = set(p) - sah - {"p"}
        assert not asing, f"{oid} akan menerima kunci yang ditolaknya: {asing}"


@pytest.mark.skipif(not olah_gpu.tersedia(), reason="GPU tidak tersedia")
def test_jalankan_gpu_dengan_semua_operasi_dinyalakan_eksplisit():
    """Resep 'semua menyala' harus benar-benar berjalan, bukan melempar.

    Inilah bentuk resep yang dikirim panel saat orang menyalakan seluruh
    saklarnya, dan bentuk inilah yang dulu menjatuhkan pembuatan versi.
    """
    from app.services import olah
    par = olah_gpu.par_gpu(_semua_resep_aktif(), olah.katalog_json()["aug"])
    rng = random.Random(7)
    img = np.random.default_rng(3).integers(0, 255, (96, 128, 3), dtype=np.uint8)
    # Dijalankan berkali-kali: peluang tiap operasi di bawah 1, jadi sekali
    # jalan belum tentu menyentuh operasi yang rusak.
    for _ in range(40):
        out = olah_gpu.jalankan_gpu(img, par, rng)
        assert out.shape == img.shape
        assert out.dtype == np.uint8


# ============================================================
# MENEMPEL OBJEK KE PELAT LATAR
# ============================================================
#
# tempel_gpu satu-satunya operasi GPU yang menyentuh kode pembawa label, jadi
# yang diuji di sini bukan cuma kecepatannya melainkan bahwa hasilnya benar-
# benar sama dengan cv2. Dua hal yang gampang salah dan keduanya mengubah
# lebar bulu campuran di tepi objek: kernel Gauss cv2 dihitung dari k ketika
# sigma=0 (jadi harus diambil dari cv2.getGaussianKernel, bukan diturunkan
# ulang), dan border-nya BORDER_REFLECT_101, bukan nol.

def _masker_acak(S, benih=3):
    import cv2
    rng = np.random.default_rng(benih)
    m = np.zeros((S, S), np.uint8)
    for _ in range(3):
        cv2.fillPoly(m, [rng.integers(10, S - 10, (5, 2)).astype(np.int32)], 255)
    return m


def _tempel_cpu(kanvas, kecil, m, oy, ox, k):
    """Cara CPU, ditulis polos — acuan yang harus ditiru tempel_gpu."""
    import cv2
    kh, kw = kecil.shape[:2]
    m = cv2.dilate(m, np.ones((k, k), np.uint8))
    m = cv2.GaussianBlur(m, (k, k), 0)
    a = (m.astype(np.float32) / 255.0)[:, :, None]
    petak = kanvas[oy:oy + kh, ox:ox + kw].astype(np.float32)
    return (kecil.astype(np.float32) * a + petak * (1 - a)).astype(np.uint8)


@pytest.mark.skipif(not olah_gpu.tersedia(), reason="GPU tidak tersedia")
@pytest.mark.parametrize("S", [128, 256, 400, 512])
def test_tempel_gpu_setara_cv2(S):
    """Selisihnya harus tinggal pembulatan, bukan bentuk bulu yang berbeda."""
    k = olah.TEMPEL_LEMBUT * 2 + 1
    rng = np.random.default_rng(7)
    kecil = rng.integers(0, 255, (S, S, 3), dtype=np.uint8)
    kanvas = rng.integers(0, 255, (S + 40, S + 40, 3), dtype=np.uint8)
    m = _masker_acak(S)
    g = olah_gpu.tempel_gpu(kanvas, kecil, m, 20, 20, k)
    assert g is not None, "jalur GPU menolak mengerjakannya"
    c = _tempel_cpu(kanvas, kecil, m, 20, 20, k)
    d = np.abs(c.astype(int) - g.astype(int))
    # Batasnya 1: itu pembulatan konvolusi terpisah, dan letaknya di pita bulu
    # campuran — artinya alfa bergeser 1/255 pada sebagian kecil tepi. Kalau
    # suatu saat angkanya melonjak, yang berubah bentuk maskernya, bukan
    # pembulatannya, dan itu memang harus menjatuhkan tes.
    assert d.max() <= 1, f"selisih {d.max()} terlalu besar untuk pembulatan"
    assert (d > 0).mean() < 0.05, f"{(d>0).mean():.1%} piksel berbeda"


@pytest.mark.skipif(not olah_gpu.tersedia(), reason="GPU tidak tersedia")
def test_tempel_gpu_masker_kosong_tidak_mengubah_kanvas():
    """Masker nol berarti objeknya tidak ditempel sama sekali."""
    k = olah.TEMPEL_LEMBUT * 2 + 1
    rng = np.random.default_rng(2)
    kecil = rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)
    kanvas = rng.integers(0, 255, (100, 100, 3), dtype=np.uint8)
    g = olah_gpu.tempel_gpu(kanvas, kecil, np.zeros((64, 64), np.uint8), 10, 10, k)
    assert np.array_equal(g, kanvas[10:74, 10:74])


@pytest.mark.skipif(not olah_gpu.tersedia(), reason="GPU tidak tersedia")
def test_tempel_gpu_masker_penuh_menimpa_seluruhnya():
    """Masker penuh berarti petaknya jadi objek itu, tanpa sisa kanvas."""
    k = olah.TEMPEL_LEMBUT * 2 + 1
    rng = np.random.default_rng(2)
    kecil = rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)
    kanvas = rng.integers(0, 255, (100, 100, 3), dtype=np.uint8)
    g = olah_gpu.tempel_gpu(kanvas, kecil, np.full((64, 64), 255, np.uint8),
                            10, 10, k)
    # Tepinya tetap berbulu karena blur menariknya ke bawah 255; yang diperiksa
    # bagian tengahnya, yang alfanya pasti penuh.
    assert np.array_equal(g[16:48, 16:48], kecil[16:48, 16:48])
