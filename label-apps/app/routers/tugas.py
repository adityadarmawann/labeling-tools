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
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import Settings, get_settings
from ..deps import current_session, current_session_api
from ..services import tag as svc_tag
from ..services import projek as svc_projek
from ..services import tugas as svc
from ..session import Session
from ..templating import templates

router = APIRouter(tags=["tugas"])

# Ubin yang digambar sekaligus di halaman bagi. Di atas ini daftarnya
# dipotong dan sisanya cukup disebut angkanya: dua puluh ribu ubin
# membuat halaman berat padahal yang diputuskan cuma berapa banyak dan
# untuk siapa.
MAKS_UBIN = 400


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

    data = svc.baca(d, sess.user)
    ringkas = await asyncio.to_thread(sp.ringkas, d)
    pr = {"nama": d.name, "path": str(d), **ringkas, "versi": 0,
          "ds": ds if "/" in ds else d.name}

    with sess.lock:
        items = list(sess.items)
    semua = {svc_tag.kunci_gambar(d, it["img"]) for it in items}
    # "Sudah dikerjakan" berarti punya berkas anotasi, TERMASUK yang ditandai
    # latar. Menandai gambar sebagai tanpa objek adalah keputusan yang sudah
    # diambil; menghitungnya sebagai belum dikerjakan membuat kemajuan pelabel
    # yang datasetnya banyak latar tampak macet.
    berlabel = {svc_tag.kunci_gambar(d, it["img"]) for it in items
                if scanner.severity(it) != "stop"}

    # Nama unggahan tiap gambar, dipakai mengelompokkan kolom pertama.
    tdata_tag = svc_tag.baca(d)
    batch_dari = {}
    if tdata_tag["gambar"]:
        for it in items:
            k = svc_tag.kunci_gambar(d, it["img"])
            b = svc_tag.untuk(tdata_tag, k)["batch"]
            if b:
                batch_dari[k] = b

    papan = svc.papan(data, berlabel, semua, batch_dari, urut)
    return templates.TemplateResponse(request, "anotasi.html", {
        "sess": sess, "pr": pr, "aktif": "anotasi",
        "boleh_kelola": svc.boleh_kelola(data, sess.user),
        "pemilik": data["pemilik"] or sess.user,
        "aku": sess.user,
        "urut_pilihan": svc.URUT_PAPAN,
        **papan,
    })


