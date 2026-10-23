"""scanner.dimensi() harus mengembalikan dimensi TEGAK (EXIF-oriented), sama
dengan yang dilihat peramban (auto-orient) dan cv2.imread (auto-orient).

Regresi bug: foto HP 4:3 ber-EXIF orientation 6 tampil gepeng di labeler dan
titik SAM meleset, karena dimensi() dulu memberi dims MENTAH sementara piksel
yang digambar peramban sudah tegak. Lihat _baca_item di buatversi: ia pun
memakai ruang TAMPIL (menukar sisi untuk kode 5-8)."""
from __future__ import annotations

from PIL import Image

from app.services import scanner


def _simpan(path, w, h, orient):
    im = Image.new("RGB", (w, h), "white")
    if orient:
        ex = im.getexif()
        ex[0x0112] = orient
        im.save(path, "JPEG", exif=ex)
    else:
        im.save(path, "JPEG")


def test_dimensi_orientasi_6_menukar_lebar_tinggi(tmp_path):
    # Mentah 400x300 (lanskap) + orientation 6 (putar 90) -> tampil 300x400 (potret).
    p = tmp_path / "hp.jpg"
    _simpan(p, 400, 300, 6)
    assert scanner.dimensi(p) == (400, 300)        # (H,W) tegak = 300 lebar x 400 tinggi


def test_dimensi_orientasi_8_menukar(tmp_path):
    p = tmp_path / "hp8.jpg"
    _simpan(p, 400, 300, 8)                          # putar 270
    assert scanner.dimensi(p) == (400, 300)


def test_dimensi_tanpa_orientasi_apa_adanya(tmp_path):
    # orientation 1 / tak ada -> dims mentah dipakai apa adanya (dataset Roboflow).
    p = tmp_path / "datar.jpg"
    _simpan(p, 400, 300, 1)
    assert scanner.dimensi(p) == (300, 400)          # (H,W) = 400 lebar x 300 tinggi


def test_dimensi_orientasi_3_tidak_menukar(tmp_path):
    # 180 derajat: dims TIDAK bertukar (cuma terbalik), jadi apa adanya.
    p = tmp_path / "putar180.jpg"
    _simpan(p, 400, 300, 3)
    assert scanner.dimensi(p) == (300, 400)
