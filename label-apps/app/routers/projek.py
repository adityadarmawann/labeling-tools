"""
Halaman projek: daftar berkartu beserta operasi atas datasetnya.

RUANG KERJANYA DIBATASI DI SINI, SEKALI
---------------------------------------
Setiap rute mengambil rootnya dari `_ruang(sess, settings)`, yang selalu
`uploads_root/<akun>`. Tidak ada rute yang menerima path bebas dari pengguna.

Itu disengaja: folder dataset bersama (`datasets_root`) memuat pekerjaan orang
lain dan sebagian milik proyek lain di mesin yang sama. Boleh dibuka dan
dibaca dari halaman pilih, tapi tidak boleh diganti nama, digandakan,
digabungkan, atau dibuang dari sini.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import cv2
from fastapi import APIRouter, Depends
from fastapi.responses import Response

from ..config import IMG_EXT, Settings, get_settings
from ..deps import current_session, current_session_api
from ..session import Session
from ..services import projek

router = APIRouter()

SAMPUL_SISI = 320


def _ruang(sess: Session, settings: Settings) -> Path:
    d = Path(settings.uploads_root) / sess.user
    d.mkdir(parents=True, exist_ok=True)
    return d


def _jawab(fn, *a, **k):
    """Jalankan operasi projek, ubah penolakannya jadi pesan yang bisa dibaca."""
    try:
        return {"ok": True, **fn(*a, **k)}
    except projek.Tolak as e:
        return {"ok": False, "error": str(e)}
    except OSError as e:
        return {"ok": False, "error": f"gagal menyentuh berkas: {str(e)[:90]}"}


def _segarkan_sesi(sess: Session, lama: Path) -> bool:
    """
    Kalau dataset yang SEDANG dibuka barusan dipindah atau diganti nama,
    sesinya menunjuk folder yang tidak ada lagi.

    Dibiarkan, halaman grid tetap menampilkan daftar gambar dari ingatan dan
    setiap kali membuka gambarnya baru gagal — satu per satu, tanpa
    menjelaskan sebabnya.
    """
    if sess.src and (sess.src == lama or not sess.src.exists()):
        sess.src = None
        sess.items = []
        sess.rencana_split = None
        return True
    return False


@router.get("/api/projek/daftar")
async def daftar(sess: Session = Depends(current_session_api),
                 settings: Settings = Depends(get_settings)):
    root = _ruang(sess, settings)
    kartu = await asyncio.to_thread(projek.daftar, root)
    sampah = await asyncio.to_thread(projek.isi_sampah, root)
    # Projek orang lain yang mengundang akun ini. Tanpa ini, orang yang
    # diundang jadi pelabel mendarat di halaman projek yang berbunyi "Belum
    # ada projek" — dan satu-satunya jalan masuk adalah URL yang harus
    # dikirimkan orang lain kepadanya.
    tamu = await asyncio.to_thread(projek.punya_tamu, settings.uploads_root,
                                   sess.user)
    kini = str(sess.src) if sess.src else ""
    for k in kartu + tamu:
        k["dibuka"] = k["path"] == kini
    return {"ok": True, "projek": kartu, "tamu": tamu, "sampah": sampah,
            "ruang": str(root)}


@router.get("/api/projek/sampul")
async def sampul(path: str = "", sess: Session = Depends(current_session),
                 settings: Settings = Depends(get_settings)):
    """
    Gambar sampul satu kartu.

    Pathnya diperiksa boleh dibuka akun ini, bukan dipercaya: tanpa itu, rute
    ini jadi jalan untuk membaca berkas mana pun di server yang bisa dijadikan
    JPEG.

    Aturannya sama dengan aturan membuka projeknya — bukan "di ruang kerja
    sendiri". Projek yang mengundang akun ini ada di ruang kerja orang lain,
    dan penjagaan yang lebih sempit dari haknya membuat kartunya muncul di
    halaman projek dengan sampul yang selalu gagal dimuat.
    """
    p = Path(path or "")
    if not p.is_file() or p.suffix.lower() not in IMG_EXT:
        return Response(status_code=404)
    if projek.boleh_buka(p.parent, sess.user, settings.uploads_root,
                         settings.datasets_root):
        return Response(status_code=404)

    def kotak_objek(h: int, w: int) -> tuple[int, int, int, int] | None:
        """
        Kotak yang memuat SATU objek terbesar di gambar itu, dengan bantalan.

        Tanpa pemotongan sama sekali, mengecilkan seluruh bingkai lalu
        memangkas dari tengah (object-fit: cover) menghasilkan sampul berisi
        MEJA: foto produk selalu punya benda kecil di tengah bingkai besar,
        dan yang tersisa setelah dikecilkan jadi 64 piksel cuma mejanya.

        Yang dipakai satu objek terbesar, BUKAN gabungan seluruh objek.
        Terukur pada paragon: sampulnya memuat dua objek yang berjauhan,
        gabungannya membentang y 276..2766 dari tinggi 4080 — yaitu nyaris
        seluruh gambar, jadi memotongnya tidak mengubah apa pun.

        Nisbahnya juga tidak dipaksa bujur sangkar; membujursangkarkan objek
        355x797 menghasilkan potongan 1354x1354 dengan 500 piksel meja di
        kiri dan kanan. Pemangkasan terakhir diserahkan ke object-fit: cover.
        """
        ann = projek.anotasi_untuk(p)
        if ann is None:
            return None
        kotak: list[tuple[float, float, float, float]] = []
        try:
            if ann.suffix.lower() == ".json":
                for b in (json.loads(ann.read_text(encoding="utf-8"))
                          .get("shapes") or []):
                    t = [q for q in (b.get("points") or [])
                         if isinstance(q, (list, tuple)) and len(q) >= 2]
                    if t:
                        xs = [float(q[0]) for q in t]
                        ys = [float(q[1]) for q in t]
                        kotak.append((min(xs), min(ys), max(xs), max(ys)))
            else:
                # YOLO: nilainya ternormalkan 0..1. Baris bbox berisi
                # `kelas cx cy w h`; baris segmentasi berisi pasangan titik.
                for baris in ann.read_text(encoding="utf-8").splitlines():
                    a = baris.split()
                    if len(a) == 5:
                        cx, cy, bw, bh = (float(v) for v in a[1:5])
                        kotak.append(((cx - bw / 2) * w, (cy - bh / 2) * h,
                                      (cx + bw / 2) * w, (cy + bh / 2) * h))
                    elif len(a) >= 7 and len(a) % 2 == 1:
                        n = [float(v) for v in a[1:]]
                        xs = [v * w for v in n[0::2]]
                        ys = [v * h for v in n[1::2]]
                        kotak.append((min(xs), min(ys), max(xs), max(ys)))
        except (OSError, ValueError, TypeError):
            return None
        if not kotak:
            return None

        x0, y0, x1, y1 = max(kotak, key=lambda k: (k[2] - k[0]) * (k[3] - k[1]))
        x0, x1 = max(0.0, x0), min(float(w), x1)
        y0, y1 = max(0.0, y0), min(float(h), y1)
        if x1 - x0 < 4 or y1 - y0 < 4:
            return None
        # Bantalan seperlima sisi terpanjang: cukup untuk memberi ruang
        # bernapas tanpa mengundang mejanya kembali masuk.
        pad = 0.2 * max(x1 - x0, y1 - y0)
        return (max(0, int(x0 - pad)), max(0, int(y0 - pad)),
                min(w, int(x1 + pad)), min(h, int(y1 + pad)))

    def kecilkan() -> bytes | None:
        im = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if im is None:
            return None
        h, w = im.shape[:2]
        kotak = kotak_objek(h, w)
        if kotak:
            x0, y0, x1, y1 = kotak
            if x1 - x0 > 8 and y1 - y0 > 8:
                im = im[y0:y1, x0:x1]
                h, w = im.shape[:2]
        sisi = max(h, w) or 1
        k = min(1.0, SAMPUL_SISI / sisi)
        if k < 1.0:
            im = cv2.resize(im, (max(1, int(w * k)), max(1, int(h * k))),
                            interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", im, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
        return buf.tobytes() if ok else None

    data = await asyncio.to_thread(kecilkan)
    if not data:
        return Response(status_code=404)
    return Response(data, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=300"})


@router.post("/api/projek/baru")
async def baru(nama: str = "", sess: Session = Depends(current_session_api),
               settings: Settings = Depends(get_settings)):
    """Projek kosong. Gambarnya diunggah belakangan, di halaman projek itu."""
    root = _ruang(sess, settings)
    return await asyncio.to_thread(_jawab, projek.buat, root, nama)


@router.post("/api/projek/ganti-nama")
async def ganti_nama(nama: str = "", baru: str = "",
                     sess: Session = Depends(current_session_api),
                     settings: Settings = Depends(get_settings)):
    root = _ruang(sess, settings)
    lama = root / projek.bersihkan_nama(nama)
    r = await asyncio.to_thread(_jawab, projek.ganti_nama, root, nama, baru)
    if r.get("ok"):
        r["sesi_ditutup"] = _segarkan_sesi(sess, lama)
    return r


@router.post("/api/projek/duplikat")
async def duplikat(nama: str = "", baru: str = "",
                   sess: Session = Depends(current_session_api),
                   settings: Settings = Depends(get_settings)):
    """Menggandakan berarti menyalin seluruh berkasnya; bisa memakan menit."""
    root = _ruang(sess, settings)
    projek.bersihkan_maju(sess.user)
    sess.projek_batal = False
    r = await asyncio.to_thread(_jawab, projek.duplikat, root, nama, baru,
                                kunci=sess.user,
                                batal=lambda: sess.projek_batal)
    projek.bersihkan_maju(sess.user)
    return r


@router.get("/api/projek/kemajuan")
async def kemajuan(sess: Session = Depends(current_session_api)):
    """Ditanya berkala selagi duplikat masih menggantung."""
    return {"ok": True, **projek.kemajuan(sess.user)}


@router.post("/api/projek/batal")
async def batal(sess: Session = Depends(current_session_api)):
    sess.projek_batal = True
    return {"ok": True}


@router.post("/api/projek/sampah")
async def ke_sampah(nama: str = "",
                    sess: Session = Depends(current_session_api),
                    settings: Settings = Depends(get_settings)):
    root = _ruang(sess, settings)
    lama = root / projek.bersihkan_nama(nama)
    r = await asyncio.to_thread(_jawab, projek.ke_sampah, root, nama)
    if r.get("ok"):
        r["sesi_ditutup"] = _segarkan_sesi(sess, lama)
    return r


@router.post("/api/projek/pulihkan")
async def pulihkan(folder: str = "",
                   sess: Session = Depends(current_session_api),
                   settings: Settings = Depends(get_settings)):
    root = _ruang(sess, settings)
    return await asyncio.to_thread(_jawab, projek.pulihkan, root, folder)


@router.post("/api/projek/gabung")
async def gabung(sumber: str = "", tujuan: str = "",
                 sess: Session = Depends(current_session_api),
                 settings: Settings = Depends(get_settings)):
    """
    Isi `sumber` disalin ke `tujuan`. Sumbernya tidak dihapus.

    Kemajuannya dilaporkan lewat rute impor yang sudah ada
    (`/api/impor/kemajuan`), karena mesin penyalinnya memang sama.
    """
    root = _ruang(sess, settings)
    # Projek tujuan dinyatakan tunduk pada aturan dataset lebih dulu. Gambar
    # yang datang dari projek lain tetap gambar baru bagi projek ini: ia belum
    # ditugaskan, belum diperiksa, dan belum dinyatakan masuk dataset.
    from ..services import tugas as svc_tugas
    d_tujuan = root / projek.bersihkan_nama(tujuan)
    if d_tujuan.is_dir():
        await asyncio.to_thread(svc_tugas.mulai_kurasi, d_tujuan,
                                projek.pemilik_dari(settings.uploads_root, d_tujuan))
    r = await asyncio.to_thread(_jawab, projek.gabung, root, sumber, tujuan,
                                kunci=sess.user)
    if r.get("ok") and sess.src and sess.src.name == projek.bersihkan_nama(tujuan):
        # Dataset tujuan sedang dibuka: daftarnya di ingatan sudah usang.
        await asyncio.to_thread(sess.reload)
        r["dipindai_ulang"] = True
    return r
