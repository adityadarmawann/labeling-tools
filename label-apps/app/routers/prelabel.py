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
from ..services import ai_antre, prelabel

router = APIRouter(tags=["prelabel"])

# Keadaan job per akun. Di memori, hilang saat proses mati -- sama seperti
# kemajuan di prelabel.py; tak ada yang perlu dibersihkan.
FITUR = "pre-label"
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
    # Lingkup: BATCH ("batch", boleh "") atau JOB ("tid") -- bentuk bodi yang
    # SAMA dengan /api/autolabel/mulai, supaya antarmuka memanggil keduanya
    # dengan cara yang sama dan tidak ada jalur yang diam-diam cuma job.
    if "batch" in body:
        batch = str(body.get("batch") or "")
        tid = ""
    else:
        batch = None
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

    # Satu pekerjaan AI per akun, LINTAS FITUR (lihat services/ai_antre.py).
    # Klaimnya atomik dan diambil DI SINI, sesudah seluruh validasi bodi lewat,
    # supaya tak ada jalur penolakan yang meninggalkan klaim tersangkut.
    # Klaim dititipi cara memeriksa pekerjanya; lihat services/ai_antre.py.
    # Tanpa itu, pekerjaan yang berhenti tanpa sempat melepas mengunci akun
    # ini selamanya dari semua fitur AI.
    def _masih_jalan(_akun=sess.user) -> bool:
        # Belum terdaftar = masih jalan. Lihat catatan sama di
        # routers/autolabel.py: gagal ke arah MENJAGA, bukan melepas.
        t = _tugas.get(_akun)
        return t is None or not t.done()

    if (lain := ai_antre.klaim(sess.user, FITUR, _masih_jalan)):
        return {"ok": False, "error": f"masih ada {lain} yang berjalan; tunggu "
                                      "sampai selesai atau batalkan dulu"}

    from ..services import tugas as svc_tugas
    sasaran = str(body.get("sasaran") or "belum")
    sasaran = sasaran if sasaran in svc_tugas.SASARAN else "belum"
    ds, akun, uploads = sess.src, sess.user, settings.uploads_root
    _batal[akun] = False

    def kerja():
        try:
            hasil = prelabel.jalankan(ds, uploads, tid, nomor, akun, batas, conf,
                                      batal=lambda: _batal.get(akun, False),
                                      batch=batch, sasaran=sasaran)
            prelabel.catat_maju(akun, jalan=False, selesai=True, **hasil)
        except prelabel.Dibatalkan:
            prelabel.catat_maju(akun, jalan=False, dibatalkan=True)
        except (ValueError, FileNotFoundError) as e:
            prelabel.catat_maju(akun, jalan=False, galat=str(e)[:200])
        except Exception as e:                       # pragma: no cover
            prelabel.catat_maju(akun, jalan=False, galat=str(e)[:200])
        finally:
            # Dilepas APA PUN hasilnya; klaim yang tertinggal mengunci akunnya
            # dari semua fitur AI.
            ai_antre.lepas(akun, FITUR)

    _tugas[akun] = asyncio.create_task(asyncio.to_thread(kerja))
    return {"ok": True}


@router.get("/api/prelabel/kemajuan")
async def kemajuan(sess: Session = Depends(current_session_api)):
    return {"ok": True, "maju": prelabel.kemajuan(sess.user)}


@router.post("/api/prelabel/batal")
async def batal(sess: Session = Depends(current_session_api)):
    _batal[sess.user] = True
    return {"ok": True}
