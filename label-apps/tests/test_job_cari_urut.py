"""Halaman job: cari nama berkas dan pemilih urutan.

Halaman INILAH yang bisa berisi 11 ribu gambar, dan sampai sekarang ia
satu-satunya kisi tanpa keduanya: urutannya dipaku nama naik, dan Ctrl+F
peramban cuma mengenai 50 ubin yang sedang tampil -- justru yang diperingatkan
panduannya sendiri.

Yang juga dijaga: panah kanvas mengikuti urutan dan pencarian itu. Kalau tidak,
"berikutnya" melompat ke gambar yang tidak bertetangga di layar, persis keluhan
yang sudah diperbaiki untuk kisi Dataset.
"""
from __future__ import annotations

import json
import pathlib
import re
import urllib.parse as up

from conftest import buat_dataset, masuk
from tests.test_data import PW_PAUL


def _job(klien, ruang, nama="job-cari", n=6):
    d = buat_dataset(ruang / nama, n, 0)
    klien.post(f"/setsrc?path={d}")
    ips = sorted(d.glob("*.jpg"))
    # Objek berbeda-beda supaya urutan "objek terbanyak" bisa diuji.
    for i, ip in enumerate(ips):
        ip.with_suffix(".json").write_text(json.dumps({
            "version": "0.4.3", "flags": {}, "imagePath": ip.name,
            "imageHeight": 64, "imageWidth": 64,
            "shapes": [{"label": "botol", "text": "", "group_id": None,
                        "shape_type": "rectangle", "flags": {},
                        "points": [[4 + j, 4], [20 + j, 20]]}
                       for j in range(i + 1)]}))
    klien.get("/")
    tid = klien.post("/api/tugas/bagi",
                     json={"pelabel": "paul",
                           "gambar": [str(p) for p in ips]}).json()["id"]
    return d, ips, tid


def _kisi(h: str) -> list[str]:
    blok = h.split('id="jb-kisi"', 1)[1]
    return [pathlib.Path(up.unquote(x)).name
            for x in re.findall(r'/label\?path=([^"&]+)', blok)]


def test_kontrol_cari_dan_urut_ada(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d, ips, tid = _job(klien, ruang)
    h = klien.get(f"/tugas/{tid}", params={"ds": d.name}).text
    assert 'id="jb-cari"' in h and 'id="jb-urut"' in h
    assert "Objek terbanyak" in h                 # daftar urutan yang sama


def test_cari_mempersempit_kisi(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d, ips, tid = _job(klien, ruang)
    target = ips[2].name
    h = klien.get(f"/tugas/{tid}",
                  params={"ds": d.name, "q": target[:-4]}).text
    assert _kisi(h) == [target]


def test_urutan_benar_benar_mengubah_kisi(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d, ips, tid = _job(klien, ruang)
    naik = _kisi(klien.get(f"/tugas/{tid}", params={"ds": d.name}).text)
    banyak = _kisi(klien.get(f"/tugas/{tid}",
                             params={"ds": d.name, "s": "objek-banyak"}).text)
    assert naik == sorted(naik)
    assert banyak[0] == ips[-1].name              # objek terbanyak di depan
    assert banyak != naik


def test_cari_tak_membuang_saringan_lain(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d, ips, tid = _job(klien, ruang)
    h = klien.get(f"/tugas/{tid}",
                  params={"ds": d.name, "saring": "sudah", "q": "job"}).text
    form = h[h.index('class="jb-cari"'):h.index("</form>", h.index('class="jb-cari"'))]
    assert 'name="saring" value="sudah"' in form


def test_panah_kanvas_mengikuti_urutan_job(klien, lingkungan):
    """Mengubah urutan di kisi job harus mengubah tetangga di kanvas juga."""
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d, ips, tid = _job(klien, ruang)
    h = klien.get(f"/tugas/{tid}",
                  params={"ds": d.name, "s": "objek-banyak"}).text
    urut = _kisi(h)
    tautan = re.findall(r'href="(/label\?path=[^"]+)"',
                        h.split('id="jb-kisi"', 1)[1])[0].replace("&amp;", "&")
    assert "s=objek-banyak" in tautan
    teks = klien.get(tautan).text
    m = re.search(r'"next"\s*:\s*"([^"]+)"', teks)
    assert m, "tetangga berikutnya tak ada"
    assert pathlib.Path(up.unquote(m.group(1))).name == urut[1]


def test_panah_kanvas_mengikuti_pencarian_job(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d, ips, tid = _job(klien, ruang)
    h = klien.get(f"/tugas/{tid}", params={"ds": d.name, "q": "job-cari-0"}).text
    urut = _kisi(h)
    tautan = re.findall(r'href="(/label\?path=[^"]+)"',
                        h.split('id="jb-kisi"', 1)[1])[0].replace("&amp;", "&")
    assert "q=job-cari-0" in tautan
    teks = klien.get(tautan).text
    m = re.search(r'id="lab-urut-n"[^>]*>\s*(\d+)\s*/\s*(\d+)', teks)
    assert m and int(m.group(2)) == len(urut), (m.group(0) if m else None, urut)
