"""
Pemilih LINGKUP kisi Dataset: satu aturan, dipakai bersama kisi dan kanvas.

Kisi di "/" menyaring dan mengurutkan gambar dengan sepuluh parameter (keadaan,
kelas, keadaan tanpa-kelas, mode gabung, tag, unggahan, split, asal-usul,
pencarian nama, urutan). Halaman kanvas "/label" dan "/view" dulu tidak
menerima satu pun di antaranya: keduanya menyusuri `sess.items` apa adanya.

Akibatnya persis yang dikeluhkan pelabel: menyaring kisi jadi "belum
diverifikasi", membuka satu gambar untuk diperbaiki, lalu menekan berikutnya
dan mendarat di gambar yang TIDAK ada di saringan tadi. Terukur: kisi
menampilkan 00, 02, 04 sementara kanvas melompat dari 00 ke 01.

Aturannya ditaruh DI SINI, bukan digandakan di tiap rute. Dua salinan aturan
saring selalu berakhir berbeda, dan bedanya senyap: yang satu menampilkan,
yang lain menyusuri, dan tidak ada yang menyalakan alarm.

Padanannya untuk halaman job adalah tugas.gambar_konteks_job.
"""
from __future__ import annotations

from . import scanner

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

TANPA_KELAS = {
    "latar": ("Latar (tanpa objek)", "bg"),
    "unlab": ("Belum dilabeli", "stop"),
}


def urutkan(items: list[dict], urut: str) -> list[dict]:
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


def saring(items: list[dict], flt: str, kelas, tanpa=(), mode="atau", *,
           tugasku=frozenset()) -> list[dict]:
    """Saringan keadaan + kelas atas daftar item mentah.

    Badannya kini tipis: aturannya tinggal di _cocok_keadaan dan _cocok_kelas,
    dipakai bersama facet() supaya angka di menu dan isi kisi tidak bisa
    berbeda. Dipertahankan sebagai fungsi tersendiri karena rute dan uji sudah
    memanggilnya dengan item mentah.
    """
    keluar = []
    for it in items:
        per, _n = scanner.objek_per_kelas(it)
        f = {"it": it, "sev": scanner.severity(it), "kelas": set(per),
             "dicek": bool(it.get("perlu_dicek")), "aku": id(it) in tugasku}
        if _cocok_keadaan(f, flt) and _cocok_kelas(f, kelas, tanpa, mode):
            keluar.append(it)
    return keluar



# Parameter lingkup yang dikenal kisi. Dipakai templat untuk menitipkan
# konteks ke kanvas, dan rute kanvas untuk membacanya kembali. SATU daftar:
# menambah saringan baru di kisi tanpa menambahkannya di sini membuat kanvas
# diam-diam menyusuri subset yang lebih luas daripada yang dilihat orang.
KUNCI_URL = ("f", "s", "q", "c", "x", "m", "tg", "bt", "sp", "tu", "dsf")


def _banyak(qp, nama: str) -> list[str]:
    ambil = getattr(qp, "getlist", None)
    nilai = ambil(nama) if ambil else qp.get(nama) or []
    if isinstance(nilai, str):
        nilai = [nilai]
    return [str(v) for v in nilai if str(v)]


def baca(qp) -> dict:
    """Query string -> parameter lingkup yang sudah dinormalkan.

    Menerima apa pun yang punya .get/.getlist (starlette QueryParams) maupun
    dict biasa, supaya rute dan uji memakai jalan masuk yang sama.
    """
    satu = (lambda n, b="": str(qp.get(n) or b))
    return {
        "f": satu("f", "all"),
        # Lingkup DATASET, sejajar dengan tab di halaman job: "di" (bawaan,
        # hanya isi dataset), "belum" (sudah dianotasi tapi belum dinyatakan
        # masuk), "semua". Tanpa ini, hasil auto-label sebatch tak punya
        # permukaan sama sekali: kisi hanya memuat isi dataset, sedangkan
        # gambar yang baru di-auto-label justru BELUM dimasukkan.
        "dsf": (satu("dsf", "di") if satu("dsf", "di") in ("di", "belum", "semua")
                else "di"),
        "urut": satu("s", URUT_BAWAAN),
        "cari": satu("q").strip(),
        "kelas": list(dict.fromkeys(k for k in _banyak(qp, "c") if k)),
        "tanpa": [t for t in dict.fromkeys(_banyak(qp, "x")) if t in TANPA_KELAS],
        "mode": "dan" if satu("m") == "dan" else "atau",
        "tag": [t for t in dict.fromkeys(_banyak(qp, "tg")) if t],
        "batch": satu("bt"),
        "split": [s for s in dict.fromkeys(_banyak(qp, "sp")) if s],
        "turunan": [t for t in dict.fromkeys(_banyak(qp, "tu"))
                    if t in ("asli", "aug", "bal")],
    }


def ada_lingkup(p: dict) -> bool:
    """Benar kalau setidaknya satu saringan aktif. Tanpa ini, membuka gambar
    dari kisi polos tak perlu membayar ongkos membangun subset."""
    return bool(p["f"] != "all" or p["cari"] or p["kelas"] or p["tanpa"]
                or p["tag"] or p["batch"] or p["split"] or p["turunan"]
                or p["urut"] != URUT_BAWAAN or p["dsf"] != "di")


