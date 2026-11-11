"""Tata letak "Drop semua" diambil dari pekerjaan yang sudah ada.

Keluhan nyata di prod: "Drop semua" di court-basketball menjatuhkan 33 titik
bernomor yang tidak berbentuk lapangan sama sekali. Sebabnya template projek
itu tak pernah menyimpan tata letak default (`tata` kosong), jadi kode jatuh
ke cadangan terakhir: menebar titik di sebuah ELIPS. Dibuktikan dari berkas
yang dihasilkannya -- uji elips atas 33 titiknya memberi r kuadrat rata-rata
1,003, yaitu persis di elips.

Padahal projek yang sama sudah berisi 2050 instance LENGKAP yang bentuknya
benar. Yang kurang cuma memakainya.
"""
from __future__ import annotations

import json
import pathlib
import statistics

import pytest

from conftest import buat_dataset, masuk
from tests.test_data import PW_PAUL
from app.services import tugas as svc

TITIK = ["a", "b", "c", "d"]


def _tulis(ip: pathlib.Path, titik_xy, kotak, W=200, H=100):
    sh = [{"label": "obj", "text": "", "group_id": 1, "shape_type": "rectangle",
           "flags": {}, "points": kotak}]
    for n, (x, y) in zip(TITIK, titik_xy):
        sh.append({"label": n, "text": "", "group_id": 1, "shape_type": "point",
                   "flags": {"v": 2}, "points": [[x, y]]})
    ip.with_suffix(".json").write_text(json.dumps({
        "version": "0.4.3", "flags": {}, "imagePath": ip.name,
        "imageWidth": W, "imageHeight": H, "shapes": sh}))


def _projek(tmp_path, n=5, geser=0.0):
    d = buat_dataset(tmp_path / "pose-tata", n, 0)
    for i, ip in enumerate(sorted(d.glob("*.jpg"))):
        g = geser * i
        _tulis(ip, [(20 + g, 20), (60 + g, 20), (60 + g, 60), (20 + g, 60)],
               [[10 + g, 10], [70 + g, 70]])
    return d


def test_mengembalikan_tata_dari_anotasi_nyata(tmp_path):
    d = _projek(tmp_path)
    r = svc.tata_contoh(d, TITIK)
    assert r and len(r["tata"]) == 4
    # Titik dinormalkan ke KOTAK instance, bukan ke gambar.
    for x, y in r["tata"]:
        assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0
    assert r["tata"][0] == pytest.approx([1 / 6, 1 / 6], abs=0.01)


def test_mengembalikan_kotak_khas_di_gambar(tmp_path):
    """Tanpa ini, tata letak sebagus apa pun tetap dijatuhkan di kotak 40% di
    tengah bingkai dan seluruhnya harus diperbesar manual."""
    d = _projek(tmp_path)
    r = svc.tata_contoh(d, TITIK)
    x, y, w, h = r["kotak"]
    assert x == pytest.approx(10 / 200, abs=0.01)
    assert y == pytest.approx(10 / 100, abs=0.01)
    assert w == pytest.approx(60 / 200, abs=0.01)
    assert h == pytest.approx(60 / 100, abs=0.01)


