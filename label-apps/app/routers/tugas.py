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

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import Settings, get_settings
from ..deps import current_session, current_session_api, bodi_json
from ..services import tag as svc_tag
from ..services import projek as svc_projek
from ..services import tugas as svc
from ..session import Session
from ..templating import templates

# Batas satu permintaan borongan, sama dengan di rute tag. Bukan penjagaan
# keamanan — penjaganya per gambar — melainkan supaya satu permintaan tidak
# menahan proses selama menit.
MAKS_SEKALI = 50_000

router = APIRouter(tags=["tugas"])

# Ubin yang digambar sekaligus di halaman bagi. Di atas ini daftarnya
# dipotong dan sisanya cukup disebut angkanya: dua puluh ribu ubin
# membuat halaman berat padahal yang diputuskan cuma berapa banyak dan
# untuk siapa.
MAKS_UBIN = 400


def _siap(sess: Session) -> tuple[dict | None, str]:
    if sess.src is None:
        return None, "belum ada dataset terbuka"
    return svc.baca_projek(sess.src, get_settings().uploads_root), ""


def _paths(nilai):
    """
    Daftar path dari bodi. None kalau bentuknya bukan larik atau teks.

    Dibedakan dari daftar kosong dengan sengaja: yang kosong itu permintaan
    tanpa isi, yang salah bentuk itu permintaan yang keliru, dan keduanya
    pantas dijawab berbeda.
    """
    if nilai is None:
        return []
    if isinstance(nilai, (list, tuple)):
        return [str(x) for x in nilai]
    return [str(nilai)] if isinstance(nilai, str) else None


def _akun_sah(nama: str, settings: Settings) -> str:
    """
    Pesan penolakan kalau `nama` bukan akun terdaftar, atau "".

    Sebelumnya nama apa pun diterima: yang tidak terdaftar, yang 300 karakter,
    bahkan "../../etc/passwd". Job untuk pelabel hantu mengunci gambarnya —
    tidak ada yang bisa menyuntingnya lagi kecuali pemilik projek, dan tidak
    ada satu pun layar yang menjelaskan kenapa.
    """
    from ..security import load_users

    if not nama:
        return "belum memilih pelabelnya"
    if nama not in load_users(settings.users_file):
        return (f"akun '{nama[:40]}' tidak terdaftar; buatkan akunnya dulu di "
                f"halaman Kelola akun, atau undang lewat alamat surel")
    return ""


def _kunci(sess: Session, paths: list[str]) -> tuple[list[str], int]:
    """
    Path dari peramban -> kunci projek, hanya yang benar-benar ada.

    Mengembalikan juga berapa yang TIDAK dikenal. Dulu path asing dibuang
    diam-diam, jadi "dilewati" pada balasan pembagian tidak pernah
    menghitungnya: enam path masuk, satu terbagi, dua dilaporkan dilewati, dan
    tiga sisanya tidak disebut di mana pun.

    Ganda dibuang. Antarmukanya sendiri tidak bisa menghasilkan path ganda,
    tetapi rutenya menyimpannya apa adanya dan papan lalu mempercayainya
    selamanya: satu gambar dikirim tiga kali menjadi "3 gambar, 0 dikerjakan".
    """
    out, tidak_dikenal = [], 0
    terlihat = set()
    for p in paths[:200_000]:
        it = sess.find(p)
        if it is None:
            tidak_dikenal += 1
            continue
        k = svc_tag.kunci_gambar(sess.src, it["img"])
        if k not in terlihat:
            terlihat.add(k)
            out.append(k)
    return out, tidak_dikenal


def _belum_ditugaskan(sess: Session, d, data: dict, batch: str = "") -> list[str]:
    """
    Kunci gambar yang belum ditugaskan ke siapa pun, urut seperti di halaman.

    Dipakai halaman /bagi DAN rute pembaginya, supaya keduanya tidak pernah
    berbeda pendapat tentang apa yang sedang dibagikan.
    """
    ditugaskan = {k for x in data["tugas"].values() for k in (x.get("gambar") or [])}
    tdata_tag = svc_tag.baca(d)
    out = []
    for it in sess.items:
        k = svc_tag.kunci_gambar(d, it["img"])
        if k in ditugaskan:
            continue
        # Sudah masuk dataset = sudah selesai, bukan "belum ditugaskan" (sejalan
        # dengan papan Anotasi). Tanpa ini, unggahan dataset jadi muncul lagi di
        # /bagi dan ikut terbagi ke pelabel padahal sudah jadi dataset.
        if svc.sudah_dimasukkan(data, k):
            continue
        # Dibatasi ke satu unggahan kalau diminta: membagi biasanya dilakukan
        # per unggahan, dan menyodorkan seluruh sisa projek saat yang dimaksud
        # satu folder membuat slidernya menunjuk kumpulan yang salah.
        if batch and svc_tag.untuk(tdata_tag, k)["batch"] != batch:
            continue
        out.append(k)
    return out


