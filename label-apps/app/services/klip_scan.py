"""
Pindai klip sebuah projek VIDEO aksi menjadi item kerja — padanan scanner.py
untuk klip (klip adalah "gambar"-nya projek video).

Tata letak yang dipindai:
    klip/<batch>/<slug>_<srcid>_tNNNN.mp4

Folder internal dilewati, persis aturan repo "folder internal jangan dihitung"
(config.FOLDER_INTERNAL): `klip/_ditolak` (klip tersaring filter objek) dan
`klip/_bad`/`_sumber/_bad` (klip cacat) BUKAN data dataset, jadi tak ikut
terpindai — kalau tidak, klip yang sudah ditolak terbaca lagi sebagai kerja.

Yang dikembalikan sengaja berbentuk yang LANGSUNG dipakai RBAC & kurasi dataset
yang sudah ada, TANPA kode baru:
  - `semua`      : himpunan kunci klip (rel path) — sama peran dengan himpunan
                   kunci gambar yang dioper ke tugas.papan/di_dataset.
  - `berlabel`   : kunci klip yang sudah dilabeli (label != "").
  - `batch_dari` : {kunci -> batch}, dioper ke tugas.papan & dipakai scope
                   labeler spesifik (tugas.boleh_labeli).
  - `items`      : [{klip, rel, label, batch, srcid}] terurut, untuk papan/grid.

Kunci klip = nama relatif terhadap akar projek (klip_tag.kunci_klip), jadi ia
cocok dengan kunci di .klip.json dan dengan apa yang diharapkan
tugas.di_dataset/masukkan/keluarkan — semuanya key-agnostic.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..config import (FOLDER_INTERNAL, KLIP, KLIP_BURUK, KLIP_DITOLAK,
                       VIDEO_EXT)
from . import klip_tag

# Nama subfolder DI DALAM klip/ yang isinya bukan klip dataset dan harus
# dilewati. scanner.tersembunyi melewatkan klip/_ditolak lewat induknya (klip),
# tetapi di sini kita MULAI dari dalam klip/, jadi induk itu sudah lepas —
# subfolder internalnya harus disebut sendiri. KLIP_DITOLAK = klip tersaring
# filter objek; KLIP_BURUK = klip cacat hasil potong (juga dipakai _sumber/).
_LEWATI = set(FOLDER_INTERNAL) | {KLIP_DITOLAK, KLIP_BURUK}


def _srcid_dari_nama(stem: str) -> str:
    """Tebak srcid dari nama `<slug>_<srcid>_tNNNN` kalau sidecar tak punya.

    klip.potong menamai klip `<slug>_<srcid>_t<desidetik>`, jadi ruas tengah =
    srcid. Cuma cadangan: .klip.json yang menang kalau ada."""
    bagian = stem.split("_")
    if len(bagian) >= 3:
        return bagian[-2]
    return ""


def pindai(projek_dir: Path) -> dict:
    """Pindai `klip/` sebuah projek -> {items, semua, berlabel, batch_dari}.

    os.scandir, bukan rglob, dengan alasan yang sama seperti projek._survei:
    jenis entri terbawa dari baca folder yang sama, tanpa stat tambahan. Folder
    internal (FOLDER_INTERNAL, mis. `_ditolak`/`_bad`) dilewati PER KOMPONEN,
    jadi `klip/_ditolak/...` tak pernah ikut.
    """
    projek_dir = Path(projek_dir)
    akar_klip = projek_dir / KLIP
    data_tag = klip_tag.baca(projek_dir)

    items: list[dict] = []
    semua: set[str] = set()
    berlabel: set[str] = set()
    batch_dari: dict[str, str] = {}

    if not akar_klip.is_dir():
        return {"items": items, "semua": semua, "berlabel": berlabel,
                "batch_dari": batch_dari}

    tumpuk = [str(akar_klip)]
    while tumpuk:
        kini = tumpuk.pop()
        try:
            entri = os.scandir(kini)
        except OSError:
            continue
        with entri:
            for e in entri:
                # Folder/berkas internal atau berawalan titik dilewati seketika:
                # klip/_ditolak dan klip/_bad tak boleh terbaca sebagai kerja.
                if e.name.startswith(".") or e.name in _LEWATI:
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        tumpuk.append(e.path)
                        continue
                except OSError:
                    continue
                titik = e.name.rfind(".")
                if titik < 0 or e.name[titik:].lower() not in VIDEO_EXT:
                    continue
                kp = Path(e.path)
                rel = klip_tag.kunci_klip(projek_dir, kp)
                # batch = folder tepat di bawah klip/ (otoritatif dari tata
                # letak); label/srcid/mutu dari sidecar bila ada.
                try:
                    bagian = kp.relative_to(akar_klip).parts
                except ValueError:
                    bagian = (kp.name,)
                batch_tataletak = bagian[0] if len(bagian) >= 2 else ""
                tg = klip_tag._untuk(data_tag, rel)
                batch = tg["batch"] or batch_tataletak
                srcid = tg["srcid"] or _srcid_dari_nama(kp.stem)
                label = tg["label"]
                semua.add(rel)
                if label:
                    berlabel.add(rel)
                if batch:
                    batch_dari[rel] = batch
                items.append({"klip": kp, "rel": rel, "label": label,
                              "batch": batch, "srcid": srcid})

    items.sort(key=lambda it: it["rel"])
    return {"items": items, "semua": semua, "berlabel": berlabel,
            "batch_dari": batch_dari}


def hitung(projek_dir: Path) -> dict:
    """Ringkas jumlah klip & berapa yang sudah dilabeli (untuk lencana)."""
    r = pindai(projek_dir)
    return {"klip": len(r["semua"]), "berlabel": len(r["berlabel"])}
