"""
Jalur augmentasi di GPU. Alternatif untuk olah.py, BUKAN penggantinya.

Kenapa ada
----------
Hambatan pembuatan versi bukan komputasi melainkan BANDWIDTH MEMORI. Terukur
di mesin ini (i5-10400F, 6 inti fisik, DDR4 dua kanal):

    augmentasi   16,52 ms   76,4% dari waktu satu gambar
    decode JPEG   3,18 ms   14,7%
    encode JPEG   1,92 ms    8,9%

Memparalelkannya ke banyak inti mentok di 2,9x dan tidak naik lagi walau
diberi 12 utas — tanda jalur ke memori yang penuh, bukan inti yang kurang.
Dua operasi termahal membangkitkan bilangan acak sebesar gambar penuh lalu
mengalikannya; itu memindahkan data, bukan menghitung.

GPU punya bandwidth ~10x lipat, dan di situlah untungnya (terukur, 640x640,
batch 32):

    derau ISO      55,72 ms -> 0,144 ms    388x
    derau Gauss    29,86 ms -> 0,074 ms    403x
    vignette        2,53 ms -> 0,027 ms     95x
    terang/kontras  1,13 ms -> 0,053 ms     21x
    encode JPEG     2,63 ms -> 0,23  ms     11x
    pindah data           -> 0,37 ms pulang-pergi

decode JPEG TIDAK ikut: nvJPEG terukur 3,75 ms lawan 3,65 ms di CPU, jadi
memindahkannya cuma menambah kerumitan tanpa hasil.

Apa yang TIDAK pindah, dan kenapa
---------------------------------
PREPROCESSING tetap di CPU, apa pun saklarnya. Ia menyentuh valid dan test,
dan keduanya alat ukur: dua versi dengan resep yang sama harus menghasilkan
valid/test yang identik byte demi byte (tests/test_versi_buat.py). Operasi
GPU tidak menjamin itu antar-versi driver, jadi memindahkannya berarti menukar
kemampuan membandingkan dua versi dengan kecepatan yang tidak dibutuhkan —
preprocessing cuma sekali per gambar, augmentasi puluhan kali.

Hasilnya BEDA dengan CPU, dan itu wajar
---------------------------------------
Bukan lebih buruk, melainkan tidak identik, karena tiga hal:

  1. Pembangkit acaknya beda mesin. Benih yang sama memberi angka yang
     berbeda pada numpy dan torch. Tetapi SEBARANNYA sama — dan untuk
     augmentasi itulah yang menentukan. Derau tetap derau dengan kekuatan
     yang sama; model tidak peduli butirannya nomor berapa.
  2. Operasi yang "sama" pun beda implementasi: penanganan tepi dan
     pembulatan blur 5x5 CPU vs GPU berbeda pada 12% piksel.
  3. Pembulatan float ke uint8 terjadi setelah rantai hitungan yang urutannya
     tidak sama persis.

Yang WAJIB tetap sama karena itu bukan piksel melainkan aturan:
  - rentang parameternya (angka v14: JPEG 35-90, vignette 0,40-0,85, dst.)
  - peluang tiap operasi dipakai
  - penjaga keterbacaan objek dan penjaga label
Ketiganya diuji berdampingan CPU lawan GPU di tests/test_olah_gpu.py.
"""
from __future__ import annotations

import os

from ..log import catat

log = catat("labelapp.olah_gpu")

# Saklar yang sama dengan yang dibaca start.sh untuk memilih venv. Dibaca
# sekali per proses: menggantinya di tengah jalan akan membuat separuh sebuah
# versi dibuat di CPU dan separuhnya di GPU.
MODE = (os.environ.get("LABELAPP_OLAH") or "cpu").strip().lower()

_siap: bool | None = None
_alasan = ""


