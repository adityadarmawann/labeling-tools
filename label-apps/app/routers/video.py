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
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..config import (KLIP, KLIP_BURUK, KLIP_DITOLAK, SUMBER_VIDEO, VIDEO_EXT,
                      Settings, get_settings)
from ..deps import bodi_json, current_session, current_session_api
from ..security import safe_relpath, safe_slug
from ..services import (export, klip, klip_filter, klip_olah, klip_scan,
                        klip_tag, projek, tugas, versi, video_ingest)
from ..session import Session
from ..templating import templates

router = APIRouter(tags=["video"])

# Tipe MIME per ekstensi klip, supaya elemen <video> tahu codec yang disajikan.
# Tanpa Content-Type yang benar, sebagian peramban menolak memutarnya.
KLIP_MIME = {".mp4": "video/mp4", ".webm": "video/webm",
             ".mkv": "video/x-matroska", ".mov": "video/quicktime",
             ".avi": "video/x-msvideo", ".m4v": "video/x-m4v"}


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


@router.post("/api/video/filter")
async def filter_objek(request: Request, ds: str = "", batch: str = "",
                       pratinjau: bool = True,
                       sess: Session = Depends(current_session_api),
                       settings: Settings = Depends(get_settings)):
    """
    Saring klip `klip/<batch>/` menurut kehadiran OBJEK sasaran (rencana C.3).

    Konfig filter (metode/model/kelas/conf/min_frame/setiap/hsv — lihat
    klip_filter.KONFIG_BAWAAN) dikirim sebagai bodi JSON; bodi kosong memakai
    bawaan (metode "warna", bola oranye). `pratinjau=true` (bawaan) = DRY-RUN:
    hanya menghitung berapa yang lolos, TAK memindah apa pun. `pratinjau=false`
    = TERAPKAN: klip yang ditolak dipindah ke klip/_ditolak/<batch>/ (dipulihkan,
    tak dihapus) dan laporan ditulis ke klip/<batch>/.filter.json.

    Kerja berat (baca frame, inferensi) dilempar ke thread lewat to_thread
    supaya event loop tak membeku. siap_filter diperiksa dulu: metode "yolo"
    tanpa ultralytics/model menjawab pesan jelas, bukan jatuh senyap.
    """
    d, err = _projek_video(sess, settings, ds)
    if err:
        return {"ok": False, "error": err}

    konfig = klip_filter.konfig_sah(await bodi_json(request))
    siap, alasan = klip_filter.siap_filter(konfig["metode"], konfig.get("model"))
    if not siap:
        return {"ok": False, "error": alasan}

    try:
        if pratinjau:
            hasil = await asyncio.to_thread(
                klip_filter.pratinjau, d, batch, konfig)
        else:
            hasil = await asyncio.to_thread(
                klip_filter.terapkan, d, batch, konfig)
    except klip_filter.FilterTolak as e:
        return {"ok": False, "error": str(e)[:160]}
    except Exception as e:                        # noqa: BLE001
        return {"ok": False, "error": f"gagal menyaring: {str(e)[:90]}"}

    return hasil


def _kunci_scrape(user: str, ds: str) -> str:
    """Kunci kemajuan scrape, diturunkan dari user+ds — BUKAN dikirim peramban,
    supaya satu sesi tak bisa mengintip kemajuan sesi lain dengan menebak kunci."""
    return f"scrape:{user}:{ds}"


@router.post("/api/video/url")
async def ingest_url(ds: str = "", url: str = "",
                     sess: Session = Depends(current_session_api),
                     settings: Settings = Depends(get_settings)):
    """
    Unduh satu video YouTube ke `_sumber/` projek (tempel-URL, rencana C.1b).

    Selalu tersedia selama siap_ingest() (yt-dlp + ffmpeg ada) — tak tunduk pada
    saklar scraper. Host diverifikasi di services (anti-SSRF). Videonya TIDAK
    dipotong di sini; pemotongan terpisah lewat /api/video/potong.
    """
    siap, alasan = video_ingest.siap_ingest()
    if not siap:
        return {"ok": False, "error": alasan}

    d, err = _projek_video(sess, settings, ds)
    if err:
        return {"ok": False, "error": err}

    dest = d / SUMBER_VIDEO
    try:
        hasil = await asyncio.to_thread(video_ingest.dari_url, url, dest)
    except Exception as e:                        # noqa: BLE001
        return {"ok": False, "error": f"gagal mengunduh: {str(e)[:90]}"}
    return hasil


