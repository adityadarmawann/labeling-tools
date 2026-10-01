"""
Rute projek VIDEO: memotong video sumber menjadi klip.

Sama seperti router lain, berkas ini TIDAK memuat logika — ia menerjemahkan
HTTP saja. Yang memutuskan ada di services/klip.py (potong, resep, deteksi
ffmpeg). Unggah video sendiri tetap lewat PUT /upload di routers/uploads.py;
video projek VIDEO mendarat utuh di `_sumber/`, dan rute di sini yang kemudian
memotongnya.

Langkah 2 sengaja minimal: UI ingest penuh (dialog resep, filter objek,
kemajuan beruntun) menyusul di Langkah berikutnya. Yang dijaga sekarang: izin
(boleh_unggah) + jenis projek (video) + prasyarat ffmpeg (siap_video).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, Request

from ..config import SUMBER_VIDEO, VIDEO_EXT, Settings, get_settings
from ..deps import bodi_json, current_session_api
from ..security import safe_relpath, safe_slug
from ..services import klip, projek, tugas
from ..session import Session

router = APIRouter(tags=["video"])


def _projek_video(sess: Session, settings: Settings, ds: str):
    """(folder, pesan_error). Gerbang izin + jenis untuk operasi klip.

    Memakai ulang RBAC apa adanya (projek.temukan + tugas.boleh_unggah), sama
    seperti _folder_unggah di routers/uploads.py — yang BUKAN anggota tak
    teratasi temukan sama sekali (None), yang Labeler teratasi tapi gagal
    boleh_unggah. Potong klip menulis media baru, jadi haknya sama dengan
    mengunggah: pemilik atau Editor.
    """
    d = projek.temukan(settings.uploads_root, sess.user, ds)
    if d is None:
        return None, "projek tidak ada atau kamu bukan anggotanya"
    tdata = tugas.baca_projek(d, settings.uploads_root)
    if not tugas.boleh_unggah(tdata, sess.user):
        return None, "kamu tidak berhak mengubah media di projek ini"
    if projek.jenis_projek(d) != "video":
        return None, "pemotongan klip hanya untuk projek video"
    return d, ""


@router.post("/api/video/potong")
async def potong(request: Request, ds: str = "", name: str = "",
                 batch: str = "",
                 sess: Session = Depends(current_session_api),
                 settings: Settings = Depends(get_settings)):
    """
    Potong satu video di `_sumber/` menjadi klip di `klip/<batch>/`.

    Resep ingest (fps/slowmo/potong/ukuran/letterbox) dikirim sebagai bodi JSON
    (lihat klip.RESEP_BAWAAN); bodi kosong memakai bawaan. Video sumbernya
    TIDAK dibuang — asetnya memang videonya (rencana G#6).
    """
    siap, alasan = klip.siap_video()
    if not siap:
        return {"ok": False, "error": alasan}

    d, err = _projek_video(sess, settings, ds)
    if err:
        return {"ok": False, "error": err}

    # `name` adalah berkas DI DALAM _sumber/. Disterilkan dengan aturan yang
    # sama dengan unggahan, lalu dicari di _sumber/ — bukan di akar projek.
    fn = safe_relpath(name, video=True)
    if not fn or Path(fn).suffix.lower() not in VIDEO_EXT:
        return {"ok": False, "error": "yang diminta bukan berkas video"}
    vp = d / SUMBER_VIDEO / Path(fn).name
    if not vp.is_file():
        return {"ok": False, "error": "videonya tidak ada di _sumber/"}

    resep = klip.resep_sah(await bodi_json(request))
    nama_batch = safe_slug(batch) or vp.stem

    try:
        hasil = await asyncio.to_thread(
            klip.potong, vp, d, resep, nama_batch=nama_batch, srcid=vp.stem)
    except klip.KlipTolak as e:
        return {"ok": False, "error": str(e)[:160]}
    except Exception as e:                        # noqa: BLE001
        return {"ok": False, "error": f"gagal memotong: {str(e)[:90]}"}

    return {"ok": True, **hasil}