def tersedia() -> bool:
    """True kalau jalur GPU benar-benar bisa dipakai SEKARANG.

    Diperiksa sungguhan, bukan disimpulkan dari saklarnya: saklar gpu di mesin
    tanpa GPU, atau dengan venv yang salah, harus jatuh ke CPU dengan alasan
    yang tercatat — bukan menjatuhkan pembuatan versi yang sudah berjalan
    setengah jam.
    """
    global _siap, _alasan
    if _siap is not None:
        return _siap
    if MODE != "gpu":
        _siap, _alasan = False, "LABELAPP_OLAH=%s" % MODE
        return False
    try:
        import torch
    except ImportError as e:
        _siap = False
        _alasan = ("torch tidak ada di venv ini (%s). Jalankan lewat "
                   ".venv-gpu, lihat requirements-gpu.txt" % e)
        log.warning("jalur GPU diminta tetapi %s", _alasan)
        return False
    if not torch.cuda.is_available():
        _siap = False
        _alasan = "torch ada tetapi CUDA tidak terbaca"
        log.warning("jalur GPU diminta tetapi %s", _alasan)
        return False
    _siap = True
    _alasan = "%s, torch %s, CUDA %s" % (torch.cuda.get_device_name(0),
                                         torch.__version__, torch.version.cuda)
    log.info("jalur augmentasi: GPU — %s", _alasan)
    return True


def alasan() -> str:
    """Kenapa GPU dipakai atau tidak — untuk dicatat di manifes versi."""
    if _siap is None:
        tersedia()
    return _alasan


# ================================================================ operasi GPU
#
# Tiap operasi menerima dan mengembalikan tensor float32 (B, 3, H, W) di GPU,
# dalam rentang 0..255 dan urutan kanal BGR — sama dengan yang dipakai OpenCV
# di jalur CPU, supaya tidak ada penukaran kanal yang tersembunyi.
#
# Yang WAJIB sama dengan jalur CPU:
#   - rentang parameternya (angka v14, dibaca dari KATALOG_AUG yang sama)
#   - peluang tiap operasi dipakai
# Yang boleh berbeda: piksel keluarannya. Lihat catatan di kepala berkas.
#
# Operasi yang di GPU TIDAK lebih cepat sengaja TIDAK ditulis di sini; ia tetap
# dikerjakan jalur CPU. Menulis versi GPU yang lebih lambat cuma menambah kode
# yang harus dipelihara sambil memperlambat hasilnya.


def _torch():
    import torch
    return torch


def terang_kontras(x, rng, terang_min=-0.45, terang_maks=0.35,
                   kontras_min=-0.30, kontras_maks=0.30):
    """RandomBrightnessContrast. Rumus albumentations: (x*(1+a) - mean*b + mean)."""
    torch = _torch()
    B = x.shape[0]
    a = torch.empty(B, 1, 1, 1, device=x.device).uniform_(terang_min, terang_maks)
    b = torch.empty(B, 1, 1, 1, device=x.device).uniform_(kontras_min, kontras_maks)
    rata = x.mean(dim=(1, 2, 3), keepdim=True)
    return ((x - rata) * (1 + b) + rata * (1 + a)).clamp_(0, 255)


def gamma(x, rng, min=60, maks=150):
    """RandomGamma. Nilainya persen, sama seperti albumentations."""
    torch = _torch()
    g = torch.empty(x.shape[0], 1, 1, 1, device=x.device).uniform_(min / 100.0,
                                                                   maks / 100.0)
    return ((x / 255.0).clamp_(0, 1).pow(g) * 255.0)


def derau_gauss(x, rng, min=0.02, maks=0.10):
    """GaussNoise. std dinyatakan sebagai pecahan dari 255, seperti di CPU."""
    torch = _torch()
    s = torch.empty(x.shape[0], 1, 1, 1, device=x.device).uniform_(min, maks) * 255.0
    return (x + torch.randn_like(x) * s).clamp_(0, 255)


def derau_iso(x, rng, warna=(0.01, 0.05), kuat=(0.1, 0.5)):
    """ISONoise: derau berwarna pada bayangan, meniru sensor ISO tinggi."""
    torch = _torch()
    B = x.shape[0]
    w = torch.empty(B, 3, 1, 1, device=x.device).uniform_(*warna)
    k = torch.empty(B, 1, 1, 1, device=x.device).uniform_(*kuat)
    return (x * (1 + torch.randn_like(x) * w) + torch.randn_like(x) * k * 25.0
            ).clamp_(0, 255)