@router.post("/api/video/scrape")
async def ingest_scrape(ds: str = "", query: str = "", maks: int = 10,
                        sess: Session = Depends(current_session_api),
                        settings: Settings = Depends(get_settings)):
    """
    Scrape kata kunci YouTube -> unduh massal ke `_sumber/` (rencana C.1c).

    DIGERBANG saklar scraper (LABELAPP_SCRAPER, MATI bawaan — lihat MEMORY
    "label-apps tertutup dari internet"): kalau mati, menjawab pesan jelas
    "scraper dimatikan", bukan diam-diam tak melakukan apa-apa. Kalau nyala,
    berjalan di THREAD LATAR (unduh banyak video memakan menit; menahannya di
    event loop membekukan seluruh server) dengan kemajuan yang di-poll lewat
    GET /api/video/kemajuan.
    """
    if not video_ingest.scraper_aktif(settings):
        return {"ok": False, "error": (
            "scraper dimatikan di server ini — nyalakan dengan "
            "LABELAPP_SCRAPER=1 kalau unduh massal dari YouTube memang "
            "diinginkan (tempel-URL satuan dan unggah berkas tetap bisa)")}
    siap, alasan = video_ingest.siap_ingest()
    if not siap:
        return {"ok": False, "error": alasan}

    d, err = _projek_video(sess, settings, ds)
    if err:
        return {"ok": False, "error": err}

    q = " ".join((query or "").split())
    if not q:
        return {"ok": False, "error": "kata kunci masih kosong"}
    maks = video_ingest.sah_maks(maks)
    dest = d / SUMBER_VIDEO
    kunci = _kunci_scrape(sess.user, ds)

    projek.bersihkan_maju(kunci)
    projek.catat_maju(kunci, tahap="mulai", persen=0.0)

    def _kerja() -> None:
        try:
            hasil = video_ingest.scrape(
                q, maks, dest,
                maju=lambda info: projek.catat_maju(kunci, **info))
            projek.catat_maju(kunci, tahap="selesai", persen=1.0,
                              **{k: v for k, v in hasil.items() if k != "ok"})
        except Exception as e:                    # noqa: BLE001
            projek.catat_maju(kunci, tahap="gagal", error=str(e)[:120])

    threading.Thread(target=_kerja, daemon=True).start()
    return {"ok": True, "mulai": True, "maks": maks}


@router.get("/api/video/kemajuan")
async def ingest_kemajuan(ds: str = "",
                          sess: Session = Depends(current_session_api),
                          settings: Settings = Depends(get_settings)):
    """Kemajuan scrape yang sedang berjalan untuk projek ini (polling).

    Kuncinya diturunkan dari user+ds di server, jadi satu akun hanya bisa
    menengok kemajuan scrape-nya sendiri."""
    return {"ok": True, **projek.kemajuan(_kunci_scrape(sess.user, ds))}


# ============================================================
# LANGKAH 6 — PELABELAN KLIP (classifier aksi)
# ============================================================
#
# Klip adalah "gambar"-nya projek video: unitnya kerja, kuncinya nama relatif
# (klip/<batch>/<file>), dan RBAC + kurasi dataset dipakai ulang APA ADANYA
# lewat kunci itu (tugas.boleh_labeli/di_dataset/masukkan). Tidak ada kode RBAC
# baru di sini — hanya terjemahan HTTP, sama seperti router lain.


