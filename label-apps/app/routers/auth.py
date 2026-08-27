"""Login dan logout."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import Settings, get_settings
from ..deps import optional_session
from ..log import catat
from ..security import (COOKIE_NAME, authenticate, daftar_sendiri,
                        load_users, menunggu_setujuan, user_slug)
from ..session import store
from ..templating import templates

log = catat("labelapp.auth")

router = APIRouter(tags=["auth"])

# Jeda setiap login gagal. Menebak password lewat jaringan jadi mahal tanpa
# perlu menyimpan penghitung percobaan per alamat.
FAILED_LOGIN_DELAY = 0.6


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if optional_session(request):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "login.html", {
        "akun": "", "boleh_daftar": get_settings().daftar_sendiri})


@router.post("/login")
async def login(request: Request,
                user: str = Form(""),
                pw: str = Form(""),
                settings: Settings = Depends(get_settings)):
    users = load_users(settings.users_file)
    akun = authenticate(users, user, pw)
    if akun is None:
        await asyncio.sleep(FAILED_LOGIN_DELAY)
        # Sandi benar tapi belum disetujui itu keadaan yang berbeda, dan
        # menyebutnya "akun atau password salah" membuat orang mengetik ulang
        # sandinya berkali-kali padahal sandinya memang benar.
        menunggu = menunggu_setujuan(users, user_slug(user))
        return templates.TemplateResponse(
            request, "login.html",
            {"akun": user, "boleh_daftar": settings.daftar_sendiri,
             "error": ("Akunmu sudah terdaftar tapi belum disetujui admin."
                       if menunggu else "Akun atau password salah.")},
            status_code=status.HTTP_401_UNAUTHORIZED)

    sid, sess = store.create(akun, settings)
    if settings.default_src and settings.default_src.is_dir():
        try:
            await asyncio.to_thread(sess.load, settings.default_src)
        except Exception:
            pass          # folder awal bermasalah bukan alasan gagal login

    resp = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(COOKIE_NAME, sid, httponly=True, samesite="lax", path="/")
    return resp


@router.get("/logout")
async def logout(request: Request):
    store.drop(request.cookies.get(COOKIE_NAME))
    resp = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@router.get("/daftar", response_class=HTMLResponse)
async def daftar_form(request: Request,
                      settings: Settings = Depends(get_settings)):
    if optional_session(request):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "daftar.html", {
        "boleh": settings.daftar_sendiri, "akun": "", "email": ""})


@router.post("/daftar")
async def daftar_kirim(request: Request, user: str = Form(""),
                       email: str = Form(""), pw: str = Form(""),
                       pw2: str = Form(""),
                       settings: Settings = Depends(get_settings)):
    """
    Pendaftaran mandiri. Hasilnya akun yang MENUNGGU persetujuan admin.

    Tanpa verifikasi email, tidak ada yang membuktikan alamat yang diketik
    milik pendaftarnya. Persetujuan admin yang menggantikan pembuktian itu,
    dan itu juga yang membuat rute ini aman kalau portnya ternyata terbuka
    ke luar jaringan kantor.
    """
    def salah(pesan):
        return templates.TemplateResponse(
            request, "daftar.html",
            {"boleh": True, "akun": user, "email": email, "error": pesan},
            status_code=status.HTTP_400_BAD_REQUEST)

    if not settings.daftar_sendiri:
        return templates.TemplateResponse(
            request, "daftar.html", {"boleh": False},
            status_code=status.HTTP_403_FORBIDDEN)
    if not (user or "").strip():
        return salah("Nama akun masih kosong.")
    if len(pw) < 8:
        return salah("Kata sandi minimal 8 karakter.")
    if pw != pw2:
        return salah("Ulangan kata sandinya tidak sama.")
    if email and "@" not in email:
        return salah("Alamat email itu tidak terlihat benar.")

    try:
        akun = await asyncio.to_thread(
            daftar_sendiri, settings.users_file, user, pw, email,
            settings.daftar_langsung)
    except ValueError as e:
        return salah(str(e).capitalize() + ".")
    except OSError as e:
        return salah(f"Gagal menyimpan: {str(e)[:80]}")

    log.info("pendaftaran mandiri: %r (email=%r) %s", akun,
             (email or "").strip(),
             "langsung aktif" if settings.daftar_langsung
             else "menunggu persetujuan")
    return templates.TemplateResponse(request, "daftar.html", {
        "boleh": True, "selesai": akun, "langsung": settings.daftar_langsung})
