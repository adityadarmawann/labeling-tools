"""Halaman verifikasi = halaman labeler yang SAMA, plus satu vonis di atasnya.

Yang dipertahankan: memverifikasi hasil AI memakai labeler biasa, bukan
halaman khusus. Alatnya lengkap, pintasannya sama, urutan gambarnya ikut
saringan. Satu-satunya yang membedakannya adalah kalimat di atas kanvas.

Yang diperbaiki di sini: kalimat itu tak ada sama sekali, dan dua tempat lain
justru MENYATAKAN hal yang salah. scanner.severity() tidak bisa melihat
`perlu_dicek`, jadi hasil auto-label yang bentuknya mulus severity-nya "ok":

  - labeler: nol tanda bahwa anotasi di kanvas ditulis AI,
  - halaman Lihat: "Anotasi terlihat wajar",
  - kartu kisi: lencana "Sudah dilabeli",

ketiganya untuk gambar yang belum pernah dilihat satu pun manusia.
"""
from __future__ import annotations

import json
import pathlib

from conftest import buat_dataset, masuk
from tests.test_data import PW_PAUL
from app.services import scanner


def _tulis(ip: pathlib.Path, perlu_dicek: bool) -> None:
    isi = {"version": "0.4.3", "flags": {}, "imagePath": ip.name,
           "imageHeight": 64, "imageWidth": 64,
           "shapes": [{"label": "botol", "text": "", "group_id": None,
                       "shape_type": "rectangle", "flags": {},
                       "points": [[4, 4], [20, 20]]}]}
    if perlu_dicek:
        isi["perlu_dicek"] = True
    ip.with_suffix(".json").write_text(json.dumps(isi))


def _siap(klien, lingkungan, nama):
    """Label ditulis SEBELUM setsrc.

    sess.segarkan() menyusul lewat jurnal perubahan (session.tandai_berubah),
    bukan dengan memindai ulang folder: pada projek 11 ribu gambar sekali
    pindai 5,8 detik. Menulis .json langsung ke disk di dalam uji melewati
    jurnal itu, jadi sesinya tak pernah tahu. Jalur nyata tidak begitu:
    auto-label memanggil prelabel._beritahu, dan simpan manual lewat rutenya
    sendiri.
    """
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_dataset(ruang / nama, 2, 0)
    ips = sorted(d.glob("*.jpg"))
    _tulis(ips[0], True)       # hasil AI, belum diverifikasi
    _tulis(ips[1], False)      # pekerjaan manusia
    klien.post(f"/setsrc?path={d}")
    return d, ips


# --------------------------------------------- yang membuat ini perlu ada
def test_severity_tak_bisa_melihat_perlu_dicek(tmp_path):
    """Inti persoalannya, dieja sebagai uji supaya tak hilang lagi.

    Kalau severity() suatu hari ikut menyadari perlu_dicek, uji ini gagal dan
    ketiga penambal di labeler/Lihat/kartu bisa ditinjau ulang.
    """
    d = buat_dataset(tmp_path / "sev", 1, 0)
    ip = sorted(d.glob("*.jpg"))[0]
    _tulis(ip, True)
    it = scanner.scan(d)[0][0]
    assert it["perlu_dicek"] is True
    assert scanner.severity(it) == "ok"      # mulus, tak terbedakan
    assert it["issues"] == []


# ------------------------------------------------------------- labeler
def test_labeler_menyebut_hasil_ai_belum_diverifikasi(klien, lingkungan):
    d, ips = _siap(klien, lingkungan, "vh-lab")
    h = klien.get("/label", params={"path": str(ips[0])}).text
    assert 'id="lab-verif"' in h
    assert "Hasil auto-label, belum diverifikasi" in h
    # Spanduknya TAMPIL untuk gambar ini.
    blok = h.split('id="lab-verif"')[1].split(">")[0]
    assert "hidden" not in blok
    assert 'data-keadaan="belum"' in blok


