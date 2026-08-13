"""
Dependency bersama untuk semua router.

Tiga hal yang selalu dibutuhkan: setelan, sesi akun yang sedang login, dan
penanda apakah permintaan datang dari mesin server sendiri.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from .config import Settings, get_settings
from .security import COOKIE_NAME
from .session import Session, store

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


class NeedsLogin(Exception):
    """Halaman HTML tanpa sesi -> dialihkan ke /login (bukan 401 mentah)."""


def is_local(request: Request) -> bool:
    """
    True kalau permintaan berasal dari mesin server sendiri.

    Sengaja memakai alamat soket, bukan X-Forwarded-For, karena header itu
    bisa dipalsukan klien. Konsekuensinya: di belakang reverse proxy semua
    permintaan terlihat lokal, jadi jangan pasang proxy di mesin yang sama
    tanpa memikirkan ini.
    """
    return bool(request.client) and request.client.host in LOCAL_HOSTS


def current_session(request: Request) -> Session:
    """Sesi akun untuk halaman HTML. Tanpa sesi -> NeedsLogin."""
    sess = store.get(request.cookies.get(COOKIE_NAME))
    if sess is None:
        raise NeedsLogin
    return sess


def current_session_api(request: Request) -> Session:
    """Sesi akun untuk endpoint JSON. Tanpa sesi -> 401 dengan pesan jelas."""
    sess = store.get(request.cookies.get(COOKIE_NAME))
    if sess is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "sesi habis — muat ulang halaman lalu masuk lagi")
    return sess


def require_local(request: Request) -> None:
    """Untuk endpoint yang memunculkan jendela di layar server."""
    if not is_local(request):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "hanya bisa dijalankan dari mesin server — jendelanya muncul di "
            "layar server, bukan di layarmu")


def optional_session(request: Request) -> Session | None:
    return store.get(request.cookies.get(COOKIE_NAME))


def login_redirect() -> RedirectResponse:
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


SettingsDep = Depends(get_settings)
SessionDep = Depends(current_session)
ApiSessionDep = Depends(current_session_api)
LocalOnly = Depends(require_local)

__all__ = ["Settings", "Session", "NeedsLogin", "is_local", "current_session",
           "current_session_api", "require_local", "optional_session",
           "login_redirect", "SettingsDep", "SessionDep", "ApiSessionDep",
           "LocalOnly"]
