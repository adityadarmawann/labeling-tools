"""Membuang hasil auto-label yang belum diverifikasi, sebatch atau sejob.

Dilaporkan dari prod: auto-label dijalankan dengan semua sumber dicentang,
hasilnya melabeli latar juga, dan 905 berkas berisi 2512 bentuk keliru
tertinggal. Membetulkannya satu per satu jauh lebih lama daripada
menjalankan ulang dengan resep yang berbeda, tetapi tak ada satu pun jalan
di antarmuka untuk membuangnya.

Yang dijaga di sini terutama bukan bahwa ia membuang, melainkan bahwa ia
TIDAK membuang pekerjaan manusia.
"""
from __future__ import annotations

import json
import pathlib

from conftest import buat_dataset, masuk
from tests.test_data import PW_PAUL
from app.services import scanner, tugas


def _tulis(ip: pathlib.Path, *, ai=False, pelabel="", n=1):
    isi = {"version": "0.4.3", "flags": {}, "imagePath": ip.name,
           "imageHeight": 64, "imageWidth": 64,
           "shapes": [{"label": "botol", "text": "", "group_id": None,
                       "shape_type": "rectangle", "flags": {},
                       "points": [[4, 4], [20, 20]]} for _ in range(n)]}
    if ai:
        isi["perlu_dicek"] = True
    if pelabel:
        isi["pelabel"] = pelabel
    ip.with_suffix(".json").write_text(json.dumps(isi))


def _items(d):
    return scanner.scan(d)[0]


# ------------------------------------------------------- yang dibuang benar
def test_membuang_hasil_ai_yang_belum_diverifikasi(tmp_path):
    d = buat_dataset(tmp_path / "b1", 3, 0)
    ips = sorted(d.glob("*.jpg"))
    _tulis(ips[0], ai=True, n=2)
    _tulis(ips[1], ai=True, n=3)
    r = tugas.buang_hasil_ai(d, _items(d))
    assert r["dibuang"] == 2 and r["bentuk"] == 5
    assert not ips[0].with_suffix(".json").exists()
    assert not ips[1].with_suffix(".json").exists()


def test_tak_menyentuh_yang_sudah_diverifikasi(tmp_path):
    """Begitu manusia menyimpannya, tandanya hilang dan berkas itu bukan lagi
    hasil AI mentah melainkan pekerjaan orang."""
    d = buat_dataset(tmp_path / "b2", 2, 0)
    ips = sorted(d.glob("*.jpg"))
    _tulis(ips[0], ai=True)
    _tulis(ips[1])                       # sudah diverifikasi: tanpa perlu_dicek
    tugas.buang_hasil_ai(d, _items(d))
    assert ips[1].with_suffix(".json").exists()


def test_tak_menyentuh_yang_pernah_disentuh_manusia(tmp_path):
    """Penjaga KEDUA, dan ini yang menyelamatkan kalau tandanya sempat salah:
    nama pelabel ditulis jalur simpan manusia, jadi keberadaannya berarti
    gambar ini pernah disentuh orang betapapun tandanya berbunyi."""
    d = buat_dataset(tmp_path / "b3", 2, 0)
    ips = sorted(d.glob("*.jpg"))
    _tulis(ips[0], ai=True, pelabel="darma")
    _tulis(ips[1], ai=True)
    r = tugas.buang_hasil_ai(d, _items(d))
    assert r["dibuang"] == 1 and r["dilindungi"] == 1
    assert ips[0].with_suffix(".json").exists()      # selamat
    assert not ips[1].with_suffix(".json").exists()


def test_gambar_tak_ikut_terhapus(tmp_path):
    """Bedanya dengan "Hapus batch": yang itu membuang GAMBARNYA."""
    d = buat_dataset(tmp_path / "b4", 2, 0)
    ips = sorted(d.glob("*.jpg"))
    _tulis(ips[0], ai=True)
    tugas.buang_hasil_ai(d, _items(d))
    assert all(p.exists() for p in ips)


# ------------------------------------------------------------ bisa kembali
def test_berkasnya_dipindah_bukan_dihapus(tmp_path):
    d = buat_dataset(tmp_path / "b5", 1, 0)
    ip = sorted(d.glob("*.jpg"))[0]
    _tulis(ip, ai=True, n=2)
    r = tugas.buang_hasil_ai(d, _items(d))
    simpanan = d / r["folder"]
    assert simpanan.is_dir()
    berkas = list((simpanan / "isi").glob("*.json"))
    assert len(berkas) == 1
    assert len(json.loads(berkas[0].read_text())["shapes"]) == 2
    assert (simpanan / "CATATAN.txt").is_file()


