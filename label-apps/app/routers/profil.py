"""
Halaman akun DIRI SENDIRI (/profil).

KENAPA TERPISAH DARI /akun
--------------------------
"Kelola member" (/akun, admin.py) KHUSUS admin dan mengurus SEMUA akun. Halaman
ini kebalikannya: untuk SIAPA SAJA yang sudah masuk, dan hanya atas AKUNNYA
SENDIRI — nama tampil, email, foto, dan sandi. Tidak ada satu pun rute di sini
yang menerima nama akun lain sebagai sasaran: semuanya menulis tepat
`users[sess.user]`. Itu penjagaan utamanya, dibuat di struktur rutenya, bukan
ditambal dengan pemeriksaan: tidak ada jalan menyebut akun orang lain, jadi
tidak ada yang perlu ditolak.

Aturan email dan sandi dipinjam utuh dari admin.py (_EMAIL, MIN_SANDI, Tolak,
_jawab) supaya keduanya tidak pernah berbeda antara "admin mengubah akun orang"
dan "orang mengubah akunnya sendiri".
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse

from ..config import IMG_EXT, Settings, get_settings
from ..deps import current_session, current_session_api
from ..log import catat
from ..security import (hash_password, load_users, save_users, user_slug,
                        verify_password)
from ..session import Session
from ..templating import templates
# Dipinjam utuh dari halaman kelola member supaya aturannya tidak pernah
# bercabang: email yang sah di satu halaman sah pula di sini, sandi yang cukup
# panjang di satu halaman cukup pula di sini.
from .admin import MIN_SANDI, Tolak, _EMAIL, _jawab

router = APIRouter(tags=["profil"])
log = catat("labelapp.profil")

# Foto profil dinormalkan jadi kotak kecil JPEG: ukuran unggahan dibatasi kecil
# (ini bukan dataset), dan sisi 256 px sudah lebih dari cukup untuk avatar
# 23 px di kepala maupun pratinjau di halaman ini.
MAKS_FOTO_MB = 4
MAKS_FOTO_BYTES = MAKS_FOTO_MB * 1024 * 1024
SISI_FOTO = 256
# Satu nama berkas per akun — avatar lama tertimpa, tak menumpuk. Selalu .jpg
# karena isinya SELALU dikodekan ulang jadi JPEG (lihat _olah_foto), apa pun
# format yang diunggah.
FOTO_EXT = ".jpg"


# ============================================================
# LETAK & PENYAJIAN AVATAR
# ============================================================

def _avatar_dir(settings: Settings) -> Path:
    """Folder avatar, dari setelan. Tidak dibuat di sini — pembuatnya penulis."""
    return Path(settings.avatar_root)


def _avatar_path(settings: Settings, akun: str) -> Path | None:
    """
    Path berkas avatar sebuah akun, ATAU None kalau namanya mencurigakan.

    `akun` di-slug lebih dulu (user_slug membuang '/', '..', dan apa pun di luar
    [a-z0-9._-]), jadi nama seperti "../../etc/passwd" menjadi slug datar yang
    tak bisa keluar folder. Lapis kedua: path akhirnya tetap diperiksa benar-
    benar berada DI DALAM folder avatar — sabuk dan bretel, sama seperti jalur
    unggah.
    """
    slug = user_slug(akun)
    if not slug or slug == "tanpa-nama":
        return None
    root = _avatar_dir(settings)
    p = (root / f"{slug}{FOTO_EXT}")
    try:
        p.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return p


@router.get("/profil/foto")
async def foto(request: Request, akun: str = "",
               sess: Session = Depends(current_session),
               settings: Settings = Depends(get_settings)):
    """
    Sajikan byte avatar sebuah akun.

    Izin: SIAPA SAJA yang sudah masuk boleh MELIHAT avatar — ia muncul di kepala
    tiap halaman dan (nanti) di daftar anggota, jadi membatasinya ke pemilik
    tidak masuk akal. Yang dibatasi ke pemilik adalah MENGUBAHnya, dan itu
    dijaga di rute tulis, bukan di sini. current_session (bukan _api) dipakai
    supaya permintaan <img> tanpa sesi dialihkan ke /login seperti /thumb,
    bukan menjawab 401 mentah.
    """
    p = _avatar_path(settings, akun)
    if p is None or not p.is_file():
        return Response(status_code=404)
    # max-age pendek + private: avatar jarang berubah, tetapi kalau diganti,
    # URL-nya membawa cap `v=<foto>` baru dari kepala sehingga peramban tetap
    # mengambil yang baru. private: avatar tak boleh disimpan proxy bersama.
    return Response(p.read_bytes(), media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=300"})


# ============================================================
# HALAMAN
# ============================================================

@router.get("/profil", response_class=HTMLResponse)
async def halaman(request: Request, sess: Session = Depends(current_session),
                  settings: Settings = Depends(get_settings)):
    # users.json dibaca sekali di sini HANYA untuk email — nama & foto sudah
    # ada di sesi. Ini halaman yang jarang dibuka (bukan jalur panas seperti
    # grid/thumb), jadi satu pembacaan di sini tidak melanggar aturan "jangan
    # baca users.json tiap permintaan" yang menjaga halaman-halaman ramai.
    users = load_users(settings.users_file)
    rec = users.get(sess.user) or {}
    return templates.TemplateResponse(request, "profil.html", {
        "sess": sess,
        "nama": sess.nama or sess.user,
        "email": rec.get("email") or "",
        # Akun hasil login Google kelak bisa tak punya sandi sama sekali; borang
        # ganti sandi menyesuaikan (minta "buat sandi", bukan "sandi sekarang").
        "punya_sandi": bool(rec.get("hash")),
        "min_sandi": MIN_SANDI,
        "maks_foto_mb": MAKS_FOTO_MB,
    })


# ============================================================
# OPERASI — SELALU atas users[sess.user], tak pernah akun lain
# ============================================================

def _ubah_profil(settings: Settings, akun: str, nama: str, email: str) -> dict:
    """Ubah nama tampil + email akun INI. Tak menyentuh `admin` atau akun lain."""
    users = load_users(settings.users_file)
    if akun not in users:
        raise Tolak("akunmu tidak ada lagi. Coba masuk ulang")
    rec = dict(users[akun])

    email = (email or "").strip()
    if email and not _EMAIL.match(email):
        raise Tolak(f"'{email}' tidak terlihat seperti alamat email")
    # Keunikan email DIKECUALIKAN diri sendiri — persis admin.py _ubah: email
    # sendiri yang tidak berubah bukan "bentrok".
    bentrok = [a for a, u in users.items()
               if a != akun and str(u.get("email") or "").lower() == email.lower()]
    if email and bentrok:
        raise Tolak(f"email {email} sudah dipakai akun lain")

    # Nama dirapikan; kosong jatuh ke slug akun supaya kepala tak pernah hampa.
    rec["nama"] = (nama or "").strip() or akun
    rec["email"] = email
    users[akun] = rec
    save_users(settings.users_file, users)
    log.info("profil diubah sendiri: %r (email=%r)", akun, email)
    return {"nama": rec["nama"], "email": email}


def _ubah_sandi(settings: Settings, akun: str, lama: str, baru: str) -> dict:
    """
    Ganti sandi akun INI. Wajib menyertakan sandi SEKARANG.

    Meminta sandi sekarang adalah penjaga swalayan yang benar: tanpa itu,
    siapa pun yang menemukan sesi terbuka (laptop tak terkunci) bisa mengunci
    pemiliknya keluar dengan mengganti sandinya. Admin yang mereset sandi orang
    TIDAK lewat sini — ia tak punya sandi lama orang itu — melainkan lewat
    /akun, dan di sanalah persetujuannya: ia memang berwenang.
    """
    users = load_users(settings.users_file)
    rec = users.get(akun)
    if not rec:
        raise Tolak("akunmu tidak ada lagi. Coba masuk ulang")
    if not verify_password(lama or "", rec.get("hash") or ""):
        raise Tolak("kata sandi sekarang salah")
    if len(baru or "") < MIN_SANDI:
        raise Tolak(f"kata sandi baru minimal {MIN_SANDI} karakter")
    rec = dict(rec)
    rec["hash"] = hash_password(baru)
    users[akun] = rec
    save_users(settings.users_file, users)
    log.info("sandi diubah sendiri: %r", akun)
    return {}


def _olah_foto(data: bytes) -> bytes:
    """
    Byte gambar apa adanya -> JPEG kotak kecil.

    Dikodekan ulang, bukan disimpan mentah, dan itu sekaligus VALIDASINYA:
    cv2.imdecode mengembalikan None untuk apa pun yang bukan gambar, jadi berkas
    bukan-gambar ditolak di sini tanpa daftar tipe terpisah. Dipotong ke kotak
    dari tengah lalu diperkecil, supaya avatar bulat di kepala tidak gepeng
    apa pun rasio aslinya.
    """
    import cv2
    import numpy as np

    arr = np.frombuffer(data, np.uint8)
    im = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if im is None:
        raise Tolak("berkas itu tidak terbaca sebagai gambar")
    h, w = im.shape[:2]
    sisi = min(h, w)
    y0, x0 = (h - sisi) // 2, (w - sisi) // 2
    im = im[y0:y0 + sisi, x0:x0 + sisi]
    im = cv2.resize(im, (SISI_FOTO, SISI_FOTO), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", im, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    if not ok:
        raise Tolak("gagal mengolah gambar")
    return buf.tobytes()


def _simpan_foto(settings: Settings, akun: str, data: bytes) -> dict:
    """Olah + tulis avatar akun INI, lalu catat capnya di users.json."""
    jpg = _olah_foto(data)
    p = _avatar_path(settings, akun)
    if p is None:
        raise Tolak("nama akun tidak sah")
    p.parent.mkdir(parents=True, exist_ok=True)
    # Tulis ke .part lalu ganti nama: dua permintaan untuk akun yang sama tak
    # boleh membuat yang kedua membaca berkas setengah tertulis. Sama polanya
    # dengan penulis thumbnail & unggahan.
    tmp = p.with_suffix(p.suffix + ".part")
    try:
        tmp.write_bytes(jpg)
        tmp.replace(p)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    # `foto` = cap isi, dipakai dua hal: penanda "ada foto" (truthy) dan pemutus
    # cache (ikut sebagai ?v= di URL avatar). Berubah tepat saat gambarnya
    # berubah, jadi peramban tak pernah menampilkan avatar lama.
    cap = hashlib.sha256(jpg).hexdigest()[:12]
    users = load_users(settings.users_file)
    if akun not in users:
        raise Tolak("akunmu tidak ada lagi. Coba masuk ulang")
    rec = dict(users[akun])
    rec["foto"] = cap
    users[akun] = rec
    save_users(settings.users_file, users)
    log.info("foto profil diperbarui sendiri: %r", akun)
    return {"foto": cap}


def _hapus_foto(settings: Settings, akun: str) -> dict:
    """Buang avatar akun INI, kembali ke inisial."""
    p = _avatar_path(settings, akun)
    if p is not None:
        p.unlink(missing_ok=True)
    users = load_users(settings.users_file)
    if akun in users and users[akun].get("foto"):
        rec = dict(users[akun])
        rec.pop("foto", None)
        users[akun] = rec
        save_users(settings.users_file, users)
    log.info("foto profil dihapus sendiri: %r", akun)
    return {"foto": ""}


@router.post("/api/profil")
async def ubah(nama: str = "", email: str = "",
               sess: Session = Depends(current_session_api),
               settings: Settings = Depends(get_settings)):
    hasil = await asyncio.to_thread(_jawab, _ubah_profil, settings,
                                    sess.user, nama, email)
    # Disegarkan DI TEMPAT, bukan dibaca ulang dari berkas: rutenya sudah
    # memegang sesinya, dan kepala halaman membaca sess.nama.
    if hasil.get("ok"):
        sess.nama = hasil.get("nama") or sess.user
    return hasil


@router.post("/api/profil/sandi")
async def sandi(sandi_lama: str = "", sandi_baru: str = "",
                sess: Session = Depends(current_session_api),
                settings: Settings = Depends(get_settings)):
    return await asyncio.to_thread(_jawab, _ubah_sandi, settings,
                                   sess.user, sandi_lama, sandi_baru)


@router.post("/api/profil/foto")
async def unggah_foto(request: Request, name: str = "",
                      sess: Session = Depends(current_session_api),
                      settings: Settings = Depends(get_settings)):
    """
    Unggah avatar untuk akun INI. Bodi mentah (satu berkas kecil), bukan
    multipart — konsisten dengan /upload dan cukup untuk berkas sekecil ini.
    """
    # Kalau nama berkas ikut dikirim, jenisnya disaring lebih dulu dengan daftar
    # yang sama seperti unggahan gambar (IMG_EXT) demi pesan yang ramah;
    # penyaring sesungguhnya tetap cv2.imdecode di _olah_foto.
    if name and Path(name).suffix.lower() not in IMG_EXT:
        return {"ok": False, "error": "pilih berkas gambar (jpg/png/webp)"}
    try:
        total = int(request.headers.get("content-length", 0))
    except ValueError:
        total = 0
    if total > MAKS_FOTO_BYTES:
        return {"ok": False, "error": f"foto lebih dari {MAKS_FOTO_MB} MB"}
    data = await request.body()
    if not data:
        return {"ok": False, "error": "tidak ada data yang diterima"}
    if len(data) > MAKS_FOTO_BYTES:
        return {"ok": False, "error": f"foto lebih dari {MAKS_FOTO_MB} MB"}
    hasil = await asyncio.to_thread(_jawab, _simpan_foto, settings,
                                    sess.user, data)
    if hasil.get("ok"):
        sess.foto = hasil.get("foto") or ""
    return hasil


@router.post("/api/profil/foto/hapus")
async def hapus_foto(sess: Session = Depends(current_session_api),
                     settings: Settings = Depends(get_settings)):
    hasil = await asyncio.to_thread(_jawab, _hapus_foto, settings, sess.user)
    if hasil.get("ok"):
        sess.foto = ""
    return hasil
