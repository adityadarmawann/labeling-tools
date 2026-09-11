"""
Rute halaman Training.

Sama seperti router lain di sini, berkas ini TIDAK memuat logika: ia
menerjemahkan HTTP saja. Yang memutuskan ada di services/latih.py — termasuk
parameter v14 dan sinkronisasi warnanya, yang terlalu mahal untuk tersebar di
dua tempat.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..config import Settings, get_settings
from ..deps import bodi_json, current_session, current_session_api
from ..log import catat
from ..services import latih as svc
from ..services import projek, tugas as svc_tugas, versi as svc_versi
from ..session import Session
from ..templating import templates

router = APIRouter()
_log = catat("labelapp.latih")


def _projek(sess: Session, settings: Settings, ds: str) -> Path | None:
    return projek.temukan(settings.uploads_root, sess.user, ds)


@router.get("/latih", response_class=HTMLResponse)
async def halaman(request: Request, ds: str = "",
                  sess: Session = Depends(current_session),
                  settings: Settings = Depends(get_settings)):
    d = _projek(sess, settings, ds)
    if d is None:
        return RedirectResponse("/pilih", status_code=303)
    # Projeknya dibuka di sesi ini, sama seperti /versi. Tanpa ini, masuk ke
    # halaman ini langsung dari sidebar membuat rute lain menjawab "belum ada
    # dataset terbuka" pada projek yang jelas-jelas sedang dilihat.
    if str(sess.src or "") != str(d):
        await asyncio.to_thread(sess.load, d)

    pr = await asyncio.to_thread(projek.konteks, d, settings.uploads_root,
                                 sess.user)
    tdata = svc_tugas.baca_projek(d, settings.uploads_root)
    return templates.TemplateResponse(request, "latih.html", {
        "sess": sess, "projek": pr, "pr": pr, "aktif": "latih",
        "boleh_kelola": svc_tugas.boleh_kelola(tdata, sess.user),
    })


@router.get("/api/latih/bahan")
async def bahan(sess: Session = Depends(current_session_api),
                settings: Settings = Depends(get_settings)):
    """Semua yang dibutuhkan form: versi, bobot, preset, batas.

    Dikirim sekali sebagai satu jawaban, bukan lima permintaan terpisah:
    formnya tidak bisa digambar setengah, dan lima permintaan berarti lima
    kesempatan tampilannya tampil timpang.
    """
    d = sess.src
    if not d:
        return {"ok": False, "error": "belum ada projek terbuka"}
    from ..services import buatversi, olah

    daftar_versi = await asyncio.to_thread(svc_versi.daftar, Path(d))
    kat = olah.katalog_json()["aug"]
    versi_siap = []
    for v in daftar_versi:
        n = v.get("nomor")
        penuh = await asyncio.to_thread(svc_versi.baca, Path(d), n) or v
        yaml = buatversi.dir_versi(Path(d), n) / "data.yaml"
        h = penuh.get("hasil") or {}
        versi_siap.append({
            "nomor": n,
            "dibuat": penuh.get("dibuat"),
            "catatan": penuh.get("catatan") or "",
            # Tanpa data.yaml versinya tidak bisa dilatih — biasanya karena
            # dibuat sebelum ekspor YOLO ada, atau berkasnya sudah dibuang.
            "siap": yaml.exists(),
            "jumlah": h.get("jumlah") or penuh.get("jumlah") or {},
            "kelas": h.get("per_kelas") or {},
            "negatif": h.get("negatif", 0),
            "jenis": h.get("jenis") or "",
            "warna": svc.periksa_warna(penuh, kat),
        })
    return {"ok": True,
            "versi": versi_siap,
            "bobot": await asyncio.to_thread(svc.bobot_tersedia),
            "preset": svc.PRESET_V14,
            "batas": {k: list(v) for k, v in svc.BATAS.items()},
            "tugas": list(svc.TUGAS)}


@router.get("/api/latih/daftar")
async def daftar(sess: Session = Depends(current_session_api)):
    if not sess.src:
        return {"ok": False, "error": "belum ada projek terbuka"}
    d = Path(sess.src)
    return {"ok": True,
            "daftar": await asyncio.to_thread(svc.daftar, d),
            "statistik": await asyncio.to_thread(svc.statistik)}


@router.post("/api/latih/mulai")
async def mulai(request: Request,
                sess: Session = Depends(current_session_api),
                settings: Settings = Depends(get_settings)):
    if not sess.src:
        return {"ok": False, "error": "belum ada projek terbuka"}
    d = Path(sess.src)
    tdata = svc_tugas.baca_projek(d, settings.uploads_root)
    if not svc_tugas.boleh_kelola(tdata, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang boleh melatih"}

    body = await bodi_json(request)
    nomor_versi = int(body.get("versi") or 0)
    v = await asyncio.to_thread(svc_versi.baca, d, nomor_versi)
    if v is None:
        return {"ok": False, "error": f"versi v{nomor_versi} tidak ada"}

    from ..services import buatversi, olah

    if not (buatversi.dir_versi(d, nomor_versi) / "data.yaml").exists():
        return {"ok": False,
                "error": f"versi v{nomor_versi} tidak punya data.yaml, "
                         "jadi tidak bisa dilatih"}

    # BATCH: satu permintaan boleh membawa beberapa training sekaligus, dan
    # semuanya masuk antrean yang sama. Itu yang membuat "coba tiga setelan
    # lalu bandingkan" bisa ditinggal semalam.
    antrian = body.get("batch") or [body]
    if not isinstance(antrian, list) or not antrian:
        return {"ok": False, "error": "daftar batch kosong"}
    if len(antrian) > 12:
        return {"ok": False, "error": "maksimal 12 training sekali kirim"}

    warna = svc.periksa_warna(v, olah.katalog_json()["aug"])
    dibuat = []
    for satu in antrian:
        try:
            isi = await asyncio.to_thread(
                svc.siapkan, d,
                nama=str(satu.get("nama") or ""),
                versi_nomor=nomor_versi,
                tugas=str(satu.get("tugas") or "segment"),
                bobot=str(satu.get("bobot") or ""),
                par=satu.get("par") or {},
                oleh=sess.user,
                catatan=str(satu.get("catatan") or ""),
                warna=warna)
        except ValueError as e:
            return {"ok": False, "error": str(e), "dibuat": dibuat}
        try:
            await asyncio.to_thread(svc.jalankan, d, isi["nomor"])
        except Exception as e:                   # noqa: BLE001
            _log.exception("gagal meluncurkan training")
            await asyncio.to_thread(svc.perbarui, d, isi["nomor"],
                                    keadaan="gagal", galat=str(e)[:200])
            return {"ok": False, "error": str(e)[:200], "dibuat": dibuat}
        dibuat.append(isi["nomor"])
    return {"ok": True, "dibuat": dibuat}


@router.post("/api/latih/batal")
async def batal(nomor: int = 0, sess: Session = Depends(current_session_api),
                settings: Settings = Depends(get_settings)):
    if not sess.src:
        return {"ok": False, "error": "belum ada projek terbuka"}
    d = Path(sess.src)
    tdata = svc_tugas.baca_projek(d, settings.uploads_root)
    if not svc_tugas.boleh_kelola(tdata, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang boleh"}
    await asyncio.to_thread(svc.batalkan, d, nomor)
    return {"ok": True}


@router.post("/api/latih/hapus")
async def hapus(nomor: int = 0, sess: Session = Depends(current_session_api),
                settings: Settings = Depends(get_settings)):
    if not sess.src:
        return {"ok": False, "error": "belum ada projek terbuka"}
    d = Path(sess.src)
    tdata = svc_tugas.baca_projek(d, settings.uploads_root)
    if not svc_tugas.boleh_kelola(tdata, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang boleh"}
    try:
        ok = await asyncio.to_thread(svc.buang, d, nomor)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": ok}


@router.get("/api/latih/rincian")
async def rincian(nomor: int = 0, sess: Session = Depends(current_session_api)):
    if not sess.src:
        return {"ok": False, "error": "belum ada projek terbuka"}
    d = Path(sess.src)
    s = await asyncio.to_thread(svc.status, d, nomor)
    if not s:
        return {"ok": False, "error": "training itu tidak ada"}
    csv = await asyncio.to_thread(svc.baca_hasil_csv, svc.dir_latih(d, nomor))
    return {"ok": True, "latih": s, "kurva": csv.get("kurva") or [],
            "log": await asyncio.to_thread(svc.ekor_log, d, nomor, 60)}


@router.get("/latih/bobot")
async def unduh_bobot(nomor: int = 0, jenis: str = "best",
                      sess: Session = Depends(current_session_api)):
    """Unduh best.pt atau last.pt satu training."""
    if not sess.src:
        return Response(status_code=404)
    if jenis not in ("best", "last"):
        return Response(status_code=400)
    p = svc.dir_latih(Path(sess.src), nomor) / "weights" / f"{jenis}.pt"
    if not p.exists():
        return Response(status_code=404)
    isi = await asyncio.to_thread(p.read_bytes)
    nama = f"L{nomor}-{jenis}.pt"
    return Response(isi, media_type="application/octet-stream",
                    headers={"Content-Disposition":
                             f'attachment; filename="{nama}"'})


@router.get("/latih/grafik")
async def grafik(nomor: int = 0, nama: str = "results.png",
                 sess: Session = Depends(current_session_api)):
    """Gambar yang digambar Ultralytics sendiri (kurva, confusion matrix)."""
    if not sess.src:
        return Response(status_code=404)
    # Nama berkas dibatasi ke satu segmen: tanpa ini, "../.." di parameternya
    # membuat rute ini bisa membaca berkas mana pun yang bisa dijangkau proses.
    if "/" in nama or "\\" in nama or nama.startswith("."):
        return Response(status_code=404)
    p = svc.dir_latih(Path(sess.src), nomor) / nama
    if not p.exists() or p.suffix.lower() not in (".png", ".jpg"):
        return Response(status_code=404)
    isi = await asyncio.to_thread(p.read_bytes)
    return Response(isi, media_type="image/png",
                    headers={"Cache-Control": "no-store"})
