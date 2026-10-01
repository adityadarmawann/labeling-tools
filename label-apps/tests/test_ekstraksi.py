"""
Uji ekstraksi frame video -> gambar untuk projek image.

Yang dijaga: jumlah frame mengikuti preset adaptif (video pendek rapat, panjang
jarang), frame buram & kembar memang terbuang, videonya sendiri tak tersentuh,
dan nama berkas tak bentrok saat dua video masuk ke projek yang sama.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.services import ekstraksi


def _tulis_video(path, frames, fps=30.0, ukuran=(320, 240)):
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         fps, ukuran)
    assert vw.isOpened(), "VideoWriter gagal dibuka"
    for f in frames:
        vw.write(f)
    vw.release()
    return path


def _frame_tajam(i, ukuran=(320, 240)):
    """Frame distinct + tajam: gradien + angka besar + kotak acak berbenih i."""
    w, h = ukuran
    g = np.tile(np.linspace(0, 255, w, dtype=np.uint8), (h, 1))
    f = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
    rng = np.random.default_rng(i)
    for _ in range(6):
        x, y = int(rng.integers(0, w - 40)), int(rng.integers(0, h - 40))
        c = tuple(int(v) for v in rng.integers(0, 255, 3))
        cv2.rectangle(f, (x, y), (x + 40, y + 40), c, -1)
    cv2.putText(f, str(i), (30, 170), cv2.FONT_HERSHEY_SIMPLEX, 4,
                (255, 255, 255), 6)
    return f


def _video_tajam(path, n=60, fps=30.0):
    return _tulis_video(path, [_frame_tajam(i) for i in range(n)], fps)


# ------------------------------------------------------------ target & preset

def test_sah_preset_jatuh_ke_bawaan():
    assert ekstraksi.sah_preset("rapat") == "rapat"
    assert ekstraksi.sah_preset("JARANG") == "jarang"
    assert ekstraksi.sah_preset("ngaco") == ekstraksi.PRESET_BAWAAN
    assert ekstraksi.sah_preset("") == ekstraksi.PRESET_BAWAAN


def test_target_mengikuti_contoh_kalibrasi():
    # Angka acuan dari docstring modul; toleransi kecil untuk pembulatan.
    assert ekstraksi.target_frame(2, "jarang") == pytest.approx(6, abs=2)
    assert ekstraksi.target_frame(2, "sedang") == pytest.approx(12, abs=2)
    assert ekstraksi.target_frame(2, "rapat") == pytest.approx(20, abs=3)
    assert ekstraksi.target_frame(60, "jarang") == pytest.approx(30, abs=4)
    assert ekstraksi.target_frame(60, "sedang") == pytest.approx(60, abs=6)
    assert ekstraksi.target_frame(60, "rapat") == pytest.approx(100, abs=8)


def test_target_monoton_dan_terbatas():
    for d in (0.5, 2, 10, 60, 600):
        assert (ekstraksi.target_frame(d, "jarang")
                <= ekstraksi.target_frame(d, "sedang")
                <= ekstraksi.target_frame(d, "rapat"))
    assert ekstraksi.target_frame(0.01, "jarang") == ekstraksi.MIN_FRAME
    assert ekstraksi.target_frame(0, "rapat") == ekstraksi.MIN_FRAME
    assert ekstraksi.target_frame(10 ** 6, "rapat") == ekstraksi.MAKS_FRAME


def test_indeks_kandidat_merata_dan_unik():
    idx = ekstraksi._indeks_kandidat(100, 10)
    assert idx[0] == 0 and idx[-1] == 99
    assert idx == sorted(set(idx))
    # n >= F mengembalikan semua frame.
    assert ekstraksi._indeks_kandidat(5, 10) == [0, 1, 2, 3, 4]


# ------------------------------------------------------------ ekstraksi penuh

def test_ekstrak_video_tajam_mencapai_target(tmp_path):
    vid = _video_tajam(tmp_path / "klip.mp4", n=60, fps=30.0)  # 2 detik
    keluar = tmp_path / "out"
    r = ekstraksi.ekstrak(vid, keluar, preset="sedang")

    target = ekstraksi.target_frame(2.0, "sedang")
    assert r["ditulis"] == target          # cukup frame bagus -> penuh ke target
    assert r["buram"] == 0 and r["duplikat"] == 0
    jpg = sorted(keluar.glob("*.jpg"))
    assert len(jpg) == target
    # Benar-benar gambar yang terbaca.
    assert cv2.imread(str(jpg[0])) is not None


def test_preset_rapat_lebih_banyak_dari_jarang(tmp_path):
    frames = [_frame_tajam(i) for i in range(90)]           # 3 detik @30
    _tulis_video(tmp_path / "v.mp4", frames)
    jr = ekstraksi.ekstrak(tmp_path / "v.mp4", tmp_path / "jr", preset="jarang")
    rp = ekstraksi.ekstrak(tmp_path / "v.mp4", tmp_path / "rp", preset="rapat")
    assert rp["ditulis"] > jr["ditulis"]


def test_frame_kembar_dibuang(tmp_path):
    # Satu frame diulang 60x: hanya satu yang layak disimpan, sisanya kembar.
    satu = _frame_tajam(7)
    _tulis_video(tmp_path / "diam.mp4", [satu] * 60)
    r = ekstraksi.ekstrak(tmp_path / "diam.mp4", tmp_path / "out", preset="sedang")
    assert r["ditulis"] == 1
    assert r["duplikat"] >= 1
    assert len(list((tmp_path / "out").glob("*.jpg"))) == 1


def test_video_blank_semua_terbuang_sebagai_buram(tmp_path):
    # Frame hitam polos: varian Laplacian ~0, semua di bawah lantai buram.
    blank = [np.zeros((240, 320, 3), np.uint8) for _ in range(60)]
    _tulis_video(tmp_path / "hitam.mp4", blank)
    with pytest.raises(ekstraksi.EkstrakTolak):
        ekstraksi.ekstrak(tmp_path / "hitam.mp4", tmp_path / "out")


def test_video_sumber_tak_tersentuh(tmp_path):
    vid = _video_tajam(tmp_path / "klip.mp4")
    sebelum = vid.read_bytes()
    ekstraksi.ekstrak(vid, tmp_path / "out", preset="jarang")
    assert vid.exists()
    assert vid.read_bytes() == sebelum      # penghapusan bukan urusan ekstrak()


def test_dua_video_ke_projek_sama_tak_bentrok(tmp_path):
    keluar = tmp_path / "out"
    _video_tajam(tmp_path / "a.mp4")
    _video_tajam(tmp_path / "b.mp4")
    r1 = ekstraksi.ekstrak(tmp_path / "a.mp4", keluar, prefix="klip")
    r2 = ekstraksi.ekstrak(tmp_path / "b.mp4", keluar, prefix="klip")
    semua = list(keluar.glob("*.jpg"))
    # Tidak ada yang tertimpa: jumlah berkas = gabungan keduanya.
    assert len(semua) == r1["ditulis"] + r2["ditulis"]
    assert len(set(p.name for p in semua)) == len(semua)


def test_bukan_video_ditolak(tmp_path):
    bukan = tmp_path / "teks.mp4"
    bukan.write_text("ini bukan video")
    with pytest.raises(ekstraksi.EkstrakTolak):
        ekstraksi.ekstrak(bukan, tmp_path / "out")
