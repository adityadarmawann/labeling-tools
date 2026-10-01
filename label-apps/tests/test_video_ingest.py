"""
Uji ingest video YouTube (Langkah 3) — TANPA jaringan, TANPA yt-dlp sungguhan.

Yang dijaga:
  - validasi host: youtube.com/youtu.be/m.youtube.com diterima, host lain
    (termasuk yang MENGANDUNG "youtube") ditolak — dan jalur penolakan TIDAK
    mengimpor/memanggil yt-dlp (anti-SSRF, bisa diuji offline).
  - scraper MATI secara bawaan; rute /api/video/scrape menjawab "dimatikan".
  - siap_ingest() melapor jujur (yt-dlp ada/tidak).
  - dari_url() jalur sukses dengan _unduh_satu di-monkeypatch (tak ada unduhan
    nyata).
"""
from __future__ import annotations

import importlib.util
import types

import pytest

from app.services import video_ingest
from tests.conftest import PW_PAUL, masuk
from tests.test_video_unggah import _buat_projek


# ------------------------------------------------------------ validasi host

@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=abc123",
    "https://youtube.com/watch?v=abc123",
    "https://m.youtube.com/watch?v=abc",
    "https://youtu.be/abc123",
    "http://youtube.com/watch?v=x",
])
def test_host_youtube_diterima(url):
    assert video_ingest.host_youtube(url) is True


@pytest.mark.parametrize("url", [
    "https://evil.com/watch?v=abc",
    "https://youtube.com.evil.net/watch?v=abc",   # host palsu bersufiks
    "https://evil.com/youtube.com",               # youtube cuma di path
    "http://169.254.169.254/latest/meta-data",    # SSRF metadata
    "file:///etc/passwd",
    "ftp://youtube.com/x",
    "bukan url",
    "",
])
def test_host_non_youtube_ditolak(url):
    assert video_ingest.host_youtube(url) is False


def test_dari_url_tolak_host_tanpa_sentuh_ytdlp(tmp_path, monkeypatch):
    """Penolakan host TIDAK boleh memanggil _unduh_satu (yang mengimpor yt-dlp):
    kalau toh dipanggil, tes ini meledak — bukti jalur tolak bebas-jaringan."""
    def _jangan(*a, **k):
        raise AssertionError("yt-dlp tak boleh disentuh pada host yang ditolak")
    monkeypatch.setattr(video_ingest, "_unduh_satu", _jangan)

    r = video_ingest.dari_url("https://evil.com/watch?v=x", tmp_path)
    assert r["ok"] is False and "YouTube" in r["error"]
    assert list(tmp_path.iterdir()) == []         # tak ada berkas mendarat


def test_dari_url_sukses_dengan_unduh_dipalsu(tmp_path, monkeypatch):
    """Jalur sukses dengan _unduh_satu di-monkeypatch — tak ada unduhan nyata."""
    def _palsu(url, dest, maju=None):
        (dest / "vid123.mp4").write_bytes(b"x")
        return {"ok": True, "nama": "vid123.mp4", "id": "vid123", "judul": "T"}
    monkeypatch.setattr(video_ingest, "_unduh_satu", _palsu)

    r = video_ingest.dari_url("https://youtu.be/vid123", tmp_path)
    assert r["ok"] is True and r["nama"] == "vid123.mp4"
    assert (tmp_path / "vid123.mp4").exists()


def test_dari_url_kosong_ditolak(tmp_path):
    assert video_ingest.dari_url("", tmp_path)["ok"] is False


# ------------------------------------------------------------ sah_maks

def test_sah_maks_dijepit():
    assert video_ingest.sah_maks(0) == 1
    assert video_ingest.sah_maks(-5) == 1
    assert video_ingest.sah_maks(9999) == video_ingest.MAKS_SCRAPE
    assert video_ingest.sah_maks("bukan angka") == 10
    assert video_ingest.sah_maks(7) == 7


# ------------------------------------------------------------ siap_ingest jujur

def test_siap_ingest_jujur():
    ok, alasan = video_ingest.siap_ingest()
    if importlib.util.find_spec("yt_dlp") is None:
        # yt-dlp tak terpasang (venv CPU bersih) -> jujur melapor kurang.
        assert ok is False and "yt-dlp" in alasan
    else:
        # Terpasang -> hasil mengikuti ketersediaan ffmpeg; apa pun, alasannya
        # konsisten dengan ok.
        assert (ok and not alasan) or (not ok and alasan)


# ------------------------------------------------------------ scraper gated

def test_scraper_aktif_bawaan_mati():
    assert video_ingest.scraper_aktif(types.SimpleNamespace(scraper=False)) is False
    assert video_ingest.scraper_aktif(types.SimpleNamespace(scraper=True)) is True
    # objek tanpa medan scraper diperlakukan mati (getattr default).
    assert video_ingest.scraper_aktif(types.SimpleNamespace()) is False


def test_rute_scrape_dimatikan_bawaan(klien, lingkungan):
    """Dengan LABELAPP_SCRAPER mati (bawaan), rute scrape menolak dengan pesan
    jelas — tanpa menyentuh jaringan."""
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "aksi-scrape", "video")
    r = klien.post("/api/video/scrape?ds=aksi-scrape&query=basket&maks=3").json()
    assert r["ok"] is False and "dimatikan" in r["error"]


def test_rute_scrape_butuh_projek_video_saat_nyala(klien, lingkungan, monkeypatch):
    """Saat scraper dinyalakan, gerbang berikutnya tetap berlaku: projek image
    ditolak (pemotongan/ingest klip hanya projek video), bukan lolos diam-diam.
    Membuktikan saklar hanya membuka gerbang scraper, bukan semua penjaga."""
    from app.config import get_settings

    monkeypatch.setenv("LABELAPP_SCRAPER", "1")
    get_settings.cache_clear()
    try:
        masuk(klien, "paul", PW_PAUL)
        _buat_projek(klien, "img-scrape", "image")
        r = klien.post("/api/video/scrape?ds=img-scrape&query=basket&maks=2").json()
        # Lolos gerbang scraper -> tertahan di siap_ingest (yt-dlp kurang) ATAU
        # di _projek_video (projek image). Yang penting: BUKAN "dimatikan".
        assert r["ok"] is False and "dimatikan" not in r["error"]
    finally:
        get_settings.cache_clear()