def _projek_aksi(sess: Session, settings: Settings, ds: str, *,
                 api: bool = True):
    """(folder, data_tugas, pesan_error) untuk operasi pelabelan klip.

    Gerbang BACA, bukan unggah: pelabel berhak melihat & melabeli klip walau
    tak berhak mengunggah, jadi di sini cukup boleh_lihat (RBAC dipakai ulang).
    Yang BUKAN anggota tak teratasi temukan sama sekali (None), persis seperti
    rute gambar. Hak MENULIS label satu klip dicek terpisah per-klip lewat
    tugas.boleh_labeli — karena labeler spesifik berhak atas sebagian klip saja.

    Projek WAJIB video + classifier aksi aktif: tanpa itu tak ada daftar kelas
    untuk melabeli, dan halaman/rutenya tak punya arti.
    """
    d = projek.temukan(settings.uploads_root, sess.user, ds)
    if d is None:
        return None, None, "projek tidak ada atau kamu bukan anggotanya"
    tdata = tugas.baca_projek(d, settings.uploads_root)
    if not tugas.boleh_lihat(tdata, sess.user):
        return None, None, "kamu bukan anggota projek ini"
    if projek.jenis_projek(d) != "video" or not tugas.aksi_aktif(tdata):
        return None, None, "projek ini bukan classifier aksi video"
    return d, tdata, ""


def _batch_klip(d, rel: str) -> str:
    """Batch sebuah klip = folder tepat di bawah klip/ (otoritatif dari tata
    letak), dengan .klip.json sebagai cadangan — persis cara klip_scan.pindai.

    Dipakai scope labeler spesifik (tugas.boleh_labeli): tanpa batch yang benar,
    pengecekannya jatuh ke penugasan papan saja dan menolak yang seharusnya boleh.
    """
    from pathlib import Path as _P

    bagian = _P(rel).parts
    if len(bagian) >= 3 and bagian[0] == KLIP:
        return bagian[1]
    return klip_tag.untuk(d, rel).get("batch", "")


def _resolve_klip(d, name: str):
    """(Path|None, pesan_error): selesaikan `name` ke berkas klip DI DALAM
    klip/ projek, dengan aman.

    Setidaknya seketat penyajian gambar: `..`, path absolut, dan apa pun yang
    keluar dari klip/ ditolak lewat resolve()+relative_to. Folder internal
    (_ditolak hasil filter, _bad klip cacat) dan berkas bertitik ditolak juga —
    bawaannya HANYA klip hidup yang disajikan/dilabeli, bukan yang sudah dibuang.
    """
    from pathlib import Path as _P

    rel = (name or "").strip().lstrip("/")
    if not rel:
        return None, "klip tidak disebut"
    base = (_P(d) / KLIP).resolve()
    try:
        target = (_P(d) / rel).resolve()
        sisa = target.relative_to(base)
    except (ValueError, OSError):
        return None, "klip di luar folder klip projek"
    if any(p.startswith(".") or p in (KLIP_DITOLAK, KLIP_BURUK)
           for p in sisa.parts):
        return None, "klip itu tidak tersedia untuk dilabeli"
    if target.suffix.lower() not in VIDEO_EXT:
        return None, "yang diminta bukan berkas klip"
    if not target.is_file():
        return None, "klipnya tidak ada di projek ini"
    return target, ""


