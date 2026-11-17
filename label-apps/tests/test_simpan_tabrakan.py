"""Simpan tidak boleh menimpa tulisan orang lain tanpa sepatah kata.

/api/simpan menulis ulang SELURUH berkas dari apa yang dikirim peramban, tanpa
membandingkan apa pun dengan keadaan di disk -- padahal datanya sudah ada di
kedua sisi: server mengirim `disunting` saat gambar dibuka, kanvas
menyimpannya, dan tak satu pun memakainya.

Yang bisa bertabrakan sah semua: pemilik dan pelabel yang ditugaskan, dua
Editor, Labeler berakses menyeluruh, satu orang dengan dua tab, atau jalur
auto-label yang menulis di latar. Yang kalah bahkan tidak tahu pernah ada yang
menulis: tidak ada toast, tidak ada konflik, tidak ada cadangan. Dan autosave
membuatnya terjadi tanpa seorang pun menekan apa pun.
"""
from __future__ import annotations

import json
import pathlib
import time

from conftest import buat_dataset, masuk
from tests.test_data import PW_PAUL

BENTUK = [{"label": "botol", "shape_type": "rectangle",
           "points": [[4, 4], [20, 20]], "text": "", "group_id": None,
           "flags": {}, "titipan": {}}]


def _siap(klien, lingkungan, nama):
    masuk(klien, "paul", PW_PAUL)
    d = buat_dataset(lingkungan["ruang"] / nama, 1, 0)
    klien.post(f"/setsrc?path={d}")
    return d, sorted(d.glob("*.jpg"))[0]


def test_simpan_pertama_mengembalikan_cap(klien, lingkungan):
    d, ip = _siap(klien, lingkungan, "tbr-cap")
    j = klien.post("/api/simpan",
                   json={"path": str(ip), "shapes": BENTUK, "dasar": 0}).json()
    assert j["ok"] is True
    assert j.get("disunting"), j


def test_simpan_ditolak_kalau_disk_lebih_baru(klien, lingkungan):
    d, ip = _siap(klien, lingkungan, "tbr-tolak")
    pertama = klien.post("/api/simpan",
                         json={"path": str(ip), "shapes": BENTUK,
                               "dasar": 0}).json()
    assert pertama["ok"] is True
    cap = pertama["disunting"]

    # Orang lain menulis sesudahnya.
    time.sleep(1.1)
    klien.post("/api/simpan", json={"path": str(ip), "shapes": BENTUK,
                                    "dasar": cap})

    # Tab lama masih memegang cap yang pertama.
    j = klien.post("/api/simpan",
                   json={"path": str(ip), "shapes": [], "dasar": cap}).json()
    assert j["ok"] is False and j.get("konflik") is True, j
    assert "berubah di disk" in j["error"]
    # Dan berkasnya TIDAK tersentuh: bentuknya masih ada.
    isi = json.loads(ip.with_suffix(".json").read_text())
    assert len(isi["shapes"]) == 1


def test_simpan_berturut_turut_oleh_orang_yang_sama_lancar(klien, lingkungan):
    """Cap baru dikembalikan tiap simpan; tanpa itu simpanan kedua orang yang
    sama membandingkan dirinya dengan simpanan pertamanya sendiri."""
    d, ip = _siap(klien, lingkungan, "tbr-lancar")
    dasar = 0
    for _ in range(3):
        j = klien.post("/api/simpan",
                       json={"path": str(ip), "shapes": BENTUK,
                             "dasar": dasar}).json()
        assert j["ok"] is True, j
        dasar = j["disunting"]
        time.sleep(1.1)


def test_tanpa_dasar_tetap_diterima(klien, lingkungan):
    """Kanvas versi lama di tab yang belum dimuat ulang tak boleh terkunci."""
    d, ip = _siap(klien, lingkungan, "tbr-lama")
    klien.post("/api/simpan", json={"path": str(ip), "shapes": BENTUK})
    time.sleep(1.1)
    j = klien.post("/api/simpan",
                   json={"path": str(ip), "shapes": BENTUK}).json()
    assert j["ok"] is True


def test_kanvas_mengirim_dan_memperbarui_dasar():
    js = (pathlib.Path(__file__).resolve().parents[1]
          / "app" / "static" / "label.js").read_text()
    assert "dasar: S.dasarDisunting" in js
    assert "if (j.disunting) S.dasarDisunting = j.disunting;" in js
    assert "j.konflik" in js
    # Gambar PERTAMA dirender server sebagai HTML dan tak lewat label-data.
    assert "lab-jejak" in js
