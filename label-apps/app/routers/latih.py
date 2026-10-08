"""
Rute halaman Training.

Sama seperti router lain di sini, berkas ini TIDAK memuat logika: ia
menerjemahkan HTTP saja. Yang memutuskan ada di services/latih.py — termasuk
parameter v14 dan sinkronisasi warnanya, yang terlalu mahal untuk tersebar di
dua tempat.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..config import Settings, get_settings
from ..deps import bodi_json, current_session, current_session_api
from ..log import catat
from ..services import latih as svc
from ..services import latih_kaggle as svc_kaggle
from ..services import projek, tugas as svc_tugas, versi as svc_versi
from ..session import Session
from ..templating import templates

router = APIRouter()
_log = catat("labelapp.latih")


def _projek(sess: Session, settings: Settings, ds: str) -> Path | None:
    return projek.temukan(settings.uploads_root, sess.user, ds)


@router.get("/latih", response_class=HTMLResponse)
async def halaman(request: Request, ds: str = "",
                  sess: Session = Depends(current_session),
                  settings: Settings = Depends(get_settings)):
    d = _projek(sess, settings, ds)
    if d is None:
        return RedirectResponse("/pilih", status_code=303)
    # Projeknya dibuka di sesi ini, sama seperti /versi. Tanpa ini, masuk ke
    # halaman ini langsung dari sidebar membuat rute lain menjawab "belum ada
    # dataset terbuka" pada projek yang jelas-jelas sedang dilihat.
    if str(sess.src or "") != str(d):
        await asyncio.to_thread(sess.load, d)

    pr = await asyncio.to_thread(projek.konteks, d, settings.uploads_root,
                                 sess.user)
    tdata = svc_tugas.baca_projek(d, settings.uploads_root)
    kaggle_siap, _ = svc_kaggle.siap(settings)
    return templates.TemplateResponse(request, "latih.html", {
        "sess": sess, "projek": pr, "pr": pr, "aktif": "latih",
        "boleh_kelola": svc_tugas.boleh_kelola(tdata, sess.user),
        # Pilihan "Jalankan di: Kaggle" hanya muncul kalau backend terkonfigurasi.
        "kaggle_siap": kaggle_siap,
        "kaggle_akun": svc_kaggle.ringkas_akun(settings),
    })


@router.get("/api/latih/bahan")
async def bahan(sess: Session = Depends(current_session_api),
                settings: Settings = Depends(get_settings)):
    """Semua yang dibutuhkan form: versi, bobot, preset, batas.

    Dikirim sekali sebagai satu jawaban, bukan lima permintaan terpisah:
    formnya tidak bisa digambar setengah, dan lima permintaan berarti lima
    kesempatan tampilannya tampil timpang.
    """
    d = sess.src
    if not d:
        return {"ok": False, "error": "belum ada projek terbuka"}
    from ..services import buatversi, olah

    daftar_versi = await asyncio.to_thread(svc_versi.daftar, Path(d))
    kat = olah.katalog_json()["aug"]
    versi_siap = []
    for v in daftar_versi:
        n = v.get("nomor")
        penuh = await asyncio.to_thread(svc_versi.baca, Path(d), n) or v
        yaml = buatversi.dir_versi(Path(d), n) / "data.yaml"
        h = penuh.get("hasil") or {}
        versi_siap.append({
            "nomor": n,
            "dibuat": penuh.get("dibuat"),
            "catatan": penuh.get("catatan") or "",
            # Tanpa data.yaml versinya tidak bisa dilatih — biasanya karena
            # dibuat sebelum ekspor YOLO ada, atau berkasnya sudah dibuang.
            "siap": yaml.exists(),
            "jumlah": h.get("jumlah") or penuh.get("jumlah") or {},
            "kelas": h.get("per_kelas") or {},
            "negatif": h.get("negatif", 0),
            "jenis": h.get("jenis") or "",
            "warna": svc.periksa_warna(penuh, kat),
        })
    siap, alasan = svc.siap_latih()
    kaggle_siap, kaggle_alasan = svc_kaggle.siap(settings)
    return {"ok": True, "siap": siap, "alasan": alasan,
            # Kesiapan backend Kaggle terpisah: server CPU bisa saja tak bisa
            # melatih lokal (siap=False) tapi tetap boleh offload ke Kaggle.
            "kaggle_siap": kaggle_siap, "kaggle_alasan": kaggle_alasan,
            # Status tiap akun pool (jam terpakai 7-hari, sisa, habis) — supaya
            # form bisa menunjukkan akun mana yang masih punya jatah minggu ini.
            "kaggle_akun": await asyncio.to_thread(svc_kaggle.ringkas_akun, settings),
            "versi": versi_siap,
            "bobot": await asyncio.to_thread(svc.bobot_tersedia),
            # Registry preset untuk selektor "Jenis preset" di form. `preset`
            # (par bawaan) dipertahankan untuk render awal; `preset_daftar`
            # memberi semua pilihan + penjelasan awam + apakah pakai toggle warna.
            "preset": svc.preset_par(svc.PRESET_BAWAAN),
            "preset_daftar": [
                {"id": k, "nama": v["nama"], "jelas": v["jelas"],
                 "warna": v["warna"], "par": v["par"]}
                for k, v in svc.PRESET.items()],
            "preset_bawaan": svc.PRESET_BAWAAN,
            "batas": {k: list(v) for k, v in svc.BATAS.items()},
            "tugas": list(svc.TUGAS)}


@router.get("/api/latih/daftar")
async def daftar(sess: Session = Depends(current_session_api)):
    if not sess.src:
        return {"ok": False, "error": "belum ada projek terbuka"}
    d = Path(sess.src)
    return {"ok": True,
            "daftar": await asyncio.to_thread(svc.daftar, d),
            "statistik": await asyncio.to_thread(svc.statistik)}


@router.post("/api/latih/mulai")
async def mulai(request: Request,
                sess: Session = Depends(current_session_api),
                settings: Settings = Depends(get_settings)):
    if not sess.src:
        return {"ok": False, "error": "belum ada projek terbuka"}
    d = Path(sess.src)
    tdata = svc_tugas.baca_projek(d, settings.uploads_root)
    if not svc_tugas.boleh_kelola(tdata, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang boleh melatih"}

    body = await bodi_json(request)
    # DI MANA dijalankan. "kaggle" meng-offload ke GPU Kaggle lewat API — maka
    # server ini TIDAK perlu ultralytics/GPU lokal; yang diperiksa justru apakah
    # akun Kaggle terkonfigurasi. "lokal" (bawaan) tetap menuntut siap_latih().
    backend = str(body.get("backend") or "lokal").strip().lower()
    if backend not in ("lokal", "kaggle"):
        backend = "lokal"
    # Preset (resep augmentasi+hyperparameter): "rvm" (SmartBin) / "olahraga"
    # (Basket). Berlaku untuk seluruh kiriman; divalidasi di siapkan().
    preset = svc.preset_sah(str(body.get("preset") or "rvm").strip().lower())
    if backend == "kaggle":
        siap, alasan = svc_kaggle.siap(settings)
    else:
        siap, alasan = svc.siap_latih()
    if not siap:
        return {"ok": False, "error": alasan}

    nomor_versi = int(body.get("versi") or 0)
    v = await asyncio.to_thread(svc_versi.baca, d, nomor_versi)
    if v is None:
        return {"ok": False, "error": f"versi v{nomor_versi} tidak ada"}

    from ..services import buatversi, olah

    if not (buatversi.dir_versi(d, nomor_versi) / "data.yaml").exists():
        return {"ok": False,
                "error": f"versi v{nomor_versi} tidak punya data.yaml, "
                         "jadi tidak bisa dilatih"}

    # BATCH: satu permintaan boleh membawa beberapa training sekaligus, dan
    # semuanya masuk antrean yang sama. Itu yang membuat "coba tiga setelan
    # lalu bandingkan" bisa ditinggal semalam.
    antrian = body.get("batch") or [body]
    if not isinstance(antrian, list) or not antrian:
        return {"ok": False, "error": "daftar batch kosong"}
    if len(antrian) > 12:
        return {"ok": False, "error": "maksimal 12 training sekali kirim"}

    kat = olah.katalog_json()["aug"]
    dibuat = []
    for satu in antrian:
        # Mode boleh ditentukan per percobaan — itu yang membuat satu antrean
        # bisa membandingkan "bergantung warna" lawan "tidak" dari versi yang
        # sama. Kalau tidak disebut, dipakai mode versinya.
        from ..services import mode_warna as mw

        minta_mode = (satu.get("mode_warna") or "").strip().lower()
        warna = svc.periksa_warna(v, kat)
        if minta_mode in mw.MODE and minta_mode != warna.get("mode"):
            warna = {**warna, "mode": minta_mode,
                     "saran": mw.par_latih(minta_mode),
                     "asal_versi": warna.get("mode"),
                     "pesan": warna.get("pesan", "")}
        try:
            isi = await asyncio.to_thread(
                svc.siapkan, d,
                nama=str(satu.get("nama") or ""),
                versi_nomor=nomor_versi,
                tugas=str(satu.get("tugas") or "segment"),
                bobot=str(satu.get("bobot") or ""),
                par=satu.get("par") or {},
                oleh=sess.user,
                catatan=str(satu.get("catatan") or ""),
                warna=warna, backend=backend, preset=preset)
        except ValueError as e:
            return {"ok": False, "error": str(e), "dibuat": dibuat}
        try:
            await asyncio.to_thread(svc.jalankan, d, isi["nomor"])
        except Exception as e:                   # noqa: BLE001
            _log.exception("gagal meluncurkan training")
            await asyncio.to_thread(svc.perbarui, d, isi["nomor"],
                                    keadaan="gagal", galat=str(e)[:200])
            return {"ok": False, "error": str(e)[:200], "dibuat": dibuat}
        dibuat.append(isi["nomor"])
    return {"ok": True, "dibuat": dibuat}


@router.post("/api/latih/lanjut")
async def lanjut(request: Request,
                 sess: Session = Depends(current_session_api),
                 settings: Settings = Depends(get_settings)):
    """Training LANJUTAN: mulai training baru dengan bobot awal dari best.pt /
    last.pt sebuah training yang sudah ada, mewarisi seluruh setelannya (versi,
    tugas, hsv, mode warna) dan hanya mengganti jumlah epoch.

    Titik awalnya bobot training sumber di projek ini sendiri — bukan path bebas
    dari klien — jadi tak ada berkas .pt sembarang yang bisa dimuat lewat sini.
    """
    if not sess.src:
        return {"ok": False, "error": "belum ada projek terbuka"}
    d = Path(sess.src)
    tdata = svc_tugas.baca_projek(d, settings.uploads_root)
    if not svc_tugas.boleh_kelola(tdata, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang boleh melatih"}
    siap, alasan = svc.siap_latih()
    if not siap:
        return {"ok": False, "error": alasan}

    body = await bodi_json(request)
    dari = int(body.get("dari") or 0)
    jenis = "last" if str(body.get("jenis") or "best").lower() == "last" else "best"
    epochs = int(body.get("epochs") or 0)

    from ..services import buatversi

    sumber = await asyncio.to_thread(svc.baca, d, dari)
    if sumber is None:
        return {"ok": False, "error": f"training L{dari} tidak ada"}
    bobot = await asyncio.to_thread(svc.bobot_training, d, dari, jenis)
    if bobot is None:
        return {"ok": False,
                "error": f"training L{dari} belum punya {jenis}.pt untuk dilanjutkan"}
    # Versi sumbernya harus masih bisa dilatih (data.yaml-nya ada) — kelasnya
    # harus cocok dengan bobot yang dilanjutkan.
    versi_nomor = int(sumber.get("versi") or 0)
    if not (buatversi.dir_versi(d, versi_nomor) / "data.yaml").exists():
        return {"ok": False,
                "error": f"versi v{versi_nomor} (sumber L{dari}) tak punya data.yaml, "
                         "jadi tak bisa dilanjutkan"}

    # Warisi par sumber apa adanya, ganti hanya epochs. Semua yang lain —
    # imgsz, lr, hsv, batch — dibiarkan sama supaya lanjutan benar-benar
    # menyambung setelan yang sama, bukan training baru yang menyaru.
    par = dict(sumber.get("par") or {})
    if epochs:
        par["epochs"] = epochs
    nama = str(body.get("nama") or "").strip() or f"{sumber.get('nama') or f'L{dari}'} lanjutan"
    catatan = str(body.get("catatan") or "").strip() \
        or f"Lanjutan dari L{dari} ({jenis}.pt, epoch {sumber.get('par',{}).get('epochs','?')})"
    try:
        isi = await asyncio.to_thread(
            svc.siapkan, d, nama=nama, versi_nomor=versi_nomor,
            tugas=str(sumber.get("tugas") or "segment"), bobot=str(bobot),
            par=par, oleh=sess.user, catatan=catatan,
            warna=sumber.get("warna") or {}, lanjut_dari=dari,
            preset=sumber.get("preset") or "rvm")
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    try:
        await asyncio.to_thread(svc.jalankan, d, isi["nomor"])
    except Exception as e:                           # noqa: BLE001
        _log.exception("gagal meluncurkan training lanjutan")
        await asyncio.to_thread(svc.perbarui, d, isi["nomor"],
                                keadaan="gagal", galat=str(e)[:200])
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "nomor": isi["nomor"], "dari": dari}


@router.post("/api/latih/sambung-kaggle")
async def sambung_kaggle(nomor: int = 0,
                         sess: Session = Depends(current_session_api),
                         settings: Settings = Depends(get_settings)):
    """Lanjutkan sebuah training Kaggle yang BERHENTI di tengah (tertunda karena
    kuota semua akun menipis, atau gagal) — TANPA kehilangan kemajuan. Worker
    Kaggle melanjutkan dari bobot (last.pt) + epoch kumulatif yang sudah tercatat
    di .latih/L<n>/; penanda 'kuota habis' dibersihkan dulu supaya ini benar-
    benar mencoba lagi sekarang (kalau masih habis, ia tertunda lagi, bukan
    rusak). Beda dari /lanjut yang membuat training BARU dari bobot."""
    if not sess.src:
        return {"ok": False, "error": "belum ada projek terbuka"}
    d = Path(sess.src)
    tdata = svc_tugas.baca_projek(d, settings.uploads_root)
    if not svc_tugas.boleh_kelola(tdata, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang boleh"}
    rek = await asyncio.to_thread(svc.baca, d, nomor)
    if rek is None:
        return {"ok": False, "error": f"training L{nomor} tidak ada"}
    if rek.get("backend") != "kaggle":
        return {"ok": False, "error": "hanya training Kaggle yang bisa disambung begini"}
    if rek.get("keadaan") in svc.BERJALAN:
        return {"ok": False, "error": "training itu masih berjalan"}
    siap, alasan = svc_kaggle.siap(settings)
    if not siap:
        return {"ok": False, "error": alasan}
    await asyncio.to_thread(svc_kaggle.reset_habis,
                            svc_kaggle.basis_ledger(settings))
    try:
        await asyncio.to_thread(svc.jalankan, d, nomor)
    except Exception as e:                           # noqa: BLE001
        _log.exception("gagal menyambung training Kaggle")
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "nomor": nomor}


@router.post("/api/latih/batal")
async def batal(nomor: int = 0, sess: Session = Depends(current_session_api),
                settings: Settings = Depends(get_settings)):
    if not sess.src:
        return {"ok": False, "error": "belum ada projek terbuka"}
    d = Path(sess.src)
    tdata = svc_tugas.baca_projek(d, settings.uploads_root)
    if not svc_tugas.boleh_kelola(tdata, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang boleh"}
    # Baca rekaman DULU (untuk info kernel Kaggle), lalu hentikan poller lokal.
    rek = await asyncio.to_thread(svc.baca, d, nomor)
    await asyncio.to_thread(svc.batalkan, d, nomor)
    # Training Kaggle: poller lokal mati tak menghentikan kernel yang telanjur
    # jalan di Kaggle — maka batalkan remote-nya juga supaya kuota GPU berhenti.
    # Best-effort: kegagalan di sini tak membatalkan pembatalan lokal.
    if rek and rek.get("backend") == "kaggle":
        kag = rek.get("kaggle") or {}
        if kag.get("kernel"):
            await asyncio.to_thread(svc_kaggle.batalkan_remote, settings,
                                    kag["kernel"], kag.get("akun"))
    return {"ok": True}


@router.post("/api/latih/hapus")
async def hapus(nomor: int = 0, sess: Session = Depends(current_session_api),
                settings: Settings = Depends(get_settings)):
    if not sess.src:
        return {"ok": False, "error": "belum ada projek terbuka"}
    d = Path(sess.src)
    tdata = svc_tugas.baca_projek(d, settings.uploads_root)
    if not svc_tugas.boleh_kelola(tdata, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang boleh"}
    try:
        ok = await asyncio.to_thread(svc.buang, d, nomor)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": ok}


@router.get("/api/latih/rincian")
async def rincian(nomor: int = 0, sess: Session = Depends(current_session_api)):
    if not sess.src:
        return {"ok": False, "error": "belum ada projek terbuka"}
    d = Path(sess.src)
    s = await asyncio.to_thread(svc.status, d, nomor)
    if not s:
        return {"ok": False, "error": "training itu tidak ada"}
    csv = await asyncio.to_thread(svc.baca_hasil_csv, svc.dir_latih(d, nomor))
    # Rincian dataset yang dilatih: versi MANA + catatannya + pembagiannya.
    # Training membeku ke SATU versi (s["versi"]); rinciannya hidup di berkas
    # versi, bukan di entri training, jadi diambil di sini supaya panel bisa
    # menyebut "dilatih dari versi vN (<catatan>)" bukan cuma "vN". Versinya
    # bisa sudah DIHAPUS sesudah training — kalau tak terbaca, versi_meta None
    # dan panel mengatakannya apa adanya, bukan menyembunyikan.
    vm = await asyncio.to_thread(svc_versi.baca, d, int(s.get("versi") or 0))
    versi_meta = None
    if vm:
        versi_meta = {
            "nomor": vm.get("nomor"),
            "catatan": vm.get("catatan") or "",
            "dibuat": vm.get("dibuat") or "",
            "oleh": vm.get("oleh") or "",
            "n": vm.get("n") or 0,
            "jumlah": vm.get("jumlah") or {},      # {train,valid,test}
            "kelas": vm.get("kelas") or 0,
            "rasio": vm.get("rasio") or "",
            "berencana": bool(vm.get("berencana")),  # True = split anti-bocor
        }
    return {"ok": True, "latih": s, "versi_meta": versi_meta,
            "kurva": csv.get("kurva") or [],
            "evaluasi": await asyncio.to_thread(svc.hasil_evaluasi, d, nomor),
            "gambar": await asyncio.to_thread(svc.gambar_hasil, d, nomor),
            "log": await asyncio.to_thread(svc.ekor_log, d, nomor, 60)}


@router.post("/api/latih/evaluasi")
async def mulai_evaluasi(nomor: int = 0,
                         sess: Session = Depends(current_session_api),
                         settings: Settings = Depends(get_settings)):
    """Uji produksi satu model: akurasi, ketergantungan warna, kelas default."""
    if not sess.src:
        return {"ok": False, "error": "belum ada projek terbuka"}
    d = Path(sess.src)
    tdata = svc_tugas.baca_projek(d, settings.uploads_root)
    if not svc_tugas.boleh_kelola(tdata, sess.user):
        return {"ok": False, "error": "hanya pemilik projek yang boleh"}
    try:
        await asyncio.to_thread(svc.jalankan_evaluasi, d, nomor)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:                       # noqa: BLE001
        _log.exception("gagal meluncurkan evaluasi")
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True}


@router.get("/latih/bobot")
async def unduh_bobot(nomor: int = 0, jenis: str = "best",
                      sess: Session = Depends(current_session_api)):
    """Unduh best.pt atau last.pt satu training."""
    if not sess.src:
        return Response(status_code=404)
    if jenis not in ("best", "last"):
        return Response(status_code=400)
    p = svc.dir_latih(Path(sess.src), nomor) / "weights" / f"{jenis}.pt"
    if not p.exists():
        return Response(status_code=404)
    isi = await asyncio.to_thread(p.read_bytes)
    nama = f"L{nomor}-{jenis}.pt"
    return Response(isi, media_type="application/octet-stream",
                    headers={"Content-Disposition":
                             f'attachment; filename="{nama}"'})


@router.get("/latih/grafik")
async def grafik(nomor: int = 0, nama: str = "results.png",
                 sess: Session = Depends(current_session_api)):
    """Gambar yang digambar Ultralytics sendiri (kurva, confusion matrix)."""
    if not sess.src:
        return Response(status_code=404)
    # Nama berkas dibatasi ke satu segmen: tanpa ini, "../.." di parameternya
    # membuat rute ini bisa membaca berkas mana pun yang bisa dijangkau proses.
    if "/" in nama or "\\" in nama or nama.startswith("."):
        return Response(status_code=404)
    p = svc.dir_latih(Path(sess.src), nomor) / nama
    if not p.exists() or p.suffix.lower() not in (".png", ".jpg"):
        return Response(status_code=404)
    isi = await asyncio.to_thread(p.read_bytes)
    return Response(isi, media_type="image/png",
                    headers={"Cache-Control": "no-store"})
