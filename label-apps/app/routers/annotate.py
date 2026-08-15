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


def kelas_resmi(sess: Session) -> list[str]:
    """
    Daftar kelas yang DIDEKLARASIKAN dataset — dari data.yaml atau classes.txt.

    Dibedakan dari `daftar_kelas`, yang hanya mengumpulkan nama yang kebetulan
    sudah dipakai. Pembedaan itu yang memungkinkan kanvas menahan salah ketik:
    tanpa daftar resmi, "Botol" dan "botol" sama-sama tampak sah karena
    dua-duanya "sudah dipakai" begitu satu objek terlanjur diberi nama itu.

    Kosong untuk dataset labelme yang memang tidak mendeklarasikan kelas; di
    situ penahanan tidak berlaku dan hanya kemiripan nama yang diperingatkan.
    """
    return [str(n).strip() for _, n in sorted(sess.names.items()) if str(n).strip()]


# Field tingkat atas dan per-bentuk yang memang milik kita. Sisanya dianggap
# titipan AnyLabeling dan harus dikembalikan apa adanya saat menyimpan.
KUNCI_ATAS = {"version", "flags", "shapes", "imagePath", "imageData",
              "imageHeight", "imageWidth"}
KUNCI_BENTUK = {"label", "text", "points", "group_id", "shape_type", "flags",
                "description"}


