"""
Menggambar mask di atas gambar, lalu menyimpannya sebagai thumbnail.

Thumbnail di-cache per akun. Warna kelas diturunkan dari nama kelasnya, jadi
kelas yang sama selalu berwarna sama tanpa perlu tabel warna.
"""
from __future__ import annotations

import colorsys
import os
import threading
from pathlib import Path

import cv2
import numpy as np

from ..config import get_settings
from .scanner import item_key, kunci_isi

JPEG_QUALITY = 86
OVERLAY_ALPHA = 0.34


def hash_kelas(nama) -> int:
    """
    Hash nama kelas yang SAMA PERSIS dengan hashKode di label.js.

    `hash()` bawaan Python tidak bisa dipakai: untuk string ia diacak ulang
    setiap proses (PYTHONHASHSEED), sehingga warna sebuah kelas berubah tiap
    kali server dinyalakan ulang — dan tidak pernah sama dengan warna di kanvas,
    walau komentar di label.js selama ini menyatakan sebaliknya.
    """
    h = 0
    for ch in str(nama):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    if h >= 0x80000000:            # kembalikan ke rentang bertanda 32-bit
        h -= 0x100000000
    return abs(h)


def warna_kelas(nama) -> str:
    """Warna kelas sebagai string CSS, sama dengan `warna()` di kanvas."""
    return f"hsl({hash_kelas(nama) % 997 / 997 * 360:.0f}, 62%, 55%)"


def cls_color(key) -> tuple[int, int, int]:
    """Warna kelas sebagai RGB, untuk menggambar overlay thumbnail."""
    h = (hash_kelas(key) % 997) / 997.0
    # HSL 62%/55%, sama dengan yang dipakai kanvas — bukan HSV, supaya
    # warnanya benar-benar sama dan bukan sekadar bernuansa mirip.
    r, g, b = colorsys.hls_to_rgb(h, 0.55, 0.62)
    return int(r * 255), int(g * 255), int(b * 255)


def render(item: dict, side: int):
    """Gambar + mask ter-overlay, diskalakan supaya sisi terpanjang = side."""
    im = cv2.imread(str(item["img"]))
    if im is None:
        return None
    ov = im.copy()
    for s in item["shapes"]:
        col = cls_color(s["label"])[::-1]          # cv2 memakai BGR
        pts = s["pts"].astype(np.int32)
        tebal = max(2, int(min(im.shape[:2]) / 200))
        # Titik, garis, dan polyline tidak punya bagian dalam: mengisinya
        # menghasilkan bercak yang tidak ada di anotasinya.
        if s["type"] == "point":
            cv2.circle(im, tuple(pts[0]), max(3, tebal * 2), col, -1)
        elif s["type"] in ("line", "linestrip"):
            cv2.polylines(im, [pts], False, col, tebal)
        else:
            cv2.fillPoly(ov, [pts], col)
            cv2.polylines(im, [pts], True, col, tebal)
    im = cv2.addWeighted(ov, OVERLAY_ALPHA, im, 1 - OVERLAY_ALPHA, 0)
    h, w = im.shape[:2]
    sc = side / max(h, w)
    return cv2.resize(im, (max(1, int(w * sc)), max(1, int(h * sc))),
                      interpolation=cv2.INTER_AREA)


# Thumbnail dipakai BERSAMA semua akun, tidak lagi satu salinan per akun.
#
# Pikselnya memang sama untuk siapa pun — ia diturunkan dari gambar, anotasi,
# dan ukurannya saja, tidak ada satu pun bagian yang bergantung pada siapa
# yang melihat. Menghitungnya ulang per akun berarti mengerjakan pekerjaan
# yang sama berkali-kali: terukur 10,89 ms CPU per thumbnail, jadi sepuluh
# orang membuka grid 120 gambar yang sama menghabiskan 13,06 detik-CPU untuk
# hasil yang identik, padahal cukup 1,31.
#
# Ia sekaligus memperbaiki kesalahan yang selama ini tidak kelihatan: dengan
# cache per akun, drop_thumbs_for hanya pernah dipanggil pada sesi orang yang
# MENGEDIT. Kalau A memperbaiki anotasi, salinan milik B tidak pernah
# dibatalkan dan B terus melihat mask yang lama. Kunci yang diturunkan dari
# isi berkasnya membuat itu tidak bisa terjadi lagi — berkasnya berubah,
# kuncinya berubah, dan yang lama tidak pernah terbaca siapa pun.
#
# Berbagi folder tidak membuka akses apa pun: rute /thumb memanggil
# sess.find(path) lebih dulu, jadi orang hanya bisa meminta thumbnail gambar
# yang memang sudah terlihat olehnya. Yang dibagi hasil perhitungannya, bukan
# haknya.
FOLDER_BERSAMA = "_bersama"


def dir_bersama() -> Path:
    d = get_settings().thumb_root / FOLDER_BERSAMA
    d.mkdir(parents=True, exist_ok=True)
    return d


def nama_thumb(item: dict, side: int) -> str:
    """`<kunci path>_<kunci isi>_<sisi>.jpg`.

    Kunci path ditaruh di depan supaya berkas lama sebuah gambar masih bisa
    disapu dengan satu glob saat anotasinya berubah — tanpa itu, tiap suntingan
    meninggalkan thumbnail yatim yang menumpuk selama server hidup.
    """
    return f"{item_key(item)}_{kunci_isi(item)}_{side}.jpg"


def thumb_path(sess, item: dict, side: int) -> Path | None:
    """Path thumbnail, dibuat kalau belum ada. Dipakai bersama semua akun."""
    p = dir_bersama() / nama_thumb(item, side)
    if not p.exists():
        im = render(item, side)
        if im is None:
            return None
        # Ditulis ke nama sementara lalu dipindahkan: dua akun bisa meminta
        # thumbnail yang sama pada saat yang sama, dan yang kedua tidak boleh
        # membaca berkas yang baru separuh tertulis. os.replace atomik di
        # dalam satu filesystem.
        # Ekstensi .jpg DIPERTAHANKAN di nama sementara: cv2.imwrite memilih
        # formatnya dari ekstensi, dan ".tmp" membuatnya melempar cv2.error
        # alih-alih menulis apa pun.
        tmp = p.with_name(
            f"{p.stem}.{os.getpid()}.{threading.get_ident()}.tmp.jpg")
        cv2.imwrite(str(tmp), im, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        try:
            os.replace(tmp, p)
        except OSError:
            tmp.unlink(missing_ok=True)
            return None
    return p
