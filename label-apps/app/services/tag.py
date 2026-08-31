"""
Tag dan nama unggahan untuk tiap gambar.

DI MANA DISIMPAN, DAN KENAPA DI SITU
------------------------------------
Satu berkas pendamping `.tag.json` di akar tiap projek, bukan di dalam berkas
anotasi. Tiga alasan, dan ketiganya soal tidak merusak yang sudah ada:

1. Gambar yang belum dilabeli TIDAK punya berkas anotasi sama sekali. Padahal
   justru gambar itulah yang paling perlu ditandai saat diunggah, supaya nanti
   bisa dicari kembali.

2. Berkas `.json` labelme juga ditulis oleh AnyLabeling desktop. Menaruh
   kunci kita sendiri di situ berarti menaruhnya di tempat yang ditulis ulang
   oleh program lain yang tidak tahu kunci itu ada.

3. Menandai seribu gambar berarti menulis seribu berkas kalau disimpan
   per-gambar. Di sini satu tulis saja.

Kuncinya nama berkas RELATIF terhadap akar projek, memakai garis miring maju.
Dataset YOLO menyimpan gambarnya di `images/train/`, jadi nama telanjang saja
bisa bertabrakan antar split.

YANG DISADARI SEJAK AWAL
------------------------
Karena kuncinya nama berkas, mengganti nama gambar di luar aplikasi ini
memutus tautannya. Aplikasi ini sendiri tidak pernah mengganti nama gambar;
yang dipindahkan hanya seluruh foldernya, dan berkas pendamping ini ikut
terbawa karena letaknya di dalam folder itu.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from ..log import catat

log = catat("labelapp.tag")

BERKAS = ".tag.json"
VERSI = 1

# Panjang nama tag dibatasi supaya tidak ada yang menempelkan satu paragraf ke
# dalam kolom yang nanti dirender sebagai pil kecil di grid.
MAKS_TAG = 40
MAKS_TAG_PER_GAMBAR = 20

_kunci = threading.Lock()


def bersihkan_tag(s: str) -> str:
    """Rapikan satu nama tag. Kosong berarti tidak sah."""
    s = " ".join(str(s or "").split())[:MAKS_TAG].strip()
    # Koma dipakai sebagai pemisah di tempat lain (tags.csv saat ekspor), jadi
    # membiarkannya di dalam nama tag membuat berkas itu tidak bisa dibaca lagi.
    return s.replace(",", " ").replace(";", " ").strip()


def _p(ds: Path) -> Path:
    return Path(ds) / BERKAS


def baca(ds: Path) -> dict:
    """Isi berkas pendamping, selalu berbentuk lengkap walau berkasnya rusak."""
    kosong = {"versi": VERSI, "gambar": {}}
    p = _p(ds)
    if not p.is_file():
        return kosong
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Berkas rusak tidak boleh menggagalkan pembukaan dataset. Tag itu
        # keterangan tambahan; kehilangannya jauh lebih ringan daripada
        # membuat seluruh projek tidak bisa dibuka.
        log.warning("berkas tag rusak, diabaikan: %s", p)
        return kosong
    if not isinstance(d, dict) or not isinstance(d.get("gambar"), dict):
        return kosong
    return {"versi": d.get("versi", VERSI), "gambar": d["gambar"]}


def _tulis(ds: Path, data: dict) -> None:
    p = _p(ds)
    tmp = p.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(p)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def kunci_gambar(ds: Path, gambar: Path) -> str:
    """Nama gambar relatif terhadap akar projek, dengan garis miring maju."""
    try:
        return Path(gambar).resolve().relative_to(Path(ds).resolve()).as_posix()
    except ValueError:
        return Path(gambar).name


def untuk(data: dict, kunci: str) -> dict:
    r = data["gambar"].get(kunci) or {}
    return {"batch": str(r.get("batch") or ""),
            "tag": [t for t in (r.get("tag") or []) if t]}


def pasang(ds: Path, kunci_gambar_daftar: list[str], *, tambah=(), buang=(),
           batch: str | None = None) -> dict:
    """
    Tambah atau buang tag pada sekumpulan gambar sekaligus.

    Satu operasi untuk banyak gambar, bukan satu per satu: menandai hasil satu
    unggahan berarti menyentuh ribuan gambar, dan seribu tulis berkas untuk
    satu klik bukan sesuatu yang pantas.
    """
    tambah = [t for t in (bersihkan_tag(x) for x in tambah) if t]
    buang = {t for t in (bersihkan_tag(x) for x in buang) if t}
    with _kunci:
        data = baca(ds)
        g = data["gambar"]
        for k in kunci_gambar_daftar:
            rec = dict(g.get(k) or {})
            tag = [t for t in (rec.get("tag") or []) if t not in buang]
            for t in tambah:
                if t not in tag:
                    tag.append(t)
            rec["tag"] = tag[:MAKS_TAG_PER_GAMBAR]
            if batch is not None:
                rec["batch"] = bersihkan_tag(batch)
            # Entri yang tidak memuat apa-apa lagi dibuang, supaya berkasnya
            # tidak tumbuh menyimpan barisan kosong selamanya.
            if rec["tag"] or rec.get("batch"):
                g[k] = rec
            else:
                g.pop(k, None)
        _tulis(ds, data)
    return {"n": len(kunci_gambar_daftar), "tag": tambah, "batch": batch}


def hitung(data: dict) -> dict:
    """Berapa gambar per tag dan per nama unggahan, untuk saringan di grid."""
    tag: dict[str, int] = {}
    batch: dict[str, int] = {}
    for rec in data["gambar"].values():
        for t in rec.get("tag") or []:
            tag[t] = tag.get(t, 0) + 1
        b = rec.get("batch")
        if b:
            batch[b] = batch.get(b, 0) + 1
    return {"tag": dict(sorted(tag.items())),
            "batch": dict(sorted(batch.items()))}


def rapikan(ds: Path, kunci_yang_ada: set[str]) -> int:
    """
    Buang catatan milik gambar yang berkasnya sudah tidak ada.

    Dipanggil saat memindai ulang. Tanpa ini, gambar yang dihapus meninggalkan
    tagnya selamanya, dan angka di saringan menghitung gambar yang tidak bisa
    ditampilkan sama sekali.
    """
    with _kunci:
        data = baca(ds)
        hilang = [k for k in data["gambar"] if k not in kunci_yang_ada]
        if not hilang:
            return 0
        for k in hilang:
            data["gambar"].pop(k, None)
        _tulis(ds, data)
    log.info("tag dirapikan: %s catatan tanpa gambar dibuang", len(hilang))
    return len(hilang)