def _range_klip(request: Request, path) -> Response:
    """Sajikan berkas klip dengan dukungan HTTP Range (206) untuk <video>.

    Elemen <video> meminta potongan byte (Range) untuk menyeek/scrub; tanpa 206
    + Content-Range yang benar, bilah geser videonya tak berfungsi dan sebagian
    peramban menolak memutar sama sekali. Permintaan tanpa Range dijawab 200
    tetapi tetap mengiklankan Accept-Ranges supaya peramban tahu seek didukung.
    """
    size = path.stat().st_size
    ctype = KLIP_MIME.get(path.suffix.lower(), "application/octet-stream")
    dasar = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=300"}
    rng = (request.headers.get("range") or "").strip().lower()
    if rng.startswith("bytes="):
        spec = rng.split("=", 1)[1].split(",")[0].strip()
        awal, _, akhir = spec.partition("-")
        try:
            if awal == "":
                # bentuk suffix "bytes=-N": N byte terakhir.
                n = int(akhir)
                start, end = max(0, size - n), size - 1
            else:
                start = int(awal)
                end = int(akhir) if akhir else size - 1
        except ValueError:
            return Response(status_code=416,
                            headers={"Content-Range": f"bytes */{size}"})
        if start >= size or start > end:
            return Response(status_code=416,
                            headers={"Content-Range": f"bytes */{size}"})
        end = min(end, size - 1)
        panjang = end - start + 1
        with open(path, "rb") as f:
            f.seek(start)
            potongan = f.read(panjang)
        return Response(potongan, status_code=206, media_type=ctype,
                        headers={**dasar,
                                 "Content-Range": f"bytes {start}-{end}/{size}",
                                 "Content-Length": str(panjang)})
    return Response(path.read_bytes(), media_type=ctype, headers=dasar)


def _subset_pelabel(d, data: dict, user: str, pindai: dict, *,
                    job: str = "") -> list[dict]:
    """Klip TERURUT yang `user` BOLEH labeli — subset yang dirender halaman aksi.

    RBAC dipakai ulang apa adanya: owner/editor/labeler-menyeluruh dapat semua,
    labeler-spesifik hanya batch dalam scope-nya ATAU klip yang ditugaskan ke
    dia lewat papan (tugas.boleh_labeli dengan kunci klip + batch). `job`
    (opsional) mempersempit ke klip penugasan itu — padanan gambar_konteks_job
    untuk klip, hanya tanpa severity (klip "berlabel" = labelnya tak kosong).
    """
    items = pindai["items"]
    if job:
        j = (data.get("tugas") or {}).get(job)
        punya = set(j.get("gambar") or []) if j else set()
        items = [it for it in items if it["rel"] in punya]
    keluar = []
    for it in items:
        rel, batch = it["rel"], it["batch"]
        if not tugas.boleh_labeli(data, user, rel, batch):
            continue
        keluar.append({"rel": rel, "label": it["label"], "batch": batch,
                       "srcid": it["srcid"],
                       "di_dataset": tugas.sudah_dimasukkan(data, rel)})
    return keluar


def _versi_aksi(d) -> list[dict]:
    """Versi klip AKSI yang sudah dibangun (hasil.jenis == 'aksi'), untuk daftar
    unduhan di halaman aksi.

    versi.daftar sudah membuang peta/gambar yang bisa puluhan ribu baris; di sini
    tinggal menyaring ke versi aksi saja (versi GAMBAR hidup di .versi/ yang sama
    tapi ber-hasil berbeda) dan hanya membawa medan yang dipakai UI."""
    out = []
    for v in versi.daftar(d):
        if (v.get("hasil") or {}).get("jenis") != "aksi":
            continue
        out.append({"nomor": v.get("nomor"), "n": v.get("n", 0),
                    "kelas": v.get("kelas", 0), "jumlah": v.get("jumlah") or {}})
    return out


def _awal_aksi(d, data: dict, user: str, pindai: dict, *, job: str = "") -> dict:
    """Bahan yang dibutuhkan halaman/skrip aksi untuk merender: daftar kelas,
    kelas negatif, dan subset klip pelabel ini beserta labelnya (resumable)."""
    ak = data.get("aksi") or {}
    klips = _subset_pelabel(d, data, user, pindai, job=job)
    return {
        "kelas": list(ak.get("kelas") or []),
        "warna": list(ak.get("warna") or []),
        "negatif": ak.get("negatif") or "",
        "klips": klips,
        "n_label": sum(1 for k in klips if k["label"]),
        "n_dataset": sum(1 for k in klips if k["di_dataset"]),
        "boleh_kelola": tugas.boleh_kelola(data, user),
    }


