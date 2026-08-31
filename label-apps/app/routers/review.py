"""Papan periksa: grid, tampilan besar, thumbnail, tandai latar."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import Settings, get_settings
from ..deps import current_session, current_session_api, is_local, require_local
from ..security import safe_slug
from ..services import annotations, anylabeling, render, scanner, tambah
from ..services.annotations import Menolak
from ..session import Session
from ..templating import templates
from .datasets import picker_context

router = APIRouter(tags=["review"])

THUMB_MIN, THUMB_MAX = 32, 2000


# Pilihan urutan. Kunci dipakai di URL, nilainya yang tampil di menu.
URUT = {
    "nama": "Nama berkas (A→Z)",
    "nama-turun": "Nama berkas (Z→A)",
    "label-baru": "Terbaru dilabeli",
    "label-lama": "Terlama dilabeli",
    "gambar-baru": "Gambar terbaru ditambahkan",
    "gambar-lama": "Gambar terlama ditambahkan",
    "objek-banyak": "Objek terbanyak",
    "objek-sedikit": "Objek tersedikit",
}
URUT_BAWAAN = "nama"

# Keadaan "tanpa kelas" yang bisa ikut dicentang di dropdown kelas. Keduanya
# sama-sama gambar tanpa objek, tetapi artinya berlawanan: `latar` sudah selesai
# diperiksa dan sengaja dikosongkan (padanan Mark Null di Roboflow), `unlab`
# justru pekerjaan yang belum dikerjakan.
TANPA_KELAS = {
    "latar": ("Latar (tanpa objek)", "bg"),
    "unlab": ("Belum dilabeli", "stop"),
}


def _urutkan(items: list[dict], urut: str) -> list[dict]:
    """
    Urutkan hasil saringan.

    Waktu label dibaca dari disk di sini, bukan diambil dari hasil pindai:
    orang melabeli beberapa gambar lalu kembali ke grid untuk melihat hasilnya,
    dan nilai yang dibekukan saat memindai tidak akan berubah sampai dipindai
    ulang — persis membuat "Terbaru dilabeli" tidak berguna.

    Nama berkas selalu jadi kunci kedua supaya urutannya tetap sama di antara
    gambar yang nilainya seri; tanpa itu, grid bisa berubah urutan sendiri tiap
    kali dimuat ulang.
    """
    nama = lambda it: it["img"].name.lower()          # noqa: E731
    if urut == "nama-turun":
        return sorted(items, key=nama, reverse=True)
    if urut in ("label-baru", "label-lama"):
        w = {id(it): scanner.waktu_label(it) for it in items}
        return sorted(items, key=lambda it: (-w[id(it)], nama(it))) if urut == "label-baru" \
            else sorted(items, key=lambda it: (w[id(it)], nama(it)))
    if urut in ("gambar-baru", "gambar-lama"):
        def mtime(it):
            try:
                return it["img"].stat().st_mtime
            except OSError:
                return 0.0
        w = {id(it): mtime(it) for it in items}
        return sorted(items, key=lambda it: (-w[id(it)], nama(it))) if urut == "gambar-baru" \
            else sorted(items, key=lambda it: (w[id(it)], nama(it)))
    if urut == "objek-banyak":
        return sorted(items, key=lambda it: (-len(it["shapes"]), nama(it)))
    if urut == "objek-sedikit":
        return sorted(items, key=lambda it: (len(it["shapes"]), nama(it)))
    return sorted(items, key=nama)


def _diruang(sess, settings) -> bool:
    """Apakah dataset yang terbuka berada di ruang kerja akun ini."""
    try:
        (sess.src.resolve()
         .relative_to((Path(settings.uploads_root) / sess.user).resolve()))
        return True
    except (ValueError, OSError, AttributeError):
        return False


def _filter(items: list[dict], flt: str, kelas, tanpa=(), mode="atau") -> list[dict]:
    if flt == "issue":
        items = [i for i in items if i["issues"] and i["shapes"]]
    elif flt == "bg":
        # Gambar yang SENGAJA ditandai tanpa objek — sampel negatif. Ia berbeda
        # dari "belum dilabeli": yang ini sudah selesai diperiksa, dan porsinya
        # di dataset menentukan seberapa sering model salah menebak latar
        # sebagai objek. Tanpa saringan sendiri, ia tidak bisa dihitung maupun
        # ditinjau ulang.
        items = [i for i in items if scanner.severity(i) == "bg"]
    elif flt == "sudah":
        items = [i for i in items if i["shapes"]]
    elif flt == "unlab":
        # severity 'stop', bukan sekadar "tanpa objek": gambar yang sudah
        # ditandai latar memang tanpa objek tapi sudah selesai diperiksa, dan
        # angka di chip "Belum dilabeli" juga menghitung 'stop'. Kalau di sini
        # dipakai "tanpa objek", jumlah di chip tidak sama dengan isi grid.
        items = [i for i in items if scanner.severity(i) == "stop"]
    if kelas or tanpa:
        # Beberapa kelas sekaligus, dengan arti "punya SALAH SATU dari ini" —
        # sama seperti filter Classes di Roboflow. Arti "punya SEMUANYA" jarang
        # dibutuhkan dan mudah disalahpahami, jadi tidak dipakai.
        #
        # Keadaan tanpa-kelas ikut di dalam pilihan yang SAMA, bukan saringan
        # terpisah: "botol atau latar" adalah satu pertanyaan, dan memisahkannya
        # ke dua kotak membuat orang harus menebak apakah keduanya digabung
        # dengan DAN atau ATAU.
        pilih = set(kelas)
        sev_pilih = {TANPA_KELAS[t][1] for t in tanpa if t in TANPA_KELAS}

        def cocok(it):
            ada = {str(s["label"]) for s in it["shapes"]}
            sev = scanner.severity(it)
            if mode == "dan":
                # SEMUA kelas yang dicentang harus ada pada gambar yang sama.
                #
                # Latar dan Belum dilabeli tidak ikut di mode ini, dan tidak
                # ditawarkan di antarmukanya. Gambar berobjek menurut
                # definisinya bukan latar, jadi "punya botol DAN latar" selalu
                # nol — dan menawarkan pilihan yang pasti nol itu sendiri sudah
                # cacat, betapa pun benarnya angka nol itu.
                return pilih <= ada
            return bool(pilih & ada) or sev in sev_pilih

        items = [i for i in items if cocok(i)]
    return items


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, f: str = "all",
                # Alias dipakai supaya URL-nya tetap pendek (?s=&q=) tanpa nama
                # `s` dan `q` masuk ke ruang nama fungsi ini — di bawah ada
                # `for s in it["shapes"]` yang akan MENIMPA parameternya, dan
                # akibatnya bukan urutan yang salah melainkan TypeError saat ada
                # gambar berlabel.
                urut_q: str = Query(URUT_BAWAAN, alias="s"),
                cari_q: str = Query("", alias="q"),
                # Boleh muncul berkali-kali: ?c=botol&c=kaleng. Satu nilai tetap
                # bekerja seperti sebelumnya, jadi tautan lama tidak rusak.
                kelas_q: list[str] = Query([], alias="c"),
                # Keadaan tanpa-kelas, dipilih dari dropdown yang sama. Dipisah
                # dari `c` supaya tidak mungkin bentrok dengan kelas yang
                # kebetulan bernama "latar" atau "unlab".
                tanpa_q: list[str] = Query([], alias="x"),
                # Aturan penggabungan centang: "atau" (bawaan, seperti Roboflow)
                # atau "dan" (semuanya harus ada dalam satu gambar).
                mode_q: str = Query("atau", alias="m"),
                sess: Session = Depends(current_session),
                settings: Settings = Depends(get_settings)):
    # Belum memilih dataset -> tampilkan pemilih, bukan grid kosong.
    if sess.src is None:
        return templates.TemplateResponse(request, "pick.html",
                                          picker_context(request, sess, settings))

    with sess.lock:
        items = list(sess.items)

    kelas_hitung: dict[str, int] = {}
    for it in items:
        for s in it["shapes"]:
            k = str(s["label"])
            kelas_hitung[k] = kelas_hitung.get(k, 0) + 1

    sev = [scanner.severity(i) for i in items]

    mode = "dan" if mode_q == "dan" else "atau"
    urut = urut_q if urut_q in URUT else URUT_BAWAAN
    cari = (cari_q or "").strip()
    # Kelas yang tidak dikenal SENGAJA dipertahankan, bukan dibuang. Membuangnya
    # membuat saringan diam-diam tidak berlaku dan grid menampilkan semuanya —
    # terlihat seperti saringan yang bekerja padahal tidak. Dibiarkan apa adanya,
    # hasilnya nol, dan penunjuk "0 dari N gambar tampil" yang menjelaskannya.
    kelas = list(dict.fromkeys(k for k in kelas_q if k))
    tanpa = [t for t in dict.fromkeys(tanpa_q) if t in TANPA_KELAS]
    # Di mode "semuanya", keadaan tanpa-kelas dibuang seluruhnya — termasuk dari
    # daftar chip — supaya URL lama atau hasil suntingan tangan tidak
    # menghasilkan saringan yang tidak bisa dibuat lewat antarmukanya sendiri.
    if mode_q == "dan":
        tanpa = []
    tampil = _filter(items, f, kelas, tanpa, mode)
    if cari:
        pola = cari.lower()
        tampil = [it for it in tampil if pola in it["img"].name.lower()]
    tampil = _urutkan(tampil, urut)

    return templates.TemplateResponse(request, "index.html", {
        "sess": sess,
        "local": is_local(request),
        "items": tampil,
        "urut": urut,
        "urut_pilihan": URUT,
        "cari": cari,
        "n_tampil": len(tampil),
        "severity": scanner.severity,
        "flt": f,
        "kelas": kelas,
        "tanpa": tanpa,
        # Nama tampil dari seluruh yang tercentang, dihitung di sini supaya
        # templatnya tidak perlu menggabungkan dua daftar yang bentuknya beda.
        "pilihan_nama": [TANPA_KELAS[t][0] for t in tanpa] + list(kelas),
        "mode": mode,
        "tanpa_nama": {k: v[0] for k, v in TANPA_KELAS.items()},
        "total": len(items),
        # Empat keadaan yang saling lepas dan jumlahnya pas `total`; itu yang
        # dipakai bilah kemajuan. n_sudah SENGAJA tumpang tindih dengan n_warn
        # (chip "Sudah dilabeli" memang memuat yang perlu dicek), jadi ia tidak
        # bisa dipakai sebagai potongan bilah.
        "n_ok": sum(1 for s in sev if s == "ok"),
        "n_warn": sum(1 for s in sev if s == "warn"),
        "n_stop": sum(1 for s in sev if s == "stop"),
        "n_bg": sum(1 for s in sev if s == "bg"),
        "n_sudah": sum(1 for s in sev if s in ("ok", "warn")),
        "n_obj": sum(len(i["shapes"]) for i in items),
        "kelas_hitung": dict(sorted(kelas_hitung.items())),
        # Dataset yang dibuka langsung dari path server tidak boleh ditambahi,
        # dan alasannya ikut dikirim supaya tombolnya bisa menjelaskan diri
        # sendiri alih-alih hanya menghilang tanpa keterangan.
        # Nama projek untuk tautan ke halaman unggah. Kosong berarti dataset
        # ini bukan milik ruang kerja akun ini (dataset bersama, atau dibuka
        # langsung dari path server), dan halaman unggah tidak berlaku untuknya.
        "unggah_ds": (sess.src.name
                      if _diruang(sess, settings) else ""),
        "tolak_tambah": tambah.boleh_ditambahi(
            sess.src, settings.uploads_root / safe_slug(sess.user)),
        "bersplit": tambah.tata_letak(sess.src) == tambah.TATA_SPLIT,
    })


@router.get("/view", response_class=HTMLResponse)
async def view(request: Request, path: str = "",
               sess: Session = Depends(current_session)):
    it = sess.find(path)
    if not it:
        return templates.TemplateResponse(
            request, "notfound.html", {"sess": sess},
            status_code=404)
    with sess.lock:
        items = sess.items
        i = items.index(it)
        prev_it = items[i - 1] if i > 0 else None
        next_it = items[i + 1] if i < len(items) - 1 else None
        posisi = (i + 1, len(items))

    hitung: dict[str, int] = {}
    for s in it["shapes"]:
        k = str(s["label"])
        hitung[k] = hitung.get(k, 0) + 1

    return templates.TemplateResponse(request, "view.html", {
        "sess": sess, "local": is_local(request), "it": it,
        "sev": scanner.severity(it), "prev_it": prev_it, "next_it": next_it,
        "posisi": posisi, "hitung": dict(sorted(hitung.items())),
    })


@router.get("/thumb")
async def thumb(path: str = "", s: int = 320,
                sess: Session = Depends(current_session)):
    it = sess.find(path)
    if not it:
        return Response(status_code=404)
    side = min(max(s, THUMB_MIN), THUMB_MAX)
    tp = await asyncio.to_thread(render.thumb_path, sess, it, side)
    if not tp:
        return Response(status_code=404)
    return Response(tp.read_bytes(), media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=60"})


@router.post("/markbg")
async def mark_bg(path: str = "", sess: Session = Depends(current_session_api)):
    return _set_bg(sess, path, True)


@router.post("/unmarkbg")
async def unmark_bg(path: str = "", sess: Session = Depends(current_session_api)):
    return _set_bg(sess, path, False)


def _set_bg(sess: Session, path: str, on: bool):
    it = sess.find(path)
    if not it:
        return {"ok": False, "error": "berkas tidak dikenal di dataset ini"}
    try:
        with sess.lock:
            if on:
                annotations.mark_background(it)
                msg = "ditandai sebagai latar"
            else:
                annotations.unmark_background(it)
                msg = "tanda latar dilepas"
            sess.drop_thumbs_for(it)
    except Menolak as e:
        return {"ok": False, "error": str(e)}
    except OSError as e:
        return {"ok": False, "error": str(e)[:90]}
    return {"ok": True, "msg": msg}


@router.post("/open", dependencies=[Depends(require_local)])
async def open_in_anylabeling(path: str = "",
                              sess: Session = Depends(current_session_api),
                              settings: Settings = Depends(get_settings)):
    """Jalankan AnyLabeling di mesin server — hanya untuk akses lokal."""
    it = sess.find(path)
    if not it:
        return {"ok": False, "error": "berkas tidak dikenal di dataset ini"}
    try:
        anylabeling.launch(sess, it["img"])
    except FileNotFoundError:
        return {"ok": False,
                "error": f"perintah '{settings.anylabeling}' tidak ditemukan"}
    except OSError as e:
        return {"ok": False, "error": str(e)[:90]}
    msg = ("folder dibuka — pakai A / D untuk pindah gambar"
           if settings.open_mode == "dir" else it["img"].name)
    return {"ok": True, "msg": msg}
