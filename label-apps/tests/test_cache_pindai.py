"""Cache hasil pindai persisten + rekonsiliasi inkremental.

Membuka projek besar memindai SEMUA gambar (~10 dtk di 30rb). Cache menyimpan
hasilnya; pada buka berikutnya, hanya gambar yang ISINYA berubah yang dibaca
ulang, sisanya dipakai ulang -> jauh lebih cepat walau projek sedang dilabeli.

Yang DIJAGA paling ketat: hasil rekonsiliasi (cache + baca-ulang-yang-berubah)
IDENTIK dengan pindai penuh, termasuk saat anotasi diedit, gambar ditambah, dan
gambar dihapus. Kalau tidak, pengguna bisa melihat anotasi basi -- itu rusak."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from app.config import get_settings
from app.services import scanner
from app import session as S
from conftest import buat_dataset


def _pts(s):
    p = s.get("pts")
    if p is None:
        p = s.get("points") or []
    try:
        return tuple((round(float(a), 2), round(float(b), 2))
                     for a, b in np.asarray(p, float).reshape(-1, 2))
    except Exception:
        return ()


def _ringkas(items):
    """Ringkasan kanonik per item untuk membandingkan dua hasil pindai."""
    out = []
    for it in items:
        shapes = sorted((str(s.get("label")), _pts(s)) for s in it["shapes"])
        out.append((it["img"].name, it.get("split", ""), it["W"], it["H"],
                    tuple(it["issues"]), tuple(shapes)))
    return sorted(out)


def test_rekonsiliasi_inkremental_identik_dengan_pindai_penuh(lingkungan, monkeypatch):
    """Inti keamanan: cache + baca-ulang-yang-berubah == pindai penuh."""
    monkeypatch.setattr(S, "_CACHE_PINDAI_MIN", 0)
    settings = get_settings()
    d = Path(lingkungan["roots"]) / "rec-proj"
    buat_dataset(d, 8, 5)

    # 1) buka pertama -> tulis cache
    s1 = S.Session("paul", settings)
    s1.load(d)
    assert S._berkas_cache_pindai(settings, d.resolve()).exists()

    # 2) MUTASI campuran: edit 1 anotasi, tambah 1 gambar+anotasi, hapus 1 gambar
    jp = sorted(d.glob("*.json"))[0]
    dd = json.loads(jp.read_text())
    dd["shapes"].append({"label": "tetra", "shape_type": "polygon",
                         "points": [[10, 10], [40, 10], [40, 40]]})
    jp.write_text(json.dumps(dd))

    buat_dataset(d / "_tmp", 1, 1)                      # bikin 1 pasang gambar+json
    import shutil
    src_extra = sorted((d / "_tmp").glob("*.jpg"))[0]
    (d / "baru.jpg").write_bytes(src_extra.read_bytes())
    ej = src_extra.with_suffix(".json")
    (d / "baru.json").write_text(ej.read_text().replace(ej.stem, "baru"))
    shutil.rmtree(d / "_tmp")

    hapus = sorted(d.glob("rec-proj-0*.jpg"))[-1]       # buang 1 gambar
    hapus.unlink()
    hapus.with_suffix(".json").unlink(missing_ok=True)

    # 3) rekonsiliasi lewat Session.load (pakai cache + peta) ...
    s2 = S.Session("anggi", settings)
    inkremental = s2.load(d)
    # 4) ... vs pindai penuh langsung
    penuh, _ = scanner.scan(d)

    assert _ringkas(inkremental) == _ringkas(penuh), \
        "rekonsiliasi inkremental TIDAK identik dengan pindai penuh"
    # perubahan benar-benar terbaca (bukan dilayani basi)
    it = s2.find(str(jp.with_suffix(".jpg")))
    assert sum(1 for x in it["shapes"]) == len(dd["shapes"])
    assert s2.find(str(d / "baru.jpg")) is not None       # gambar baru masuk
    assert s2.find(str(hapus)) is None                    # gambar hapus hilang


def test_cache_dipakai_ulang_tanpa_perubahan(lingkungan, monkeypatch):
    monkeypatch.setattr(S, "_CACHE_PINDAI_MIN", 0)
    settings = get_settings()
    d = Path(lingkungan["roots"]) / "stabil-proj"
    buat_dataset(d, 6, 4)
    a = S.Session("paul", settings).load(d)
    b = S.Session("anggi", settings).load(d)            # tanpa perubahan -> dari cache
    assert _ringkas(a) == _ringkas(b)


def test_projek_kecil_tak_di_cache(lingkungan):
    settings = get_settings()
    d = Path(lingkungan["roots"]) / "kecil-proj"
    buat_dataset(d, 3, 2)
    S.Session("paul", settings).load(d)                 # ambang bawaan 2000
    assert not S._berkas_cache_pindai(settings, d.resolve()).exists()
