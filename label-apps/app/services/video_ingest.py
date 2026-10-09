"""
Ingest video projek aksi dari YouTube: tempel satu URL, atau scrape kata kunci.

TIGA CARA memasukkan video ke projek video (rencana C.1); modul ini menangani
dua di antaranya, keduanya mendarat di `_sumber/` dan TIDAK langsung dipotong
(pemotongan jadi klip terpisah lewat services/klip.py + /api/video/potong):

  (a) unggah berkas  -> routers/uploads.py (bukan di sini)
  (b) tempel URL     -> dari_url()   : satu video YouTube
  (c) scraper        -> scrape()     : `ytsearch`, unduh banyak, dedup per id

KENAPA yt-dlp DIIMPOR MALAS DI DALAM FUNGSI
-------------------------------------------
Sama seperti ultralytics/torch di modul lain: venv CPU harus mengimpor seluruh
aplikasi tanpa yt-dlp terpasang. Karena itu `import yt_dlp` tidak pernah di
puncak modul — ia di dalam fungsi yang benar-benar mengunduh, dan siap_ingest()
mengatakan apa adanya kalau paketnya belum ada, bukan membiarkan ingest jatuh
pada baris import beberapa detik setelah orang menekan tombol.

KENAPA HOST DIVERIFIKASI SEBELUM yt-dlp DISENTUH
------------------------------------------------
yt-dlp bisa mengambil dari ratusan situs. Menerima URL apa pun dari form lalu
menyerahkannya ke yt-dlp membuat rute ini jadi perkakas SSRF: satu permintaan
dengan `url=http://169.254.169.254/...` memaksa server mengambil dari host
internal. Karena itu host DIPERIKSA lebih dulu — hanya domain YouTube yang
lolos — dan jalur penolakannya tidak mengimpor maupun memanggil yt-dlp sama
sekali. Itu juga yang membuat penolakan bisa diuji tanpa jaringan.
"""
from __future__ import annotations

import logging
import urllib.parse
from pathlib import Path
from typing import Callable

from ..config import VIDEO_EXT
from . import klip

log = logging.getLogger(__name__)


class IngestTolak(Exception):
    """Ingest tak bisa dijalankan (paket kurang, URL bukan YouTube, dst).

    Padanan klip.KlipTolak untuk jalur ingest: dilempar pada kegagalan yang
    pemanggilnya perlu laporkan ke pengguna dengan kalimat yang jelas.
    """


# Video yang lebih panjang dari ini dilewati saat scrape: highlight pendek yang
# dicari, bukan siaran penuh satu jam. Diport dari MAX_VIDEO_DURATION
# basketball_scrapper_8class.py.
MAKS_DURASI = 600                 # detik
# Batas jumlah video sekali scrape. Dijepit supaya satu angka keliru dari form
# tak menyuruh server mengunduh ribuan video — rate-limit YouTube maupun disk
# jadi korbannya. 50 sudah jauh di atas kebutuhan satu sesi.
MAKS_SCRAPE = 50

# Host YouTube yang sah. Dicocokkan PERSIS (bukan "mengandung youtube"): host
# jahat "youtube.com.evil.net" atau "evil.com/youtube.com" tak boleh lolos.
_HOST_YT = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "music.youtube.com", "youtu.be", "www.youtu.be",
}


def _host(url: str) -> str:
    """Host sebuah URL (huruf kecil), atau "" kalau URL tak terbaca/bukan http."""
    try:
        u = urllib.parse.urlparse(str(url or "").strip())
    except ValueError:
        return ""
    if u.scheme not in ("http", "https"):
        return ""
    return (u.hostname or "").lower()


def host_youtube(url: str) -> bool:
    """True kalau `url` menunjuk host YouTube yang sah. Dipakai SEBELUM yt-dlp
    disentuh — inilah penjaga anti-SSRF; lihat kepala berkas."""
    return _host(url) in _HOST_YT


def sah_maks(maks) -> int:
    """Jumlah video scrape yang dijepit ke rentang wajar (1..MAKS_SCRAPE)."""
    try:
        n = int(maks)
    except (TypeError, ValueError):
        return 10
    return min(max(n, 1), MAKS_SCRAPE)


# ============================================================
# PRASYARAT
# ============================================================

def siap_ingest() -> tuple[bool, str]:
    """Server ini bisa ingest video dari YouTube atau tidak, beserta alasannya.

    Cermin klip.siap_video()/latih.siap_latih(): diperiksa SEBELUM orang
    memulai, supaya form bisa mengatakan apa adanya bahwa ingest belum bisa —
    bukan membiarkan pekerjaan diluncurkan lalu jatuh senyap.

    Dua prasyarat: (1) yt-dlp importable (pure-python, di venv CPU), dan (2)
    ffmpeg+ffprobe ada dengan encoder yang benar — yt-dlp memakai ffmpeg untuk
    menggabung video+audio jadi mp4, jadi tanpa ffmpeg unduhannya gagal
    menggabung. Prasyarat ffmpeg dipinjam apa adanya dari klip.siap_video().
    """
    import importlib.util

    if importlib.util.find_spec("yt_dlp") is None:
        return False, ("yt-dlp belum terpasang di server ini. Pasang dengan "
                       "`pip install yt-dlp` (lihat requirements.txt)")
    return klip.siap_video()


