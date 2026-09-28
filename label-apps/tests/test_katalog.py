"""Skor katalog: foto latar putih polos (web/katalog) dikenali dari kecerahan
bingkai luar, dipakai filter opsional di version builder."""
from __future__ import annotations

import cv2
import numpy as np

from app.services import katalog


def _tulis(d, nama, border, isi=90):
    """Gambar 200x200: bingkai luar `border`, tengah `isi`."""
    im = np.full((200, 200, 3), isi, np.uint8)
    b = 40
    im[:b] = border
    im[-b:] = border
    im[:, :b] = border
    im[:, -b:] = border
    p = d / nama
    cv2.imwrite(str(p), im)
    return p


def test_latar_putih_dikenali_katalog(tmp_path):
    putih = _tulis(tmp_path, "web.jpg", border=255)
    gelap = _tulis(tmp_path, "asli.jpg", border=45)
    assert katalog.adalah_katalog(putih) is True
    assert katalog.adalah_katalog(gelap) is False


def test_skor_dari_cache_konsisten(tmp_path):
    p = _tulis(tmp_path, "x.jpg", border=255)
    s1 = katalog.skor(p)
    s2 = katalog.skor(p)                 # kali kedua dari cache
    assert s1 == s2 and s1 >= katalog.AMBANG


def test_gambar_tak_terbaca_bukan_katalog(tmp_path):
    (tmp_path / "rusak.jpg").write_bytes(b"bukan gambar")
    assert katalog.adalah_katalog(tmp_path / "rusak.jpg") is False


def test_buang_katalog_menyisakan_yang_asli(tmp_path):
    a = _tulis(tmp_path, "web1.jpg", 255)
    b = _tulis(tmp_path, "web2.jpg", 255)
    c = _tulis(tmp_path, "asli1.jpg", 45)
    obj = [{"label": "botol"}]                        # ada objek
    items = [{"img": a, "shapes": obj}, {"img": b, "shapes": obj},
             {"img": c, "shapes": obj}]
    tersisa, dibuang = katalog.buang_katalog(items)
    assert dibuang == 2
    assert [it["img"].name for it in tersisa] == ["asli1.jpg"]


def test_sampel_negatif_berlatar_putih_tak_pernah_dibuang(tmp_path):
    """Latar putih + label kosong = sampel negatif DISENGAJA. Filter katalog
    tak boleh menyapunya walau bingkainya putih — ini yang paling penting."""
    neg = _tulis(tmp_path, "latar.jpg", 255)          # putih penuh, TANPA objek
    web = _tulis(tmp_path, "web.jpg", 255)            # putih penuh, ADA objek
    items = [{"img": neg, "shapes": []},              # negatif
             {"img": web, "shapes": [{"label": "botol"}]}]
    tersisa, dibuang = katalog.buang_katalog(items)
    assert dibuang == 1                                # hanya yang ber-objek
    assert [it["img"].name for it in tersisa] == ["latar.jpg"]
