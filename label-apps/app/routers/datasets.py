"""Memilih dataset: daftar dari folder induk, path bebas, atau dialog desktop."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response

from ..config import Settings, get_settings
from ..deps import current_session, current_session_api, is_local, require_local
from ..log import catat
from ..services import (anylabeling, export, riwayat, scanner, split,
                        tugas, versi)
from ..services import tag as svc_tag

_log = catat("labelapp.split")
from ..session import Session
from ..templating import templates

router = APIRouter(tags=["datasets"])


def picker_context(request: Request, sess: Session, settings: Settings,
                   error: str | None = None) -> dict:
    return {
        "sess": sess,
        "local": is_local(request),
        "error": error,
        "datasets": scanner.list_dirs(settings.datasets_root),
        "unggahan": scanner.list_dirs(settings.uploads_root / sess.user),
        "datasets_root": settings.datasets_root,
        "max_upload_mb": settings.max_upload_mb,
        "max_zip_mb": settings.max_zip_mb,
        "riwayat": riwayat.baca(settings, sess.user),
    }


@router.get("/pilih", response_class=HTMLResponse)
async def picker(request: Request,
                 sess: Session = Depends(current_session),
                 settings: Settings = Depends(get_settings)):
    return templates.TemplateResponse(request, "pick.html",
                                      picker_context(request, sess, settings))


def boleh_buka(d: Path, sess: Session, settings: Settings) -> str:
    """Pembungkus tipis; aturannya ada di services.projek.boleh_buka."""
    from ..services.projek import boleh_buka as aturan

    return aturan(d, sess.user, settings.uploads_root, settings.datasets_root)


@router.post("/setsrc")
async def set_source(path: str = "",
                     sess: Session = Depends(current_session_api),
                     settings: Settings = Depends(get_settings)):
    raw = (path or "").strip()
    if not raw:
        return {"ok": False, "error": "path masih kosong"}
    d = Path(raw).expanduser()
    if not d.is_dir():
        return {"ok": False, "error": "folder tidak ada di server"}
    tolak = await asyncio.to_thread(boleh_buka, d, sess, settings)
    if tolak:
        return {"ok": False, "error": tolak}
    n = len(await asyncio.to_thread(sess.load, d))
    if not n:
        return {"ok": False, "error": "tidak ada gambar terbaca di folder itu"}
    riwayat.catat(settings, sess.user, d.resolve(), "buka")
    return {"ok": True, "dir": str(d.resolve()), "n": n}


@router.post("/lupakan-path")
async def lupakan_path(path: str = "",
                       sess: Session = Depends(current_session_api),
                       settings: Settings = Depends(get_settings)):
    """Buang satu baris dari riwayat. Hanya catatannya — foldernya tidak
    disentuh sama sekali, dan itu perlu dinyatakan supaya tombolnya tidak
    terbaca sebagai 'hapus dataset'."""
    riwayat.lupakan(settings, sess.user, (path or "").strip())
    return {"ok": True}


@router.post("/rescan")
async def rescan(sess: Session = Depends(current_session_api)):
    if sess.src is None:
        return {"ok": False, "error": "belum ada dataset yang dibuka"}
    n = len(await asyncio.to_thread(sess.reload))
    return {"ok": True, "n": n}


@router.post("/pickdir", dependencies=[Depends(require_local)])
async def pick_dir(sess: Session = Depends(current_session_api),
                   settings: Settings = Depends(get_settings)):
    """Dialog folder milik sistem — hanya untuk akses dari mesin server."""
    start = str(sess.src or Path.home())
    path, err = await asyncio.to_thread(anylabeling.pick_dir, start)
    if not path:
        return {"ok": False, "error": err or "dibatalkan"}
    d = Path(path)
    if not d.is_dir():
        return {"ok": False, "error": "bukan folder"}
    # Dialognya memang dibuka dari mesin server, tetapi yang memilih tetap
    # sebuah akun, dan aturan folder mana yang boleh dibuka tidak berubah
    # hanya karena pemilihnya memakai jendela alih-alih kotak isian.
    tolak = await asyncio.to_thread(boleh_buka, d, sess, settings)
    if tolak:
        return {"ok": False, "error": tolak}
    n = len(await asyncio.to_thread(sess.load, d))
    return {"ok": True, "dir": str(d), "n": n}


@router.post("/api/versi/buat")
async def versi_buat(split: str = "", catatan: str = "",
                     sess: Session = Depends(current_session_api),
                     settings: Settings = Depends(get_settings)):
    """
    Bekukan pembagian yang berlaku sekarang jadi satu versi.

    Yang dibekukan petanya, bukan rasionya: rasio yang sama pada dataset yang
    sudah bertambah menghasilkan pembagian yang lain, dan versi yang isinya
    berubah bukan versi.
    """
    if sess.src is None:
        return {"ok": False, "error": "belum ada dataset terbuka"}
    # Versi adalah keputusan tentang isi projek, bukan tentang satu job.
    # Pemilik projek yang memutuskannya. Sebelumnya tombolnya cuma
    # disembunyikan lewat boleh_kelola, dan rutenya menerima siapa saja:
    # anggota biasa bisa MENGHAPUS PERMANEN versi milik pemiliknya, dan versi
    # tidak masuk sampah.
    #
    # Pemiliknya dibaca dari LETAK folder, bukan dari akun pemanggil. baca()
    # memakai argumen keduanya sebagai pemilik cadangan, jadi di folder
    # dataset BERSAMA — yang memang tidak punya berkas tugas — pemanggil
    # siapa pun tercatat sebagai pemiliknya sendiri dan boleh_kelola selalu
    # menjawab ya. Terbukti: dua akun biasa membuat versi di folder bersama,
    # lalu yang satu menghapus permanen versi buatan yang lain.
    tdata = await asyncio.to_thread(tugas.baca_projek, sess.src,
                                    settings.uploads_root)
    if not tugas.boleh_kelola(tdata, sess.user):
        return {"ok": False, "error": (
            "hanya pemilik projek yang mengurus versi"
            if tdata["pemilik"] else
            "folder dataset bersama tidak punya pemilik, jadi versinya tidak "
            "bisa diurus dari sini — salin dulu ke ruang kerjamu")}
    with sess.lock:
        items = list(sess.items)
        names = dict(sess.names)
    items, hitung = await asyncio.to_thread(tugas.saring_dataset, items,
                                            sess.src, settings.uploads_root)
    if not items:
        return {"ok": False, "error": (
            "belum ada gambar yang dimasukkan ke dataset. Versi dibuat dari isi "
            "dataset, bukan dari seluruh gambar yang diunggah.")}

    rasio = split or "8:1:1"
    rencana = sess.rencana_split
    bagian = await asyncio.to_thread(export.bagi_split, items,
                                     export.baca_rasio(rasio), rencana)
    peta = {it["img"].name: s for s, daftar in bagian.items() for it in daftar}
    r = await asyncio.to_thread(export.ringkasan, items, True,
                                export.baca_rasio(rasio), names, rencana)
    hasil = await asyncio.to_thread(
        versi.buat, sess.src, sess.user, rasio,
        [it["img"].name for it in items], peta, r, catatan)
    return {"ok": True, **hasil, **hitung,
            # Rencana anti-bocor ikut dicatat ADA atau tidak: versi yang dibuat
            # tanpa memeriksa isi gambar tidak boleh terlihat sama dengan yang
            # sudah diperiksa.
            "berencana": bool(rencana)}


@router.post("/api/versi/hapus")
async def versi_hapus(nomor: int = 0,
                      sess: Session = Depends(current_session_api),
                      settings: Settings = Depends(get_settings)):
    if sess.src is None:
        return {"ok": False, "error": "belum ada dataset terbuka"}
    # Versi adalah keputusan tentang isi projek, bukan tentang satu job.
    # Pemilik projek yang memutuskannya. Sebelumnya tombolnya cuma
    # disembunyikan lewat boleh_kelola, dan rutenya menerima siapa saja:
    # anggota biasa bisa MENGHAPUS PERMANEN versi milik pemiliknya, dan versi
    # tidak masuk sampah.
    #
    # Pemiliknya dibaca dari LETAK folder, bukan dari akun pemanggil. baca()
    # memakai argumen keduanya sebagai pemilik cadangan, jadi di folder
    # dataset BERSAMA — yang memang tidak punya berkas tugas — pemanggil
    # siapa pun tercatat sebagai pemiliknya sendiri dan boleh_kelola selalu
    # menjawab ya. Terbukti: dua akun biasa membuat versi di folder bersama,
    # lalu yang satu menghapus permanen versi buatan yang lain.
    tdata = await asyncio.to_thread(tugas.baca_projek, sess.src,
                                    settings.uploads_root)
    if not tugas.boleh_kelola(tdata, sess.user):
        return {"ok": False, "error": (
            "hanya pemilik projek yang mengurus versi"
            if tdata["pemilik"] else
            "folder dataset bersama tidak punya pemilik, jadi versinya tidak "
            "bisa diurus dari sini — salin dulu ke ruang kerjamu")}
    ok = await asyncio.to_thread(versi.hapus, sess.src, nomor)
    return {"ok": ok, "error": "" if ok else f"versi v{nomor} tidak ada"}


@router.get("/api/ekspor/ringkasan")
async def ekspor_ringkasan(format: str = "yolo-seg", split: str = "",
                           sess: Session = Depends(current_session_api),
                           settings: Settings = Depends(get_settings)):
    """Angka yang ditampilkan sebelum orang menekan unduh."""
    if sess.src is None:
        return {"ok": False, "error": "belum ada dataset terbuka"}
    if format not in export.FORMAT:
        return {"ok": False, "error": f"format '{format}' tidak dikenal"}
    # Kuncinya HANYA menyelimuti penyalinan daftarnya, tidak sampai ke await.
    #
    # sess.lock adalah threading.Lock, dan menahannya melewati await mematikan
    # seluruh server: permintaan kedua memanggil acquire() di thread event loop,
    # thread itu berhenti, dan pemegang kuncinya tidak akan pernah bisa
    # dilanjutkan untuk melepasnya — karena yang melanjutkannya adalah event
    # loop yang sudah berhenti itu. Servernya membeku di 0% CPU sampai
    # direstart. Cukup dua permintaan ringkasan bertumpang, misalnya karena
    # kotak rasio diubah selagi hitungan pertama masih jalan.
    #
    # `sess.names` dibawa serta supaya indeks kelas mengikuti urutan data.yaml
    # dataset sumbernya, bukan diturunkan ulang dari label yang kebetulan ada
    # di seleksi ini.
    with sess.lock:
        items = list(sess.items)
        names = dict(sess.names)
    # Yang diekspor HANYA yang sudah dinyatakan masuk dataset. Lihat
    # tugas.saring_dataset; projek yang belum pernah dibagi tidak terpengaruh.
    items, hitung = await asyncio.to_thread(tugas.saring_dataset, items,
                                            sess.src, settings.uploads_root)
    rencana = sess.rencana_split
    r = await asyncio.to_thread(export.ringkasan, items, format == "yolo-seg",
                                export.baca_rasio(split), names, rencana)
    return {"ok": True, "format": export.FORMAT[format],
            "rencana": ringkas_rencana(rencana), **hitung, **r}


@router.get("/ekspor")
async def ekspor(format: str = "yolo-seg", gambar: int = 1, split: str = "",
                 tanda: str = "", nomor: int = 0,
                 sess: Session = Depends(current_session),
                 settings: Settings = Depends(get_settings)):
    """
    Unduh dataset sebagai ZIP bertata letak ultralytics.

    Dibuat di memori lalu dikirim sekali jalan: dataset tim ini ukurannya
    ribuan gambar, bukan ratusan ribu, jadi tidak perlu berkas sementara di
    disk yang harus dibersihkan.
    """
    if sess.src is None:
        return Response("belum ada dataset terbuka", status_code=400,
                        media_type="text/plain; charset=utf-8")
    if format not in export.FORMAT:
        return Response("format tidak dikenal", status_code=400,
                        media_type="text/plain; charset=utf-8")
    nama = sess.src.name
    with sess.lock:
        items = list(sess.items)
        names = dict(sess.names)
    # Isi ZIP-nya harus sama persis dengan yang dihitung ringkasan di panelnya.
    # Kalau ringkasan menyaring dan unduhan tidak, angka yang dibaca sebelum
    # menekan tombol bukan angka yang diterima sesudahnya.
    # Urutannya penting. Versi disaring LEBIH DULU, baru isi dataset — dan
    # untuk versi, saringan dataset tidak dipakai sama sekali. Kalau
    # dibalik, gambar yang dikeluarkan dari dataset sesudah versinya dibuat
    # ikut hilang dari versi itu: kartunya bilang 4 gambar, ZIP-nya berisi 2,
    # dan versi yang isinya bisa berubah bukan versi.
    rencana_dipakai = sess.rencana_split
    if nomor:
        v = await asyncio.to_thread(versi.baca, sess.src, nomor)
        if v is None:
            return Response(f"versi v{nomor} tidak ada", status_code=404,
                            media_type="text/plain; charset=utf-8")
        punya = set(v.get("gambar") or [])
        items = [it for it in items if it["img"].name in punya]
        rencana_dipakai = {"peta": v.get("peta") or {}, "versi": nomor}
        split = v.get("rasio") or split
        nama = f"{nama}-v{nomor}"
    else:
        items, _ = await asyncio.to_thread(tugas.saring_dataset, items,
                                           sess.src, settings.uploads_root)

    if not items:
        # Ditolak dengan sebabnya, bukan ZIP kosong yang baru ketahuan salah
        # setelah diunduh dan dibuka.
        return Response(
            "belum ada gambar yang dimasukkan ke dataset. Buka Anotasi, pilih "
            "gambar yang sudah selesai, lalu tekan Tambahkan ke dataset.",
            status_code=409, media_type="text/plain; charset=utf-8")
    # Tag ikut sebagai berkas terpisah: tidak satu pun format dataset punya
    # tempat untuknya, dan menyembunyikannya berarti keterangan yang sudah
    # dipasang orang hilang begitu datanya keluar dari aplikasi ini.
    tdata_tag = await asyncio.to_thread(svc_tag.baca, sess.src)
    tag_peta = {}
    for it in items:
        r = svc_tag.untuk(tdata_tag, svc_tag.kunci_gambar(sess.src, it["img"]))
        if r["tag"] or r["batch"]:
            tag_peta[it["img"].name] = r

    data = await asyncio.to_thread(export.zip_dataset, items, nama, format,
                                   bool(gambar), export.baca_rasio(split), names,
                                   rencana_dipakai, tag_peta)
    berkas = f"{nama}-{format}.zip"
    r = Response(data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{berkas}"',
        "Content-Length": str(len(data)),
    })
    # Penanda "byte-nya sudah mulai mengalir", dibaca browser lewat cookie.
    #
    # Unduhan lewat <a href> tidak punya kejadian yang bisa ditunggu JS: begitu
    # diklik, tidak ada apa pun yang memberi tahu bahwa servernya sedang
    # bekerja. Padahal ZIP paragon 1,27 GB butuh 33 detik untuk dibentuk, dan
    # dataset 11 ribu gambar sekitar 13 menit. Selama itu tombolnya tampak
    # rusak. Cookie ini menandai balasannya sudah dikirim, sehingga JS bisa
    # berhenti menampilkan "menyiapkan".
    if tanda:
        r.set_cookie("unduh_siap", str(tanda), max_age=120, path="/",
                     samesite="lax")
    return r


# ============================================================
# PEMBELAHAN TRAIN / VALID / TEST
# ============================================================
#
# Dijalankan sebagai langkah tersendiri, bukan diam-diam saat mengunduh.
# Dua alasan. Pertama, membaca isi tiap gambar memakan waktu — diukur 56 ms
# per gambar, jadi satu juta gambar berarti berjam-jam; itu tidak boleh
# menggantung di dalam satu permintaan HTTP. Kedua, dan lebih penting:
# pembelahan yang tidak bisa diperiksa lebih dulu adalah pembelahan yang
# tidak bisa dipercaya. Angkanya harus bisa dibaca SEBELUM ZIP puluhan
# gigabyte dibuat.


def ringkas_rencana(r: dict | None) -> dict | None:
    """Rencana tanpa `peta`-nya.

    Petanya bisa memuat sejuta nama berkas; mengirimnya ke browser di setiap
    pembaruan ringkasan akan menghabiskan memori di kedua sisi tanpa ada yang
    membacanya.
    """
    if not r:
        return None
    return {k: v for k, v in r.items() if k != "peta"}


@router.post("/api/split/jalankan")
async def split_jalankan(split_q: str = Query("", alias="split"),
                         sess: Session = Depends(current_session_api),
                         settings: Settings = Depends(get_settings)):
    if sess.src is None:
        return {"ok": False, "error": "belum ada dataset terbuka"}
    # Splitting memeriksa isi tiap gambar; pada dataset sebesar sebelas ribu
    # foto itu menit-menit CPU. Hasilnya cuma menempel di sesi pemanggilnya,
    # jadi ini bukan soal merusak data orang lain — melainkan soal siapa yang
    # boleh memicu pekerjaan seberat itu berkali-kali.
    tdata = await asyncio.to_thread(tugas.baca_projek, sess.src,
                                    settings.uploads_root)
    if tdata["pemilik"] and not tugas.boleh_kelola(tdata, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang bisa "
                                      "menjalankan splitting"}
    with sess.lock:
        items = list(sess.items)
    items, hitung = await asyncio.to_thread(tugas.saring_dataset, items,
                                            sess.src, settings.uploads_root)
    if not items:
        return {"ok": False, "error": (
            "belum ada gambar yang dimasukkan ke dataset. Splitting bekerja "
            "pada isi dataset, bukan pada seluruh gambar yang diunggah.")}

    sess.split_batal = False
    split.bersihkan_maju(sess.user)
    _log.info("permintaan splitting dari %r untuk %s", sess.user, sess.src)
    try:
        r = await asyncio.to_thread(
            split.rencanakan, items, export.baca_rasio(split_q),
            kunci=sess.user, batal=lambda: sess.split_batal)
    except split.Dibatalkan:
        split.bersihkan_maju(sess.user)
        _log.warning("splitting dihentikan oleh %r", sess.user)
        return {"ok": False, "batal": True, "error": "dihentikan"}
    except Exception:
        # Dicatat lengkap dengan jejaknya. Tanpa ini, satu-satunya jejak
        # kegagalan adalah bilah progres yang berhenti di tengah, dan tidak
        # ada yang bisa dibaca sesudahnya.
        split.bersihkan_maju(sess.user)
        _log.exception("splitting GAGAL untuk %s", sess.src)
        raise
    sess.rencana_split = r
    return {"ok": True, **ringkas_rencana(r)}


@router.get("/api/split/kemajuan")
async def split_kemajuan(sess: Session = Depends(current_session_api)):
    """Ditanyakan berkala selagi /api/split/jalankan masih menggantung."""
    k = split.kemajuan(sess.user)
    return {"ok": True, "fase_nama": split.FASE.get(k.get("fase"), ""), **k}


@router.post("/api/split/batal")
async def split_batal(sess: Session = Depends(current_session_api)):
    sess.split_batal = True
    return {"ok": True}


@router.post("/api/split/lupakan")
async def split_lupakan(sess: Session = Depends(current_session_api)):
    """Kembali ke pembelahan cepat berbasis nama berkas."""
    sess.rencana_split = None
    split.bersihkan_maju(sess.user)
    return {"ok": True}
