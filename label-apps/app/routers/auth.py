"""Login dan logout."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import Settings, get_settings
from ..deps import optional_session
from ..log import catat
from ..services import projek as svc_projek
from ..security import (COOKIE_NAME, authenticate, daftar_sendiri,
                        load_users, menunggu_setujuan, user_slug)
from ..session import store
from ..templating import templates

log = catat("labelapp.auth")

router = APIRouter(tags=["auth"])

# Jeda setiap login gagal. Menebak password lewat jaringan jadi mahal tanpa
# perlu menyimpan penghitung percobaan per alamat.
FAILED_LOGIN_DELAY = 0.6


# Halaman PUBLIK (tanpa sesi): wajah aplikasi + dokumentasi fitur. Akar "/"
# untuk pengunjung anonim juga memakai landing.html (lewat handler NeedsLogin di
# main.py); rute eksplisit ini menyediakan URL tetap yang SELALU menampilkannya
# — berguna untuk ditautkan, dan untuk pratinjau di dev (yang auto-login
# membuat "/" selalu masuk sebagai devuser). Keduanya tak butuh sesi.
@router.get("/landing", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse(request, "landing.html", {})


@router.get("/fitur", response_class=HTMLResponse)
async def fitur(request: Request):
    return templates.TemplateResponse(request, "fitur.html", {})


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, next: str = ""):
    if optional_session(request):
        # Sudah masuk: langsung ke tujuan semula, bukan ke beranda. Orang yang
        # menekan tautan undangan sambil sudah punya sesi tidak perlu lewat
        # halaman masuk sama sekali.
        tujuan = next if next.startswith("/") and not next.startswith("//") else "/"
        return RedirectResponse(tujuan, status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "login.html", {
        "akun": "", "next": next,
        "boleh_daftar": get_settings().daftar_sendiri,
        "daftar_langsung": get_settings().daftar_langsung})


@router.post("/login")
async def login(request: Request,
                user: str = Form(""),
                pw: str = Form(""),
                next: str = Form(""),
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
            {"akun": user, "next": next,
             "boleh_daftar": settings.daftar_sendiri,
             "daftar_langsung": settings.daftar_langsung,
             "error": ("Akun ini sudah terdaftar tapi belum disetujui admin."
                       if menunggu else "Akun atau password salah.")},
            status_code=status.HTTP_401_UNAUTHORIZED)

    sid, sess = store.create(akun, settings)
    if settings.default_src and settings.default_src.is_dir():
        try:
            await asyncio.to_thread(sess.load, settings.default_src)
        except Exception:
            pass          # folder awal bermasalah bukan alasan gagal login

    # Kembali ke halaman yang tadi diminta, kalau ada. Hanya jalur di dalam
    # aplikasi ini — '//' dan skema apa pun dibuang, supaya kolom tersembunyi
    # ini tidak bisa dipakai mengarahkan orang ke situs lain lewat alamat yang
    # mereka percaya.
    tujuan = next if next.startswith("/") and not next.startswith("//") else "/"
    resp = RedirectResponse(tujuan, status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(COOKIE_NAME, sid, httponly=True, samesite="lax", path="/")
    return resp


@router.get("/logout")
async def logout(request: Request):
    store.drop(request.cookies.get(COOKIE_NAME))
    resp = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@router.get("/daftar", response_class=HTMLResponse)
async def daftar_form(request: Request, next: str = "",
                      settings: Settings = Depends(get_settings)):
    if optional_session(request):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "daftar.html", {
        "boleh": settings.daftar_sendiri, "akun": "", "email": "", "next": next})


def _token_undangan(next: str) -> str:
    """Token undangan dari `next` kalau ia menunjuk /undangan/<token>, else ''."""
    if next.startswith("/undangan/"):
        return next[len("/undangan/"):].split("?", 1)[0].split("/", 1)[0]
    return ""


@router.post("/daftar")
async def daftar_kirim(request: Request, user: str = Form(""),
                       email: str = Form(""), pw: str = Form(""),
                       pw2: str = Form(""), next: str = Form(""),
                       settings: Settings = Depends(get_settings)):
    """
    Pendaftaran mandiri. Hasilnya akun yang MENUNGGU persetujuan admin.

    Tanpa verifikasi email, tidak ada yang membuktikan alamat yang diketik
    milik pendaftarnya. Persetujuan admin yang menggantikan pembuktian itu,
    dan itu juga yang membuat rute ini aman kalau portnya ternyata terbuka
    ke luar jaringan kantor.

    KECUALI kalau mendaftar lewat tautan UNDANGAN bertoken yang sah (`next` =
    /undangan/<token>): token itu rahasia dan diberikan pemilik projek, jadi
    pendaftarnya SUDAH diizinkan. Akunnya langsung aktif (tak perlu persetujuan
    admin terpisah), lalu langsung dibawa menerima undangannya -- satu alur,
    satu klik: tautan email -> daftar -> langsung gabung.
    """
    def salah(pesan):
        return templates.TemplateResponse(
            request, "daftar.html",
            {"boleh": True, "akun": user, "email": email, "next": next,
             "error": pesan},
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

    token = _token_undangan(next)
    diundang = bool(token) and (await asyncio.to_thread(
        svc_projek.cari_undangan, settings.uploads_root, token)) is not None

    try:
        akun = await asyncio.to_thread(
            daftar_sendiri, settings.users_file, user, pw, email,
            settings.daftar_langsung or diundang)
    except ValueError as e:
        return salah(str(e).capitalize() + ".")
    except OSError as e:
        return salah(f"Gagal menyimpan: {str(e)[:80]}")

    if diundang:
        # Diundang: langsung masuk + diteruskan ke halaman terima undangan, yang
        # menambahkannya jadi anggota projek. Tak ada layar "menunggu" di tengah.
        log.info("pendaftaran via undangan: %r (email=%r) aktif + diteruskan",
                 akun, (email or "").strip())
        sid, _sess = store.create(akun, settings)
        resp = RedirectResponse(next, status_code=status.HTTP_303_SEE_OTHER)
        resp.set_cookie(COOKIE_NAME, sid, httponly=True, samesite="lax", path="/")
        return resp

    log.info("pendaftaran mandiri: %r (email=%r) %s", akun,
             (email or "").strip(),
             "langsung aktif" if settings.daftar_langsung
             else "menunggu persetujuan")
    return templates.TemplateResponse(request, "daftar.html", {
        "boleh": True, "selesai": akun, "langsung": settings.daftar_langsung})
