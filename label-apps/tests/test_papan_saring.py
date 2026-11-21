"""Papan Anotasi: saringan per pelabel, per keadaan, dan pencarian judul.

Tiga kolom merender SELURUH kartu tanpa paginasi dan tanpa saringan apa pun.
Pada projek dengan puluhan tugas, satu-satunya cara mencari pekerjaan seseorang
adalah membaca satu per satu. Datanya sudah ada di tiap kartu (pelabel,
keadaan, judul); yang tidak ada cuma kontrolnya. CVAT dan Label Studio
sama-sama punya saringan assignee dan state di papan pekerjaannya.
"""
from __future__ import annotations

import pathlib
import re

from conftest import buat_dataset, masuk
from tests.test_data import PW_PAUL, PW_ANGGI


def _papan(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_dataset(ruang / "papan-saring", 6, 0)
    klien.post(f"/setsrc?path={d}")
    ips = sorted(d.glob("*.jpg"))
    a = klien.post("/api/tugas/bagi",
                   json={"pelabel": "paul", "judul": "sesi pagi",
                         "gambar": [str(p) for p in ips[:3]]}).json()
    b = klien.post("/api/tugas/bagi",
                   json={"pelabel": "anggi", "judul": "sesi malam",
                         "gambar": [str(p) for p in ips[3:]]}).json()
    assert a.get("id") and b.get("id"), (a, b)
    return d, a["id"], b["id"]


def _judul(h: str) -> list[str]:
    blok = h.split('class="an-papan"', 1)[1]
    return re.findall(r'class="an-judul[^"]*"[^>]*>([^<]+)', blok)


def test_kontrol_saringan_papan_ada(klien, lingkungan):
    d, _a, _b = _papan(klien, lingkungan)
    h = klien.get("/anotasi", params={"ds": d.name}).text
    assert 'id="an-pl"' in h and 'id="an-kd"' in h and 'id="an-cari"' in h
    # Huruf kapital sejak bilahnya jadi pil: isinya kini berdiri sendiri
    # sebagai pilihan, bukan sambungan dari label di sebelah kirinya.
    assert "Semua pelabel" in h and "Sedang dikerjakan" in h


def test_saring_per_pelabel(klien, lingkungan):
    d, _a, _b = _papan(klien, lingkungan)
    h = klien.get("/anotasi", params={"ds": d.name, "pl": "anggi"}).text
    assert "sesi malam" in h
    blok = h.split('class="an-papan"', 1)[1]
    assert "sesi pagi" not in blok


def test_cari_judul_tugas(klien, lingkungan):
    d, _a, _b = _papan(klien, lingkungan)
    h = klien.get("/anotasi", params={"ds": d.name, "q": "pagi"}).text
    blok = h.split('class="an-papan"', 1)[1]
    assert "sesi pagi" in blok and "sesi malam" not in blok


def test_cari_juga_mengenai_nama_pelabel(klien, lingkungan):
    d, _a, _b = _papan(klien, lingkungan)
    h = klien.get("/anotasi", params={"ds": d.name, "q": "anggi"}).text
    blok = h.split('class="an-papan"', 1)[1]
    assert "sesi malam" in blok and "sesi pagi" not in blok


def test_saring_keadaan(klien, lingkungan):
    d, _a, _b = _papan(klien, lingkungan)
    h = klien.get("/anotasi", params={"ds": d.name, "kd": "baru"}).text
    blok = h.split('class="an-papan"', 1)[1]
    assert "sesi pagi" in blok and "sesi malam" in blok   # keduanya belum disentuh
    h2 = klien.get("/anotasi", params={"ds": d.name, "kd": "selesai"}).text
    blok2 = h2.split('class="an-papan"', 1)[1]
    assert "sesi pagi" not in blok2 and "sesi malam" not in blok2


def test_nilai_saringan_ngawur_diabaikan(klien, lingkungan):
    d, _a, _b = _papan(klien, lingkungan)
    h = klien.get("/anotasi",
                  params={"ds": d.name, "pl": "hantu", "kd": "ngawur"}).text
    blok = h.split('class="an-papan"', 1)[1]
    assert "sesi pagi" in blok and "sesi malam" in blok


def test_daftar_pelabel_tak_menyusut_saat_satu_dipilih(klien, lingkungan):
    """Kalau daftarnya ikut disaring, memilih satu pelabel membuat yang lain
    hilang dari menu dan orang tak bisa berpindah tanpa membersihkan dulu."""
    d, _a, _b = _papan(klien, lingkungan)
    h = klien.get("/anotasi", params={"ds": d.name, "pl": "anggi"}).text
    menu = h[h.index('id="an-pl"'):h.index("</select>", h.index('id="an-pl"'))]
    assert "paul" in menu and "anggi" in menu


def test_pil_menyebut_berapa_yang_tersaring(klien, lingkungan):
    d, _a, _b = _papan(klien, lingkungan)
    h = klien.get("/anotasi", params={"ds": d.name, "pl": "anggi"}).text
    assert "1 dari 2 tugas" in h


def test_saringan_tak_membuang_urutan(klien, lingkungan):
    d, _a, _b = _papan(klien, lingkungan)
    h = klien.get("/anotasi",
                  params={"ds": d.name, "pl": "anggi", "urut": "pelabel"}).text
    # Diiris dari FORM-nya, bukan dari kelas tata letak yang bisa berganti:
    # yang dijaga di sini perilakunya, yaitu menyaring tak membuang urutan.
    i = h.index('<form class="an-saring"')
    form = h[i:h.index("</form>", i)]
    assert 'value="pelabel" selected' in form
    assert 'value="anggi" selected' in form
