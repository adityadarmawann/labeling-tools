"""
Penugasan pelabelan: siapa mengerjakan gambar yang mana.

KENAPA ADA
----------
Selama satu orang mengerjakan satu projek sendirian, tidak ada yang perlu
dicatat. Begitu pekerjaannya dibagi, tiga pertanyaan muncul dan tidak satu pun
bisa dijawab sistem ini sebelumnya: siapa yang boleh membuka projek ini, siapa
yang mengerjakan gambar yang mana, dan gambar mana yang sudah dinyatakan
selesai sehingga pantas ikut ke dataset.

BENTUK PENYIMPANANNYA
---------------------
Satu berkas `.tugas.json` di akar projek, bersebelahan dengan `.tag.json`.
Alasannya sama: gambar yang belum dilabeli tidak punya berkas anotasi, dan
justru gambar itulah yang paling perlu ditugaskan.

PROJEK WARISAN
--------------
Projek yang belum pernah ditugaskan TIDAK punya berkas ini, dan itu bukan
keadaan yang perlu diperbaiki. Ia dibaca sebagai "milik pemilik foldernya,
tanpa tamu, dan seluruh isinya sudah di dataset" — persis kelakuan aplikasi
ini sebelum penugasan ada. Berkasnya baru lahir saat seseorang pertama kali
diundang atau ditugaskan.

Itu yang membuat fitur ini bisa dipasang tanpa memigrasi satu projek pun, dan
tanpa membuat ekspor projek lama mendadak kosong.

HAK
---
    pemilik projek   : melihat, melabeli APA PUN, mengelola projeknya
    pelabel bertugas : melihat semuanya, melabeli HANYA jatahnya
    anggota lain     : melihat semuanya, tidak boleh melabeli
    bukan anggota    : projeknya tidak muncul sama sekali

Melihat pekerjaan orang lain sengaja dibiarkan terbuka: itu satu-satunya cara
seorang pelabel tahu bagaimana kelas yang sama diberi bentuk oleh rekannya,
dan tanpa itu tiap orang mengarang gayanya sendiri.
"""
from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime
from pathlib import Path

from ..log import catat
from .tag import kunci_gambar

log = catat("labelapp.tugas")

BERKAS = ".tugas.json"
VERSI = 1

# Keadaan sebuah job, dan ketiganya jadi kolom di papan Anotasi.
BARU = "baru"            # sudah ditugaskan, belum ada yang dilabeli
JALAN = "jalan"          # sebagian sudah dilabeli
SELESAI = "selesai"      # seluruh gambarnya sudah masuk dataset

_kunci = threading.Lock()


def _p(ds: Path) -> Path:
    return Path(ds) / BERKAS


def kosong(pemilik: str = "") -> dict:
    return {"versi": VERSI, "pemilik": pemilik, "anggota": {},
            "tugas": {}, "dataset": [], "undangan": {},
            "kurasi": False, "warisan": True}


def baca(ds: Path, pemilik: str = "") -> dict:
    """
    Isi berkas tugas, selalu lengkap.

    `warisan` True berarti berkasnya belum pernah ada. Pemanggilnya memakai itu
    untuk memutuskan bahwa seluruh isi projek dianggap sudah di dataset.
    """
    p = _p(ds)
    if not p.is_file():
        return kosong(pemilik)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Berkas rusak tidak boleh mengunci seluruh tim di luar projeknya.
        # Dibaca sebagai warisan: semua kembali seperti sebelum ada penugasan.
        log.warning("berkas tugas rusak, dibaca sebagai warisan: %s", p)
        return kosong(pemilik)
    if not isinstance(d, dict):
        return kosong(pemilik)
    return {"versi": d.get("versi", VERSI),
            "pemilik": d.get("pemilik") or pemilik,
            "anggota": d.get("anggota") or {},
            "tugas": d.get("tugas") or {},
            "dataset": d.get("dataset") or [],
            "undangan": d.get("undangan") or {},
            # Kurasi dimulai saat orang pertama kali menekan "Tambahkan ke
            # dataset", BUKAN saat berkas ini lahir. Berkas ini lahir karena
            # banyak sebab — mengundang, membagi, menandai — dan hanya satu di
            # antaranya berarti "aku mulai memilih isi dataset".
            #
            # Daftar dataset yang berisi dibaca sebagai kurasi yang sudah
            # berjalan, sekalipun penandanya tidak ada. Hanya masukkan() yang
            # bisa mengisi daftar itu, jadi isinya adalah buktinya sendiri —
            # dan berkas yang ditulis sebelum penanda ini ada memang berisi
            # daftar tanpa penanda. Tanpa aturan ini, projek yang pemiliknya
            # sudah memilih 38 gambar tetap menampilkan seluruh 476-nya.
            "kurasi": bool(d.get("kurasi")) or bool(d.get("dataset")),
            "warisan": False}


