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
import re
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
            "kurasi": False, "jenis_anotasi": "",
            # Template skeleton (keypoint/pose) tingkat projek. Kosong = projek
            # ini bukan projek keypoint. Lihat _sah_skeleton/set_skeleton.
            "skeleton": {"kelas": "", "titik": [], "edge": [],
                         "flip_idx": [], "warna": [], "tata": []},
            # Daftar kelas aksi tingkat projek (projek VIDEO sub-jenis aksi).
            # Kosong = projek ini belum/ bukan classifier aksi. Padanan
            # skeleton{} untuk klip; lihat _sah_aksi/set_aksi.
            "aksi": {"kelas": [], "merge": {}, "warna": [], "negatif": ""},
            "warisan": True}


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
            # "" = belum ditentukan, jadi ditebak dari bentuk yang ada.
            # Lihat set_jenis() untuk alasan kenapa ini setelan, bukan tebakan
            # saja.
            "jenis_anotasi": (d.get("jenis_anotasi") or "").strip().lower(),
            # Template skeleton disaring agar selalu konsisten (edge menunjuk
            # indeks titik yang ada, flip_idx panjangnya = jumlah titik, dst).
            "skeleton": _sah_skeleton(d.get("skeleton")),
            # Daftar kelas aksi disaring sama ketatnya (nama unik, target merge
            # ada di kelas, kelas negatif salah satu kelas yang ada).
            "aksi": _sah_aksi(d.get("aksi")),
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
    """Ganti nama, gabung, gandakan, buang, membagi tugas, DAN kelola anggota.

    Sengaja tetap PEMILIK saja, termasuk setelah peran Editor ada: Editor boleh
    mengunggah dan melabeli, tetapi mengelola projek dan anggotanya tetap hak
    pemilik. Satu pintu ini menjaga semua rute pengelolaan tetap owner-only
    tanpa perlu disentuh satu per satu.
    """
    return akun == data["pemilik"]


# ------------------------------------------------------------ peran & akses
#
# Model akses anggota (keputusan desain): PERAN + SCOPE adalah penentu utama
# siapa boleh apa. Papan "Bagi tugas" tetap ada sebagai alat pemilik, tetapi
# anggota sharing tidak wajib ditugaskan per-gambar lebih dulu untuk mulai
# melabeli — cukup perannya memberi hak itu.
#
#   Editor            : mengunggah media + melabeli SEMUA (tak mengelola)
#   Labeler menyeluruh: melabeli SEMUA (akses="semua"), tak mengunggah
#   Labeler spesifik  : melabeli hanya batch tertentu (akses="spesifik")
#
# Anggota LAMA (dibuat sebelum RBAC) tidak punya kolom `akses`. Mereka sengaja
# dibiarkan berperilaku seperti dulu: hak labelnya hanya gambar yang ditugaskan
# lewat papan. Jadi scope baru ini OPT-IN — hanya berlaku kalau pemilik memang
# menyetelnya lewat UI anggota.
PERAN_SAH = ("editor", "pelabel")
PERAN_BAWAAN = "pelabel"
AKSES_SAH = ("semua", "spesifik")
AKSES_BAWAAN = "semua"


def sah_peran(peran: str) -> str:
    p = str(peran or "").strip().lower()
    return p if p in PERAN_SAH else PERAN_BAWAAN


def sah_akses(akses: str) -> str:
    a = str(akses or "").strip().lower()
    return a if a in AKSES_SAH else AKSES_BAWAAN


def _bersih_batch(batch) -> list[str]:
    """Daftar nama batch yang rapi & unik, urut, untuk scope labeler spesifik."""
    if isinstance(batch, str):
        batch = batch.split(",")
    keluar, lihat = [], set()
    for b in batch or []:
        nama = " ".join(str(b or "").split())[:80]
        if nama and nama not in lihat:
            lihat.add(nama)
            keluar.append(nama)
    return sorted(keluar, key=str.lower)


def peran_anggota(data: dict, akun: str) -> str:
    """Peran seseorang di projek ini: 'pemilik', 'editor', 'pelabel', atau ''
    (bukan anggota). Pemilik selalu menang."""
    if akun == data["pemilik"]:
        return "pemilik"
    a = data["anggota"].get(akun)
    if not a:
        return ""
    return sah_peran(a.get("peran"))


def boleh_unggah(data: dict, akun: str) -> bool:
    """Siapa yang boleh menambah media ke projek: pemilik atau Editor.

    Labeler tidak pernah mengunggah — perannya melabeli yang sudah ada. Projek
    warisan (belum ada berkas tugas/anggota) tetap milik pemilik foldernya, dan
    hanya dia yang lolos di sini, sama seperti sebelum peran ada."""
    return akun == data["pemilik"] or peran_anggota(data, akun) == "editor"


def pelabel_gambar(data: dict, kunci: str) -> str:
    """Siapa yang ditugaskan pada satu gambar. Kosong berarti belum ada."""
    for t in data["tugas"].values():
        if kunci in (t.get("gambar") or []):
            return str(t.get("pelabel") or "")
    return ""


def boleh_labeli(data: dict, akun: str, kunci: str, batch: str = "") -> bool:
    """
    Siapa yang boleh MENYUNTING label satu gambar.

    Urutannya:
      - Pemilik & Editor: selalu boleh (bertanggung jawab / berhak penuh atas
        seluruh isi).
      - Labeler "menyeluruh" (akses="semua"): boleh semua gambar.
      - Labeler "spesifik" (akses="spesifik"): boleh kalau batch gambar ini ada
        di scope-nya — ATAU gambar ini memang ditugaskan ke dia lewat papan
        (penugasan eksplisit pemilik menang atas scope).
      - Anggota LAMA tanpa kolom `akses`: perilaku warisan — hanya gambar yang
        ditugaskan ke dia lewat papan.
      - Projek yang belum diorganisasi sama sekali: siapa pun yang bisa membuka
        boleh menyunting, persis seperti sebelum penugasan ada.

    `batch` = nama batch gambar ini (dari .tag.json), hanya dipakai untuk
    labeler spesifik. Pemanggil yang tidak menyediakannya (mis. pengecekan
    kasar) membuat scope spesifik jatuh ke penugasan papan saja.

    Anggota lain sengaja tidak boleh menimpa di luar haknya: dua orang yang
    menyunting gambar yang sama tanpa saling tahu berakhir dengan yang terakhir
    menimpa yang pertama.
    """
    if akun == data["pemilik"]:
        return True
    peran = peran_anggota(data, akun)
    if peran == "editor":
        return True
    # Projek yang belum diorganisasi sama sekali berperilaku seperti sebelum
    # penugasan ada: siapa pun yang bisa membukanya boleh menyuntingnya.
    if not data["tugas"] and not data["anggota"]:
        return True
    ditugaskan = pelabel_gambar(data, kunci) == akun
    if peran == "pelabel":
        akses = (data["anggota"].get(akun) or {}).get("akses")
        if akses == "semua":
            return True
        if akses == "spesifik":
            scope = (data["anggota"].get(akun) or {}).get("batch") or []
            return ditugaskan or (bool(batch) and batch in scope)
        # akses tak disetel (anggota lama): jatuh ke penugasan papan saja.
    return ditugaskan


