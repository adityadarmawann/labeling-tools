"""Memilih dataset: daftar dari folder induk, path bebas, atau dialog desktop."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from ..config import Settings, get_settings
from ..deps import current_session, current_session_api, is_local, require_local
from ..services import anylabeling, export, riwayat, scanner
from ..session import Session
from ..templating import templates

router = APIRouter(tags=["datasets"])


def picker_context(request: Request, sess: Session, settings: Settings,
                   error: str | None = None) -> dict:
    return {
        "sess": sess,
        "local": is_local(request),
        "error": error,
        "datasets": scanner.list_dirs(settings.datasets_root),
        "unggahan": scanner.list_dirs(settings.uploads_root / sess.user),
        "datasets_root": settings.datasets_root,
        "max_upload_mb": settings.max_upload_mb,
        "max_zip_mb": settings.max_zip_mb,
        "riwayat": riwayat.baca(settings, sess.user),
    }


@router.get("/pilih", response_class=HTMLResponse)
async def picker(request: Request,
                 sess: Session = Depends(current_session),
                 settings: Settings = Depends(get_settings)):
    return templates.TemplateResponse(request, "pick.html",
                                      picker_context(request, sess, settings))


@router.post("/setsrc")
async def set_source(path: str = "",
                     sess: Session = Depends(current_session_api),
                     settings: Settings = Depends(get_settings)):
    raw = (path or "").strip()
    if not raw:
        return {"ok": False, "error": "path masih kosong"}
    d = Path(raw).expanduser()
    if not d.is_dir():
        return {"ok": False, "error": "folder tidak ada di server"}
    n = len(await asyncio.to_thread(sess.load, d))
    if not n:
        return {"ok": False, "error": "tidak ada gambar terbaca di folder itu"}
    riwayat.catat(settings, sess.user, d.resolve(), "buka")
    return {"ok": True, "dir": str(d.resolve()), "n": n}


@router.post("/lupakan-path")
async def lupakan_path(path: str = "",
                       sess: Session = Depends(current_session_api),
                       settings: Settings = Depends(get_settings)):
    """Buang satu baris dari riwayat. Hanya catatannya — foldernya tidak
    disentuh sama sekali, dan itu perlu dinyatakan supaya tombolnya tidak
    terbaca sebagai 'hapus dataset'."""
    riwayat.lupakan(settings, sess.user, (path or "").strip())
    return {"ok": True}


@router.post("/rescan")
async def rescan(sess: Session = Depends(current_session_api)):
    if sess.src is None:
        return {"ok": False, "error": "belum ada dataset yang dibuka"}
    n = len(await asyncio.to_thread(sess.reload))
    return {"ok": True, "n": n}


@router.post("/pickdir", dependencies=[Depends(require_local)])
async def pick_dir(sess: Session = Depends(current_session_api)):
    """Dialog folder milik sistem — hanya untuk akses dari mesin server."""
    start = str(sess.src or Path.home())
    path, err = await asyncio.to_thread(anylabeling.pick_dir, start)
    if not path:
        return {"ok": False, "error": err or "dibatalkan"}
    d = Path(path)
    if not d.is_dir():
        return {"ok": False, "error": "bukan folder"}
    n = len(await asyncio.to_thread(sess.load, d))
    return {"ok": True, "dir": str(d), "n": n}


@router.get("/api/ekspor/ringkasan")
async def ekspor_ringkasan(format: str = "yolo-seg", split: str = "",
                           sess: Session = Depends(current_session_api)):
    """Angka yang ditampilkan sebelum orang menekan unduh."""
    if sess.src is None:
        return {"ok": False, "error": "belum ada dataset terbuka"}
    if format not in export.FORMAT:
        return {"ok": False, "error": f"format '{format}' tidak dikenal"}
    # Kuncinya HANYA menyelimuti penyalinan daftarnya, tidak sampai ke await.
    #
    # sess.lock adalah threading.Lock, dan menahannya melewati await mematikan
    # seluruh server: permintaan kedua memanggil acquire() di thread event loop,
    # thread itu berhenti, dan pemegang kuncinya tidak akan pernah bisa
    # dilanjutkan untuk melepasnya — karena yang melanjutkannya adalah event
    # loop yang sudah berhenti itu. Servernya membeku di 0% CPU sampai
    # direstart. Cukup dua permintaan ringkasan bertumpang, misalnya karena
    # kotak rasio diubah selagi hitungan pertama masih jalan.
    #
    # `sess.names` dibawa serta supaya indeks kelas mengikuti urutan data.yaml
    # dataset sumbernya, bukan diturunkan ulang dari label yang kebetulan ada
    # di seleksi ini.
    with sess.lock:
        items = list(sess.items)
        names = dict(sess.names)
    r = await asyncio.to_thread(export.ringkasan, items, format == "yolo-seg",
                                export.baca_rasio(split), names)
    return {"ok": True, "format": export.FORMAT[format], **r}


@router.get("/ekspor")
async def ekspor(format: str = "yolo-seg", gambar: int = 1, split: str = "",
                 sess: Session = Depends(current_session)):
    """
    Unduh dataset sebagai ZIP bertata letak ultralytics.

    Dibuat di memori lalu dikirim sekali jalan: dataset tim ini ukurannya
    ribuan gambar, bukan ratusan ribu, jadi tidak perlu berkas sementara di
    disk yang harus dibersihkan.
    """
    if sess.src is None:
        return Response("belum ada dataset terbuka", status_code=400,
                        media_type="text/plain; charset=utf-8")
    if format not in export.FORMAT:
        return Response("format tidak dikenal", status_code=400,
                        media_type="text/plain; charset=utf-8")
    nama = sess.src.name
    with sess.lock:
        items = list(sess.items)
        names = dict(sess.names)
    data = await asyncio.to_thread(export.zip_dataset, items, nama, format,
                                   bool(gambar), export.baca_rasio(split), names)
    berkas = f"{nama}-{format}.zip"
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{berkas}"',
        "Content-Length": str(len(data)),
    })
