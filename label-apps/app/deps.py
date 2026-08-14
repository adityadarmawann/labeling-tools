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


# Header yang dipasang reverse proxy. Kehadirannya berarti permintaan sudah
# melewati perantara, jadi tidak mungkin benar-benar dari mesin server.
HEADER_PROXY = ("x-forwarded-for", "x-real-ip", "forwarded",
                "x-forwarded-host", "x-forwarded-proto")


def is_local(request: Request) -> bool:
    """
    True kalau permintaan benar-benar dari mesin server sendiri.

    Dua syarat, dan syarat kedua penting:

    1. Alamat soketnya lokal. Sengaja alamat soket, bukan X-Forwarded-For,
       karena header itu bisa dipalsukan klien.
    2. Tidak ada satu pun header proxy. Di belakang nginx pada mesin yang sama,
       SEMUA permintaan datang dari 127.0.0.1 — tanpa syarat ini, tombol yang
       membuka jendela di layar server (AnyLabeling, dialog folder) akan aktif
       untuk siapa saja yang mengakses lewat domain, dan jendelanya muncul di
       monitor fisik server.

    Akibat yang disengaja: kalau kamu sendiri membuka lewat domain, tombol
    desktop juga mati. Untuk memakainya, buka langsung lewat 127.0.0.1 dari
    mesin itu.
    """
    if any(h in request.headers for h in HEADER_PROXY):
        return False
    return bool(request.client) and request.client.host in LOCAL_HOSTS


def sesi_otomatis(request: Request) -> Session | None:
    """
    Login otomatis untuk mode dev, supaya `--reload` tidak memaksa login ulang
    setiap kali kode disentuh.

    Tiga syarat, dan dua terakhir yang membuatnya aman dipasang:

    1. LABELAPP_DEV_AUTOLOGIN memuat nama akun.
    2. Permintaan datang dari mesin itu sendiri — memakai is_local(), jadi
       permintaan lewat reverse proxy atau dari jaringan tetap ditolak.
    3. Akunnya benar-benar ada di berkas akun.

    Syarat kedua yang penting: kalau setelan ini sampai tersalin ke produksi,
    anggota tim dari jaringan TETAP harus login. Yang bisa memakainya hanya
    orang yang sudah berada di mesin server.
    """
    from .config import get_settings
    from .security import load_users, user_slug

    st = get_settings()
    if not st.autologin or not is_local(request):
        return None
    akun = user_slug(st.autologin)
    if akun not in load_users(st.users_file):
        return None
    _, sess = store.create(akun, st)
    return sess


def current_session(request: Request) -> Session:
    """Sesi akun untuk halaman HTML. Tanpa sesi -> NeedsLogin."""
    sess = store.get(request.cookies.get(COOKIE_NAME))
    if sess is None:
        sess = sesi_otomatis(request)
    if sess is None:
        raise NeedsLogin
    return sess


def current_session_api(request: Request) -> Session:
    """Sesi akun untuk endpoint JSON. Tanpa sesi -> 401 dengan pesan jelas."""
    sess = store.get(request.cookies.get(COOKIE_NAME))
    if sess is None:
        sess = sesi_otomatis(request)
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