def vignette(x, rng, kuat_min=0.40, kuat_maks=0.85,
             radius_min=0.45, radius_maks=0.85, geser=0.05):
    """Gelapkan tepi. Rumusnya sama dengan olah.vignette_acak (v14 f:1373)."""
    torch = _torch()
    B, _, H, W = x.shape
    d = x.device
    kuat = torch.empty(B, 1, 1, 1, device=d).uniform_(kuat_min, kuat_maks)
    rad = torch.empty(B, 1, 1, 1, device=d).uniform_(radius_min, radius_maks)
    cx = W / 2 + torch.empty(B, 1, 1, 1, device=d).uniform_(-W * geser, W * geser)
    cy = H / 2 + torch.empty(B, 1, 1, 1, device=d).uniform_(-H * geser, H * geser)
    ys = torch.arange(H, device=d, dtype=x.dtype).view(1, 1, H, 1)
    xs = torch.arange(W, device=d, dtype=x.dtype).view(1, 1, 1, W)
    jarak = ((xs - cx) ** 2 + (ys - cy) ** 2).sqrt()
    maks = (cx ** 2 + cy ** 2).sqrt()
    topeng = (1.0 - kuat * (jarak / (maks * rad)).clamp(0, 1)).clamp(0, 1)
    return x * topeng


def downscale(x, rng, skala_min=0.5, skala_maks=0.75):
    """Kecilkan lalu kembalikan — tekstur hilang, bentuk tidak.

    Satu faktor untuk seluruh batch: interpolate menuntut ukuran keluaran yang
    sama untuk semua, dan memecahnya per gambar justru mengembalikan biaya
    per-gambar yang mau dihindari batch.
    """
    torch = _torch()
    F = torch.nn.functional
    _, _, H, W = x.shape
    f = float(torch.empty(1).uniform_(skala_min, skala_maks))
    kecil = F.interpolate(x, size=(max(8, int(H * f)), max(8, int(W * f))),
                          mode="area")
    return F.interpolate(kecil, size=(H, W), mode="bilinear", align_corners=False)