def test_labeler_gambar_manusia_tak_berubah(klien, lingkungan):
    """Halaman labeler biasa harus tetap persis seperti biasa."""
    d, ips = _siap(klien, lingkungan, "vh-lab2")
    h = klien.get("/label", params={"path": str(ips[1])}).text
    blok = h.split('id="lab-verif"')[1].split(">")[0]
    assert "hidden" in blok
    assert 'data-keadaan="tak-usah"' in blok


def test_labeler_tetap_labeler_yang_sama(klien, lingkungan):
    """Bukan halaman khusus: kanvas, palet alat, Simpan, navigasi, semuanya
    sama dengan membuka gambar mana pun."""
    d, ips = _siap(klien, lingkungan, "vh-sama")
    ai = klien.get("/label", params={"path": str(ips[0])}).text
    biasa = klien.get("/label", params={"path": str(ips[1])}).text
    for tanda in ('id="c"', 'id="btn-simpan"', 'id="prev"', 'id="next"',
                  'class="palet-judul"', 'id="palet-rincian"', "label.js"):
        assert tanda in ai, tanda
        assert tanda in biasa, tanda


# --------------------------------------- pindah gambar tanpa muat ulang
def test_label_data_mengembalikan_perlu_dicek(klien, lingkungan):
    """Panah kiri/kanan memakai rute ini, bukan memuat ulang halaman. Tanpa
    field ini spanduk gambar PERTAMA menempel di seluruh sisa batch."""
    d, ips = _siap(klien, lingkungan, "vh-data")
    a = klien.get("/api/label-data", params={"path": str(ips[0])}).json()
    b = klien.get("/api/label-data", params={"path": str(ips[1])}).json()
    assert a["perlu_dicek"] is True
    assert b["perlu_dicek"] is False


def test_js_membalik_spanduk_saat_pindah_dan_saat_simpan():
    js = pathlib.Path("app/static/label.js").read_text()
    assert "function keadaanVerif(" in js
    # ikut gambar yang sekarang saat pindah
    assert "keadaanVerif(d.perlu_dicek ? 'belum' : 'tak-usah')" in js
    # dan berubah begitu disimpan: menyimpan manual menghapus perlu_dicek
    assert "keadaanVerif('sudah')" in js


# ---------------------------------------------------------- halaman Lihat
def test_lihat_tak_bilang_wajar_untuk_hasil_ai(klien, lingkungan):
    d, ips = _siap(klien, lingkungan, "vh-lihat")
    h = klien.get("/view", params={"path": str(ips[0])}).text
    assert "Hasil auto-label, belum diverifikasi" in h
    assert "Anotasi terlihat wajar" not in h


def test_lihat_gambar_manusia_tetap_wajar(klien, lingkungan):
    d, ips = _siap(klien, lingkungan, "vh-lihat2")
    h = klien.get("/view", params={"path": str(ips[1])}).text
    assert "Anotasi terlihat wajar" in h
    assert "belum diverifikasi" not in h


# ------------------------------------------------------------ kartu kisi
def test_kartu_kisi_melencanai_hasil_ai(klien, lingkungan):
    d, ips = _siap(klien, lingkungan, "vh-kisi")
    klien.post("/api/tugas/dataset",
               json={"gambar": [str(p) for p in ips], "masuk": True})
    h = klien.get("/", params={"f": "dicek"}).text
    assert 'class="kartu-ai"' in h
    assert "Hasil AI, belum diverifikasi" in h


def test_kartu_manusia_tanpa_lencana_ai(klien, lingkungan):
    d, ips = _siap(klien, lingkungan, "vh-kisi2")
    klien.post("/api/tugas/dataset",
               json={"gambar": [str(p) for p in ips], "masuk": True})
    h = klien.get("/", params={"f": "verif"}).text
    assert 'class="kartu-ai"' not in h


def test_lencana_ai_punya_gaya_sendiri():
    """Lencana yang tak punya CSS tak terlihat sama sekali, dan ujinya tetap
    hijau karena markahnya ada."""
    css = pathlib.Path("app/static/app.css").read_text()
    assert ".kartu-ai{" in css
    assert "--ok-bg:" in css      # token spanduk "sudah" di labeler
