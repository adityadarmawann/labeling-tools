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
    anylabeling: str = "anylabeling"
    open_mode: str = "file"          # "file" | "dir"
    lock_labels: bool = False
    extra_labels: list[str] = field(default_factory=list)
    # Nama akun yang dimasuki otomatis TANPA password. Hanya berlaku untuk
    # permintaan dari mesin itu sendiri — lihat deps.sesi_otomatis.
    autologin: str = ""

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


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
        anylabeling=_get("ANYLABELING") or "anylabeling",
        open_mode="dir" if _get("OPEN_MODE") == "dir" else "file",
        lock_labels=_bool("LOCK_LABELS"),
        extra_labels=_read_labels(_path("LABELS_FILE")),
        autologin=_get("DEV_AUTOLOGIN"),
    )
