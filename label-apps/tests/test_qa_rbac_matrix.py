"""
QA matriks RBAC end-to-end pada alur IMAGE.

Melengkapi test_rbac.py: di sini diuji MATRIKS hak akses penuh antar peran —
Owner, Editor, Labeler menyeluruh, Labeler spesifik, dan non-anggota — menembus
rute sungguhan (simpan label, unggah, kelola anggota, akses silang projek
sharing). Tujuannya menangkap ketidaksinkronan antar-flow: satu peran yang
diam-diam bisa melakukan yang bukan haknya, atau ditolak dari yang seharusnya
boleh.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.security import hash_password
from app.services import tugas
from tests.conftest import PW_ANGGI, PW_PAUL, klien_baru, masuk

PW_BUDI = "sandi-uji-budi-1"


def _tambah_user(lingkungan, akun, pw):
    users = json.loads(lingkungan["users"].read_text())
    users[akun] = {"hash": hash_password(pw), "nama": akun.title()}
    lingkungan["users"].write_text(json.dumps(users))


def _ruang(klien):
    return pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])


def _simpan(cli, path):
    return cli.post("/api/simpan", json={"path": path, "shapes": [
        {"label": "x", "shape_type": "rectangle",
         "points": [[1, 1], [5, 5]]}]}).json()


def _siapkan_projek(klien, nama, n=2):
    """Owner paul membuat projek berisi n gambar, terbuka di sesinya."""
    from tests.test_projek import _projek as buat_projek
    d = buat_projek(_ruang(klien), nama, n=n, label=False)
    klien.post(f"/setsrc?path={d}")
    g = sorted(str(q) for q in d.glob("*.jpg"))
    return d, g


# ======================================================= OWNER (kendali penuh)

def test_owner_boleh_simpan_unggah_dan_kelola(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    d, g = _siapkan_projek(klien, "owner-penuh")
    assert _simpan(klien, g[0]).get("ok") is True          # label
    r = klien.put("/upload?ds=owner-penuh&name=baru.png", content=b"x" * 50).json()
    assert r["ok"] is True                                 # unggah
    assert klien.post("/api/tugas/undang?akun=anggi").json()["ok"]  # kelola


# ======================================================= EDITOR

def test_editor_label_semua_dan_unggah_tapi_tak_mengelola(klien, aplikasi, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    d, g = _siapkan_projek(klien, "proj-editor")
    tugas.undang(d, "paul", "anggi", peran="editor")

    ed = klien_baru(aplikasi, "anggi", PW_ANGGI)
    ed.get("/?ds=paul/proj-editor")                        # buka projek sharing

    # Label SEMUA gambar (tanpa penugasan papan).
    assert _simpan(ed, g[0]).get("ok") is True
    assert _simpan(ed, g[1]).get("ok") is True
    # Unggah ke projek pemiliknya.
    r = ed.put("/upload?ds=paul/proj-editor&name=ed.png", content=b"x" * 50).json()
    assert r["ok"] is True and (d / "ed.png").exists()
    # TAK boleh mengelola: undang / atur / keluarkan / bagi semuanya owner-only.
    assert ed.post("/api/tugas/undang?akun=paul").json()["ok"] is False
    assert ed.post("/api/tugas/atur-anggota?akun=anggi&peran=editor").json()["ok"] is False
    assert ed.post("/api/tugas/bagi", json={"pelabel": "anggi", "n": 1}).json()["ok"] is False


# ======================================================= LABELER menyeluruh

def test_labeler_menyeluruh_label_semua_tak_unggah_tak_kelola(klien, aplikasi, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    d, g = _siapkan_projek(klien, "proj-lab")
    tugas.undang(d, "paul", "anggi", peran="pelabel", akses="semua")

    lab = klien_baru(aplikasi, "anggi", PW_ANGGI)
    lab.get("/?ds=paul/proj-lab")

    assert _simpan(lab, g[0]).get("ok") is True            # label semua
    assert _simpan(lab, g[1]).get("ok") is True
    # TAK boleh unggah.
    r = lab.put("/upload?ds=paul/proj-lab&name=x.png", content=b"x" * 50).json()
    assert r["ok"] is False and not (d / "x.png").exists()
    # TAK boleh mengelola.
    assert lab.post("/api/tugas/undang?akun=budi").json()["ok"] is False


# ======================================================= LABELER spesifik

def test_labeler_spesifik_hanya_scope_tak_unggah(klien, aplikasi, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    d, g = _siapkan_projek(klien, "proj-spes")
    klien.post("/api/tag/pasang", json={"paths": [g[0]], "batch": "a"})
    klien.post("/api/tag/pasang", json={"paths": [g[1]], "batch": "b"})
    tugas.undang(d, "paul", "anggi", peran="pelabel", akses="spesifik", batch=["a"])

    lab = klien_baru(aplikasi, "anggi", PW_ANGGI)
    lab.get("/?ds=paul/proj-spes")
    assert _simpan(lab, g[0]).get("ok") is True            # batch a (scope)
    assert _simpan(lab, g[1]).get("ok") is False           # batch b (luar)
    # TAK boleh unggah meski labeler spesifik.
    r = lab.put("/upload?ds=paul/proj-spes&name=x.png", content=b"x" * 50).json()
    assert r["ok"] is False


# ======================================================= NON-ANGGOTA

def test_non_anggota_tak_bisa_akses_atau_tulis(klien, aplikasi, lingkungan):
    _tambah_user(lingkungan, "budi", PW_BUDI)
    masuk(klien, "paul", PW_PAUL)
    d, g = _siapkan_projek(klien, "proj-tutup")

    budi = klien_baru(aplikasi, "budi", PW_BUDI)
    # Membuka projek orang lain yang tak mengundangnya: tak boleh.
    budi.get("/?ds=paul/proj-tutup")
    assert _simpan(budi, g[0]).get("ok") is False
    # Unggah "ke" projek paul malah mendarat di ruang budi sendiri, BUKAN milik
    # paul — tak pernah menyentuh folder orang lain.
    budi.put("/upload?ds=paul/proj-tutup&name=sisip.png", content=b"x" * 50)
    assert not (d / "sisip.png").exists()
    # Projek paul tak muncul di daftar sharing budi.
    assert budi.get("/api/projek/daftar").json()["tamu"] == []


# ======================================================= KELOLA ANGGOTA round-trip

def test_kelola_anggota_round_trip(klien, aplikasi, lingkungan):
    _tambah_user(lingkungan, "budi", PW_BUDI)
    masuk(klien, "paul", PW_PAUL)
    d, g = _siapkan_projek(klien, "proj-tim")

    # Undang budi sebagai Labeler menyeluruh lewat RUTE (bukan service).
    assert klien.post("/api/tugas/undang?akun=budi&peran=pelabel&akses=semua").json()["ok"]
    c = klien.get("/api/tugas/calon").json()
    brow = next(a for a in c["akun"] if a["akun"] == "budi")
    assert brow["peran"] == "pelabel" and brow["akses"] == "semua"

    # budi bisa melabeli.
    budi = klien_baru(aplikasi, "budi", PW_BUDI)
    budi.get("/?ds=paul/proj-tim")
    assert _simpan(budi, g[0]).get("ok") is True

    # Naikkan budi jadi Editor lewat atur-anggota.
    assert klien.post("/api/tugas/atur-anggota?akun=budi&peran=editor").json()["ok"]
    assert tugas.baca(d, "paul")["anggota"]["budi"]["peran"] == "editor"
    budi.get("/?ds=paul/proj-tim")
    r = budi.put("/upload?ds=paul/proj-tim&name=b.png", content=b"x" * 50).json()
    assert r["ok"] is True                                 # editor boleh unggah

    # Keluarkan budi: aksesnya hilang.
    assert klien.post("/api/tugas/keluarkan-anggota?akun=budi").json()["ok"]
    assert "budi" not in tugas.baca(d, "paul")["anggota"]
    budi2 = klien_baru(aplikasi, "budi", PW_BUDI)
    budi2.get("/?ds=paul/proj-tim")
    assert _simpan(budi2, g[1]).get("ok") is False


# ======================================================= Halaman Bagi non-owner

def test_halaman_bagi_non_owner_hanya_baca(klien, aplikasi, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    d, g = _siapkan_projek(klien, "proj-bagi")
    tugas.undang(d, "paul", "anggi", peran="pelabel", akses="semua")

    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    h = anggi.get("/bagi?ds=paul/proj-bagi").text
    assert "Hanya pemilik projek yang mengelola anggota" in h
    # Dan rute calon-nya pun menolak non-pemilik.
    anggi.get("/?ds=paul/proj-bagi")
    assert anggi.get("/api/tugas/calon").json()["ok"] is False


# ========================= Kurasi dataset: labeler cuma label (temuan QA)

def test_labeler_tak_bisa_hapus_gambar_atau_keluarkan(klien, aplikasi, lingkungan):
    """Labeler menyeluruh boleh melabeli semua, tapi TAK boleh menghapus media
    atau mengeluarkan gambar dari dataset (itu kurasi, bukan pelabelan)."""
    masuk(klien, "paul", PW_PAUL)
    d, g = _siapkan_projek(klien, "kurasi-lab")
    tugas.undang(d, "paul", "anggi", peran="pelabel", akses="semua")

    lab = klien_baru(aplikasi, "anggi", PW_ANGGI)
    lab.get("/?ds=paul/kurasi-lab")
    rh = lab.post("/api/tugas/hapus-gambar", json={"gambar": [g[0]]}).json()
    assert rh["ok"] is False and "Editor" in rh["error"]
    rk = lab.post("/api/tugas/dataset",
                  json={"gambar": [g[0]], "keluarkan": True}).json()
    assert rk["ok"] is False and "Editor" in rk["error"]


def test_labeler_spesifik_masuk_dataset_dalam_scope(klien, aplikasi, lingkungan):
    """Labeler spesifik BOLEH memasukkan gambar in-scope yang sudah ia labeli
    ke dataset (perbaikan: rute kini sadar batch)."""
    masuk(klien, "paul", PW_PAUL)
    d, g = _siapkan_projek(klien, "kurasi-spes")
    klien.post("/api/tag/pasang", json={"paths": [g[0]], "batch": "a"})
    klien.post("/api/tag/pasang", json={"paths": [g[1]], "batch": "b"})
    tugas.undang(d, "paul", "anggi", peran="pelabel", akses="spesifik", batch=["a"])

    lab = klien_baru(aplikasi, "anggi", PW_ANGGI)
    lab.get("/?ds=paul/kurasi-spes")
    assert _simpan(lab, g[0]).get("ok") is True            # labeli in-scope
    r = lab.post("/api/tugas/dataset", json={"gambar": [g[0]]}).json()
    assert r["ok"] is True and r.get("ditambah") == 1, r   # masuk dataset
    # Gambar luar scope tetap ditolak.
    r2 = lab.post("/api/tugas/dataset", json={"gambar": [g[1]]}).json()
    assert r2["ok"] is False and "bukan tugasmu" in r2["error"]


def test_editor_boleh_hapus_dan_keluarkan(klien, aplikasi, lingkungan):
    """Editor mengelola media: boleh menghapus gambar & mengeluarkan dari dataset."""
    masuk(klien, "paul", PW_PAUL)
    d, g = _siapkan_projek(klien, "kurasi-ed")
    _simpan(klien, g[0])
    klien.post("/api/tugas/dataset", json={"gambar": [g[0]]})   # owner masukkan
    tugas.undang(d, "paul", "anggi", peran="editor")

    ed = klien_baru(aplikasi, "anggi", PW_ANGGI)
    ed.get("/?ds=paul/kurasi-ed")
    rk = ed.post("/api/tugas/dataset",
                 json={"gambar": [g[0]], "keluarkan": True}).json()
    assert rk["ok"] is True and rk.get("dikeluarkan") == 1, rk
    rh = ed.post("/api/tugas/hapus-gambar", json={"gambar": [g[1]]}).json()
    assert rh["ok"] is True and rh.get("dibuang") == 1, rh