def tolak_tulis(ds: Path, akun: str, gambar: Path) -> str:
    """
    Penjaga satu pintu untuk SEMUA jalur yang mengubah label sebuah gambar.

    Dipanggil dari rute simpan, tandai latar, dan batalkan latar. Menaruhnya di
    satu tempat bukan kerapian: kalau tiap rute memeriksa sendiri, satu rute
    baru yang lupa memeriksa membuat seluruh aturannya tidak berlaku, dan
    tidak ada yang terlihat salah sampai ada yang menimpa pekerjaan orang lain.

    Kunci gambarnya memakai aturan yang sama dengan berkas tag, supaya satu
    gambar tidak punya dua nama di dua berkas pendamping.

    Batch gambarnya ikut dibaca dari berkas tag dan diteruskan: labeler
    spesifik berhak atas batch yang masuk scope-nya, dan tanpa batch itu
    pengecekannya jatuh ke penugasan papan saja — menolak yang seharusnya boleh.
    """
    from . import tag as _tag

    kunci = kunci_gambar(ds, gambar)
    batch = _tag.untuk(_tag.baca(ds), kunci).get("batch", "")
    return alasan_tolak(baca(ds), akun, kunci, batch)


def alasan_tolak(data: dict, akun: str, kunci: str, batch: str = "") -> str:
    """Pesan yang bisa dibaca, atau "" kalau boleh."""
    if boleh_labeli(data, akun, kunci, batch):
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


def gambar_konteks_job(items: list[dict], data: dict, ds: Path, tid: str, *,
                       saring: str = "semua", dsf: str = "semua",
                       kelas=(), latar: bool = False) -> list[dict] | None:
    """Item gambar TERURUT (nama) untuk satu konteks job + filter — sama persis
    dengan yang ditampilkan halaman rincian job (routers/tugas.halaman_rincian).

    Dipakai kanvas supaya panah kiri/kanan hanya menyusuri gambar penugasan ini
    yang cocok filter yang dibuka (mis. "belum dianotasi"), bukan seluruh folder
    projek. `None` kalau tid tak dikenal — pemanggil lalu memakai daftar penuh.

    Aturan saringnya WAJIB tetap sama dengan router: kalau salah satu berubah,
    subset kanvas dan grid job jadi berbeda diam-diam. test_konteks_kanvas
    menjaganya tetap sinkron.
    """
    from . import scanner
    from . import tag as _tag
    job = (data.get("tugas") or {}).get(tid)
    if job is None:
        return None
    punya = set(job.get("gambar") or [])
    kpset = set(kelas)
    keluar = []
    for it in items:
        k = _tag.kunci_gambar(ds, it["img"])
        if k not in punya:
            continue
        sev = scanner.severity(it)
        berlabel = sev != "stop"           # latar TERMASUK sudah dianotasi
        if saring == "belum" and berlabel:
            continue
        if saring == "sudah" and not berlabel:
            continue
        if dsf == "di" and not sudah_dimasukkan(data, k):
            continue
        if dsf == "belum" and sudah_dimasukkan(data, k):
            continue
        if kpset or latar:
            kls = {str(s["label"]) for s in it["shapes"]}
            if not ((latar and sev == "bg") or (kpset & kls)):
                continue
        keluar.append(it)
    keluar.sort(key=lambda x: x["img"].name)
    return keluar


JENIS_ANOTASI = ("poligon", "kotak", "kerangka")

# Batas jumlah keypoint satu template — jauh di atas kebutuhan nyata (COCO
# orang = 17, wajah/tangan ~21, lapangan court ~33), cuma penjaga dari data
# rusak/ekstrem. Jumlah & nama keypoint SELALU ditentukan per projek, bukan
# tetap: tiap objek (botol, kaleng, lapangan, orang) punya kerangkanya sendiri.
MAKS_KEYPOINT = 200


