"""
Halaman kelola akun.

KENAPA ADA
----------
Menambah anggota tim sebelumnya hanya bisa lewat `run.py --adduser` di
terminal server. Itu berarti satu orang harus punya akses SSH untuk pekerjaan
yang sebenarnya administratif, dan tiap anggota baru menunggu orang itu
sempat.

DUA HAL YANG DIJAGA KETAT DI SINI
---------------------------------
1. **Hanya admin yang boleh masuk.** Diperiksa di tiap rute, bukan hanya
   disembunyikan dari menu.

2. **Admin terakhir tidak bisa dihapus atau diturunkan.** Tanpa penjaga itu,
   satu klik keliru mengunci seluruh tim di luar halaman ini, dan satu-satunya
   jalan pulang adalah menyunting users.json lewat SSH — persis keadaan yang
   hendak ditinggalkan.

EMAIL DAN LOGIN GOOGLE
----------------------
Kolom `email` disiapkan untuk login Google Workspace yang menyusul. Aturannya
nanti dua lapis: siapa pun dengan email di domain Workspace boleh masuk, DAN
email di luar domain itu hanya boleh kalau sudah didaftarkan admin di sini.

Sekarang email cuma disimpan; ia belum memberi hak apa pun sampai OAuth-nya
tersambung. Itu disengaja — mendaftarkan orang lebih dulu berarti begitu
login Google menyala, tim tidak perlu diurus ulang satu per satu.
"""
from __future__ import annotations

import asyncio
import re
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ..config import Settings, get_settings
from ..deps import current_session, current_session_api
from ..log import catat
from ..security import (hash_password, is_admin, load_users, save_users,
                        user_slug)
from ..session import Session
from ..templating import templates

router = APIRouter()
log = catat("labelapp.admin")

MIN_SANDI = 8

# Sengaja longgar: memvalidasi email dengan ketat menolak alamat yang sah dan
# tidak menghalangi satu pun yang tidak sah.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Tolak(Exception):
    """Permintaan yang ditolak, dengan alasan yang bisa dibaca pengguna."""


def _wajib_admin(sess: Session, settings: Settings) -> dict:
    users = load_users(settings.users_file)
    if not is_admin(users, sess.user):
        raise Tolak("halaman ini hanya untuk admin")
    return users


def _jawab(fn, *a, **k):
    try:
        return {"ok": True, **fn(*a, **k)}
    except Tolak as e:
        return {"ok": False, "error": str(e)}
    except OSError as e:
        return {"ok": False, "error": f"gagal menulis berkas akun: {str(e)[:80]}"}


def _n_admin(users: dict) -> int:
    return sum(1 for u in users.values() if u.get("admin"))


def _daftar(users: dict, sess_user: str) -> list[dict]:
    out = []
    for akun in sorted(users):
        rec = users[akun]
        out.append({
            "akun": akun,
            "nama": rec.get("nama") or akun,
            "email": rec.get("email") or "",
            "admin": bool(rec.get("admin")) or is_admin(users, akun),
            "dibuat": rec.get("dibuat") or "",
            "oleh": rec.get("oleh") or "",
            "diri_sendiri": akun == sess_user,
            "punya_sandi": bool(rec.get("hash")),
        })
    return out


# ============================================================
# HALAMAN
# ============================================================

@router.get("/akun", response_class=HTMLResponse)
async def halaman(request: Request, sess: Session = Depends(current_session),
                  settings: Settings = Depends(get_settings)):
    users = load_users(settings.users_file)
    return templates.TemplateResponse(request, "admin.html", {
        "sess": sess,
        "admin": is_admin(users, sess.user),
        "domain_google": settings.google_domain,
        "berkas": str(settings.users_file),
    })


@router.get("/api/akun/daftar")
async def daftar(sess: Session = Depends(current_session_api),
                 settings: Settings = Depends(get_settings)):
    try:
        users = _wajib_admin(sess, settings)
    except Tolak as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "akun": _daftar(users, sess.user),
            "domain_google": settings.google_domain,
            "n_admin": _n_admin(users) or 1}


# ============================================================
# OPERASI
# ============================================================