def subset(items: list[dict], src, user: str, data: dict, tagdata: dict,
           p: dict, skel_kelas: str = "") -> list[dict]:
    """Gambar yang BENAR-BENAR tampil di kisi untuk parameter `p`, urut sama.

    Satu aturan saja: _cocok() yang sama dipakai kisi, facet, kanvas, dan
    halaman Lihat. Dulu urutan langkahnya ditulis ulang di sini dan di rute
    kisi; dua salinan aturan saring selalu berakhir berbeda, dan bedanya
    senyap karena yang satu menampilkan sementara yang lain menyusuri.
    """
    fk = fakta(items, src, user, data, tagdata, skel_kelas)
    keluar = [f["it"] for f in fk if _cocok(f, p)]
    return urutkan(keluar, p["urut"] if p["urut"] in URUT else URUT_BAWAAN)



def ke_query(p: dict, awalan: str = "&") -> str:
    """Parameter lingkup -> potongan query string.

    Dirakit DI SINI, bukan di templat, supaya daftar kuncinya cuma ada satu.
    Daftar kedua di templat adalah cara paling mudah kehilangan satu saringan
    diam-diam saat ada saringan baru ditambahkan.
    """
    from urllib.parse import quote

    bagian: list[str] = []
    if p["f"] and p["f"] != "all":
        bagian.append(f"f={quote(p['f'])}")
    if p["dsf"] and p["dsf"] != "di":
        bagian.append(f"dsf={quote(p['dsf'])}")
    bagian += [f"c={quote(k)}" for k in p["kelas"]]
    bagian += [f"x={quote(k)}" for k in p["tanpa"]]
    if p["mode"] and p["mode"] != "atau":
        bagian.append(f"m={quote(p['mode'])}")
    if p["urut"] and p["urut"] != URUT_BAWAAN:
        bagian.append(f"s={quote(p['urut'])}")
    if p["cari"]:
        bagian.append(f"q={quote(p['cari'])}")
    bagian += [f"tg={quote(k)}" for k in p["tag"]]
    if p["batch"]:
        bagian.append(f"bt={quote(p['batch'])}")
    bagian += [f"sp={quote(k)}" for k in p["split"]]
    bagian += [f"tu={quote(k)}" for k in p["turunan"]]
    if not bagian:
        return ""
    return awalan + "&".join(bagian)


def pilih_dataset(items: list, src, uploads_root, dsf: str = "di"):
    """Terapkan lingkup DATASET, lalu kembalikan (subset, hitungan).

    Kisi Dataset selalu menyaring ke isi dataset. Itu benar sebagai bawaan,
    tetapi membuat satu golongan gambar tak punya permukaan sama sekali:
    yang sudah dianotasi (mis. oleh auto-label sebatch) tetapi belum pernah
    dinyatakan masuk. Tombol "Verifikasi N hasil AI" di papan menunjuk ke
    sana dan selalu mendarat di halaman kosong, karena angkanya justru
    dihitung dari gambar yang BELUM masuk dataset.
    """
    from . import tugas as _tugas

    dalam, hitung = _tugas.saring_dataset(items, src, uploads_root)
    if hitung.get("warisan") or dsf == "di":
        return dalam, hitung
    if dsf == "semua":
        return list(items), hitung
    tersisa = {id(x) for x in dalam}
    return [x for x in items if id(x) not in tersisa], hitung


# ----------------------------------------------------------------- facet
# Angka tiap saringan dihitung atas hasil saringan LAIN yang sedang aktif,
# bukan atas seluruh dataset.
#
# Tanpa itu: orang menyaring split "valid", membuka menu kelas, melihat
# "botol 842", mencentangnya, dan mendapat NOL hasil -- karena 842 itu angka
# seluruh dataset. Tidak ada apa pun di layar yang menjelaskannya. Roboflow dan
# Label Studio sama-sama memakai hitungan ber-facet; ini padanannya.
#
# Dimensi sebuah facet TIDAK ikut menyaring dirinya sendiri: kalau ikut,
# mencentang satu kelas akan membuat semua kelas lain menunjukkan nol dan
# daftar itu berhenti bisa dipakai memilih.


def fakta(items: list, src, user: str, data: dict, tagdata: dict,
          skel_kelas: str = "") -> list[dict]:
    """Sifat tiap gambar yang dibutuhkan saringan, dihitung SEKALI.

    Memisahkan "membaca gambar" dari "menyaring" supaya facet bisa menjalankan
    saringan delapan kali tanpa membaca apa pun delapan kali.
    """
    from . import tag as _tag
    from . import tugas as _tugas

    warisan = bool((data or {}).get("warisan"))
    punya_tag = bool((tagdata or {}).get("gambar"))
    keluar = []
    for it in items:
        k = _tag.kunci_gambar(src, it["img"]) if (not warisan or punya_tag) else ""
        r = _tag.untuk(tagdata, k) if punya_tag else {"tag": [], "batch": ""}
        per, n_obj = scanner.objek_per_kelas(it, skel_kelas)
        keluar.append({
            "it": it,
            "sev": scanner.severity(it),
            "kelas": set(per),
            "objek": per,
            "n_obj": n_obj,
            "dicek": bool(it.get("perlu_dicek")),
            "aku": (not warisan) and _tugas.pelabel_gambar(data, k) == user,
            "tag": set(r.get("tag") or []),
            "batch": str(r.get("batch") or ""),
            "split": str(it.get("split") or ""),
            "turunan": scanner.jenis_turunan(it["img"].name),
            "nama": it["img"].name.lower(),
        })
    return keluar


