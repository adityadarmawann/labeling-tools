"""
Auto-label batch. Dua mode lewat /api/autolabel/mulai:

  - ZERO-SHOT (lama, back-compat): body tanpa `sumber` -> model teks tunggal
    (YOLO-World/SAM 3), autolabel_zero.jalankan.
  - GABUNGAN (ensemble): body `sumber: [...]` -> beberapa sumber (model terlatih
    projek, model .pt diunggah, zero-shot) dijalankan lalu hasilnya digabung
    (autolabel_gabung.jalankan). Di mana satu sumber lemah, yang lain menutup.

Pola job: POST memulai lalu BALIK SEGERA, kemajuan dijajak lewat GET, pembatalan
lewat flag, satu job per akun, DITOLAK saat training lokal aktif. Kemajuan kedua
mode memakai saluran yang sama (autolabel_zero.kemajuan).
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request

from ..config import Settings, get_settings
from ..deps import bodi_json, current_session_api
from ..services import (ai_antre, autolabel, autolabel_gabung, autolabel_zero,
                        gpu_sinyal, prelabel, scanner, tugas)
from ..session import Session

router = APIRouter(tags=["autolabel"])

# Batas ukuran unggah model .pt: model ultralytics umumnya < 100 MB, yang besar
# s/d ratusan MB; 2 GB memberi kepala tanpa membuka pintu berkas raksasa.
MAKS_PT = 2_000_000_000

FITUR = "auto-label"
_tugas: dict[str, asyncio.Task] = {}
_batal: dict[str, bool] = {}


def _conf(v, bawaan: float) -> float:
    try:
        return min(max(float(v), 0.01), 0.99)
    except (TypeError, ValueError):
        return bawaan


def _parse_prompt(raw) -> list:
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    return []


@router.get("/api/autolabel/info")
async def info(sess: Session = Depends(current_session_api),
               settings: Settings = Depends(get_settings)):
    """Semua yang antarmuka butuh: model teks zero-shot (+ukuran unduh), run
    terlatih projek, model .pt diunggah, pilihan/prompt bawaan, apakah Claude
    aktif, dan apakah training sedang berjalan."""
    if sess.src is None:
        return {"ok": False, "error": "belum ada projek terbuka"}
    jenis = await asyncio.to_thread(scanner.jenis_projek, sess.src)
    prompt = await asyncio.to_thread(autolabel_zero.prompt_default, sess.src)
    terlatih = await asyncio.to_thread(prelabel.model_terlatih, sess.src)
    eksternal = await asyncio.to_thread(prelabel.model_eksternal, sess.src)
    inf = autolabel.info()
    return {"ok": True,
            "teks": inf.get("teks", []),
            "model_bawaan": autolabel_zero.pilih_model(jenis),
            "jenis": jenis,
            "prompt_bawaan": prompt,
            "terlatih": terlatih,
            "eksternal": eksternal,
            "groundingdino_siap": inf.get("groundingdino_siap", False),
            # `claude_aktif` DIBUANG, bukan dibiarkan False. Ia tak pernah
            # dibaca antarmuka mana pun, jadi satu-satunya gunanya adalah
            # membuat pembaca mengira ada jalur Claude yang bisa dinyalakan.
            # Kalau Fase B jadi dikerjakan, menambahkannya kembali sepele.
            "training_aktif": gpu_sinyal.latih_aktif()}


def _sibuk(fitur: str) -> dict:
    return {"ok": False, "error": f"masih ada {fitur} yang berjalan; tunggu "
                                  "sampai selesai atau batalkan dulu"}


def _spawn(akun: str, buat_hasil) -> bool:
    """Klaim antrean AI lalu jalankan di thread. False kalau akun itu sedang
    memegang pekerjaan AI lain (klaimnya atomik, jadi inilah penjaga yang
    sebenarnya; pemeriksaan di rute hanya untuk pesan yang lebih cepat)."""
    """Jalankan `buat_hasil()` (yang memanggil jalankan) di thread, petakan hasil/
    galat ke saluran kemajuan bersama. buat_hasil mengembalikan dict hasil."""
    if ai_antre.klaim(akun, FITUR):
        return False
    _batal[akun] = False

    def kerja():
        try:
            hasil = buat_hasil(lambda: _batal.get(akun, False))
            autolabel_zero.catat_maju(akun, jalan=False, selesai=True, **hasil)
        except autolabel_zero.Dibatalkan:
            autolabel_zero.catat_maju(akun, jalan=False, dibatalkan=True)
        except autolabel_zero.TrainingSedangJalan as e:
            autolabel_zero.catat_maju(akun, jalan=False, training=True,
                                      galat=str(e)[:200])
        except (ValueError, FileNotFoundError) as e:
            autolabel_zero.catat_maju(akun, jalan=False, galat=str(e)[:200])
        except Exception as e:                       # pragma: no cover
            autolabel_zero.catat_maju(akun, jalan=False, galat=str(e)[:200])
        finally:
            # Klaim dilepas APA PUN hasilnya: dibatalkan, galat, atau selesai.
            # Klaim yang tertinggal mengunci akunnya dari semua fitur AI.
            ai_antre.lepas(akun, FITUR)

    _tugas[akun] = asyncio.create_task(asyncio.to_thread(kerja))
    return True


@router.post("/api/autolabel/mulai")
async def mulai(request: Request,
                sess: Session = Depends(current_session_api),
                settings: Settings = Depends(get_settings)):
    if sess.src is None:
        return {"ok": False, "error": "belum ada projek terbuka"}
    if gpu_sinyal.latih_aktif():
        return {"ok": False, "training": True,
                "error": "training sedang berjalan di server ini; auto-label "
                         "dinonaktifkan sampai selesai. Anotasi manual tetap bisa."}
    body = await bodi_json(request)

    # Lingkup: BATCH ("batch", boleh "") atau JOB ("tid").
    if "batch" in body:
        batch = str(body.get("batch") or "")
        tid = ""
    else:
        batch = None
        tid = str(body.get("tid") or "").strip()
        if not tid:
            return {"ok": False, "error": "tugas tak disebut"}

    # Satu pekerjaan AI per akun, LINTAS FITUR. Dulu tiap router punya petanya
    # sendiri, jadi auto-label dan pre-label bisa jalan berbarengan di GPU yang
    # sama, dan pesan penolakannya pun menyebut fitur yang keliru.
    #
    # Di sini baru PEMERIKSAAN, bukan klaim: di bawah masih ada beberapa jalur
    # yang menolak permintaan karena isinya tak sah, dan klaim yang diambil
    # sebelum itu akan tertinggal di sana -- mengunci akunnya dari semua fitur
    # AI sampai proses dimatikan. Klaim sesungguhnya diambil tepat sebelum
    # pekerjaannya dijalankan, dan ia sendiri atomik.
    if (lain := ai_antre.sedang_jalan(sess.user)):
        return _sibuk(lain)

    jenis = await asyncio.to_thread(scanner.jenis_projek, sess.src)
    prompt_bawaan = await asyncio.to_thread(autolabel_zero.prompt_default, sess.src)
    try:
        batas = max(1, min(int(body.get("batas") or 1000), 100_000))
    except (TypeError, ValueError):
        batas = 1000
    ds, akun, uploads = sess.src, sess.user, settings.uploads_root

    # ---- Mode GABUNGAN (beberapa sumber) ---------------------------------
    sumber_raw = body.get("sumber")
    if isinstance(sumber_raw, list) and sumber_raw:
        sumber = []
        for sc in sumber_raw:
            if not isinstance(sc, dict):
                continue
            j = str(sc.get("jenis") or "")
            if j == "terlatih":
                try:
                    sumber.append({"jenis": "terlatih", "nomor": int(sc["nomor"]),
                                   "conf": _conf(sc.get("conf"), 0.25)})
                except (KeyError, TypeError, ValueError):
                    pass
            elif j == "eksternal":
                nm = str(sc.get("nama") or "").strip()
                if nm:
                    sumber.append({"jenis": "eksternal", "nama": nm,
                                   "conf": _conf(sc.get("conf"), 0.25)})
            elif j == "zeroshot":
                m = str(sc.get("model") or "").strip() or autolabel_zero.pilih_model(jenis)
                if m not in autolabel.MODEL_TEKS:
                    continue
                pr = _parse_prompt(sc.get("prompt")) or prompt_bawaan
                if not pr:
                    continue
                sumber.append({"jenis": "zeroshot", "model": m, "prompt": pr,
                               "conf": _conf(sc.get("conf"), 0.1)})
            elif j == "groundingdino" and autolabel.gdino_tersedia():
                pr = _parse_prompt(sc.get("prompt")) or prompt_bawaan
                if not pr:
                    continue
                sumber.append({"jenis": "groundingdino", "prompt": pr,
                               "conf": _conf(sc.get("conf"), 0.25)})
        if not sumber:
            return {"ok": False, "error": "tidak ada sumber yang sah dipilih"}
        iou = min(max(_conf(body.get("iou"), 0.6), 0.3), 0.95)

        if not _spawn(akun, lambda batal: autolabel_gabung.jalankan(
                ds, uploads, akun, batas, sumber, tid=tid or None, batch=batch,
                iou_ambang=iou, batal=batal)):
            return _sibuk(ai_antre.sedang_jalan(akun) or "pekerjaan AI")
        return {"ok": True, "sumber": len(sumber)}

    # ---- Mode ZERO-SHOT (lama, back-compat) ------------------------------
    model = str(body.get("model") or "").strip() or autolabel_zero.pilih_model(jenis)
    if model not in autolabel.MODEL_TEKS:
        return {"ok": False, "error": "model auto-label tidak dikenal"}
    prompt = _parse_prompt(body.get("prompt")) or prompt_bawaan
    if not prompt:
        return {"ok": False, "error": "projek belum punya kelas; ketik nama "
                                      "objek yang dicari dulu"}
    conf = _conf(body.get("conf"), 0.1)
    if not _spawn(akun, lambda batal: autolabel_zero.jalankan(
            ds, uploads, tid, akun, batas, conf, model, prompt, batal=batal,
            batch=batch)):
        return _sibuk(ai_antre.sedang_jalan(akun) or "pekerjaan AI")
    return {"ok": True, "model": model, "prompt": prompt}


@router.put("/api/autolabel/model-unggah")
async def model_unggah(request: Request, nama: str = "",
                       sess: Session = Depends(current_session_api),
                       settings: Settings = Depends(get_settings)):
    """Unggah satu model .pt eksternal ke projek (sumber auto-label). HANYA
    pemilik/Editor (boleh_unggah). CATATAN KEAMANAN: memuat .pt menjalankan
    unpickle -> unggah hanya model tepercaya milik tim."""
    if sess.src is None:
        return {"ok": False, "error": "belum ada projek terbuka"}
    data = await asyncio.to_thread(tugas.baca_projek, sess.src, settings.uploads_root)
    if not tugas.boleh_unggah(data, sess.user):
        return {"ok": False, "error": "hanya pemilik atau Editor yang boleh unggah model"}
    nama = str(nama or "").strip()
    if not nama:
        return {"ok": False, "error": "nama model kosong"}

    total = int(request.headers.get("content-length", 0) or 0)
    if total and total > MAKS_PT:
        return {"ok": False, "error": f"model terlalu besar (maks {MAKS_PT // 1_000_000} MB)"}

    dest = autolabel_gabung.pt_eksternal(sess.src, nama)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".pt.part")
    ditulis = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                ditulis += len(chunk)
                if ditulis > MAKS_PT:
                    raise ValueError("model terlalu besar")
                f.write(chunk)
        if ditulis == 0:
            raise ValueError("berkas kosong")
        tmp.replace(dest)
    except Exception as e:                           # noqa: BLE001
        tmp.unlink(missing_ok=True)
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "nama": dest.stem}


@router.get("/api/autolabel/kemajuan")
async def kemajuan(sess: Session = Depends(current_session_api)):
    return {"ok": True, "maju": autolabel_zero.kemajuan(sess.user)}


@router.post("/api/autolabel/batal")
async def batal(sess: Session = Depends(current_session_api)):
    _batal[sess.user] = True
    return {"ok": True}
