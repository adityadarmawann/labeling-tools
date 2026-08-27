"""
Halaman projek: daftar berkartu beserta operasi atas datasetnya.

RUANG KERJANYA DIBATASI DI SINI, SEKALI
---------------------------------------
Setiap rute mengambil rootnya dari `_ruang(sess, settings)`, yang selalu
`uploads_root/<akun>`. Tidak ada rute yang menerima path bebas dari pengguna.

Itu disengaja: folder dataset bersama (`datasets_root`) memuat pekerjaan orang
lain dan sebagian milik proyek lain di mesin yang sama. Boleh dibuka dan
dibaca dari halaman pilih, tapi tidak boleh diganti nama, digandakan,
digabungkan, atau dibuang dari sini.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import cv2
from fastapi import APIRouter, Depends
from fastapi.responses import Response

from ..config import IMG_EXT, Settings, get_settings
from ..deps import current_session, current_session_api
from ..session import Session
from ..services import projek

router = APIRouter()

SAMPUL_SISI = 320


def _ruang(sess: Session, settings: Settings) -> Path:
    d = Path(settings.uploads_root) / sess.user
    d.mkdir(parents=True, exist_ok=True)
    return d


def _jawab(fn, *a, **k):
    """Jalankan operasi projek, ubah penolakannya jadi pesan yang bisa dibaca."""
    try:
        return {"ok": True, **fn(*a, **k)}
    except projek.Tolak as e:
        return {"ok": False, "error": str(e)}
    except OSError as e:
        return {"ok": False, "error": f"gagal menyentuh berkas: {str(e)[:90]}"}


def _segarkan_sesi(sess: Session, lama: Path) -> bool:
    """
    Kalau dataset yang SEDANG dibuka barusan dipindah atau diganti nama,
    sesinya menunjuk folder yang tidak ada lagi.

    Dibiarkan, halaman grid tetap menampilkan daftar gambar dari ingatan dan
    setiap kali membuka gambarnya baru gagal — satu per satu, tanpa
    menjelaskan sebabnya.
    """
    if sess.src and (sess.src == lama or not sess.src.exists()):
        sess.src = None
        sess.items = []
        sess.rencana_split = None
        return True
    return False


@router.get("/api/projek/daftar")
async def daftar(sess: Session = Depends(current_session_api),
                 settings: Settings = Depends(get_settings)):
    root = _ruang(sess, settings)
    kartu = await asyncio.to_thread(projek.daftar, root)
    sampah = await asyncio.to_thread(projek.isi_sampah, root)
    kini = str(sess.src) if sess.src else ""
    for k in kartu:
        k["dibuka"] = k["path"] == kini
    return {"ok": True, "projek": kartu, "sampah": sampah, "ruang": str(root)}


@router.get("/api/projek/sampul")
async def sampul(path: str = "", sess: Session = Depends(current_session),
                 settings: Settings = Depends(get_settings)):
    """
    Gambar sampul satu kartu.

    Pathnya diperiksa berada di dalam ruang kerja akun ini, bukan dipercaya:
    tanpa itu, rute ini jadi jalan untuk membaca berkas mana pun di server
    yang bisa dijadikan JPEG.
    """
    p = Path(path or "")
    if (not p.is_file() or p.suffix.lower() not in IMG_EXT
            or not projek._didalam(p, _ruang(sess, settings))):
        return Response(status_code=404)

    def kecilkan() -> bytes | None:
        im = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if im is None:
            return None
        h, w = im.shape[:2]
        sisi = max(h, w) or 1
        k = min(1.0, SAMPUL_SISI / sisi)
        if k < 1.0:
            im = cv2.resize(im, (max(1, int(w * k)), max(1, int(h * k))),
                            interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", im, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
        return buf.tobytes() if ok else None

    data = await asyncio.to_thread(kecilkan)
    if not data:
        return Response(status_code=404)
    return Response(data, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=300"})


@router.post("/api/projek/ganti-nama")
async def ganti_nama(nama: str = "", baru: str = "",
                     sess: Session = Depends(current_session_api),
                     settings: Settings = Depends(get_settings)):
    root = _ruang(sess, settings)
    lama = root / projek.bersihkan_nama(nama)
    r = await asyncio.to_thread(_jawab, projek.ganti_nama, root, nama, baru)
    if r.get("ok"):
        r["sesi_ditutup"] = _segarkan_sesi(sess, lama)
    return r


@router.post("/api/projek/duplikat")
async def duplikat(nama: str = "", baru: str = "",
                   sess: Session = Depends(current_session_api),
                   settings: Settings = Depends(get_settings)):
    """Menggandakan berarti menyalin seluruh berkasnya; bisa memakan menit."""
    root = _ruang(sess, settings)
    projek.bersihkan_maju(sess.user)
    sess.projek_batal = False
    r = await asyncio.to_thread(_jawab, projek.duplikat, root, nama, baru,
                                kunci=sess.user,
                                batal=lambda: sess.projek_batal)
    projek.bersihkan_maju(sess.user)
    return r


@router.get("/api/projek/kemajuan")
async def kemajuan(sess: Session = Depends(current_session_api)):
    """Ditanya berkala selagi duplikat masih menggantung."""
    return {"ok": True, **projek.kemajuan(sess.user)}


@router.post("/api/projek/batal")
async def batal(sess: Session = Depends(current_session_api)):
    sess.projek_batal = True
    return {"ok": True}


@router.post("/api/projek/sampah")
async def ke_sampah(nama: str = "",
                    sess: Session = Depends(current_session_api),
                    settings: Settings = Depends(get_settings)):
    root = _ruang(sess, settings)
    lama = root / projek.bersihkan_nama(nama)
    r = await asyncio.to_thread(_jawab, projek.ke_sampah, root, nama)
    if r.get("ok"):
        r["sesi_ditutup"] = _segarkan_sesi(sess, lama)
    return r


@router.post("/api/projek/pulihkan")
async def pulihkan(folder: str = "",
                   sess: Session = Depends(current_session_api),
                   settings: Settings = Depends(get_settings)):
    root = _ruang(sess, settings)
    return await asyncio.to_thread(_jawab, projek.pulihkan, root, folder)


@router.post("/api/projek/gabung")
async def gabung(sumber: str = "", tujuan: str = "",
                 sess: Session = Depends(current_session_api),
                 settings: Settings = Depends(get_settings)):
    """
    Isi `sumber` disalin ke `tujuan`. Sumbernya tidak dihapus.

    Kemajuannya dilaporkan lewat rute impor yang sudah ada
    (`/api/impor/kemajuan`), karena mesin penyalinnya memang sama.
    """
    root = _ruang(sess, settings)
    r = await asyncio.to_thread(_jawab, projek.gabung, root, sumber, tujuan,
                                kunci=sess.user)
    if r.get("ok") and sess.src and sess.src.name == projek.bersihkan_nama(tujuan):
        # Dataset tujuan sedang dibuka: daftarnya di ingatan sudah usang.
        await asyncio.to_thread(sess.reload)
        r["dipindai_ulang"] = True
    return r
