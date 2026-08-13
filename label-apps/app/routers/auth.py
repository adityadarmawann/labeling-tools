"""Login dan logout."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import Settings, get_settings
from ..deps import optional_session
from ..security import COOKIE_NAME, authenticate, load_users
from ..session import store
from ..templating import templates

router = APIRouter(tags=["auth"])

# Jeda setiap login gagal. Menebak password lewat jaringan jadi mahal tanpa
# perlu menyimpan penghitung percobaan per alamat.
FAILED_LOGIN_DELAY = 0.6


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if optional_session(request):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "login.html", {"akun": ""})


@router.post("/login")
async def login(request: Request,
                user: str = Form(""),
                pw: str = Form(""),
                settings: Settings = Depends(get_settings)):
    akun = authenticate(load_users(settings.users_file), user, pw)
    if akun is None:
        await asyncio.sleep(FAILED_LOGIN_DELAY)
        return templates.TemplateResponse(
            request, "login.html",
            {"akun": user, "error": "Akun atau password salah."},
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