def test_simpanannya_tak_terbaca_pemindai(tmp_path):
    """Kalau terbaca, anotasi yang dibuang muncul lagi sebagai gambar baru."""
    d = buat_dataset(tmp_path / "b6", 1, 0)
    ip = sorted(d.glob("*.jpg"))[0]
    _tulis(ip, ai=True)
    r = tugas.buang_hasil_ai(d, _items(d))
    simpanan = d / r["folder"]
    assert simpanan.name.startswith(".")
    assert scanner.tersembunyi(simpanan / "isi" / "a.json", d)
    assert len(scanner.scan(d)[0]) == 1          # tetap satu gambar


def test_tanpa_hasil_ai_tak_membuat_folder(tmp_path):
    d = buat_dataset(tmp_path / "b7", 1, 0)
    _tulis(sorted(d.glob("*.jpg"))[0])
    r = tugas.buang_hasil_ai(d, _items(d))
    assert r["dibuang"] == 0 and r["folder"] == ""
    assert not list(d.glob(".ai-dibuang-*"))


# ------------------------------------------------------------------- rute
def test_rute_membuang_sebatch(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_dataset(ruang / "buang-rute", 2, 0)
    ips = sorted(d.glob("*.jpg"))
    _tulis(ips[0], ai=True, n=2)
    klien.post(f"/setsrc?path={d}")
    j = klien.post("/api/tugas/buang-ai",
                   json={"batch": "", "ds": "buang-rute"}).json()
    assert j.get("ok"), j
    assert j["dibuang"] == 1 and j["bentuk"] == 2
    assert not ips[0].with_suffix(".json").exists()


def test_rute_menolak_lingkup_yang_tak_disebut(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_dataset(ruang / "buang-rute2", 1, 0)
    klien.post(f"/setsrc?path={d}")
    j = klien.post("/api/tugas/buang-ai", json={"ds": "buang-rute2"}).json()
    assert j["ok"] is False and "lingkup" in j["error"]


def test_rute_bilang_kalau_tak_ada_yang_dibuang(klien, lingkungan):
    """Diam-diam menjawab sukses untuk nol berkas membuat orang mengira
    hasilnya sudah terbuang padahal masih utuh."""
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_dataset(ruang / "buang-rute3", 1, 0)
    _tulis(sorted(d.glob("*.jpg"))[0])
    klien.post(f"/setsrc?path={d}")
    j = klien.post("/api/tugas/buang-ai",
                   json={"batch": "", "ds": "buang-rute3"}).json()
    assert j["ok"] is False and "tidak ada hasil AI" in j["error"]


def test_rute_memakai_pemilih_lingkup_yang_sama_dengan_autolabel():
    """Yang dibuang harus persis yang tadi ditulis auto-label, bukan tebakan
    lain yang kebetulan mirip."""
    r = pathlib.Path("app/routers/tugas.py").read_text()
    i = r.index('@router.post("/api/tugas/buang-ai")')
    blok = r[i:r.index('@router.post("/api/tugas/hapus-batch")', i)]
    assert "svc.target_autolabel(" in blok
    assert 'sasaran="dicek"' in blok
    assert "boleh_unggah" in blok          # hak sama dengan hapus batch


# -------------------------------------------------------------- antarmuka
def test_tombol_muncul_hanya_kalau_ada_hasil_ai():
    h = pathlib.Path("app/templates/anotasi.html").read_text()
    assert "an-buang-ai" in h
    i = h.index("an-buang-ai")
    assert "{% if b.dicek %}" in h[max(0, i - 400):i]
    # kartu batch dan kedua kolom kartu job
    assert h.count("an-buang-ai") == 3


def test_konfirmasinya_menyebut_gambar_tak_dihapus():
    """Bedanya dengan "Hapus batch" harus jelas SEBELUM ditekan."""
    js = pathlib.Path("app/static/anotasi.js").read_text()
    i = js.index("for (const b of document.querySelectorAll('.an-buang-ai'))")
    blok = js[i:js.index("for (const b of document.querySelectorAll('.an-hapus-batch'))", i)]
    assert "Gambarnya TIDAK dihapus" in blok
    assert "masih bisa " in blok           # bisa dikembalikan
    assert "/api/tugas/buang-ai" in blok
