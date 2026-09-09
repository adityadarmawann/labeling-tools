"""
Mesin transformasi gambar + label untuk pembuatan versi dataset.

Dipisah tegas jadi dua tahap, dan pemisahan itu yang paling penting di berkas
ini:

  PRA  — deterministik, dikenakan ke SETIAP gambar di train, valid, dan test.
         Hasilnya harus sama persis tiap kali dijalankan, karena valid dan test
         adalah alat ukur: kalau isinya berubah tiap kali versi dibuat, angka
         mAP dua versi tidak bisa dibandingkan.
  AUG  — acak, HANYA train. Menghasilkan salinan tambahan, tidak pernah
         mengganti aslinya.

Sumber acuan parameter kelompok "default" adalah
`smartbin/sirsak/sirsak-v14/aug-bal-v14.py`. Angkanya disalin apa adanya —
kalau di sini berbeda, kelompok "default (v14)" itu bohong.

Tiga hal yang SENGAJA menyimpang dari v14, beserta alasannya:

  1. Letterbox v14 memakai pelat latar RVM yang dipilih acak (aug-bal-v14.py
     f:1111), jadi ia tidak deterministik. Di sini letterbox adalah PRA, dan
     PRA harus deterministik: warna isiannya tetap. Pelat acak tetap dipakai,
     tetapi hanya di tahap AUG (isian rotate/zoom/skala), tempat keacakan
     memang yang dimaksud.
  2. v14 hanya menyentuh `train/`. Pemisahan pra/aug di atas tidak ada
     bentuknya di sana; ini rancangan baru.
  3. Tiga saklar v14 — `kamera_downscale`, `cahaya_vignette`,
     `kamera_artefak_jpeg` — tidak pernah menghasilkan berkas apa pun pada
     setelan bawaannya (PHASE5_VARIANTS_PER_RESULT=1 membuat n_extra=0 di
     f:1434). Di sini ketiganya ADA dan benar-benar bekerja, karena jumlah
     varian per hasil bisa diatur; kalau tidak, saklarnya berbohong.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

import cv2
import numpy as np

from ..log import catat

log = catat("labelapp.olah")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LATAR_DIR = DATA_DIR / "latar-rvm"

# --------------------------------------------------------------- tetapan v14
# Angka-angka ini disalin dari aug-bal-v14.py. Jangan "dirapikan".
TARGET_SIZE = 640                      # f:413
WARNA_LATAR_CADANGAN = (118, 112, 122)  # f:799, median pelat RVM
MIN_AREA_RATIO = 0.30                  # f:515
MIN_POLY_POINTS = 8                    # f:516
MIN_STD_OBJEK = 12.0                   # f:442
RENTANG_MEAN_OBJEK = (25.0, 235.0)     # f:443
MAKS_ULANG_AUG = 3                     # f:444
TEMPEL_LEMBUT = 5                      # f:435


# ============================================================== label: baca/tulis
#
# Bentuknya sengaja sama dengan v14: satu label = (kelas, [x1,y1,x2,y2,...])
# dengan koordinat TERNORMALKAN 0..1. Bbox 5-kolom YOLO diubah jadi poligon 4
# titik supaya seluruh mesin ini hanya perlu mengenal satu bentuk.

def baca_label(p: Path) -> list[tuple[int, list[float]]]:
    """Berkas .txt YOLO -> [(kelas, poligon ternormalkan)]. Berkas kosong -> []."""
    if not p.exists():
        return []
    out = []
    for baris in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        bagian = baris.split()
        if len(bagian) < 5:
            continue
        try:
            c = int(float(bagian[0]))
            nilai = [float(x) for x in bagian[1:]]
        except ValueError:
            continue
        if len(nilai) == 4:
            cx, cy, bw, bh = nilai
            x1, y1 = cx - bw / 2, cy - bh / 2
            x2, y2 = cx + bw / 2, cy + bh / 2
            nilai = [x1, y1, x2, y1, x2, y2, x1, y2]
        if len(nilai) < 6 or len(nilai) % 2:
            continue
        out.append((c, nilai))
    return out


def tulis_label(p: Path, label: list[tuple[int, list[float]]], segmentasi: bool) -> None:
    """
    Tulis .txt YOLO. Berkas TETAP DIBUAT walau labelnya kosong — berkas kosong
    itulah yang menandai sampel negatif, dan tidak adanya berkas berarti
    "belum dilabeli". Dua hal yang berbeda.
    """
    baris = []
    for c, poli in label:
        if segmentasi:
            baris.append(str(c) + " " + " ".join(f"{v:.6f}" for v in poli))
        else:
            xs, ys = poli[0::2], poli[1::2]
            x1, x2 = min(xs), max(xs)
            y1, y2 = min(ys), max(ys)
            baris.append(f"{c} {(x1+x2)/2:.6f} {(y1+y2)/2:.6f} {x2-x1:.6f} {y2-y1:.6f}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(baris) + ("\n" if baris else ""), encoding="utf-8")


def luas_poligon(poli: list[float]) -> float:
    """Luas ternormalkan lewat rumus shoelace (aug-bal-v14.py:1522)."""
    xs = np.asarray(poli[0::2], dtype=np.float64)
    ys = np.asarray(poli[1::2], dtype=np.float64)
    if len(xs) < 3:
        return 0.0
    return float(abs(np.dot(xs, np.roll(ys, -1)) - np.dot(ys, np.roll(xs, -1))) / 2.0)


def _potong_ke_bingkai(poli: list[float], kotak=(0.0, 0.0, 1.0, 1.0)):
    """
    Potong poligon ke sebuah kotak, memakai shapely persis seperti
    clip_polygon_norm v14 (f:1023-1077): make_valid -> intersection -> ambil
    bagian terbesar kalau pecah -> tolak kalau sisa areanya di bawah ambang.

    Mengembalikan None kalau poligonnya harus dibuang.
    """
    from shapely.geometry import Polygon, box
    from shapely.validation import make_valid

    titik = list(zip(poli[0::2], poli[1::2]))
    if len(titik) < 3:
        return None
    try:
        g = make_valid(Polygon(titik))
    except Exception:
        return None
    if g.is_empty:
        return None
    luas_awal = g.area
    if luas_awal <= 0:
        return None
    try:
        g = g.intersection(box(*kotak))
    except Exception:
        return None
    if g.is_empty:
        return None
    if g.geom_type == "MultiPolygon":
        g = max(g.geoms, key=lambda q: q.area)
    if g.geom_type != "Polygon" or g.is_empty:
        return None
    if g.area / luas_awal < MIN_AREA_RATIO:
        return None
    koor = list(g.exterior.coords)[:-1]
    # v14 membuang poligon hasil clipping yang titiknya < 8 (f:516, f:1070).
    # Aturan itu benar untuk maksudnya — menangkap poligon rinci yang tercabik
    # jadi puntung — tetapi salah kalau diterapkan apa adanya di sini: label
    # kotak diubah jadi poligon 4 titik oleh baca_label(), dan seluruh dataset
    # deteksi akan lenyap tanpa satu pun pesan. Ambangnya karena itu dibatasi
    # oleh jumlah titik ASLINYA: bentuk yang memang sederhana sejak awal tidak
    # pernah dibuang hanya karena kesederhanaannya.
    batas = min(MIN_POLY_POINTS, len(titik))
    if len(koor) < batas:
        return None
    keluar = []
    for x, y in koor:
        keluar.extend((float(min(max(x, 0.0), 1.0)), float(min(max(y, 0.0), 1.0))))
    return keluar


def saring_label(label, kotak=(0.0, 0.0, 1.0, 1.0)):
    """Potong seluruh label ke kotak, buang yang tidak lolos."""
    out = []
    for c, poli in label:
        baru = _potong_ke_bingkai(poli, kotak)
        if baru:
            out.append((c, baru))
    return out


# ================================================================ pelat latar
#
# Dimuat sekali per proses dan disimpan di memori: 9 pelat 640x640 = ~11 MB
# terdekode. v14 menyimpannya di _BG_CACHE tanpa batas (f:918); di sini
# jumlahnya tetap karena pelatnya ikut dibundel bersama aplikasi.

_pelat: list[np.ndarray] = []
_warna_latar: tuple[int, int, int] | None = None


def muat_pelat() -> list[np.ndarray]:
    global _pelat, _warna_latar
    if _pelat:
        return _pelat
    kumpul = []
    for p in sorted(LATAR_DIR.glob("*.png")):
        im = cv2.imread(str(p))
        if im is not None:
            kumpul.append(im)
    _pelat = kumpul
    if kumpul:
        semua = np.concatenate([im.reshape(-1, 3) for im in kumpul], axis=0)
        _warna_latar = tuple(int(v) for v in np.median(semua, axis=0))
    else:
        _warna_latar = WARNA_LATAR_CADANGAN
    return _pelat


def warna_latar() -> tuple[int, int, int]:
    """Median warna seluruh pelat RVM — dipakai sebagai isian tepi (v14 f:925)."""
    if _warna_latar is None:
        muat_pelat()
    return _warna_latar or WARNA_LATAR_CADANGAN


def kanvas_latar(w: int, h: int, rng: random.Random) -> np.ndarray:
    """
    Kanvas berisi potongan pelat RVM acak. HANYA untuk tahap augmentasi —
    pemakaian di preprocessing membuat hasilnya tidak deterministik.
    """
    pelat = muat_pelat()
    if not pelat:
        return np.full((h, w, 3), warna_latar(), np.uint8)
    src = pelat[rng.randrange(len(pelat))]
    sh, sw = src.shape[:2]
    if sh < h or sw < w:
        s = max(h / sh, w / sw)
        src = cv2.resize(src, (int(math.ceil(sw * s)), int(math.ceil(sh * s))),
                         interpolation=cv2.INTER_LINEAR)
        sh, sw = src.shape[:2]
    oy = rng.randrange(sh - h + 1)
    ox = rng.randrange(sw - w + 1)
    return src[oy:oy + h, ox:ox + w].copy()


def kanvas_polos(w: int, h: int, warna=None) -> np.ndarray:
    """Kanvas warna tetap. Inilah yang dipakai preprocessing."""
    return np.full((h, w, 3), warna or warna_latar(), np.uint8)


def warna_bgr(nilai) -> tuple[int, int, int] | None:
    """
    '#rrggbb' atau (b, g, r) -> (b, g, r). None berarti "ikut pelat RVM".

    Peramban hanya bisa mengirim heks lewat <input type="color">, sedangkan
    OpenCV bekerja dalam BGR. Penerjemahnya duduk di sini, satu tempat, supaya
    tidak ada yang menebak urutan kanal di tempat lain.
    """
    if nilai is None:
        return None
    if isinstance(nilai, str):
        teks = nilai.strip().lstrip("#")
        if len(teks) != 6:
            return None
        try:
            r, g, b = (int(teks[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return None
        return (b, g, r)
    try:
        b, g, r = (int(v) for v in nilai)
    except (TypeError, ValueError):
        return None
    return tuple(min(255, max(0, v)) for v in (b, g, r))


def hex_dari_bgr(warna) -> str:
    """(b, g, r) -> '#rrggbb', untuk ditawarkan ke pemilih warna peramban."""
    b, g, r = (int(min(255, max(0, v))) for v in warna)
    return f"#{r:02x}{g:02x}{b:02x}"


# =========================================================== operasi PRA
#
# Tanda tangan seragam: f(img, label, par) -> (img, label) atau None.
# None berarti gambar ini dibuang dari versi sama sekali.
# `label` selalu daftar (kelas, poligon ternormalkan 0..1).

def _pra_periksa_label(img, label, par):
    """
    Buang label tak sah dan jepit koordinat ke dalam bingkai.
    Padanan periksa_label() v14 (f:1920-1990), tetapi tanpa menulis balik ke
    berkas sumber — di sini sumber tidak pernah disentuh.
    """
    nk = int(par.get("n_kelas") or 0)
    out = []
    for c, poli in label:
        if nk and not (0 <= c < nk):
            continue
        if len(poli) < 6 or len(poli) % 2:
            continue
        jepit = [min(max(v, 0.0), 1.0) for v in poli]
        if luas_poligon(jepit) <= 0:
            continue
        out.append((c, jepit))
    return img, out


# EXIF orientation 1..8. Kodenya menyatakan bagaimana piksel TERSIMPAN harus
# diubah supaya tampil tegak. Kebalikan tiap kode dipakai saat saklarnya mati:
# 6 (putar 90 searah jarum) dibatalkan oleh 8, sedangkan 2, 3, 4, 5, dan 7
# membatalkan dirinya sendiri.
INVERS_EXIF = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 8, 7: 7, 8: 6}


def putar_exif_gambar(img, kode: int):
    """Piksel tersimpan -> piksel tegak, menurut kode EXIF orientation."""
    if kode == 2:
        return cv2.flip(img, 1)
    if kode == 3:
        return cv2.flip(img, -1)
    if kode == 4:
        return cv2.flip(img, 0)
    if kode == 5:
        return cv2.transpose(img)
    if kode == 6:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if kode == 7:
        return cv2.flip(cv2.transpose(img), -1)
    if kode == 8:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return img


def putar_exif_titik(poli: list[float], kode: int) -> list[float]:
    """
    Poligon TERNORMALKAN mengikuti putaran yang sama dengan gambarnya.

    Ternormalkan, jadi tidak perlu tahu ukuran gambarnya sama sekali — dan
    itu yang membuat pemetaannya bisa dibalik tanpa membawa dimensi ke
    mana-mana.
    """
    keluar = []
    for i in range(0, len(poli), 2):
        x, y = poli[i], poli[i + 1]
        if kode == 2:
            u, v = 1.0 - x, y
        elif kode == 3:
            u, v = 1.0 - x, 1.0 - y
        elif kode == 4:
            u, v = x, 1.0 - y
        elif kode == 5:
            u, v = y, x
        elif kode == 6:
            u, v = 1.0 - y, x
        elif kode == 7:
            u, v = 1.0 - y, 1.0 - x
        elif kode == 8:
            u, v = y, 1.0 - x
        else:
            u, v = x, y
        keluar.extend((u, v))
    return keluar


def _pra_auto_orient(img, label, par):
    """
    Tegakkan gambar menurut EXIF orientation, lalu buang metadatanya.

    Gambarnya dibaca MENTAH (cv2.IMREAD_IGNORE_ORIENTATION) dan labelnya ikut
    dibawa ke ruang mentah, jadi operasi ini benar-benar yang menegakkan
    keduanya. Sebelumnya cv2.imread menegakkan sendiri lebih dulu dan fungsi
    ini tidak mengerjakan apa pun: saklarnya ada di layar tetapi mematikannya
    tidak mengubah satu piksel pun.

    Mematikannya sekarang berarti gambar keluar apa adanya seperti tersimpan
    — miring kalau memang begitu tersimpannya — dan labelnya ikut miring
    bersamanya, sehingga datasetnya tetap sah, hanya tidak ditegakkan.
    """
    kode = int(par.get("orientasi") or 1)
    if kode == 1:
        return img, label
    return (putar_exif_gambar(img, kode),
            [(c, putar_exif_titik(poli, kode)) for c, poli in label])


def _pra_resize(img, label, par):
    """
    Tiga mode, sama seperti Roboflow:
      fit    — muat seluruh gambar, sisanya diisi warna tetap (letterbox v14)
      regang — tarik ke ukuran sasaran, rasio berubah
      isi    — isi penuh lalu potong tengah, rasio tetap tetapi tepi hilang
    """
    W = int(par.get("lebar") or TARGET_SIZE)
    H = int(par.get("tinggi") or TARGET_SIZE)
    mode = par.get("mode") or "fit"
    h, w = img.shape[:2]
    if w <= 0 or h <= 0:
        return None

    if mode == "regang":
        keluar = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
        return keluar, label            # koordinat ternormalkan tidak berubah

    if mode == "isi":
        s = max(W / w, H / h)
        nw, nh = int(round(w * s)), int(round(h * s))
        kecil = cv2.resize(img, (nw, nh),
                           interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
        ox, oy = (nw - W) // 2, (nh - H) // 2
        keluar = kecil[oy:oy + H, ox:ox + W]
        baru = []
        for c, poli in label:
            p = []
            for i in range(0, len(poli), 2):
                p.append((poli[i] * nw - ox) / W)
                p.append((poli[i + 1] * nh - oy) / H)
            baru.append((c, p))
        return keluar, saring_label(baru)

    # fit / letterbox — v14 Fase 0, tetapi warna isiannya TETAP.
    s = min(W / w, H / h)
    nw, nh = max(1, int(round(w * s))), max(1, int(round(h * s)))
    kecil = cv2.resize(img, (nw, nh),
                       interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    keluar = kanvas_polos(W, H, warna_bgr(par.get("warna")))
    pl, pt = (W - nw) // 2, (H - nh) // 2
    keluar[pt:pt + nh, pl:pl + nw] = kecil
    baru = []
    for c, poli in label:
        p = []
        for i in range(0, len(poli), 2):
            p.append((poli[i] * nw + pl) / W)
            p.append((poli[i + 1] * nh + pt) / H)
        baru.append((c, p))
    return keluar, baru                 # tidak ada yang keluar bingkai


def _pra_grayscale(img, label, par):
    abu = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.cvtColor(abu, cv2.COLOR_GRAY2BGR), label


def _pra_auto_kontras(img, label, par):
    """
    CLAHE pada kanal L (LAB), bukan peregangan histogram global: foto RVM
    sering punya satu sudut sangat terang, dan peregangan global memakai sudut
    itu sebagai titik putih lalu meratakan seluruh sisanya.
    """
    klip = float(par.get("klip") or 2.0)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=klip, tileGridSize=(8, 8)).apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR), label


def _pra_potong_tetap(img, label, par):
    """Static crop: kotak tetap dalam persen, sama untuk setiap gambar."""
    x1 = float(par.get("x1", 0)) / 100.0
    y1 = float(par.get("y1", 0)) / 100.0
    x2 = float(par.get("x2", 100)) / 100.0
    y2 = float(par.get("y2", 100)) / 100.0
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        return img, label
    h, w = img.shape[:2]
    px1, py1 = int(x1 * w), int(y1 * h)
    px2, py2 = int(x2 * w), int(y2 * h)
    if px2 - px1 < 8 or py2 - py1 < 8:
        return img, label
    keluar = img[py1:py2, px1:px2]
    nw, nh = px2 - px1, py2 - py1
    baru = []
    for c, poli in label:
        p = []
        for i in range(0, len(poli), 2):
            p.append((poli[i] * w - px1) / nw)
            p.append((poli[i + 1] * h - py1) / nh)
        baru.append((c, p))
    return keluar, saring_label(baru)


def _pra_buang_kosong(img, label, par):
    """
    Buang gambar tanpa objek.

    Bawaannya MATI, dan itu disengaja: di projek ini gambar berlabel kosong
    adalah SAMPEL NEGATIF yang sengaja dibuat — kemasan yang harus ditolak
    mesin RVM. Menyalakannya membuang justru data yang paling mahal
    dikumpulkan.
    """
    return None if not label else (img, label)


def _pra_ubah_kelas(img, label, par):
    """
    Ganti nama, gabungkan, atau buang kelas.
    `peta` = {indeks_lama: indeks_baru}, indeks_baru None berarti dibuang.
    """
    peta = par.get("peta") or {}
    if not peta:
        return img, label
    out = []
    for c, poli in label:
        baru = peta.get(str(c), peta.get(c, c))
        if baru is None:
            continue
        out.append((int(baru), poli))
    return img, out


# =========================================================== iluminan v14
#
# Disalin dari aug-bal-v14.py f:598-634. Lampu berwarna tidak "menggeser hue"
# secara seragam; ia MENGALIKAN tiap kanal, sehingga objek terang dan objek
# gelap terpengaruh berbeda — itu yang tidak bisa ditiru HueSaturationValue.
# Ruang detektor RVM berlampu ungu/magenta, di luar lokus blackbody, jadi
# PlanckianJitter saja tidak cukup.

ILLUM_GAIN_RANGE = (0.65, 1.35)     # f:494
ILLUM_PROB = 0.70                   # f:495
ILLUM_JENUH_PROB = 0.35             # f:496
ILLUM_JENUH_KUAT = (0.30, 0.85)     # f:497
FISHEYE_K1_RANGE = (0.08, 0.20)     # f:464
FISHEYE_PROB = 0.35                 # f:465
FISHEYE_MAX_R = 0.80                # f:479


def gain_dari_rona(hue_deg: float, kuat: float):
    warna = cv2.cvtColor(np.uint8([[[int(hue_deg / 2) % 180, 255, 255]]]),
                         cv2.COLOR_HSV2BGR)[0, 0].astype(np.float32) / 255.0
    warna = warna / max(warna.max(), 1e-6)
    return (1.0 - kuat) + kuat * warna


def iluminan_acak(img, **_):
    if np.random.random() < ILLUM_JENUH_PROB:
        g = gain_dari_rona(np.random.uniform(0.0, 360.0),
                           np.random.uniform(*ILLUM_JENUH_KUAT))
    else:
        g = np.random.uniform(ILLUM_GAIN_RANGE[0], ILLUM_GAIN_RANGE[1], 3)
    # Luminans rata-rata dijaga: yang berubah hanya keseimbangan warna, bukan
    # tingkat terang. Tanpa normalisasi ini, gain jenuh yang kebetulan besar
    # memotong kanal ke 255 dan objek jadi bidang polos tanpa tekstur — label
    # yang menunjuk ke ruang kosong. Terukur di v14: 3,5% objek kehilangan
    # tekstur sebelum perbaikan ini.
    g = np.asarray(g, np.float32)
    g = g / max(float(g.mean()), 1e-6) * np.random.uniform(0.90, 1.10)
    return np.clip(img.astype(np.float32) * g.reshape(1, 1, 3), 0, 255).astype(np.uint8)


# ------------------------------------------------------------ fisheye v14
# Tidak bisa jadi A.Lambda: ia memindahkan piksel, jadi poligonnya harus ikut
# dipetakan. Peta mundur untuk piksel (cv2.remap), Newton per titik untuk label.

_peta_fisheye: dict[tuple[int, int, float], tuple] = {}


def _fisheye_maps(w: int, h: int, k1: float):
    # Di-cache per (w, h, k1): v14 membangun ulang dua peta float32 640x640
    # setiap pemanggilan (f:648), dan pada puluhan ribu gambar itu jadi
    # pekerjaan terbesar kedua setelah JPEG.
    kunci = (w, h, round(k1, 4))
    jadi = _peta_fisheye.get(kunci)
    if jadi is not None:
        return jadi
    cx, cy = w / 2.0, h / 2.0
    R = math.sqrt(cx * cx + cy * cy)
    ys, xs = np.indices((h, w), dtype=np.float32)
    dx, dy = (xs - cx) / R, (ys - cy) / R
    f = 1.0 + k1 * (dx * dx + dy * dy)
    jadi = ((cx + dx * f * R).astype(np.float32), (cy + dy * f * R).astype(np.float32))
    if len(_peta_fisheye) < 64:
        _peta_fisheye[kunci] = jadi
    return jadi


def _fisheye_titik(x, y, w, h, k1):
    cx, cy = w / 2.0, h / 2.0
    R = math.sqrt(cx * cx + cy * cy)
    dx, dy = (x - cx) / R, (y - cy) / R
    rs = math.hypot(dx, dy)
    if rs < 1e-9:
        return x, y
    ro = rs
    for _ in range(25):
        fx = ro + k1 * ro ** 3 - rs
        fp = 1.0 + 3.0 * k1 * ro * ro
        if abs(fp) < 1e-12:
            break
        step = fx / fp
        ro -= step
        if abs(step) < 1e-10:
            break
    if ro <= 1e-9:
        return x, y
    s = ro / rs
    return cx + dx * s * R, cy + dy * s * R


def radius_maks_objek(label) -> float:
    rm = 0.0
    for _, poli in label:
        for i in range(0, len(poli), 2):
            dx, dy = poli[i] - 0.5, poli[i + 1] - 0.5
            rm = max(rm, math.hypot(dx, dy) / math.hypot(0.5, 0.5))
    return rm


def terapkan_fisheye(img, label, k1=None, rng=None):
    """
    Lengkungkan gambar beserta poligonnya.

    Dilewati kalau objeknya terlalu besar atau terlalu ke tepi (FISHEYE_MAX_R):
    di ruang detektor objek selalu kecil dan dekat pusat-bawah, dan
    melengkungkan objek yang memenuhi frame menghasilkan bentuk yang tidak
    pernah ada di produksi.
    """
    if label and radius_maks_objek(label) > FISHEYE_MAX_R:
        return img, label, False
    rng = rng or random
    if k1 is None:
        k1 = rng.uniform(*FISHEYE_K1_RANGE)
    h, w = img.shape[:2]
    mx, my = _fisheye_maps(w, h, k1)
    keluar = cv2.remap(img, mx, my, interpolation=cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_REPLICATE)
    baru = []
    for c, poli in label:
        koor = []
        for i in range(0, len(poli), 2):
            nx, ny = _fisheye_titik(poli[i] * w, poli[i + 1] * h, w, h, k1)
            koor.extend((nx / w, ny / h))
        baru.append((c, koor))
    return keluar, saring_label(baru), True


# ------------------------------------------------- penjaga keterbacaan objek
def objek_terbaca(img_bgr, label) -> bool:
    """
    Tolak hasil augmentasi yang objeknya jadi bidang polos.

    v14 f:716-745. Ambangnya std gray < 12 di dalam masker poligon; sebelum
    penjaga ini ada, 3,5% objek kehilangan seluruh teksturnya dan labelnya
    menunjuk ke ruang kosong.
    """
    if not label:
        return True
    h, w = img_bgr.shape[:2]
    abu = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    for _, poli in label:
        titik = np.array([[poli[i] * w, poli[i + 1] * h]
                          for i in range(0, len(poli), 2)], np.int32)
        if len(titik) < 3:
            continue
        masker = np.zeros((h, w), np.uint8)
        cv2.fillPoly(masker, [titik], 255)
        piksel = abu[masker > 0]
        if piksel.size < 16:
            continue
        if float(piksel.std()) < MIN_STD_OBJEK:
            return False
        rata = float(piksel.mean())
        if not (RENTANG_MEAN_OBJEK[0] <= rata <= RENTANG_MEAN_OBJEK[1]):
            return False
    return True


# ==================================================== pipeline augmentasi
#
# Enam belas transform Fase 1 v14 (f:802-871), masing-masing jadi saklar
# tersendiri supaya bisa dimatikan satu-satu dari popup. Parameternya disalin
# apa adanya; kalau berbeda, kelompok "default (v14)" itu bohong.
#
# Urutan daftar = urutan eksekusi. Jangan diurutkan ulang: warna diacak
# SEBELUM blur dan derau, supaya deraunya tidak ikut tergeser warnanya.

def _A():
    import albumentations as A
    return A


AUG_V14 = [
    # (id, kelompok saklar v14, pembangun)
    ("flip_h",      "geo_flip_horizontal",   lambda A, p: A.HorizontalFlip(p=p.get("p", 0.5))),
    ("flip_v",      "geo_flip_vertikal",     lambda A, p: A.VerticalFlip(p=p.get("p", 0.3))),
    ("rotasi",      "geo_rotasi",            lambda A, p: A.Rotate(
        limit=p.get("derajat", 25), p=p.get("p", 0.7),
        border_mode=cv2.BORDER_CONSTANT, fill=warna_latar())),
    ("terang_kontras", "cahaya_terang_kontras", lambda A, p: A.RandomBrightnessContrast(
        brightness_limit=(p.get("terang_min", -0.45), p.get("terang_maks", 0.35)),
        contrast_limit=(p.get("kontras_min", -0.30), p.get("kontras_maks", 0.30)),
        p=p.get("p", 0.85))),
    ("gamma",       "cahaya_gamma",          lambda A, p: A.RandomGamma(
        gamma_limit=(p.get("min", 60), p.get("maks", 150)), p=p.get("p", 0.40))),
    ("blackbody",   "warna_blackbody",       lambda A, p: A.PlanckianJitter(
        mode="blackbody", p=p.get("p", 0.45))),
    ("iluminan",    "warna_iluminan_berwarna", lambda A, p: A.Lambda(
        image=iluminan_acak, p=p.get("p", ILLUM_PROB))),
    ("hue_sat",     "warna_hue_saturasi",    lambda A, p: A.HueSaturationValue(
        hue_shift_limit=p.get("hue", 50), sat_shift_limit=p.get("sat", 45),
        val_shift_limit=p.get("val", 35), p=p.get("p", 0.80))),
    ("color_jitter", "warna_color_jitter",   lambda A, p: A.ColorJitter(
        brightness=p.get("terang", 0.30), contrast=p.get("kontras", 0.30),
        saturation=p.get("saturasi", 0.45), hue=p.get("hue", 0.12),
        p=p.get("p", 0.50))),
    ("grayscale",   "warna_grayscale",       lambda A, p: A.ToGray(p=p.get("p", 0.12))),
    ("bayangan",    "cahaya_bayangan",       lambda A, p: A.RandomShadow(
        shadow_roi=(0, 0, 1, 1), num_shadows_limit=(1, 2),
        shadow_dimension=7, p=p.get("p", 0.18))),
    ("blur",        "kamera_blur",           lambda A, p: A.Blur(
        blur_limit=(3, p.get("maks", 5)), p=p.get("p", 0.25))),
    ("derau_gauss", "kamera_derau_gauss",    lambda A, p: A.GaussNoise(
        std_range=(p.get("min", 0.02), p.get("maks", 0.10)), p=p.get("p", 0.35))),
    ("derau_iso",   "kamera_derau_iso",      lambda A, p: A.ISONoise(
        color_shift=(0.01, 0.05), intensity=(0.1, 0.5), p=p.get("p", 0.20))),
    ("crop_acak",   "geo_crop_acak",         lambda A, p: A.RandomResizedCrop(
        size=p.get("sisi_hw") or (TARGET_SIZE, TARGET_SIZE),
        scale=(p.get("skala_min", 0.80), 1.0), ratio=(0.9, 1.1), p=p.get("p", 0.4))),
    ("affine",      "geo_affine",            lambda A, p: A.Affine(
        scale=(p.get("skala_min", 0.72), p.get("skala_maks", 1.10)),
        translate_percent={"x": (-p.get("geser", 0.25), p.get("geser", 0.25)),
                           "y": (-p.get("geser", 0.25), p.get("geser", 0.25))},
        rotate=0, shear=0, p=p.get("p", 0.6),
        border_mode=cv2.BORDER_CONSTANT, fill=warna_latar())),
]

# Tambahan gaya Roboflow yang TIDAK ada di v14.
AUG_TAMBAHAN = [
    ("rotasi_90",   lambda A, p: A.RandomRotate90(p=p.get("p", 0.5))),
    ("shear",       lambda A, p: A.Affine(
        shear={"x": (-p.get("derajat", 10), p.get("derajat", 10)),
               "y": (-p.get("derajat", 10), p.get("derajat", 10))},
        p=p.get("p", 0.3), border_mode=cv2.BORDER_CONSTANT, fill=warna_latar())),
    ("cutout",      lambda A, p: A.CoarseDropout(
        num_holes_range=(1, p.get("jumlah", 8)),
        hole_height_range=(0.02, p.get("ukuran", 0.10)),
        hole_width_range=(0.02, p.get("ukuran", 0.10)), p=p.get("p", 0.3))),
    ("eksposur",    lambda A, p: A.RandomToneCurve(
        scale=p.get("kuat", 0.25), p=p.get("p", 0.3))),
    ("saturasi",    lambda A, p: A.HueSaturationValue(
        hue_shift_limit=0, sat_shift_limit=p.get("sat", 40),
        val_shift_limit=0, p=p.get("p", 0.3))),
]

_TAMBAHAN = dict(AUG_TAMBAHAN)


def ukuran_keluaran(resep: dict) -> tuple[int, int] | None:
    """
    (H, W) yang dihasilkan preprocessing, atau None kalau Resize dimatikan.

    Dipakai crop_acak, yang harus mengeluarkan gambar seukuran gambar
    masuknya. Tanpa ini ia memakai TARGET_SIZE mati: dataset yang
    dipreprocessing ke 416 mendapat sebagian train 640 sementara valid dan
    test tetap 416, dan tidak ada satu pun pesan yang menyebutnya.
    """
    par = ((resep or {}).get("pra") or {}).get("resize")
    aktif = (KATALOG_PRA["resize"]["bawaan_aktif"] if par is None
             else par.get("aktif", True))
    if not aktif:
        return None
    par = par or {}
    return (int(par.get("tinggi") or TARGET_SIZE),
            int(par.get("lebar") or TARGET_SIZE))


def ada_aug(resep: dict) -> bool:
    """
    Apakah resep menyalakan setidaknya satu transform augmentasi?

    Menjawab dengan aturan yang sama persis dengan bangun_pipeline, tetapi
    TANPA mengimpor albumentations. Dibutuhkan sejak pipeline disusun per
    gambar: yang memutuskan sebuah fase berjalan atau tidak tidak lagi bisa
    menunggu sampai ada gambar untuk menyusunnya.
    """
    aug = (resep or {}).get("aug") or {}
    for oid, _saklar, _bangun in AUG_V14:
        par = aug.get(oid)
        if par is None or par.get("aktif", True):
            return True
    for oid, _bangun in AUG_TAMBAHAN:
        par = aug.get(oid)
        if par and par.get("aktif"):
            return True
    return False


def bangun_pipeline(resep: dict, sisi: tuple[int, int] | None = None):
    """
    Susun albumentations.Compose dari resep.

    `resep["aug"]` = {id: {aktif: bool, ...param}}. Yang tidak disebut memakai
    bawaan v14 dan dianggap AKTIF — sama seperti panel SAKLAR v14 yang seluruh
    bawaannya True.

    `sisi` (H, W) adalah ukuran keluaran yang harus dipatuhi crop_acak. Kalau
    tidak disebut, diambil dari langkah Resize di resepnya.
    """
    A = _A()
    aug = (resep or {}).get("aug") or {}
    if sisi is None:
        sisi = ukuran_keluaran(resep)
    urut = []
    for oid, _saklar, bangun in AUG_V14:
        par = aug.get(oid)
        if par is not None and not par.get("aktif", True):
            continue
        p = saring_par(KATALOG_AUG[oid]["param"], par or {})
        if oid == "crop_acak" and sisi:
            # Lewat parameter, bukan konstanta: satu-satunya yang boleh
            # menentukan ukuran keluaran adalah langkah Resize.
            p["sisi_hw"] = (int(sisi[0]), int(sisi[1]))
        urut.append(bangun(A, p))
    for oid, bangun in AUG_TAMBAHAN:
        par = aug.get(oid)
        if not par or not par.get("aktif"):
            continue                      # tambahan bawaannya MATI
        urut.append(bangun(A, saring_par(KATALOG_AUG[oid]["param"], par)))
    if not urut:
        return None
    return A.Compose(urut, keypoint_params=A.KeypointParams(
        format="xy", label_fields=["kp_label"], remove_invisible=False))


def augmentasi_sekali(img_bgr, label, pipeline, rng=None):
    """
    Satu percobaan augmentasi pada satu gambar.

    Poligon dibawa sebagai keypoint — sama seperti v14, yang tidak memakai
    bbox_params maupun mask. Mengembalikan (img, label) atau None kalau
    hasilnya harus dibuang.

    Gambar TANPA objek tetap diaugmentasi: itu sampel negatif, dan variasi
    lampu pada latar kosong justru yang mengajari model menolak ruang kosong.
    """
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    if not label:
        hasil = pipeline(image=rgb, keypoints=[], kp_label=[])
        return cv2.cvtColor(hasil["image"], cv2.COLOR_RGB2BGR), []

    h, w = rgb.shape[:2]
    titik, milik = [], []
    for i, (_c, poli) in enumerate(label):
        for j in range(0, len(poli), 2):
            titik.append((poli[j] * w, poli[j + 1] * h))
            milik.append(i)

    hasil = pipeline(image=rgb, keypoints=titik, kp_label=milik)
    out = hasil["image"]
    ah, aw = out.shape[:2]
    kumpul: dict[int, list[float]] = {}
    for (x, y), i in zip(hasil["keypoints"], hasil["kp_label"]):
        kumpul.setdefault(i, []).extend((x / aw, y / ah))

    baru = []
    for i, (c, _poli) in enumerate(label):
        koor = kumpul.get(i)
        if not koor or len(koor) < 6:
            continue
        baru.append((c, koor))
    baru = saring_label(baru)
    if not baru:
        return None
    return cv2.cvtColor(out, cv2.COLOR_RGB2BGR), baru


# ================================================================= katalog
#
# Satu-satunya sumber kebenaran untuk popup "Tambah langkah". Setiap operasi
# menyebut sendiri namanya, kelompoknya, dan parameternya, jadi menambah
# operasi baru cukup di sini — templat dan JS membacanya lewat /api/versi/katalog.
#
# kelompok "default" = berasal dari aug-bal-v14 dan menyala sejak awal.
# kelompok "tambahan" = gaya Roboflow yang belum ada di v14, bawaannya mati.

def _p(jenis, bawaan, **sisa):
    return {"jenis": jenis, "bawaan": bawaan, **sisa}


KATALOG_PRA = {
    "periksa_label": {
        "nama": "Periksa label", "kelompok": "default", "bawaan_aktif": True,
        "ket": "Buang label tak sah dan jepit koordinat ke dalam bingkai.",
        "param": {}, "fn": _pra_periksa_label,
        "ringkas": lambda p: "Dibersihkan",
    },
    "resize": {
        "nama": "Resize", "kelompok": "default", "bawaan_aktif": True,
        "ket": "Ubah ukuran semua gambar. Mode fit memuat gambar utuh lalu "
               "mengisi sisa tepinya dengan warna tetap.",
        "param": {
            "mode": _p("pilih", "fit", label="Cara memuat",
                       pilihan=[["fit", "Fit (isi tepi)"],
                                ["regang", "Regang"],
                                ["isi", "Isi lalu potong"]]),
            "lebar": _p("int", TARGET_SIZE, min=64, maks=2048, langkah=32,
                        label="Lebar", satuan=" px"),
            "tinggi": _p("int", TARGET_SIZE, min=64, maks=2048, langkah=32,
                         label="Tinggi", satuan=" px"),
            # Hanya berlaku pada mode fit — dua mode lain tidak menyisakan tepi
            # untuk diisi. Bawaannya None, artinya ikut median pelat RVM;
            # katalog_json menggantinya dengan warna itu dalam heks supaya yang
            # ditawarkan popup adalah warna yang benar-benar dipakai.
            "warna": _p("warna", None, label="Warna isian tepi (mode fit)"),
        },
        "fn": _pra_resize,
        "ringkas": lambda p: f"{p.get('mode','fit')} {p.get('lebar',640)}×{p.get('tinggi',640)}",
    },
    "auto_orient": {
        "nama": "Auto-Orient", "kelompok": "tambahan", "bawaan_aktif": True,
        "ket": "Tegakkan gambar menurut EXIF orientation lalu buang "
               "metadatanya. Dimatikan berarti piksel keluar apa adanya "
               "seperti tersimpan, dan labelnya ikut.",
        "param": {}, "fn": _pra_auto_orient,
        "ringkas": lambda p: "Diterapkan",
    },
    "auto_kontras": {
        "nama": "Auto-Adjust Contrast", "kelompok": "tambahan", "bawaan_aktif": False,
        "ket": "Naikkan kontras per bagian gambar. Lebih aman daripada "
               "peregangan menyeluruh kalau ada satu sudut yang sangat terang.",
        "param": {"klip": _p("float", 2.0, min=0.5, maks=8.0, langkah=0.1,
                             label="Batas kontras")},
        "fn": _pra_auto_kontras,
        "ringkas": lambda p: f"CLAHE klip {p.get('klip', 2.0)}",
    },
    "grayscale": {
        "nama": "Grayscale", "kelompok": "tambahan", "bawaan_aktif": False,
        "ket": "Buang warna dari semua gambar. Hati-hati: warna dipakai model "
               "ini sebagai petunjuk.",
        "param": {}, "fn": _pra_grayscale,
        "ringkas": lambda p: "Semua gambar",
    },
    "potong_tetap": {
        "nama": "Static Crop", "kelompok": "tambahan", "bawaan_aktif": False,
        "ket": "Potong kotak tetap dalam persen, sama untuk setiap gambar.",
        "param": {
            "x1": _p("int", 0, min=0, maks=99, label="Tepi kiri", satuan="%"),
            "y1": _p("int", 0, min=0, maks=99, label="Tepi atas", satuan="%"),
            "x2": _p("int", 100, min=1, maks=100, label="Tepi kanan", satuan="%"),
            "y2": _p("int", 100, min=1, maks=100, label="Tepi bawah", satuan="%"),
        },
        "fn": _pra_potong_tetap,
        "ringkas": lambda p: f"{p.get('x1',0)},{p.get('y1',0)} – {p.get('x2',100)},{p.get('y2',100)}%",
    },
    "buang_kosong": {
        "nama": "Filter Null", "kelompok": "tambahan", "bawaan_aktif": False,
        "ket": "Buang gambar tanpa objek. Sengaja mati: gambar berlabel kosong "
               "di sini adalah sampel negatif yang memang dikumpulkan.",
        "param": {}, "fn": _pra_buang_kosong,
        "ringkas": lambda p: "Gambar tanpa objek dibuang",
    },
    "ubah_kelas": {
        "nama": "Modify Classes", "kelompok": "tambahan", "bawaan_aktif": False,
        "ket": "Ganti nama, gabungkan, atau buang kelas tanpa menyentuh anotasi "
               "aslinya.",
        # `peta` menggabungkan/membuang (dikerjakan _pra_ubah_kelas, per label),
        # `nama` mengganti nama (dikerjakan buatversi saat menulis data.yaml).
        "param": {"peta": _p("peta_kelas", {}, label="Gabung atau buang"),
                  "nama": _p("nama_kelas", {}, label="Ganti nama")},
        "fn": _pra_ubah_kelas,
        "ringkas": lambda p: f"{len(p.get('peta') or {})} kelas diubah",
    },
}

# Urutan penerapan preprocessing. Tetap, tidak mengikuti urutan orang menambah:
# memotong sesudah resize berarti memotong piksel yang sudah dibuang.
URUT_PRA = ["periksa_label", "ubah_kelas", "buang_kosong", "auto_orient",
            "potong_tetap", "auto_kontras", "grayscale", "resize"]

_KET_AUG = {
    "flip_h": ("Flip horizontal", "Cermin kiri-kanan."),
    "flip_v": ("Flip vertikal", "Cermin atas-bawah."),
    "rotasi": ("Rotasi", "Putar acak. Tepinya diisi warna ruang detektor."),
    "terang_kontras": ("Terang & kontras", "Rentangnya sengaja lebar dan condong ke gelap."),
    "gamma": ("Gamma", "Melengkungkan respons terang, bukan menggesernya."),
    "blackbody": ("Suhu warna", "Cahaya hangat sampai dingin, seperti pergantian lampu."),
    "iluminan": ("Lampu berwarna", "Meniru lampu berwarna ruang detektor yang ungu atau magenta."),
    "hue_sat": ("Hue / saturasi / nilai", "Pengacakan warna kuat."),
    "color_jitter": ("Color jitter", "Guncangan warna gabungan."),
    "grayscale": ("Grayscale acak", "Sebagian kecil salinan dibuat abu-abu."),
    "bayangan": ("Bayangan", "Bayangan jatuh acak."),
    "blur": ("Blur", "Kabur ringan, meniru fokus meleset."),
    "derau_gauss": ("Derau Gauss", "Derau sensor."),
    "derau_iso": ("Derau ISO", "Derau berwarna pada ISO tinggi."),
    "crop_acak": ("Crop acak", "Potong lalu kembalikan ke ukuran semula."),
    "affine": ("Geser & skala", "Digeser dan diperbesar-kecilkan. Tepinya diisi warna ruang "
                    "detektor."),
}
_KET_TAMBAHAN = {
    "rotasi_90": ("Rotasi 90°", "Putar kelipatan 90°."),
    "shear": ("Shear", "Miringkan bidang gambar."),
    "cutout": ("Cutout", "Tutup beberapa petak acak supaya model tidak bergantung pada "
                    "satu bagian objek."),
    "eksposur": ("Eksposur", "Kurva nada, meniru over/under-exposure."),
    "saturasi": ("Saturasi", "Hanya saturasi, tanpa menggeser rona."),
}

# Angka yang boleh digeser orang. Kuncinya HARUS sama persis dengan yang
# dibaca pembangun transform di AUG_V14/AUG_TAMBAHAN, dan bawaannya HARUS sama
# dengan bawaan p.get() di sana: katalog inilah yang mengaku "bawaan dari
# v14", jadi kalau keduanya berbeda pengakuan itu bohong. tests/test_versi_par
# membandingkan keduanya baris demi baris supaya tidak bisa menyimpang diam-diam.
#
# `sisi` pada crop_acak sengaja TIDAK dibuka sebagai parameter yang bisa
# disetel orang: ia harus mengikuti ukuran resize, dan dua tempat mengatur
# ukuran keluaran adalah cara termudah menghasilkan dataset yang gambarnya
# tidak seragam. Yang menyalurkannya adalah bangun_pipeline lewat `sisi_hw`;
# sebelum itu ada, niat ini cuma tertulis di komentar dan crop_acak diam-diam
# memakai 640 pada dataset yang dipreprocessing ke ukuran lain.

# Peluang tiap transform dipakai pada satu salinan (albumentations `p=`).
_PELUANG = {
    "flip_h": 0.5, "flip_v": 0.3, "rotasi": 0.7, "terang_kontras": 0.85,
    "gamma": 0.40, "blackbody": 0.45, "iluminan": ILLUM_PROB, "hue_sat": 0.80,
    "color_jitter": 0.50, "grayscale": 0.12, "bayangan": 0.18, "blur": 0.25,
    "derau_gauss": 0.35, "derau_iso": 0.20, "crop_acak": 0.4, "affine": 0.6,
    "rotasi_90": 0.5, "shear": 0.3, "cutout": 0.3, "eksposur": 0.3,
    "saturasi": 0.3,
}

_PAR_AUG = {
    "rotasi": {"derajat": _p("int", 25, min=0, maks=180, langkah=1,
                             label="Sudut maksimum", satuan="\u00b0")},
    "terang_kontras": {
        "terang_min": _p("float", -0.45, min=-1.0, maks=0.0, langkah=0.01,
                         label="Paling gelap"),
        "terang_maks": _p("float", 0.35, min=0.0, maks=1.0, langkah=0.01,
                          label="Paling terang"),
        "kontras_min": _p("float", -0.30, min=-1.0, maks=0.0, langkah=0.01,
                          label="Kontras terendah"),
        "kontras_maks": _p("float", 0.30, min=0.0, maks=1.0, langkah=0.01,
                           label="Kontras tertinggi"),
    },
    "gamma": {"min": _p("int", 60, min=10, maks=100, langkah=1,
                        label="Gamma terendah"),
              "maks": _p("int", 150, min=100, maks=300, langkah=1,
                         label="Gamma tertinggi")},
    "hue_sat": {"hue": _p("int", 50, min=0, maks=100, langkah=1,
                          label="Geser rona"),
                "sat": _p("int", 45, min=0, maks=100, langkah=1,
                          label="Geser saturasi"),
                "val": _p("int", 35, min=0, maks=100, langkah=1,
                          label="Geser kecerahan")},
    "color_jitter": {"terang": _p("float", 0.30, min=0.0, maks=1.0, langkah=0.01,
                                  label="Terang"),
                     "kontras": _p("float", 0.30, min=0.0, maks=1.0, langkah=0.01,
                                   label="Kontras"),
                     "saturasi": _p("float", 0.45, min=0.0, maks=1.0, langkah=0.01,
                                    label="Saturasi"),
                     "hue": _p("float", 0.12, min=0.0, maks=0.5, langkah=0.01,
                               label="Rona")},
    "blur": {"maks": _p("int", 5, min=3, maks=15, langkah=2,
                        label="Radius maksimum", satuan=" px")},
    "derau_gauss": {"min": _p("float", 0.02, min=0.0, maks=0.5, langkah=0.01,
                              label="Derau paling lemah"),
                    "maks": _p("float", 0.10, min=0.01, maks=1.0, langkah=0.01,
                               label="Derau paling kuat")},
    "crop_acak": {"skala_min": _p("float", 0.80, min=0.1, maks=1.0, langkah=0.01,
                                  label="Sisa luas terkecil")},
    "affine": {"skala_min": _p("float", 0.72, min=0.1, maks=1.0, langkah=0.01,
                               label="Perbesaran terkecil"),
               "skala_maks": _p("float", 1.10, min=1.0, maks=2.0, langkah=0.01,
                                label="Perbesaran terbesar"),
               "geser": _p("float", 0.25, min=0.0, maks=0.9, langkah=0.01,
                           label="Geser maksimum")},
    "shear": {"derajat": _p("int", 10, min=1, maks=45, langkah=1,
                            label="Kemiringan", satuan="\u00b0")},
    "cutout": {"jumlah": _p("int", 8, min=1, maks=32, langkah=1,
                            label="Petak terbanyak"),
               "ukuran": _p("float", 0.10, min=0.02, maks=0.5, langkah=0.01,
                            label="Petak terbesar")},
    "eksposur": {"kuat": _p("float", 0.25, min=0.05, maks=1.0, langkah=0.01,
                            label="Kekuatan")},
    "saturasi": {"sat": _p("int", 40, min=0, maks=100, langkah=1,
                           label="Geser saturasi")},
}

# Pasangan yang tidak boleh terbalik. albumentations melempar ValueError kalau
# gamma_limit=(150, 60), dan orang yang menggeser batang bawah melewati batang
# atas tidak sedang meminta versinya gagal di tengah jalan.
PASANGAN_PAR = [("terang_min", "terang_maks"), ("kontras_min", "kontras_maks"),
                ("min", "maks"), ("skala_min", "skala_maks")]


def _par_aug(oid: str) -> dict:
    """Peluang selalu ada; sisanya menurut operasinya."""
    return {"p": _p("peluang", _PELUANG[oid], min=0.0, maks=1.0, langkah=0.01,
                    label="Peluang dipakai"),
            **_PAR_AUG.get(oid, {})}


def saring_par(spec: dict, par: dict) -> dict:
    """
    Jepit tiap angka ke rentang katalognya, lalu betulkan pasangan min/maks
    yang terbalik.

    Nilai yang tidak dikenal katalog dibiarkan lewat: `sisi` dan `n_kelas`
    dipakai mesin tetapi sengaja tidak ditawarkan ke orang.
    """
    keluar = dict(par or {})
    for kunci, s in (spec or {}).items():
        if kunci not in keluar:
            continue
        nilai = keluar[kunci]
        jenis = s.get("jenis")
        if jenis in ("int", "float", "peluang"):
            try:
                nilai = int(nilai) if jenis == "int" else float(nilai)
            except (TypeError, ValueError):
                keluar[kunci] = s["bawaan"]
                continue
            if "min" in s:
                nilai = max(s["min"], nilai)
            if "maks" in s:
                nilai = min(s["maks"], nilai)
            keluar[kunci] = nilai
        elif jenis == "warna":
            # Heks tak sah dikembalikan ke bawaan, bukan diteruskan ke
            # np.full — yang akan melempar di tengah versi yang sudah jalan.
            keluar[kunci] = nilai if warna_bgr(nilai) else s["bawaan"]
        elif jenis == "pilih":
            sah = [a for a, _ in s.get("pilihan", [])]
            if nilai not in sah:
                keluar[kunci] = s["bawaan"]
    for bawah, atas in PASANGAN_PAR:
        if bawah in keluar and atas in keluar:
            try:
                if keluar[bawah] > keluar[atas]:
                    keluar[bawah], keluar[atas] = keluar[atas], keluar[bawah]
            except TypeError:
                pass
    return keluar


KATALOG_AUG = {}
for _oid, _saklar, _b in AUG_V14:
    _nama, _ket = _KET_AUG[_oid]
    KATALOG_AUG[_oid] = {"nama": _nama, "kelompok": "default", "bawaan_aktif": True,
                         "ket": _ket, "saklar_v14": _saklar,
                         "param": _par_aug(_oid)}
for _oid, _b in AUG_TAMBAHAN:
    _nama, _ket = _KET_TAMBAHAN[_oid]
    KATALOG_AUG[_oid] = {"nama": _nama, "kelompok": "tambahan", "bawaan_aktif": False,
                         "ket": _ket, "param": _par_aug(_oid)}


def katalog_json() -> dict:
    """Katalog untuk popup, tanpa objek Python yang tidak bisa di-JSON-kan."""
    def bersih(d):
        keluar = {}
        for k, v in d.items():
            item = {kk: vv for kk, vv in v.items() if kk not in ("fn", "ringkas")}
            # param disalin, bukan dibagi: di bawah ada bawaan yang diisi saat
            # permintaan datang, dan menulisnya ke dict katalog akan mengubah
            # katalog seluruh proses.
            item["param"] = {pk: dict(pv) for pk, pv in (v.get("param") or {}).items()}
            keluar[k] = item
        return keluar
    pra = bersih(KATALOG_PRA)
    # Warna isian tepi bawaannya bukan angka tetap melainkan median pelat RVM,
    # dan itu baru diketahui setelah pelatnya dimuat. Popup harus menawarkan
    # warna yang BENAR-BENAR dipakai kalau tidak ada yang menyentuhnya.
    warna = pra.get("resize", {}).get("param", {}).get("warna")
    if warna is not None and warna.get("bawaan") is None:
        warna["bawaan"] = hex_dari_bgr(warna_latar())
    return {"pra": pra, "aug": bersih(KATALOG_AUG), "urut_pra": URUT_PRA}


def terapkan_pra(img, label, resep: dict, n_kelas: int = 0,
                 orientasi: int = 1):
    """
    Jalankan seluruh preprocessing yang aktif, berurutan tetap.
    Mengembalikan None kalau gambarnya harus dibuang dari versi.

    `orientasi` adalah kode EXIF gambar ini; hanya auto_orient yang memakainya,
    dan disalurkan lewat par dengan cara yang sama seperti n_kelas.
    """
    pra = (resep or {}).get("pra") or {}
    for oid in URUT_PRA:
        meta = KATALOG_PRA[oid]
        par = pra.get(oid)
        aktif = meta["bawaan_aktif"] if par is None else par.get("aktif", True)
        if not aktif:
            continue
        p = saring_par(meta["param"], par or {})
        p.setdefault("n_kelas", n_kelas)
        p.setdefault("orientasi", orientasi)
        hasil = meta["fn"](img, label, p)
        if hasil is None:
            return None
        img, label = hasil
    return img, label