def _sah_skeleton(raw) -> dict:
    """Template skeleton yang rapi & aman, dari data mentah .tugas.json.

    Tak pernah melempar: apa pun yang rusak disederhanakan, tak dibuang mentah.
    Dijaga konsisten sendiri — edge hanya menunjuk indeks titik yang ADA, dan
    flip_idx hanya sah kalau panjangnya persis = jumlah titik dengan nilai di
    dalam rentang; kalau tidak, flip_idx dikosongkan (artinya identitas / tanpa
    tukar kiri-kanan saat flip horizontal).

    Bentuknya: {kelas, titik:[nama...], edge:[[i,j]...], flip_idx:[...], warna:[...]}.
    """
    kosong = {"kelas": "", "titik": [], "edge": [], "flip_idx": [], "warna": []}
    if not isinstance(raw, dict):
        return kosong
    # Setiap bidang HARUS list; apa pun selain itu (string, angka, null)
    # diperlakukan kosong — iterasi string diam-diam akan memecah per huruf.
    def _list(x):
        return x if isinstance(x, list) else []
    titik, lihat = [], set()
    for t in _list(raw.get("titik"))[:MAKS_KEYPOINT]:
        nama = " ".join(str(t or "").split())[:40]
        # Nama keypoint harus unik: ia yang memetakan titik ke slot YOLO-pose.
        if nama and nama not in lihat:
            lihat.add(nama)
            titik.append(nama)
    K = len(titik)
    edge = []
    for e in _list(raw.get("edge")):
        try:
            i, j = int(e[0]), int(e[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 <= i < K and 0 <= j < K and i != j \
                and [i, j] not in edge and [j, i] not in edge:
            edge.append([i, j])
    try:
        flip = [int(x) for x in _list(raw.get("flip_idx"))]
    except (TypeError, ValueError):
        flip = []
    # flip_idx WAJIB involusi (flip[flip[i]]==i): pemetaan cermin yang bukan
    # involusi (mis. [1,1] atau siklus [1,2,0]) menukar identitas keypoint
    # dengan salah saat flip horizontal -> melatih kiri sebagai kanan tanpa
    # tanda. Yang tak memenuhi dikosongkan (identitas = tanpa tukar).
    if (len(flip) != K or any(not (0 <= x < K) for x in flip)
            or any(flip[flip[i]] != i for i in range(len(flip)))):
        flip = []
    # Warna per keypoint (opsional); dipotong/diisi agar sepanjang titik.
    warna = [str(c)[:9] for c in _list(raw.get("warna"))][:K]
    # Tata letak default (relatif 0..1) untuk "drop-whole": jatuhkan semua K
    # titik sekaligus, lalu labeler menyesuaikan. Hanya sah kalau lengkap K
    # pasangan [x,y] di [0,1]; kalau tidak, dikosongkan (nanti dipola otomatis).
    tata = []
    raw_tata = _list(raw.get("tata"))
    if len(raw_tata) == K:
        ok = True
        for t in raw_tata:
            try:
                x, y = float(t[0]), float(t[1])
            except (TypeError, ValueError, IndexError):
                ok = False
                break
            tata.append([min(1.0, max(0.0, x)), min(1.0, max(0.0, y))])
        if not ok:
            tata = []
    kelas = " ".join(str(raw.get("kelas") or "").split())[:80]
    return {"kelas": kelas, "titik": titik, "edge": edge,
            "flip_idx": flip, "warna": warna, "tata": tata}


def skeleton_aktif(data: dict) -> bool:
    """Projek ini projek keypoint: punya template skeleton berisi titik."""
    sk = data.get("skeleton") or {}
    return bool(sk.get("titik"))


def _relabel_anotasi(ds: Path, peta: dict[str, str]) -> int:
    """Ganti nama titik pada anotasi labelme (.json) yang SUDAH tersimpan.

    Frame yang masih ekspor YOLO (.txt) menyimpan keypoint secara POSISIONAL —
    namanya diberi template saat dibaca, jadi mengganti nama di template sudah
    cukup. Tapi frame yang pernah disunting disimpan sebagai .json dengan label
    titik tertulis apa adanya; tanpa ini, ganti-nama slot membuat label lama di
    situ tak lagi cocok template dan rangkanya putus di frame-frame itu.

    `peta` = {nama_lama: nama_baru}. Hanya bentuk `point` yang labelnya ada di
    peta yang disentuh (kotak kelas "court" dkk tak tersentuh). Mengembalikan
    jumlah berkas yang berubah.
    """
    from . import scanner
    from .annotations import tulis_aman

    n = 0
    for jp in scanner.anotasi_json(ds):
        try:
            d = json.loads(jp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or not isinstance(d.get("shapes"), list):
            continue
        ubah = False
        for s in d["shapes"]:
            if (isinstance(s, dict) and s.get("shape_type") == "point"
                    and s.get("label") in peta):
                s["label"] = peta[s["label"]]
                ubah = True
        if ubah:
            try:
                tulis_aman(jp, json.dumps(d, ensure_ascii=False))
                n += 1
            except OSError as e:                      # noqa: BLE001
                log.warning("re-label %s gagal: %s", jp.name, e)
    if n:
        log.info("re-label %d berkas anotasi di %s mengikuti ganti nama slot",
                 n, Path(ds).name)
    return n


def set_skeleton(ds: Path, template, pemilik: str = "") -> dict:
    """
    Tetapkan template skeleton projek: nama keypoint, edge, flip_idx (per projek).

    Jumlah & posisi keypoint berbeda tiap projek — inilah tempat owner
    mendefinisikannya sekali, lalu dipakai ulang untuk tiap objek di tiap
    gambar. Template disaring lewat _sah_skeleton sebelum disimpan.
    """
    bersih = _sah_skeleton(template)
    with _kunci:
        data = baca(ds, pemilik)
        # Nama slot LAMA, untuk mendeteksi ganti-nama (K sama, nama beda) supaya
        # anotasi .json yang sudah ada ikut diganti namanya — lihat _relabel.
        lama = list((data.get("skeleton") or {}).get("titik") or [])
        data["pemilik"] = data["pemilik"] or pemilik
        data["skeleton"] = bersih
        # Jenis anotasi diselaraskan: punya titik -> projek kerangka; template
        # dikosongkan -> lepas dari kerangka (kembali ke tebakan otomatis),
        # tapi jangan menimpa kalau owner sudah memilih poligon/kotak.
        if bersih["titik"]:
            data["jenis_anotasi"] = "kerangka"
        elif data.get("jenis_anotasi") == "kerangka":
            data["jenis_anotasi"] = ""
        _tulis(ds, data)
    # Ganti-nama slot (jumlah sama, posisi sama, nama beda): rawat anotasi .json
    # yang sudah tersimpan supaya tak putus. Di luar _kunci — ini I/O berkas
    # anotasi, bukan .tugas.json, dan frame YOLO (.txt) tak perlu disentuh.
    baru = bersih["titik"]
    if lama and len(lama) == len(baru):
        peta = {a: b for a, b in zip(lama, baru) if a != b}
        if peta:
            _relabel_anotasi(ds, peta)
    log.info("skeleton %s: %d titik, %d edge, flip_idx %s", Path(ds).name,
             len(bersih["titik"]), len(bersih["edge"]),
             "ada" if bersih["flip_idx"] else "identitas")
    return {"ok": True, "skeleton": bersih}


# ============================================================
# IMPOR YOLO-POSE  (auto-buat template dari ekspor Roboflow)
# ============================================================

# Warna keluarga cermin untuk template pose hasil impor. Diturunkan dari
# flip_idx, bukan disetel tangan: tiap pasangan involusi (i<j) -> i KIRI (merah),
# j KANAN (teal); titik yang memetakan ke dirinya sendiri (garis tengah/atas/
# bawah court) -> TENGAH (kuning). Ini yang mereproduksi skeleton kiri-merah/
# kanan-teal/tengah-kuning Roboflow otomatis, tanpa satu pun input manual.
WARNA_KIRI = "#ef4444"
WARNA_KANAN = "#14b8a6"
WARNA_TENGAH = "#eab308"

# Preset COCO-17 (pose orang standar COCO/Ultralytics). Dipakai saat impor K=17:
# nama baku + rangka (edge) + flip_idx cermin kiri-kanan, 0-indexed. Urutan slot
# = urutan baku COCO, jadi cocok dengan dataset pose orang mana pun.
_COCO17_NAMA = [
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
]
_COCO17_FLIP = [0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15]
_COCO17_EDGE = [
    [15, 13], [13, 11], [16, 14], [14, 12], [11, 12], [5, 11], [6, 12],
    [5, 6], [5, 7], [6, 8], [7, 9], [8, 10], [1, 2], [0, 1], [0, 2],
    [1, 3], [2, 4], [3, 5], [4, 6],
]


def _warna_cermin(K: int, flip: list) -> list:
    """Warna per keypoint dari keluarga cermin flip_idx (lihat di atas)."""
    if K <= 0:
        return []
    # Tanpa info cermin yang sah (flip kosong / salah panjang) semua jadi tengah:
    # tak ada dasar untuk membedakan kiri dari kanan.
    if not (isinstance(flip, list) and len(flip) == K):
        return [WARNA_TENGAH] * K
    warna = [WARNA_TENGAH] * K
    for i in range(K):
        j = flip[i]
        if not (isinstance(j, int) and 0 <= j < K):
            continue
        # Hanya sisi KECIL pasangan yang mewarnai keduanya; sisi besar (j<i) dan
        # titik swa-peta (j==i) dibiarkan — yang terakhir tetap tengah.
        if i < j:
            warna[i] = WARNA_KIRI
            warna[j] = WARNA_KANAN
    return warna


def template_dari_pose(spec: dict) -> dict:
    """
    Bangun template skeleton dari spek pose data.yaml (scanner.baca_pose_template).

    Nama keypoint = nomor slot 1-based berpad nol ("01".."33"): data.yaml pose
    Roboflow TAK membawa nama keypoint (cuma kpt_shape+flip_idx), jadi nama asli
    tak bisa dipulihkan saat impor murni-YOLO — owner menggantinya belakangan di
    editor #dlg-skel kalau perlu. kelas dari names[0] data.yaml (atau "objek"),
    flip dari berkas (divalidasi ulang sebagai involusi oleh _sah_skeleton),
    warna dari keluarga cermin flip_idx, edge kosong (court Roboflow tak
    mengapalkannya), tata diisi kemudian dari label terimpor (buat_skeleton_dari_pose).

    Dibangun untuk lolos _sah_skeleton apa adanya: ia tetap pintu tunggalnya,
    jadi template ini tak bisa dibedakan dari yang dibuat tangan dan sepenuhnya
    bisa disunting di editor yang sama.
    """
    from . import scanner

    spec = spec if isinstance(spec, dict) else {}
    try:
        K = int(spec.get("K") or 0)
    except (TypeError, ValueError):
        K = 0
    names = spec.get("names") if isinstance(spec.get("names"), dict) else {}
    kelas = " ".join(str(names.get(0) or "").split()) or "objek"
    flip = spec.get("flip_idx") or []
    try:
        flip = ([int(x) for x in flip]
                if isinstance(flip, list) and len(flip) == K else [])
    except (TypeError, ValueError):
        flip = []
    # K=17 hampir pasti pose orang COCO (standar Ultralytics/COCO) — diberi nama,
    # rangka (edge), dan flip_idx yang benar otomatis, bukan slot "01".."17"
    # tanpa rangka. data.yaml YOLO tetap tak membawa nama, tapi K=17 cukup kuat
    # menebak COCO; owner bebas mengubah di #dlg-skel. flip dari berkas tetap
    # diutamakan (otoritatif); COCO dipakai hanya kalau berkas tak menyediakannya.
    if K == 17:
        titik = list(_COCO17_NAMA)
        edge = [e[:] for e in _COCO17_EDGE]
        if not flip:
            flip = list(_COCO17_FLIP)
    else:
        titik = scanner.nama_slot_keypoint(K)
        edge = []
    return {"kelas": kelas, "titik": titik, "edge": edge,
            "flip_idx": flip, "warna": _warna_cermin(K, flip), "tata": []}


def _tata_dari_pose(items: list, titik: list) -> list:
    """
    Tata letak default (relatif 0..1 per slot) dari instance pose terimpor.

    data.yaml pose tak membawa pose acuan, jadi "Drop semua" tanpa ini
    menjatuhkan lingkaran yang tak berarti. Di sini tiap titik v>=1 dinormalkan
    ke bbox instance-nya SENDIRI, lalu diambil MEDIAN per slot atas semua instance
    (tahan terhadap satu-dua anotasi meleset). Slot yang tak pernah terlihat
    jatuh ke instance pertama yang punya, lalu [0.5,0.5].
    """
    from statistics import median

    K = len(titik)
    if K == 0:
        return []
    slot = {n: i for i, n in enumerate(titik)}
    kumpul: list = [[] for _ in range(K)]
    pertama: list = [None] * K
    for it in items:
        grup: dict = {}
        for s in it.get("shapes") or []:
            gid = s.get("group_id")
            if gid is None:
                continue
            g = grup.setdefault(gid, {"rect": None, "pts": {}})
            if s.get("type") == "rectangle":
                g["rect"] = s
            elif s.get("type") == "point":
                nm = None if s.get("label") is None else str(s["label"]).strip()
                if nm in slot:
                    g["pts"][slot[nm]] = s
        for g in grup.values():
            rect = g["rect"]
            if rect is None or not g["pts"]:
                continue
            pts = rect["pts"].tolist()
            xs = [q[0] for q in pts]
            ys = [q[1] for q in pts]
            x0, y0 = min(xs), min(ys)
            w = (max(xs) - x0) or 1.0
            h = (max(ys) - y0) or 1.0
            for i, s in g["pts"].items():
                if ((s.get("flags") or {}).get("v") or 0) < 1:
                    continue                      # titik absen tak menata apa pun
                px, py = s["pts"].tolist()[0]
                nx = min(1.0, max(0.0, (px - x0) / w))
                ny = min(1.0, max(0.0, (py - y0) / h))
                kumpul[i].append((nx, ny))
                if pertama[i] is None:
                    pertama[i] = (nx, ny)
    tata = []
    for i in range(K):
        if kumpul[i]:
            tata.append([median(p[0] for p in kumpul[i]),
                         median(p[1] for p in kumpul[i])])
        elif pertama[i] is not None:
            tata.append([pertama[i][0], pertama[i][1]])
        else:
            tata.append([0.5, 0.5])
    return tata


def buat_skeleton_dari_pose(ds: Path, pemilik: str = "") -> dict:
    """
    Auto-buat template skeleton saat dataset YOLO-pose diimpor.

    Hanya kalau (a) ada data.yaml pose (kpt_shape) DAN (b) projek belum punya
    skeleton — idempoten, TAK pernah menimpa template manual owner. Dibangun lalu
    disimpan lewat set_skeleton yang SAMA dengan editor manual, lalu `tata`
    diturunkan dari label terimpor dalam satu pas pindai (scanner.scan jalan
    SETELAH skeleton tersimpan, supaya ia mengelompokkan baris pose dari
    .tugas.json yang baru).

    Dipanggil dari setiap pintu impor (bongkar arsip, buka unggahan, impor/
    tambah dari server). Murah pada panggilan kedua dan seterusnya.
    """
    from . import scanner

    spec = scanner.baca_pose_template(ds)
    if not spec:
        return {"dibuat": False, "alasan": "bukan dataset pose"}
    if skeleton_aktif(baca(ds, pemilik)):
        return {"dibuat": False, "alasan": "sudah punya skeleton"}
    tpl = template_dari_pose(spec)
    if not tpl["titik"]:
        return {"dibuat": False, "alasan": "K tak sah"}
    set_skeleton(ds, tpl, pemilik)
    try:
        items, _ = scanner.scan(ds)
        tata = _tata_dari_pose(items, tpl["titik"])
        if tata:
            set_skeleton(ds, {**tpl, "tata": tata}, pemilik)
    except Exception as e:                        # tata hiasan; jangan gagalkan impor
        log.warning("tata pose %s gagal diturunkan: %s", Path(ds).name, e)
    log.info("skeleton pose auto-dibuat di %s: K=%d, kelas=%r",
             Path(ds).name, len(tpl["titik"]), tpl["kelas"])
    return {"dibuat": True, "K": len(tpl["titik"]), "kelas": tpl["kelas"]}


def _punya_manifes_dataset(ds: Path) -> bool:
    """True kalau `ds` adalah ekspor dataset JADI (Roboflow/YOLO), ditandai
    manifes `data.yaml` — di akar (tempatnya setelah ratakan_split) atau di satu
    folder pembungkus (ekspor tanpa split yang belum diratakan).

    Inilah batas yang memisahkan dua hal yang mudah tertukar: "mengunggah
    dataset jadi" (anotasinya ikut, memang sudah diputuskan masuk) dari "projek
    pelabelan biasa" (anotasinya dibuat di sini, dan yang memutuskan sebuah
    gambar layak masuk dataset adalah orangnya — lewat tombol borongan — bukan
    sistem yang menebak atas namanya). Projek pelabelan tak punya data.yaml,
    jadi gerbang ini membiarkannya persis seperti dulu.
    """
    ds = Path(ds)
    nama = ("data.yaml", "data.yml", "dataset.yaml")

    def ada(folder: Path) -> bool:
        return any((folder / n).is_file() for n in nama)

    try:
        if ada(ds):
            return True
        for sub in ds.iterdir():
            if sub.is_dir() and not sub.name.startswith((".", "_")) and ada(sub):
                return True
    except OSError:
        pass
    return False


def masuk_dataset_berlabel(ds: Path, pemilik: str = "") -> int:
    """
    FILTER otomatis sesudah impor: gambar yang SUDAH berlabel langsung MASUK
    DATASET, tanpa perlu dibagi ke pelabel.

    Kasus utamanya: dataset JADI yang diunggah LENGKAP (ekspor Roboflow/YOLO
    berikut anotasi) — pekerjaannya sudah selesai, jadi membaginya ke pelabel
    tak masuk akal; ia langsung jadi dataset dan ikut versi/latih. Gambar TANPA
    anotasi (unggahan gambar mentah) TIDAK disentuh: ia tinggal di "Belum
    ditugaskan" untuk dilabeli lebih dulu. Satu unggahan CAMPURAN pun terpilah
    sendiri — yang berlabel ke dataset, yang mentah menunggu dilabeli.

    HANYA berlaku untuk ekspor dataset jadi (ada manifes data.yaml). Tanpa
    manifes, ini projek pelabelan biasa: tidak ada yang disentuh, isi dataset
    tetap diputuskan orangnya lewat tombol borongan. Gerbang itu yang membuat
    aturan lama ("tak satu pun rute menyelinapkan gambar ke dataset") tetap utuh
    untuk projek pelabelan, sementara dataset jadi boleh langsung masuk.

    "Berlabel" = severity != "stop", jadi gambar yang sengaja ditandai LATAR
    (tanpa objek) ikut masuk — menandainya sudah keputusan yang diambil.
    Idempoten (yang sudah di dataset dilewati; aman dipanggil ulang tiap impor).
    Mengembalikan jumlah gambar yang BARU dimasukkan.
    """
    from . import scanner
    from . import tag as _tag

    ds = Path(ds)
    # Gerbang manifes: projek pelabelan (tanpa data.yaml) dibiarkan utuh.
    if not _punya_manifes_dataset(ds):
        return 0
    try:
        items, _ = scanner.scan(ds)
    except Exception as e:                            # noqa: BLE001
        log.warning("auto-masuk dataset %s gagal memindai: %s", Path(ds).name, e)
        return 0
    semua, berlabel, batch_dari = set(), set(), {}
    tag_all = _tag.baca(ds)
    for it in items:
        k = _tag.kunci_gambar(ds, it["img"])
        semua.add(k)
        if scanner.severity(it) != "stop":
            berlabel.add(k)
        b = _tag.untuk(tag_all, k)["batch"]
        if b:
            batch_dari[k] = b
    data = baca(ds, pemilik)
    kunci = belum_ditugaskan_siap(data, berlabel, semua, "", batch_dari)
    if not kunci:
        return 0
    masukkan(ds, kunci, data["pemilik"] or pemilik)
    log.info("auto-masuk dataset %s: %d gambar berlabel langsung jadi dataset",
             Path(ds).name, len(kunci))
    return len(kunci)


# ============================================================
# KELAS AKSI  (projek VIDEO sub-jenis aksi; padanan skeleton{})
# ============================================================

# Batas jumlah kelas aksi satu projek — jauh di atas kebutuhan nyata (acuan
# basket 6-8 kelas), cuma penjaga dari data rusak/ekstrem. Jumlah & nama kelas
# SELALU ditentukan per projek, bukan tetap: tiap classifier punya daftar
# kelasnya sendiri (basket beda dari daur-ulang).
MAKS_KELAS_AKSI = 200


def _sah_aksi(raw) -> dict:
    """Daftar kelas aksi yang rapi & aman, dari data mentah .tugas.json.

    Tak pernah melempar: apa pun yang rusak disederhanakan, tak dibuang mentah.
    Dijaga konsisten sendiri, persis _sah_skeleton:

      - kelas    : nama UNIK & tak kosong (duplikat/kosong dibuang). Inilah yang
                   memetakan klip ke indeks kelas saat ekspor/latih — nama ganda
                   membuat dua kelas berbeda bertabrakan ke satu indeks.
      - merge    : peta {nama_lama -> nama_kanonik}. TARGET-nya WAJIB salah satu
                   `kelas` yang ada (merge ke kelas yang tak ada = klip hilang
                   ke kelas hantu); sumber == target dibuang (tak ada gunanya).
                   Diport dari MERGE_MAP aug-balance-v4.
      - warna    : per kelas (opsional), dipotong/diisi sepanjang kelas.
      - negatif  : kelas "tanpa-aksi" opsional; WAJIB salah satu `kelas` yang
                   ada, kalau tidak dikosongkan. Label kosong = BELUM dilabeli,
                   BUKAN negatif — negatif adalah kelas eksplisit (rencana G#11,
                   MEMORY "sampel negatif label kosong").

    Bentuk: {kelas:[...], merge:{...}, warna:[...], negatif:"<kelas>"|""}.
    """
    kosong = {"kelas": [], "merge": {}, "warna": [], "negatif": ""}
    if not isinstance(raw, dict):
        return kosong

    def _list(x):
        return x if isinstance(x, list) else []

    def _nama(x):
        return " ".join(str(x or "").split())[:80]

    kelas, lihat = [], set()
    for k in _list(raw.get("kelas"))[:MAKS_KELAS_AKSI]:
        nama = _nama(k)
        if nama and nama not in lihat:
            lihat.add(nama)
            kelas.append(nama)
    kset = set(kelas)

    merge = {}
    raw_merge = raw.get("merge") if isinstance(raw.get("merge"), dict) else {}
    for src, dst in raw_merge.items():
        s, t = _nama(src), _nama(dst)
        # Target WAJIB kelas yang ada; sumber tak boleh == target (lingkar
        # kosong). Sumber sendiri TAK wajib ada di `kelas`: ia nama lama yang
        # DIPETAKAN ke kelas kanonik, dan setelah merge memang tak lagi muncul.
        if s and t and t in kset and s != t:
            merge[s] = t

    warna = [str(c)[:9] for c in _list(raw.get("warna"))][:len(kelas)]

    negatif = _nama(raw.get("negatif"))
    if negatif not in kset:
        negatif = ""

    return {"kelas": kelas, "merge": merge, "warna": warna, "negatif": negatif}


def aksi_aktif(data: dict) -> bool:
    """Projek ini classifier aksi: punya daftar kelas aksi berisi."""
    ak = data.get("aksi") or {}
    return bool(ak.get("kelas"))


def set_aksi(ds: Path, template, pemilik: str = "") -> dict:
    """
    Tetapkan daftar kelas aksi projek (nama kelas + merge + kelas negatif).

    Jumlah & nama kelas berbeda tiap projek — inilah tempat owner
    mendefinisikannya sekali, lalu dipakai ulang untuk tiap klip. Template
    disaring lewat _sah_aksi sebelum disimpan. Padanan set_skeleton; disimpan
    lewat jalur baca/tulis yang sama.
    """
    bersih = _sah_aksi(template)
    with _kunci:
        data = baca(ds, pemilik)
        data["pemilik"] = data["pemilik"] or pemilik
        data["aksi"] = bersih
        _tulis(ds, data)
    log.info("aksi %s: %d kelas, %d merge, negatif %r", Path(ds).name,
             len(bersih["kelas"]), len(bersih["merge"]), bersih["negatif"])
    return {"ok": True, "aksi": bersih}


def set_jenis(ds: Path, jenis: str, pemilik: str = "") -> dict:
    """
    Tetapkan format anotasi yang dituju projek ini: "poligon" atau "kotak".

    Kenapa setelan, bukan tebakan semata. Dulu pembuatan versi memutuskannya
    dengan `any(bentuk bukan rectangle)` atas SELURUH projek, sehingga satu
    poligon nyasar di antara sepuluh ribu kotak membuat kesepuluh ribu kotak
    itu ditulis sebagai poligon empat titik -- mask persegi yang mengajari
    model bahwa objeknya memang berbentuk kotak. Keputusan sebesar itu harus
    terlihat dan bisa dikoreksi orang, bukan disimpulkan dari satu bentuk.

    "" mengembalikannya ke tebakan otomatis.
    """
    j = (jenis or "").strip().lower()
    if j and j not in JENIS_ANOTASI:
        raise ValueError(f"jenis anotasi tidak dikenal: {jenis!r}")
    with _kunci:
        data = baca(ds, pemilik)
        if data["jenis_anotasi"] == j:
            return _tanpa_perubahan(data, {"jenis": j})
        data["jenis_anotasi"] = j
        _tulis(ds, data)
    log.info("jenis anotasi %s -> %r", Path(ds).name, j or "(otomatis)")
    return {"jenis": j}


def jenis_berlaku(data: dict, items: list | None = None) -> str:
    """
    Format yang BERLAKU: setelan projek kalau ada, kalau tidak ditebak dari
    bentuk terbanyak. Tebakannya condong ke "poligon" saat seri, karena
    menurunkan poligon jadi kotak masih masuk akal sedangkan menaikkan kotak
    jadi poligon mengarang mask yang tidak pernah digambar siapa pun.
    """
    if data.get("jenis_anotasi") in JENIS_ANOTASI:
        return data["jenis_anotasi"]
    n_kotak = n_lain = 0
    for it in items or []:
        for s in it.get("shapes") or []:
            if (s.get("type") or "polygon") == "rectangle":
                n_kotak += 1
            else:
                n_lain += 1
    return "kotak" if n_kotak > n_lain else "poligon"


def keluarkan(ds: Path, kunci_daftar: list[str], pemilik: str = "") -> dict:
    """Keluarkan gambar dari dataset DAN kembalikan ke belum ditugaskan.

    Dua langkah dalam satu, sesuai yang dipilih: gambar lepas dari dataset
    (tidak lagi ikut ekspor/versi) dan sekaligus lepas dari job-nya, jadi ia
    kembali ke kolam "belum ditugaskan" di halaman Bagi dan bisa dibagi ulang.
    Dulu ia hanya lepas dari dataset tetapi tetap tugas pelabelnya, dan tidak
    ada satu pun jalan mengembalikan satu gambar ke belum-ditugaskan tanpa
    membubarkan seluruh job.
    """
    with _kunci:
        data = baca(ds, pemilik)
        buang = set(kunci_daftar)

        # 1. Lepas dari dataset (kalau kurasi menyala; kalau belum, tidak ada
        #    yang tercatat masuk sehingga tidak ada yang bisa dikeluarkan).
        n_ds = 0
        if data["kurasi"]:
            sisa = [k for k in data["dataset"] if k not in buang]
            n_ds = len(data["dataset"]) - len(sisa)
            data["dataset"] = sisa

        # 2. Lepas dari SEMUA job -> kembali ke belum ditugaskan. Job yang jadi
        #    kosong dibiarkan apa adanya; membubarkannya diam-diam menghapus
        #    catatan siapa yang pernah mengerjakannya, dan itu keputusan yang
        #    pantas dilihat pemiliknya, bukan efek samping.
        n_unassign = 0
        for job in data["tugas"].values():
            g = job.get("gambar") or []
            baru = [k for k in g if k not in buang]
            if len(baru) != len(g):
                n_unassign += len(g) - len(baru)
                job["gambar"] = baru

        if not n_ds and not n_unassign:
            return _tanpa_perubahan(data, {"dikeluarkan": 0, "diunassign": 0,
                                           "total": len(data["dataset"])})
        _tulis(ds, data)
    return {"dikeluarkan": n_ds, "diunassign": n_unassign,
            "total": len(data["dataset"])}


def buang_gambar(ds: Path, item_daftar: list[dict], pemilik: str = "") -> dict:
    """Pindahkan gambar + anotasinya ke tempat sampah projek, bisa dipulihkan.

    BUKAN hapus permanen: satu klik keliru di sini berarti jam kerja pelabelan
    hilang, jadi berkasnya dipindahkan ke `<projek>/_sampah-gambar/<cap>/`,
    bukan di-unlink. Sekaligus dilepas dari dataset dan dari semua job, supaya
    tidak ada catatan yang menunjuk ke gambar yang sudah tidak ada.

    `item_daftar` adalah item hasil pindai (punya path berkasnya), karena yang
    dipindah bukan cuma catatannya melainkan berkas di disk.
    """
    from datetime import datetime

    from ..config import IMG_EXT, SAMPAH_GAMBAR

    dsr = Path(ds)
    kotak = dsr / SAMPAH_GAMBAR / datetime.now().strftime("%Y%m%d-%H%M%S")
    dipindah, kunci_buang = 0, set()
    with _kunci:
        data = baca(ds, pemilik)
        for it in item_daftar:
            img = Path(it["img"])
            if not img.is_file():
                continue
            # Gambar dan SEMUA berkas sebelahnya yang menyertainya: .json
            # labelme, .txt YOLO. Dipindah ke bawah sampah dengan tata letak
            # relatif yang sama, jadi memulihkannya cukup memindahnya balik.
            rel = img.resolve().relative_to(dsr.resolve())
            tuju = kotak / rel
            tuju.parent.mkdir(parents=True, exist_ok=True)
            try:
                for berkas in _berkas_menyertai(it):
                    if berkas.is_file():
                        b_rel = berkas.resolve().relative_to(dsr.resolve())
                        b_tuju = kotak / b_rel
                        b_tuju.parent.mkdir(parents=True, exist_ok=True)
                        berkas.replace(b_tuju)
                img.replace(tuju)
            except OSError:
                continue
            dipindah += 1
            kunci_buang.add(kunci_gambar(ds, img))

        if not dipindah:
            return {"dibuang": 0}
        # Bersihkan catatannya: keluar dari dataset dan dari semua job.
        if data["kurasi"]:
            data["dataset"] = [k for k in data["dataset"] if k not in kunci_buang]
        for job in data["tugas"].values():
            g = job.get("gambar") or []
            job["gambar"] = [k for k in g if k not in kunci_buang]
        _tulis(ds, data)
    log.info("%s gambar dibuang ke sampah di %s", dipindah, dsr.name)
    return {"dibuang": dipindah, "sampah": str(kotak)}


def _berkas_menyertai(it: dict) -> list[Path]:
    """Berkas anotasi yang menempel pada satu gambar (tanpa gambarnya)."""
    keluar = []
    for kunci in ("ann", "labels"):
        p = it.get(kunci)
        if p:
            keluar.append(Path(p))
    # labelme di sebelah gambar, kalau item YOLO pun kadang punya cadangan .json
    keluar.append(Path(it["img"]).with_suffix(".json"))
    # unik, dan bukan gambarnya sendiri
    img = Path(it["img"]).resolve()
    out, lihat = [], set()
    for p in keluar:
        r = p.resolve()
        if r != img and r not in lihat:
            lihat.add(r)
            out.append(p)
    return out


# ============================================================
# ANGGOTA DAN TUGAS
# ============================================================

def undang(ds: Path, pemilik: str, akun: str, peran: str = PERAN_BAWAAN,
           akses: str | None = None, batch=None) -> dict:
    """Tambahkan `akun` sebagai anggota dengan peran & scope-nya.

    Anggota yang SUDAH ada diperbarui peran/scope-nya (bukan dibiarkan) supaya
    satu pintu ini juga jadi cara mengubah hak akses dari UI undang.

    `akses` sengaja OPT-IN: kalau None (undangan polos dari rute lama, tanpa
    pilihan scope), kolom akses TIDAK disetel, dan labeler itu berperilaku
    warisan — hanya gambar yang ditugaskan lewat papan. Scope menyeluruh/
    spesifik hanya berlaku kalau pemilik memang memilihnya. Editor mengabaikan
    akses/batch.
    """
    if not akun or akun == pemilik:
        return {"anggota": []}
    peran = sah_peran(peran)
    with _kunci:
        data = baca(ds, pemilik)
        data["pemilik"] = data["pemilik"] or pemilik
        a = data["anggota"].get(akun) or {
            "sejak": datetime.now().strftime("%Y-%m-%d")}
        a["peran"] = peran
        if peran == "editor":
            # Editor berhak penuh; scope labeler tak relevan — dibuang kalau ada
            # sisa dari peran sebelumnya supaya berkasnya tak menyesatkan.
            a.pop("akses", None)
            a.pop("batch", None)
        elif akses is not None:
            a["akses"] = sah_akses(akses)
            a["batch"] = _bersih_batch(batch) if a["akses"] == "spesifik" else []
        data["anggota"][akun] = a
        _tulis(ds, data)
    log.info("%r diundang/diatur ke projek %s oleh %r (peran %s)",
             akun, Path(ds).name, pemilik, peran)
    return {"anggota": sorted(data["anggota"])}


def atur_anggota(ds: Path, pemilik: str, akun: str, *, peran: str | None = None,
                 akses: str | None = None, batch=None) -> dict:
    """Ubah peran/scope anggota yang sudah ada, tanpa menyentuh yang lain.

    Hanya menyentuh kolom yang diberikan; sisanya dibiarkan. Mengubah peran ke
    Editor membuang scope labeler, dan mengubah akses ke "menyeluruh" membuang
    daftar batch — supaya berkasnya tak menyimpan scope yang tak lagi berlaku.
    """
    with _kunci:
        data = baca(ds, pemilik)
        a = data["anggota"].get(akun)
        if a is None:
            return {"ok": False, "error": "orang itu bukan anggota projek ini"}
        if peran is not None:
            a["peran"] = sah_peran(peran)
        if a.get("peran") == "editor":
            a.pop("akses", None)
            a.pop("batch", None)
        else:
            if akses is not None:
                a["akses"] = sah_akses(akses)
            if a.get("akses") == "spesifik":
                if batch is not None:
                    a["batch"] = _bersih_batch(batch)
                a.setdefault("batch", [])
            else:
                a.pop("batch", None)
        data["anggota"][akun] = a
        _tulis(ds, data)
    log.info("anggota %r di %s diatur (peran %s, akses %s) oleh %r",
             akun, Path(ds).name, a.get("peran"), a.get("akses", "-"), pemilik)
    return {"ok": True, "akun": akun, "peran": a.get("peran"),
            "akses": a.get("akses", ""), "batch": a.get("batch", [])}


def undang_email(ds: Path, pemilik: str, email: str, peran: str = PERAN_BAWAAN,
                 akses: str | None = None, batch=None) -> dict:
    """
    Undangan untuk alamat surel, bukan akun.

    Dipakai saat orangnya belum punya akun di sini. Yang dibuat sebuah token
    rahasia; siapa pun yang membukanya sambil masuk sebagai akun mana pun akan
    bergabung ke projek ini. Karena itu ia sekali pakai dan panjang.

    Peran & scope yang dipilih pemilik ikut disimpan di token dan diterapkan
    saat undangannya dipakai — jadi orang yang masuk lewat tautan langsung
    mendapat hak yang benar, bukan selalu labeler menyeluruh.

    Tokennya TIDAK memuat nama projek. Tautan yang menyebut nama projek sudah
    membocorkan isinya sebelum ada yang menerima undangannya.
    """
    peran = sah_peran(peran)
    # akses "" = tak dipilih (undangan polos) -> labeler warisan saat dipakai.
    akses = sah_akses(akses) if (peran == "pelabel" and akses is not None) else ""
    batch_scope = _bersih_batch(batch) if akses == "spesifik" else []
    email = " ".join(str(email or "").split())[:120].strip()
    # Lebih dari sekadar "ada @": '@' sendirian dan '<script>alert(1)</script>@x.com'
    # dulu diterima, lalu tersimpan sebagai undangan yang tidak mungkin sampai
    # ke siapa pun. Diperiksa sekadar bentuknya — ada nama, ada domain
    # bertitik, tanpa spasi dan tanda kurung sudut — bukan sampai RFC.
    if not re.fullmatch(r"[^@\s<>\"'/\\]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+",
                        email):
        raise ValueError("bukan alamat surel")
    token = secrets.token_urlsafe(18)
    with _kunci:
        data = baca(ds, pemilik)
        # Satu alamat, satu tautan hidup. Dulu tiap panggilan membuat token
        # baru tanpa menengok yang masih terbuka, dan panelnya cuma
        # menampilkan alamatnya — tiga baris "h@example.com" tidak bisa
        # dibedakan, jadi pemilik projek mencabut yang satu dan mengira
        # aksesnya tertutup sementara kembarannya masih hidup.
        for tok, u in data["undangan"].items():
            if u.get("email") == email and not u.get("dipakai"):
                return _tanpa_perubahan(data, {
                    "token": tok, "email": email, "sudah_terbuka": True})
        data["pemilik"] = data["pemilik"] or pemilik
        data["undangan"][token] = {
            "email": email, "oleh": pemilik,
            "dibuat": datetime.now().strftime("%Y-%m-%d %H:%M"), "dipakai": "",
            "peran": peran, "akses": akses, "batch": batch_scope,
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
            peran = sah_peran(u.get("peran"))
            a = {"peran": peran,
                 "sejak": datetime.now().strftime("%Y-%m-%d"),
                 "lewat": u.get("email", "")}
            # akses hanya disetel kalau pemilik memang memilihnya saat mengundang
            # (token menyimpan "" kalau tidak) — kalau tidak, labeler warisan.
            if peran == "pelabel" and u.get("akses"):
                a["akses"] = sah_akses(u.get("akses"))
                a["batch"] = (_bersih_batch(u.get("batch"))
                              if a["akses"] == "spesifik" else [])
            data["anggota"][akun] = a
        _tulis(ds, data)
    log.info("undangan diterima oleh %r di projek %s", akun, Path(ds).name)
    return {"ok": True, "pemilik": data["pemilik"], "nama": Path(ds).name}


def undangan_terbuka(data: dict) -> list[dict]:
    """Undangan yang belum dipakai, untuk ditampilkan di panel anggota."""
    return [{"token": t, **u} for t, u in data["undangan"].items()
            if not u.get("dipakai")]


def batalkan_undangan(ds: Path, pemilik: str, token: str) -> dict:
    """
    Cabut satu tautan undangan yang belum dipakai.

    Undangan yang SUDAH dipakai tidak bisa dibatalkan, dan itu bukan
    keterbatasan: yang tersisa dari undangan seperti itu cuma catatan "lewat
    surel mana orang ini masuk". Menghapusnya menjawab ok:true dan
    dibatalkan:true — yang terbaca seperti akses dicabut — sementara orangnya
    tetap anggota. Yang dimaksud pada kasus itu adalah mengeluarkan
    anggotanya, dan itu yang disebutkan.
    """
    with _kunci:
        data = baca(ds, pemilik)
        u = data["undangan"].get(token)
        if u is None:
            return _tanpa_perubahan(data, {"dibatalkan": False,
                                           "error": "undangan itu tidak ada"})
        if u.get("dipakai"):
            return _tanpa_perubahan(data, {
                "dibatalkan": False,
                "error": f"undangan ini sudah dipakai {u['dipakai']}; "
                         f"keluarkan anggotanya kalau mau mencabut aksesnya"})
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
        # Job tanpa satu pun gambar tidak pernah bisa dikerjakan dan cuma
        # membingungkan (papan menampilkannya sebagai job 0 gambar). Dulu ia
        # tetap terbuat kalau seluruh gambar yang diminta SUDAH ditugaskan ke
        # job lain. Ditolak di sini, sebelum anggota atau job dibuat.
        if not milik:
            return {"id": None, "pelabel": pelabel, "n": 0,
                    "dilewati": len(gambar), "kosong": True}
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


def buang_job_kosong(ds: Path, pemilik: str) -> int:
    """Buang job yang tidak punya satu pun gambar.

    Job 0 gambar tak bisa dikerjakan dan hanya membingungkan di papan (leftover
    dari membagi gambar yang semuanya sudah ditugaskan). Membuangnya tidak
    menghilangkan pekerjaan apa pun — memang tidak ada. Idempoten dan berkunci,
    jadi aman dipanggil saat papan dibuka walau beberapa sesi sekaligus."""
    with _kunci:
        data = baca(ds, pemilik)
        kosong = [tid for tid, t in data["tugas"].items()
                  if not (t.get("gambar") or [])]
        for tid in kosong:
            data["tugas"].pop(tid, None)
        if kosong:
            _tulis(ds, data)
    if kosong:
        log.info("buang %s job kosong di %s", len(kosong), Path(ds).name)
    return len(kosong)


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
        # SELESAI menuntut seluruhnya sudah DIKERJAKAN, bukan cuma sudah
        # dimasukkan. Memasukkan gambar yang belum dilabeli ke dataset memang
        # bisa — kadang memang itu yang dimaksud — tetapi kartunya lalu pindah
        # ke kolom Dataset dan menyatakan 100% untuk pekerjaan yang 0%
        # dikerjakan, bertentangan dengan halaman rinciannya sendiri.
        keadaan = (SELESAI if g and n_dataset == len(g) and n_label == len(g)
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
            # Job yang seluruh gambarnya sudah tidak ada di disk. Ia menetap di
            # kolom "Dikerjakan" selamanya dengan 0 dari 0, dan tidak ada apa
            # pun di kartunya yang menjelaskan kenapa. Disebutkan, bukan
            # disembunyikan: yang menyembunyikannya membuat pemiliknya tidak
            # punya jalan membubarkannya.
            "hilang": bool(t.get("gambar")) and not g,
            "n_asal": len(t.get("gambar") or []),
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