def blur(x, rng, maks=5):
    """Blur kotak. Dipisah jadi dua lintasan 1D: k^2 kali lipat lebih murah."""
    torch = _torch()
    F = torch.nn.functional
    k = int(torch.randint(3, max(4, maks + 1), (1,)))
    k = k + 1 if k % 2 == 0 else k
    inti = torch.ones(3, 1, 1, k, device=x.device, dtype=x.dtype) / k
    y = F.conv2d(F.pad(x, (k // 2, k // 2, 0, 0), mode="reflect"), inti, groups=3)
    inti = inti.view(3, 1, k, 1)
    return F.conv2d(F.pad(y, (0, 0, k // 2, k // 2), mode="reflect"), inti, groups=3)


def grayscale(x, rng):
    """ToGray. Bobot BT.601 dalam urutan BGR, sama dengan cv2.COLOR_BGR2GRAY."""
    torch = _torch()
    b, g, r = x[:, 0:1], x[:, 1:2], x[:, 2:3]
    abu = 0.114 * b + 0.587 * g + 0.299 * r
    return abu.repeat(1, 3, 1, 1)


def iluminan(x, rng, gain=(0.65, 1.35)):
    """Lampu berwarna: satu gain per kanal, luminans rata-rata dijaga.

    Penjagaan luminans itu bukan hiasan. Tanpanya, gain jenuh yang kebetulan
    besar memotong kanal ke 255 dan objek jadi bidang polos tanpa tekstur —
    label yang menunjuk ke ruang kosong. Terukur di v14: 3,5% objek kehilangan
    tekstur sebelum penjagaan ini ada.
    """
    torch = _torch()
    B = x.shape[0]
    g = torch.empty(B, 3, 1, 1, device=x.device).uniform_(*gain)
    g = g / g.mean(dim=1, keepdim=True).clamp_min(1e-6)
    return (x * g).clamp_(0, 255)


# Nama -> fungsi.
#
# SATU ATURAN yang berlaku untuk seluruh isi daftar ini, dan bisa diperiksa
# mesin: tidak satu pun menggeser piksel, jadi LABELNYA TIDAK PERNAH BERUBAH.
# Itu yang membuat jalur GPU tidak perlu memindahkan keypoint sama sekali.
#
# Geometri -- rotasi, affine, crop acak, flip -- sengaja TIDAK di sini dan
# tetap dikerjakan albumentations di CPU. Terukur, geometri cuma 9,8% dari
# waktu augmentasi sementara yang fotometrik 90,2%; menukar 9,8% dengan risiko
# label meleset diam-diam bukan pertukaran yang masuk akal. Label yang meleset
# jauh lebih mahal daripada versi yang selesai 10% lebih lambat.
#
# Dipakai juga oleh tes untuk memeriksa bahwa TIAP operasi di sini benar-benar
# lebih cepat daripada padanannya di CPU.
OPERASI = {
    "terang_kontras": terang_kontras,
    "gamma": gamma,
    "derau_gauss": derau_gauss,
    "derau_iso": derau_iso,
    "vignette": vignette,
    "downscale": downscale,
    "blur": blur,
    "grayscale": grayscale,
    "iluminan": iluminan,
}


def ke_gpu(img_bgr):
    """Gambar HWC uint8 -> tensor (1,3,H,W) float32 di GPU."""
    torch = _torch()
    t = torch.from_numpy(img_bgr).cuda(non_blocking=True)
    return t.permute(2, 0, 1).unsqueeze(0).float()


def ke_cpu(x):
    """Kebalikan ke_gpu, dengan pembulatan yang sama dengan jalur CPU."""
    import numpy as np
    y = x[0].permute(1, 2, 0).round_().clamp_(0, 255).to(_torch().uint8)
    return y.cpu().numpy().astype(np.uint8)


# ============================================================ pipeline hibrida
#
# Geometri di CPU, fotometrik di GPU. Bukan kompromi setengah hati melainkan
# pembagian yang mengikuti ukuran: 90,2% waktu augmentasi ada di operasi
# fotometrik, dan justru operasi geometrilah yang menggeser label.
#
# Urutannya SAMA dengan jalur CPU -- geometri dulu, baru fotometrik -- supaya
# hasilnya sebanding. Membalik urutan mengubah artinya: derau yang ditambahkan
# sebelum diputar akan ikut diputar dan diinterpolasi, sehingga butirannya
# melunak dan tidak lagi meniru derau sensor.

# Operasi yang ditangani GPU. Nama-nama ini dimatikan di pipeline CPU supaya
# tidak dikerjakan dua kali.
DIPEGANG_GPU = tuple(OPERASI)


def resep_cpu_saja(resep: dict) -> dict:
    """Salinan resep dengan operasi yang dipegang GPU DIMATIKAN.

    Dikembalikan sebagai salinan, bukan diubah di tempat: resep yang sama
    dipakai lagi untuk menulis manifes, dan resep yang berubah diam-diam
    membuat manifes berbohong tentang apa yang dijalankan.
    """
    keluar = dict(resep or {})
    aug = dict(keluar.get("aug") or {})
    for oid in DIPEGANG_GPU:
        par = dict(aug.get(oid) or {})
        par["aktif"] = False
        aug[oid] = par
    keluar["aug"] = aug
    return keluar


def par_gpu(resep: dict, katalog_aug: dict) -> dict:
    """{operasi: parameter} untuk yang dikerjakan GPU, lengkap dengan peluang.

    Parameternya dibaca dari KATALOG yANG SAMA dengan jalur CPU, bukan dari
    tetapan yang disalin ke sini. Menyalin angka berarti membuka kesempatan
    keduanya berbeda tanpa ada yang menyadari — dan angka v14 itu justru yang
    paling tidak boleh bergeser.
    """
    from . import olah as _olah

    aug = (resep or {}).get("aug") or {}
    keluar = {}
    for oid in DIPEGANG_GPU:
        spec = katalog_aug.get(oid, {}).get("param") or {}
        minta = aug.get(oid)
        if minta is not None and minta.get("aktif") is False:
            continue
        if minta is None and not katalog_aug.get(oid, {}).get("bawaan_aktif", True):
            continue
        # Bawaan katalog diisi LEBIH DULU, baru ditimpa yang diminta orang.
        # saring_par hanya membersihkan kunci yang benar-benar disebut; ia
        # tidak mengisi yang tidak disebut. Tanpa baris ini "p" ikut hilang,
        # dan p yang hilang dibaca sebagai 1,0 -- artinya SETIAP operasi kena
        # SETIAP gambar. Terukur: vignette yang seharusnya mengenai 25% gambar
        # mengenai seluruhnya, rata-rata piksel jatuh dari 106 ke 55, dan
        # ragamnya menyempit karena semua gambar diperlakukan sama.
        penuh = {k: s["bawaan"] for k, s in spec.items()}
        bersih = _olah.saring_par(spec, minta or {})
        # HANYA kunci yang dikenal katalog yang diteruskan. saring_par sengaja
        # meloloskan kunci asing (`sisi` dan `n_kelas` dipakai mesin dan tidak
        # ditawarkan ke orang), dan jalur CPU tidak terganggu karena ia
        # menyerahkan parameternya sebagai SATU dict -- fungsinya membaca kunci
        # yang ia perlukan dan mengabaikan sisanya. Jalur GPU membongkarnya
        # dengan **arg, jadi satu kunci asing saja menjatuhkannya:
        #     TypeError: gamma() got an unexpected keyword argument 'aktif'
        # Itu benar-benar terjadi, dan lebih buruk daripada kedengarannya --
        # galatnya muncul di dalam thread pembuatan versi, penanganannya di
        # router memanggil `log` yang tidak ada, dan hasilnya pembuatan versi
        # menggantung 180 detik tanpa satu pun keterangan. Dari luar itu
        # terbaca sebagai "GPU lambat", bukan sebagai crash.
        penuh.update({k: v for k, v in bersih.items() if k in spec})
        keluar[oid] = penuh
    return keluar


def jalankan_gpu(img_bgr, par: dict, rng):
    """Terapkan seluruh operasi fotometrik di GPU pada SATU gambar.

    Label tidak diserahkan dan tidak dikembalikan: tidak satu pun operasi di
    sini menggeser piksel, jadi label pemanggil tetap berlaku apa adanya.
    """
    if not par:
        return img_bgr
    x = ke_gpu(img_bgr)
    for oid, p in par.items():
        # Peluang diputuskan RNG pemanggil, bukan RNG torch: dengan begitu
        # operasi mana yang dipakai tetap ditentukan benih pekerjaan, sama
        # seperti di jalur CPU.
        if rng.random() >= float(p.get("p", 1.0)):
            continue
        arg = {k: v for k, v in p.items() if k != "p"}
        x = OPERASI[oid](x, rng, **arg)
    return ke_cpu(x)


# ---------------------------------------------------- gelombang kedua
#
# Dipilih dari pengukuran, bukan dari daftar keinginan. Biaya CPU dikalikan
# PELUANG dipakainya, karena operasi mahal yang jarang terpakai menyumbang
# sedikit sementara operasi murah yang hampir selalu terpakai menyumbang
# banyak:
#
#     hue_sat        2,42 ms x 0,80 = 1,94 ms
#     color_jitter   3,46 ms x 0,50 = 1,73 ms
#     bayangan       7,83 ms x 0,18 = 1,41 ms
#     blackbody      0,82 ms x 0,45 = 0,37 ms
#     eksposur       0,38 ms x 0,30 = 0,11 ms
#
# artefak_jpeg SENGAJA tidak ikut: pulang-pergi JPEG di GPU butuh decode, dan
# decode nvJPEG terukur 3,75 ms lawan 3,65 ms di CPU. Memindahkannya membuat
# operasi ini LEBIH LAMBAT — aturan yang sama yang membuat decode tidak pernah
# masuk daftar ini sejak awal.


def _ke_hsv(x):
    """BGR 0..255 -> (h 0..360, s 0..1, v 0..1). Rumus baku, bukan hampiran."""
    torch = _torch()
    b, g, r = x[:, 0] / 255, x[:, 1] / 255, x[:, 2] / 255
    maks, _ = x.max(dim=1)
    mins, _ = x.min(dim=1)
    maks, mins = maks / 255, mins / 255
    d = (maks - mins).clamp_min(1e-8)
    h = torch.zeros_like(maks)
    h = torch.where(maks == r, ((g - b) / d) % 6, h)
    h = torch.where(maks == g, (b - r) / d + 2, h)
    h = torch.where(maks == b, (r - g) / d + 4, h)
    h = (h * 60) % 360
    s = torch.where(maks > 0, (maks - mins) / maks.clamp_min(1e-8),
                    torch.zeros_like(maks))
    return h, s, maks


def _dari_hsv(h, s, v):
    """Kebalikan _ke_hsv -> BGR 0..255."""
    torch = _torch()
    c = v * s
    x2 = c * (1 - ((h / 60) % 2 - 1).abs())
    m = v - c
    z = torch.zeros_like(c)
    i = (h / 60).floor() % 6
    r = torch.where(i == 0, c, torch.where(i == 1, x2, torch.where(
        i == 2, z, torch.where(i == 3, z, torch.where(i == 4, x2, c)))))
    g = torch.where(i == 0, x2, torch.where(i == 1, c, torch.where(
        i == 2, c, torch.where(i == 3, x2, torch.where(i == 4, z, z)))))
    b = torch.where(i == 0, z, torch.where(i == 1, z, torch.where(
        i == 2, x2, torch.where(i == 3, c, torch.where(i == 4, c, x2)))))
    return torch.stack([b + m, g + m, r + m], dim=1).clamp_(0, 1) * 255


def hue_sat(x, rng, hue=50, sat=45, val=35):
    """HueSaturationValue. Satuannya sama dengan OpenCV: hue 0..180, sisanya
    0..255 — itu yang dipakai albumentations, dan angkanya dibaca dari katalog
    yang sama."""
    torch = _torch()
    B = x.shape[0]
    d = x.device
    dh = torch.empty(B, 1, 1, device=d).uniform_(-hue, hue) * 2.0   # 0..180 -> derajat
    ds = torch.empty(B, 1, 1, device=d).uniform_(-sat, sat) / 255.0
    dv = torch.empty(B, 1, 1, device=d).uniform_(-val, val) / 255.0
    h, s, v = _ke_hsv(x)
    return _dari_hsv((h + dh) % 360, (s + ds).clamp(0, 1), (v + dv).clamp(0, 1))


def saturasi(x, rng, sat=40):
    """Hanya saturasi, rona tidak digeser."""
    torch = _torch()
    ds = torch.empty(x.shape[0], 1, 1, device=x.device).uniform_(-sat, sat) / 255.0
    h, s, v = _ke_hsv(x)
    return _dari_hsv(h, (s + ds).clamp(0, 1), v)


def color_jitter(x, rng, terang=0.30, kontras=0.30, saturasi=0.45, hue=0.12):
    """ColorJitter: terang, kontras, saturasi, rona sekaligus."""
    torch = _torch()
    B = x.shape[0]
    d = x.device
    fb = torch.empty(B, 1, 1, 1, device=d).uniform_(1 - terang, 1 + terang)
    fc = torch.empty(B, 1, 1, 1, device=d).uniform_(1 - kontras, 1 + kontras)
    fs = torch.empty(B, 1, 1, device=d).uniform_(1 - saturasi, 1 + saturasi)
    fh = torch.empty(B, 1, 1, device=d).uniform_(-hue, hue) * 360.0
    y = (x * fb).clamp(0, 255)
    rata = y.mean(dim=(1, 2, 3), keepdim=True)
    y = ((y - rata) * fc + rata).clamp(0, 255)
    h, s, v = _ke_hsv(y)
    return _dari_hsv((h + fh) % 360, (s * fs).clamp(0, 1), v)


def blackbody(x, rng, kuat=(0.0, 1.0)):
    """PlanckianJitter mode blackbody: menggeser suhu warna sepanjang lokus
    benda hitam — hangat ke dingin, bukan rona sembarang."""
    torch = _torch()
    B = x.shape[0]
    d = x.device
    # Gain kanal untuk 3000K (hangat) sampai 15000K (dingin), didekati linear.
    t = torch.empty(B, 1, 1, 1, device=d).uniform_(*kuat)
    # Rentangnya lebar karena yang ditiru pergantian lampu dari sangat hangat
    # ke sangat dingin. Percobaan pertama memakai rentang sempit DAN
    # menormalkan luminansnya; hasilnya rata-rata pas tetapi RAGAMNYA cuma 26
    # lawan 36 di CPU -- warnanya bergeser jauh lebih sedikit daripada yang
    # dimaksud, dan itu tidak terlihat dari rata-rata sama sekali.
    r = 1.55 - 1.00 * t
    g = torch.ones_like(r)
    b = 0.55 + 1.00 * t
    gain = torch.cat([b, g, r], dim=1)            # BGR
    return (x * gain).clamp_(0, 255)


def eksposur(x, rng, kuat=0.25):
    """RandomToneCurve: kurva nada S, meniru over/under-exposure."""
    torch = _torch()
    B = x.shape[0]
    d = x.device
    a = torch.empty(B, 1, 1, 1, device=d).normal_(0, kuat).clamp(-0.9, 0.9)
    t = (x / 255.0).clamp(0, 1)
    # Kurva yang sama bentuknya dengan albumentations: t + a*t*(1-t)*(1-2t)
    return ((t + a * t * (1 - t) * (1 - 2 * t)).clamp(0, 1) * 255)


def bayangan(x, rng, jumlah=(1, 2), sisi=7):
    """RandomShadow: beberapa poligon acak digelapkan.

    Poligonnya dibuat sebagai topeng lewat uji setengah-bidang, bukan dengan
    menggambar di CPU lalu memindahkannya: memindahkan topeng per gambar
    mengembalikan biaya pulang-pergi yang justru mau dihindari.
    """
    torch = _torch()
    B, _, H, W = x.shape
    d = x.device
    ys = torch.arange(H, device=d, dtype=x.dtype).view(1, H, 1) / H
    xs = torch.arange(W, device=d, dtype=x.dtype).view(1, 1, W) / W
    topeng = torch.ones(B, H, W, device=d, dtype=x.dtype)
    for _ in range(int(torch.randint(jumlah[0], jumlah[1] + 1, (1,)))):
        # Satu bayangan = irisan tiga setengah-bidang acak: bentuknya poligon
        # cembung, cukup mirip bayangan benda di luar bingkai.
        # Pusat acak, lalu tiga setengah-bidang yang mengelilinginya. Ambangnya
        # kecil (0,10-0,30) karena LUAS-nya yang harus cocok, bukan cuma
        # gelapnya: terukur, albumentations menutupi 20,6% bingkai dengan sisa
        # terang 0,48. Percobaan pertama memakai 0,15-0,75 dan menutupi 83% --
        # gelapnya pas, luasnya empat kali lipat, dan seluruh gambar jadi
        # muram tanpa ada yang terlihat salah sepintas.
        px = torch.empty(B, 1, 1, device=d).uniform_(0.1, 0.9)
        py = torch.empty(B, 1, 1, device=d).uniform_(0.1, 0.9)
        p = torch.ones(B, H, W, device=d, dtype=x.dtype)
        for _ in range(3):
            a = torch.empty(B, 1, 1, device=d).uniform_(0, 6.2832)
            r0 = torch.empty(B, 1, 1, device=d).uniform_(0.04, 0.20)
            sisi_ = (xs - px) * a.cos() + (ys - py) * a.sin()
            p = p * (sisi_ < r0).to(x.dtype)
        gelap = torch.empty(B, 1, 1, device=d).uniform_(0.35, 0.75)
        topeng = topeng * (1 - p * (1 - gelap))
    return x * topeng.unsqueeze(1)


OPERASI.update({
    "hue_sat": hue_sat,
    "saturasi": saturasi,
    "color_jitter": color_jitter,
    "blackbody": blackbody,
    "eksposur": eksposur,
    "bayangan": bayangan,
})
DIPEGANG_GPU = tuple(OPERASI)


# ================================================== simpan JPEG lewat GPU
#
# Encode kena SETIAP gambar keluaran -- bukan cuma yang diaugmentasi -- jadi
# sekecil apa pun untungnya ia terkali jumlah berkas. Terukur 1,91 ms di CPU
# lawan 0,23 ms di GPU: 8,3x.
#
# decode TIDAK ikut, dan itu hasil ukur bukan kelalaian: nvJPEG terukur 3,75 ms
# lawan 3,65 ms di CPU. Satu-satunya operasi di berkas ini yang GPU KALAH.


# Skala mutu nvJPEG TIDAK sama dengan skala cv2. Terukur pada gambar yang sama:
#
#     cv2 q92   143 KB, beda 11,23 terhadap aslinya
#     GPU q92   295 KB, beda  4,40      -> 2,06x lebih besar
#     GPU q75   128 KB, beda 11,31      -> 0,90x, mutunya setara
#
# Mengoper angka mutu apa adanya berarti setiap berkas jadi DUA KALI LIPAT
# tanpa ada yang memintanya: pada sejuta gambar itu 261 GB menjadi sekitar
# 520 GB, dan tidak ada satu pun pesan yang menyebutkannya. Karena itu mutunya
# diterjemahkan, bukan diteruskan.
MUTU_CV2_KE_GPU = {92: 75}


def _mutu_gpu(mutu: int) -> int:
    """Mutu cv2 -> mutu nvJPEG yang menghasilkan berkas sepadan."""
    if mutu in MUTU_CV2_KE_GPU:
        return MUTU_CV2_KE_GPU[mutu]
    # Di luar titik yang diukur, dipakai pendekatan linear dari titik itu.
    # Lebih baik meleset sedikit daripada diam-diam melipatgandakan ukurannya.
    return max(1, min(100, round(mutu * 75 / 92)))


def simpan_jpeg(path, img_bgr, mutu: int) -> bool:
    """Tulis JPEG lewat GPU. False kalau tidak bisa — pemanggil pakai cv2.

    Mengembalikan False alih-alih melempar: satu gambar yang gagal di-encode
    GPU tidak boleh menjatuhkan pembuatan versi yang sudah berjalan berjam-jam,
    dan cv2 di sisi pemanggil sudah cukup sebagai jaring.
    """
    if not tersedia():
        return False
    try:
        import torch
        from torchvision.io import encode_jpeg

        t = torch.from_numpy(img_bgr).cuda(non_blocking=True)
        # encode_jpeg menuntut RGB; sumbernya BGR, jadi kanalnya dibalik di GPU
        # (murah) alih-alih di CPU sebelum dipindah.
        t = t.permute(2, 0, 1).flip(0).contiguous()
        buf = encode_jpeg(t, quality=_mutu_gpu(int(mutu)))
        with open(path, "wb") as f:
            f.write(buf.cpu().numpy().tobytes())
        return True
    except Exception as e:                      # noqa: BLE001
        # Dicatat SEKALI lalu jalur ini dimatikan: kalau encode GPU gagal pada
        # gambar pertama ia akan gagal pada seluruhnya, dan menulis puluhan
        # ribu baris log yang sama tidak menolong siapa pun.
        global _jpeg_mati
        if not _jpeg_mati:
            _jpeg_mati = True
            log.warning("encode JPEG di GPU gagal (%s) — memakai cv2 untuk "
                        "sisanya", e)
        return False


_jpeg_mati = False