def baca_mentah(jp: Path) -> dict:
    """Isi .json apa adanya, untuk mempertahankan field yang tidak kita pakai."""
    if not jp.exists():
        return {}
    try:
        d = json.loads(jp.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def bentuk_untuk_kanvas(it: dict, mentah: dict) -> list[dict]:
    """
    Bentuk hasil pindai -> bentuk siap dikirim ke JavaScript.

    Field yang tidak dikenali kanvas (difficult, attributes, kie_linking, ...)
    dibawa serta sebagai `titipan` supaya bisa dikembalikan utuh saat disimpan.
    Tanpa ini, berkas yang dibuat di AnyLabeling akan kehilangan datanya begitu
    disimpan ulang dari web — kerusakan yang tidak terlihat sampai terlambat.
    """
    asli = mentah.get("shapes") or []
    out = []
    for i, s in enumerate(it["shapes"]):
        a = asli[i] if i < len(asli) and isinstance(asli[i], dict) else {}
        out.append({
            "label": "" if s["label"] is None else str(s["label"]),
            "shape_type": s["type"],
            # Titik ASLI, bukan yang sudah dimekarkan untuk digambar. Dulu di
            # sini dipakai `s["pts"]`, sehingga rectangle 2 titik buatan
            # AnyLabeling berubah jadi 4 titik lalu tersimpan begitu — dan
            # berkasnya tidak bisa dibuka lagi di desktop, karena shape.py:160
            # di sana menuntut rectangle tepat 1 atau 2 titik.
            "points": s.get("pts_asli")
                      or [[float(x), float(y)] for x, y in s["pts"].tolist()],
            # Untuk dataset YOLO, group_id/teks/flag sudah digabungkan pemindai
            # dari cadangan .json dengan penjagaan jumlah bentuk; nilai itu yang
            # dipakai. Untuk labelme, diambil langsung dari berkasnya.
            "text": s.get("text", a.get("text", "")) or "",
            "group_id": s.get("group_id", a.get("group_id")),
            "flags": s.get("flags") or a.get("flags") or {},
            "titipan": s.get("titipan")
                       or {k: v for k, v in a.items() if k not in KUNCI_BENTUK},
        })
    return out


@router.get("/label", response_class=HTMLResponse)
async def halaman(request: Request, path: str = "",
                  sess: Session = Depends(current_session)):
    if not sess.items:
        return templates.TemplateResponse(request, "notfound.html", {"sess": sess},
                                          status_code=404)
    it = sess.find(path) or sess.items[0]
    mentah = baca_mentah(it["img"].with_suffix(".json"))
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
        "kelas_resmi": kelas_resmi(sess),
        "bentuk": bentuk_untuk_kanvas(it, mentah),
        "flags_gambar": mentah.get("flags") or {},
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
    eps = min(max(float(body.get("eps", autolabel.EPSILON_ANYLABELING)), 0.0005), 0.05)
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
        jenis = s.get("shape_type") or "polygon"
        if jenis not in scanner.JENIS_BENTUK:
            jenis = "polygon"
        if not label:
            return {"ok": False, "error": "ada bentuk tanpa kelas — pilih kelasnya dulu"}
        # Tiap tipe punya jumlah titik minimal sendiri (shape.py AnyLabeling):
        # point 1, rectangle/circle/line/linestrip 2, polygon 3. Tanpa
        # pembedaan ini, titik dan garis akan terbuang diam-diam.
        if len(titik) < scanner.JENIS_BENTUK[jenis]:
            continue
        # Rectangle dan circle SELALU disimpan 2 titik. Kanvas boleh mengirim
        # bentuk yang sudah dimekarkan; yang menentukan isi berkas adalah
        # konvensi labelme, bukan apa yang kebetulan mudah digambar.
        if jenis == "rectangle" and len(titik) > 2:
            xs = [p[0] for p in titik]
            ys = [p[1] for p in titik]
            titik = [[min(xs), min(ys)], [max(xs), max(ys)]]
        elif jenis in ("circle", "line") and len(titik) > 2:
            titik = titik[:2]
        elif jenis == "point" and len(titik) > 1:
            titik = titik[:1]
        elif jenis == "polygon":
            # Cincin ditutup di berkas, seperti AnyLabeling dan Roboflow.
            # Kanvas memakainya terbuka; pembukaannya di scanner.read_json.
            titik = scanner.tutup_cincin(titik)
        # Titipan dikembalikan lebih dulu supaya field milik kita tetap menang.
        bentuk.append({
            **(s.get("titipan") if isinstance(s.get("titipan"), dict) else {}),
            "label": label,
            "text": s.get("text") or "",
            "points": titik,
            "group_id": s.get("group_id"),
            "shape_type": jenis,
            "flags": s.get("flags") or {},
        })

    jp: Path = it["img"].with_suffix(".json")
    lama = baca_mentah(jp)
    isi = {
        # Field tingkat atas yang bukan milik kita (mis. sumber kustom) ikut
        # dipertahankan, begitu juga imageData kalau berkasnya memang menanam
        # gambar — membuangnya berarti mengubah berkas orang tanpa diminta.
        **{k: v for k, v in lama.items() if k not in KUNCI_ATAS},
        "version": lama.get("version") or LABELME_VERSION,
        "flags": body.get("flags") if body.get("flags") is not None
                 else (lama.get("flags") or {}),
        "shapes": bentuk,
        "imagePath": it["img"].name,
        "imageData": lama.get("imageData"),
        "imageHeight": it["H"],
        "imageWidth": it["W"],
    }
    tmp = jp.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(isi, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(jp)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        return {"ok": False, "error": str(e)[:100]}

    peringatan: list[str] = []
    if it.get("yolo"):
        # Dataset YOLO: berkas .txt itulah yang dibaca saat melatih, jadi dia
        # yang harus ikut berubah. Tanpa ini, menyimpan dari web terasa
        # berhasil tetapi hasilnya tidak pernah terpakai — dan hilang begitu
        # dataset dipindai ulang.
        indeks = {n: i for i, n in sess.names.items()}
        try:
            _, peringatan = scanner.tulis_yolo(
                it["labels"], bentuk, it["W"], it["H"], indeks)
        except OSError as e:
            return {"ok": False, "error": f"gagal menulis label YOLO: {str(e)[:80]}"}

    # Perbarui keadaan sesi supaya grid dan panel Files ikut berubah tanpa
    # perlu memindai ulang seluruh folder.
    with sess.lock:
        try:
            if it.get("yolo"):
                sh = scanner.read_yolo(it["labels"], it["W"], it["H"], sess.names)
                scanner._gabung_cadangan(it["img"], sh)
                it["shapes"] = sh
                it["issues"] = scanner.inspect(sh, it["W"], it["H"], True)
            else:
                sh, W, H = scanner.read_json(jp)
                it["shapes"] = sh
                it["issues"] = scanner.inspect(sh, W or it["W"], H or it["H"], True)
        except Exception:
            it["issues"] = ["berkas anotasi rusak"]
        sess.drop_thumbs_for(it)
        annotations.write_label_file(sess)

    return {"ok": True, "n": len(bentuk), "issues": it["issues"],
            "sev": scanner.severity(it), "peringatan": peringatan}
