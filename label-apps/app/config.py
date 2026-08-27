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
# Arsip yang boleh diunggah lalu dibongkar di server.
ARSIP_EXT = (".zip",)

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
    # Nama akun yang dimasuki otomatis TANPA password. Hanya berlaku untuk
    # permintaan dari mesin itu sendiri — lihat deps.sesi_otomatis.
    autologin: str = ""
    # Domain Google Workspace yang boleh masuk sendiri, mis. "higo.id".
    # Kosong berarti login Google mati. Email di LUAR domain itu hanya boleh
    # kalau sudah didaftarkan admin lewat halaman kelola akun.
    google_domain: str = ""

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_zip_bytes(self) -> int:
        return self.max_zip_mb * 1024 * 1024

    def batas_untuk(self, nama: str) -> tuple[int, str]:
        """Batas ukuran unggahan untuk sebuah nama berkas -> (byte, keterangan)."""
        if Path(nama).suffix.lower() in ARSIP_EXT:
            return self.max_zip_bytes, f"{self.max_zip_mb} MB (arsip)"
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
        max_zip_mb=max(1, _int("MAX_ZIP_MB", 4096)),
        anylabeling=_get("ANYLABELING") or "anylabeling",
        open_mode="dir" if _get("OPEN_MODE") == "dir" else "file",
        lock_labels=_bool("LOCK_LABELS"),
        extra_labels=_read_labels(_path("LABELS_FILE")),
        flags=_read_labels(_path("FLAGS_FILE")),
        autologin=_get("DEV_AUTOLOGIN"),
        google_domain=_get("GOOGLE_DOMAIN"),
    )
