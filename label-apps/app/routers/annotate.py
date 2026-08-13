"""
Halaman anotasi: kanvas di browser.

Tata letaknya mengikuti AnyLabeling — toolbar alat di kiri, kanvas di tengah,
panel Labels / Objects / Files di kanan — supaya yang sudah biasa dengan
aplikasi desktopnya tidak perlu belajar ulang.

Yang ditulis ke disk tetap `.json` labelme, jadi satu dataset bisa dibuka
bergantian di web ini dan di AnyLabeling desktop tanpa konversi.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse

from ..deps import current_session, current_session_api, is_local
from ..services import annotations, autolabel, scanner
from ..services.autolabel import TidakAdaObjek
from ..session import Session
from ..templating import templates

router = APIRouter(tags=["annotate"])

LABELME_VERSION = "0.4.36"

MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".bmp": "image/bmp", ".webp": "image/webp",
        ".tif": "image/tiff", ".tiff": "image/tiff"}


def daftar_kelas(sess: Session) -> list[str]:
    """Kelas yang sudah dipakai di dataset + kelas tambahan dari setelan."""
    dipakai = {str(s["label"]).strip() for it in sess.items for s in it["shapes"]
               if s["label"] is not None and str(s["label"]).strip()}
    return sorted(dipakai | set(sess.settings.extra_labels))


def bentuk_untuk_kanvas(it: dict) -> list[dict]:
    """Bentuk hasil pindai -> bentuk siap dikirim ke JavaScript."""
    return [{"label": "" if s["label"] is None else str(s["label"]),
             "shape_type": s["type"],
             "points": [[float(x), float(y)] for x, y in s["pts"].tolist()]}
            for s in it["shapes"]]


@router.get("/label", response_class=HTMLResponse)
async def halaman(request: Request, path: str = "",
                  sess: Session = Depends(current_session)):
    if not sess.items:
        return templates.TemplateResponse(request, "notfound.html", {"sess": sess},
                                          status_code=404)
    it = sess.find(path) or sess.items[0]
    with sess.lock:
        items = sess.items
        i = items.index(it)
        prev_it = items[i - 1] if i > 0 else None
        next_it = items[i + 1] if i < len(items) - 1 else None
        berkas = [{"nama": x["img"].name, "path": str(x["img"].resolve()),
                   "n": len(x["shapes"]), "sev": scanner.severity(x)} for x in items]

    return templates.TemplateResponse(request, "label.html", {
        "sess": sess,
        "local": is_local(request),
        "it": it,
        "posisi": (i + 1, len(items)),
        "prev_path": str(prev_it["img"].resolve()) if prev_it else "",
        "next_path": str(next_it["img"].resolve()) if next_it else "",
        "kelas": daftar_kelas(sess),
        "bentuk": bentuk_untuk_kanvas(it),
        "berkas": berkas,
        "sam": autolabel.info(),
    })


@router.get("/gambar")
async def gambar(path: str = "", sess: Session = Depends(current_session)):
    """
    Gambar apa adanya, tanpa mask dibakar ke dalamnya — kanvas menggambar
    bentuknya sendiri. Berbeda dari /thumb yang sudah ber-overlay.
    """
    it = sess.find(path)
    if not it:
        return Response(status_code=404)
    p: Path = it["img"]
    return Response(p.read_bytes(),
                    media_type=MIME.get(p.suffix.lower(), "application/octet-stream"),
                    headers={"Cache-Control": "private, max-age=300"})


@router.post("/api/sam")
async def api_sam(request: Request, sess: Session = Depends(current_session_api)):
    """
    Prompt dari kanvas -> poligon.

    Menerima `box: [x1,y1,x2,y2]` atau `points: [[x,y],...]` dengan
    `point_labels` (1 = bagian objek, 0 = bukan objek). Titik berlabel 0
    dipakai untuk memangkas mask yang meluber tanpa menggambar ulang.
    """
    body = await request.json()
    it = sess.find(body.get("path", ""))
    if not it:
        return {"ok": False, "error": "berkas tidak dikenal di dataset ini"}

    model = body.get("model") or autolabel.MODEL_DEFAULT
    eps = min(max(float(body.get("eps", 0.004)), 0.0005), 0.05)
    try:
        if body.get("box"):
            x1, y1, x2, y2 = (float(v) for v in body["box"])
            u = await asyncio.to_thread(autolabel.dari_kotak, it["img"],
                                        x1, y1, x2, y2, model, eps)
        elif body.get("points"):
            u = await asyncio.to_thread(autolabel.dari_titik, it["img"],
                                        body["points"], body.get("point_labels"),
                                        model, eps)
        else:
            return {"ok": False, "error": "tidak ada prompt"}
    except TidakAdaObjek as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:140]}

    return {"ok": True, "points": u.points, "bbox": u.bbox,
            "model": u.model, "dari_cache": u.dari_cache}


@router.post("/api/simpan")
async def api_simpan(request: Request, sess: Session = Depends(current_session_api)):
    """
    Tulis seluruh bentuk pada satu gambar ke `.json` labelme.

    Ditulis utuh, bukan menambah sebagian: kanvas selalu mengirim keadaan
    lengkap gambar itu, sehingga bentuk yang dihapus di kanvas juga hilang di
    berkas. Ditulis ke .tmp lalu diganti nama, supaya proses yang terputus
    tidak meninggalkan JSON rusak yang membuat gambar tampak "anotasi rusak".

    Tanpa bentuk sama sekali, berkasnya tetap ditulis dengan shapes kosong —
    itu penanda "latar", sama artinya dengan Mark Null di Roboflow, bukan
    "belum dilabeli".
    """
    body = await request.json()
    it = sess.find(body.get("path", ""))
    if not it:
        return {"ok": False, "error": "berkas tidak dikenal di dataset ini"}

    bentuk = []
    for s in body.get("shapes", []):
        titik = [[float(x), float(y)] for x, y in s.get("points", [])]
        label = str(s.get("label", "")).strip()
        jenis = "rectangle" if s.get("shape_type") == "rectangle" else "polygon"
        if not label:
            return {"ok": False, "error": "ada bentuk tanpa kelas — pilih kelasnya dulu"}
        # Rectangle labelme hanya 2 titik (kiri-atas, kanan-bawah); poligon
        # butuh minimal 3. Tanpa pembedaan ini rectangle akan terbuang diam-diam.
        if len(titik) < (2 if jenis == "rectangle" else 3):
            continue
        bentuk.append({
            "label": label,
            "points": titik,
            "group_id": s.get("group_id"),
            "shape_type": jenis,
            "flags": s.get("flags") or {},
            "description": s.get("description") or None,
        })

    jp: Path = it["img"].with_suffix(".json")
    tmp = jp.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps({
            "version": LABELME_VERSION,
            "flags": body.get("flags") or {},
            "shapes": bentuk,
            "imagePath": it["img"].name,
            "imageData": None,
            "imageHeight": it["H"],
            "imageWidth": it["W"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(jp)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        return {"ok": False, "error": str(e)[:100]}

    # Perbarui keadaan sesi supaya grid dan panel Files ikut berubah tanpa
    # perlu memindai ulang seluruh folder.
    with sess.lock:
        try:
            sh, W, H = scanner.read_json(jp)
            it["shapes"] = sh
            it["issues"] = scanner.inspect(sh, W or it["W"], H or it["H"], True)
        except Exception:
            it["issues"] = ["berkas anotasi rusak"]
        sess.drop_thumbs_for(it)
        annotations.write_label_file(sess)

    return {"ok": True, "n": len(bentuk), "issues": it["issues"],
            "sev": scanner.severity(it)}
