"""
Pre-label batch: jalankan model terlatih ke gambar belum teranotasi di satu
tugas, isi prediksinya untuk diverifikasi.

Rutenya mengikuti pola job latar /api/versi: POST memulai lalu BALIK SEGERA
(pekerjaannya bisa menit-menit), kemajuannya dijajak lewat GET, dan pembatalan
lewat flag. Satu job per akun pada satu waktu.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request

from ..config import Settings, get_settings
from ..deps import bodi_json, current_session_api
from ..session import Session
from ..services import prelabel

router = APIRouter(tags=["prelabel"])

# Keadaan job per akun. Di memori, hilang saat proses mati -- sama seperti
# kemajuan di prelabel.py; tak ada yang perlu dibersihkan.
_tugas: dict[str, asyncio.Task] = {}
_batal: dict[str, bool] = {}


@router.get("/api/prelabel/model")
async def daftar_model(sess: Session = Depends(current_session_api)):
    """Model terlatih (YOLO, punya best.pt) yang bisa dipakai memprediksi."""
    if sess.src is None:
        return {"ok": False, "error": "belum ada projek terbuka"}
    model = await asyncio.to_thread(prelabel.model_terlatih, sess.src)
    return {"ok": True, "model": model}


@router.post("/api/prelabel/mulai")
async def mulai(request: Request,
                sess: Session = Depends(current_session_api),
                settings: Settings = Depends(get_settings)):
    if sess.src is None:
        return {"ok": False, "error": "belum ada projek terbuka"}
    body = await bodi_json(request)
    tid = str(body.get("tid") or "").strip()
    if not tid:
        return {"ok": False, "error": "tugas tak disebut"}
    try:
        nomor = int(body.get("nomor"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "model belum dipilih"}
    try:
        batas = int(body.get("batas") or 1000)
    except (TypeError, ValueError):
        batas = 1000
    batas = max(1, min(batas, 100_000))
    try:
        conf = float(body.get("conf"))
    except (TypeError, ValueError):
        conf = 0.25
    conf = min(max(conf, 0.01), 0.99)

    t = _tugas.get(sess.user)
    if t and not t.done():
        return {"ok": False, "error": "masih ada pre-label yang berjalan"}

    ds, akun, uploads = sess.src, sess.user, settings.uploads_root
    _batal[akun] = False

    def kerja():
        try:
            hasil = prelabel.jalankan(ds, uploads, tid, nomor, akun, batas, conf,
                                      batal=lambda: _batal.get(akun, False))
            prelabel.catat_maju(akun, jalan=False, selesai=True, **hasil)
        except prelabel.Dibatalkan:
            prelabel.catat_maju(akun, jalan=False, dibatalkan=True)
        except FileNotFoundError as e:
            prelabel.catat_maju(akun, jalan=False, galat=str(e)[:200])
        except Exception as e:                       # pragma: no cover
            prelabel.catat_maju(akun, jalan=False, galat=str(e)[:200])

    _tugas[akun] = asyncio.create_task(asyncio.to_thread(kerja))
    return {"ok": True}


@router.get("/api/prelabel/kemajuan")
async def kemajuan(sess: Session = Depends(current_session_api)):
    return {"ok": True, "maju": prelabel.kemajuan(sess.user)}


@router.post("/api/prelabel/batal")
async def batal(sess: Session = Depends(current_session_api)):
    _batal[sess.user] = True
    return {"ok": True}
