"""Tampilan saringan: apa yang terlihat, apa yang bisa dibuang, apa yang hilang.

Audit menemukan beberapa cacat yang semuanya punya bentuk sama: saringan yang
BERLAKU tetapi tidak terlihat, atau terlihat tetapi diam-diam dibuang.
"""
from __future__ import annotations

import json
import pathlib
import re

from conftest import buat_dataset, masuk
from tests.test_data import PW_PAUL


def _proj(klien, ruang, nama, n=4, kurasi=True):
    from app.services import tugas as svc
    d = buat_dataset(ruang / nama, n, 0)
    if kurasi:
        svc.mulai_kurasi(d, "paul")
    klien.post(f"/setsrc?path={d}")
    for i, ip in enumerate(sorted(d.glob("*.jpg"))):
        ip.with_suffix(".json").write_text(json.dumps({
            "version": "0.4.3", "flags": {}, "imagePath": ip.name,
            "imageHeight": 64, "imageWidth": 64, "perlu_dicek": i % 2 == 0,
            "shapes": [{"label": "botol" if i % 2 else "kaleng", "text": "",
                        "group_id": None, "shape_type": "rectangle",
                        "flags": {}, "points": [[4, 4], [20, 20]]}]}))
    klien.get("/")
    return d


def test_form_kelas_tak_membuang_saringan_lain(klien, lingkungan):
    """Dulu form kelas menulis tiga medan tersembunyi sendiri (f, s, q) dan
    tidak menitipkan sisanya: menekan Terapkan membuang tag, unggahan, split,
    asal gambar, dan jumlah per halaman. Kisinya MELEBAR tepat ketika orang
    baru saja menambah satu saringan."""
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _proj(klien, ruang, "flt-form")
    h = klien.get("/", params={"dsf": "belum", "q": "flt"}).text
    # Form di dalam dropdown kelas
    i = h.index('id="menu-kelas"')
    form = h[i:h.index("</form>", i)]
    assert 'name="dsf"' in form, "lingkup dataset tidak dititipkan"
    assert 'name="q"' in form, "pencarian tidak dititipkan"


def test_tiap_saringan_punya_pil_yang_bisa_dibuang(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _proj(klien, ruang, "flt-pil")
    h = klien.get("/", params={"f": "dicek", "dsf": "belum", "c": "kaleng",
                               "q": "flt"}).text
    pil = re.findall(r'<a class="chip saring-pil" href="([^"]+)"', h)
    assert len(pil) == 4, pil
    # Tiap pil membuang SATU saringan dan mempertahankan sisanya.
    buang_kelas = [u for u in pil if "c=kaleng" not in u]
    assert buang_kelas, "tak ada pil yang membuang saringan kelas"
    u = buang_kelas[0].replace("&amp;", "&")
    assert "q=flt" in u and "dsf=belum" in u, u


def test_bersihkan_semua_hanya_saat_lebih_dari_satu(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _proj(klien, ruang, "flt-satu")
    satu = klien.get("/", params={"f": "dicek"}).text
    assert "Bersihkan semua" not in satu
    dua = klien.get("/", params={"f": "dicek", "q": "flt"}).text
    assert "Bersihkan semua" in dua


def test_keadaan_tak_dikenal_tidak_jadi_chip_hantu(klien, lingkungan):
    """?f=ngawur dulu mengembalikan semuanya tetapi templat tetap menganggapnya
    saringan aktif: tombolnya menyala berisi caret saja, dan chipnya menyebut
    nama kosong."""
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _proj(klien, ruang, "flt-ngawur")
    h = klien.get("/", params={"f": "ngawur"}).text
    assert '<a class="chip saring-pil"' not in h
    assert "Bersihkan semua" not in h


def test_saringan_kelas_job_punya_penanda_aktif(klien, lingkungan):
    """Kelas `chip-on` tidak pernah ada di CSS mana pun, jadi tombolnya tampil
    persis sama saat saringan aktif maupun tidak."""
    akar = pathlib.Path(__file__).resolve().parents[1]
    job = (akar / "app" / "templates" / "job.html").read_text()
    css = (akar / "app" / "static" / "app.css").read_text()
    assert "chip-on" not in job.replace("`chip-on`", "")
    assert "kelas_aktif or latar_aktif %} data-on" in job
    assert "[data-on]" in css or "data-on" in css


def test_panduan_mendokumentasikan_keadaan_verifikasi():
    akar = pathlib.Path(__file__).resolve().parents[1]
    teks = (akar / "app" / "templates" / "_panduan_grid.html").read_text()
    assert "<dt>Belum diverifikasi</dt>" in teks
    assert "<dt>Terverifikasi</dt>" in teks


def test_istilah_warn_sama_di_semua_halaman():
    """Severity 'warn' disebut "Perlu dicek" di kisi tetapi "Perlu diperiksa"
    di halaman Lihat: satu keadaan, dua nama."""
    akar = pathlib.Path(__file__).resolve().parents[1]
    view = (akar / "app" / "templates" / "view.html").read_text()
    assert "Perlu diperiksa" not in view
    assert "'warn': 'Perlu dicek'" in view


def test_klaim_perlu_dicek_saat_versi_dibuat_dihapus():
    """buatversi tak pernah menulis perlu_dicek; teks lama menjanjikan keadaan
    yang tidak pernah ada dan tak bisa dicari dengan saringan mana pun."""
    akar = pathlib.Path(__file__).resolve().parents[1]
    an = (akar / "app" / "templates" / "anotasi.html").read_text()
    bv = (akar / "app" / "services" / "buatversi.py").read_text()
    assert "perlu_dicek" not in bv
    assert "ditandai perlu dicek" not in an


def test_hasil_versi_punya_pemilih_per_halaman():
    """JS-nya mencari #vh-per yang tak pernah ada: versi puluhan ribu gambar
    terkunci di 120 per halaman."""
    akar = pathlib.Path(__file__).resolve().parents[1]
    t = (akar / "app" / "templates" / "versi_hasil.html").read_text()
    assert 'id="vh-per"' in t and 'name="per"' in t


def test_em_dash_tak_ada_di_teks_terlihat():
    akar = pathlib.Path(__file__).resolve().parents[1]
    for nama in ("versi_hasil.html", "view.html"):
        teks = (akar / "app" / "templates" / nama).read_text()
        # Komentar Jinja boleh memakai tanda pisah; teks terlihat tidak.
        tanpa_komentar = re.sub(r"\{#.*?#\}", "", teks, flags=re.S)
        assert "&mdash;" not in tanpa_komentar, nama
