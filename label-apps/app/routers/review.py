"""Papan periksa: grid, tampilan besar, thumbnail, tandai latar."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import Settings, get_settings
from ..deps import current_session, current_session_api, is_local, require_local
from ..services import annotations, anylabeling, render, scanner
from ..services.annotations import Menolak
from ..session import Session
from ..templating import templates
from .datasets import picker_context

router = APIRouter(tags=["review"])

THUMB_MIN, THUMB_MAX = 32, 2000
STRIP_MAX = 400          # tick di strip kesehatan dataset


def _filter(items: list[dict], flt: str, kelas: str | None) -> list[dict]:
    if flt == "issue":
        items = [i for i in items if i["issues"] and i["shapes"]]
    elif flt == "unlab":
        # severity 'stop', bukan sekadar "tanpa objek": gambar yang sudah
        # ditandai latar memang tanpa objek tapi sudah selesai diperiksa, dan
        # angka di chip "Belum dilabeli" juga menghitung 'stop'. Kalau di sini
        # dipakai "tanpa objek", jumlah di chip tidak sama dengan isi grid.
        items = [i for i in items if scanner.severity(i) == "stop"]
    if kelas:
        items = [i for i in items
                 if any(str(s["label"]) == kelas for s in i["shapes"])]
    return items


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, f: str = "all", c: str | None = None,
                sess: Session = Depends(current_session),
                settings: Settings = Depends(get_settings)):
    # Belum memilih dataset -> tampilkan pemilih, bukan grid kosong.
    if sess.src is None:
        return templates.TemplateResponse(request, "pick.html",
                                          picker_context(request, sess, settings))

    with sess.lock:
        items = list(sess.items)

    kelas_hitung: dict[str, int] = {}
    for it in items:
        for s in it["shapes"]:
            k = str(s["label"])
            kelas_hitung[k] = kelas_hitung.get(k, 0) + 1

    sev = [scanner.severity(i) for i in items]
    return templates.TemplateResponse(request, "index.html", {
        "sess": sess,
        "local": is_local(request),
        "items": _filter(items, f, c),
        "severity": scanner.severity,
        "strip": sev[:STRIP_MAX],
        "flt": f,
        "kelas": c,
        "total": len(items),
        "n_warn": sum(1 for s in sev if s == "warn"),
        "n_stop": sum(1 for s in sev if s == "stop"),
        "n_obj": sum(len(i["shapes"]) for i in items),
        "kelas_hitung": dict(sorted(kelas_hitung.items())),
    })


@router.get("/view", response_class=HTMLResponse)
async def view(request: Request, path: str = "",
               sess: Session = Depends(current_session)):
    it = sess.find(path)
    if not it:
        return templates.TemplateResponse(
            request, "notfound.html", {"sess": sess},
            status_code=404)
    with sess.lock:
        items = sess.items
        i = items.index(it)
        prev_it = items[i - 1] if i > 0 else None
        next_it = items[i + 1] if i < len(items) - 1 else None
        posisi = (i + 1, len(items))

    hitung: dict[str, int] = {}
    for s in it["shapes"]:
        k = str(s["label"])
        hitung[k] = hitung.get(k, 0) + 1

    return templates.TemplateResponse(request, "view.html", {
        "sess": sess, "local": is_local(request), "it": it,
        "sev": scanner.severity(it), "prev_it": prev_it, "next_it": next_it,
        "posisi": posisi, "hitung": dict(sorted(hitung.items())),
    })


@router.get("/thumb")
async def thumb(path: str = "", s: int = 320,
                sess: Session = Depends(current_session)):
    it = sess.find(path)
    if not it:
        return Response(status_code=404)
    side = min(max(s, THUMB_MIN), THUMB_MAX)
    tp = await asyncio.to_thread(render.thumb_path, sess, it, side)
    if not tp:
        return Response(status_code=404)
    return Response(tp.read_bytes(), media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=60"})


@router.post("/markbg")
async def mark_bg(path: str = "", sess: Session = Depends(current_session_api)):
    return _set_bg(sess, path, True)


@router.post("/unmarkbg")
async def unmark_bg(path: str = "", sess: Session = Depends(current_session_api)):
    return _set_bg(sess, path, False)


def _set_bg(sess: Session, path: str, on: bool):
    it = sess.find(path)
    if not it:
        return {"ok": False, "error": "berkas tidak dikenal di dataset ini"}
    try:
        with sess.lock:
            if on:
                annotations.mark_background(it)
                msg = "ditandai sebagai latar"
            else:
                annotations.unmark_background(it)
                msg = "tanda latar dilepas"
            sess.drop_thumbs_for(it)
    except Menolak as e:
        return {"ok": False, "error": str(e)}
    except OSError as e:
        return {"ok": False, "error": str(e)[:90]}
    return {"ok": True, "msg": msg}


@router.post("/open", dependencies=[Depends(require_local)])
async def open_in_anylabeling(path: str = "",
                              sess: Session = Depends(current_session_api),
                              settings: Settings = Depends(get_settings)):
    """Jalankan AnyLabeling di mesin server — hanya untuk akses lokal."""
    it = sess.find(path)
    if not it:
        return {"ok": False, "error": "berkas tidak dikenal di dataset ini"}
    try:
        anylabeling.launch(sess, it["img"])
    except FileNotFoundError:
        return {"ok": False,
                "error": f"perintah '{settings.anylabeling}' tidak ditemukan"}
    except OSError as e:
        return {"ok": False, "error": str(e)[:90]}
    msg = ("folder dibuka — pakai A / D untuk pindah gambar"
           if settings.open_mode == "dir" else it["img"].name)
    return {"ok": True, "msg": msg}
