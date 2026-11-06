"""
Auto-label kolaboratif -- engine merge (langkah 1): IoU, penggabungan/dedup
lintas sumber dengan prioritas label + geometri, dan koersi ke jenis projek.
"""
from __future__ import annotations

from app.services.autolabel_gabung import Deteksi, _iou, gabung, ke_bentuk


def test_iou():
    assert _iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert _iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert 0.1 < _iou((0, 0, 10, 10), (5, 5, 15, 15)) < 0.2


def test_gabung_objek_sama_jadi_satu_label_prioritas_geometri():
    # Dua sumber menemukan objek yang sama (IoU tinggi). Label dari 'terlatih'
    # (prioritas), geometri poligon dari 'zeroshot' (SAM) -> kelas+bentuk berpadu.
    t = Deteksi(bbox=(10, 10, 50, 50), label="kaleng", skor=0.9, sumber="terlatih")
    z = Deteksi(bbox=(11, 11, 49, 49), label="can", skor=0.8, sumber="zeroshot",
                pts=[[10, 10], [50, 10], [50, 50], [10, 50]])
    out = gabung([z, t])
    assert len(out) == 1
    assert out[0].label == "kaleng"               # label dari terlatih
    assert out[0].pts is not None                 # geometri poligon dari zeroshot
    assert "terlatih" in out[0].sumber and "zeroshot" in out[0].sumber


def test_gabung_objek_beda_union():
    a = Deteksi(bbox=(0, 0, 20, 20), label="botol", skor=0.9, sumber="terlatih")
    b = Deteksi(bbox=(100, 100, 140, 140), label="can", skor=0.7, sumber="zeroshot")
    out = gabung([a, b])
    assert len(out) == 2                          # objek beda -> keduanya disimpan


def test_gabung_zeroshot_menutup_yang_terlewat():
    # Model terlatih cuma menemukan 1; zeroshot menemukan objek LAIN -> union 2.
    t = Deteksi(bbox=(0, 0, 20, 20), label="kaleng", skor=0.9, sumber="terlatih")
    z = Deteksi(bbox=(200, 200, 240, 260), label="carton", skor=0.6, sumber="zeroshot")
    assert len(gabung([t, z])) == 2


def test_ke_bentuk_kotak_dan_poligon():
    d = [Deteksi(bbox=(10, 10, 50, 50), label="botol", sumber="terlatih")]
    kot = ke_bentuk(d, "kotak", 100, 100)
    assert kot[0]["shape_type"] == "rectangle"
    assert kot[0]["points"] == [[10.0, 10.0], [50.0, 50.0]]

    # Poligon: pakai pts kalau ada.
    dp = [Deteksi(bbox=(10, 10, 50, 50), label="botol", sumber="zeroshot",
                  pts=[[10, 10], [50, 10], [50, 50], [10, 50]])]
    pol = ke_bentuk(dp, "poligon", 100, 100)
    assert pol[0]["shape_type"] == "polygon" and len(pol[0]["points"]) >= 4

    # Poligon tapi hanya kotak -> diubah jadi poligon 4 titik (perkiraan).
    pol2 = ke_bentuk(d, "poligon", 100, 100)
    assert pol2[0]["shape_type"] == "polygon" and len(pol2[0]["points"]) >= 4


def test_ke_bentuk_kurung_ke_batas():
    d = [Deteksi(bbox=(-5, -5, 999, 999), label="x", sumber="terlatih")]
    b = ke_bentuk(d, "kotak", 80, 60)
    for x, y in b[0]["points"]:
        assert 0 <= x <= 80 and 0 <= y <= 60