def _tambah(settings: Settings, oleh: str, nama: str, sandi: str,
            email: str, admin: bool) -> dict:
    users = load_users(settings.users_file)
    # Diperiksa SEBELUM di-slug. safe_slug punya nilai cadangan "tanpa-nama",
    # jadi nama kosong tidak pernah menghasilkan slug kosong — ia diam-diam
    # membuat akun bernama "tanpa-nama".
    if not (nama or "").strip():
        raise Tolak("nama akun masih kosong")
    akun = user_slug(nama)
    if not akun or akun == "tanpa-nama":
        raise Tolak("nama akun kosong atau seluruhnya karakter terlarang")
    if akun in users:
        raise Tolak(f"akun '{akun}' sudah ada")
    email = (email or "").strip()
    if email and not _EMAIL.match(email):
        raise Tolak(f"'{email}' tidak terlihat seperti alamat email")
    if email and any(str(u.get("email") or "").lower() == email.lower()
                     for u in users.values()):
        raise Tolak(f"email {email} sudah dipakai akun lain")
    if len(sandi or "") < MIN_SANDI:
        raise Tolak(f"kata sandi minimal {MIN_SANDI} karakter")

    users[akun] = {"hash": hash_password(sandi), "nama": nama.strip() or akun,
                   "email": email, "admin": bool(admin),
                   "dibuat": date.today().isoformat(), "oleh": oleh}
    save_users(settings.users_file, users)
    log.info("akun dibuat: %r oleh %r (admin=%s, email=%r)",
             akun, oleh, bool(admin), email)
    return {"akun": akun}


def _ubah(settings: Settings, oleh: str, akun: str, email=None,
          admin=None, sandi=None) -> dict:
    users = load_users(settings.users_file)
    akun = user_slug(akun)
    if akun not in users:
        raise Tolak(f"akun '{akun}' tidak ada")
    rec = dict(users[akun])

    if email is not None:
        email = (email or "").strip()
        if email and not _EMAIL.match(email):
            raise Tolak(f"'{email}' tidak terlihat seperti alamat email")
        bentrok = [a for a, u in users.items()
                   if a != akun and str(u.get("email") or "").lower() == email.lower()]
        if email and bentrok:
            raise Tolak(f"email {email} sudah dipakai akun '{bentrok[0]}'")
        rec["email"] = email

    if admin is not None:
        admin = bool(admin)
        # Menurunkan admin terakhir mengunci semua orang di luar halaman ini.
        if not admin and rec.get("admin") and _n_admin(users) <= 1:
            raise Tolak("ini admin terakhir; angkat admin lain lebih dulu")
        rec["admin"] = admin

    if sandi is not None:
        if len(sandi) < MIN_SANDI:
            raise Tolak(f"kata sandi minimal {MIN_SANDI} karakter")
        rec["hash"] = hash_password(sandi)

    users[akun] = rec
    save_users(settings.users_file, users)
    log.info("akun diubah: %r oleh %r (%s)", akun, oleh,
             ", ".join(k for k, v in (("email", email), ("admin", admin),
                                      ("sandi", sandi)) if v is not None))
    return {"akun": akun}


def _hapus(settings: Settings, oleh: str, akun: str) -> dict:
    users = load_users(settings.users_file)
    akun = user_slug(akun)
    if akun not in users:
        raise Tolak(f"akun '{akun}' tidak ada")
    if akun == oleh:
        raise Tolak("tidak bisa menghapus akunmu sendiri")
    if users[akun].get("admin") and _n_admin(users) <= 1:
        raise Tolak("ini admin terakhir; angkat admin lain lebih dulu")
    users.pop(akun)
    save_users(settings.users_file, users)
    log.warning("akun DIHAPUS: %r oleh %r (%s akun tersisa)",
                akun, oleh, len(users))
    return {"akun": akun, "sisa": len(users)}


@router.post("/api/akun/tambah")
async def tambah(nama: str = "", sandi: str = "", email: str = "",
                 admin: int = 0, sess: Session = Depends(current_session_api),
                 settings: Settings = Depends(get_settings)):
    try:
        _wajib_admin(sess, settings)
    except Tolak as e:
        return {"ok": False, "error": str(e)}
    return await asyncio.to_thread(_jawab, _tambah, settings, sess.user,
                                   nama, sandi, email, bool(admin))


@router.post("/api/akun/ubah")
async def ubah(akun: str = "", email: str | None = None,
               admin: int | None = None, sandi: str | None = None,
               sess: Session = Depends(current_session_api),
               settings: Settings = Depends(get_settings)):
    try:
        _wajib_admin(sess, settings)
    except Tolak as e:
        return {"ok": False, "error": str(e)}
    return await asyncio.to_thread(
        _jawab, _ubah, settings, sess.user, akun, email,
        None if admin is None else bool(admin), sandi)


@router.post("/api/akun/hapus")
async def hapus(akun: str = "", sess: Session = Depends(current_session_api),
                settings: Settings = Depends(get_settings)):
    try:
        _wajib_admin(sess, settings)
    except Tolak as e:
        return {"ok": False, "error": str(e)}
    return await asyncio.to_thread(_jawab, _hapus, settings, sess.user, akun)