def scraper_aktif(settings) -> bool:
    """Scraper kata-kunci (unduh massal) dinyalakan atau tidak.

    MATI secara bawaan (config.Settings.scraper / LABELAPP_SCRAPER). Tempel-URL
    satuan dan unggah berkas TIDAK tunduk pada saklar ini — keduanya selalu
    boleh; yang digerbang hanya unduh massal yang membuka lalu lintas keluar
    besar ke YouTube. Lihat MEMORY "label-apps tertutup dari internet".
    """
    return bool(getattr(settings, "scraper", False))


# ============================================================
# UNDUH SATUAN
# ============================================================

def _opsi_ydl(dest: Path, maju: Callable[[dict], None] | None) -> dict:
    """Opsi yt-dlp bersama untuk dari_url & scrape (format 480p mp4, merge mp4).

    Diport dari download_video() basketball_scrapper_8class.py: resolusi sedang
    (<=480) cukup untuk training aksi dan hemat disk; keluaran digabung jadi mp4
    supaya seragam dengan klip hasil potong (h264/mp4).
    """
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,                  # satu URL = satu video, bukan playlist
        "outtmpl": str(dest / "%(id)s.%(ext)s"),
        "format": ("bestvideo[height<=480][ext=mp4]+bestaudio/"
                   "best[height<=480]/best"),
        "merge_output_format": "mp4",
    }
    if maju is not None:
        def _hook(d: dict) -> None:
            # yt-dlp memanggil hook dengan status downloading/finished; diteruskan
            # apa adanya sebagai kemajuan satuan.
            try:
                maju({"tahap": "unduh-berkas",
                      "status": d.get("status"),
                      "persen": _persen_hook(d)})
            except Exception:                 # noqa: BLE001  (hook tak boleh menggagalkan unduh)
                pass
        opts["progress_hooks"] = [_hook]
    return opts


def _persen_hook(d: dict) -> float:
    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
    done = d.get("downloaded_bytes") or 0
    if d.get("status") == "finished":
        return 1.0
    try:
        return min(1.0, done / total) if total else 0.0
    except (TypeError, ZeroDivisionError):
        return 0.0


def _unduh_satu(url: str, dest: Path,
                maju: Callable[[dict], None] | None = None) -> dict:
    """Unduh satu video YouTube ke `dest`. yt-dlp DIIMPOR DI SINI (malas).

    Mengembalikan {ok, nama, id, judul} kalau berhasil; melempar IngestTolak
    kalau yt-dlp belum ada atau unduhannya gagal. Dipisah dari dari_url/scrape
    supaya pengujian bisa menggantinya (monkeypatch) tanpa jaringan.
    """
    try:
        import yt_dlp
    except ImportError as e:
        raise IngestTolak("yt-dlp belum terpasang di server ini") from e

    dest.mkdir(parents=True, exist_ok=True)
    try:
        with yt_dlp.YoutubeDL(_opsi_ydl(dest, maju)) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:                    # noqa: BLE001  (yt-dlp melempar macam-macam)
        raise IngestTolak(f"unduhan gagal: {str(e)[:120]}") from e

    vid = str((info or {}).get("id") or "")
    # Berkas hasil merge: <id>.mp4 kalau ada, kalau tidak berkas apa pun ber-id
    # itu dengan ekstensi video yang dikenali.
    berkas = _berkas_untuk(dest, vid)
    if berkas is None:
        raise IngestTolak("unduhan selesai tetapi berkasnya tak ditemukan")
    return {"ok": True, "nama": berkas.name, "id": vid,
            "judul": str((info or {}).get("title") or "")}


def _berkas_untuk(dest: Path, vid: str) -> Path | None:
    """Berkas video ber-id `vid` di `dest`, mp4 didahulukan. None kalau tak ada."""
    if not vid:
        return None
    calon = [p for p in dest.glob(f"{vid}.*")
             if p.suffix.lower() in VIDEO_EXT]
    if not calon:
        return None
    calon.sort(key=lambda p: (p.suffix.lower() != ".mp4", p.name))
    return calon[0]


# ============================================================
# (b) TEMPEL URL
# ============================================================