@router.get("/aksi", response_class=HTMLResponse)
async def halaman_aksi(request: Request, ds: str = "", job: str = "",
                       saring: str = "semua",
                       sess: Session = Depends(current_session),
                       settings: Settings = Depends(get_settings)):
    """
    Halaman pelabelan klip: pemutar <video> + tombol kelas berpintasan angka.

    Pengganti web multi-user dari review_reclassify_v4 (tkinter): putar klip,
    tekan angka kelas / Skip / Hapus-label / Ulang, panah kiri-kanan menyusuri
    subset penugasan ini. Resumable (label tiap klip tampil), aman banyak orang
    (izin dicek per-klip di rute tulis). Klip yang BOLEH dilabeli pemakai ini
    yang dirender (owner/editor/labeler-menyeluruh: semua; labeler-spesifik:
    batch-nya), persis subset yang diizinkan boleh_labeli.
    """
    d = projek.temukan(settings.uploads_root, sess.user, ds)
    if d is None:
        return RedirectResponse("/pilih", status_code=303)
    tdata = tugas.baca_projek(d, settings.uploads_root)
    # Projek video non-aksi (atau image) tak punya daftar kelas klip: dialihkan
    # ke papan anotasi gambar, bukan menampilkan halaman yang tombolnya kosong.
    if projek.jenis_projek(d) != "video" or not tugas.aksi_aktif(tdata):
        return RedirectResponse(f"/anotasi?ds={ds}", status_code=303)
    # Projek dibuka di sesi ini supaya sidebar & pencabutan hak selaras dengan
    # halaman lain. Projek video tak punya gambar jadi pemindaiannya nyaris nol.
    if str(sess.src or "") != str(d):
        await asyncio.to_thread(sess.load, d)

    pr = await asyncio.to_thread(projek.konteks, d, settings.uploads_root,
                                 sess.user)
    pindai = await asyncio.to_thread(klip_scan.pindai, d)
    awal = {"ds": pr["ds"], "job": job,
            **_awal_aksi(d, tdata, sess.user, pindai, job=job),
            "versi": await asyncio.to_thread(_versi_aksi, d)}
    return templates.TemplateResponse(request, "aksi.html", {
        "sess": sess, "pr": pr, "aktif": "aksi",
        "awal": awal,
        "saring": saring if saring in ("semua", "belum", "sudah") else "semua",
        "boleh_kelola": tugas.boleh_kelola(tdata, sess.user),
    })


@router.get("/api/aksi/keterangan")
async def aksi_keterangan(ds: str = "", job: str = "",
                          sess: Session = Depends(current_session_api),
                          settings: Settings = Depends(get_settings)):
    """Subset klip pelabel + daftar kelas, sebagai JSON (dipakai aksi.js untuk
    menyegarkan setelah melabeli tanpa memuat ulang halaman)."""
    d, tdata, err = _projek_aksi(sess, settings, ds)
    if err:
        return {"ok": False, "error": err}
    pindai = await asyncio.to_thread(klip_scan.pindai, d)
    return {"ok": True, **_awal_aksi(d, tdata, sess.user, pindai, job=job)}


@router.get("/klip")
async def sajikan_klip(request: Request, ds: str = "", name: str = "",
                       sess: Session = Depends(current_session_api),
                       settings: Settings = Depends(get_settings)):
    """
    Sajikan byte satu klip dari klip/<batch>/..., dengan dukungan Range (206).

    Digerbang persis seperti penyajian gambar: projek diselesaikan lewat temukan
    (bukan anggota -> None) lalu boleh_lihat. Path diselesaikan AMAN di dalam
    klip/ (menolak `..`, absolut, _ditolak/_bad). Dikembalikan Response, bukan
    JSON {ok:false}: ini media untuk elemen <video>, galatnya kode status.
    """
    d = projek.temukan(settings.uploads_root, sess.user, ds)
    if d is None:
        return Response(status_code=404)
    tdata = tugas.baca_projek(d, settings.uploads_root)
    if not tugas.boleh_lihat(tdata, sess.user):
        return Response(status_code=403)
    target, err = _resolve_klip(d, name)
    if err:
        return Response(status_code=404)
    return _range_klip(request, target)


