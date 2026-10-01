"""
Uji pemotongan video -> klip (Langkah 2), services/klip.py.

CPU-only lewat ffmpeg. Semua asert yang butuh ffmpeg digerbang di belakang
klip.siap_video(): di CI tanpa ffmpeg tesnya di-skip bersih, bukan gagal.

Yang dijaga: jumlah klip = durasi/setiap_detik (potongan ekor dibuang), tiap
klip berdurasi/ber-fps/berjumlah-frame dalam toleransi, ukuran keluaran persis
resep (letterbox, bukan crop/regang), klip cacat dikarantina ke _sumber/_bad/
(bukan dihapus), dan detect_ffmpeg memilih biner yang benar-benar punya encoder.
"""
from __future__ import annotations

import subprocess

import pytest

from app.services import klip

# Deteksi sekali; kalau gagal, seluruh modul di-skip (CI tanpa ffmpeg).
_SIAP, _ALASAN = klip.siap_video()
pytestmark = pytest.mark.skipif(not _SIAP, reason=f"ffmpeg tak siap: {_ALASAN}")


def _buat_sumber(path, detik=6, w=320, h=240, fps=30):
    """Video sumber pasti terbaca & berdurasi persis, dibuat via ffmpeg lavfi.

    _video_bytes (cv2 mp4v) kadang menulis metadata durasi yang kabur; testsrc
    memberi durasi/fps yang pasti, jadi jumlah klip bisa diperiksa tepat.
    """
    ffmpeg, _, enc, opts = klip.detect_ffmpeg()
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
           "-i", f"testsrc=duration={detik}:size={w}x{h}:rate={fps}",
           "-c:v", enc, *opts, "-pix_fmt", "yuv420p", "-an", "-y", str(path)]
    assert subprocess.run(cmd, capture_output=True, timeout=120).returncode == 0
    return path


# ------------------------------------------------------------ detect_ffmpeg

def test_detect_ffmpeg_pilih_biner_berfungsi():
    klip._lupakan_deteksi()
    ffmpeg, ffprobe, enc, opts = klip.detect_ffmpeg()
    # Encoder yang dipilih harus salah satu yang memang diketahui ada — di mesin
    # ini ffmpeg conda di PATH tanpa libx264, jadi deteksi WAJIB memilih biner
    # lain yang punya encoder, bukan percaya $PATH.
    assert enc in ("libx264", "libopenh264", "mpeg4")
    r = subprocess.run([ffmpeg, "-hide_banner", "-encoders"],
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0 and enc in r.stdout


# ------------------------------------------------------------ resep

def test_resep_sah_menjepit_nilai_liar():
    r = klip.resep_sah({"fps": -5, "ukuran": [223, 101],
                        "slowmo": {"aktif": True, "faktor": 999, "mode": "x"},
                        "potong": {"setiap_detik": 0, "mulai": -3}})
    assert r["fps"] == 0                      # <=0 jadi "ikut sumber"
    assert r["ukuran"] == [224, 102]          # dibulatkan ke genap
    assert r["slowmo"]["faktor"] == 8 and r["slowmo"]["mode"] == "blend"
    assert r["potong"]["setiap_detik"] >= 0.1 and r["potong"]["mulai"] == 0.0


# ------------------------------------------------------------ potong penuh

def test_potong_jumlah_dan_mutu_klip(tmp_path):
    proj = tmp_path / "proj"
    sumber = proj / "_sumber"
    sumber.mkdir(parents=True)
    _buat_sumber(sumber / "src.mp4", detik=6, w=320, h=240, fps=30)

    resep = {"fps": 25, "potong": {"setiap_detik": 2.0, "mulai": 0.0},
             "ukuran": [224, 224], "letterbox": True}
    hasil = klip.potong(sumber / "src.mp4", proj, resep, nama_batch="b1")

    # 6 detik / 2 detik = 3 klip utuh.
    assert hasil["ditulis"] == 3 and hasil["dikarantina"] == 0
    assert hasil["frame_per_klip"] == 50          # 25fps * 2.0s
    keluar = proj / "klip" / "b1"
    klips = sorted(keluar.glob("*.mp4"))
    assert len(klips) == 3

    for k in klips:
        info = klip.probe(k)
        assert abs(info["duration"] - 2.0) <= 0.12
        assert abs(info["fps"] - 25) <= 1
        assert info["w"] == 224 and info["h"] == 224   # letterbox ke ukuran
        if info["frames"] is not None:
            assert abs(info["frames"] - 50) <= 2


def test_potong_slowmo_memperbanyak_klip(tmp_path):
    # Slow-mo 2x menggandakan durasi efektif -> klip dua kali lebih banyak.
    proj = tmp_path / "proj"
    sumber = proj / "_sumber"
    sumber.mkdir(parents=True)
    _buat_sumber(sumber / "src.mp4", detik=3, w=320, h=240, fps=30)

    biasa = klip.potong(sumber / "src.mp4", proj, nama_batch="biasa",
                        resep={"fps": 25, "potong": {"setiap_detik": 1.0}})
    lambat = klip.potong(sumber / "src.mp4", proj, nama_batch="lambat",
                         resep={"fps": 25, "potong": {"setiap_detik": 1.0},
                                "slowmo": {"aktif": True, "faktor": 2,
                                           "mode": "blend"}})
    assert lambat["ditulis"] > biasa["ditulis"]


def test_video_terlalu_pendek_ditolak(tmp_path):
    proj = tmp_path / "proj"
    sumber = proj / "_sumber"
    sumber.mkdir(parents=True)
    _buat_sumber(sumber / "src.mp4", detik=1, w=160, h=120, fps=30)
    with pytest.raises(klip.KlipTolak):
        klip.potong(sumber / "src.mp4", proj,
                    resep={"potong": {"setiap_detik": 5.0}})


# ------------------------------------------------------------ verifikasi & karantina

def test_klip_sah_menolak_durasi_meleset(tmp_path):
    proj = tmp_path / "proj"
    sumber = proj / "_sumber"
    sumber.mkdir(parents=True)
    _buat_sumber(sumber / "src.mp4", detik=4, w=224, h=224, fps=25)
    hasil = klip.potong(sumber / "src.mp4", proj, nama_batch="b",
                        resep={"fps": 25, "potong": {"setiap_detik": 2.0}})
    k = proj / "klip" / "b" / hasil["nama"][0]
    _, ffprobe, _, _ = klip.detect_ffmpeg()
    assert klip._klip_sah(k, 2.0, 50, ffprobe) is True
    assert klip._klip_sah(k, 1.0, 25, ffprobe) is False      # durasi beda


def test_klip_cacat_dikarantina_ke_bad(tmp_path, monkeypatch):
    # Paksa verifikasi selalu gagal -> semua klip harus pindah ke _sumber/_bad/
    # (dipulihkan, tak dihapus), dan potong() memberi tahu tak ada yang lolos.
    proj = tmp_path / "proj"
    sumber = proj / "_sumber"
    sumber.mkdir(parents=True)
    _buat_sumber(sumber / "src.mp4", detik=4, w=224, h=224, fps=25)

    monkeypatch.setattr(klip, "_klip_sah", lambda *a, **k: False)
    with pytest.raises(klip.KlipTolak):
        klip.potong(sumber / "src.mp4", proj, nama_batch="b",
                    resep={"fps": 25, "potong": {"setiap_detik": 2.0}})

    bad = proj / "_sumber" / "_bad"
    assert bad.is_dir() and len(list(bad.glob("*.mp4"))) >= 1
    assert sumber.joinpath("src.mp4").exists()               # sumber utuh
