"""Hitungan kelas: satu kolom angka tidak boleh berarti dua satuan.

Menu saringan kisi Dataset menampilkan "botol 842" lalu hasilnya "310 gambar",
dan tak ada apa pun di layar yang menjelaskan kenapa. Sebabnya angka itu
jumlah OBJEK sementara saringannya bekerja per GAMBAR. Lebih buruk, dua baris
tepat di atasnya di daftar yang SAMA (Latar, Belum dilabeli) memang hitungan
gambar: satu kolom, dua satuan, tanpa satuan ditulis.

Halaman job menghitung per gambar, halaman Lihat per instance. Kontrol yang
terlihat identik karena itu menyebut tiga hal berbeda di tiga halaman.
"""
from __future__ import annotations

import json
import pathlib
import re

from conftest import buat_dataset, masuk
from tests.test_data import PW_PAUL
from app.services import scanner


def _proj(klien, ruang, nama):
    """3 gambar: masing-masing 1, 2, dan 3 objek berkelas 'botol'."""
    d = buat_dataset(ruang / nama, 3, 0)
    klien.post(f"/setsrc?path={d}")
    for i, ip in enumerate(sorted(d.glob("*.jpg"))):
        ip.with_suffix(".json").write_text(json.dumps({
            "version": "0.4.3", "flags": {}, "imagePath": ip.name,
            "imageHeight": 64, "imageWidth": 64,
            "shapes": [{"label": "botol", "text": "", "group_id": None,
                        "shape_type": "rectangle", "flags": {},
                        "points": [[4 + j, 4], [20 + j, 20]]}
                       for j in range(i + 1)]}))
    klien.get("/")
    return d


def test_menu_kelas_menyebut_gambar_dan_objek(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    _proj(klien, ruang, "hk-menu")
    h = klien.get("/").text
    assert '3 gambar memuat kelas ini">3</b>' in h
    assert '6 objek berkelas ini">6</em>' in h
    # Kepala kolomnya menyebutkan satuan keduanya.
    assert "<b>gbr</b><em>objek</em>" in h


def test_ringkasan_hasil_menyebut_satuan(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    _proj(klien, ruang, "hk-ringkas")
    h = " ".join(klien.get("/").text.split())
    assert "<b>3 gambar</b>" in h
    assert "6 objek" in h


def test_baris_tanpa_kelas_juga_bersatuan(klien, lingkungan):
    """Latar dan Belum dilabeli memang hitungan GAMBAR; satuannya ditulis
    supaya tidak terbaca sebagai hitungan objek seperti baris di bawahnya."""
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    _proj(klien, ruang, "hk-tanpa")
    h = klien.get("/").text
    assert "<small>gbr</small>" in h


# ------------------------------------------------- pelipatan instance pose
def _pose_item(n_titik=33):
    bentuk = [{"label": "court", "type": "rectangle", "group_id": 1,
               "pts": [[0, 0], [10, 10]]}]
    bentuk += [{"label": str(i + 1).zfill(2), "type": "point", "group_id": 1,
                "pts": [[i, i]]} for i in range(n_titik)]
    return {"shapes": bentuk}


def test_instance_pose_dilipat_jadi_satu_objek():
    """Tanpa pelipatan, satu gambar court berisi SATU objek dihitung 34 objek
    dan mendaftar 33 "kelas" bernama slot titik 01..33."""
    hitung, n = scanner.objek_per_kelas(_pose_item())
    assert n == 1
    assert hitung == {"court": 1}


def test_instance_tanpa_kotak_jatuh_ke_kelas_skeleton():
    it = {"shapes": [{"label": "07", "type": "point", "group_id": 3,
                      "pts": [[1, 1]]}]}
    assert scanner.objek_per_kelas(it, "court")[0] == {"court": 1}
    assert scanner.objek_per_kelas(it)[0] == {"objek": 1}


def test_bentuk_biasa_tetap_dihitung_per_bentuk():
    it = {"shapes": [{"label": "botol", "type": "rectangle", "group_id": None,
                      "pts": []},
                     {"label": "botol", "type": "polygon", "group_id": None,
                      "pts": []},
                     {"label": "kaleng", "type": "rectangle", "group_id": None,
                      "pts": []}]}
    hitung, n = scanner.objek_per_kelas(it)
    assert n == 3 and hitung == {"botol": 2, "kaleng": 1}


def test_kisi_dan_lihat_memakai_aturan_yang_sama():
    """Dulu aturan pelipatan hanya hidup di rute /view, jadi satu projek
    menyebut dua angka berbeda untuk gambar yang sama di dua halaman."""
    akar = pathlib.Path(__file__).resolve().parents[1]
    r = (akar / "app" / "routers" / "review.py").read_text()
    t = (akar / "app" / "routers" / "tugas.py").read_text()
    k = (akar / "app" / "services" / "konteks_kisi.py").read_text()
    assert "scanner.objek_per_kelas(" in r                 # halaman Lihat
    assert "scanner.objek_per_kelas(" in t                 # halaman job
    # Kisi Dataset lewat konteks_kisi, yang juga yang menyaring dan menghitung
    # facet: satu tempat, jadi angka di menu dan isi kisi tak bisa berbeda.
    assert "scanner.objek_per_kelas(" in k
    assert "konteks_kisi.fakta(" in r


def test_kontras_angka_saringan_bukan_ink_faint():
    """Angka itulah yang paling sering dibaca di menu saringan, dan dengan
    --ink-faint kontrasnya sekitar 2,4:1 pada tema terang di ukuran 11px."""
    css = (pathlib.Path(__file__).resolve().parents[1]
           / "app" / "static" / "app.css").read_text()
    blok = css[css.index(".kelas-baris b{"):]
    blok = blok[:blok.index("}")]
    assert "--ink-dim" in blok and "--ink-faint" not in blok
