"""Menyalin ke projek lain: dengan atau tanpa label, dua-duanya disengaja.

Dua jalur menyalin antarprojek dan keduanya dulu memaksakan satu pilihan:

  - gabung() menyalin SELURUH isi projek, selalu beserta anotasinya,
  - salin_gambar_ke_projek() menyalin subset, selalu TANPA anotasi.

Jadi memindahkan pekerjaan yang sudah jadi ke projek lain -- atau sebaliknya,
mengambil gambarnya saja untuk dilabeli ulang dari nol -- hanya bisa lewat
terminal. Sekarang keduanya menerima `label`.
"""
from __future__ import annotations

import json

import pytest

from conftest import buat_dataset
from app.services import projek as svc


def _label(ip, kelas="botol"):
    ip.with_suffix(".json").write_text(json.dumps({
        "version": "0.4.3", "flags": {}, "imagePath": ip.name,
        "imageHeight": 64, "imageWidth": 64,
        "shapes": [{"label": kelas, "text": "", "group_id": None,
                    "shape_type": "rectangle", "flags": {},
                    "points": [[4, 4], [20, 20]]}]}))


def _siap(tmp_path, n=3, berlabel=True):
    a = buat_dataset(tmp_path / "ruang" / "sumber", n, 0)
    b = buat_dataset(tmp_path / "ruang" / "tujuan", 0, 0)
    ips = sorted(a.glob("*.jpg"))
    if berlabel:
        for ip in ips:
            _label(ip)
    return a, b, ips


def _jml(d, sfx):
    """Berkas DATA saja. Projek punya sidecar sendiri (.tag.json, .tugas.json)
    yang namanya berawalan titik; menghitungnya membuat projek kosong pun
    tampak berisi satu anotasi."""
    return len([p for p in d.rglob("*" + sfx)
                if not p.name.startswith(".") and "_sampah" not in str(p)])


# --------------------------------------------------- subset (papan Anotasi)
def test_subset_tanpa_label_hanya_gambar(tmp_path):
    a, b, ips = _siap(tmp_path)
    r = svc.salin_gambar_ke_projek(b, ips, "batch-uji")
    assert r["disalin"] == 3
    assert _jml(b, ".jpg") == 3
    assert _jml(b, ".json") == 0
    assert r["label"] is False and r["label_ikut"] == 0


def test_subset_dengan_label_membawa_anotasinya(tmp_path):
    a, b, ips = _siap(tmp_path)
    r = svc.salin_gambar_ke_projek(b, ips, "batch-uji", label=True)
    assert r["disalin"] == 3
    assert _jml(b, ".jpg") == 3
    assert _jml(b, ".json") == 3
    assert r["label"] is True and r["label_ikut"] == 3


def test_label_mendarat_di_sebelah_gambarnya(tmp_path):
    """Label yatim di folder lain membuat gambarnya tampak belum dilabeli."""
    a, b, ips = _siap(tmp_path, 2)
    svc.salin_gambar_ke_projek(b, ips, "x", label=True)
    for p in b.rglob("*.jpg"):
        assert p.with_suffix(".json").is_file(), p


def test_isi_labelnya_utuh(tmp_path):
    a, b, ips = _siap(tmp_path, 1)
    svc.salin_gambar_ke_projek(b, ips, "x", label=True)
    salinan = next(b.rglob("*.json"))
    assert json.loads(salinan.read_text())["shapes"][0]["label"] == "botol"


def test_gambar_tanpa_label_tak_bikin_gagal(tmp_path):
    """Sebagian gambar belum dilabeli: sisanya tetap harus ikut."""
    a, b, ips = _siap(tmp_path, 3)
    ips[0].with_suffix(".json").unlink()
    r = svc.salin_gambar_ke_projek(b, ips, "x", label=True)
    assert r["disalin"] == 3 and r["label_ikut"] == 2


def test_gambar_yang_sudah_ada_labelnya_tak_menimpa(tmp_path):
    """Gambar yang sudah ada di tujuan bisa saja sudah dilabeli orang lain;
    menimpanya membuang pekerjaan yang tak kelihatan dari sini."""
    a, b, ips = _siap(tmp_path, 2)
    svc.salin_gambar_ke_projek(b, ips, "x")          # gambar saja dulu
    sasaran = next(b.rglob(ips[0].name)).with_suffix(".json")
    _label(sasaran.with_suffix(".jpg"), kelas="punya-tujuan")

    r = svc.salin_gambar_ke_projek(b, ips, "x", label=True)
    assert r["disalin"] == 0 and r["sudah_ada"] == 2
    assert json.loads(sasaran.read_text())["shapes"][0]["label"] == "punya-tujuan"


# ------------------------------------------------------ seluruh projek
def test_gabung_bawaannya_tetap_membawa_label(tmp_path):
    """Perilaku lama harus bertahan persis untuk pemanggil yang tak memilih."""
    a, b, ips = _siap(tmp_path)
    r = svc.gabung(a.parent, a.name, b.name)
    assert r["label"] is True
    assert _jml(b, ".jpg") == 3 and _jml(b, ".json") == 3


