"""
Unggah gambar dari laptop pemakai.

Dikirim satu berkas per permintaan PUT dengan bodi mentah, bukan satu form
multipart berisi ratusan berkas. Alasannya: bodi dialirkan langsung ke disk
sehingga memori server tidak menumpuk, progres bisa dihitung per berkas, dan
satu berkas gagal tidak menggagalkan seluruh batch.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import ARSIP_EXT, Settings, get_settings
from ..deps import current_session, current_session_api, is_local
from ..security import safe_relpath, safe_slug
from ..services import arsip, impor, projek, riwayat, scanner, tambah
from ..session import Session
from ..templating import templates

router = APIRouter(tags=["uploads"])

CHUNK = 256 * 1024


@router.get("/unggah", response_class=HTMLResponse)
async def halaman_unggah(request: Request, ds: str = "",
                         sess: Session = Depends(current_session),
                         settings: Settings = Depends(get_settings)):
    """
    Halaman unggah milik SATU projek.

    Projeknya ditentukan lebih dulu, dan itu inti perubahan alurnya: dulu
    memberi nama dan memilih berkas adalah satu tindakan, sehingga projek tidak
    bisa ada sebelum ada isinya. Satu projek diisi berkali-kali, dari sumber
    berbeda, pada hari berbeda; memaksa keduanya bersamaan berarti memaksa
    seluruh gambar siap sebelum boleh memberi nama.
    """
    nama = projek.bersihkan_nama(ds)
    d = Path(settings.uploads_root) / sess.user / nama if nama else None
    if not nama or d is None or not d.is_dir():
        # Tanpa projek yang jelas, halaman ini tidak punya tujuan menyimpan.
        # Dikembalikan ke daftar projek, bukan menampilkan halaman yang
        # tombolnya semua menolak.
        return RedirectResponse("/pilih", status_code=303)
    # Dua jalur, dan yang menentukan bukan pilihan pengguna melainkan keadaan
    # projeknya. Projek kosong menerima apa saja lewat /upload, termasuk .zip
    # dan data.yaml, lalu dipindai dari nol. Projek yang sudah berisi harus
    # lewat /tambah, yang menaruh tiap berkas mengikuti tata letak yang SUDAH
    # ada di sana: dataset bersplit tetap terbagi train/valid/test, dataset
    # YOLO tetap punya images/ dan labels/. Menyamakan keduanya berarti gambar
    # baru mendarat di akar dan merusak pembagian yang sudah jalan.
    berisi = await asyncio.to_thread(projek.punya_gambar, d)
    return templates.TemplateResponse(request, "unggah.html", {
        "sess": sess,
        "local": is_local(request),
        "projek": {"nama": nama, "path": str(d)},
        "berisi": berisi,
        "tata": tambah.tata_letak(d) if berisi else "",
        "max_upload_mb": settings.max_upload_mb,
        "max_zip_mb": settings.max_zip_mb,
        "riwayat": riwayat.baca(settings, sess.user),
    })


@router.put("/upload")
async def upload(request: Request, ds: str = "", name: str = "",
                 sess: Session = Depends(current_session_api),
                 settings: Settings = Depends(get_settings)):
    # `name` boleh memuat subfolder (unggahan folder mengirim
    # webkitRelativePath), dan strukturnya dipertahankan.
    #
    # Arsip diizinkan di SINI saja, bukan di dalam isi arsip: `arsip.bongkar`
    # memanggil safe_relpath tanpa izin itu, sehingga zip di dalam zip
    # dilewati dan pembongkarannya tidak pernah berlapis.
    fn = safe_relpath(name, arsip=True)
    if not fn:
        return {"ok": False, "error": "nama atau jenis berkas tidak didukung"}

    batas, sebutan = settings.batas_untuk(fn)
    try:
        total = int(request.headers.get("content-length", 0))
    except ValueError:
        total = 0
    if total <= 0:
        return {"ok": False, "error": "berkas kosong"}
    if total > batas:
        return {"ok": False, "error": f"lebih dari {sebutan}"}

    d = sess.upload_dir(ds)
    dest = d / fn
    # Penjagaan berlapis: walau safe_relpath sudah membuang `..`, tujuan akhirnya
    # tetap diperiksa masih berada di dalam folder milik akun ini.
    try:
        dest.resolve().relative_to(d.resolve().parent.resolve() / d.name)
    except ValueError:
        return {"ok": False, "error": "tujuan di luar folder unggahan"}
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Tulis ke .part dulu, ganti nama setelah lengkap, supaya koneksi yang
    # terputus tidak meninggalkan berkas setengah jadi yang ikut terpindai.
    tmp = dest.with_suffix(dest.suffix + ".part")

    written = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                if not chunk:
                    continue
                written += len(chunk)
                if written > batas:
                    raise ValueError(f"lebih dari {sebutan}")
                f.write(chunk)
        if written == 0:
            raise ValueError("tidak ada data yang diterima")
        tmp.replace(dest)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return {"ok": False, "error": str(e)[:90]}

    return {"ok": True, "name": fn, "bytes": written,
            "arsip": Path(fn).suffix.lower() in ARSIP_EXT}


@router.post("/unzip")
async def unzip(ds: str = "", name: str = "",
                sess: Session = Depends(current_session_api),
                settings: Settings = Depends(get_settings)):
    """
    Bongkar arsip yang sudah terunggah, di tempat.

    Dipisah dari /upload supaya kegagalan bisa dibedakan: arsip yang terkirim
    utuh tetapi isinya ditolak berbeda dari arsip yang gagal terkirim, dan
    keduanya perlu pesan yang berbeda. Pemisahan ini juga membuat unggahan
    besar tidak menahan koneksi selama pembongkaran.
    """
    fn = safe_relpath(name, arsip=True)
    if not fn or Path(fn).suffix.lower() not in ARSIP_EXT:
        return {"ok": False, "error": "yang diminta bukan berkas arsip"}

    d = sess.upload_dir(ds)
    zp = d / fn
    if not zp.is_file():
        return {"ok": False, "error": "arsipnya tidak ada di folder unggahan"}

    try:
        hasil = await asyncio.to_thread(
            arsip.bongkar, zp, d,
            maks_byte=settings.max_zip_bytes * settings.zip_ratio_max,
            maks_entri=settings.zip_entries_max)
    except arsip.ArsipTolak as e:
        return {"ok": False, "error": str(e)[:140]}
    except OSError as e:
        return {"ok": False, "error": f"gagal membongkar: {str(e)[:80]}"}

    if not hasil["ditulis"]:
        return {"ok": False,
                "error": "tidak ada berkas yang bisa dipakai di dalam arsip itu"}

    # Arsipnya dibuang setelah isinya selamat: menyimpan salinan 1 GB yang
    # sudah ada bentuk terbongkarnya hanya menghabiskan disk, dan berkas itu
    # tidak pernah ikut terbaca sebagai bagian dataset.
    zp.unlink(missing_ok=True)
    return {"ok": True, "n": hasil["ditulis"], "dilewati": hasil["dilewati"],
            "bytes": hasil["bytes"], "contoh_dilewati": hasil["contoh_dilewati"],
            "arsip_dihapus": True}


@router.get("/api/impor/survei")
async def impor_survei(path: str = "",
                       sess: Session = Depends(current_session_api)):
    """Berapa banyak dan berapa besar yang akan disalin — sebelum menyalin."""
    d = Path((path or "").strip()).expanduser()
    if not d.is_dir():
        return {"ok": False, "error": "folder tidak ada di server"}
    s = await asyncio.to_thread(impor.survei, d)
    return {"ok": True, "nama_usul": safe_slug(d.name), **s}


@router.get("/api/impor/kemajuan")
async def impor_kemajuan(sess: Session = Depends(current_session_api)):
    """
    Sudah berapa banyak yang tersalin.

    Penyalinan berjalan di thread terpisah sementara permintaan /impor
    menggantung sampai selesai, jadi kemajuannya tidak bisa ikut di balasan
    permintaan itu. Ditanyakan lewat permintaan terpisah — dan itu bisa
    dilayani justru karena penyalinannya tidak menahan event loop.
    """
    return {"ok": True, **impor.kemajuan(sess.user)}


@router.post("/impor")
async def impor_dari_server(path: str = "", ds: str = "",
                            sess: Session = Depends(current_session_api),
                            settings: Settings = Depends(get_settings)):
    """
    Salin dataset dari sebuah path di server ke folder unggahan akun ini.

    Alur yang sama dengan unggah dari laptop, hanya sumbernya berbeda. Bedanya
    dengan /setsrc penting: /setsrc membuka folder ITU JUGA, sehingga menyunting
    berarti mengubah dataset aslinya. Di sini yang dibuka adalah SALINAN, dan
    folder sumber tidak pernah ditulis sama sekali.
    """
    sumber = Path((path or "").strip()).expanduser()
    # Diserahkan apa adanya ke upload_dir, yang memegang satu-satunya aturan
    # nama projek. Membersihkannya di sini lebih dulu dengan aturan yang
    # berbeda membuat unggahan mendarat di folder yang bukan projeknya.
    tujuan = sess.upload_dir(ds or sumber.name)
    nama = tujuan.name
    try:
        hasil = await asyncio.to_thread(impor.impor_folder, sumber, tujuan,
                                        kunci=sess.user)
    except impor.ImporTolak as e:
        impor.catat_maju(sess.user, tahap="gagal")
        return {"ok": False, "error": str(e)[:160]}
    except OSError as e:
        impor.catat_maju(sess.user, tahap="gagal")
        return {"ok": False, "error": f"gagal menyalin: {str(e)[:90]}"}

    n = len(await asyncio.to_thread(sess.load, tujuan))
    peringatan = await asyncio.to_thread(scanner.periksa_kelengkapan, tujuan)
    riwayat.catat(settings, sess.user, sumber.resolve(), "salin")
    impor.catat_maju(sess.user, tahap="selesai")
    return {"ok": True, "nama": nama, "dir": str(tujuan), "n": n,
            "disalin": hasil["berkas"], "dilewati": hasil["dilewati"],
            "bytes": hasil["bytes"], "peringatan": peringatan,
            "contoh_dilewati": hasil["contoh_dilewati"],
            "bentrok": hasil["bentrok"]}


def _siap_ditambahi(sess: Session, settings: Settings) -> str:
    if sess.src is None:
        return "belum ada dataset yang dibuka"
    return tambah.boleh_ditambahi(sess.src,
                                  settings.uploads_root / safe_slug(sess.user))


@router.put("/tambah")
async def tambah_berkas(request: Request, name: str = "",
                        sess: Session = Depends(current_session_api),
                        settings: Settings = Depends(get_settings)):
    """
    Tambahkan satu berkas ke dataset yang SEDANG dibuka.

    Berbeda dengan /upload, yang menaruh berkas di folder unggahan bernama
    tersendiri. Di sini berkasnya menyatu ke dataset yang terbuka, dan
    letaknya ditentukan tata letak dataset itu — bukan tata letak folder di
    laptop pengirim. Lihat tambah.Penempat.
    """
    galat = _siap_ditambahi(sess, settings)
    if galat:
        return {"ok": False, "error": galat}

    fn = safe_relpath(name)
    if not fn:
        return {"ok": False, "error": "nama atau jenis berkas tidak didukung"}

    batas, sebutan = settings.batas_untuk(fn)
    try:
        total = int(request.headers.get("content-length", 0))
    except ValueError:
        total = 0
    if total <= 0:
        return {"ok": False, "error": "berkas kosong"}
    if total > batas:
        return {"ok": False, "error": f"lebih dari {sebutan}"}

    # Penempatnya dipegang sesi, bukan dibuat ulang tiap berkas: satu seretan
    # folder mengirim ratusan permintaan terpisah, dan perbandingan split baru
    # terjaga kalau keputusannya dihitung atas keadaan yang sama-sama berjalan.
    with sess.lock:
        penempat = sess.penempat_tambah()
        dest = penempat.tujuan(fn)
    if dest is None:
        return {"ok": False, "error": "hanya gambar dan anotasi yang bisa "
                                      "ditambahkan ke dataset yang sudah ada"}

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    ditulis = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                if not chunk:
                    continue
                ditulis += len(chunk)
                if ditulis > batas:
                    raise ValueError(f"lebih dari {sebutan}")
                f.write(chunk)
        if ditulis == 0:
            raise ValueError("tidak ada data yang diterima")
        akhir, hasil = await asyncio.to_thread(tambah.pasang, tmp, dest)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return {"ok": False, "error": str(e)[:90]}

    if akhir is not None:
        with sess.lock:
            penempat.catat(fn, akhir)
    return {"ok": True, "name": fn, "bytes": ditulis, "hasil": hasil,
            "split": akhir.parent.parent.name if akhir and
            penempat.tata == tambah.TATA_SPLIT else ""}


@router.post("/tambah/impor")
async def tambah_dari_server(path: str = "",
                             sess: Session = Depends(current_session_api),
                             settings: Settings = Depends(get_settings)):
    """Gabungkan folder di server ke dataset yang sedang dibuka."""
    galat = _siap_ditambahi(sess, settings)
    if galat:
        return {"ok": False, "error": galat}

    sumber = Path((path or "").strip()).expanduser()
    tujuan = sess.src
    if impor._didalam(tujuan, sumber) or impor._didalam(sumber, tujuan):
        return {"ok": False, "error": "folder itu berada di dalam datasetnya "
                                      "sendiri — tidak ada yang perlu ditambah"}
    with sess.lock:
        penempat = sess.penempat_tambah()
    try:
        hasil = await asyncio.to_thread(
            impor.impor_folder, sumber, tujuan, kunci=sess.user,
            tentukan=penempat.tujuan, lapor_nama=penempat.catat)
    except impor.ImporTolak as e:
        impor.catat_maju(sess.user, tahap="gagal")
        return {"ok": False, "error": str(e)[:160]}
    except OSError as e:
        impor.catat_maju(sess.user, tahap="gagal")
        return {"ok": False, "error": f"gagal menyalin: {str(e)[:90]}"}

    n = len(await asyncio.to_thread(sess.load, tujuan))
    peringatan = await asyncio.to_thread(scanner.periksa_kelengkapan, tujuan)
    impor.catat_maju(sess.user, tahap="selesai")
    return {"ok": True, "n": n, "ditambah": hasil["berkas"],
            "sudah_ada": hasil["sudah_ada"], "dilewati": hasil["dilewati"],
            "bytes": hasil["bytes"], "peringatan": peringatan,
            "contoh_dilewati": hasil["contoh_dilewati"],
            "bentrok": hasil["bentrok"]}


@router.post("/useupload")
async def use_upload(ds: str = "", sess: Session = Depends(current_session_api)):
    """Buka folder hasil unggahan sebagai dataset yang sedang diperiksa."""
    d = sess.upload_dir(ds)
    if not d.is_dir():
        return {"ok": False, "error": "folder unggahan belum ada"}
    n = len(await asyncio.to_thread(sess.load, d))
    if not n:
        return {"ok": False, "error": "tidak ada gambar terbaca di unggahan itu"}
    # Peringatan dikirim bersama hasil, bukan sebagai kegagalan: datasetnya
    # tetap bisa dibuka, hanya ada yang perlu diketahui lebih dulu.
    peringatan = await asyncio.to_thread(scanner.periksa_kelengkapan, d)
    return {"ok": True, "dir": str(d), "n": n, "nama": d.name,
            "peringatan": peringatan}
