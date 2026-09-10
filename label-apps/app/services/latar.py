"""
Pelat latar ruang detektor RVM milik SATU projek.

Kenapa ini ada
--------------
Pengukuran v13: sesudah augmentasi, 50,3% piksel data latih adalah putih
polos — akibat letterbox, rotate dan kanvas zoom-out yang mengisi tepi dengan
putih. Model jadi belajar "objek selalu muncul di atas latar terang", padahal
produksi memberi "objek di ruang logam gelap melengkung berlampu ungu".
Buktinya telak: memindahkan objek yang sama ke latar putih membuat akurasi
v13 melonjak dari 0/7 jadi 3/7 — jadi penyebabnya latar, bukan objeknya.

Aplikasi ini membawa 9 pelat bawaan di app/data/latar-rvm. Pelat itu berasal
dari SATU ruang detektor, dan ruang itu belum tentu ruang yang dipakai orang
yang sedang melabeli. Modul ini yang membuat mereka bisa memasukkan foto ruang
sendiri.

Pelat projek DITAMBAHKAN ke pelat bawaan, tidak menggantikannya: latar yang
lebih beragam membuat model lebih sulit menghafal satu ruangan tertentu, dan
membuang pelat bawaan berarti dataset kecil kehilangan variasi yang sudah ada
tanpa mendapat gantinya.

Alurnya disalin dari prep-latar-rvm.py v14, berikut alasan tiap langkah:

  1. buang_bantalan  — foto dari RVM sering sudah di-letterbox, jadi ada pita
     polos di tepinya. Kalau pita itu ikut jadi pelat, kita justru menempelkan
     kembali bantalan polos yang sedang dihilangkan dari dataset.
  2. netralkan       — foto latar diambil saat lampu neopixel menyala pada
     satu warna. Kalau warna lampu itu tetap menempel, ia BERTUMPUK dengan
     warna lampu yang ditambahkan augmentasi: pelat ungu x lampu hijau =
     warna yang tidak pernah ada di RVM mana pun.
  3. ke_petak        — dipotong persegi di tiga posisi berbeda.
  4. varian terang & suhu — satu foto harus menghasilkan beberapa pelat
     berbeda. Kalau tidak, tiap gambar hasil augmentasi memakai latar yang
     identik dan model menghafal latar itu, bukan objeknya.

Satu foto menghasilkan 9 pelat (3 posisi x 3 tingkat terang).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import cv2
import numpy as np

from ..log import catat

log = catat("labelapp.latar")

# Sidecar bertitik, sama seperti .tugas.json dan .versi: folder bertitik tidak
# pernah ikut dipindai sebagai gambar dataset.
FOLDER = ".latar"
ASLI = "asli"          # foto yang diunggah orang, disimpan apa adanya
PELAT = "pelat"        # hasil olahan yang benar-benar dipakai augmentasi

SISI = 640
MAKS_FOTO = 3          # batas yang disepakati untuk formulirnya
GESERAN = ((0.5, 0.5), (0.15, 0.5), (0.85, 0.5))       # v14 f:222
TERANG = (0.70, 0.95, 1.20)                             # v14 linspace(0.70,1.20,3)
SUHU = ((1.00, 1.00, 1.00), (1.06, 1.00, 0.94), (0.94, 1.00, 1.06))   # v14 f:224
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp")


def folder(ds: Path) -> Path:
    return Path(ds) / FOLDER


def _rata(v: np.ndarray, toleransi: float = 6.0) -> bool:
    """True kalau baris/kolom itu warnanya seragam — pita bantalan."""
    return bool(v.std() < toleransi and (v.mean() > 235 or v.mean() < 20))


def buang_bantalan(im: np.ndarray, toleransi: float = 6.0):
    """Buang pita letterbox polos di tepi. -> (gambar, dipotong?)

    Contoh nyata dari v14: latar-rvm.jpg berisi pita PUTIH pada y 0-77 dan
    y 554-639; isi sebenarnya cuma y 78..553.
    """
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY).astype(np.float32)
    h, w = g.shape
    y0, y1 = 0, h - 1
    while y0 < y1 and _rata(g[y0], toleransi):
        y0 += 1
    while y1 > y0 and _rata(g[y1], toleransi):
        y1 -= 1
    x0, x1 = 0, w - 1
    while x0 < x1 and _rata(g[:, x0], toleransi):
        x0 += 1
    while x1 > x0 and _rata(g[:, x1], toleransi):
        x1 -= 1
    # Kalau yang tersisa terlalu kecil, yang terbaca bukan bantalan melainkan
    # foto yang memang polos. Dikembalikan utuh, bukan dipotong jadi seiris.
    if y1 - y0 < 32 or x1 - x0 < 32:
        return im, False
    dipotong = (y0 > 0 or x0 > 0 or y1 < h - 1 or x1 < w - 1)
    return im[y0:y1 + 1, x0:x1 + 1], dipotong


def netralkan(im: np.ndarray) -> np.ndarray:
    """Buang warna lampu dari pelat (gray-world), sisakan warna ruangannya."""
    f = im.astype(np.float32)
    mu = f.reshape(-1, 3).mean(0)
    mu[mu < 1e-6] = 1e-6
    return np.clip(f * (mu.mean() / mu), 0, 255).astype(np.uint8)


def ke_petak(im: np.ndarray, geser=(0.5, 0.5), sisi: int = SISI) -> np.ndarray:
    """Potong jadi persegi lalu skalakan — TANPA bantalan."""
    h, w = im.shape[:2]
    s = min(h, w)
    y0 = int(round((h - s) * min(max(geser[1], 0.0), 1.0)))
    x0 = int(round((w - s) * min(max(geser[0], 0.0), 1.0)))
    return cv2.resize(im[y0:y0 + s, x0:x0 + s], (sisi, sisi),
                      interpolation=cv2.INTER_AREA)


def buat_pelat(im: np.ndarray, netral: bool = True) -> list[np.ndarray]:
    """
    Satu foto ruang kosong -> 9 pelat (3 posisi x 3 tingkat terang).

    `netral=True` (bawaan, cara v14): warna lampu dibuang dan tiap pelat
    diberi sedikit variasi suhu warna. Ini yang membuat warna lampu yang
    ditambahkan augmentasi tidak bertumpuk dengan warna lampu yang terlanjur
    terekam di fotonya.

    `netral=False`: WARNANYA TIDAK DISENTUH sama sekali — tidak dinetralkan,
    tidak divariasikan suhunya. Yang berubah hanya terang-gelapnya, karena
    mengalikan ketiga kanal dengan angka yang sama tidak menggeser rona.
    Dipakai kalau lampu ruangannya memang selalu satu warna itu dan kamu
    ingin pelatnya persis seperti apa adanya.
    """
    im, _ = buang_bantalan(im)
    if netral:
        im = netralkan(im)
    keluar = []
    for gi, g in enumerate(GESERAN):
        petak = ke_petak(im, g)
        for i, f in enumerate(TERANG):
            out = np.clip(petak.astype(np.float32) * f, 0, 255).astype(np.uint8)
            if netral:
                gain = np.asarray(SUHU[(gi + i) % len(SUHU)], np.float32)
                out = np.clip(out.astype(np.float32) * gain.reshape(1, 1, 3),
                              0, 255).astype(np.uint8)
            keluar.append(out)
    return keluar


# ---------------------------------------------------------------- simpanan


def daftar_asli(ds: Path) -> list[Path]:
    d = folder(ds) / ASLI
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir()
                  if p.suffix.lower() in IMG_EXT and p.is_file())


def daftar_pelat(ds: Path) -> list[Path]:
    d = folder(ds) / PELAT
    if not d.is_dir():
        return []
    return sorted(d.glob("*.png"))


MODE = ("netral", "asli")


def mode_dari(nama: str) -> str:
    """Mode dibaca dari NAMA berkasnya: latar-1-netral.png / latar-1-asli.png.

    Disimpan di nama, bukan di berkas keterangan terpisah, supaya tidak ada
    yang bisa lepas sinkron: menghapus fotonya sudah menghapus modenya, dan
    tidak ada catatan yatim yang menyebut foto yang sudah tidak ada.
    """
    ekor = Path(nama).stem.rsplit("-", 1)[-1].lower()
    return ekor if ekor in MODE else "netral"


def bangun_ulang(ds: Path) -> int:
    """Buat ulang SELURUH pelat dari foto asli yang tersimpan. -> jumlah pelat.

    Dibangun ulang seluruhnya, bukan ditambal, supaya isi folder pelat selalu
    bisa diterangkan seluruhnya oleh foto yang ada: pelat yatim dari foto yang
    sudah dihapus akan tetap dipakai augmentasi tanpa satu pun jejak asalnya.
    """
    d = folder(ds) / PELAT
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    asli = daftar_asli(ds)
    if not asli:
        return 0
    d.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in asli:
        im = cv2.imread(str(p))
        if im is None:
            log.warning("foto latar tidak terbaca: %s", p.name)
            continue
        for i, pelat in enumerate(buat_pelat(im, netral=mode_dari(p.name) == "netral")):
            cv2.imwrite(str(d / f"{p.stem}_p{i:02d}.png"), pelat)
            n += 1
    log.info("%s pelat latar dibangun dari %s foto di %s", n, len(asli), ds.name)
    return n


def tambah(ds: Path, nama: str, data: bytes, mode: str = "netral") -> dict:
    """Simpan satu foto latar lalu bangun ulang pelatnya."""
    mode = mode if mode in MODE else "netral"
    asli = daftar_asli(ds)
    if len(asli) >= MAKS_FOTO:
        return {"ok": False,
                "error": f"sudah ada {len(asli)} foto latar, batasnya {MAKS_FOTO}"}
    ext = Path(nama).suffix.lower()
    if ext not in IMG_EXT:
        return {"ok": False, "error": "yang diminta berkas gambar"}
    im = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if im is None:
        return {"ok": False, "error": "gambarnya tidak terbaca"}
    if min(im.shape[:2]) < 64:
        return {"ok": False, "error": "gambarnya terlalu kecil (minimal 64 px)"}
    d = folder(ds) / ASLI
    d.mkdir(parents=True, exist_ok=True)
    # Nama berurut, bukan nama aslinya: nama dari peramban bisa memuat apa saja,
    # dan yang dipakai di sini cuma urutannya.
    urut = 1
    while any((d / f"latar-{urut}-{m}{ext}").exists() for m in MODE):
        urut += 1
    (d / f"latar-{urut}-{mode}{ext}").write_bytes(data)
    n = bangun_ulang(ds)
    return {"ok": True, "n_foto": len(daftar_asli(ds)), "n_pelat": n}


def buang(ds: Path, nama: str) -> dict:
    """Hapus satu foto latar lalu bangun ulang pelatnya."""
    for p in daftar_asli(ds):
        if p.name == nama:
            p.unlink(missing_ok=True)
            n = bangun_ulang(ds)
            return {"ok": True, "n_foto": len(daftar_asli(ds)), "n_pelat": n}
    return {"ok": False, "error": "foto latar itu tidak ada"}


def ringkas(ds: Path) -> dict:
    """Keadaan latar projek ini, untuk digambar formulirnya."""
    asli = daftar_asli(ds)
    return {"maks": MAKS_FOTO,
            "foto": [{"nama": p.name, "mode": mode_dari(p.name)} for p in asli],
            "n_pelat": len(daftar_pelat(ds))}
