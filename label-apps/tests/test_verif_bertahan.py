"""Tanda "Terverifikasi" harus bertahan saat pindah gambar lalu kembali.

Dilaporkan dari pemakaian: spanduk berubah jadi "Terverifikasi" begitu
disimpan, lalu kembali jadi "Hasil auto-label, belum diverifikasi" begitu
orangnya pindah ke gambar berikutnya dan kembali lagi.

Sebabnya satu nilai yang dibuang. muat_ulang_item membaca ulang anotasi satu
gambar dari disk dengan `sh, W, H, _ = scanner.read_json(...)`, dan nilai
keempat yang dibuang ke `_` itu justru perlu_dicek. Jadi menyimpan
membersihkan tandanya DI BERKAS tetapi tidak di ingatan sesi, sementara
jalur pindah gambar (/api/label-data) membaca ingatan sesi itu.

Pekerjaannya tidak pernah hilang. Yang hilang kepercayaan pada tandanya, dan
itu cukup untuk membuat orang memeriksa ulang gambar yang sudah selesai.
"""
from __future__ import annotations

import json
import pathlib

from conftest import buat_dataset, masuk
from tests.test_data import PW_PAUL


def _tulis_ai(ip: pathlib.Path):
    ip.with_suffix(".json").write_text(json.dumps({
        "version": "0.4.3", "flags": {}, "imagePath": ip.name,
        "imageHeight": 64, "imageWidth": 64, "perlu_dicek": True,
        "shapes": [{"label": "botol", "text": "", "group_id": None,
                    "shape_type": "rectangle", "flags": {},
                    "points": [[4, 4], [20, 20]]}]}))


def _siap(klien, nama):
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_dataset(ruang / nama, 2, 0)
    ips = sorted(d.glob("*.jpg"))
    for ip in ips:
        _tulis_ai(ip)
    klien.post(f"/setsrc?path={d}")
    return d, ips


def _simpan(klien, ip):
    return klien.post("/api/simpan", json={
        "path": str(ip),
        "shapes": [{"label": "botol", "text": "", "group_id": None,
                    "shape_type": "rectangle", "flags": {},
                    "points": [[4, 4], [20, 20]]}],
        "flags": {}, "image_text": ""}).json()


def test_tanda_bertahan_sesudah_pindah_dan_kembali(klien, lingkungan):
    """Inti laporannya, diuji sebagai ALUR: simpan, pindah, kembali."""
    d, ips = _siap(klien, "verif-tahan")

    # sebelum disimpan: memang hasil AI
    assert klien.get("/api/label-data",
                     params={"path": str(ips[0])}).json()["perlu_dicek"] is True

    j = _simpan(klien, ips[0])
    assert j.get("ok"), j

    # pindah ke gambar lain, lalu KEMBALI
    klien.get("/api/label-data", params={"path": str(ips[1])})
    kembali = klien.get("/api/label-data", params={"path": str(ips[0])}).json()
    assert kembali["perlu_dicek"] is False, "tandanya hidup lagi sesudah kembali"


def test_berkasnya_memang_sudah_bersih(klien, lingkungan):
    """Membedakan "ingatan sesi basi" dari "simpan tak membersihkan apa pun".
    Kalau uji ini gagal bersama yang di atas, yang salah jalur simpannya."""
    d, ips = _siap(klien, "verif-berkas")
    _simpan(klien, ips[0])
    isi = json.loads(ips[0].with_suffix(".json").read_text())
    assert not isi.get("perlu_dicek")


def test_gambar_lain_tak_ikut_tertandai_bersih(klien, lingkungan):
    """Menyegarkan satu item tak boleh menyentuh tetangganya."""
    d, ips = _siap(klien, "verif-tetangga")
    _simpan(klien, ips[0])
    lain = klien.get("/api/label-data", params={"path": str(ips[1])}).json()
    assert lain["perlu_dicek"] is True


def test_halaman_labeler_ikut_benar_sesudah_simpan(klien, lingkungan):
    """Bukan cuma jalur pindah gambar: memuat ulang halamannya pun harus
    memberi spanduk yang benar."""
    d, ips = _siap(klien, "verif-halaman")
    _simpan(klien, ips[0])
    h = klien.get("/label", params={"path": str(ips[0])}).text
    blok = h[h.index('id="lab-verif"'):h.index(">", h.index('id="lab-verif"'))]
    assert 'data-keadaan="tak-usah"' in blok
    assert "hidden" in blok


def test_nilai_keempat_tak_dibuang_lagi():
    """Penjaga pada sebabnya, bukan cuma gejalanya."""
    import inspect
    from app.session import Session
    src = inspect.getsource(Session.muat_ulang_item)
    assert "sh, W, H, _ = scanner.read_json" not in src
    assert 'it["perlu_dicek"]' in src
