"""
Penugasan pelabelan: mengundang anggota, membagi gambar, menyatakan selesai.

Semua rute di sini bekerja pada dataset yang SEDANG dibuka sesi, sama seperti
rute tag. Itu disengaja: kunci gambar dihitung relatif terhadap akar projek,
dan menerima path bebas berarti menerima kunci yang tidak pernah bisa
diverifikasi ada di projek itu.

Yang boleh apa diputuskan services/tugas.py, bukan di sini. Rute ini hanya
menerjemahkan HTTP.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request

from ..deps import current_session_api
from ..services import tag as svc_tag
from ..services import tugas as svc
from ..session import Session

router = APIRouter(tags=["tugas"])


def _siap(sess: Session) -> tuple[dict | None, str]:
    if sess.src is None:
        return None, "belum ada dataset terbuka"
    return svc.baca(sess.src, sess.user), ""


def _kunci(sess: Session, paths: list[str]) -> list[str]:
    """Path dari peramban -> kunci projek, hanya yang benar-benar ada."""
    out = []
    for p in paths[:200_000]:
        it = sess.find(p)
        if it:
            out.append(svc_tag.kunci_gambar(sess.src, it["img"]))
    return out


@router.get("/api/tugas/keadaan")
async def keadaan(sess: Session = Depends(current_session_api)):
    """Siapa pemiliknya, siapa anggotanya, dan apa yang boleh kulakukan."""
    data, galat = _siap(sess)
    if galat:
        return {"ok": False, "error": galat}
    return {"ok": True, "pemilik": data["pemilik"],
            "anggota": sorted(data["anggota"]),
            "warisan": data["warisan"],
            "boleh_kelola": svc.boleh_kelola(data, sess.user),
            "n_tugas": len(data["tugas"]),
            "n_dataset": len(data["dataset"])}


@router.post("/api/tugas/undang")
async def undang(akun: str = "", sess: Session = Depends(current_session_api)):
    data, galat = _siap(sess)
    if galat:
        return {"ok": False, "error": galat}
    if not svc.boleh_kelola(data, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang bisa mengundang"}
    r = await asyncio.to_thread(svc.undang, sess.src, sess.user, akun)
    return {"ok": True, **r}


@router.post("/api/tugas/keluarkan-anggota")
async def keluarkan_anggota(akun: str = "",
                            sess: Session = Depends(current_session_api)):
    data, galat = _siap(sess)
    if galat:
        return {"ok": False, "error": galat}
    if not svc.boleh_kelola(data, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang bisa mengeluarkan"}
    r = await asyncio.to_thread(svc.keluarkan_anggota, sess.src, sess.user, akun)
    return {"ok": True, **r}


@router.post("/api/tugas/bagi")
async def bagi(request: Request, sess: Session = Depends(current_session_api)):
    """Buat satu job: sekumpulan gambar untuk satu pelabel."""
    data, galat = _siap(sess)
    if galat:
        return {"ok": False, "error": galat}
    if not svc.boleh_kelola(data, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang bisa membagi tugas"}
    body = await request.json()
    pelabel = str(body.get("pelabel") or "").strip()
    if not pelabel:
        return {"ok": False, "error": "belum memilih pelabelnya"}
    kunci = _kunci(sess, [str(p) for p in (body.get("gambar") or [])])
    if not kunci:
        return {"ok": False, "error": "tidak satu pun gambar itu ada di projek ini"}
    r = await asyncio.to_thread(svc.tugaskan, sess.src, sess.user, pelabel,
                                kunci, str(body.get("catatan") or ""))
    return {"ok": True, **r}


@router.post("/api/tugas/bubarkan")
async def bubarkan(id: str = "", sess: Session = Depends(current_session_api)):
    data, galat = _siap(sess)
    if galat:
        return {"ok": False, "error": galat}
    if not svc.boleh_kelola(data, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang bisa membubarkan"}
    r = await asyncio.to_thread(svc.bubarkan, sess.src, sess.user, id)
    return {"ok": True, **r}


@router.post("/api/tugas/dataset")
async def ke_dataset(request: Request,
                     sess: Session = Depends(current_session_api)):
    """
    Masukkan atau keluarkan gambar dari dataset.

    Inilah yang menentukan isi ekspor, splitting, dan versi. Yang boleh
    memasukkan hanya pemilik projek dan pelabel gambar itu sendiri: menyatakan
    pekerjaan orang lain selesai bukan keputusan yang pantas diambil diam-diam.
    """
    data, galat = _siap(sess)
    if galat:
        return {"ok": False, "error": galat}
    body = await request.json()
    kunci = _kunci(sess, [str(p) for p in (body.get("gambar") or [])])
    if not kunci:
        return {"ok": False, "error": "tidak satu pun gambar itu ada di projek ini"}

    tolak = [k for k in kunci if not svc.boleh_labeli(data, sess.user, k)]
    if tolak:
        return {"ok": False, "error": f"{len(tolak)} gambar bukan tugasmu; "
                                      f"hanya pelabelnya atau pemilik projek "
                                      f"yang bisa memasukkannya ke dataset"}

    if body.get("keluarkan"):
        r = await asyncio.to_thread(svc.keluarkan, sess.src, kunci, sess.user)
    else:
        r = await asyncio.to_thread(svc.masukkan, sess.src, kunci, sess.user)
    return {"ok": True, **r}
