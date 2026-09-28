"""Skor "kemungkinan foto katalog/web" untuk sebuah gambar.

Foto katalog/web (produk difoto di latar putih studio) berbeda dari capture RVM
asli: latarnya putih mulus. Itu satu-satunya sinyal yang SELAMAT setelah semua
gambar disusutkan ke 640 — derau sensor (pembeda katalog vs asli pada foto
full-res) sudah hilang rata karena penyusutan, jadi tidak bisa dipakai di sini.

Diukur dari KECERAHAN BINGKAI LUAR: berapa fraksi piksel tepi yang mendekati
putih. Divalidasi visual pada dataset botol-kaleng-tetra: skor >= 0.85 hampir
selalu produk di latar putih polos (kaleng/gelas/tisu katalog), sedangkan foto
asli 3D (di tangan/meja/lantai/chamber) jatuh di bawahnya. Ambang sengaja
KONSERVATIF: lebih baik menyisakan sedikit katalog daripada ikut membuang foto
asli yang bagus.

Dipakai version builder sebagai filter opsional — TIDAK menyentuh dataset:
skornya cuma dihitung dan di-cache di memori, tidak ditulis ke mana pun di
folder projek.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np

# Ambang bawaan: >= ini dianggap katalog. Diturunkan dari kalibrasi visual, dan
# sengaja tinggi supaya cuma menangkap yang JELAS latar putih polos.
AMBANG = 0.85

# Cache skor per gambar. Kuncinya (path, mtime_ns, size) supaya gambar yang
# diganti isinya otomatis dihitung ulang. Dibatasi agar tidak tumbuh tanpa
# henti kalau banyak projek dibuka dalam satu sesi.
_cache: "OrderedDict[tuple, float]" = OrderedDict()
_CACHE_MAKS = 200_000
_kunci = threading.Lock()


def _hitung(fp: Path) -> float | None:
    """Fraksi piksel bingkai luar yang mendekati putih (0..1), atau None kalau
    gambarnya tak terbaca. Dibaca pada 1/4 resolusi: 16x lebih cepat dan skornya
    praktis sama dengan full-res (beda rata 0,001 pada uji 1.500 gambar)."""
    im = cv2.imread(str(fp), cv2.IMREAD_REDUCED_COLOR_4)
    if im is None:
        return None
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    h, w = g.shape
    b = max(2, int(min(h, w) * 0.04))
    tepi = np.concatenate([g[:b].ravel(), g[-b:].ravel(),
                           g[:, :b].ravel(), g[:, -b:].ravel()])
    return float((tepi > 235).mean())


def skor(fp: Path) -> float | None:
    """Skor katalog satu gambar, lewat cache."""
    p = Path(fp)
    try:
        st = p.stat()
    except OSError:
        return None
    kunci = (str(p), st.st_mtime_ns, st.st_size)
    with _kunci:
        if kunci in _cache:
            _cache.move_to_end(kunci)
            return _cache[kunci]
    nilai = _hitung(p)
    if nilai is None:
        return None
    with _kunci:
        _cache[kunci] = nilai
        if len(_cache) > _CACHE_MAKS:
            _cache.popitem(last=False)
    return nilai


def adalah_katalog(fp: Path, ambang: float = AMBANG) -> bool:
    """True kalau gambarnya kemungkinan katalog. Gambar yang tak terbaca
    dianggap BUKAN katalog — lebih aman menyisakannya daripada membuang."""
    s = skor(fp)
    return s is not None and s >= ambang


def buang_katalog(items: list, *, ambang: float = AMBANG) -> tuple[list, int]:
    """Sisakan item yang BUKAN katalog. Kembalikan (tersisa, jumlah_dibuang).

    Item tanpa kunci "img" atau yang gambarnya tak terbaca selalu disisakan."""
    tersisa, dibuang = [], 0
    for it in items:
        img = it.get("img")
        if img is not None and adalah_katalog(img, ambang):
            dibuang += 1
            continue
        tersisa.append(it)
    return tersisa, dibuang
