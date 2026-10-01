"""
Setelan aplikasi, dibaca dari environment.

Semua konfigurasi lewat environment supaya tiga cara menjalankan aplikasi
memakai sumber yang sama: run.py (yang menerjemahkan argumen CLI menjadi
environment), `uvicorn app.main:app` langsung, dan unit systemd.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff")
ANN_EXT = (".json", ".txt")
# Berkas pendamping dataset. `data.yaml` ekspor Roboflow ada di sini — di
# situlah nama kelas disimpan, dan tanpa menerimanya dataset yang diunggah
# tampil dengan kelas "0", "1", "2" alih-alih nama sebenarnya.
META_EXT = (".yaml", ".yml")

# Keranjang gambar yang dibuang lewat "Hapus dari projek" (tugas.buang_gambar
# memindahkan berkasnya ke <projek>/_sampah-gambar/<cap>/). Ia ada DI DALAM
# projek tapi ISINYA BUKAN data projek lagi — sama seperti berkas berawalan
# titik. Namanya berawalan "_" bukan ".", jadi ia harus disebut terpisah di
# mana pun isi projek ditelusuri (scanner maupun sidebar) supaya gambar yang
# sudah dibuang tidak ikut terhitung.
SAMPAH_GAMBAR = "_sampah-gambar"
# Folder internal projek VIDEO (lihat services/klip.py). Sama seperti
# _sampah-gambar: ADA di dalam projek tapi ISINYA BUKAN gambar dataset, jadi
# harus dilewati di SETIAP penghitung gambar (scanner + sidebar) — kalau tidak,
# satu video yang diunggah atau ratusan klip hasil potong terbaca sebagai
# "gambar" di kartu dan lencana sidebar.
#   _sumber/  video mentah hasil unggah (tak diekstrak, tak dihapus)
#   klip/     keluaran potongan per batch; klip/_ditolak hasil filter objek
SUMBER_VIDEO = "_sumber"
KLIP = "klip"
KLIP_DITOLAK = "_ditolak"        # subfolder di dalam klip/ untuk klip tersaring
KLIP_BURUK = "_bad"              # subfolder di dalam _sumber/ untuk klip cacat
# Semua folder internal yang isinya bukan gambar dataset, untuk sekali lewat.
# klip/_ditolak dan _sumber/_bad ikut terlewati karena induknya (klip, _sumber)
# sudah ada di sini — penyaring menelusuri PER KOMPONEN path.
FOLDER_INTERNAL = (SAMPAH_GAMBAR, SUMBER_VIDEO, KLIP)
# Arsip yang boleh diunggah lalu dibongkar di server.
ARSIP_EXT = (".zip",)
# Video yang boleh diunggah ke projek IMAGE (diekstrak jadi frame lewat
# services/ekstraksi.py; videonya dibuang setelah frame selamat) MAUPUN ke
# projek VIDEO (disimpan utuh di _sumber/, dipotong jadi klip lewat
# services/klip.py; videonya TIDAK dibuang).
VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v")

PREFIX = "LABELAPP_"


def _get(name: str, default: str = "") -> str:
    return os.environ.get(PREFIX + name, default).strip()


def _path(name: str, default: Path | None = None) -> Path | None:
    v = _get(name)
    return Path(v).expanduser().resolve() if v else default


def _int(name: str, default: int) -> int:
    try:
        return int(_get(name) or default)
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    v = _get(name)
    return v.lower() in ("1", "true", "yes", "ya", "on") if v else default


@dataclass(frozen=True)
class Settings:
    """Setelan yang sama untuk semua akun. Keadaan per akun ada di Session."""

    users_file: Path
    uploads_root: Path
    thumb_root: Path
    datasets_root: Path | None = None
    default_src: Path | None = None
    max_upload_mb: int = 80
    # Arsip punya batas sendiri: satu ekspor Roboflow bisa lebih dari 1 GB,
    # sementara batas per-gambar sengaja tetap kecil supaya salah seret tidak
    # mengirim berkas raksasa.
    max_zip_mb: int = 4096
    # Video (diekstrak jadi frame) punya batas sendiri: satu klip bisa jauh
    # lebih besar dari satu gambar, tapi tak sebesar ekspor dataset penuh.
    max_video_mb: int = 2048
    # Perlindungan zip bomb: total isi setelah dibongkar dibatasi sekian kali
    # ukuran arsipnya. Ekspor dataset berisi JPEG yang sudah termampatkan,
    # jadi rasionya mendekati 1 — nilai 20 sudah sangat longgar.
    zip_ratio_max: int = 20
    zip_entries_max: int = 200_000
    anylabeling: str = "anylabeling"
    open_mode: str = "file"          # "file" | "dir"
    lock_labels: bool = False
    extra_labels: list[str] = field(default_factory=list)
    # Flag tingkat gambar yang SELALU ditawarkan di tiap gambar, padanan
    # `flags:` di anylabeling_config.yaml (label_widget.py:202-203). Tanpa
    # daftar tetap, nama flag harus diketik ulang persis di tiap gambar.
    flags: list[str] = field(default_factory=list)
    # Nama yang dipakai aplikasi ini menyebut dirinya: judul tab peramban dan
    # kepala halaman. Dev dan prod berjalan bersamaan di mesin yang sama, dan
    # dua tab yang judulnya sama persis adalah cara termudah mengetik di
    # jendela yang salah — karena itu dev menyebut dirinya "HIGOLAB-DEV".
    nama_app: str = "HIGOLAB"
    # Nama akun yang dimasuki otomatis TANPA password. Hanya berlaku untuk
    # permintaan dari mesin itu sendiri — lihat deps.sesi_otomatis.
    autologin: str = ""
    # Domain Google Workspace yang boleh masuk sendiri, mis. "higo.id".
    # Kosong berarti login Google mati. Email di LUAR domain itu hanya boleh
    # kalau sudah didaftarkan admin lewat halaman kelola akun.
    google_domain: str = ""
    # Boleh mendaftar sendiri lewat /daftar. Hasilnya SELALU akun yang
    # menunggu persetujuan admin, tidak pernah langsung bisa masuk.
    daftar_sendiri: bool = True
    # Kalau True, akun hasil pendaftaran mandiri LANGSUNG bisa masuk tanpa
    # menunggu persetujuan. Hanya pantas dinyalakan kalau portnya memang
    # dibatasi ke jaringan tepercaya — periksa `sudo ufw status verbose`,
    # dan pastikan bawaan incoming-nya deny.
    daftar_langsung: bool = False
    # Scraper kata-kunci YouTube (projek video: kata kunci -> unduh massal lewat
    # yt-dlp). MATI secara bawaan — persis seperti daftar_langsung, dan karena
    # alasan yang sama: aplikasi ini ditutup dari internet (ufw membatasi port
    # masuk), tetapi scraper membuka lalu lintas KELUAR ke YouTube dalam jumlah
    # besar — tunduk pada rate-limit/ToS-nya, dan pantas dinyalakan hanya dengan
    # sengaja. Tempel-URL satuan dan unggah berkas tidak tersentuh saklar ini;
    # keduanya selalu boleh (lihat services/video_ingest.scraper_aktif).
    scraper: bool = False

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_zip_bytes(self) -> int:
        return self.max_zip_mb * 1024 * 1024

    @property
    def max_video_bytes(self) -> int:
        return self.max_video_mb * 1024 * 1024

    def batas_untuk(self, nama: str) -> tuple[int, str]:
        """Batas ukuran unggahan untuk sebuah nama berkas -> (byte, keterangan)."""
        sfx = Path(nama).suffix.lower()
        if sfx in ARSIP_EXT:
            return self.max_zip_bytes, f"{self.max_zip_mb} MB (arsip)"
        if sfx in VIDEO_EXT:
            return self.max_video_bytes, f"{self.max_video_mb} MB (video)"
        return self.max_upload_bytes, f"{self.max_upload_mb} MB"


def _read_labels(p: Path | None) -> list[str]:
    if not p or not p.exists():
        return []
    return [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Dibaca sekali per proses. lru_cache membuatnya aman dipakai sebagai
    dependency FastAPI tanpa membaca ulang berkas di setiap permintaan."""
    root = Path(__file__).resolve().parent.parent

    datasets_root = _path("DATASETS_ROOT")
    uploads_root = _path("UPLOADS_ROOT") or (
        datasets_root / "_unggahan" if datasets_root else Path.home() / "labelapp-unggahan")
    thumb_root = _path("THUMB_ROOT") or (
        Path(os.environ.get("TMPDIR", "/tmp")) / f"labelapp_{os.getpid()}")

    uploads_root.mkdir(parents=True, exist_ok=True)
    thumb_root.mkdir(parents=True, exist_ok=True)

    return Settings(
        users_file=_path("USERS_FILE") or (root / "users.json"),
        uploads_root=uploads_root,
        thumb_root=thumb_root,
        datasets_root=datasets_root,
        default_src=_path("DEFAULT_SRC"),
        max_upload_mb=max(1, _int("MAX_UPLOAD_MB", 80)),
        max_video_mb=max(1, _int("MAX_VIDEO_MB", 2048)),
        max_zip_mb=max(1, _int("MAX_ZIP_MB", 4096)),
        anylabeling=_get("ANYLABELING") or "anylabeling",
        open_mode="dir" if _get("OPEN_MODE") == "dir" else "file",
        lock_labels=_bool("LOCK_LABELS"),
        extra_labels=_read_labels(_path("LABELS_FILE")),
        flags=_read_labels(_path("FLAGS_FILE")),
        nama_app=_get("NAMA") or "HIGOLAB",
        autologin=_get("DEV_AUTOLOGIN"),
        google_domain=_get("GOOGLE_DOMAIN"),
        daftar_sendiri=_bool("DAFTAR_SENDIRI", True),
        daftar_langsung=_bool("DAFTAR_LANGSUNG", False),
        scraper=_bool("SCRAPER", False),
    )