@router.post("/api/aksi/label")
async def label_klip(ds: str = "", klip: str = "", label: str = "",
                     sess: Session = Depends(current_session_api),
                     settings: Settings = Depends(get_settings)):
    """
    Tetapkan label satu klip (atau "" untuk menghapusnya).

    Penjaga sama persis dengan menyimpan anotasi gambar: izin dicek PER-KLIP
    (tugas.alasan_tolak dengan kunci klip + batch-nya), jadi labeler spesifik
    hanya bisa menulis batch dalam scope-nya dan tak bisa menimpa kerja orang.
    `label` wajib salah satu kelas aksi projek; "" mengosongkan (klip kembali
    belum-dilabeli, batch/srcid tetap — itu metadata asal, bukan keputusan).
    """
    d, tdata, err = _projek_aksi(sess, settings, ds)
    if err:
        return {"ok": False, "error": err}
    # Klip WAJIB benar-benar ada di klip/ projek: tanpa ini rute jadi jalan
    # menulis kunci sembarang ke .klip.json (persis alasan tag.py menyaring
    # lewat sess.find). _resolve_klip juga menolak _ditolak/_bad & `..`.
    target, err = _resolve_klip(d, klip)
    if err:
        return {"ok": False, "error": err}
    rel = klip_tag.kunci_klip(d, target)
    batch = _batch_klip(d, rel)

    lab = " ".join((label or "").split())
    kelas = (tdata.get("aksi") or {}).get("kelas") or []
    if lab and lab not in kelas:
        return {"ok": False,
                "error": f"'{lab[:40]}' bukan kelas aksi projek ini"}

    tolak = tugas.alasan_tolak(tdata, sess.user, rel, batch)
    if tolak:
        return {"ok": False, "error": tolak}

    # srcid dipertahankan: dari sidecar kalau ada, kalau tidak ditebak dari nama
    # (`<slug>_<srcid>_tNNNN`) — dipakai pembelahan anti-bocor per sumber nanti.
    srcid = (klip_tag.untuk(d, rel).get("srcid")
             or klip_scan._srcid_dari_nama(target.stem) or None)
    r = await asyncio.to_thread(klip_tag.set_label, d, rel, lab,
                                batch=batch or None, srcid=srcid)
    h = await asyncio.to_thread(klip_scan.hitung, d)
    # Angka proyek diberi nama tersendiri supaya tak menimpa "klip"/"berlabel"
    # milik balasan ini (hitung() mengembalikan dua kunci bernama sama).
    return {"ok": True, "klip": rel, "label": lab, "berlabel": r["berlabel"],
            "n_klip": h["klip"], "n_berlabel": h["berlabel"]}


@router.post("/api/aksi/dataset")
async def aksi_ke_dataset(request: Request, ds: str = "",
                          sess: Session = Depends(current_session_api),
                          settings: Settings = Depends(get_settings)):
    """
    Masukkan borongan klip yang SUDAH dilabeli ke dataset (tombol "Tambah ke
    dataset" di halaman aksi).

    Memakai ulang tugas.masukkan apa adanya (kunci klip = kunci gambar). Yang
    dimasukkan DISARING di server, bukan dipercaya dari peramban: tiap klip
    harus benar-benar ada, sudah berlabel, dan BOLEH disunting pemakai ini
    (boleh_labeli per-klip) — supaya "pilih semua" seorang pelabel tak menyeret
    klip orang lain, dan klip belum-berlabel tak diam-diam masuk dataset.
    """
    d, tdata, err = _projek_aksi(sess, settings, ds)
    if err:
        return {"ok": False, "error": err}
    # Folder dataset bersama tak punya pemilik/alur dataset — tapi projek video
    # selalu di ruang unggahan (berpemilik), jadi ini sekadar jaga-jaga selaras
    # dengan rute gambar.
    if not tdata["pemilik"]:
        return {"ok": False, "error": "projek ini tak punya alur dataset"}

    body = await bodi_json(request)
    minta = body.get("klip")
    if not isinstance(minta, list) or not minta:
        return {"ok": False, "error": "belum memilih klip yang dimasukkan"}

    pindai = await asyncio.to_thread(klip_scan.pindai, d)
    label_dari = {it["rel"]: it["label"] for it in pindai["items"]}
    batch_dari = pindai["batch_dari"]
    boleh, lihat = [], set()
    for k in minta[:200_000]:
        k = str(k)
        if k in lihat or k not in label_dari or not label_dari[k]:
            continue
        if tugas.boleh_labeli(tdata, sess.user, k, batch_dari.get(k, "")):
            lihat.add(k)
            boleh.append(k)
    if not boleh:
        return {"ok": False, "error": "tidak ada klip berlabel milikmu yang "
                                      "bisa dimasukkan di sini"}
    r = await asyncio.to_thread(tugas.masukkan, d, boleh, tdata["pemilik"])
    h = await asyncio.to_thread(klip_scan.hitung, d)
    return {"ok": True, **r, **h}


