"""
Unggah gambar dari laptop pemakai.

Dikirim satu berkas per permintaan PUT dengan bodi mentah, bukan satu form
multipart berisi ratusan berkas. Alasannya: bodi dialirkan langsung ke disk
sehingga memori server tidak menumpuk, progres bisa dihitung per berkas, dan
satu berkas gagal tidak menggagalkan seluruh batch.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request

from ..config import Settings, get_settings
from ..deps import current_session_api
from ..security import safe_filename, safe_slug
from ..session import Session

router = APIRouter(tags=["uploads"])

CHUNK = 256 * 1024


@router.put("/upload")
async def upload(request: Request, ds: str = "", name: str = "",
                 sess: Session = Depends(current_session_api),
                 settings: Settings = Depends(get_settings)):
    fn = safe_filename(name)
    if not fn:
        return {"ok": False, "error": "nama atau jenis berkas tidak didukung"}

    try:
        total = int(request.headers.get("content-length", 0))
    except ValueError:
        total = 0
    if total <= 0:
        return {"ok": False, "error": "berkas kosong"}
    if total > settings.max_upload_bytes:
        return {"ok": False,
                "error": f"lebih dari {settings.max_upload_mb} MB"}

    d = sess.upload_dir(ds)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / fn
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
                if written > settings.max_upload_bytes:
                    raise ValueError(f"lebih dari {settings.max_upload_mb} MB")
                f.write(chunk)
        if written == 0:
            raise ValueError("tidak ada data yang diterima")
        tmp.replace(dest)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return {"ok": False, "error": str(e)[:90]}

    return {"ok": True, "name": fn, "bytes": written}


@router.post("/useupload")
async def use_upload(ds: str = "", sess: Session = Depends(current_session_api)):
    """Buka folder hasil unggahan sebagai dataset yang sedang diperiksa."""
    d = sess.upload_dir(ds)
    if not d.is_dir():
        return {"ok": False, "error": "folder unggahan belum ada"}
    n = len(await asyncio.to_thread(sess.load, d))
    if not n:
        return {"ok": False, "error": "tidak ada gambar terbaca di unggahan itu"}
    return {"ok": True, "dir": str(d), "n": n, "nama": safe_slug(ds)}