def dari_url(url: str, dest_dir, *,
             maju: Callable[[dict], None] | None = None) -> dict:
    """Unduh satu video YouTube ke `dest_dir` (= `_sumber/` projek).

    Host DIVERIFIKASI lebih dulu (host_youtube) SEBELUM yt-dlp disentuh — host
    non-YouTube ditolak dengan {ok:False} tanpa mengimpor/memanggil yt-dlp
    (anti-SSRF; lihat kepala berkas). Tidak memotong jadi klip; itu langkah
    terpisah.
    """
    url = str(url or "").strip()
    if not url:
        return {"ok": False, "error": "URL masih kosong"}
    if not host_youtube(url):
        return {"ok": False, "error": (
            "hanya tautan YouTube yang diterima (youtube.com / youtu.be / "
            "m.youtube.com) — tempel tautan video YouTube, atau unggah "
            "berkasnya langsung")}
    dest = Path(dest_dir)
    # Sudah ada? yt-dlp sendiri melewati yang sudah terunduh, tapi kita belum
    # tahu id sebelum menanyakannya; kalau toh ganda, _berkas_untuk memilih yang
    # ada. Penghematan nyata ada di scrape() yang tahu id dari pencarian.
    try:
        hasil = _unduh_satu(url, dest, maju=maju)
    except IngestTolak as e:
        return {"ok": False, "error": str(e)[:160]}
    log.info("ingest URL %s -> %s", url[:60], hasil["nama"])
    return hasil


# ============================================================
# (c) SCRAPER KATA KUNCI
# ============================================================

def _cari(query: str, maks: int) -> list[dict]:
    """Cari `maks` video YouTube untuk `query`. yt-dlp DIIMPOR DI SINI (malas).

    Diport dari search_youtube() basketball_scrapper_8class.py: `ytsearch` +
    extract_flat, lalu buang yang lebih panjang dari MAKS_DURASI. Mengembalikan
    [{id, judul, durasi, url}]. Dipisah supaya pengujian bisa menggantinya.
    """
    try:
        import yt_dlp
    except ImportError as e:
        raise IngestTolak("yt-dlp belum terpasang di server ini") from e

    opts = {"quiet": True, "no_warnings": True, "extract_flat": True,
            "playlist_items": f"1:{maks}"}
    hasil: list[dict] = []
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{maks}:{query}", download=False)
    except Exception as e:                    # noqa: BLE001
        raise IngestTolak(f"pencarian gagal: {str(e)[:120]}") from e
    for entry in (info or {}).get("entries") or []:
        if not entry:
            continue
        durasi = entry.get("duration")
        if durasi is not None and durasi > MAKS_DURASI:
            continue
        vid = entry.get("id")
        if not vid:
            continue
        hasil.append({"id": vid, "judul": entry.get("title") or "",
                      "durasi": durasi,
                      "url": f"https://www.youtube.com/watch?v={vid}"})
    return hasil


def scrape(query: str, maks, dest_dir, *,
           maju: Callable[[dict], None] | None = None,
           batal: Callable[[], bool] | None = None) -> dict:
    """Cari `query` di YouTube dan unduh sampai `maks` video ke `dest_dir`.

    Resumable: id yang BERKASNYA sudah ada di `dest_dir` dilewati (dedup per id,
    diport dari basketball_scrapper_8class.py). `maju(dict)` dipanggil berkala;
    `batal()` yang True menghentikan di sela-sela video (video yang sedang
    diunduh diselesaikan dulu — yt-dlp tak kita potong di tengah).

    Satu video yang gagal TIDAK menggagalkan sisanya: ia dicatat sebagai gagal
    dan loop lanjut, sama seperti perilaku script asalnya.
    """
    query = " ".join(str(query or "").split())
    if not query:
        return {"ok": False, "error": "kata kunci masih kosong"}
    maks = sah_maks(maks)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    videos = _cari(query, maks)[:maks]
    total = len(videos)
    if maju:
        maju({"tahap": "cari", "total": total, "n": 0, "persen": 0.0})

    diunduh, dilewati, gagal = [], 0, 0
    for i, v in enumerate(videos):
        if batal is not None and batal():
            break
        # Sudah ada (sesi sebelumnya): lewati tanpa mengunduh ulang.
        if _berkas_untuk(dest, v["id"]) is not None:
            dilewati += 1
        else:
            try:
                hasil = _unduh_satu(v["url"], dest)
                diunduh.append(hasil["nama"])
            except IngestTolak as e:
                gagal += 1
                log.warning("scrape lewati %s: %s", v["id"], e)
        if maju:
            maju({"tahap": "unduh", "n": i + 1, "total": total,
                  "diunduh": len(diunduh), "dilewati": dilewati, "gagal": gagal,
                  "persen": (i + 1) / total if total else 1.0})

    log.info("scrape %r: %d video (unduh %d, lewati %d, gagal %d)",
             query[:60], total, len(diunduh), dilewati, gagal)
    return {"ok": True, "query": query, "diminta": maks, "ditemukan": total,
            "diunduh": len(diunduh), "dilewati": dilewati, "gagal": gagal,
            "nama": diunduh}
