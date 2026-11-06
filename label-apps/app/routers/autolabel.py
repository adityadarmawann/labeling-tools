"""
Auto-label batch ZERO-SHOT: sebut nama kelasnya, model teks (YOLO-World/SAM 3)
memindai gambar belum teranotasi di satu tugas dan mengisi prediksinya.

Polanya sama dengan /api/prelabel: POST memulai lalu BALIK SEGERA, kemajuan
dijajak lewat GET, pembatalan lewat flag, satu job per akun. Bedanya: tak perlu
model terlatih (cukup nama kelas), dan DITOLAK saat training lokal aktif.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request

from ..config import Settings, get_settings
from ..deps import bodi_json, current_session_api
from ..services import autolabel, autolabel_zero, gpu_sinyal, scanner
from ..session import Session

router = APIRouter(tags=["autolabel"])

# Keadaan job per akun. Di memori, hilang saat proses mati -- sama seperti
# prelabel; tak ada yang perlu dibersihkan.
_tugas: dict[str, asyncio.Task] = {}
_batal: dict[str, bool] = {}


@router.get("/api/autolabel/info")
async def info(sess: Session = Depends(current_session_api)):
    """Model teks tersedia (+ ukuran unduh & apakah sudah terunduh), pilihan
    bawaan untuk projek ini, prompt bawaan (nama kelas), dan apakah training
    sedang berjalan (antarmuka memakainya untuk menonaktifkan tombol)."""
    if sess.src is None:
        return {"ok": False, "error": "belum ada projek terbuka"}
    jenis = await asyncio.to_thread(scanner.jenis_projek, sess.src)
    prompt = await asyncio.to_thread(autolabel_zero.prompt_default, sess.src)
    inf = autolabel.info()
    return {"ok": True,
            "teks": inf.get("teks", []),
            "model_bawaan": autolabel_zero.pilih_model(jenis),
            "jenis": jenis,
            "prompt_bawaan": prompt,
            "training_aktif": gpu_sinyal.latih_aktif()}


@router.post("/api/autolabel/mulai")
async def mulai(request: Request,
                sess: Session = Depends(current_session_api),
                settings: Settings = Depends(get_settings)):
    if sess.src is None:
        return {"ok": False, "error": "belum ada projek terbuka"}
    # Guard training: umpan balik langsung, sebelum job dibuat.
    if gpu_sinyal.latih_aktif():
        return {"ok": False, "training": True,
                "error": "training sedang berjalan di server ini; auto-label "
                         "dinonaktifkan sampai selesai. Anotasi manual tetap bisa."}
    body = await bodi_json(request)
    # Lingkup: satu BATCH/unggahan ("batch", boleh "" = tanpa nama) ATAU satu
    # JOB ("tid"). Kehadiran kunci "batch" menandakan lingkup batch.
    if "batch" in body:
        batch = str(body.get("batch") or "")
        tid = ""
    else:
        batch = None
        tid = str(body.get("tid") or "").strip()
        if not tid:
            return {"ok": False, "error": "tugas tak disebut"}

    jenis = await asyncio.to_thread(scanner.jenis_projek, sess.src)
    model = str(body.get("model") or "").strip() or autolabel_zero.pilih_model(jenis)
    if model not in autolabel.MODEL_TEKS:
        return {"ok": False, "error": "model auto-label tidak dikenal"}

    # Prompt: daftar, atau teks dipisah koma. Kosong -> pakai nama kelas projek.
    raw = body.get("prompt")
    if isinstance(raw, str):
        prompt = [t.strip() for t in raw.split(",") if t.strip()]
    elif isinstance(raw, list):
        prompt = [str(t).strip() for t in raw if str(t).strip()]
    else:
        prompt = []
    if not prompt:
        prompt = await asyncio.to_thread(autolabel_zero.prompt_default, sess.src)
    if not prompt:
        return {"ok": False, "error": "projek belum punya kelas; ketik nama "
                                      "objek yang dicari dulu"}

    try:
        batas = int(body.get("batas") or 1000)
    except (TypeError, ValueError):
        batas = 1000
    batas = max(1, min(batas, 100_000))
    try:
        conf = float(body.get("conf"))
    except (TypeError, ValueError):
        conf = 0.1
    conf = min(max(conf, 0.01), 0.99)

    t = _tugas.get(sess.user)
    if t and not t.done():
        return {"ok": False, "error": "masih ada auto-label yang berjalan"}

    ds, akun, uploads = sess.src, sess.user, settings.uploads_root
    _batal[akun] = False

    def kerja():
        try:
            hasil = autolabel_zero.jalankan(
                ds, uploads, tid, akun, batas, conf, model, prompt,
                batal=lambda: _batal.get(akun, False), batch=batch)
            autolabel_zero.catat_maju(akun, jalan=False, selesai=True, **hasil)
        except autolabel_zero.Dibatalkan:
            autolabel_zero.catat_maju(akun, jalan=False, dibatalkan=True)
        except autolabel_zero.TrainingSedangJalan as e:
            autolabel_zero.catat_maju(akun, jalan=False, training=True,
                                      galat=str(e)[:200])
        except (ValueError, FileNotFoundError) as e:
            autolabel_zero.catat_maju(akun, jalan=False, galat=str(e)[:200])
        except Exception as e:                       # pragma: no cover
            autolabel_zero.catat_maju(akun, jalan=False, galat=str(e)[:200])

    _tugas[akun] = asyncio.create_task(asyncio.to_thread(kerja))
    return {"ok": True, "model": model, "prompt": prompt}


@router.get("/api/autolabel/kemajuan")
async def kemajuan(sess: Session = Depends(current_session_api)):
    return {"ok": True, "maju": autolabel_zero.kemajuan(sess.user)}


@router.post("/api/autolabel/batal")
async def batal(sess: Session = Depends(current_session_api)):
    _batal[sess.user] = True
    return {"ok": True}