def baca_projek(ds: Path, uploads_root: Path) -> dict:
    """
    Berkas tugas sebuah projek, dengan pemilik yang diambil dari LETAKNYA.

    Ini yang harus dipakai rute, bukan baca() langsung. baca() menerima
    pemilik sebagai nilai cadangan, dan rute yang mengoperkan akun pemanggil
    ke situ membuat siapa pun yang menyentuh folder lebih dulu jadi pemiliknya.
    """
    from .projek import pemilik_dari

    return baca(ds, pemilik_dari(uploads_root, ds))


def _tanpa_perubahan(data: dict, hasil: dict) -> dict:
    """
    Kembalikan hasil TANPA menulis berkasnya.

    Ini bukan penghematan I/O. Projek yang belum pernah ditugaskan tidak punya
    berkas ini sama sekali, dan itulah yang membuatnya dibaca sebagai warisan:
    semua boleh menyunting, semua terhitung masuk dataset. Menulis berkas
    kosong pada projek seperti itu — misalnya saat membubarkan job yang tidak
    ada — mengubahnya jadi projek berdataset KOSONG, dan ekspornya terjun ke
    nol tanpa satu pun tindakan yang benar-benar mengubah sesuatu.

    Karena itu setiap operasi tulis harus memastikan ada yang berubah lebih
    dulu, dan berhenti di sini kalau tidak.
    """
    return hasil