@router.get("/tugas/{tid}", response_class=HTMLResponse)
async def halaman_job(request: Request, tid: str, ds: str = "",
                      sess: Session = Depends(current_session),
                      settings: Settings = Depends(get_settings)):
    """
    Rincian satu job: apa saja isinya, mana yang sudah dikerjakan, dan mana
    yang sudah dinyatakan masuk dataset.

    Di sinilah pekerjaan yang sudah selesai dipindahkan ke dataset. Melabeli
    dan menyatakan selesai sengaja dua tindakan terpisah: yang pertama
    dilakukan berkali-kali sambil ragu, yang kedua sekali dan berakibat.
    """
    from ..services import projek as sp
    from ..services import scanner

    d = sp.temukan(settings.uploads_root, sess.user, ds)
    if d is None:
        return RedirectResponse("/pilih", status_code=303)
    if str(sess.src or "") != str(d):
        await asyncio.to_thread(sess.load, d)

    data = svc.baca(d, sess.user)
    job = data["tugas"].get(tid)
    if job is None:
        return RedirectResponse(f"/anotasi?ds={ds}", status_code=303)

    ringkas = await asyncio.to_thread(sp.ringkas, d)
    pr = {"nama": d.name, "path": str(d), **ringkas, "versi": 0,
          "ds": ds if "/" in ds else d.name}

    with sess.lock:
        items = list(sess.items)
    punya = set(job.get("gambar") or [])
    isi = []
    for it in items:
        k = svc_tag.kunci_gambar(d, it["img"])
        if k not in punya:
            continue
        isi.append({
            "it": it, "kunci": k,
            "berlabel": scanner.severity(it) != "stop",
            "di_dataset": svc.di_dataset(data, k),
        })
    isi.sort(key=lambda x: x["it"]["img"].name)

    n_label = sum(1 for x in isi if x["berlabel"])
    n_ds = sum(1 for x in isi if x["di_dataset"])
    return templates.TemplateResponse(request, "job.html", {
        "sess": sess, "pr": pr, "aktif": "anotasi", "aku": sess.user,
        "tid": tid, "job": job, "isi": isi,
        "n": len(isi), "n_label": n_label, "n_dataset": n_ds,
        "persen": round(n_label * 100 / len(isi)) if isi else 0,
        # Yang boleh memindahkan ke dataset hanya pelabelnya sendiri dan
        # pemilik projek. Sama persis dengan aturan menyunting labelnya.
        "boleh_ubah": (sess.user == job.get("pelabel")
                       or svc.boleh_kelola(data, sess.user)),
    })


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

    data = svc.baca(d, sess.user)
    ringkas = await asyncio.to_thread(sp.ringkas, d)
    pr = {"nama": d.name, "path": str(d), **ringkas, "versi": 0,
          "ds": ds if "/" in ds else d.name}

    ditugaskan = {k for t in data["tugas"].values()
                  for k in (t.get("gambar") or [])}
    tdata_tag = svc_tag.baca(d)
    belum = []
    for it in sess.items:
        k = svc_tag.kunci_gambar(d, it["img"])
        if k in ditugaskan:
            continue
        # Dibatasi ke satu unggahan kalau diminta: membagi biasanya dilakukan
        # per unggahan, dan menyodorkan seluruh sisa projek saat yang dimaksud
        # satu folder membuat slidernya menunjuk kumpulan yang salah.
        if batch and svc_tag.untuk(tdata_tag, k)["batch"] != batch:
            continue
        belum.append(it)

    return templates.TemplateResponse(request, "bagi.html", {
        "sess": sess, "pr": pr, "aktif": "anotasi",
        "boleh_kelola": svc.boleh_kelola(data, sess.user),
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
    if not svc.boleh_kelola(data, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang bisa membagi tugas"}
    from ..security import load_users
    users = load_users(settings.users_file)
    out = [{"akun": a, "nama": (r.get("nama") or a),
            "email": r.get("email") or "",
            "anggota": a in data["anggota"] or a == data["pemilik"]}
           for a, r in sorted(users.items())]
    return {"ok": True, "akun": out, "pemilik": data["pemilik"],
            "anggota": sorted(data["anggota"]),
            # Undangan yang belum dipakai ikut, supaya panelnya bisa
            # menampilkan dan membatalkannya. Tautan yang tidak bisa dicabut
            # berlaku selamanya, dan itu bukan yang dimaksud saat mengundang.
            "undangan": svc.undangan_terbuka(data)}


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


@router.post("/api/tugas/undang-email")
async def undang_email(email: str = "", request: Request = None,
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
    from ..security import load_users
    from ..config import get_settings as _gs
    users = load_users(_gs().users_file)
    for akun, rec in users.items():
        if str(rec.get("email") or "").lower() == email.strip().lower():
            r = await asyncio.to_thread(svc.undang, sess.src, sess.user, akun)
            return {"ok": True, "akun": akun, "sudah_terdaftar": True, **r}

    try:
        r = await asyncio.to_thread(svc.undang_email, sess.src, sess.user, email)
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
    r = await asyncio.to_thread(svc.batalkan_undangan, sess.src, sess.user, token)
    return {"ok": True, **r}


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
                                kunci, str(body.get("catatan") or ""),
                                str(body.get("judul") or ""))
    return {"ok": True, **r}


@router.post("/api/tugas/ubah")
async def ubah(id: str = "", pelabel: str = "", catatan: str | None = None,
               judul: str | None = None,
               sess: Session = Depends(current_session_api)):
    """Tugaskan ulang, ubah catatan, atau ganti judul job."""
    data, galat = _siap(sess)
    if galat:
        return {"ok": False, "error": galat}
    if not svc.boleh_kelola(data, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang bisa mengubah tugas"}
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
