"""
Unggah gambar dari laptop pemakai.

Dikirim satu berkas per permintaan PUT dengan bodi mentah, bukan satu form
multipart berisi ratusan berkas. Alasannya: bodi dialirkan langsung ke disk
sehingga memori server tidak menumpuk, progres bisa dihitung per berkas, dan
satu berkas gagal tidak menggagalkan seluruh batch.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, Request

from ..config import ARSIP_EXT, Settings, get_settings
from ..deps import current_session_api
from ..security import safe_relpath, safe_slug
from ..services import arsip, impor, riwayat, scanner
from ..session import Session

router = APIRouter(tags=["uploads"])

CHUNK = 256 * 1024


@router.put("/upload")
async def upload(request: Request, ds: str = "", name: str = "",
                 sess: Session = Depends(current_session_api),
                 settings: Settings = Depends(get_settings)):
    # `name` boleh memuat subfolder (unggahan folder mengirim
    # webkitRelativePath), dan strukturnya dipertahankan.
    #
    # Arsip diizinkan di SINI saja, bukan di dalam isi arsip: `arsip.bongkar`
    # memanggil safe_relpath tanpa izin itu, sehingga zip di dalam zip
    # dilewati dan pembongkarannya tidak pernah berlapis.
    fn = safe_relpath(name, arsip=True)
    if not fn:
        return {"ok": False, "error": "nama atau jenis berkas tidak didukung"}

    batas, sebutan = settings.batas_untuk(fn)
    try:
        total = int(request.headers.get("content-length", 0))
    except ValueError:
        total = 0
    if total <= 0:
        return {"ok": False, "error": "berkas kosong"}
    if total > batas:
        return {"ok": False, "error": f"lebih dari {sebutan}"}

    d = sess.upload_dir(ds)
    dest = d / fn
    # Penjagaan berlapis: walau safe_relpath sudah membuang `..`, tujuan akhirnya
    # tetap diperiksa masih berada di dalam folder milik akun ini.
    try:
        dest.resolve().relative_to(d.resolve().parent.resolve() / d.name)
    except ValueError:
        return {"ok": False, "error": "tujuan di luar folder unggahan"}
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Tulis ke .part dulu, ganti nama setelah lengkap, supaya koneksi yang
    # terputus tidak meninggalkan berkas setengah jadi yang ikut terpindai.
    tmp = dest.with_suffix(dest.suffix + ".part")

    written = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                if not chunk:
                    continue
                written += len(chunk)
                if written > batas:
                    raise ValueError(f"lebih dari {sebutan}")
                f.write(chunk)
        if written == 0:
            raise ValueError("tidak ada data yang diterima")
        tmp.replace(dest)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return {"ok": False, "error": str(e)[:90]}

    return {"ok": True, "name": fn, "bytes": written,
            "arsip": Path(fn).suffix.lower() in ARSIP_EXT}


@router.post("/unzip")
async def unzip(ds: str = "", name: str = "",
                sess: Session = Depends(current_session_api),
                settings: Settings = Depends(get_settings)):
    """
    Bongkar arsip yang sudah terunggah, di tempat.

    Dipisah dari /upload supaya kegagalan bisa dibedakan: arsip yang terkirim
    utuh tetapi isinya ditolak berbeda dari arsip yang gagal terkirim, dan
    keduanya perlu pesan yang berbeda. Pemisahan ini juga membuat unggahan
    besar tidak menahan koneksi selama pembongkaran.
    """
    fn = safe_relpath(name, arsip=True)
    if not fn or Path(fn).suffix.lower() not in ARSIP_EXT:
        return {"ok": False, "error": "yang diminta bukan berkas arsip"}

    d = sess.upload_dir(ds)
    zp = d / fn
    if not zp.is_file():
        return {"ok": False, "error": "arsipnya tidak ada di folder unggahan"}

    try:
        hasil = await asyncio.to_thread(
            arsip.bongkar, zp, d,
            maks_byte=settings.max_zip_bytes * settings.zip_ratio_max,
            maks_entri=settings.zip_entries_max)
    except arsip.ArsipTolak as e:
        return {"ok": False, "error": str(e)[:140]}
    except OSError as e:
        return {"ok": False, "error": f"gagal membongkar: {str(e)[:80]}"}

    if not hasil["ditulis"]:
        return {"ok": False,
                "error": "tidak ada berkas yang bisa dipakai di dalam arsip itu"}

    # Arsipnya dibuang setelah isinya selamat: menyimpan salinan 1 GB yang
    # sudah ada bentuk terbongkarnya hanya menghabiskan disk, dan berkas itu
    # tidak pernah ikut terbaca sebagai bagian dataset.
    zp.unlink(missing_ok=True)
    return {"ok": True, "n": hasil["ditulis"], "dilewati": hasil["dilewati"],
            "bytes": hasil["bytes"], "contoh_dilewati": hasil["contoh_dilewati"],
            "arsip_dihapus": True}


@router.get("/api/impor/survei")
async def impor_survei(path: str = "",
                       sess: Session = Depends(current_session_api)):
    """Berapa banyak dan berapa besar yang akan disalin — sebelum menyalin."""
    d = Path((path or "").strip()).expanduser()
    if not d.is_dir():
        return {"ok": False, "error": "folder tidak ada di server"}
    s = await asyncio.to_thread(impor.survei, d)
    return {"ok": True, "nama_usul": safe_slug(d.name), **s}


@router.post("/impor")
async def impor_dari_server(path: str = "", ds: str = "",
                            sess: Session = Depends(current_session_api),
                            settings: Settings = Depends(get_settings)):
    """
    Salin dataset dari sebuah path di server ke folder unggahan akun ini.

    Alur yang sama dengan unggah dari laptop, hanya sumbernya berbeda. Bedanya
    dengan /setsrc penting: /setsrc membuka folder ITU JUGA, sehingga menyunting
    berarti mengubah dataset aslinya. Di sini yang dibuka adalah SALINAN, dan
    folder sumber tidak pernah ditulis sama sekali.
    """
    sumber = Path((path or "").strip()).expanduser()
    nama = safe_slug(ds or sumber.name)
    tujuan = sess.upload_dir(nama)
    try:
        hasil = await asyncio.to_thread(impor.impor_folder, sumber, tujuan)
    except impor.ImporTolak as e:
        return {"ok": False, "error": str(e)[:160]}
    except OSError as e:
        return {"ok": False, "error": f"gagal menyalin: {str(e)[:90]}"}

    n = len(await asyncio.to_thread(sess.load, tujuan))
    peringatan = await asyncio.to_thread(scanner.periksa_kelengkapan, tujuan)
    riwayat.catat(settings, sess.user, sumber.resolve(), "salin")
    return {"ok": True, "nama": nama, "dir": str(tujuan), "n": n,
            "disalin": hasil["berkas"], "dilewati": hasil["dilewati"],
            "bytes": hasil["bytes"], "peringatan": peringatan,
            "contoh_dilewati": hasil["contoh_dilewati"],
            "bentrok": hasil["bentrok"]}


@router.post("/useupload")
async def use_upload(ds: str = "", sess: Session = Depends(current_session_api)):
    """Buka folder hasil unggahan sebagai dataset yang sedang diperiksa."""
    d = sess.upload_dir(ds)
    if not d.is_dir():
        return {"ok": False, "error": "folder unggahan belum ada"}
    n = len(await asyncio.to_thread(sess.load, d))
    if not n:
        return {"ok": False, "error": "tidak ada gambar terbaca di unggahan itu"}
    # Peringatan dikirim bersama hasil, bukan sebagai kegagalan: datasetnya
    # tetap bisa dibuka, hanya ada yang perlu diketahui lebih dulu.
    peringatan = await asyncio.to_thread(scanner.periksa_kelengkapan, d)
    return {"ok": True, "dir": str(d), "n": n, "nama": safe_slug(ds),
            "peringatan": peringatan}
