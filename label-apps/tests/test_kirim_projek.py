"""
Kirim gambar satu job ke projek LAIN (gambar saja, disalin).

Dipakai di papan Anotasi: "Kirim gambar ke projek lain". Yang dijaga: hanya
GAMBAR yang ikut (anotasi court/keypoint tak relevan di projek tujuan), sumber
tak berubah, tak pernah menimpa, dan gambar mendarat ter-tag batch supaya jadi
satu kartu di "Belum ditugaskan" tujuan.
"""
from __future__ import annotations

import pathlib

import cv2
import numpy as np

from app.services import impor, projek, tag


def _img(p: pathlib.Path, c: int) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(p), np.full((20, 30, 3), c, np.uint8))


# ------------------------------------------------------------ service
def test_salin_gambar_ke_projek_gambar_saja_tag_sumber_utuh(tmp_path, monkeypatch):
    monkeypatch.setattr(impor, "SISA_MIN_BYTE", 0)      # jangan rewel disk di CI
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    dst.mkdir(parents=True)
    imgs = []
    for i in range(3):
        p = src / f"frame-{i}.jpg"
        _img(p, 40 + i * 20)
        p.with_suffix(".json").write_text('{"shapes": []}')   # pendamping: TAK ikut
        imgs.append(p)

    r = projek.salin_gambar_ke_projek(dst, imgs, "USC-UBAYA")
    assert r["disalin"] == 3 and r["sudah_ada"] == 0
    for i in range(3):
        assert (dst / f"frame-{i}.jpg").is_file()
        assert not (dst / f"frame-{i}.json").exists()       # anotasi tak ikut
    assert all(p.is_file() for p in imgs)                   # sumber utuh
    assert all(p.with_suffix(".json").is_file() for p in imgs)

    data = tag.baca(dst)
    batches = {(rec or {}).get("batch") for rec in (data.get("gambar") or {}).values()}
    assert batches == {"USC-UBAYA"}                         # semua ter-tag batch

    # Salin lagi: isi identik -> "sudah-ada", tak menggandakan.
    r2 = projek.salin_gambar_ke_projek(dst, imgs, "USC-UBAYA")
    assert r2["disalin"] == 0 and r2["sudah_ada"] == 3


def test_salin_tolak_kalau_tak_ada_gambar(tmp_path, monkeypatch):
    monkeypatch.setattr(impor, "SISA_MIN_BYTE", 0)
    dst = tmp_path / "dst"
    dst.mkdir()
    import pytest
    with pytest.raises(projek.Tolak):
        projek.salin_gambar_ke_projek(dst, [], "B")


# ------------------------------------------------------------ route e2e
def test_kirim_job_ke_projek_lewat_route(klien, lingkungan, monkeypatch):
    monkeypatch.setattr(impor, "SISA_MIN_BYTE", 0)
    from tests.test_data import masuk, PW_PAUL
    from tests.test_projek import _projek
    from app.services import tugas

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    src = _projek(ruang, "src-kirim", n=3, label=False)
    _projek(ruang, "dst-kirim", n=1, label=False)           # tujuan sudah ada
    klien.post(f"/setsrc?path={src}")

    names = sorted(p.name for p in src.glob("*.jpg"))
    tugas.tugaskan(src, "paul", "paul", names, judul="USC-TEST")
    jid = next(k for k, t in tugas.baca(src, "paul")["tugas"].items()
               if t.get("judul") == "USC-TEST")

    j = klien.post("/api/tugas/kirim-ke-projek",
                   json={"job": jid, "tujuan": "dst-kirim", "ds": "src-kirim"}).json()
    assert j.get("ok"), j
    assert j["disalin"] == 3 and j["tujuan"] == "dst-kirim"

    dst = ruang / "dst-kirim"
    for nm in names:
        assert (dst / nm).is_file()                         # gambar sampai
    data = tag.baca(dst)
    tagged = [k for k, rec in (data.get("gambar") or {}).items()
              if (rec or {}).get("batch") == "USC-TEST"]
    assert len(tagged) == 3                                 # ter-tag batch judul job
    # sumber utuh
    assert all((src / nm).is_file() for nm in names)


def test_kirim_tolak_tujuan_sama_dengan_sumber(klien, lingkungan, monkeypatch):
    monkeypatch.setattr(impor, "SISA_MIN_BYTE", 0)
    from tests.test_data import masuk, PW_PAUL
    from tests.test_projek import _projek
    from app.services import tugas

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    src = _projek(ruang, "solo-kirim", n=2, label=False)
    klien.post(f"/setsrc?path={src}")
    names = sorted(p.name for p in src.glob("*.jpg"))
    tugas.tugaskan(src, "paul", "paul", names, judul="J")
    jid = next(iter(tugas.baca(src, "paul")["tugas"]))
    j = klien.post("/api/tugas/kirim-ke-projek",
                   json={"job": jid, "tujuan": "solo-kirim", "ds": "solo-kirim"}).json()
    assert not j.get("ok") and "sama" in (j.get("error") or "")
