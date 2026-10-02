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

from ..config import ARSIP_EXT, SUMBER_VIDEO, VIDEO_EXT, Settings, get_settings
from ..deps import current_session, current_session_api, is_local
from ..security import safe_relpath, safe_slug
from ..services import (arsip, ekstraksi, impor, projek, riwayat, scanner,
                        tambah, tugas)
from ..session import Session
from ..templating import templates

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
            return None, "kamu tidak berhak mengunggah ke projek ini"
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
        "tata": tambah.tata_letak(d) if berisi else "",
        "max_upload_mb": settings.max_upload_mb,
        "max_zip_mb": settings.max_zip_mb,
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
                "error": f"'{fn}' sudah ada di projek ini — ganti namanya "
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
    # Filter otomatis "dataset jadi -> langsung masuk dataset" SENGAJA tidak di
    # sini: /unzip belum meratakan split, jadi kunci gambar masih bentuk
    # `train/images/x.jpg`. /useupload yang menyusul MERATAKANNYA jadi
    # `images/x.jpg` — kunci yang disimpan di sini akan jadi basi. Maka
    # masuk_dataset_berlabel dijalankan di /useupload (sesudah ratakan_split),
    # di atas kunci yang final. Projek berisi pun menolak .zip, jadi tiap
    # unggahan .zip pasti lewat /useupload.
    return {"ok": True, "n": hasil["ditulis"], "dilewati": hasil["dilewati"],
            "bytes": hasil["bytes"], "contoh_dilewati": hasil["contoh_dilewati"],
            "arsip_dihapus": True}


@router.post("/ekstrak")
async def ekstrak_video(ds: str = "", name: str = "", preset: str = "sedang",
                        sess: Session = Depends(current_session_api),
                        settings: Settings = Depends(get_settings)):
    """
    Ekstrak video yang sudah terunggah menjadi frame gambar, di tempat.

    Kembaran /unzip untuk video: /upload menaruh videonya utuh, lalu rute ini
    memotongnya jadi gambar dan membuang videonya setelah frame-nya selamat.
    Hanya untuk projek IMAGE — projek video menyimpan videonya apa adanya, dan
    jalur itu belum ada.

    `preset` mengatur kerapatan: "jarang"/"sedang"/"rapat" (lihat ekstraksi).
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

    try:
        hasil = await asyncio.to_thread(
            ekstraksi.ekstrak, vp, vp.parent, ekstraksi.sah_preset(preset),
            vp.stem)
    except ekstraksi.EkstrakTolak as e:
        return {"ok": False, "error": str(e)[:140]}
    except Exception as e:                       # cv2 bisa melempar macam-macam
        return {"ok": False, "error": f"gagal mengekstrak: {str(e)[:90]}"}

    # Videonya dibuang setelah frame-nya selamat — menyimpan salinan mentahnya
    # cuma menghabiskan disk dan tak pernah terbaca sebagai bagian dataset.
    vp.unlink(missing_ok=True)
    return {"ok": True, "n": hasil["ditulis"], "buram": hasil["buram"],
            "duplikat": hasil["duplikat"], "durasi": hasil["durasi"],
            "target": hasil["target"], "preset": hasil["preset"],
            "video_dihapus": True}


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
        settings.uploads_root, settings.datasets_root)
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
        settings.uploads_root, settings.datasets_root)
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
        settings.uploads_root, settings.datasets_root)
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
    # FILTER otomatis (sama seperti /unzip): isi berlabel langsung masuk dataset,
    # isi mentah menunggu dilabeli. Jadi "Pilih folder" dataset jadi pun terpilah.
    n_masuk = await asyncio.to_thread(tugas.masuk_dataset_berlabel, d, _pemilik)
    n = len(await asyncio.to_thread(sess.load, d))
    if not n:
        return {"ok": False, "error": "tidak ada gambar terbaca di unggahan itu"}
    # Peringatan dikirim bersama hasil, bukan sebagai kegagalan: datasetnya
    # tetap bisa dibuka, hanya ada yang perlu diketahui lebih dulu.
    peringatan = await asyncio.to_thread(scanner.periksa_kelengkapan, d)
    return {"ok": True, "dir": str(d), "n": n, "nama": d.name,
            "peringatan": peringatan, "masuk_dataset": n_masuk}
