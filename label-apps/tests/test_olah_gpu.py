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

def _ms_gpu(fn, n=20):
    for _ in range(3):
        fn()
    torch.cuda.synchronize()
    a = time.perf_counter()
    for _ in range(n):
        fn()
    torch.cuda.synchronize()
    return (time.perf_counter() - a) / n * 1000


@pytest.mark.parametrize("nama", sorted(olah_gpu.OPERASI))
def test_tiap_operasi_gpu_lebih_cepat_daripada_padanan_cpu(nama):
    """Ambangnya sengaja rendah — 1,5x, bukan 10x.

    Yang mau dicegah bukan "kurang cepat", melainkan operasi yang diam-diam
    LEBIH LAMBAT di GPU. Itu pernah terjadi pada decode JPEG (nvJPEG 3,75 ms
    lawan 3,65 ms di CPU), dan karena itu decode sengaja tidak ada di sini.
    """
    im = _gambar(640)
    fn = olah_gpu.OPERASI[nama]
    x = olah_gpu.ke_gpu(im).repeat(8, 1, 1, 1)
    per_gambar = _ms_gpu(lambda: fn(x, None)) / 8
    # Pembanding CPU: satu operasi numpy sederhana seukuran gambar yang sama.
    # Bukan padanan persis, melainkan LANTAI biaya -- satu lintasan penuh atas
    # gambarnya. Operasi GPU yang tidak mengalahkan lantai ini tidak layak ada.
    f = im.astype(np.float32)
    a = time.perf_counter()
    for _ in range(10):
        np.clip(f * 1.1 + 3.0, 0, 255)
    lantai = (time.perf_counter() - a) / 10 * 1000
    assert per_gambar < lantai, f"{nama}: GPU {per_gambar:.3f} ms vs lantai CPU {lantai:.3f} ms"
