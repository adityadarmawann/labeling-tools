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
            "tugas": {}, "dataset": [], "undangan": {}, "warisan": True}


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
            "warisan": False}


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
    if data["warisan"] or akun == data["pemilik"]:
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
    Apakah satu gambar sudah dinyatakan masuk dataset.

    Projek warisan menjawab True untuk semuanya: sebelum penugasan ada, seluruh
    isi projek memang dataset itu sendiri, dan menjawab False akan membuat
    ekspor projek lama mendadak kosong.
    """
    return data["warisan"] or kunci in data["dataset"]


def saring_dataset(items: list, ds: Path, akun: str = "") -> tuple[list, dict]:
    """
    Hanya gambar yang sudah dinyatakan masuk dataset, beserta angkanya.

    Inilah yang membedakan "sudah dilabeli" dari "sudah selesai". Melabeli
    dilakukan berkali-kali sambil ragu; menyatakan masuk dataset sekali dan
    berakibat, dan yang berakibat itu justru di sini: splitting, versi, dan
    ekspor semuanya bekerja pada hasil saringan ini.

    Projek warisan mengembalikan seluruhnya apa adanya. Tanpa itu, ekspor empat
    projek yang sudah ada akan mendadak kosong pada hari fitur ini dipasang.
    """
    from .tag import kunci_gambar

    data = baca(ds, akun)
    if data["warisan"]:
        return list(items), {"n_semua": len(items), "n_dataset": len(items),
                             "warisan": True}
    dipakai = [it for it in items
               if di_dataset(data, kunci_gambar(ds, it["img"]))]
    return dipakai, {"n_semua": len(items), "n_dataset": len(dipakai),
                     "warisan": False}


def masukkan(ds: Path, kunci_daftar: list[str], pemilik: str = "") -> dict:
    """Nyatakan sekumpulan gambar masuk dataset."""
    with _kunci:
        data = baca(ds, pemilik)
        ada = set(data["dataset"])
        baru = [k for k in kunci_daftar if k not in ada]
        data["dataset"] = data["dataset"] + baru
        _tulis(ds, data)
    log.info("%s gambar masuk dataset di %s", len(baru), Path(ds).name)
    return {"ditambah": len(baru), "total": len(data["dataset"])}


def keluarkan(ds: Path, kunci_daftar: list[str], pemilik: str = "") -> dict:
    """Kembalikan gambar dari dataset ke daftar yang masih dikerjakan."""
    with _kunci:
        data = baca(ds, pemilik)
        buang = set(kunci_daftar)
        sebelum = len(data["dataset"])
        data["dataset"] = [k for k in data["dataset"] if k not in buang]
        _tulis(ds, data)
    return {"dikeluarkan": sebelum - len(data["dataset"]),
            "total": len(data["dataset"])}


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
        ada = data["undangan"].pop(token, None)
        _tulis(ds, data)
    return {"dibatalkan": bool(ada)}


def keluarkan_anggota(ds: Path, pemilik: str, akun: str) -> dict:
    with _kunci:
        data = baca(ds, pemilik)
        data["anggota"].pop(akun, None)
        # Tugasnya ikut dibubarkan: job tanpa pelabel yang masih berhak
        # menyunting adalah pekerjaan yang tidak bisa dilanjutkan siapa pun.
        for tid in [t for t, v in data["tugas"].items() if v.get("pelabel") == akun]:
            data["tugas"].pop(tid, None)
        _tulis(ds, data)
    return {"anggota": sorted(data["anggota"])}


def tugaskan(ds: Path, pemilik: str, pelabel: str, gambar: list[str],
             catatan: str = "") -> dict:
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


def bubarkan(ds: Path, pemilik: str, tid: str) -> dict:
    with _kunci:
        data = baca(ds, pemilik)
        ada = data["tugas"].pop(tid, None)
        _tulis(ds, data)
    return {"dibubarkan": bool(ada)}


# ============================================================
# PAPAN
# ============================================================

def papan(data: dict, berlabel: set[str], semua: set[str]) -> dict:
    """
    Bahan untuk papan Anotasi: tiga kolom.

    `berlabel` dan `semua` datang dari pemindai, bukan dihitung di sini. Berkas
    tugas menyimpan siapa mengerjakan apa; yang tahu sebuah gambar sudah punya
    objek atau belum cuma pemindainya.
    """
    ditugaskan = set()
    kartu = []
    for tid, t in data["tugas"].items():
        g = [k for k in (t.get("gambar") or []) if k in semua]
        ditugaskan.update(g)
        n_label = sum(1 for k in g if k in berlabel)
        n_dataset = sum(1 for k in g if di_dataset(data, k))
        keadaan = (SELESAI if g and n_dataset == len(g)
                   else JALAN if n_label else BARU)
        kartu.append({
            "id": tid, "pelabel": t.get("pelabel", ""),
            "dibuat": t.get("dibuat", ""), "catatan": t.get("catatan", ""),
            "jumlah": len(g), "berlabel": n_label,
            "di_dataset": n_dataset, "keadaan": keadaan,
            "persen": round(n_label * 100 / len(g)) if g else 0,
        })
    kartu.sort(key=lambda k: k["dibuat"], reverse=True)
    belum = sorted(semua - ditugaskan)

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

    return {
        "belum_ditugaskan": len(belum),
        "kartu": kartu,
        "per_pelabel": per_pelabel,
        "n_dataset": sum(1 for k in semua if di_dataset(data, k)),
        "n_semua": len(semua),
        "n_berlabel": len(berlabel & semua),
    }
