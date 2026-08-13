"""
Sesi per akun.

Inti dari aplikasi ini dipakai bersama: setiap akun punya folder dataset,
hasil pindai, dan cache thumbnail sendiri. Tanpa pemisahan ini, satu orang
mengganti folder akan mengubah tampilan semua orang.

Sesi disimpan di memori, jadi hilang saat proses restart dan semua orang
login ulang. Itu disengaja — tidak ada sesi menggantung yang tidak bisa
dicabut.
"""
from __future__ import annotations

import secrets
import shutil
import threading
from pathlib import Path

from .config import Settings
from .security import safe_slug
from .services import annotations, scanner


class Session:
    """Keadaan milik satu akun yang sedang login."""

    def __init__(self, user: str, settings: Settings):
        self.user = user
        self.settings = settings
        self.src: Path | None = None
        self.items: list[dict] = []
        self.names: dict[int, str] = {}
        self.labelfile: Path | None = None
        self.thumbdir = settings.thumb_root / safe_slug(user)
        self.thumbdir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    # -- dataset --

    def load(self, src: Path) -> list[dict]:
        """Pindai folder baru dan buang cache thumbnail folder sebelumnya."""
        self.src = Path(src).resolve()
        self.items, self.names = scanner.scan(self.src)
        self.reset_thumbs()
        annotations.write_label_file(self)
        return self.items

    def reload(self) -> list[dict]:
        if self.src is None:
            return []
        return self.load(self.src)

    def find(self, path: str) -> dict | None:
        """
        Cari item berdasarkan path. Hanya berkas yang ada di dataset yang
        sedang dibuka akun ini yang bisa ditemukan — inilah yang mencegah
        satu akun membaca berkas sembarangan di disk server.
        """
        try:
            rp = Path(path).resolve()
        except (OSError, ValueError):
            return None
        for it in self.items:
            if it["img"].resolve() == rp:
                return it
        return None

    # -- berkas milik akun --

    def reset_thumbs(self) -> None:
        shutil.rmtree(self.thumbdir, ignore_errors=True)
        self.thumbdir.mkdir(parents=True, exist_ok=True)

    def drop_thumbs_for(self, item: dict) -> None:
        for f in self.thumbdir.glob(f"{scanner.item_key(item)}_*.jpg"):
            f.unlink(missing_ok=True)

    def upload_dir(self, ds: str) -> Path:
        return self.settings.uploads_root / safe_slug(self.user) / safe_slug(ds)


class SessionStore:
    """Peta sid (dari cookie) -> Session, aman dipakai dari banyak thread."""

    def __init__(self):
        self._data: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, user: str, settings: Settings) -> tuple[str, Session]:
        sid = secrets.token_urlsafe(32)
        sess = Session(user, settings)
        with self._lock:
            self._data[sid] = sess
        return sid, sess

    def get(self, sid: str | None) -> Session | None:
        if not sid:
            return None
        with self._lock:
            return self._data.get(sid)

    def drop(self, sid: str | None) -> None:
        if not sid:
            return
        with self._lock:
            sess = self._data.pop(sid, None)
        if sess:
            shutil.rmtree(sess.thumbdir, ignore_errors=True)

    def users(self) -> list[str]:
        with self._lock:
            return sorted({s.user for s in self._data.values()})


store = SessionStore()