@router.get("/anotasi", response_class=HTMLResponse)
async def halaman_papan(request: Request, ds: str = "", urut: str = "terbaru",
                        sess: Session = Depends(current_session),
                        settings: Settings = Depends(get_settings)):
    """
    Papan anotasi: siapa mengerjakan apa, dan sudah sejauh mana.

    Tiga kolom, sepadan dengan papan Roboflow. Yang menggerakkan kolomnya bukan
    tombol melainkan keadaan: sebuah job pindah ke Dikerjakan begitu ada satu
    gambarnya berlabel, dan ke Selesai begitu seluruhnya masuk dataset.
    """
    from ..services import projek as sp
    from ..services import scanner

    d = sp.temukan(settings.uploads_root, sess.user, ds)
    if d is None:
        return RedirectResponse("/pilih", status_code=303)
    if str(sess.src or "") != str(d):
        await asyncio.to_thread(sess.load, d)
    else:
        await asyncio.to_thread(sess.segarkan)

    data = svc.baca_projek(d, settings.uploads_root)
    # Bersihkan job 0 gambar yang terlanjur ada (leftover dari membagi gambar
    # yang semuanya sudah ditugaskan). Tidak ada pekerjaan yang hilang — memang
    # kosong — dan sesudahnya papan tidak lagi menampilkan job yang membingungkan.
    if any(not (t.get("gambar") or []) for t in data.get("tugas", {}).values()):
        await asyncio.to_thread(svc.buang_job_kosong, d, data["pemilik"] or sess.user)
        data = svc.baca_projek(d, settings.uploads_root)
    pr = await asyncio.to_thread(sp.konteks, d, settings.uploads_root, sess.user)

    with sess.lock:
        items = list(sess.items)
    # Kunci tiap gambar dihitung SEKALI, lalu dipakai ulang. Dulu tiga blok di
    # bawah masing-masing memanggil kunci_gambar atas SELURUH isi projek — pada
    # 11.000 gambar itu tiga kali 0,5 detik hanya untuk kunci yang sama.
    kunci_dari = {id(it): svc_tag.kunci_gambar(d, it["img"]) for it in items}
    semua = set(kunci_dari.values())
    # "Sudah dikerjakan" berarti punya berkas anotasi, TERMASUK yang ditandai
    # latar. Menandai gambar sebagai tanpa objek adalah keputusan yang sudah
    # diambil; menghitungnya sebagai belum dikerjakan membuat kemajuan pelabel
    # yang datasetnya banyak latar tampak macet.
    berlabel = {kunci_dari[id(it)] for it in items
                if scanner.severity(it) != "stop"}

    # Nama unggahan tiap gambar, dipakai mengelompokkan kolom pertama.
    tdata_tag = svc_tag.baca(d)
    batch_dari = {}
    if tdata_tag["gambar"]:
        for it in items:
            k = kunci_dari[id(it)]
            b = svc_tag.untuk(tdata_tag, k)["batch"]
            if b:
                batch_dari[k] = b

    # Nama sumber dataset (data.yaml ekspor Roboflow) buat menamai kartu kolom
    # Dataset — unggahan dataset jadi yang auto-masuk tampil sebagai kartu.
    sumber_impor = scanner.nama_sumber_dataset(d)
    papan = svc.papan(data, berlabel, semua, batch_dari, urut, sumber_impor)
    return templates.TemplateResponse(request, "anotasi.html", {
        "sess": sess, "pr": pr, "aktif": "anotasi",
        "boleh_kelola": svc.boleh_kelola(data, sess.user),
        "boleh_bagi": svc.boleh_bagi(data, sess.user),
        "pemilik": data["pemilik"] or sess.user,
        "aku": sess.user,
        # Dipakai kolom "Dikerjakan" yang kosong untuk membedakan anggota yang
        # menunggu dibagi dari orang yang kebetulan lewat.
        "anggota_semua": set(data["anggota"]),
        "urut_pilihan": svc.URUT_PAPAN,
        # Format anotasi yang dituju projek. `jenis` kosong berarti belum
        # ditentukan orang; `jenis_efektif` yang benar-benar dipakai, ditebak
        # dari bentuk terbanyak. Keduanya dikirim supaya layar bisa membedakan
        # "otomatis (poligon)" dari "poligon" yang dipilih sengaja.
        "jenis": data.get("jenis_anotasi") or "",
        "jenis_efektif": svc.jenis_berlaku(data, items),
        "jenis_pilihan": svc.JENIS_ANOTASI,
        **papan,
    })


@router.post("/api/tugas/jenis")
async def set_jenis(ds: str = "", jenis: str = "",
                    sess: Session = Depends(current_session_api),
                    settings: Settings = Depends(get_settings)):
    """
    Tetapkan format anotasi yang dituju projek: "poligon", "kotak", atau ""
    untuk kembali ke tebakan otomatis.

    Hanya pemilik/pengelola. Setelan ini menentukan bentuk mana yang dianggap
    tidak sesuai, dan bentuk tak sesuai dikembalikan ke antrean kerja saat
    versi dibuat -- jadi ia mengubah pekerjaan orang lain, bukan cuma tampilan.
    """
    d = svc_projek.temukan(settings.uploads_root, sess.user, ds)
    if d is None:
        return {"ok": False, "error": "projek tidak ada"}
    data = await asyncio.to_thread(svc.baca_projek, d, settings.uploads_root)
    if not svc.boleh_kelola(data, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang mengubah ini"}
    try:
        r = await asyncio.to_thread(svc.set_jenis, d, jenis, sess.user)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, **r}


@router.get("/api/tugas/skeleton")
async def get_skeleton(ds: str = "", sess: Session = Depends(current_session_api),
                       settings: Settings = Depends(get_settings)):
    """Template skeleton (keypoint) projek — untuk kanvas & editor template.

    Boleh dilihat SIAPA PUN anggota (pelabel perlu tahu nama/urutan keypoint
    agar bisa melabeli), tapi hanya pemilik yang mengubahnya (POST)."""
    # ds kosong = projek yang SEDANG dibuka (dipakai dari kanvas /label).
    d = svc_projek.temukan(settings.uploads_root, sess.user, ds) if ds else sess.src
    if d is None:
        return {"ok": False, "error": "projek tidak ada / belum ada yang dibuka"}
    data = await asyncio.to_thread(svc.baca_projek, d, settings.uploads_root)
    if not svc.boleh_lihat(data, sess.user):
        return {"ok": False, "error": "kamu bukan anggota projek ini"}
    return {"ok": True, "skeleton": data.get("skeleton"),
            "jenis": data.get("jenis_anotasi", ""),
            "boleh_ubah": svc.boleh_kelola(data, sess.user)}