def test_memilih_instance_nyata_bukan_rata_rata(tmp_path):
    """Me-median-kan banyak instance terlihat menggoda karena meredam derau,
    tetapi tiap gambar diambil dari sudut kamera berbeda: hasilnya campuran
    beberapa perspektif dan bentuknya berhenti menyerupai lapangan. Terbukti
    saat diperiksa pada data prod."""
    d = buat_dataset(tmp_path / "pose-pilih", 3, 0)
    ips = sorted(d.glob("*.jpg"))
    # Dua bentuk yang SANGAT berbeda; rata-ratanya bukan salah satunya.
    _tulis(ips[0], [(10, 10), (90, 10), (90, 90), (10, 90)], [[0, 0], [100, 100]])
    _tulis(ips[1], [(10, 10), (90, 10), (90, 90), (10, 90)], [[0, 0], [100, 100]])
    _tulis(ips[2], [(50, 10), (90, 50), (50, 90), (10, 50)], [[0, 0], [100, 100]])
    r = svc.tata_contoh(d, TITIK)
    # Yang terpilih harus PERSIS salah satu instance, bukan campuran.
    semua = [[[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
             [[0.5, 0.1], [0.9, 0.5], [0.5, 0.9], [0.1, 0.5]]]
    assert any(all(p == pytest.approx(q, abs=0.01)
                   for p, q in zip(r["tata"], bentuk)) for bentuk in semua)


def test_instance_tak_lengkap_diabaikan(tmp_path):
    d = buat_dataset(tmp_path / "pose-sebagian", 2, 0)
    ips = sorted(d.glob("*.jpg"))
    # Satu kurang titik -> tak boleh jadi contoh.
    sh = [{"label": "obj", "group_id": 1, "shape_type": "rectangle",
           "flags": {}, "text": "", "points": [[0, 0], [100, 100]]},
          {"label": "a", "group_id": 1, "shape_type": "point", "flags": {"v": 2},
           "text": "", "points": [[10, 10]]}]
    ips[0].with_suffix(".json").write_text(json.dumps({
        "version": "0.4.3", "flags": {}, "imagePath": ips[0].name,
        "imageWidth": 200, "imageHeight": 100, "shapes": sh}))
    assert svc.tata_contoh(d, TITIK) is None

    _tulis(ips[1], [(20, 20), (60, 20), (60, 60), (20, 60)], [[10, 10], [70, 70]])
    assert svc.tata_contoh(d, TITIK) is not None


def test_tanpa_anotasi_mengembalikan_none(tmp_path):
    d = buat_dataset(tmp_path / "pose-kosong", 2, 0)
    assert svc.tata_contoh(d, TITIK) is None
    assert svc.tata_contoh(d, []) is None


def test_rute_melayani_tata_contoh(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, n=3)
    svc.set_skeleton(d, {"kelas": "obj", "titik": TITIK, "edge": []},
                     pemilik="paul")
    klien.post(f"/setsrc?path={d}")
    j = klien.get("/api/tugas/tata-contoh").json()
    assert j["ok"] is True
    assert len(j["tata"]) == 4 and len(j["kotak"]) == 4
    assert j["dari"] >= 3


def test_rute_jujur_saat_tak_ada_contoh(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_dataset(ruang / "pose-nihil", 2, 0)
    svc.set_skeleton(d, {"kelas": "obj", "titik": TITIK, "edge": []},
                     pemilik="paul")
    klien.post(f"/setsrc?path={d}")
    j = klien.get("/api/tugas/tata-contoh").json()
    assert j["ok"] is False and "instance lengkap" in j["error"]


def test_kanvas_memakai_contoh_sebelum_cincin():
    """Urutannya harus: tata default projek, lalu contoh dari anotasi, baru
    cincin. Cincin dipertahankan sebagai cadangan terakhir, bukan dibuang."""
    js = (pathlib.Path(__file__).resolve().parents[1]
          / "app" / "static" / "label.js").read_text()
    blok = js.split("async function dropTemplate", 1)[1].split("\n}", 1)[0]
    assert blok.index("skelTpl().tata") < blok.index("ambilTataContoh")
    assert blok.index("ambilTataContoh") < blok.index("Math.cos")
    # Dan kotak khasnya dipakai, bukan selalu 40% di tengah.
    assert "c.kotak" in blok and "D.W * box[0]" in blok


def test_kanvas_meminta_contoh_sekali_saja():
    js = (pathlib.Path(__file__).resolve().parents[1]
          / "app" / "static" / "label.js").read_text()
    blok = js.split("async function ambilTataContoh", 1)[1].split("\n}", 1)[0]
    assert "_tataContoh !== undefined" in blok
    assert "_tataContoh = null" in blok              # gagal pun tak diulang
