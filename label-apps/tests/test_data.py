"""Unggahan, tandai latar, dan saringan grid."""
from __future__ import annotations

import json
import re

from conftest import PW_ANGGI, PW_PAUL, masuk


# ---------------------------------------------------------------- unggahan

def test_unggah_berkas_normal(klien, lingkungan):
    masuk(klien, "anggi", PW_ANGGI)
    r = klien.put("/upload?ds=batch-1&name=foto.png", content=b"x" * 500)
    assert r.json() == {"ok": True, "name": "foto.png", "bytes": 500}
    assert (lingkungan["tmp"] / "unggahan" / "anggi" / "batch-1" / "foto.png").exists()


def test_nama_berkas_disterilkan_dan_tetap_di_folder_akun(klien, lingkungan):
    masuk(klien, "anggi", PW_ANGGI)
    unggahan = lingkungan["tmp"] / "unggahan"

    r = klien.put("/upload?ds=batch-1&name=../../../etc/jahat.png", content=b"x" * 10)
    assert r.json()["ok"] is True
    assert r.json()["name"] == "jahat.png"
    # mendarat di dalam folder akun, bukan di luar
    assert (unggahan / "anggi" / "batch-1" / "jahat.png").exists()
    assert not (unggahan.parent / "etc").exists()

    # nama dataset pun tidak boleh membawa keluar
    klien.put("/upload?ds=../../luar&name=b.png", content=b"x" * 10)
    assert (unggahan / "anggi" / "luar" / "b.png").exists()
    assert not (unggahan.parent / "luar").exists()

    # seluruh berkas hasil unggahan berada di bawah folder milik akun itu
    for p in unggahan.rglob("*"):
        if p.is_file():
            assert unggahan / "anggi" in p.parents


def test_ekstensi_tak_didukung_ditolak(klien):
    masuk(klien, "anggi", PW_ANGGI)
    for nama in ("jahat.sh", "a.py", "b.exe", "tanpa-ekstensi", ".bashrc"):
        r = klien.put(f"/upload?ds=x&name={nama}", content=b"x" * 10)
        assert r.json()["ok"] is False, nama


def test_berkas_kosong_ditolak(klien):
    masuk(klien, "anggi", PW_ANGGI)
    assert klien.put("/upload?ds=x&name=a.png", content=b"").json()["ok"] is False


def test_berkas_lebih_besar_dari_batas_ditolak(klien, lingkungan):
    """MAX_UPLOAD_MB=1 di lingkungan uji."""
    masuk(klien, "anggi", PW_ANGGI)
    r = klien.put("/upload?ds=x&name=besar.png", content=b"x" * (2 * 1024 * 1024))
    assert r.json()["ok"] is False
    assert "1 MB" in r.json()["error"]
    # tidak ada berkas setengah jadi yang tertinggal
    assert not list((lingkungan["tmp"] / "unggahan").rglob("*.part"))
    assert not list((lingkungan["tmp"] / "unggahan").rglob("besar.png"))


def test_hasil_unggahan_bisa_dibuka_sebagai_dataset(klien, lingkungan):
    masuk(klien, "anggi", PW_ANGGI)
    # gambar sungguhan supaya bisa dipindai OpenCV
    asli = (lingkungan["roots"] / "ds-beta" / "ds-beta-00.jpg").read_bytes()
    klien.put("/upload?ds=batch-2&name=satu.jpg", content=asli)
    klien.put("/upload?ds=batch-2&name=dua.jpg", content=asli)

    r = klien.post("/useupload?ds=batch-2")
    assert r.json()["ok"] is True
    assert r.json()["n"] == 2
    assert klien.get("/").text.count('class="card"') == 2


def test_useupload_folder_tak_ada(klien):
    masuk(klien, "anggi", PW_ANGGI)
    assert klien.post("/useupload?ds=belum-pernah").json()["ok"] is False


# ---------------------------------------------------------------- tandai latar

def _gambar(lingkungan, ds, i):
    return lingkungan["roots"] / ds / f"{ds}-{i:02d}.jpg"


