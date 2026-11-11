"""Penyunting template keypoint: kanvas, bukan hanya kotak teks.

Sebelum ini dialog template cuma tiga kotak teks. Bentuknya tak bisa diatur
SAMA SEKALI -- "Drop semua" menebak sendiri -- dan garis penghubung diketik
buta sebagai pasangan indeks ("0-1"). Untuk 33 titik dan 36 garis itu menuntut
orang menghafal nomor tiap titik sebelum bisa mengetik satu baris pun, dan tak
pernah melihat bentuk yang sedang dibuatnya.

Satu cacat yang ikut ketahuan: simpan() tidak pernah mengirim `tata`, jadi
menyimpan template -- bahkan hanya untuk mengganti satu nama -- diam-diam
MENGHAPUS tata letak yang sudah disusun.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from conftest import buat_dataset, masuk
from tests.test_data import PW_PAUL
from app.services import tugas as svc

AKAR = pathlib.Path(__file__).resolve().parents[1]
JS = (AKAR / "app" / "static" / "label.js").read_text()
HTML = (AKAR / "app" / "templates" / "label.html").read_text()
CSS = (AKAR / "app" / "static" / "label.css").read_text()


def test_dialog_punya_kanvas_dan_alatnya():
    assert 'id="skel-kanvas"' in HTML
    for alat in ("skel-m-tata", "skel-m-edge", "skel-snap",
                 "skel-dari-gambar", "skel-dari-contoh", "skel-reset-tata"):
        assert f'id="{alat}"' in HTML, alat


def test_kotak_teks_lama_dipertahankan():
    """Menyunting banyak edge sekaligus lewat teks kadang jauh lebih cepat;
    kanvas menambah jalan, bukan menutup yang lama."""
    for f in ("skel-titik", "skel-edge", "skel-flip", "skel-kelas"):
        assert f'id="{f}"' in HTML, f


def test_simpan_mengirim_tata():
    """Inti cacatnya: tanpa ini, menyimpan template menghapus tata letak."""
    # Diiris pada penyimpan DIALOG TEMPLATE, bukan penyimpan anotasi: nama
    # fungsinya sama persis di berkas ini, jadi yang dipakai penanda unik.
    blok = JS.split("  async function simpan(kosong) {", 1)[1][:2600]
    assert "titik, edge, flip_idx, tata" in blok
    assert "const tata =" in blok


def test_tata_hanya_dikirim_kalau_panjangnya_cocok():
    """Panjang tata WAJIB sama dengan jumlah titik, kalau tidak server
    membuangnya diam-diam dan orang menyangka tersimpan."""
    blok = JS.split("  async function simpan(kosong) {", 1)[1][:2600]
    assert "SK.tata.length !== K" in blok


def test_kanvas_memetakan_dengan_sisipan_tepi():
    """Nilai 0 dan 1 adalah tepi kotak objek. Dipetakan tepat ke pinggir
    kanvas, separuh titiknya terpotong dan tak bisa diseret lagi."""
    assert "const TEPI =" in JS
    assert JS.count("skPeta(") >= 4          # gambar, hit-test, seret, definisi


def test_mode_garis_menyambung_dan_memutus():
    blok = JS.split("function skPasangEditor", 1)[1][:3000]
    assert "SK.edge.splice(ada, 1)" in blok   # klik ulang = putus
    assert "SK.edge.push([a, b])" in blok


def test_edge_tersinkron_dua_arah():
    assert "function skTulisEdge" in JS       # kanvas -> teks
    blok = JS.split("function skPasangEditor", 1)[1][:3000]
    assert "el('skel-edge').addEventListener('input'" in blok   # teks -> kanvas


def test_menambah_titik_tak_menumpuk_di_satu_sudut():
    """Titik baru yang menumpuk di satu tempat mustahil dipisahkan lagi
    dengan tetikus."""
    blok = JS.split("function skSesuaikanPanjang", 1)[1][:800]
    assert "Math.cos" in blok and "Math.sin" in blok


def test_edge_dibuang_saat_titiknya_hilang():
    blok = JS.split("function skSesuaikanPanjang", 1)[1][:800]
    assert "SK.edge = SK.edge.filter" in blok


def test_kanvas_tak_menyeleksi_teks_saat_diseret():
    assert "user-select:none" in CSS.split("#skel-kanvas", 1)[1][:400]


# ------------------------------------------------------- sisi server
def test_server_menyimpan_tata_yang_dikirim(tmp_path):
    d = buat_dataset(tmp_path / "tpl", 1, 0)
    titik = ["a", "b", "c"]
    tata = [[0.0, 0.0], [1.0, 0.5], [0.25, 1.0]]
    sk = svc.set_skeleton(d, {"kelas": "obj", "titik": titik,
                              "edge": [[0, 1], [1, 2]], "tata": tata},
                          pemilik="paul")["skeleton"]
    assert sk["tata"] == tata
    assert sk["edge"] == [[0, 1], [1, 2]]


def test_server_membuang_tata_yang_panjangnya_salah(tmp_path):
    d = buat_dataset(tmp_path / "tpl2", 1, 0)
    sk = svc.set_skeleton(d, {"kelas": "obj", "titik": ["a", "b", "c"],
                              "edge": [], "tata": [[0.1, 0.1]]},
                          pemilik="paul")["skeleton"]
    assert sk["tata"] == []


def test_server_menjepit_tata_ke_nol_satu(tmp_path):
    d = buat_dataset(tmp_path / "tpl3", 1, 0)
    sk = svc.set_skeleton(d, {"kelas": "obj", "titik": ["a", "b"],
                              "edge": [], "tata": [[-3, 9], [0.5, 0.5]]},
                          pemilik="paul")["skeleton"]
    assert sk["tata"] == [[0.0, 1.0], [0.5, 0.5]]


def test_menyimpan_template_tak_lagi_menghapus_tata(klien, lingkungan):
    """Alur nyata yang dulu merusak: buka template, ganti satu nama, simpan."""
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_dataset(ruang / "tpl-alur", 1, 0)
    klien.post(f"/setsrc?path={d}")
    tata = [[0.0, 0.0], [1.0, 1.0]]
    svc.set_skeleton(d, {"kelas": "obj", "titik": ["a", "b"], "edge": [],
                         "tata": tata}, pemilik="paul")
    r = klien.post("/api/tugas/skeleton", json={"skeleton": {
        "kelas": "obj", "titik": ["a", "bb"], "edge": [], "flip_idx": [],
        "tata": tata}})
    j = r.json()
    assert j["ok"] is True
    assert j["skeleton"]["tata"] == tata, "tata hilang saat menyimpan template"


# ------------------------------------------------ kisi bantu kelurusan
def test_kisi_punya_tiga_tingkat():
    """Satu tingkat ketebalan tak cukup menilai kelurusan: garis seragam yang
    samar hilang di latar gelap, yang cukup terang jadi bising sampai bentuknya
    tenggelam."""
    blok = JS.split("function skGambar", 1)[1][:2600]
    assert "garis(1," in blok and "garis(5," in blok     # halus + tiap lima
    assert "W / 2" in blok and "H / 2" in blok           # sumbu tengah


def test_batas_kotak_objek_digambar():
    """Tanpa bingkai, 0..1 jadi angka tanpa tempat."""
    blok = JS.split("function skGambar", 1)[1][:2600]
    assert "strokeRect" in blok and "setLineDash" in blok


def test_titik_sebaris_ditandai_saat_diseret():
    """Kisi menunjukkan ke mana titik jatuh; ini menunjukkan apakah ia sejajar
    dengan titik yang memang harus sejajar."""
    assert "function skSorotSebaris" in JS
    blok = JS.split("function skSorotSebaris", 1)[1][:900]
    assert "setLineDash" in blok
    assert "0.5 / KISI" in blok                          # toleransi sejajar
    # Dipanggil DI PENANGAN SERET, bukan sekadar didefinisikan.
    seret = JS.split("window.addEventListener('mousemove'", 1)[1][:600]
    assert "skSorotSebaris();" in seret