def _tulis(ds: Path, data: dict) -> None:
    simpan = {k: v for k, v in data.items() if k != "warisan"}
    p = _p(ds)
    tmp = p.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(simpan, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(p)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


# ============================================================
# HAK
# ============================================================

def boleh_lihat(data: dict, akun: str) -> bool:
    return akun == data["pemilik"] or akun in data["anggota"]


def boleh_kelola(data: dict, akun: str) -> bool:
    """Ganti nama, gabung, gandakan, buang, dan membagi tugas."""
    return akun == data["pemilik"]


def pelabel_gambar(data: dict, kunci: str) -> str:
    """Siapa yang ditugaskan pada satu gambar. Kosong berarti belum ada."""
    for t in data["tugas"].values():
        if kunci in (t.get("gambar") or []):
            return str(t.get("pelabel") or "")
    return ""


def boleh_labeli(data: dict, akun: str, kunci: str) -> bool:
    """
    Siapa yang boleh MENYUNTING label satu gambar.

    Pemilik projek selalu boleh; ia yang bertanggung jawab atas isinya. Selain
    itu hanya pelabel yang ditugaskan pada gambar itu. Gambar yang belum
    ditugaskan ke siapa pun tetap milik pemiliknya sendiri.

    Anggota lain sengaja tidak boleh: mereka melihat pekerjaan satu sama lain
    sebagai rujukan, dan dua orang yang menyunting gambar yang sama tanpa
    saling tahu berakhir dengan yang terakhir menimpa yang pertama.
    """
    if akun == data["pemilik"]:
        return True
    # Projek yang belum diorganisasi sama sekali berperilaku seperti sebelum
    # penugasan ada: siapa pun yang bisa membukanya boleh menyuntingnya.
    if not data["tugas"] and not data["anggota"]:
        return True
    return pelabel_gambar(data, kunci) == akun


def tolak_tulis(ds: Path, akun: str, gambar: Path) -> str:
    """
    Penjaga satu pintu untuk SEMUA jalur yang mengubah label sebuah gambar.

    Dipanggil dari rute simpan, tandai latar, dan batalkan latar. Menaruhnya di
    satu tempat bukan kerapian: kalau tiap rute memeriksa sendiri, satu rute
    baru yang lupa memeriksa membuat seluruh aturannya tidak berlaku, dan
    tidak ada yang terlihat salah sampai ada yang menimpa pekerjaan orang lain.

    Kunci gambarnya memakai aturan yang sama dengan berkas tag, supaya satu
    gambar tidak punya dua nama di dua berkas pendamping.
    """
    return alasan_tolak(baca(ds), akun, kunci_gambar(ds, gambar))


def alasan_tolak(data: dict, akun: str, kunci: str) -> str:
    """Pesan yang bisa dibaca, atau "" kalau boleh."""
    if boleh_labeli(data, akun, kunci):
        return ""
    siapa = pelabel_gambar(data, kunci)
    if siapa:
        return (f"gambar ini ditugaskan ke {siapa}; hanya dia dan pemilik "
                f"projek yang bisa menyuntingnya")
    return ("gambar ini belum ditugaskan kepadamu; minta pemilik projek "
            "menugaskannya lebih dulu")


# ============================================================
# DATASET
# ============================================================

def di_dataset(data: dict, kunci: str) -> bool:
    """
    Apakah satu gambar terhitung masuk dataset.

    Selama kurasinya belum dimulai, SELURUH isi projek adalah datasetnya —
    persis kelakuan aplikasi ini sebelum penugasan ada. Yang mengubahnya cuma
    satu tindakan: seseorang menekan "Tambahkan ke dataset" untuk pertama
    kalinya. Sejak saat itu, yang di dataset hanya yang disebutkan.

    Sengaja TIDAK bergantung pada ada atau tidaknya berkas tugas. Berkas itu
    lahir karena mengundang, membagi, atau menandai; tidak satu pun berarti
    "aku mulai memilih isi dataset".
    """
    return (not data["kurasi"]) or kunci in data["dataset"]


def saring_dataset(items: list, ds: Path, uploads_root: Path) -> tuple[list, dict]:
    """
    Hanya gambar yang sudah dinyatakan masuk dataset, beserta angkanya.

    Inilah yang membedakan "sudah dilabeli" dari "sudah selesai". Melabeli
    dilakukan berkali-kali sambil ragu; menyatakan masuk dataset sekali dan
    berakibat, dan yang berakibat itu justru di sini: splitting, versi, dan
    ekspor semuanya bekerja pada hasil saringan ini.

    Projek yang kurasinya belum pernah dimulai mengembalikan seluruhnya apa
    adanya — itu satu-satunya kelakuan yang masuk akal untuk folder dataset
    bersama, yang dibuka langsung dari path server dan tidak punya alur
    unggah, tugas, maupun dataset sama sekali.

    Projek di ruang kerja tidak pernah tinggal di keadaan itu: kurasinya
    dinyalakan saat aplikasi menyala atau saat gambar pertama masuk, mana yang
    lebih dulu.
    """
    from .tag import kunci_gambar

    data = baca_projek(ds, uploads_root)
    if not data["kurasi"]:
        return list(items), {"n_semua": len(items), "n_dataset": len(items),
                             "warisan": True}
    dipakai = [it for it in items
               if di_dataset(data, kunci_gambar(ds, it["img"]))]
    return dipakai, {"n_semua": len(items), "n_dataset": len(dipakai),
                     "warisan": False}


def sudah_dimasukkan(data: dict, kunci: str) -> bool:
    """
    Apakah gambar ini DISEBUT dalam daftar dataset, apa adanya.

    Berbeda dari di_dataset, dan bedanya penting. di_dataset menjawab "apakah
    ia ikut diekspor" — selama kurasinya belum dimulai, jawabannya ya untuk
    semuanya. Yang ini menjawab "apakah seseorang sudah menekan Tambahkan ke
    dataset untuknya", dan itu yang menggerakkan kolom ketiga di papan serta
    cap di halaman rincian job.

    Memakai di_dataset di sana membuat setiap job langsung tampak selesai pada
    projek yang belum pernah dikurasi, padahal belum ada yang dikerjakan.
    """
    return kunci in data["dataset"]


def mulai_kurasi(ds: Path, pemilik: str = "") -> dict:
    """
    Nyatakan projek ini tunduk pada aturan dataset, tanpa memasukkan apa pun.

    Dipanggil dari setiap jalur yang menambah gambar, dan sekali untuk tiap
    projek lama saat aplikasi menyala. Yang dilakukannya cuma menyalakan
    penanda; daftar datasetnya dibiarkan apa adanya — biasanya kosong.

    Sengaja TIDAK membekukan gambar yang sudah dianotasi ke dalam dataset.
    Sempat begitu, dengan alasan menjaga ekspor projek lama tidak berubah, dan
    alasan itu salah: "sudah dianotasi" bukan "sudah dinyatakan masuk". Yang
    memutuskan sebuah gambar layak ikut ke dataset adalah orang, lewat
    Tambahkan ke dataset, dan sistem yang memutuskannya sendiri atas nama
    orang persis kebalikan dari yang diminta halaman ini.

    Yang membuatnya aman bukan pembekuan itu, melainkan tombol borongan di
    kolom "Belum ditugaskan": seluruh isi projek lama bisa dimasukkan dengan
    satu klik, dengan angkanya disebutkan lebih dulu, oleh pemiliknya.

    Idempoten: projek yang kurasinya sudah berjalan tidak disentuh sama sekali.
    """
    with _kunci:
        data = baca(ds, pemilik)
        if data["kurasi"]:
            return _tanpa_perubahan(data, {"dimulai": False,
                                           "total": len(data["dataset"])})
        data["pemilik"] = data["pemilik"] or pemilik
        data["kurasi"] = True
        _tulis(ds, data)
    log.info("kurasi dimulai di %s (%s gambar sudah di dataset)",
             Path(ds).name, len(data["dataset"]))
    return {"dimulai": True, "total": len(data["dataset"])}


def kurasi_projek_lama(uploads_root: Path) -> list[dict]:
    """
    Nyalakan kurasi pada setiap projek lama yang belum punya berkas tugas.

    Dijalankan sekali saat aplikasi menyala. Tanpa ini, projek yang tidak
    pernah diunggahi lagi tetap memakai aturan warisan selamanya: SELURUH
    isinya terhitung dataset, termasuk gambar yang belum dilabeli sama sekali,
    dan halaman Dataset dua projek bersebelahan berperilaku berbeda tanpa ada
    yang bisa menjelaskan kenapa.

    Hanya membuat berkas yang belum ada. Berkas yang sudah ada tidak pernah
    disentuh: isinya adalah keputusan yang sudah diambil orang.
    """
    akar = Path(uploads_root)
    if not akar.is_dir():
        return []
    hasil = []
    for ruang in sorted(p for p in akar.iterdir() if p.is_dir()):
        if ruang.name.startswith((".", "_")):
            continue
        for d in sorted(q for q in ruang.iterdir() if q.is_dir()):
            if d.name.startswith((".", "_")) or _p(d).is_file():
                continue
            try:
                mulai_kurasi(d, ruang.name)
            except OSError as e:
                log.warning("kurasi %s gagal: %s", d, e)
                continue
            hasil.append({"projek": f"{ruang.name}/{d.name}"})
    return hasil


def belum_ditugaskan_siap(data: dict, berlabel: set[str], semua: set[str],
                          batch: str = "",
                          batch_dari: dict[str, str] | None = None) -> list[str]:
    """
    Gambar yang sudah dianotasi tetapi tidak ditugaskan ke siapa pun.

    Dipakai tombol borongan di kolom "Belum ditugaskan". Aturannya dihitung di
    sini, bukan dikirim peramban sebagai daftar path: daftar yang datang dari
    luar bisa memuat gambar yang justru sedang dikerjakan orang lain, dan
    memasukkannya berarti menyatakan pekerjaan orang selesai tanpa ia tahu.
    """
    bd = batch_dari or {}
    ditugaskan = {k for t in data["tugas"].values() for k in (t.get("gambar") or [])}
    return sorted(k for k in semua
                  if k in berlabel and k not in ditugaskan
                  and not sudah_dimasukkan(data, k)
                  and (not batch or (bd.get(k) or "") == batch))


def masukkan(ds: Path, kunci_daftar: list[str], pemilik: str = "") -> dict:
    """Nyatakan sekumpulan gambar masuk dataset."""
    with _kunci:
        data = baca(ds, pemilik)
        ada = set(data["dataset"])
        baru = [k for k in kunci_daftar if k not in ada]
        if not baru and data["kurasi"]:
            return _tanpa_perubahan(data, {"ditambah": 0,
                                           "total": len(data["dataset"])})
        # Menyalakan kurasi adalah keputusan besar: sejak ini, gambar yang
        # tidak disebutkan TIDAK ikut diekspor. Karena itu hanya di sini.
        data["kurasi"] = True
        data["dataset"] = data["dataset"] + baru
        _tulis(ds, data)
    log.info("%s gambar masuk dataset di %s", len(baru), Path(ds).name)
    return {"ditambah": len(baru), "total": len(data["dataset"])}


def keluarkan(ds: Path, kunci_daftar: list[str], pemilik: str = "") -> dict:
    """Kembalikan gambar dari dataset ke daftar yang masih dikerjakan."""
    with _kunci:
        data = baca(ds, pemilik)
        if not data["kurasi"]:
            # Belum ada yang dimasukkan, jadi tidak ada yang bisa dikeluarkan.
            # Menjawab "berhasil, 0 dikeluarkan" membuat orang mengira
            # datasetnya kosong padahal seluruh isinya justru terhitung masuk.
            return _tanpa_perubahan(data, {"dikeluarkan": 0, "total": 0,
                                           "belum_dikurasi": True})
        buang = set(kunci_daftar)
        sebelum = len(data["dataset"])
        sisa = [k for k in data["dataset"] if k not in buang]
        if len(sisa) == sebelum:
            return _tanpa_perubahan(data, {"dikeluarkan": 0,
                                           "total": len(data["dataset"])})
        data["dataset"] = sisa
        _tulis(ds, data)
    return {"dikeluarkan": sebelum - len(sisa), "total": len(sisa)}


# ============================================================
# ANGGOTA DAN TUGAS
# ============================================================

def undang(ds: Path, pemilik: str, akun: str) -> dict:
    if not akun or akun == pemilik:
        return {"anggota": []}
    with _kunci:
        data = baca(ds, pemilik)
        data["pemilik"] = data["pemilik"] or pemilik
        if akun not in data["anggota"]:
            data["anggota"][akun] = {
                "peran": "pelabel",
                "sejak": datetime.now().strftime("%Y-%m-%d"),
            }
        _tulis(ds, data)
    log.info("%r diundang ke projek %s oleh %r", akun, Path(ds).name, pemilik)
    return {"anggota": sorted(data["anggota"])}


def undang_email(ds: Path, pemilik: str, email: str) -> dict:
    """
    Undangan untuk alamat surel, bukan akun.

    Dipakai saat orangnya belum punya akun di sini. Yang dibuat sebuah token
    rahasia; siapa pun yang membukanya sambil masuk sebagai akun mana pun akan
    bergabung ke projek ini. Karena itu ia sekali pakai dan panjang.

    Tokennya TIDAK memuat nama projek. Tautan yang menyebut nama projek sudah
    membocorkan isinya sebelum ada yang menerima undangannya.
    """
    email = " ".join(str(email or "").split())[:120].strip()
    if "@" not in email:
        raise ValueError("bukan alamat surel")
    token = secrets.token_urlsafe(18)
    with _kunci:
        data = baca(ds, pemilik)
        data["pemilik"] = data["pemilik"] or pemilik
        data["undangan"][token] = {
            "email": email, "oleh": pemilik,
            "dibuat": datetime.now().strftime("%Y-%m-%d %H:%M"), "dipakai": "",
        }
        _tulis(ds, data)
    log.info("undangan dibuat untuk %r di projek %s", email, Path(ds).name)
    return {"token": token, "email": email}


def pakai_undangan(ds: Path, token: str, akun: str) -> dict:
    """
    Terima undangan sebagai `akun`.

    Sekali pakai: token yang sudah dipakai ditolak, supaya tautan yang
    diteruskan ke orang lain tidak menambah anggota yang tidak diundang.
    """
    with _kunci:
        data = baca(ds)
        u = data["undangan"].get(token)
        if not u:
            return {"ok": False, "error": "undangan tidak dikenal"}
        if u.get("dipakai"):
            return {"ok": False, "error": f"undangan ini sudah dipakai "
                                          f"{u['dipakai']}"}
        if akun == data["pemilik"]:
            return {"ok": False, "error": "kamu pemilik projek ini"}
        u["dipakai"] = akun
        u["diterima"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        if akun not in data["anggota"]:
            data["anggota"][akun] = {
                "peran": "pelabel", "sejak": datetime.now().strftime("%Y-%m-%d"),
                "lewat": u.get("email", ""),
            }
        _tulis(ds, data)
    log.info("undangan diterima oleh %r di projek %s", akun, Path(ds).name)
    return {"ok": True, "pemilik": data["pemilik"], "nama": Path(ds).name}


def undangan_terbuka(data: dict) -> list[dict]:
    """Undangan yang belum dipakai, untuk ditampilkan di panel anggota."""
    return [{"token": t, **u} for t, u in data["undangan"].items()
            if not u.get("dipakai")]


def batalkan_undangan(ds: Path, pemilik: str, token: str) -> dict:
    with _kunci:
        data = baca(ds, pemilik)
        if token not in data["undangan"]:
            return _tanpa_perubahan(data, {"dibatalkan": False})
        data["undangan"].pop(token, None)
        _tulis(ds, data)
    return {"dibatalkan": True}


def keluarkan_anggota(ds: Path, pemilik: str, akun: str) -> dict:
    with _kunci:
        data = baca(ds, pemilik)
        punya_job = [t for t, v in data["tugas"].items()
                     if v.get("pelabel") == akun]
        if akun not in data["anggota"] and not punya_job:
            return _tanpa_perubahan(data, {"anggota": sorted(data["anggota"])})
        data["anggota"].pop(akun, None)
        # Tugasnya ikut dibubarkan: job tanpa pelabel yang masih berhak
        # menyunting adalah pekerjaan yang tidak bisa dilanjutkan siapa pun.
        for tid in punya_job:
            data["tugas"].pop(tid, None)
        _tulis(ds, data)
    return {"anggota": sorted(data["anggota"])}


def tugaskan(ds: Path, pemilik: str, pelabel: str, gambar: list[str],
             catatan: str = "", judul: str = "") -> dict:
    """
    Buat satu job: sekumpulan gambar untuk satu orang.

    Gambar yang sudah ditugaskan ke orang lain dilewati, tidak dipindahkan
    diam-diam. Memindahkan pekerjaan yang sedang berjalan tanpa memberi tahu
    keduanya adalah cara tercepat membuat dua orang mengerjakan hal yang sama.
    """
    if not pelabel:
        raise ValueError("pelabel kosong")
    with _kunci:
        data = baca(ds, pemilik)
        data["pemilik"] = data["pemilik"] or pemilik
        sudah = {k for t in data["tugas"].values() for k in (t.get("gambar") or [])}
        milik = [k for k in gambar if k not in sudah]
        if pelabel != data["pemilik"] and pelabel not in data["anggota"]:
            data["anggota"][pelabel] = {
                "peran": "pelabel",
                "sejak": datetime.now().strftime("%Y-%m-%d"),
            }
        tid = "t" + secrets.token_hex(4)
        data["tugas"][tid] = {
            "pelabel": pelabel,
            # Judulnya disimpan saat dibagi, bukan diturunkan tiap kali dibaca:
            # gambar bisa berpindah job, dan judul yang ikut berubah membuat
            # kartu yang sama tampak jadi kartu lain.
            "judul": " ".join((judul or "").split())[:80],
            "dibuat": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "oleh": pemilik,
            "catatan": (catatan or "").strip()[:400],
            "gambar": milik,
        }
        _tulis(ds, data)
    log.info("job %s: %s gambar untuk %r di %s (%s dilewati, sudah ditugaskan)",
             tid, len(milik), pelabel, Path(ds).name, len(gambar) - len(milik))
    return {"id": tid, "pelabel": pelabel, "n": len(milik),
            "dilewati": len(gambar) - len(milik)}


def ubah_job(ds: Path, pemilik: str, tid: str, *, pelabel: str | None = None,
             catatan: str | None = None, judul: str | None = None) -> dict:
    """
    Ubah satu job tanpa membubarkannya.

    Menugaskan ulang lebih baik daripada membubarkan lalu membagi lagi:
    membubarkan mengembalikan gambarnya ke kolom pertama, dan siapa pun bisa
    mengambilnya lebih dulu sebelum pembagian ulangnya sempat dikerjakan.
    """
    with _kunci:
        data = baca(ds, pemilik)
        job = data["tugas"].get(tid)
        if job is None:
            raise KeyError(tid)
        if pelabel:
            job["pelabel"] = pelabel
            if pelabel != data["pemilik"] and pelabel not in data["anggota"]:
                data["anggota"][pelabel] = {
                    "peran": "pelabel",
                    "sejak": datetime.now().strftime("%Y-%m-%d"),
                }
        if catatan is not None:
            job["catatan"] = " ".join(catatan.split())[:400]
        if judul is not None:
            job["judul"] = " ".join(judul.split())[:80]
        _tulis(ds, data)
    log.info("job %s diubah di %s oleh %r", tid, Path(ds).name, pemilik)
    return {"id": tid, "pelabel": job["pelabel"]}


def bubarkan(ds: Path, pemilik: str, tid: str) -> dict:
    with _kunci:
        data = baca(ds, pemilik)
        if tid not in data["tugas"]:
            return _tanpa_perubahan(data, {"dibubarkan": False})
        data["tugas"].pop(tid, None)
        _tulis(ds, data)
    return {"dibubarkan": True}


# ============================================================
# PAPAN
# ============================================================

URUT_PAPAN = {
    "terbaru": "Terbaru dibagi",
    "terlama": "Terlama dibagi",
    "maju": "Paling maju",
    "tertinggal": "Paling tertinggal",
    "terbanyak": "Gambar terbanyak",
    "pelabel": "Nama pelabel",
}


def papan(data: dict, berlabel: set[str], semua: set[str],
          batch_dari: dict[str, str] | None = None,
          urut: str = "terbaru") -> dict:
    """
    Bahan untuk papan Anotasi: tiga kolom.

    `berlabel` dan `semua` datang dari pemindai, bukan dihitung di sini. Berkas
    tugas menyimpan siapa mengerjakan apa; yang tahu sebuah gambar sudah punya
    objek atau belum cuma pemindainya.
    """
    bd = batch_dari or {}
    ditugaskan = set()
    kartu = []
    for tid, t in data["tugas"].items():
        g = [k for k in (t.get("gambar") or []) if k in semua]
        ditugaskan.update(g)
        n_label = sum(1 for k in g if k in berlabel)
        n_dataset = sum(1 for k in g if sudah_dimasukkan(data, k))
        keadaan = (SELESAI if g and n_dataset == len(g)
                   else JALAN if n_label else BARU)
        # Judul: yang disimpan saat dibagi, atau unggahan yang paling banyak
        # menyumbang isinya, atau tanggalnya. Dua job untuk orang yang sama
        # tanpa judul tampak kembar, dan menebak mana yang mana dari
        # tanggalnya saja lebih lambat daripada membacanya.
        judul = t.get("judul") or ""
        if not judul and g:
            asal: dict[str, int] = {}
            for k in g:
                b = bd.get(k)
                if b:
                    asal[b] = asal.get(b, 0) + 1
            if asal:
                judul = max(asal.items(), key=lambda x: x[1])[0]
        kartu.append({
            "id": tid, "pelabel": t.get("pelabel", ""),
            "judul": judul or f"Dibagi {t.get('dibuat', '')[:10]}",
            "dibuat": t.get("dibuat", ""), "catatan": t.get("catatan", ""),
            "jumlah": len(g), "berlabel": n_label,
            "di_dataset": n_dataset, "keadaan": keadaan,
            "persen": round(n_label * 100 / len(g)) if g else 0,
        })
    # Urutan kartu. "terbaru" bawaannya, karena pekerjaan yang baru dibagi
    # itulah yang paling sering dicari sesudah membaginya.
    URUT = {
        "terbaru": (lambda k: k["dibuat"], True),
        "terlama": (lambda k: k["dibuat"], False),
        "maju": (lambda k: k["persen"], True),
        "tertinggal": (lambda k: k["persen"], False),
        "pelabel": (lambda k: k["pelabel"].lower(), False),
        "terbanyak": (lambda k: k["jumlah"], True),
    }
    kunci_urut, turun = URUT.get(urut, URUT["terbaru"])
    kartu.sort(key=kunci_urut, reverse=turun)
    belum = sorted(semua - ditugaskan)

    # Yang belum ditugaskan dikelompokkan per UNGGAHAN, bukan disebut sebagai
    # satu angka gabungan. Satu angka 378 tidak memberi tahu apa pun tentang
    # asalnya: satu unggahan besar dan lima unggahan kecil terlihat sama, dan
    # keputusan membaginya justru hampir selalu per unggahan.
    kelompok: dict[str, int] = {}
    # Berapa di antaranya yang SUDAH dianotasi tetapi belum dimasukkan. Gambar
    # seperti itu pekerjaannya sudah selesai tetapi tidak pernah lewat job,
    # jadi tidak ada halaman job yang bisa memasukkannya borongan — dan satu
    # per satu pun tidak bisa, karena halaman Dataset justru belum memuatnya.
    #
    # Syaratnya harus sama persis dengan belum_ditugaskan_siap(). Tombol yang
    # menghitung dengan aturan sendiri akan menawarkan "masukkan 2" lalu
    # rutenya menjawab tidak ada apa-apa untuk dimasukkan.
    siap_kelompok: dict[str, int] = {}
    for k in belum:
        b = bd.get(k) or ""
        kelompok[b] = kelompok.get(b, 0) + 1
        if k in berlabel and not sudah_dimasukkan(data, k):
            siap_kelompok[b] = siap_kelompok.get(b, 0) + 1
    belum_batch = sorted(
        ({"batch": nama, "n": n, "siap": siap_kelompok.get(nama, 0)}
         for nama, n in kelompok.items()),
        key=lambda x: (x["batch"] == "", -x["n"], x["batch"]))

    # Ringkasan per orang, dan inilah yang paling sering ditanyakan: si aditya
    # sudah berapa persen. Dijumlahkan dari kartunya, bukan dihitung ulang,
    # supaya angka di ringkasan dan angka di kartu tidak pernah berbeda.
    orang: dict[str, dict] = {}
    for k in kartu:
        o = orang.setdefault(k["pelabel"], {"pelabel": k["pelabel"], "job": 0,
                                            "jumlah": 0, "berlabel": 0,
                                            "di_dataset": 0})
        o["job"] += 1
        for f in ("jumlah", "berlabel", "di_dataset"):
            o[f] += k[f]
    for o in orang.values():
        o["persen"] = round(o["berlabel"] * 100 / o["jumlah"]) if o["jumlah"] else 0
    per_pelabel = sorted(orang.values(),
                         key=lambda o: (-o["jumlah"], o["pelabel"]))

    # Anggota yang sudah diterima tetapi belum kebagian satu gambar pun.
    # Mereka tidak muncul di mana-mana sebelumnya: ringkasan orang dirakit
    # dari kartu tugas, dan orang tanpa tugas tidak punya kartu. Akibatnya
    # pemilik projek mengundang seseorang, mengira itu sudah memberinya
    # pekerjaan, dan yang diundang membuka papan yang kosong tanpa satu pun
    # keterangan kenapa.
    punya_tugas = {k["pelabel"] for k in kartu}
    tanpa_tugas = sorted(a for a in data["anggota"] if a not in punya_tugas)

    return {
        "urut": urut if urut in URUT_PAPAN else "terbaru",
        "belum_ditugaskan": len(belum),
        "belum_batch": belum_batch,
        "belum_siap": sum(siap_kelompok.values()),
        "kartu": kartu,
        "per_pelabel": per_pelabel,
        "tanpa_tugas": tanpa_tugas,
        "n_dataset": sum(1 for k in semua if di_dataset(data, k)),
        "n_semua": len(semua),
        "n_berlabel": len(berlabel & semua),
    }