def test_tandai_latar_menulis_json_kosong(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={lingkungan['roots'] / 'ds-alpha'}")
    img = _gambar(lingkungan, "ds-alpha", 3)        # belum berlabel

    assert klien.post(f"/markbg?path={img}").json()["ok"] is True
    jp = img.with_suffix(".json")
    assert jp.exists()
    d = json.loads(jp.read_text())
    assert d["shapes"] == []
    assert (d["imageWidth"], d["imageHeight"]) == (80, 60)


def test_tandai_latar_ditolak_kalau_gambar_punya_objek(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={lingkungan['roots'] / 'ds-alpha'}")
    img = _gambar(lingkungan, "ds-alpha", 0)        # punya 1 objek
    isi_sebelum = img.with_suffix(".json").read_text()

    r = klien.post(f"/markbg?path={img}")
    assert r.json()["ok"] is False
    assert "punya 1 objek" in r.json()["error"]
    # anotasi aslinya tidak tersentuh
    assert img.with_suffix(".json").read_text() == isi_sebelum


def test_batal_tandai_latar_menghapus_json_kosong(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={lingkungan['roots'] / 'ds-alpha'}")
    img = _gambar(lingkungan, "ds-alpha", 3)

    klien.post(f"/markbg?path={img}")
    assert klien.post(f"/unmarkbg?path={img}").json()["ok"] is True
    assert not img.with_suffix(".json").exists()


def test_batal_tandai_latar_tidak_menghapus_anotasi_berisi(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={lingkungan['roots'] / 'ds-alpha'}")
    img = _gambar(lingkungan, "ds-alpha", 0)

    assert klien.post(f"/unmarkbg?path={img}").json()["ok"] is False
    assert img.with_suffix(".json").exists()


# ---------------------------------------------------------------- saringan grid

def _chip(html: str, nama: str) -> int:
    m = re.search(rf"{nama}\s*<b>(\d+)</b>", html.replace("\n", " "))
    assert m, f"chip '{nama}' tidak ditemukan"
    return int(m.group(1))


def test_jumlah_chip_sama_dengan_isi_grid(klien, lingkungan):
    """
    Regresi: dulu chip "Belum dilabeli" menghitung severity 'stop' sementara
    saringannya memakai "tanpa objek", sehingga gambar yang sudah ditandai
    latar ikut muncul dan angkanya tidak cocok dengan isi grid.
    """
    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={lingkungan['roots'] / 'ds-alpha'}")

    def cocok():
        html = klien.get("/?f=all").text
        kartu = klien.get("/?f=unlab").text.count('class="card"')
        assert _chip(html, "Belum dilabeli") == kartu, html[:0] or "chip != grid"
        return kartu

    assert cocok() == 2                                     # 4 gambar, 2 berlabel
    klien.post(f"/markbg?path={_gambar(lingkungan, 'ds-alpha', 3)}")
    assert cocok() == 1                                     # satu jadi latar
    klien.post(f"/unmarkbg?path={_gambar(lingkungan, 'ds-alpha', 3)}")
    assert cocok() == 2


def test_saringan_per_kelas(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={lingkungan['roots'] / 'ds-alpha'}")

    assert klien.get("/?f=all").text.count('class="card"') == 4
    assert klien.get("/?f=all&c=botol").text.count('class="card"') == 1
    assert klien.get("/?f=all&c=kaleng").text.count('class="card"') == 1
    assert klien.get("/?f=all&c=tidak-ada").text.count('class="card"') == 0


def test_pindai_ulang_menangkap_berkas_baru(klien, lingkungan):
    from conftest import buat_dataset

    masuk(klien, "paul", PW_PAUL)
    src = lingkungan["roots"] / "ds-alpha"
    klien.post(f"/setsrc?path={src}")
    assert klien.get("/").text.count('class="card"') == 4

    buat_dataset(src, 6, 2)                      # tambah 2 gambar baru
    assert klien.post("/rescan").json()["n"] == 6
    assert klien.get("/").text.count('class="card"') == 6


def test_daftar_dataset_muncul_di_halaman_pilih(klien):
    masuk(klien, "paul", PW_PAUL)
    html = klien.get("/pilih").text
    assert "ds-alpha" in html and "ds-beta" in html
    assert 'id="drop"' in html          # panel unggah
    assert 'id="pathbox"' in html       # kotak path
