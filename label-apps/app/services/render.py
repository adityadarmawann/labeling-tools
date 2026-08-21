"""
Menggambar mask di atas gambar, lalu menyimpannya sebagai thumbnail.

Thumbnail di-cache per akun. Warna kelas diturunkan dari nama kelasnya, jadi
kelas yang sama selalu berwarna sama tanpa perlu tabel warna.
"""
from __future__ import annotations

import colorsys
from pathlib import Path

import cv2
import numpy as np

from .scanner import item_key

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


def thumb_path(sess, item: dict, side: int) -> Path | None:
    """Path thumbnail milik akun ini, dibuat kalau belum ada."""
    p = sess.thumbdir / f"{item_key(item)}_{side}.jpg"
    if not p.exists():
        im = render(item, side)
        if im is None:
            return None
        p.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(p), im, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    return p