# ============================================================
# LANGKAH 7 — AUGMENTASI + BALANCER + PEMBEKUAN VERSI KLIP
# ============================================================
#
# Padanan /api/versi/mulai gambar, untuk klip. POST hanya MEMULAI (build bisa
# berjam-jam pada ribuan klip; tak ada peramban/proxy yang menunggu selama itu),
# lalu peramban memantau lewat GET /api/aksi/versi/kemajuan. Build jalan di
# thread latar di bawah Giliran — sama polanya dengan scrape di berkas ini.


def _kunci_versi_aksi(user: str, ds: str) -> str:
    """Kunci kemajuan build versi klip, diturunkan dari user+ds DI SERVER —
    bukan dikirim peramban, supaya satu sesi tak mengintip build sesi lain."""
    return f"aksi-versi:{user}:{ds}"


@router.post("/api/aksi/versi/mulai")
async def aksi_versi_mulai(request: Request, ds: str = "", catatan: str = "",
                           sess: Session = Depends(current_session_api),
                           settings: Settings = Depends(get_settings)):
    """
    Mulai membangun satu versi klip beku (aug + balance) ke `.versi/vN/`.

    Digerbang persis seperti operasi klip lain: boleh_unggah (pemilik/Editor) +
    jenis video + classifier aksi aktif. Parameter aug/balance (varian, rasio_val,
    cap, fps, ukuran) dikirim sebagai bodi JSON di bawah kunci "resep"; kosong
    memakai bawaan (lihat klip_olah.resep_sah). Prasyarat ffmpeg diperiksa dulu.
    """
    siap, alasan = klip.siap_video()
    if not siap:
        return {"ok": False, "error": alasan}

    # _projek_video sudah menjaga boleh_unggah + jenis video. Classifier aksi
    # harus aktif: tanpa daftar kelas tak ada yang bisa dibekukan jadi versi.
    d, err = _projek_video(sess, settings, ds)
    if err:
        return {"ok": False, "error": err}
    tdata = await asyncio.to_thread(tugas.baca_projek, d, settings.uploads_root)
    if not tugas.aksi_aktif(tdata):
        return {"ok": False, "error": "projek ini belum punya daftar kelas aksi"}

    kunci = _kunci_versi_aksi(sess.user, ds)
    if klip_olah.kemajuan(kunci).get("jalan"):
        return {"ok": False, "error": "masih ada pembuatan versi yang berjalan"}

    resep = (await bodi_json(request)).get("resep") or {}
    pindai = await asyncio.to_thread(klip_scan.pindai, d)
    items = pindai["items"]
    if not any(it["label"] for it in items):
        return {"ok": False, "error": (
            "belum ada klip berlabel — labeli klip dulu sebelum membuat versi")}

    nomor = await asyncio.to_thread(versi.nomor_berikut, d)
    aksi = tdata.get("aksi") or {}
    oleh = sess.user

    def _kerja():
        klip_olah.jalankan_versi(d, nomor, items, aksi, resep,
                                 kunci=kunci, oleh=oleh, catatan=catatan)

    threading.Thread(target=_kerja, daemon=True).start()
    return {"ok": True, "nomor": nomor, "mulai": True}


