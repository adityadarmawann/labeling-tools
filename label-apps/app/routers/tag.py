"""
Tag dan nama unggahan untuk gambar di dataset yang sedang dibuka.

Tag itu keterangan yang dipasang ORANG, bukan yang disimpulkan dari isi
gambar: "sesi pagi", "lampu redup", "ulang foto". Gunanya satu — menemukan
kembali sekelompok gambar yang tidak punya ciri lain yang bisa dicari.

Penyimpanannya di app/services/tag.py, satu berkas pendamping per projek.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request

from ..deps import current_session_api, bodi_json
from ..services import tag as svc
from ..services import tugas as svc_tugas
from ..session import Session

router = APIRouter(tags=["tag"])

# Satu permintaan menandai sekumpulan gambar sekaligus. Batasnya ada supaya
# satu bodi JSON tidak pernah membuat server menahan daftar tak berhingga di
# memori; 50 ribu sudah jauh di atas dataset terbesar tim ini.
MAKS_SEKALI = 50_000


def _daftar(nilai) -> list[str]:
    """Terima daftar tag, atau satu tag sebagai string."""
    if nilai is None:
        return []
    if isinstance(nilai, str):
        return [nilai]
    return [str(x) for x in nilai]


def _kunci(sess: Session, paths: list[str]) -> list[str]:
    """Path dari peramban -> kunci di berkas tag, hanya yang benar-benar ada.

    Disaring lewat sess.find, bukan dipercaya: tanpa itu rute ini bisa dipakai
    menulis nama berkas apa pun ke dalam berkas tag milik projek orang lain.
    """
    out = []
    for p in paths[:MAKS_SEKALI]:
        it = sess.find(p)
        if it:
            out.append(svc.kunci_gambar(sess.src, it["img"]))
    return out


@router.post("/api/tag/pasang")
async def pasang(request: Request,
                 sess: Session = Depends(current_session_api)):
    if sess.src is None:
        return {"ok": False, "error": "belum ada dataset terbuka"}
    body = await bodi_json(request)
    paths = body.get("paths") or []

    if body.get("tanpa_batch"):
        # Dipakai tepat setelah satu unggahan selesai: yang baru masuk adalah
        # gambar yang belum punya nama batch. Menandainya lewat daftar nama
        # yang dikirim peramban tidak bisa diandalkan, karena isi .zip baru
        # diketahui setelah dibongkar DI SERVER, dan peramban tidak pernah
        # melihat nama berkas di dalamnya.
        data = await asyncio.to_thread(svc.baca, sess.src)
        kunci = [k for k in (svc.kunci_gambar(sess.src, it["img"])
                             for it in sess.items)
                 if not svc.untuk(data, k)["batch"]]
        if not kunci:
            return {"ok": True, "n": 0, **svc.hitung(data)}
    else:
        if not isinstance(paths, list) or not paths:
            return {"ok": False, "error": "tidak ada gambar yang ditunjuk"}
        kunci = _kunci(sess, [str(p) for p in paths])
    if not kunci:
        return {"ok": False, "error": "tidak satu pun gambar itu ada di dataset ini"}

    # Menandai gambar itu MENULIS keterangan tentangnya, jadi ia tunduk pada
    # aturan yang sama dengan menyunting labelnya. Sebelumnya halaman /view
    # menghitung `boleh_tag` untuk menyembunyikan kotaknya, tetapi rutenya
    # sendiri tidak pernah memeriksa — dan aturan yang cuma berlaku di
    # tampilan bukan aturan.
    tdata = svc_tugas.baca(sess.src, sess.user)
    tolak = [k for k in kunci if not svc_tugas.boleh_labeli(tdata, sess.user, k)]
    if tolak:
        return {"ok": False, "error": (
            f"{len(tolak)} gambar ditugaskan ke orang lain; hanya pelabelnya "
            f"atau pemilik projek yang bisa menandainya")}

    batch = body.get("batch")
    r = await asyncio.to_thread(
        svc.pasang, sess.src, kunci,
        # String tunggal dibungkus jadi satu unsur. Tanpa ini "abc" dipecah
        # per huruf oleh iterasinya sendiri, dan tiga tag bernama a, b, dan c
        # lahir dari satu tag yang dimaksud.
        tambah=_daftar(body.get("tambah")),
        buang=_daftar(body.get("buang")),
        batch=None if batch is None else str(batch))
    data = await asyncio.to_thread(svc.baca, sess.src)
    return {"ok": True, **r, **svc.hitung(data)}
