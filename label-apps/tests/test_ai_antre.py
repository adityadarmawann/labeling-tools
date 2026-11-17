"""Satu antrean untuk semua pekerjaan AI yang memakai GPU.

Dulu ada TIGA semaphore terpisah (pre-label, zero-shot, gabungan) dan DUA peta
"satu job per akun" (satu per router). Akibatnya satu orang bisa menjalankan
pre-label dan auto-label bersamaan: dua model ultralytics, plus SAM/YOLO-World
dan GroundingDINO kalau ensemble, termuat di GPU yang sama pada saat yang sama.
Pada kartu 8 GB itu jalan terpendek menuju kehabisan VRAM di tengah pekerjaan
orang, dan yang gagal belum tentu yang belakangan dimulai.
"""
from __future__ import annotations

import pytest

from conftest import buat_dataset, masuk
from tests.test_data import PW_PAUL
from app.services import ai_antre, autolabel_gabung, autolabel_zero, prelabel


def test_semua_jalur_berbagi_satu_antrean():
    g = ai_antre.giliran
    assert prelabel._giliran is g
    assert autolabel_zero._giliran is g
    assert autolabel_gabung._giliran is g


def test_klaim_satu_per_akun_lintas_fitur():
    ai_antre.lepas("uji")
    assert ai_antre.klaim("uji", "pre-label") is None
    # Fitur LAIN pun ditolak, dan yang dikembalikan nama pemegangnya -- dulu
    # pesannya menyebut fitur yang keliru.
    assert ai_antre.klaim("uji", "auto-label") == "pre-label"
    assert ai_antre.sedang_jalan("uji") == "pre-label"
    ai_antre.lepas("uji", "pre-label")
    assert ai_antre.sedang_jalan("uji") == ""
    assert ai_antre.klaim("uji", "auto-label") is None
    ai_antre.lepas("uji")


def test_lepas_tak_mencabut_klaim_milik_job_lain():
    """Job lama yang selesai belakangan tak boleh mencabut klaim job baru."""
    ai_antre.lepas("uji2")
    ai_antre.klaim("uji2", "pre-label")
    ai_antre.lepas("uji2", "auto-label")            # bukan pemegangnya
    assert ai_antre.sedang_jalan("uji2") == "pre-label"
    ai_antre.lepas("uji2", "pre-label")
    assert ai_antre.sedang_jalan("uji2") == ""


def test_akun_berbeda_tidak_saling_mengunci():
    ai_antre.lepas("a"); ai_antre.lepas("b")
    assert ai_antre.klaim("a", "pre-label") is None
    assert ai_antre.klaim("b", "auto-label") is None
    ai_antre.lepas("a"); ai_antre.lepas("b")


def test_serentak_ambil_yang_terkecil(monkeypatch):
    """Env lama masih dibaca supaya pemasangan lama tak berubah diam-diam, dan
    yang TERKECIL menang: menaikkan batas harus disengaja."""
    monkeypatch.setenv("LABELAPP_PRELABEL_SERENTAK", "4")
    monkeypatch.setenv("LABELAPP_AUTOLABEL_SERENTAK", "2")
    assert ai_antre._env("LABELAPP_PRELABEL_SERENTAK",
                         "LABELAPP_AUTOLABEL_SERENTAK") == 2
    monkeypatch.delenv("LABELAPP_PRELABEL_SERENTAK")
    monkeypatch.delenv("LABELAPP_AUTOLABEL_SERENTAK")
    assert ai_antre._env("LABELAPP_TAK_ADA") == 1


def test_rute_autolabel_ditolak_saat_prelabel_jalan(klien, lingkungan):
    """Penolakan lintas fitur sampai ke antarmuka, dengan nama yang benar."""
    masuk(klien, "paul", PW_PAUL)
    d = lingkungan["ruang"] / "antre1"
    buat_dataset(d, 2, 0)
    klien.post(f"/setsrc?path={d}")
    ai_antre.lepas("paul")
    ai_antre.klaim("paul", "pre-label")
    try:
        r = klien.post("/api/autolabel/mulai",
                       json={"batch": "", "sumber": [{"jenis": "terlatih",
                                                      "nomor": 1}]})
        j = r.json()
        assert j["ok"] is False
        assert "pre-label" in j["error"]
    finally:
        ai_antre.lepas("paul")


def test_klaim_tak_tertinggal_saat_permintaan_ditolak(klien, lingkungan):
    """Jalur penolakan isi-bodi TIDAK boleh meninggalkan klaim: kalau
    tertinggal, akun itu terkunci dari semua fitur AI sampai proses dimatikan."""
    masuk(klien, "paul", PW_PAUL)
    d = lingkungan["ruang"] / "antre2"
    buat_dataset(d, 2, 0)
    klien.post(f"/setsrc?path={d}")
    ai_antre.lepas("paul")
    r = klien.post("/api/autolabel/mulai", json={"batch": "", "sumber": [{}]})
    assert r.json()["ok"] is False                   # tak ada sumber yang sah
    assert ai_antre.sedang_jalan("paul") == ""       # dan klaimnya bersih