@router.get("/api/aksi/versi/kemajuan")
async def aksi_versi_kemajuan(ds: str = "",
                              sess: Session = Depends(current_session_api),
                              settings: Settings = Depends(get_settings)):
    """Kemajuan build versi klip untuk projek ini (polling). Kuncinya
    diturunkan dari user+ds di server, jadi hanya build sendiri yang terlihat."""
    return {"ok": True, **klip_olah.kemajuan(_kunci_versi_aksi(sess.user, ds))}


@router.post("/api/aksi/versi/batal")
async def aksi_versi_batal(ds: str = "",
                           sess: Session = Depends(current_session_api),
                           settings: Settings = Depends(get_settings)):
    """Hentikan build versi klip yang sedang berjalan (keluaran setengah jadi
    dibuang oleh pekerjaannya sendiri)."""
    klip_olah.minta_batal(_kunci_versi_aksi(sess.user, ds))
    return {"ok": True}


# ============================================================
# LANGKAH 8 — EKSPOR (unduh versi klip yang sudah dibangun)
# ============================================================
#
# Padanan /ekspor gambar, untuk klip. Versi beku .versi/vN/ (dibangun Langkah 7)
# dibungkus jadi arsip folder-per-kelas lewat export.zip_aksi. Tak ada splitting/
# augmentasi di sini — semua sudah dibekukan saat build, jadi ekspor cuma
# membungkus apa adanya (anti-bocor + valid-bersih ikut terbawa).


@router.get("/api/aksi/versi/unduh")
async def aksi_versi_unduh(ds: str = "", nomor: int = 0,
                           sess: Session = Depends(current_session_api),
                           settings: Settings = Depends(get_settings)):
    """
    Unduh satu versi klip aksi yang sudah dibangun sebagai arsip ZIP
    (folder-per-kelas, train/valid) — Langkah 8.

    Gerbang BACA, bukan unggah: ANGGOTA boleh mengunduh keluaran latih/ekspor
    (pemilik "biarkan terbuka"), jadi _projek_aksi (boleh_lihat) yang dipakai —
    bukan boleh_unggah seperti rute membangun versi. Bukan anggota ditolak.
    Dikembalikan Response (bukan JSON {ok}): ini unduhan berkas, galatnya kode
    status + teks — unduhan lampiran biasa (bukan sandbox artifact, ini aplikasi).
    """
    d, tdata, err = _projek_aksi(sess, settings, ds)
    if err:
        return Response(err, status_code=403,
                        media_type="text/plain; charset=utf-8")
    if nomor <= 0:
        return Response("nomor versi tidak sah", status_code=404,
                        media_type="text/plain; charset=utf-8")
    # Versi harus TERDAFTAR dan memang versi AKSI: versi GAMBAR hidup di .versi/
    # yang sama tetapi tata letaknya berbeda — menolaknya di sini (bukan di
    # zip_aksi saja) memberi pesan yang jelas, bukan "MANIFES tak ada".
    v = await asyncio.to_thread(versi.baca, d, nomor)
    if v is None:
        return Response(f"versi v{nomor} tidak ada", status_code=404,
                        media_type="text/plain; charset=utf-8")
    if (v.get("hasil") or {}).get("jenis") != "aksi":
        return Response(f"versi v{nomor} bukan dataset klip aksi",
                        status_code=404, media_type="text/plain; charset=utf-8")

    try:
        data = await asyncio.to_thread(export.zip_aksi,
                                       klip_olah.dir_versi(d, nomor))
    except ValueError as e:
        # Terdaftar tetapi berkas hasilnya hilang (mis. sudah dibuang di disk).
        return Response(str(e), status_code=404,
                        media_type="text/plain; charset=utf-8")

    berkas = f"{safe_slug(d.name) or 'projek'}-aksi-v{nomor}.zip"
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{berkas}"',
        "Content-Length": str(len(data)),
    })