@router.post("/api/tugas/skeleton")
async def post_skeleton(request: Request, ds: str = "",
                        sess: Session = Depends(current_session_api),
                        settings: Settings = Depends(get_settings)):
    """Tetapkan template skeleton projek (nama keypoint + edge + flip_idx).

    Hanya pemilik. Jumlah & posisi keypoint ditentukan PER PROJEK di sini —
    court 33 titik, botol 4 titik, dst. Menyimpan template berisi otomatis
    menandai projek sebagai jenis 'kerangka'."""
    d = svc_projek.temukan(settings.uploads_root, sess.user, ds) if ds else sess.src
    if d is None:
        return {"ok": False, "error": "projek tidak ada / belum ada yang dibuka"}
    data = await asyncio.to_thread(svc.baca_projek, d, settings.uploads_root)
    if not svc.boleh_kelola(data, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang mengubah ini"}
    body = await bodi_json(request)
    template = body.get("skeleton") if isinstance(body.get("skeleton"), dict) else body
    r = await asyncio.to_thread(svc.set_skeleton, d, template, sess.user)
    return {"ok": True, **r}


@router.get("/api/tugas/aksi")
async def get_aksi(ds: str = "", sess: Session = Depends(current_session_api),
                   settings: Settings = Depends(get_settings)):
    """Daftar kelas aksi projek (projek VIDEO sub-jenis aksi).

    Sama bentuk izinnya dengan /api/tugas/skeleton: SIAPA PUN anggota boleh
    melihat (pelabel perlu tahu daftar kelas untuk melabeli klip), hanya pemilik
    yang mengubah (POST)."""
    d = svc_projek.temukan(settings.uploads_root, sess.user, ds) if ds else sess.src
    if d is None:
        return {"ok": False, "error": "projek tidak ada / belum ada yang dibuka"}
    data = await asyncio.to_thread(svc.baca_projek, d, settings.uploads_root)
    if not svc.boleh_lihat(data, sess.user):
        return {"ok": False, "error": "kamu bukan anggota projek ini"}
    return {"ok": True, "aksi": data.get("aksi"),
            "boleh_ubah": svc.boleh_kelola(data, sess.user)}


@router.post("/api/tugas/aksi")
async def post_aksi(request: Request, ds: str = "",
                    sess: Session = Depends(current_session_api),
                    settings: Settings = Depends(get_settings)):
    """Tetapkan daftar kelas aksi projek (nama kelas + merge + kelas negatif).

    Hanya pemilik. Jumlah & nama kelas ditentukan PER PROJEK di sini — bebas,
    bukan daftar tetap. Padanan POST /api/tugas/skeleton (owner-only write,
    members read)."""
    d = svc_projek.temukan(settings.uploads_root, sess.user, ds) if ds else sess.src
    if d is None:
        return {"ok": False, "error": "projek tidak ada / belum ada yang dibuka"}
    data = await asyncio.to_thread(svc.baca_projek, d, settings.uploads_root)
    if not svc.boleh_kelola(data, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang mengubah ini"}
    body = await bodi_json(request)
    template = body.get("aksi") if isinstance(body.get("aksi"), dict) else body
    r = await asyncio.to_thread(svc.set_aksi, d, template, sess.user)
    return {"ok": True, **r}


@router.get("/tugas/{tid}", response_class=HTMLResponse)
async def halaman_job(request: Request, tid: str, ds: str = "",
                      saring_q: str = Query("semua", alias="saring"),
                      dsf_q: str = Query("semua", alias="dsf"),
                      kelas_q: list[str] = Query([], alias="c"),
                      latar_q: int = Query(0, alias="bg"),
                      per_q: int = Query(0, alias="per"),
                      hal_q: int = Query(1, alias="hal"),
                      dari_q: int = Query(0, alias="dari"),
                      sess: Session = Depends(current_session),
                      settings: Settings = Depends(get_settings)):
    """
    Rincian satu job: apa saja isinya, mana yang sudah dikerjakan, dan mana
    yang sudah dinyatakan masuk dataset.

    Di sinilah pekerjaan yang sudah selesai dipindahkan ke dataset. Melabeli
    dan menyatakan selesai sengaja dua tindakan terpisah: yang pertama
    dilakukan berkali-kali sambil ragu, yang kedua sekali dan berakibat.

    Saringan (tab dan kelas) dan paginasi dikerjakan DI SERVER, sama seperti
    grid Dataset. Dulu seluruh isi job dirender sekali jalan lalu disaring di
    peramban: job produksi berisi 11.409 gambar berarti 11.409 kartu, HTML
    15,8 MB, dan setiap muat halaman menembakkan 11.409 permintaan thumbnail.
    Menyaring di peramban tidak menolong — kartunya tetap harus dikirim dan
    dibangun dulu sebelum ada yang bisa disembunyikan.
    """
    from ..routers.review import (PER_BAWAAN, PER_PILIHAN,
                                per_efektif, simpan_per)
    from ..services import projek as sp
    from ..services import scanner

    d = sp.temukan(settings.uploads_root, sess.user, ds)
    if d is None:
        return RedirectResponse("/pilih", status_code=303)
    if str(sess.src or "") != str(d):
        await asyncio.to_thread(sess.load, d)
    else:
        # Isi projek dipindai sekali lalu dipakai dari ingatan. Sejak
        # pekerjaannya dibagi, anotasi yang dibuat pelabel tidak pernah
        # terlihat oleh pemilik projek — papan kemajuannya membeku di angka
        # saat ia membukanya, dan memuat ulang halaman tidak menolong.
        await asyncio.to_thread(sess.segarkan)

    data = svc.baca_projek(d, settings.uploads_root)
    job = data["tugas"].get(tid)
    if job is None:
        return RedirectResponse(f"/anotasi?ds={ds}", status_code=303)

    pr = await asyncio.to_thread(sp.konteks, d, settings.uploads_root, sess.user)

    with sess.lock:
        items = list(sess.items)
    punya = set(job.get("gambar") or [])
    isi = []
    for it in items:
        k = svc_tag.kunci_gambar(d, it["img"])
        if k not in punya:
            continue
        sev = scanner.severity(it)
        isi.append({
            "it": it, "kunci": k,
            # Latar TERMASUK sudah dianotasi. Menandai gambar tanpa objek
            # adalah keputusan yang sudah diambil, bukan pekerjaan yang belum
            # dikerjakan, dan memisahkannya jadi kolom sendiri membuat angka
            # "sudah dianotasi" mengecil setiap kali seseorang menyelesaikan
            # satu contoh negatif.
            "berlabel": sev != "stop",
            "latar": sev == "bg",
            "kelas": sorted({str(s["label"]) for s in it["shapes"]}),
            "di_dataset": svc.sudah_dimasukkan(data, k),
        })
    isi.sort(key=lambda x: x["it"]["img"].name)

    # Angka tab dan bilah kemajuan dihitung dari SELURUH isi job, bukan dari
    # halaman yang sedang tampil: "Belum dianotasi 86" harus tetap 86 di
    # halaman berapa pun.
    n_semua = len(isi)
    n_label = sum(1 for x in isi if x["berlabel"])
    n_latar = sum(1 for x in isi if x["latar"])
    n_ds = sum(1 for x in isi if x["di_dataset"])
    # Kelas yang benar-benar dipakai DI JOB INI, bukan seluruh projek: saringan
    # yang menawarkan kelas yang tidak ada isinya selalu memberi nol, dan yang
    # membacanya menyangka jatahnya kosong.
    kelas_hitung: dict[str, int] = {}
    for x in isi:
        for k2 in x["kelas"]:
            kelas_hitung[k2] = kelas_hitung.get(k2, 0) + 1

    # -- saringan, DI SERVER (dulu di job.js) --------------------------------
    saring = saring_q if saring_q in ("semua", "belum", "sudah") else "semua"
    # Latar termasuk "sudah dianotasi": menandai gambar tanpa objek adalah
    # keputusan yang sudah diambil, bukan pekerjaan yang belum dikerjakan.
    if saring == "belum":
        tampil = [x for x in isi if not x["berlabel"]]
    elif saring == "sudah":
        tampil = [x for x in isi if x["berlabel"]]
    else:
        tampil = list(isi)
    # Saringan status dataset — terpisah dari status anotasi. Pada projek besar
    # gambar yang SUDAH dianotasi tapi BELUM dimasukkan ke dataset tersebar di
    # antara ribuan yang sudah masuk, dan tanpa saringan ini tidak ada cara
    # menemukan mana yang tinggal dimasukkan. Justru inilah yang dihitung
    # lencana Anotasi di sidebar.
    dsf = dsf_q if dsf_q in ("semua", "di", "belum") else "semua"
    if dsf == "di":
        tampil = [x for x in tampil if x["di_dataset"]]
    elif dsf == "belum":
        tampil = [x for x in tampil if not x["di_dataset"]]
    kelas_pilih = [k for k in dict.fromkeys(kelas_q) if k in kelas_hitung]
    latar_pilih = bool(latar_q)
    if kelas_pilih or latar_pilih:
        # "Punya salah satu", sama seperti di grid: "botol atau latar" adalah
        # satu pertanyaan, dan menuntut keduanya sekaligus hampir tidak pernah
        # yang dimaksud.
        kpset = set(kelas_pilih)
        tampil = [x for x in tampil
                  if (latar_pilih and x["latar"])
                  or (kpset & set(x["kelas"]))]

    # -- paginasi, pola sama persis dengan grid Dataset ----------------------
    per = per_efektif(per_q, request)
    n_tampil = len(tampil)
    n_hal = max(1, -(-n_tampil // per))
    hal = (dari_q - 1) // per + 1 if dari_q > 0 else hal_q
    hal = min(max(hal, 1), n_hal)
    mulai_i = (hal - 1) * per
    halaman = tampil[mulai_i:mulai_i + per]

    resp = templates.TemplateResponse(request, "job.html", {
        "sess": sess, "pr": pr, "aktif": "anotasi", "aku": sess.user,
        "tid": tid, "job": job, "isi": halaman,
        "n": n_semua, "n_label": n_label, "n_latar": n_latar,
        "n_dataset": n_ds,
        "kelas_hitung": dict(sorted(kelas_hitung.items())),
        "persen": round(n_label * 100 / n_semua) if n_semua else 0,
        # Keadaan saringan, supaya tab dan menu kelas tahu yang sedang aktif.
        "saring": saring,
        "dsf": dsf,
        "n_belum_ds": n_semua - n_ds,
        "kelas_aktif": set(kelas_pilih),
        "latar_aktif": latar_pilih,
        # Paginasi. `mulai`/`akhir` 1-berbasis untuk "1–50 dari 86".
        "per": per, "per_pilihan": PER_PILIHAN, "per_bawaan": PER_BAWAAN,
        "hal": hal, "n_hal": n_hal, "n_tampil": n_tampil,
        "mulai": mulai_i + 1 if n_tampil else 0,
        "akhir": mulai_i + len(halaman),
        # Yang boleh memindahkan ke dataset: pelabel job ini sendiri, pemilik,
        # atau Editor (yang berhak melabeli SEMUA). Diselaraskan dengan rute
        # /api/tugas/dataset supaya tombolnya tidak tersembunyi dari orang yang
        # rutenya memang mengizinkan.
        "boleh_ubah": (sess.user == job.get("pelabel")
                       or svc.boleh_unggah(data, sess.user)),
    })
    simpan_per(resp, per_q)
    return resp


@router.get("/bagi", response_class=HTMLResponse)
async def halaman_bagi(request: Request, ds: str = "", batch: str = "",
                       sess: Session = Depends(current_session),
                       settings: Settings = Depends(get_settings)):
    """
    Bagi gambar yang belum ditugaskan ke anggota tim.

    Inilah yang menggantikan "langsung ke grid" setelah unggah. Unggahan yang
    selesai lalu mendarat di grid tidak menjawab pertanyaan berikutnya, yaitu
    siapa yang mengerjakan ini; halaman ini yang menjawabnya.
    """
    from ..services import projek as sp

    d = sp.temukan(settings.uploads_root, sess.user, ds)
    if d is None:
        return RedirectResponse("/pilih", status_code=303)
    if str(sess.src or "") != str(d):
        await asyncio.to_thread(sess.load, d)
    else:
        await asyncio.to_thread(sess.segarkan)

    data = svc.baca_projek(d, settings.uploads_root)
    pr = await asyncio.to_thread(sp.konteks, d, settings.uploads_root, sess.user)

    kunci_belum = _belum_ditugaskan(sess, d, data, batch)
    punya = set(kunci_belum)
    belum = [it for it in sess.items
             if svc_tag.kunci_gambar(d, it["img"]) in punya]

    return templates.TemplateResponse(request, "bagi.html", {
        "sess": sess, "pr": pr, "aktif": "anotasi",
        "boleh_kelola": svc.boleh_kelola(data, sess.user),
        "boleh_bagi": svc.boleh_bagi(data, sess.user),
        "pemilik": data["pemilik"] or sess.user,
        "anggota": sorted(data["anggota"]),
        "undangan": svc.undangan_terbuka(data),
        "batch": batch,
        "belum": belum[:MAKS_UBIN],
        "n_belum": len(belum),
        "n_dipotong": max(0, len(belum) - MAKS_UBIN),
    })


@router.get("/api/tugas/calon")
async def calon(sess: Session = Depends(current_session_api),
                settings: Settings = Depends(get_settings)):
    """
    Akun yang bisa ditugaskan.

    Hanya untuk pemilik projek. Daftar akun adalah keterangan tentang orang;
    membukanya ke siapa pun yang punya sesi berarti siapa pun bisa menyusun
    daftar seluruh anggota tim.
    """
    data, galat = _siap(sess)
    if galat:
        return {"ok": False, "error": galat}
    if not svc.boleh_bagi(data, sess.user):
        return {"ok": False, "error": "hanya pemilik atau editor yang bisa membagi tugas"}
    from ..security import load_users
    users = load_users(settings.users_file)
    # Alamat surel hanya untuk orang yang MEMANG sudah di projek ini. Daftar
    # akun sendiri perlu ditampilkan — itu yang dipakai memilih pelabel — tapi
    # alamat surelnya tidak: setiap pengguna terdaftar adalah pemilik
    # projeknya sendiri, jadi "hanya pemilik projek" tidak menyaring siapa pun,
    # dan rute ini sempat menyerahkan seluruh alamat surel tim ke akun mana pun
    # yang membuat satu projek kosong.
    out = []
    for a, r in sorted(users.items()):
        anggota = a in data["anggota"] or a == data["pemilik"]
        info = data["anggota"].get(a) or {}
        # Peran & scope ikut supaya panel bisa menampilkan dan menyuntingnya.
        peran = ("pemilik" if a == data["pemilik"]
                 else svc.sah_peran(info.get("peran")) if a in data["anggota"]
                 else "")
        out.append({"akun": a, "nama": (r.get("nama") or a),
                    "email": (r.get("email") or "") if anggota else "",
                    "anggota": anggota, "peran": peran,
                    "akses": info.get("akses", ""),
                    "batch_scope": info.get("batch", [])})
    # Daftar batch yang ada di projek ini, untuk memilih scope Labeler spesifik.
    batches = sorted(svc_tag.hitung(svc_tag.baca(sess.src))["batch"].keys())
    return {"ok": True, "akun": out, "pemilik": data["pemilik"],
            "anggota": sorted(data["anggota"]),
            "batch_tersedia": batches,
            # Undangan yang belum dipakai ikut, supaya panelnya bisa
            # menampilkan dan membatalkannya. Tautan yang tidak bisa dicabut
            # berlaku selamanya, dan itu bukan yang dimaksud saat mengundang.
            "undangan": svc.undangan_terbuka(data)}


@router.post("/api/tugas/undang")
async def undang(akun: str = "", peran: str = "pelabel", akses: str = "",
                 batch: str = "", sess: Session = Depends(current_session_api),
                 settings: Settings = Depends(get_settings)):
    """Undang akun yang sudah ada, dengan peran & scope-nya.

    `peran` = "editor" | "pelabel"; untuk pelabel, `akses` = "semua" |
    "spesifik" dan `batch` daftar nama batch (dipisah koma) untuk scope
    spesifik. `akses` kosong = undangan polos (labeler warisan, lihat service).
    """
    data, galat = _siap(sess)
    if galat:
        return {"ok": False, "error": galat}
    if not svc.boleh_kelola(data, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang bisa mengundang"}
    tolak = _akun_sah(akun, settings)
    if tolak:
        return {"ok": False, "error": tolak}
    if akun == data["pemilik"]:
        # Dulu dijawab ok:true dengan daftar anggota KOSONG — bukan sekadar
        # "tidak berubah", tapi keadaan yang salah — lalu layarnya menoast
        # "X jadi anggota" padahal tidak terjadi apa-apa.
        return {"ok": False, "error": "kamu pemilik projek ini, tidak perlu "
                                      "diundang"}
    r = await asyncio.to_thread(svc.undang, sess.src, data["pemilik"], akun,
                                peran, akses or None, batch)
    return {"ok": True, **r}


@router.post("/api/tugas/keluarkan-anggota")
async def keluarkan_anggota(akun: str = "",
                            sess: Session = Depends(current_session_api)):
    data, galat = _siap(sess)
    if galat:
        return {"ok": False, "error": galat}
    if not svc.boleh_kelola(data, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang bisa mengeluarkan"}
    # Dulu rute ini menjawab ok:true untuk apa pun — nama hantu, nama kosong,
    # akun yang memang bukan anggota — sehingga salah ketik tidak pernah
    # kelihatan. Dan akun pemilik sendiri diterima, lalu membubarkan job
    # miliknya sendiri sambil melaporkan daftar anggota yang tidak berubah.
    if not akun:
        return {"ok": False, "error": "belum memilih siapa yang dikeluarkan"}
    if akun == data["pemilik"]:
        return {"ok": False, "error": "kamu pemilik projek ini; pemilik tidak "
                                      "bisa dikeluarkan dari projeknya sendiri"}
    if akun not in data["anggota"]:
        return {"ok": False, "error": f"'{akun[:40]}' bukan anggota projek ini"}
    r = await asyncio.to_thread(svc.keluarkan_anggota, sess.src,
                                data["pemilik"], akun)
    return {"ok": True, **r}


@router.post("/api/tugas/atur-anggota")
async def atur_anggota(akun: str = "", peran: str = "", akses: str = "",
                       batch: str = "",
                       sess: Session = Depends(current_session_api)):
    """Ubah peran/scope anggota yang sudah ada. Hanya pemilik projek.

    `peran` kosong = jangan ubah peran; `akses` kosong = jangan ubah akses.
    `batch` (dipisah koma) hanya berlaku saat aksesnya spesifik.
    """
    data, galat = _siap(sess)
    if galat:
        return {"ok": False, "error": galat}
    if not svc.boleh_kelola(data, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang bisa mengatur anggota"}
    if akun == data["pemilik"]:
        return {"ok": False, "error": "pengaturan ini untuk anggota, bukan pemilik"}
    if akun not in data["anggota"]:
        return {"ok": False, "error": f"'{akun[:40]}' bukan anggota projek ini"}
    return await asyncio.to_thread(
        svc.atur_anggota, sess.src, data["pemilik"], akun,
        peran=peran or None, akses=akses or None, batch=batch)


@router.post("/api/tugas/undang-email")
async def undang_email(email: str = "", peran: str = "pelabel", akses: str = "",
                       batch: str = "", request: Request = None,
                       sess: Session = Depends(current_session_api)):
    """
    Undangan untuk orang yang belum punya akun di sini.

    Yang dikembalikan sebuah tautan untuk disalin. Pengirimannya lewat surel
    menyusul; yang dibuat di sini tetap sama persis, jadi menambahkan pengirim
    nanti tidak mengubah apa pun yang sudah terlanjur dibagikan.
    """
    data, galat = _siap(sess)
    if galat:
        return {"ok": False, "error": galat}
    if not svc.boleh_kelola(data, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang bisa mengundang"}

    # Kalau alamat itu sudah dipakai sebuah akun, langsung jadikan anggota:
    # menyuruh orang yang sudah punya akun menerima tautan undangan cuma
    # menambah satu langkah yang tidak menghasilkan apa-apa.
    alamat = (email or "").strip()
    if not alamat:
        return {"ok": False, "error": "alamat surel masih kosong"}

    from ..security import load_users
    from ..config import get_settings as _gs
    users = load_users(_gs().users_file)
    for akun, rec in users.items():
        # `or None` penting: akun tanpa email punya kolom kosong, dan
        # membandingkannya dengan alamat kosong membuat orang pertama yang
        # kebetulan belum mengisi email jadi anggota tanpa pernah diundang.
        surel_akun = str(rec.get("email") or "").strip().lower()
        if surel_akun and surel_akun == alamat.lower():
            r = await asyncio.to_thread(svc.undang, sess.src, sess.user, akun,
                                        peran, akses or None, batch)
            return {"ok": True, "akun": akun, "sudah_terdaftar": True, **r}

    try:
        r = await asyncio.to_thread(svc.undang_email, sess.src, sess.user,
                                    alamat, peran, akses or None, batch)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    asal = str(request.base_url).rstrip("/") if request else ""
    return {"ok": True, "sudah_terdaftar": False, "email": r["email"],
            "tautan": f"{asal}/undangan/{r['token']}"}


@router.post("/api/tugas/batalkan-undangan")
async def batalkan_undangan(token: str = "",
                            sess: Session = Depends(current_session_api)):
    data, galat = _siap(sess)
    if galat:
        return {"ok": False, "error": galat}
    if not svc.boleh_kelola(data, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang bisa membatalkan"}
    r = await asyncio.to_thread(svc.batalkan_undangan, sess.src,
                                data["pemilik"], token)
    # ok mengikuti apa yang benar-benar terjadi. Dulu selalu True, dan layarnya
    # menoast "Undangan dibatalkan" untuk token ngawur maupun untuk undangan
    # yang sudah dipakai.
    return {"ok": bool(r.get("dibatalkan")), **r}


@router.get("/undangan/{token}", response_class=HTMLResponse)
async def halaman_undangan(request: Request, token: str,
                           sess: Session = Depends(current_session),
                           settings: Settings = Depends(get_settings)):
    """
    Menerima undangan. Butuh sesi, jadi yang belum punya akun dialihkan ke
    halaman masuk lebih dulu lalu kembali ke sini.
    """
    d = await asyncio.to_thread(svc_projek.cari_undangan,
                                settings.uploads_root, token)
    if d is None:
        return templates.TemplateResponse(request, "undangan.html", {
            "sess": sess, "galat": "Undangan ini tidak dikenal atau sudah "
                                   "dibatalkan.", "pr": None})
    hasil = await asyncio.to_thread(svc.pakai_undangan, d, token, sess.user)
    if not hasil.get("ok"):
        return templates.TemplateResponse(request, "undangan.html", {
            "sess": sess, "galat": hasil["error"], "pr": None})
    return templates.TemplateResponse(request, "undangan.html", {
        "sess": sess, "galat": "",
        "pr": {"nama": hasil["nama"], "pemilik": hasil["pemilik"],
               "ds": f"{hasil['pemilik']}/{hasil['nama']}"}})


@router.post("/api/tugas/bagi")
async def bagi(request: Request, sess: Session = Depends(current_session_api),
               settings: Settings = Depends(get_settings)):
    """Buat satu job: sekumpulan gambar untuk satu pelabel."""
    data, galat = _siap(sess)
    if galat:
        return {"ok": False, "error": galat}
    if not svc.boleh_bagi(data, sess.user):
        return {"ok": False, "error": "hanya pemilik atau editor yang bisa membagi tugas"}
    body = await bodi_json(request)
    pelabel = str(body.get("pelabel") or "").strip()
    tolak = _akun_sah(pelabel, settings)
    if tolak:
        return {"ok": False, "error": tolak}
    # Dua cara menyebut apa yang dibagikan.
    #
    # `n` menyerahkan pemilihannya ke server, dan itu yang dipakai halaman
    # /bagi. Sebelumnya peramban yang mengirim daftar path — padahal halaman
    # itu hanya menggambar 400 ubin, sementara slidernya memakai jumlah penuh.
    # Meminta 410 menghasilkan 400 terbagi dan 10 tertinggal diam-diam, dengan
    # kalimat di kepala halaman yang justru berjanji sebaliknya.
    #
    # `gambar` tetap diterima untuk pemilihan yang benar-benar disebut satu per
    # satu, dan untuk pemanggil di luar halaman itu.
    tidak_dikenal = 0
    if body.get("gambar") is None and body.get("n") is not None:
        try:
            n = int(body.get("n"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "jumlah gambar harus angka"}
        semua = _belum_ditugaskan(sess, sess.src, data,
                                  str(body.get("batch") or ""))
        if not semua:
            return {"ok": False, "error": "tidak ada gambar yang belum ditugaskan"}
        n = max(1, min(n, len(semua)))
        if body.get("acak"):
            # Diacak di server dengan alasan yang sama seperti di layar: nama
            # berkas berurutan hampir selalu berarti waktu pemotretan
            # berurutan, dan membagi berurutan memberi satu orang seluruh sesi
            # pagi. Sebelumnya pengacakan hanya berjalan atas 400 ubin pertama,
            # jadi sisanya tidak pernah punya peluang terpilih.
            import random
            semua = random.sample(semua, len(semua))
        kunci = semua[:n]
    else:
        minta = _paths(body.get("gambar"))
        if minta is None:
            return {"ok": False, "error": "daftar gambar harus berupa larik"}
        if not minta:
            return {"ok": False, "error": "belum memilih gambar yang dibagikan"}
        kunci, tidak_dikenal = _kunci(sess, minta)
        if not kunci:
            return {"ok": False, "error": "tidak satu pun gambar itu ada di projek ini"}
    r = await asyncio.to_thread(svc.tugaskan, sess.src, data["pemilik"], pelabel,
                                kunci, str(body.get("catatan") or ""),
                                str(body.get("judul") or ""))
    if r.get("kosong"):
        return {"ok": False, "error": "semua gambar itu sudah ditugaskan ke job "
                "lain — tidak ada job baru dibuat"}
    # Path yang tidak dikenal ikut dihitung dilewati: dulu ia dibuang sebelum
    # tugaskan() sempat melihatnya, jadi enam path masuk dan balasannya
    # menyebut satu terbagi, dua dilewati, tanpa menyinggung tiga sisanya.
    r["dilewati"] = r.get("dilewati", 0) + tidak_dikenal
    return {"ok": True, **r}


@router.post("/api/tugas/ubah")
async def ubah(id: str = "", pelabel: str = "", catatan: str | None = None,
               judul: str | None = None,
               sess: Session = Depends(current_session_api),
               settings: Settings = Depends(get_settings)):
    """Tugaskan ulang, ubah catatan, atau ganti judul job."""
    data, galat = _siap(sess)
    if galat:
        return {"ok": False, "error": galat}
    if not svc.boleh_bagi(data, sess.user):
        return {"ok": False, "error": "hanya pemilik atau editor yang bisa mengubah tugas"}
    if pelabel:
        tolak = _akun_sah(pelabel, settings)
        if tolak:
            return {"ok": False, "error": tolak}
    try:
        r = await asyncio.to_thread(svc.ubah_job, sess.src, sess.user, id,
                                    pelabel=pelabel or None,
                                    catatan=catatan, judul=judul)
    except KeyError:
        return {"ok": False, "error": f"tugas {id} tidak ada"}
    return {"ok": True, **r}


@router.post("/api/tugas/bubarkan")
async def bubarkan(id: str = "", sess: Session = Depends(current_session_api)):
    data, galat = _siap(sess)
    if galat:
        return {"ok": False, "error": galat}
    if not svc.boleh_bagi(data, sess.user):
        return {"ok": False, "error": "hanya pemilik atau editor yang bisa membubarkan"}
    r = await asyncio.to_thread(svc.bubarkan, sess.src, sess.user, id)
    return {"ok": True, **r}


def _punya_alur_dataset(data: dict) -> str:
    """
    Pesan penolakan kalau folder ini bukan projek, atau "".

    Folder dataset BERSAMA tidak punya pemilik: ia dibuka lewat path server,
    dipakai beberapa orang, dan tidak punya alur unggah-tugas-dataset sama
    sekali. Tanpa penjagaan ini, boleh_labeli menjawab ya untuk siapa pun di
    sana — tidak ada tugas dan tidak ada anggota — dan satu klik "Tambahkan ke
    dataset" menulis .tugas.json ke folder itu, menyalakan kurasi, lalu
    MENGECILKAN ekspor folder itu untuk semua orang. Terbukti: ekspor turun
    dari 3 gambar jadi 1, dan satu-satunya cara memulihkannya adalah menghapus
    berkasnya dari disk.
    """
    if not data["pemilik"]:
        return ("folder dataset bersama tidak punya alur dataset — salin dulu "
                "ke ruang kerjamu lewat halaman Unggah")
    return ""


@router.post("/api/latar")
async def tandai_latar(request: Request,
                       sess: Session = Depends(current_session_api),
                       settings: Settings = Depends(get_settings)):
    """
    Tandai sekumpulan gambar sebagai latar, atau lepaskan tandanya.

    Latar adalah gambar yang sengaja dinyatakan tidak berisi objek apa pun:
    contoh negatif, dan ia ikut terekspor sebagai berkas label kosong. Itu
    keputusan yang diambil, bukan pekerjaan yang belum dikerjakan.

    Ada versi satu-gambar di /markbg sejak lama, tetapi hanya bisa dijangkau
    dari grid dan halaman Lihat — dan sejak halaman Dataset cuma memuat isi
    dataset, dua-duanya tidak lagi memuat gambar yang justru paling sering
    perlu ditandai latar: yang baru dibagikan dan belum dikerjakan.

    Menandai latar MENULIS berkas anotasi, jadi tiap gambar tunduk pada
    penjaga yang sama dengan menyimpan bentuk. Diperiksa satu per satu, bukan
    sekali di depan: satu daftar bisa memuat gambar milik dua pelabel.
    """
    from ..services import annotations

    if sess.src is None:
        return {"ok": False, "error": "belum ada dataset terbuka"}
    body = await bodi_json(request)
    minta = _paths(body.get("gambar"))
    if minta is None:
        return {"ok": False, "error": "daftar gambar harus berupa larik"}
    lepas = bool(body.get("lepas"))

    berhasil, tolak = 0, 0
    diubah = []
    with sess.lock:
        for jalur in minta[:MAKS_SEKALI]:
            it = sess.find(jalur)
            if it is None:
                tolak += 1
                continue
            if svc.tolak_tulis(sess.src, sess.user, it["img"]):
                tolak += 1
                continue
            try:
                if lepas:
                    annotations.unmark_background(it)
                else:
                    annotations.mark_background(it)
            except (OSError, annotations.Menolak):
                tolak += 1
                continue
            berhasil += 1
            diubah.append(it["img"])
    if diubah:
        # Sesi LAIN memegang salinan isi projek ini. Tanpa penanda, papan
        # kemajuan mereka membeku di angka sebelum perubahan ini. Gambarnya
        # disebutkan satu per satu supaya mereka cukup membaca ulang yang itu.
        from ..session import tandai_berubah
        for q in diubah:
            tandai_berubah(sess.src, q)
    if not berhasil:
        return {"ok": False,
                "error": "tidak satu pun gambar itu bisa kamu ubah"}
    return {"ok": True, "n": berhasil, "ditolak": tolak, "lepas": lepas}


@router.post("/api/tugas/dataset-siap")
async def dataset_siap(request: Request,
                       sess: Session = Depends(current_session_api),
                       settings: Settings = Depends(get_settings)):
    """
    Masukkan borongan gambar yang sudah dianotasi tetapi belum ditugaskan.

    Gambar seperti ini tidak punya jalan lain sama sekali. Halaman job hanya
    memuat yang sudah dibagi, dan halaman Dataset justru belum memuatnya —
    jadi tanpa rute ini satu-satunya cara memasukkannya adalah membagikannya
    lebih dulu ke seseorang, padahal pekerjaannya sudah selesai.

    Yang dimasukkan dipilih di server, bukan dikirim peramban: daftar path dari
    luar bisa memuat gambar yang sedang dikerjakan orang lain.
    """
    data, galat = _siap(sess)
    if galat:
        return {"ok": False, "error": galat}
    galat = _punya_alur_dataset(data)
    if galat:
        return {"ok": False, "error": galat}
    if not svc.boleh_bagi(data, sess.user):
        return {"ok": False, "error": "hanya pemilik atau editor yang bisa "
                                      "memasukkan gambar yang belum ditugaskan"}
    from ..services import scanner

    body = await bodi_json(request)
    batch = str(body.get("batch") or "")

    with sess.lock:
        items = list(sess.items)
    semua, berlabel, batch_dari = set(), set(), {}
    tdata_tag = svc_tag.baca(sess.src)
    for it in items:
        k = svc_tag.kunci_gambar(sess.src, it["img"])
        semua.add(k)
        if scanner.severity(it) != "stop":
            berlabel.add(k)
        b = svc_tag.untuk(tdata_tag, k)["batch"]
        if b:
            batch_dari[k] = b

    kunci = svc.belum_ditugaskan_siap(data, berlabel, semua, batch, batch_dari)
    if not kunci:
        return {"ok": False, "error": "tidak ada gambar yang sudah dianotasi "
                                      "dan belum ditugaskan di sini"}
    r = await asyncio.to_thread(svc.masukkan, sess.src, kunci, data["pemilik"])
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
    galat = _punya_alur_dataset(data)
    if galat:
        return {"ok": False, "error": galat}
    body = await bodi_json(request)
    minta = _paths(body.get("gambar"))
    if minta is None:
        return {"ok": False, "error": "daftar gambar harus berupa larik"}
    kunci, _ = _kunci(sess, minta)
    if not kunci:
        return {"ok": False, "error": "tidak satu pun gambar itu ada di projek ini"}

    # Pemiliknya diambil dari data yang sudah dibaca lewat letak folder, bukan
    # dari akun pemanggil. Rute inilah satu-satunya penulis berkas tugas yang
    # penjaganya bukan boleh_kelola, jadi tanpa ini folder dataset bersama yang
    # belum berpemilik mencatat pemanggil pertamanya sebagai pemilik.
    pemilik = data["pemilik"]
    keluarkan = bool(body.get("keluarkan"))

    if keluarkan:
        # MENGELUARKAN dari dataset ikut membubarkan penugasannya (gambar balik
        # ke "belum ditugaskan"). Itu kurasi + penugasan ulang, bukan pelabelan,
        # jadi hanya pemilik atau Editor — bukan setiap pelabel yang kebetulan
        # berhak menyunting gambarnya. Tanpa batas ini, labeler menyeluruh bisa
        # menarik gambar keluar dari job orang lain se-projek.
        if not svc.boleh_unggah(data, sess.user):
            return {"ok": False, "error": "hanya pemilik atau Editor yang bisa "
                                          "mengeluarkan gambar dari dataset"}
        ditolak = 0
        r = await asyncio.to_thread(svc.keluarkan, sess.src, kunci, pemilik)
    else:
        # MEMASUKKAN gambar yang sudah dilabeli adalah bagian dari pelabelan,
        # jadi pelabel boleh memasukkan gambar yang BERHAK ia sunting — dicek
        # per gambar DENGAN batch-nya, persis penjaga simpan (tolak_tulis), biar
        # labeler spesifik tak salah ditolak atas gambar dalam scope-nya.
        # Diperiksa per gambar supaya "pilih semua" di halaman seorang pelabel
        # tidak jatuh ke nol gara-gara gambar orang lain.
        tagdata = await asyncio.to_thread(svc_tag.baca, sess.src)
        boleh = [k for k in kunci
                 if svc.boleh_labeli(data, sess.user, k,
                                     svc_tag.untuk(tagdata, k).get("batch", ""))]
        ditolak = len(kunci) - len(boleh)
        if not boleh:
            return {"ok": False, "error": f"{ditolak} gambar bukan tugasmu; "
                                          f"hanya pelabelnya atau pemilik projek "
                                          f"yang bisa memasukkannya ke dataset"}
        kunci = boleh
        # Hanya gambar yang SUDAH dianotasi — berlabel ATAU ditandai latar —
        # yang boleh masuk dataset. Yang belum dianotasi (severity "stop")
        # disingkirkan di sini, bukan sekadar ditanyakan di peramban: konfirmasi
        # peramban bisa dilewati, dan satu gambar tanpa label yang lolos ke
        # dataset ikut terekspor lalu mengajari model sebuah contoh yang tak
        # punya jawaban. Latar TERMASUK dianotasi (menandai gambar tanpa objek
        # adalah keputusan yang sudah diambil).
        from ..services import scanner
        with sess.lock:
            items = list(sess.items)
        sev_dari = {svc_tag.kunci_gambar(sess.src, it["img"]): scanner.severity(it)
                    for it in items}
        berlabel = [k for k in kunci if sev_dari.get(k) != "stop"]
        belum = len(kunci) - len(berlabel)
        if not berlabel:
            return {"ok": False, "ditolak": ditolak, "belum_dianotasi": belum,
                    "error": f"{belum} gambar belum dianotasi — hanya gambar "
                             "yang sudah dianotasi (berlabel atau ditandai "
                             "latar) yang bisa masuk dataset"}
        kunci = berlabel
        r = await asyncio.to_thread(svc.masukkan, sess.src, kunci, pemilik)
        r["belum_dianotasi"] = belum
    r["ditolak"] = ditolak

    # `total` dihitung ulang atas gambar yang BENAR-BENAR ada di dataset
    # sekarang. Angka mentah dari berkasnya ikut menghitung catatan milik
    # gambar yang berkasnya sudah dibuang, dan angka yang lebih besar daripada
    # isi ZIP-nya membuat orang mengira ekspornya kehilangan sesuatu.
    with sess.lock:
        items = list(sess.items)
    ada = {svc_tag.kunci_gambar(sess.src, it["img"]) for it in items}
    data = await asyncio.to_thread(svc.baca, sess.src, sess.user)
    r["total"] = sum(1 for k in data["dataset"] if k in ada)
    return {"ok": True, **r}


@router.post("/api/tugas/hapus-gambar")
async def hapus_gambar(request: Request,
                       sess: Session = Depends(current_session_api),
                       settings: Settings = Depends(get_settings)):
    """
    Buang sekumpulan gambar dari projek ke tempat sampah, bisa dipulihkan.

    Destruktif walau ke sampah: berkasnya benar-benar pindah dari folder yang
    dipindai, jadi ia hilang dari grid, job, dan dataset seketika. Menghapus
    MEDIA adalah pengelolaan, bukan pelabelan — jadi hanya pemilik atau Editor,
    bukan setiap pelabel yang kebetulan bisa menyunting label gambar itu. Labeler
    yang "cuma label" tidak menghapus gambar orang (apalagi se-projek).
    """
    data, galat = _siap(sess)
    if galat:
        return {"ok": False, "error": galat}
    if not svc.boleh_unggah(data, sess.user):
        return {"ok": False, "error": "hanya pemilik atau Editor yang bisa "
                                      "menghapus gambar dari projek"}
    body = await bodi_json(request)
    minta = _paths(body.get("gambar"))
    if minta is None:
        return {"ok": False, "error": "daftar gambar harus berupa larik"}

    # Path -> item hasil pindai (yang memegang letak berkasnya di disk). Yang
    # tak ada di projek dihitung ditolak, bukan menggagalkan seluruh daftar.
    boleh, ditolak = [], 0
    for p in minta:
        it = sess.find(p)
        if it is None:
            ditolak += 1
            continue
        boleh.append(it)
    if not boleh:
        return {"ok": False,
                "error": "tidak satu pun gambar itu ada di projek ini"}

    r = await asyncio.to_thread(svc.buang_gambar, sess.src, boleh, data["pemilik"])
    # Berkasnya sudah pindah dari disk; pindai ulang supaya sesi ini tidak lagi
    # menunjuk gambar yang tak ada, dan indeks find()-nya ikut segar.
    await asyncio.to_thread(sess.load, sess.src)
    r["ditolak"] = ditolak
    return {"ok": True, **r}
