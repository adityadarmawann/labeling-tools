"""
Unggah gambar dari laptop pemakai.

Dikirim satu berkas per permintaan PUT dengan bodi mentah, bukan satu form
multipart berisi ratusan berkas. Alasannya: bodi dialirkan langsung ke disk
sehingga memori server tidak menumpuk, progres bisa dihitung per berkas, dan
satu berkas gagal tidak menggagalkan seluruh batch.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import ARSIP_EXT, SUMBER_VIDEO, VIDEO_EXT, Settings, get_settings
from ..deps import current_session, current_session_api, is_local
from ..security import is_admin, load_users, safe_relpath, safe_slug
from ..services import (arsip, ekstraksi, impor, projek, riwayat, scanner,
                        tambah, tugas, unggah_token)
from ..session import Session
from ..templating import templates
from ..log import catat

log = catat("labelapp.uploads")

router = APIRouter(tags=["uploads"])

CHUNK = 256 * 1024


def _folder_unggah(sess: Session, settings: Settings, ds: str):
    """Folder tujuan unggah + pengecekan izin -> (Path|None, pesan_error).

    Mendukung dua bentuk `ds`, sama seperti membuka projek:
      "nama"        projek milik akun ini
      "owner/nama"  projek milik orang lain yang mengundang akun ini

    Projek yang SUDAH ada hanya boleh ditambahi oleh yang berhak mengunggah —
    pemilik atau Editor (tugas.boleh_unggah). Inilah yang membuat Editor sebuah
    projek sharing bisa menambah media ke projek pemiliknya, sementara Labeler
    (yang hanya melihat/melabeli) ditolak di sini, bukan dibiarkan menulis ke
    folder orang lain.

    Yang menjaga projek orang lain bukan penolakan di sini, melainkan cek
    boleh_unggah: Labeler sebuah projek sharing teratasi lewat temukan tapi
    gagal boleh_unggah -> ditolak, jadi ia tak bisa menulis ke folder
    pemiliknya. Yang BUKAN anggota tak teratasi temukan sama sekali, lalu jatuh
    ke upload_dir yang membelokkan bentuk "owner/nama" ke folder sendiri
    (gagal terlihat, tak pernah menyentuh folder orang lain) — dan bentuk
    "nama" biasa dibuat di ruang kerja sendiri, jalur "buat projek lalu unggah".
    """
    d = projek.temukan(settings.uploads_root, sess.user, ds)
    if d is not None:
        tdata = tugas.baca_projek(d, settings.uploads_root)
        if not tugas.boleh_unggah(tdata, sess.user):
            return None, "tidak berhak mengunggah ke projek ini"
        return d, ""
    return sess.upload_dir(ds), ""


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
    d = projek.temukan(settings.uploads_root, sess.user, ds)
    if d is None:
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
    # Tamu boleh melihat dan melabeli, tidak menambah gambar. Dikatakan di
    # halamannya, bukan dibiarkan jadi unggahan yang dijawab berhasil lalu
    # mendarat entah di mana.
    # Dulu owner-only; kini Editor projek sharing pun boleh menambah media.
    # Pengelolaan lain (ganti nama, bagi tugas, anggota) tetap owner-only lewat
    # boleh_kelola — yang dilonggarkan hanya mengunggah.
    pemilik = projek.pemilik_dari(settings.uploads_root, d)
    tdata = await asyncio.to_thread(tugas.baca_projek, d, settings.uploads_root)
    boleh_unggah = tugas.boleh_unggah(tdata, sess.user)
    pr = await asyncio.to_thread(projek.konteks, d, settings.uploads_root, sess.user)
    berisi = pr["jumlah"] > 0
    return templates.TemplateResponse(request, "unggah.html", {
        "sess": sess,
        "local": is_local(request),
        "projek": pr,
        # Dipakai berkas sisipan _sisi.html, yang sama untuk semua halaman
        # projek. Namanya pendek karena ia disebut belasan kali di situ.
        "pr": pr,
        "aktif": "unggah",
        "berisi": berisi,
        "boleh_unggah": boleh_unggah,
        "pemilik": pemilik,
        # Projek video: "Ambil dari folder di server" mendaftarkan dataset KLIP
        # (train/val folder-per-kelas) sebagai versi aksi lewat /api/aksi/impor-
        # path (admin), BUKAN jalur impor gambar. Jadi templat perlu tahu jenis
        # + status admin untuk mengarahkan tombolnya.
        "jenis": projek.jenis_projek(d),
        "admin": is_admin(load_users(settings.users_file), sess.user),
        "tata": tambah.tata_letak(d) if berisi else "",
        "max_upload_mb": settings.max_upload_mb,
        "max_zip_mb": settings.max_zip_mb,
        "max_video_mb": settings.max_video_mb,
        "riwayat": riwayat.baca(settings, sess.user),
    })


@router.get("/versi", response_class=HTMLResponse)
async def halaman_versi(request: Request, ds: str = "",
                        sess: Session = Depends(current_session),
                        settings: Settings = Depends(get_settings)):
    """
    Versi dataset: pembagian train/valid/test yang dibekukan dan bisa diunduh
    ulang kapan saja.

    Menunya sudah ada di sidebar sejak sekarang, halamannya menyusul. Menu yang
    baru muncul belakangan membuat orang mengira fiturnya tidak ada; menu yang
    ada dan mengatakan "belum" tidak.
    """
    d = projek.temukan(settings.uploads_root, sess.user, ds)
    if d is None:
        return RedirectResponse("/pilih", status_code=303)
    # Projeknya dibuka di sesi ini, sama seperti yang dilakukan halaman grid.
    # Menu Ekspor tinggal di halaman ini sekarang, dan seluruh rute ekspor —
    # ringkasan, splitting, unduhan — bekerja pada `sess.src`. Tanpa baris ini,
    # membuka /versi langsung dari sidebar (tanpa mampir ke grid dulu) membuat
    # menunya menjawab "belum ada dataset terbuka" pada projek yang jelas-jelas
    # sedang dilihat.
    if str(sess.src or "") != str(d):
        await asyncio.to_thread(sess.load, d)

    from ..services import versi as svc_versi
    from ..services import tugas as svc_tugas

    pr = await asyncio.to_thread(projek.konteks, d, settings.uploads_root, sess.user)
    daftar = await asyncio.to_thread(svc_versi.daftar, d)
    tdata = svc_tugas.baca_projek(d, settings.uploads_root)
    return templates.TemplateResponse(request, "versi.html", {
        "sess": sess, "projek": pr, "pr": pr, "aktif": "versi",
        "daftar": daftar,
        "boleh_kelola": svc_tugas.boleh_kelola(tdata, sess.user),
        # Projek keypoint: tawarkan ekspor YOLO-pose di menu Ekspor.
        "jenis_pose": svc_tugas.skeleton_aktif(tdata),
        # Warna identitas kelas pilihan projek, supaya daftar kelas di wizard
        # Versi memakai warna yang sama dengan kanvas & grid.
        "kelas_warna": tdata.get("kelas_warna") or {},
    })


@router.get("/versi/hasil", response_class=HTMLResponse)
async def halaman_versi_hasil(
        request: Request, nomor: int = 0, ds: str = "",
        sp: list[str] = Query(default=[]), c: list[str] = Query(default=[]),
        x: list[str] = Query(default=[]), per: int = 120, hal: int = 1,
        sess: Session = Depends(current_session),
        settings: Settings = Depends(get_settings)):
    """Grid hasil SATU versi: gambar train/valid/test yang benar-benar ditulis,
    bisa disaring per kelas & split, dengan paginasi. Per-versi: `nomor` memilih
    versi mana yang ditampilkan (rincian v1 -> hasil v1 saja).

    Paginasi + filter dikerjakan di SERVER atas indeks ringan yang di-cache
    (buatversi.indeks_hasil), jadi versi berisi puluhan ribu gambar pun tak
    pernah menaruh semuanya di satu HTML dan tiap klik tak memindai ulang label.
    Thumbnail dilayani /versi/gambar (resize on-the-fly yang sudah ada)."""
    from math import ceil
    from urllib.parse import urlencode
    from ..services import buatversi

    # Dari link rincian versi, `ds` tak disertakan -> pakai projek yang sedang
    # terbuka di sesi (sama seperti halaman /versi yang mengantar ke sini).
    d = projek.temukan(settings.uploads_root, sess.user, ds) if ds \
        else (Path(sess.src) if sess.src else None)
    if d is None:
        return RedirectResponse("/pilih", status_code=303)
    if str(sess.src or "") != str(d):
        await asyncio.to_thread(sess.load, d)
    if not (buatversi.dir_versi(d, nomor) / "data.yaml").exists():
        return RedirectResponse("/versi", status_code=303)

    idx = await asyncio.to_thread(buatversi.indeks_hasil, d, nomor)
    pr = await asyncio.to_thread(projek.konteks, d, settings.uploads_root, sess.user)

    spset = {s for s in sp if s in buatversi.SPLIT}
    cc = set(c)
    neg_dipilih = "neg" in set(x)
    ada_filter = bool(cc or neg_dipilih)          # tanpa pilihan = tampilkan semua

    def lolos(it: dict) -> bool:
        if spset and it["split"] not in spset:
            return False
        if not ada_filter:
            return True
        if it["neg"]:
            return neg_dipilih
        return bool(cc & set(it["kelas"]))

    tampil = [it for it in idx["items"] if lolos(it)]
    per = max(20, min(int(per or 120), 1000))
    n = len(tampil)
    n_hal = max(1, ceil(n / per))
    hal = max(1, min(int(hal or 1), n_hal))
    mulai = (hal - 1) * per
    halaman = tampil[mulai:mulai + per]

    # Query dasar (semua filter KECUALI hal) untuk tautan paginasi.
    qbase = urlencode([("nomor", nomor), ("per", per)]
                      + [("sp", s) for s in sp]
                      + [("c", k) for k in c]
                      + [("x", k) for k in x], doseq=True)

    return templates.TemplateResponse(request, "versi_hasil.html", {
        "sess": sess, "projek": pr, "pr": pr, "aktif": "versi",
        "nomor": nomor, "idx": idx,
        "items": halaman, "n": n, "per": per, "hal": hal, "n_hal": n_hal,
        "mulai": mulai, "sp_set": spset, "c_set": cc, "neg_dipilih": neg_dipilih,
        "qbase": qbase,
    })


async def _mulai_kurasi(d: Path, settings: Settings) -> None:
    """
    Nyatakan projek tujuan tunduk pada aturan dataset, sebelum gambar mendarat.

    Dipanggil dari SETIAP jalur yang menambah gambar. Projek yang belum pernah
    dikurasi menghitung seluruh isinya sebagai dataset, jadi tanpa ini gambar
    yang baru diunggah — belum dilabeli, belum ditugaskan, belum dimasukkan
    siapa pun — langsung muncul di halaman Dataset dan langsung ikut
    terekspor.

    Idempoten dan murah pada panggilan kedua dan seterusnya; satu unggahan
    folder mengirim ratusan permintaan terpisah.

    Foldernya dibuat kalau belum ada: penandanya harus tertulis SEBELUM gambar
    pertama mendarat, dan pada projek yang benar-benar baru berarti sebelum
    apa pun ada di sana. Yang dibuat cuma folder yang berkas berikutnya akan
    membuat juga sedetik kemudian; nama projeknya sudah disaring upload_dir.
    """
    from ..services import tugas as svc_tugas

    d.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(svc_tugas.mulai_kurasi, d,
                            projek.pemilik_dari(settings.uploads_root, d))
    # Jumlah gambar projek berubah -> buang cache survei supaya kartu/sidebar
    # langsung segar, tak menunggu TTL. Dipanggil di SETIAP jalur penambah
    # gambar (lihat pemanggil _mulai_kurasi).
    projek.lupakan_survei(d)


@router.put("/upload")
async def upload(request: Request, ds: str = "", name: str = "",
                 sess: Session = Depends(current_session_api),
                 settings: Settings = Depends(get_settings)):
    return await _lakukan_unggah(request, sess, settings, ds, name)


async def _lakukan_unggah(request: Request, sess: Session, settings: Settings,
                          ds: str, name: str) -> dict:
    """Terima SATU berkas unggahan ke projek `ds` atas nama `sess`.

    Dipakai bersama dua jalur yang beda cara masuknya tapi identik nasibnya:
    unggah dari laptop (/upload, sesi cookie) dan unggah dari HP (/unggah-hp/
    upload, token). Keduanya lewat satu badan ini supaya pemeriksaan izin,
    batas ukuran, dan penulisan .part->ganti-nama tidak pernah menyimpang.
    """
    # `name` boleh memuat subfolder (unggahan folder mengirim
    # webkitRelativePath), dan strukturnya dipertahankan.
    #
    # Arsip diizinkan di SINI saja, bukan di dalam isi arsip: `arsip.bongkar`
    # memanggil safe_relpath tanpa izin itu, sehingga zip di dalam zip
    # dilewati dan pembongkarannya tidak pernah berlapis.
    #
    # Video diterima dua projek, dengan nasib berbeda:
    #   projek IMAGE : diunggah utuh ke akar, lalu diekstrak jadi frame lewat
    #                  /ekstrak, dan videonya dibuang (perilaku lama, utuh).
    #   projek VIDEO : mendarat utuh di _sumber/, TIDAK diekstrak & TIDAK
    #                  dibuang — videonya sendiri asetnya, dipotong jadi klip
    #                  belakangan lewat /api/video/potong (services/klip.py).
    # Arsip (.zip) tetap hanya untuk projek image: projek video tak punya alur
    # membongkar arsip, jadi di sana hanya ekstensi video yang diterima.
    d, err = _folder_unggah(sess, settings, ds)
    if err:
        return {"ok": False, "error": err}
    projek_video = projek.jenis_projek(d) == "video"
    fn = safe_relpath(name, arsip=not projek_video, video=True)
    if not fn:
        return {"ok": False, "error": "nama atau jenis berkas tidak didukung"}
    sfx = Path(fn).suffix.lower()
    if projek_video and sfx not in VIDEO_EXT:
        return {"ok": False, "error": "projek video hanya menerima berkas video"}

    batas, sebutan = settings.batas_untuk(fn)
    try:
        total = int(request.headers.get("content-length", 0))
    except ValueError:
        total = 0
    if total <= 0:
        return {"ok": False, "error": "berkas kosong"}
    if total > batas:
        return {"ok": False, "error": f"lebih dari {sebutan}"}

    await _mulai_kurasi(d, settings)
    # Projek video menaruh videonya di folder internal _sumber/ (dilewati setiap
    # penghitung gambar, lihat config.FOLDER_INTERNAL), bukan di akar — supaya
    # ia tak terbaca sebagai "gambar" dan tak ikut terkurasi sebagai dataset.
    dest = (d / SUMBER_VIDEO / fn) if projek_video else (d / fn)
    # Penjagaan berlapis: walau safe_relpath sudah membuang `..`, tujuan akhirnya
    # tetap diperiksa masih berada di dalam folder milik akun ini.
    try:
        dest.resolve().relative_to(d.resolve().parent.resolve() / d.name)
    except ValueError:
        return {"ok": False, "error": "tujuan di luar folder unggahan"}
    # Menimpa gambar yang sudah ada berarti berkas anotasi lamanya tetap di
    # tempatnya sambil kini menggambarkan foto yang berbeda — dan gambar itu
    # tetap terhitung "sudah masuk dataset". Jalur penambahan lain (tambah,
    # impor, gabung) tidak pernah menimpa; yang ini dulu satu-satunya yang
    # melakukannya, tanpa satu pun peringatan.
    if dest.exists() and dest.suffix.lower() not in ARSIP_EXT:
        return {"ok": False, "name": fn, "bytes": 0, "arsip": False,
                "sudah_ada": True,
                "error": f"'{fn}' sudah ada di projek ini. Ganti namanya "
                         f"dulu, atau hapus yang lama"}
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

    hasil = {"ok": True, "name": fn, "bytes": written,
             "arsip": sfx in ARSIP_EXT, "video": sfx in VIDEO_EXT}
    # Projek video: beri tahu JS videonya mendarat di _sumber/ dan JANGAN
    # memanggil /ekstrak (itu jalur projek image). Pemotongan jadi klip dipicu
    # terpisah lewat /api/video/potong.
    if projek_video and sfx in VIDEO_EXT:
        hasil["sumber"] = f"{SUMBER_VIDEO}/{fn}"
    return hasil


# ----------------------------------------------------------- unggah dari HP
#
# Kartu "Unggah dari HP" (meniru Roboflow): desktop meminta token, menampilkan
# QR; ponsel memindainya, membuka halaman unggah ringkas tanpa login, dan
# gambarnya mendarat di projek yang sama lewat jalur yang sama persis (hanya
# cara autentikasinya yang beda: token, bukan cookie sesi). Lihat
# services/unggah_token.py untuk umur & cakupan token.


def _basis_publik(request: Request, settings: Settings) -> str:
    """Alamat yang bisa dijangkau dari LUAR untuk disematkan ke QR.

    Di balik reverse proxy, request.base_url cuma http://127.0.0.1:PORT yang
    ponsel tak bisa buka; maka LABELAPP_BASIS_PUBLIK dipakai lebih dulu. Tanpa
    itu (dev di localhost, tanpa proxy) base_url memang benar.
    """
    return (settings.basis_publik or str(request.base_url)).rstrip("/")


@router.post("/api/unggah/token")
async def buat_token_hp(request: Request, ds: str = "",
                        sess: Session = Depends(current_session_api),
                        settings: Settings = Depends(get_settings)):
    """Terbitkan tautan+QR unggah HP untuk projek yang sedang dibuka.

    Hak yang sama dengan unggah biasa: hanya yang boleh_unggah ke projek ini
    yang bisa membuat tautannya, dan tautannya menitipkan HAK ITU SAJA ke
    ponsel. Projek video tak ditawari — ponsel mengunggah gambar, dan jalur
    video sumber punya alurnya sendiri."""
    d, err = _folder_unggah(sess, settings, ds)
    if err:
        return {"ok": False, "error": err}
    if projek.jenis_projek(d) == "video":
        return {"ok": False,
                "error": "unggah dari HP hanya untuk projek gambar"}
    # HP selalu mendaratkan berkas di AKAR projek (tak lewat /tambah yang menjaga
    # tata letak). Jadi dataset ber-split (train/valid/test) atau YOLO
    # (images/labels) tak boleh menerima foto HP -- itu akan mencampur foto baru
    # di akar dan merusak strukturnya. Hanya projek datar/kosong yang ditawari,
    # sama batasnya dengan unggah video desktop (empty-or-flat).
    if tambah.tata_letak(d) != tambah.TATA_DATAR:
        return {"ok": False,
                "error": "projek ini berstruktur dataset (split/YOLO); tambah "
                         "gambar lewat laptop supaya tata letaknya terjaga. "
                         "Unggah HP hanya untuk projek gambar datar."}
    token, exp = unggah_token.buat(sess.user, ds)
    url = f"{_basis_publik(request, settings)}/unggah-hp?t={token}"
    return {"ok": True, "url": url, "kedaluwarsa": exp,
            "aman": url.startswith("https://")}


@router.get("/unggah-hp", response_class=HTMLResponse)
async def halaman_unggah_hp(request: Request, t: str = "",
                            settings: Settings = Depends(get_settings)):
    """Halaman unggah ringkas untuk ponsel. TANPA sesi: diautentikasi token
    di query. Token mati -> halaman yang mengatakannya, bukan form yang
    tombolnya menolak diam-diam."""
    info = unggah_token.ambil(t)
    ktx = {"sah": bool(info), "token": t,
           "max_upload_mb": settings.max_upload_mb,
           "max_video_mb": settings.max_video_mb}
    if info:
        # Nama projek untuk ditampilkan: bentuk "owner/nama" disederhanakan ke
        # nama saja (ponsel tak perlu tahu kepemilikan, cuma tujuan).
        ktx["projek"] = (info["ds"].split("/", 1)[-1] or info["ds"])
    return templates.TemplateResponse(request, "unggah_hp.html", ktx)


# ------------------------------------------------------ status ekstraksi video
# Ekstraksi video jalan DI LATAR (thread lepas dari permintaan), jadi aman walau
# tab browser ditutup di tengah jalan -- tetapi user yang membuka lagi harus bisa
# tahu "masih jalan / selesai / gagal". Status ditulis ke berkas .ekstraksi.json
# di dalam projek (diawali titik -> dilewati scanner, tak terhitung sebagai data)
# dan dibaca lewat /api/ekstrak/status. Rekaman lama dipangkas supaya tak tumbuh.
_BERKAS_STATUS = ".ekstraksi.json"
_status_lock = threading.Lock()
# Video yang ekstraksinya BENAR-BENAR jalan di PROSES INI (kunci "<dir>|<video>").
# Dipakai mendeteksi rekaman "jalan" yang yatim: kalau statusnya "jalan" tapi
# tak ada di sini (thread mati / server dimulai ulang), ia terputus -- bukan
# sedang jalan -- jadi tak boleh berputar selamanya.
_ekstrak_hidup: set[str] = set()


def _kunci_hidup(d: Path, video: str) -> str:
    return f"{Path(d).resolve()}|{video}"


def _status_baca(d: Path) -> dict:
    try:
        data = json.loads((d / _BERKAS_STATUS).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _status_tulis(d: Path, video: str, **nilai) -> None:
    with _status_lock:
        data = _status_baca(d)
        rec = data.get(video) or {}
        rec.update(nilai)
        data[video] = rec
        # Pangkas: simpan paling banyak 30 rekaman terakhir (yang jalan + recent).
        if len(data) > 30:
            urut = sorted(data.items(),
                          key=lambda kv: (kv[1].get("keadaan") == "jalan",
                                          kv[1].get("mulai") or ""))
            data = dict(urut[-30:])
        try:
            p = d / _BERKAS_STATUS
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=1))
            tmp.replace(p)
        except OSError:
            pass


def _status_hapus(d: Path, video: str | None = None) -> None:
    """Buang rekaman status yang SUDAH kelar (selesai/gagal) supaya spanduknya
    tidak menetap selamanya. `video` tertentu -> buang satu itu; None -> buang
    semua yang bukan 'jalan'. Rekaman yang masih 'jalan' TAK pernah dibuang."""
    with _status_lock:
        data = _status_baca(d)
        if video:
            rec = data.get(video)
            if not rec or rec.get("keadaan") == "jalan":
                return
            data.pop(video, None)
        else:
            data = {k: v for k, v in data.items()
                    if (v or {}).get("keadaan") == "jalan"}
        try:
            p = d / _BERKAS_STATUS
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=1))
            tmp.replace(p)
        except OSError:
            pass


def _jalankan_ekstrak_latar(vp: Path, d: Path, settings: Settings, *,
                            fps: float | None = None,
                            kerapatan: float | None = None,
                            preset: str = "sedang") -> None:
    """Ekstrak satu video jadi frame DI LATAR, catat statusnya, buang videonya.

    Dijalankan di thread terpisah dari permintaan supaya: (1) unggahan/ekstraksi
    besar tak menembus timeout nginx, dan (2) menutup tab TIDAK membatalkan
    ekstraksi maupun penghapusan video -- keduanya terjadi di sini, bukan di kode
    permintaan yang bisa dibatalkan saat klien putus. Statusnya tercatat agar
    user yang membuka lagi tahu masih jalan / selesai / gagal."""
    from ..services import tugas as svc_tugas
    from ..session import tandai_berubah
    video = vp.name
    # `pid` dicatat supaya pemantau status tahu rekaman "jalan" ini milik proses
    # yang MASIH hidup; sesudah server dimulai ulang pid-nya beda -> terdeteksi
    # terputus. Dan daftarkan ke _ekstrak_hidup untuk deteksi thread mati.
    _ekstrak_hidup.add(_kunci_hidup(d, video))
    _status_tulis(d, video, keadaan="jalan", galat="", pid=os.getpid(),
                  mulai=datetime.now().strftime("%Y-%m-%d %H:%M"))
    try:
        r = ekstraksi.ekstrak(vp, vp.parent, ekstraksi.sah_preset(preset),
                              vp.stem, kerapatan, fps)
        vp.unlink(missing_ok=True)       # video dibuang setelah frame selamat
        _status_tulis(d, video, keadaan="selesai", n=r.get("ditulis", 0),
                      buram=r.get("buram", 0),
                      selesai=datetime.now().strftime("%Y-%m-%d %H:%M"))
        log.info("ekstrak %s -> %d frame", video, r.get("ditulis", 0))
    except ekstraksi.EkstrakTolak as e:
        # Video tak bisa dibaca / tak menghasilkan frame: retry sia-sia, buang.
        vp.unlink(missing_ok=True)
        _status_tulis(d, video, keadaan="gagal", galat=str(e)[:140])
        log.warning("ekstrak ditolak %s: %s", video, e)
    except Exception as e:               # noqa: BLE001 -- cv2 macam-macam
        # Galat lain (mungkin transient): video DIPERTAHANKAN supaya bisa diulang.
        _status_tulis(d, video, keadaan="gagal", galat=f"gagal mengekstrak: {str(e)[:90]}")
        log.warning("ekstrak gagal %s: %s", video, e)
    finally:
        _ekstrak_hidup.discard(_kunci_hidup(d, video))
        # Frame baru -> kurasi ulang + beri tahu SEMUA sesi bahwa daftar gambar
        # berubah. tandai_berubah menaikkan penanda (segarkan() di papan/grid
        # jadi memaksa pindai ulang -> frame baru LANGSUNG muncul tanpa perlu
        # restart) DAN membuang cache survei di dalamnya. Tanpa ini, ekstraksi
        # menulis 4.453 frame ke disk tapi papan tetap kosong sampai server
        # dimuat ulang.
        try:
            svc_tugas.mulai_kurasi(d, projek.pemilik_dari(settings.uploads_root, d))
            tandai_berubah(d, None)
        except Exception:                # noqa: BLE001
            pass


@router.put("/unggah-hp/upload")
async def upload_hp(request: Request, t: str = "", name: str = "",
                    settings: Settings = Depends(get_settings)):
    """Terima berkas dari ponsel. Token -> (akun, projek); sisanya identik
    dengan unggah biasa, termasuk cek ulang boleh_unggah di _folder_unggah
    (hak yang dicabut menutup unggahan HP juga).

    Video: HP tak bisa memanggil /ekstrak (itu jalur sesi cookie), jadi kalau
    yang diunggah video ke projek image, server mengekstraknya DI LATAR di sini.
    """
    info = unggah_token.ambil(t)
    if not info:
        return {"ok": False,
                "error": "tautan kedaluwarsa, minta QR baru dari laptop"}
    sess = Session(info["user"], settings)
    hasil = await _lakukan_unggah(request, sess, settings, info["ds"], name)

    if hasil.get("ok") and hasil.get("video") and not hasil.get("sumber"):
        # video=True & tanpa 'sumber' = video mendarat di akar projek IMAGE.
        d, err = _folder_unggah(sess, settings, info["ds"])
        vp = (d / hasil["name"]) if not err else None
        if vp and vp.is_file():
            # HP tak punya slider -> fps bawaan.
            threading.Thread(
                target=_jalankan_ekstrak_latar, args=(vp, d, settings),
                kwargs={"fps": ekstraksi.FPS_BAWAAN}, daemon=True).start()
            hasil["ekstrak_latar"] = True   # beri tahu HP: diproses di latar
    return hasil


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

    d, err = _folder_unggah(sess, settings, ds)
    if err:
        return {"ok": False, "error": err}
    await _mulai_kurasi(d, settings)
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
    # Ekspor YOLO-pose (data.yaml ber-kpt_shape): auto-buat template keypoint,
    # sekali, hanya kalau projek belum punya — supaya unggahan court langsung
    # bisa diedit sebagai keypoint. Idempoten & tak menimpa template manual.
    _pemilik = projek.pemilik_dari(settings.uploads_root, d)
    await asyncio.to_thread(tugas.buat_skeleton_dari_pose, d, _pemilik)
    # /unzip hanya MEMBONGKAR (projek kosong): split diratakan belakangan di
    # /useupload, dan unggahan TAK lagi auto-masuk dataset -- ia mendarat sebagai
    # batch di papan Anotasi untuk ditinjau dulu. Projek DATAR yang sudah berisi
    # memakai jalur sendiri (/gabung-zip), yang meratakan + mengonversi label ke
    # labelme + menyatukan di akar.
    return {"ok": True, "n": hasil["ditulis"], "dilewati": hasil["dilewati"],
            "bytes": hasil["bytes"], "contoh_dilewati": hasil["contoh_dilewati"],
            "arsip_dihapus": True}


@router.post("/gabung-zip")
async def gabung_zip(ds: str = "", name: str = "",
                     sess: Session = Depends(current_session_api),
                     settings: Settings = Depends(get_settings)):
    """
    Gabungkan .zip dataset (ekspor Roboflow bersplit) ke projek DATAR yang SUDAH
    berisi: ratakan split + konversi label YOLO .txt jadi labelme .json + taruh
    di akar, menyatu dengan gambar yang sudah ada.

    Kembaran /unzip untuk projek BERISI. /unzip dipakai projek kosong (isinya
    JADI projeknya, tata YOLO). Di sini gambar baru mendarat sebagai BATCH di
    papan Anotasi (nama batch diberi lewat /api/tag/pasang sesudah ini), BUKAN
    langsung masuk dataset -- supaya ditinjau/dibagi dulu.
    """
    fn = safe_relpath(name, arsip=True)
    if not fn or Path(fn).suffix.lower() not in ARSIP_EXT:
        return {"ok": False, "error": "yang diminta bukan berkas arsip"}
    d, err = _folder_unggah(sess, settings, ds)
    if err:
        return {"ok": False, "error": err}
    # Hanya projek gambar DATAR (labelme) yang menerima gabung .zip: struktur
    # split/yolo punya tempat berkasnya sendiri, dan projek video tak terima
    # gambar. Projek kosong dibiarkan ke /unzip (jadi tata YOLO apa adanya).
    if projek.jenis_projek(d) == "video":
        return {"ok": False, "error": "projek video tidak menerima arsip gambar"}
    if tambah.tata_letak(d) != tambah.TATA_DATAR:
        return {"ok": False,
                "error": "hanya projek datar (labelme) yang bisa menerima "
                         ".zip di dalam projek berisi"}
    await _mulai_kurasi(d, settings)
    zp = d / fn
    if not zp.is_file():
        return {"ok": False, "error": "arsipnya tidak ada di folder unggahan"}

    try:
        hasil = await asyncio.to_thread(
            tambah.gabung_dataset_ke_projek, zp, d,
            maks_byte=settings.max_zip_bytes * settings.zip_ratio_max,
            maks_entri=settings.zip_entries_max)
    except arsip.ArsipTolak as e:
        return {"ok": False, "error": str(e)[:140]}
    except OSError as e:
        return {"ok": False, "error": f"gagal menggabung: {str(e)[:80]}"}

    if not hasil["digabung"]:
        return {"ok": False,
                "error": "tidak ada gambar yang bisa dipakai di dalam arsip itu"}
    # data.yaml pose -> auto-template (idempoten), lalu muat ulang projeknya.
    _pemilik = projek.pemilik_dari(settings.uploads_root, d)
    await asyncio.to_thread(tugas.buat_skeleton_dari_pose, d, _pemilik)
    n = len(await asyncio.to_thread(sess.load, d))
    peringatan = await asyncio.to_thread(scanner.periksa_kelengkapan, d)
    return {"ok": True, "n": n, "digabung": hasil["digabung"],
            "boxes": hasil["boxes"], "kelas": hasil["kelas"],
            "bentrok": hasil["bentrok"], "peringatan": peringatan,
            "masuk_dataset": 0}


@router.post("/ekstrak")
async def ekstrak_video(ds: str = "", name: str = "", preset: str = "sedang",
                        kerapatan: float = 0.0, fps: float = 0.0,
                        sess: Session = Depends(current_session_api),
                        settings: Settings = Depends(get_settings)):
    """
    Ekstrak video yang sudah terunggah menjadi frame gambar, di tempat.

    Kembaran /unzip untuk video: /upload menaruh videonya utuh, lalu rute ini
    memotongnya jadi gambar dan membuang videonya setelah frame-nya selamat.
    Hanya untuk projek IMAGE — projek video menyimpan videonya apa adanya, dan
    jalur itu belum ada.

    Kerapatan ekstraksi (yang pertama terisi menang): `fps` (frame per detik,
    gaya Roboflow, >0 menang) -> `kerapatan` (faktor slider lama) -> `preset`.
    Buang buram/kembar berjalan apa pun pilihannya. Lihat ekstraksi.
    """
    fn = safe_relpath(name, video=True)
    if not fn or Path(fn).suffix.lower() not in VIDEO_EXT:
        return {"ok": False, "error": "yang diminta bukan berkas video"}

    d, err = _folder_unggah(sess, settings, ds)
    if err:
        return {"ok": False, "error": err}
    if projek.jenis_projek(d) != "image":
        return {"ok": False, "error": "ekstraksi video hanya untuk projek image"}
    await _mulai_kurasi(d, settings)
    vp = d / fn
    if not vp.is_file():
        return {"ok": False, "error": "videonya tidak ada di folder unggahan"}

    # Ekstraksi DI LATAR: video panjang (HEVC 60fps bisa 10+ menit) tak boleh
    # menahan permintaan sampai menembus timeout, dan menutup tab tak boleh
    # membatalkannya. Thread lepas ini mencatat status (jalan/selesai/gagal) +
    # membuang videonya sendiri; UI memantaunya lewat /api/ekstrak/status.
    threading.Thread(
        target=_jalankan_ekstrak_latar, args=(vp, d, settings),
        kwargs={"fps": (fps if fps and fps > 0 else None),
                "kerapatan": (kerapatan if kerapatan and kerapatan > 0 else None),
                "preset": preset}, daemon=True).start()
    return {"ok": True, "latar": True, "name": fn}


@router.get("/api/ekstrak/status")
async def ekstrak_status(ds: str = "",
                         sess: Session = Depends(current_session_api),
                         settings: Settings = Depends(get_settings)):
    """Status ekstraksi video di LATAR untuk projek `ds`: tiap video + keadaan
    (jalan/selesai/gagal), jumlah frame, dan galat. Dipakai UI supaya user yang
    membuka lagi tahu prosesnya masih jalan atau sudah selesai/gagal."""
    d, err = _folder_unggah(sess, settings, ds)
    if err:
        return {"ok": False, "error": err}
    data = _status_baca(d)
    for nama, rec in data.items():
        if rec.get("keadaan") != "jalan":
            continue
        prefix = safe_slug(Path(nama).stem) or "frame"
        # Rekaman "jalan" yang YATIM: prosesnya sudah beda (server dimulai ulang)
        # atau thread-nya tak ada lagi di proses ini (mati). Itu TERPUTUS, bukan
        # sedang jalan -- kalau dibiarkan, spanduknya berputar selamanya. Tandai
        # gagal (bisa ditutup/diulang) dan bersihkan folder sementaranya.
        pid = rec.get("pid")
        yatim = (pid != os.getpid()) or \
                (_kunci_hidup(d, nama) not in _ekstrak_hidup)
        if yatim:
            _status_tulis(d, nama, keadaan="gagal",
                          galat="terputus (proses berhenti / server dimulai "
                                "ulang). Unggah dan ekstrak lagi.")
            rec["keadaan"] = "gagal"
            rec["galat"] = "terputus (proses berhenti / server dimulai ulang). " \
                           "Unggah dan ekstrak lagi."
            try:
                shutil.rmtree(ekstraksi.tmp_dir(d, Path(nama).stem),
                              ignore_errors=True)
            except Exception:            # noqa: BLE001
                pass
            continue
        # Masih benar-benar jalan: progres = frame yang sudah final DI folder
        # projek + frame yang sedang ditulis ffmpeg di folder sementara. Tanpa
        # yang kedua, angkanya diam di 0 sepanjang ffmpeg (ia memindah frame ke
        # projek hanya di AKHIR), jadi user mengira prosesnya macet.
        try:
            n_final = sum(1 for _ in d.glob(f"{prefix}-*.jpg"))
            tmp = ekstraksi.tmp_dir(d, Path(nama).stem)
            n_tmp = sum(1 for _ in tmp.glob("*.jpg")) if tmp.is_dir() else 0
            rec["sudah"] = n_final + n_tmp
        except OSError:
            pass
    return {"ok": True, "status": data}


@router.post("/api/ekstrak/tutup")
async def ekstrak_tutup(ds: str = "", video: str = "",
                        sess: Session = Depends(current_session_api),
                        settings: Settings = Depends(get_settings)):
    """Sembunyikan spanduk status ekstraksi yang SUDAH kelar. `video` kosong =
    bersihkan semua yang selesai/gagal. Yang masih 'jalan' tak tersentuh."""
    d, err = _folder_unggah(sess, settings, ds)
    if err:
        return {"ok": False, "error": err}
    await asyncio.to_thread(_status_hapus, d, video or None)
    return {"ok": True}


@router.get("/api/impor/survei")
async def impor_survei(path: str = "",
                       sess: Session = Depends(current_session_api),
                       settings: Settings = Depends(get_settings)):
    """Berapa banyak dan berapa besar yang akan disalin — sebelum menyalin."""
    d = Path((path or "").strip()).expanduser()
    if not d.is_dir():
        return {"ok": False, "error": "folder tidak ada di server"}
    # Menghitung isi sebuah folder sudah membocorkan isinya. Aturannya sama
    # dengan membuka: yang tidak boleh dibuka tidak boleh dihitung.
    tolak = await asyncio.to_thread(
        projek.boleh_buka, d, sess.user,
        settings.uploads_root, settings.datasets_root, settings.impor_roots)
    if tolak:
        return {"ok": False, "error": tolak}
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
    # Membaca isi folder di server tunduk pada aturan yang sama dengan
    # membukanya. Tanpa ini, rute ini jadi jalan pintas mengelilingi seluruh
    # penjagaan /setsrc: satu akun dev pernah menghitung isi akar dataset
    # PRODUKSI dari sini, dan rute impor di sebelahnya bisa menyalinnya.
    tolak = await asyncio.to_thread(
        projek.boleh_buka, sumber, sess.user,
        settings.uploads_root, settings.datasets_root, settings.impor_roots)
    if tolak:
        return {"ok": False, "error": tolak}
    # Diserahkan apa adanya ke upload_dir, yang memegang satu-satunya aturan
    # nama projek. Membersihkannya di sini lebih dulu dengan aturan yang
    # berbeda membuat unggahan mendarat di folder yang bukan projeknya.
    tujuan = sess.upload_dir(ds or sumber.name)
    await _mulai_kurasi(tujuan, settings)
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

    # Persebaran train/valid/test ekspor Roboflow dicopot di sini: HIGOLAB
    # membelah sendiri per versi (anti-bocor), jadi split bawaan harus lenyap
    # dulu supaya tidak dipertahankan dan membatalkan pembelahan itu. Datar
    # begini, semua gambar berlabel langsung terbaca "sudah dianotasi".
    await asyncio.to_thread(tambah.ratakan_split, tujuan)
    # data.yaml bertahan di akar setelah ratakan_split, jadi pose masih terbaca.
    _pemilik = projek.pemilik_dari(settings.uploads_root, tujuan)
    await asyncio.to_thread(tugas.buat_skeleton_dari_pose, tujuan, _pemilik)
    # FILTER otomatis: isi berlabel langsung masuk dataset (dataset jadi lengkap),
    # isi mentah menunggu dilabeli.
    await asyncio.to_thread(tugas.masuk_dataset_berlabel, tujuan, _pemilik)

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
    # Pemilik atau Editor boleh menambah ke projek yang sedang dibuka, walau
    # foldernya milik owner lain (projek sharing). boleh_unggah yang menjamin
    # haknya. Dataset yang dibuka langsung dari folder server tidak punya
    # pemilik yang cocok -> boleh_unggah False -> jatuh ke aturan ruang kerja,
    # yang memang menolaknya (folder sumber hanya dibaca).
    tdata = tugas.baca_projek(sess.src, settings.uploads_root)
    if tugas.boleh_unggah(tdata, sess.user):
        return ""
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
    await _mulai_kurasi(sess.src, settings)

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
    # Membaca folder di server tunduk pada aturan yang sama dengan membukanya.
    # Rute kembarannya /impor dan /api/impor/survei sudah memeriksanya; yang
    # ini terlewat, dan itu membuatnya jadi satu-satunya jalan menyalin projek
    # PRIBADI akun lain ke ruang kerja sendiri — lalu mengekspornya sebagai
    # milik sendiri. /setsrc menolak folder yang sama, /impor menolak, yang ini
    # menjawab "ok".
    tolak = await asyncio.to_thread(
        projek.boleh_buka, sumber, sess.user,
        settings.uploads_root, settings.datasets_root, settings.impor_roots)
    if tolak:
        return {"ok": False, "error": tolak}
    await _mulai_kurasi(sess.src, settings)
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

    # Dataset tujuan bisa jadi ekspor pose yang digabung; auto-buat template
    # kalau belum ada (guard di dalam menolak kalau sudah / bukan pose).
    _pemilik = projek.pemilik_dari(settings.uploads_root, tujuan)
    await asyncio.to_thread(tugas.buat_skeleton_dari_pose, tujuan, _pemilik)
    # FILTER otomatis: isi berlabel yang baru ditambahkan langsung masuk dataset.
    await asyncio.to_thread(tugas.masuk_dataset_berlabel, tujuan, _pemilik)
    n = len(await asyncio.to_thread(sess.load, tujuan))
    peringatan = await asyncio.to_thread(scanner.periksa_kelengkapan, tujuan)
    impor.catat_maju(sess.user, tahap="selesai")
    return {"ok": True, "n": n, "ditambah": hasil["berkas"],
            "sudah_ada": hasil["sudah_ada"], "dilewati": hasil["dilewati"],
            "bytes": hasil["bytes"], "peringatan": peringatan,
            "contoh_dilewati": hasil["contoh_dilewati"],
            "bentrok": hasil["bentrok"]}


@router.post("/useupload")
async def use_upload(ds: str = "", sess: Session = Depends(current_session_api),
                     settings: Settings = Depends(get_settings)):
    """Buka folder hasil unggahan sebagai dataset yang sedang diperiksa."""
    # Resolve lewat temukan supaya projek sharing (owner/nama) ikut terbuka,
    # bukan dibelokkan ke ruang sendiri oleh upload_dir.
    d = projek.temukan(settings.uploads_root, sess.user, ds)
    if d is None or not d.is_dir():
        return {"ok": False, "error": "folder unggahan belum ada"}
    # Sama seperti /impor: kalau yang diunggah ternyata ekspor bersplit
    # (train/valid/test dari laptop atau di dalam zip), split-nya dicopot
    # supaya mesin versi yang membelahnya, bukan Roboflow.
    await asyncio.to_thread(tambah.ratakan_split, d)
    # Unggahan folder ekspor pose (bukan zip, jadi tak lewat /unzip): auto-buat
    # template di sini juga. Idempoten — kalau /unzip sudah membuatnya, dilewati.
    _pemilik = projek.pemilik_dari(settings.uploads_root, d)
    await asyncio.to_thread(tugas.buat_skeleton_dari_pose, d, _pemilik)
    # Unggahan (termasuk "dataset jadi" dari Roboflow) TIDAK lagi langsung masuk
    # dataset: ia mendarat sebagai BATCH di papan Anotasi supaya ditinjau/dibagi
    # dulu, lalu dimasukkan ke dataset lewat tombol "Masukkan" saat siap. Dulu
    # gambar berlabel auto-masuk dataset -> melewati langkah tinjau itu.
    n = len(await asyncio.to_thread(sess.load, d))
    if not n:
        return {"ok": False, "error": "tidak ada gambar terbaca di unggahan itu"}
    # Peringatan dikirim bersama hasil, bukan sebagai kegagalan: datasetnya
    # tetap bisa dibuka, hanya ada yang perlu diketahui lebih dulu.
    peringatan = await asyncio.to_thread(scanner.periksa_kelengkapan, d)
    return {"ok": True, "dir": str(d), "n": n, "nama": d.name,
            "peringatan": peringatan, "masuk_dataset": 0}
