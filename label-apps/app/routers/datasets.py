"""Memilih dataset: daftar dari folder induk, path bebas, atau dialog desktop."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ..config import Settings, get_settings
from ..deps import current_session, current_session_api, is_local, require_local
from ..services import anylabeling, scanner
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
    }


@router.get("/pilih", response_class=HTMLResponse)
async def picker(request: Request,
                 sess: Session = Depends(current_session),
                 settings: Settings = Depends(get_settings)):
    return templates.TemplateResponse(request, "pick.html",
                                      picker_context(request, sess, settings))


@router.post("/setsrc")
async def set_source(path: str = "",
                     sess: Session = Depends(current_session_api)):
    raw = (path or "").strip()
    if not raw:
        return {"ok": False, "error": "path masih kosong"}
    d = Path(raw).expanduser()
    if not d.is_dir():
        return {"ok": False, "error": "folder tidak ada di server"}
    n = len(await asyncio.to_thread(sess.load, d))
    if not n:
        return {"ok": False, "error": "tidak ada gambar terbaca di folder itu"}
    return {"ok": True, "dir": str(d.resolve()), "n": n}


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
