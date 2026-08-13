"""
Uji kesetaraan MobileSAM dengan AnyLabeling.

Nilai acuan di sini diperoleh dari perbandingan langsung terhadap
SegmentAnythingONNX milik AnyLabeling pada mesin ini: IoU mask 1.0000 dengan
nol piksel berbeda, untuk prompt kotak, prompt titik, dan titik negatif, pada
tiga rasio gambar berbeda.

Empat hal di bawah pernah salah dan masing-masing mengubah hasil labeling.
Uji ini yang menjaganya:

1. Skala koordinat prompt harus min(1024/W, 684/H), bukan 1024/sisi-terpanjang.
   Salah di sini membuat mask prompt titik pada gambar 640x488 meluber ke
   hampir seluruh gambar — IoU 0.017 terhadap hasil desktop.
2. Titik pengisi [0,0] berlabel -1 selalu ditambahkan, termasuk untuk prompt
   kotak. Tanpa itu prompt kotak menyimpang (IoU 0.949).
3. orig_im_size diisi ukuran kanvas (684,1024), bukan ukuran gambar asli.
   Salah di sini menggeser mask 14 px di sumbu Y.
4. epsilon approxPolyDP 0.001, sama seperti segment_anything.py.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from conftest import ROOT

pytestmark = pytest.mark.skipif(
    not (list((ROOT.parent / "models").glob("*encoder*.onnx"))
         and list((ROOT.parent / "models").glob("*decoder*.onnx"))),
    reason="berkas ONNX MobileSAM tidak ada di models/")


@pytest.fixture
def lingkaran(tmp_path):
    """Gambar 640x400 dengan lingkaran di (430,150) r=70 -> bbox (360,80,500,220)."""
    im = np.full((400, 640, 3), 30, np.uint8)
    cv2.circle(im, (430, 150), 70, (40, 40, 220), -1)
    p = tmp_path / "lingkaran.jpg"
    cv2.imwrite(str(p), im)
    return p


def test_setelan_sesuai_anylabeling():
    from app.services import autolabel as al
    assert al.SAM_INPUT == (684, 1024), "ukuran kanvas encoder AnyLabeling"
    assert al.EPSILON_ANYLABELING == 0.001, "epsilon approxPolyDP AnyLabeling"
    assert al.MODEL_DEFAULT == "mobilesam"


def test_skala_prompt_dari_matriks_warp(lingkaran):
    """Skala koordinat harus sama dengan skala warp, bukan 1024/sisi-terpanjang."""
    from app.services import autolabel as al
    sesi = al._mobilesam()
    _, matriks, hw = sesi.embed(cv2.imread(str(lingkaran)))
    H, W = hw
    assert matriks[0][0] == pytest.approx(min(1024 / W, 684 / H))
    # untuk 640x400 keduanya kebetulan sama; yang penting rumusnya benar
    im = np.zeros((488, 640, 3), np.uint8)
    p2 = lingkaran.parent / "lain.jpg"
    cv2.imwrite(str(p2), im)
    _, m2, hw2 = sesi.embed(cv2.imread(str(p2)))
    assert m2[0][0] == pytest.approx(684 / 488)
    assert m2[0][0] != pytest.approx(1024 / 640)


def test_kotak_dan_titik_menghasilkan_bbox_acuan(lingkaran):
    """Nilai acuan dari perbandingan langsung dengan AnyLabeling."""
    from app.services import autolabel as al
    al.kosongkan_cache()
    kotak = al.dari_kotak(lingkaran, 345, 65, 515, 235, model="mobilesam")
    titik = al.dari_titik(lingkaran, [[430, 150]], model="mobilesam")
    for nama, u in (("kotak", kotak), ("titik", titik)):
        assert u.bbox == (361, 81, 499, 219), f"{nama}: {u.bbox}"
        assert len(u.points) > 40, f"{nama}: poligon terlalu kasar, epsilon berubah?"


def test_mask_seukuran_gambar_dan_di_dalam_batas(lingkaran):
    from app.services import autolabel as al
    al.kosongkan_cache()
    u = al.dari_titik(lingkaran, [[430, 150]], model="mobilesam")
    xs = [p[0] for p in u.points]
    ys = [p[1] for p in u.points]
    assert 0 <= min(xs) and max(xs) <= 640
    assert 0 <= min(ys) and max(ys) <= 400


def test_titik_negatif_memangkas_mask(lingkaran):
    """Titik berlabel 0 harus mengecilkan mask, bukan menambah."""
    from app.services import autolabel as al
    al.kosongkan_cache()
    a = al.dari_titik(lingkaran, [[430, 150]], model="mobilesam")
    b = al.dari_titik(lingkaran, [[430, 150], [430, 205]], [1, 0], model="mobilesam")
    luas = lambda u: (u.bbox[2] - u.bbox[0]) * (u.bbox[3] - u.bbox[1])
    assert luas(b) <= luas(a)
