"""Halaman verifikasi: "Belum diverifikasi" dan "Terverifikasi".

Sebelum ini saringannya hanya semua/belum/sudah/dicek, dan "sudah" MENCAMPUR
hasil auto-label mentah dengan yang benar-benar sudah diperiksa orang. Tidak
ada satu pun angka yang menunjukkan kemajuan verifikasi.

Dua lagi yang diperbaiki di sini:

  - Kata "perlu dicek" dipakai untuk DUA arti berbeda di aplikasi yang sama.
    Di kisi Dataset artinya anotasi yang BENTUKNYA mencurigakan menurut
    pemindai (severity "warn"); di halaman job artinya hasil AI yang belum
    diperiksa manusia. Yang kedua kini memakai kata "verifikasi".

  - Gambar sebuah unggahan yang belum dibagi jadi job tidak punya halaman kisi
    sama sekali. Jadi "Auto-label semua" bisa dijalankan sebatch, tetapi
    hasilnya tak bisa ditinjau kecuali dibagi lebih dulu.
"""
from __future__ import annotations

import json
import pathlib

from conftest import buat_dataset, masuk
from tests.test_data import PW_PAUL
from app.services import tugas


def _tulis(ip: pathlib.Path, perlu_dicek: bool) -> None:
    isi = {"version": "0.4.3", "flags": {}, "imagePath": ip.name,
           "imageHeight": 64, "imageWidth": 64,
           "shapes": [{"label": "botol", "text": "", "group_id": None,
                       "shape_type": "rectangle", "flags": {},
                       "points": [[4, 4], [20, 20]]}]}
    if perlu_dicek:
        isi["perlu_dicek"] = True
    ip.with_suffix(".json").write_text(json.dumps(isi))


# ------------------------------------------------- kisi Dataset (f=dicek/verif)
def test_kisi_dataset_punya_dua_keadaan_verifikasi(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_dataset(ruang / "verif-kisi", 3, 0)
    klien.post(f"/setsrc?path={d}")
    ips = sorted(d.glob("*.jpg"))
    _tulis(ips[0], True)
    _tulis(ips[1], False)
    klien.post("/api/tugas/dataset",
               json={"gambar": [str(p) for p in ips], "masuk": True})

    h = klien.get("/", params={"f": "dicek"}).text
    assert "Belum diverifikasi" in h and "Terverifikasi" in h


def test_saring_dicek_dan_verif_saling_melengkapi(klien, lingkungan):
    """Keduanya memecah "sudah dilabeli" tanpa tumpang tindih dan tanpa sisa."""
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_dataset(ruang / "verif-belah", 4, 0)
    klien.post(f"/setsrc?path={d}")
    ips = sorted(d.glob("*.jpg"))
    _tulis(ips[0], True)
    _tulis(ips[1], True)
    _tulis(ips[2], False)
    # ips[3] belum dilabeli
    klien.post("/api/tugas/dataset",
               json={"gambar": [str(p) for p in ips], "masuk": True})

    from app.routers import review
    from app.services import scanner
    # Dihitung langsung lewat _filter, bukan dari HTML: yang diuji aturannya.
    items, _ = scanner.scan(d)
    dicek = review._filter(items, "dicek", [])
    verif = review._filter(items, "verif", [])
    sudah = review._filter(items, "sudah", [])
    assert len(dicek) == 2 and len(verif) == 1
    assert len(dicek) + len(verif) == len(sudah)     # membelah, tak tumpang tindih
    assert not ({id(i) for i in dicek} & {id(i) for i in verif})


def test_belum_dilabeli_tak_masuk_keduanya(klien, lingkungan):
    from app.routers import review
    from app.services import scanner
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_dataset(ruang / "verif-kosong", 2, 0)
    items, _ = scanner.scan(d)
    assert review._filter(items, "dicek", []) == []
    assert review._filter(items, "verif", []) == []


# --------------------------------------------- kartu batch -> jalan verifikasi
def test_papan_menghitung_dicek_per_batch(tmp_path):
    d = buat_dataset(tmp_path / "pp", 1, 0)
    data = tugas.baca(d, "paul")
    semua = {"a.jpg", "b.jpg", "c.jpg"}
    berlabel = {"a.jpg", "b.jpg"}
    dicek = {"a.jpg"}
    bd = dict.fromkeys(semua, "unggahan-1")
    pp = tugas.papan(data, berlabel, semua, bd, dicek)
    kartu = {b["batch"]: b for b in pp["belum_batch"]}
    assert kartu["unggahan-1"]["n"] == 3
    assert kartu["unggahan-1"]["dicek"] == 1


def test_papan_tanpa_dicek_tetap_jalan(tmp_path):
    """Argumen baru opsional: pemanggil lama tak boleh pecah."""
    d = buat_dataset(tmp_path / "pp2", 1, 0)
    data = tugas.baca(d, "paul")
    pp = tugas.papan(data, set(), {"a.jpg"}, {"a.jpg": "u"})
    assert pp["belum_batch"][0]["dicek"] == 0


def test_kartu_batch_menautkan_ke_verifikasi(klien, lingkungan):
    """Tautannya memakai dua saringan yang SUDAH ada di kisi Dataset: keadaan
    (f) dan unggahan (bt). Tanpa itu, hasil auto-label sebatch tak bisa
    ditinjau sama sekali sebelum dibagi jadi job."""
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_dataset(ruang / "verif-kartu", 2, 0)
    klien.post(f"/setsrc?path={d}")
    for ip in sorted(d.glob("*.jpg")):
        _tulis(ip, True)
    h = klien.get("/anotasi", params={"ds": d.name}).text
    assert "f=dicek" in h and "Verifikasi" in h