def _cocok_keadaan(f: dict, flt: str) -> bool:
    """Aturan dimensi KEADAAN, dipakai bersama saring() dan facet()."""
    sev = f["sev"]
    if flt == "tugasku":
        return f["aku"]
    if flt == "issue":
        return bool(f["it"]["issues"] and f["it"]["shapes"])
    if flt == "bg":
        return sev == "bg"
    if flt == "sudah":
        return sev != "stop"
    if flt == "unlab":
        return sev == "stop"
    if flt == "dicek":
        return f["dicek"] and sev != "stop"
    if flt == "verif":
        return sev != "stop" and not f["dicek"]
    return True


def _cocok_kelas(f: dict, kelas, tanpa, mode: str) -> bool:
    if not kelas and not tanpa:
        return True
    pilih = set(kelas)
    sev_pilih = {TANPA_KELAS[t][1] for t in tanpa if t in TANPA_KELAS}
    if mode == "dan":
        return pilih <= f["kelas"]
    return bool(pilih & f["kelas"]) or f["sev"] in sev_pilih


_DIMENSI = ("f", "kelas", "tag", "batch", "split", "turunan", "cari")


def _cocok(f: dict, p: dict, kecuali: str = "") -> bool:
    if kecuali != "f" and not _cocok_keadaan(f, p["f"]):
        return False
    if kecuali != "kelas" and not _cocok_kelas(f, p["kelas"], p["tanpa"],
                                               p["mode"]):
        return False
    if kecuali != "tag" and p["tag"] and not (set(p["tag"]) & f["tag"]):
        return False
    if kecuali != "batch" and p["batch"] and f["batch"] != p["batch"]:
        return False
    if kecuali != "split" and p["split"] and f["split"] not in set(p["split"]):
        return False
    if kecuali != "turunan" and p["turunan"] \
            and f["turunan"] not in set(p["turunan"]):
        return False
    if kecuali != "cari" and p["cari"] and p["cari"].lower() not in f["nama"]:
        return False
    return True


def facet(fk: list[dict], p: dict) -> dict:
    """Hitungan tiap dimensi, masing-masing atas hasil saringan LAIN."""
    def basis(dim: str) -> list[dict]:
        return [f for f in fk if _cocok(f, p, kecuali=dim)]

    b_f = basis("f")
    keadaan = {v: sum(1 for f in b_f if _cocok_keadaan(f, v))
               for v in ("all", "tugasku", "issue", "bg", "sudah", "unlab",
                         "dicek", "verif")}

    b_k = basis("kelas")
    kelas_gambar: dict[str, int] = {}
    kelas_objek: dict[str, int] = {}
    for f in b_k:
        for k, n in f["objek"].items():
            kelas_gambar[k] = kelas_gambar.get(k, 0) + 1
            kelas_objek[k] = kelas_objek.get(k, 0) + n
    tanpa_hitung = {"latar": sum(1 for f in b_k if f["sev"] == "bg"),
                    "unlab": sum(1 for f in b_k if f["sev"] == "stop")}

    tag_hitung: dict[str, int] = {}
    for f in basis("tag"):
        for t in f["tag"]:
            tag_hitung[t] = tag_hitung.get(t, 0) + 1
    batch_hitung: dict[str, int] = {}
    for f in basis("batch"):
        if f["batch"]:
            batch_hitung[f["batch"]] = batch_hitung.get(f["batch"], 0) + 1
    split_hitung: dict[str, int] = {}
    for f in basis("split"):
        if f["split"]:
            split_hitung[f["split"]] = split_hitung.get(f["split"], 0) + 1
    turunan_hitung: dict[str, int] = {}
    for f in basis("turunan"):
        turunan_hitung[f["turunan"]] = turunan_hitung.get(f["turunan"], 0) + 1

    tampil = [f for f in fk if _cocok(f, p)]
    return {"keadaan": keadaan,
            "kelas_gambar": dict(sorted(kelas_gambar.items())),
            "kelas_objek": kelas_objek,
            "tanpa": tanpa_hitung,
            "tag": dict(sorted(tag_hitung.items())),
            "batch": dict(sorted(batch_hitung.items())),
            "split": dict(sorted(split_hitung.items())),
            "turunan": turunan_hitung,
            "tampil": [f["it"] for f in tampil],
            "n_obj_tampil": sum(f["n_obj"] for f in tampil),
            "n_obj_semua": sum(f["n_obj"] for f in fk)}
