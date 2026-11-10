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
    if flt == "tugasku":
        # Gambar yang ditugaskan kepadaku. Bukan sekadar kenyamanan: di projek
        # 10.000 gambar, jatah seseorang bisa 200, dan tanpa saringan ini ia
        # menggulir sembilan ribu delapan ratus gambar milik orang lain untuk
        # menemukan pekerjaannya sendiri.
        items = [i for i in items if id(i) in tugasku]
    elif flt == "issue":
        items = [i for i in items if i["issues"] and i["shapes"]]
    elif flt == "bg":
        # Gambar yang SENGAJA ditandai tanpa objek — sampel negatif. Ia berbeda
        # dari "belum dilabeli": yang ini sudah selesai diperiksa, dan porsinya
        # di dataset menentukan seberapa sering model salah menebak latar
        # sebagai objek. Tanpa saringan sendiri, ia tidak bisa dihitung maupun
        # ditinjau ulang.
        items = [i for i in items if scanner.severity(i) == "bg"]
    elif flt == "sudah":
        # Latar IKUT. Menandai gambar tanpa objek adalah keputusan yang sudah
        # diambil, bukan pekerjaan yang belum dikerjakan — dan ia satu-satunya
        # isi sampel negatif, yang di sini justru paling perlu dihitung.
        #
        # Empat tempat lain sudah menghitungnya begitu sejak awal: kartu
        # projek, sidebar, papan anotasi, dan halaman tugas. Grid yang
        # sendirian mengecualikannya membuat satu projek menyebut dua angka
        # "sudah dikerjakan" yang berbeda di dua halaman.
        items = [i for i in items if scanner.severity(i) != "stop"]
    elif flt == "dicek":
        # Hasil auto-label yang BELUM diperiksa manusia. Sengaja memakai kata
        # "verifikasi", bukan "perlu dicek": kata itu sudah dipakai halaman ini
        # sendiri untuk severity "warn" (anotasi yang BENTUKNYA mencurigakan),
        # dan dua arti yang memakai satu kata membuat orang mengira keduanya
        # menghitung hal yang sama.
        items = [i for i in items
                 if i.get("perlu_dicek") and scanner.severity(i) != "stop"]
    elif flt == "verif":
        # Pasangannya: sudah dilabeli DAN sudah dilewati manusia. Tanpa ini,
        # "Sudah dilabeli" mencampur hasil AI mentah dengan yang benar-benar
        # selesai diperiksa, dan tak ada angka yang menunjukkan kemajuan
        # verifikasi.
        items = [i for i in items
                 if scanner.severity(i) != "stop" and not i.get("perlu_dicek")]
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


# Parameter lingkup yang dikenal kisi. Dipakai templat untuk menitipkan
# konteks ke kanvas, dan rute kanvas untuk membacanya kembali. SATU daftar:
# menambah saringan baru di kisi tanpa menambahkannya di sini membuat kanvas
# diam-diam menyusuri subset yang lebih luas daripada yang dilihat orang.
KUNCI_URL = ("f", "s", "q", "c", "x", "m", "tg", "bt", "sp", "tu")


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
                or p["urut"] != URUT_BAWAAN)


def subset(items: list[dict], src, user: str, data: dict, tagdata: dict,
           p: dict) -> list[dict]:
    """Gambar yang BENAR-BENAR tampil di kisi untuk parameter `p`, urut sama.

    Urutan langkahnya sengaja identik dengan rute kisi: saring keadaan/kelas
    dulu, lalu tag dan unggahan, lalu split dan asal-usul, lalu pencarian
    nama, baru diurutkan. Mengubah urutannya di satu tempat saja membuat
    subset kanvas dan isi kisi berbeda tanpa ada yang mengeluh.
    """
    from . import tag as _tag
    from . import tugas as _tugas

    tugasku: set = set()
    if data and not data.get("warisan"):
        for it in items:
            k = _tag.kunci_gambar(src, it["img"])
            if _tugas.pelabel_gambar(data, k) == user:
                tugasku.add(id(it))

    perlu_tag = bool(p["tag"] or p["batch"])
    tag_dari: dict = {}
    if perlu_tag and (tagdata or {}).get("gambar"):
        for it in items:
            r = _tag.untuk(tagdata, _tag.kunci_gambar(src, it["img"]))
            if r["tag"] or r["batch"]:
                tag_dari[id(it)] = r

    keluar = saring(items, p["f"], p["kelas"], p["tanpa"], p["mode"],
                    tugasku=tugasku)
    if p["tag"]:
        pilih = set(p["tag"])
        keluar = [it for it in keluar
                  if pilih & set((tag_dari.get(id(it)) or {}).get("tag") or [])]
    if p["batch"]:
        keluar = [it for it in keluar
                  if (tag_dari.get(id(it)) or {}).get("batch") == p["batch"]]
    if p["split"]:
        pilih = set(p["split"])
        keluar = [it for it in keluar if (it.get("split") or "") in pilih]
    if p["turunan"]:
        pilih = set(p["turunan"])
        keluar = [it for it in keluar
                  if scanner.jenis_turunan(it["img"].name) in pilih]
    if p["cari"]:
        pola = p["cari"].lower()
        keluar = [it for it in keluar if pola in it["img"].name.lower()]
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
