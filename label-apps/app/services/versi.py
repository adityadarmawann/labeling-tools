"""
Versi dataset: pembagian train/valid/test yang dibekukan.

KENAPA ADA
----------
Tanpa versi, satu-satunya cara mengetahui data apa yang dipakai melatih sebuah
model adalah mengingatnya. Dataset terus bertambah; ekspor minggu lalu dan
ekspor hari ini berisi gambar yang berbeda, dan pembagian train/valid/test-nya
pun dihitung ulang. Model yang hasilnya turun lalu tidak bisa dibandingkan
dengan apa pun, karena datanya sudah bukan data yang sama.

Sebuah versi menyimpan dua hal yang membuat ekspornya bisa diulang persis:
daftar gambarnya, dan gambar mana masuk split mana. Gambar yang ditambahkan
sesudahnya tidak pernah masuk ke versi yang sudah dibuat.

DI MANA
-------
Folder `.versi/` di dalam projeknya, satu berkas per versi. Berawalan titik
supaya pemindai melewatinya: berkas di dalamnya bernama v1.json, dan tanpa
aturan itu ia terbaca sebagai anotasi labelme.

Satu berkas per versi, bukan satu berkas berisi semuanya, karena tiap versi
memuat peta selengkap jumlah gambarnya. Sepuluh versi dari dataset sebelas ribu
gambar berarti seratus sepuluh ribu baris dalam satu berkas yang harus dibaca
utuh hanya untuk menampilkan daftarnya.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path

from ..log import catat

log = catat("labelapp.versi")

FOLDER = ".versi"
MAKS_CATATAN = 400

_kunci = threading.Lock()


def _dir(ds: Path) -> Path:
    return Path(ds) / FOLDER


def _berkas(ds: Path, nomor: int) -> Path:
    return _dir(ds) / f"v{int(nomor)}.json"


def daftar(ds: Path) -> list[dict]:
    """Ringkasan tiap versi, TANPA petanya.

    Petanya bisa berisi puluhan ribu baris dan tidak dipakai sama sekali untuk
    menampilkan daftarnya.

    `n_ada` menyebut berapa gambarnya yang masih benar-benar ada di disk.
    Angka `n` adalah jumlah saat versi itu dibekukan dan memang tidak boleh
    berubah — tetapi kartu yang menyebut 6 sementara ZIP-nya berisi 5 membuat
    orang mengira ekspornya kehilangan sesuatu, dan alasan itu sudah ditulis
    sendiri di rute dataset untuk kasus yang sama persis.
    """
    from ..config import IMG_EXT

    d = _dir(ds)
    if not d.is_dir():
        return []
    ada = set()
    akar = Path(ds)
    for q in akar.rglob("*"):
        if q.suffix.lower() in IMG_EXT and q.is_file() and not any(
                x.startswith(".") for x in q.relative_to(akar).parts):
            ada.add(q.name)
            ada.add(q.resolve().relative_to(akar.resolve()).as_posix())
    out = []
    for p in sorted(d.glob("v*.json")):
        try:
            v = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("berkas versi rusak, dilewati: %s", p)
            continue
        v["n_ada"] = sum(1 for g in (v.get("gambar") or []) if g in ada)
        v.pop("peta", None)
        v.pop("gambar", None)
        out.append(v)
    out.sort(key=lambda v: v.get("nomor", 0), reverse=True)
    return out


def baca(ds: Path, nomor: int) -> dict | None:
    p = _berkas(ds, nomor)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def nomor_berikut(ds: Path) -> int:
    d = _dir(ds)
    if not d.is_dir():
        return 1
    angka = [int(m.group(1)) for p in d.glob("v*.json")
             if (m := re.fullmatch(r"v(\d+)\.json", p.name))]
    return (max(angka) + 1) if angka else 1


def buat(ds: Path, oleh: str, rasio: str, gambar: list[str],
         peta: dict[str, str], ringkas: dict, catatan: str = "") -> dict:
    """
    Bekukan pembagian yang berlaku sekarang.

    `gambar` daftar nama berkas yang ikut, `peta` nama berkas -> split. Keduanya
    disimpan apa adanya: yang membuat versi bisa diulang persis bukan rasionya
    melainkan petanya, karena rasio yang sama pada dataset yang sudah bertambah
    menghasilkan pembagian yang lain.
    """
    with _kunci:
        d = _dir(ds)
        d.mkdir(parents=True, exist_ok=True)
        n = nomor_berikut(ds)
        isi = {
            "nomor": n,
            "dibuat": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "oleh": oleh,
            "rasio": rasio,
            "catatan": " ".join((catatan or "").split())[:MAKS_CATATAN],
            "n": len(gambar),
            # export.ringkasan menamainya "split", bukan "jumlah".
            "jumlah": ringkas.get("split") or {},
            # export.ringkasan mengembalikan JUMLAH kelas, bukan daftarnya.
            "kelas": ringkas.get("kelas") or 0,
            "objek": ringkas.get("objek", 0),
            "beralas": ringkas.get("beralas", False),
            "gambar": sorted(gambar),
            "peta": peta,
        }
        p = _berkas(ds, n)
        tmp = p.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(isi, ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
    log.info("versi v%s dibuat di %s: %s gambar, rasio %s",
             n, Path(ds).name, len(gambar), rasio)
    return {"nomor": n, "n": len(gambar)}


def hapus(ds: Path, nomor: int) -> bool:
    """
    Versi dibuang permanen.

    Tidak dipindahkan ke sampah seperti projek: isinya hanya catatan pembagian,
    bukan gambar. Yang hilang catatan bahwa suatu pembagian pernah ada, dan itu
    memang yang diminta saat menghapusnya.
    """
    p = _berkas(ds, nomor)
    if not p.is_file():
        return False
    p.unlink()
    log.warning("versi v%s dihapus dari %s", nomor, Path(ds).name)
    return True