def test_gabung_tanpa_label_hanya_gambar(tmp_path):
    a, b, ips = _siap(tmp_path)
    r = svc.gabung(a.parent, a.name, b.name, label=False)
    assert r["label"] is False
    assert _jml(b, ".jpg") == 3
    assert _jml(b, ".json") == 0


def test_gabung_tanpa_label_juga_melewati_txt_yolo(tmp_path):
    """ANN_EXT bukan cuma .json: ekspor YOLO memakai .txt."""
    a, b, ips = _siap(tmp_path, 2, berlabel=False)
    for ip in ips:
        ip.with_suffix(".txt").write_text("0 0.5 0.5 0.2 0.2\n")
    svc.gabung(a.parent, a.name, b.name, label=False)
    assert _jml(b, ".jpg") == 2 and _jml(b, ".txt") == 0


def test_sumber_tak_pernah_berubah(tmp_path):
    a, b, ips = _siap(tmp_path, 2)
    sebelum = sorted(p.name for p in a.rglob("*") if p.is_file())
    svc.salin_gambar_ke_projek(b, ips, "x", label=True)
    svc.gabung(a.parent, a.name, b.name, label=False)
    assert sorted(p.name for p in a.rglob("*") if p.is_file()) == sebelum


# ------------------------------------------------------------- lewat rute
def test_rute_kirim_ke_projek_membawa_label(klien, lingkungan, monkeypatch):
    """Dari HTTP sampai disk, bukan cuma lapisan layanannya."""
    import pathlib
    from app.services import impor, tugas
    from tests.test_data import masuk, PW_PAUL
    from tests.test_projek import _projek

    monkeypatch.setattr(impor, "SISA_MIN_BYTE", 0)
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    src = _projek(ruang, "src-lab", n=2, label=False)
    _projek(ruang, "dst-lab", n=0, label=False)
    for ip in sorted(src.glob("*.jpg")):
        _label(ip, kelas="kaleng")
    klien.post(f"/setsrc?path={src}")

    nama = sorted(p.name for p in src.glob("*.jpg"))
    tugas.tugaskan(src, "paul", "paul", nama, judul="BAWA-LABEL")
    jid = next(k for k, t in tugas.baca(src, "paul")["tugas"].items()
               if t.get("judul") == "BAWA-LABEL")

    j = klien.post("/api/tugas/kirim-ke-projek",
                   json={"job": jid, "tujuan": "dst-lab", "ds": "src-lab",
                         "label": True}).json()
    assert j.get("ok"), j
    assert j["disalin"] == 2 and j["label_ikut"] == 2
    dst = ruang / "dst-lab"
    for nm in nama:
        lab = (dst / nm).with_suffix(".json")
        assert lab.is_file(), lab
        assert json.loads(lab.read_text())["shapes"][0]["label"] == "kaleng"


def test_rute_kirim_bawaannya_tetap_tanpa_label(klien, lingkungan, monkeypatch):
    """Body tanpa `label` harus berarti sama seperti sebelum fitur ini ada."""
    import pathlib
    from app.services import impor, tugas
    from tests.test_data import masuk, PW_PAUL
    from tests.test_projek import _projek

    monkeypatch.setattr(impor, "SISA_MIN_BYTE", 0)
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    src = _projek(ruang, "src-lab2", n=2, label=False)
    _projek(ruang, "dst-lab2", n=0, label=False)
    for ip in sorted(src.glob("*.jpg")):
        _label(ip)
    klien.post(f"/setsrc?path={src}")
    nama = sorted(p.name for p in src.glob("*.jpg"))
    tugas.tugaskan(src, "paul", "paul", nama, judul="TANPA")
    jid = next(k for k, t in tugas.baca(src, "paul")["tugas"].items()
               if t.get("judul") == "TANPA")

    j = klien.post("/api/tugas/kirim-ke-projek",
                   json={"job": jid, "tujuan": "dst-lab2", "ds": "src-lab2"}).json()
    assert j.get("ok"), j
    assert j["label_ikut"] == 0
    dst = ruang / "dst-lab2"
    assert _jml(dst, ".json") == 0


def test_rute_gabung_menerima_label_nol(klien, lingkungan, monkeypatch):
    import pathlib
    from app.services import impor
    from tests.test_data import masuk, PW_PAUL
    from tests.test_projek import _projek

    monkeypatch.setattr(impor, "SISA_MIN_BYTE", 0)
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    src = _projek(ruang, "g-src", n=2, label=False)
    _projek(ruang, "g-dst", n=0, label=False)
    for ip in sorted(src.glob("*.jpg")):
        _label(ip)

    # "0" persis seperti yang dikirim antarmuka (URLSearchParams).
    j = klien.post("/api/projek/gabung?sumber=g-src&tujuan=g-dst&label=0").json()
    assert j.get("ok"), j
    assert j["label"] is False
    dst = ruang / "g-dst"
    assert _jml(dst, ".jpg") == 2 and _jml(dst, ".json") == 0
