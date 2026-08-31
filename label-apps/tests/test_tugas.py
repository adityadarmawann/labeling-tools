"""
Uji penugasan pelabelan.

Yang dijaga di sini bukan "tombolnya jalan", melainkan tiga aturan yang kalau
gugur akibatnya baru terlihat setelah pekerjaan orang lain tertimpa:

1. Projek yang belum pernah ditugaskan berperilaku persis seperti sebelum
   fitur ini ada. Tidak ada satu pun projek lama yang perlu dimigrasi.
2. Yang boleh menyunting label sebuah gambar hanya pelabelnya dan pemilik
   projek, dan itu diperiksa di RUTE, bukan disembunyikan dari tampilan.
3. Projek orang lain tidak muncul dan tidak bisa dibuka sebelum diundang.
"""
from __future__ import annotations

import json

from app.services import projek, tugas
from tests.test_data import PW_ANGGI, PW_PAUL, masuk


def _ds(tmp_path, nama="projek"):
    d = tmp_path / nama
    d.mkdir(parents=True, exist_ok=True)
    return d


# ============================================================
# WARISAN
# ============================================================

def test_projek_tanpa_berkas_tugas_berperilaku_seperti_sebelumnya(tmp_path):
    """Tidak ada migrasi, dan tidak ada ekspor yang mendadak kosong."""
    d = _ds(tmp_path)
    data = tugas.baca(d, "darma")
    assert data["warisan"] is True
    assert tugas.di_dataset(data, "apa saja.jpg") is True
    assert tugas.boleh_labeli(data, "siapa pun", "apa saja.jpg") is True
    assert not (d / tugas.BERKAS).exists(), "berkas lahir tanpa diminta"


def test_berkas_lahir_saat_pertama_kali_menugaskan(tmp_path):
    d = _ds(tmp_path)
    tugas.tugaskan(d, "darma", "aditya", ["a.jpg", "b.jpg"])
    assert (d / tugas.BERKAS).is_file()
    data = tugas.baca(d, "darma")
    assert data["warisan"] is False
    # Sejak ada berkasnya, dataset TIDAK lagi otomatis berisi semuanya.
    assert tugas.di_dataset(data, "a.jpg") is False


def test_berkas_tugas_rusak_dibaca_sebagai_warisan(tmp_path):
    """Berkas rusak tidak boleh mengunci seluruh tim di luar projeknya."""
    d = _ds(tmp_path)
    (d / tugas.BERKAS).write_text("{ bukan json")
    data = tugas.baca(d, "darma")
    assert data["warisan"] is True and data["pemilik"] == "darma"


# ============================================================
# HAK
# ============================================================

def test_hanya_pelabel_dan_pemilik_yang_boleh_menyunting(tmp_path):
    d = _ds(tmp_path)
    tugas.tugaskan(d, "darma", "aditya", ["a.jpg"])
    data = tugas.baca(d, "darma")

    assert tugas.boleh_labeli(data, "aditya", "a.jpg") is True
    assert tugas.boleh_labeli(data, "darma", "a.jpg") is True, "pemilik selalu boleh"
    assert tugas.boleh_labeli(data, "rizky", "a.jpg") is False
    assert "aditya" in tugas.alasan_tolak(data, "rizky", "a.jpg")

    # Gambar yang belum ditugaskan tetap milik pemiliknya sendiri.
    assert tugas.boleh_labeli(data, "darma", "b.jpg") is True
    assert tugas.boleh_labeli(data, "aditya", "b.jpg") is False


def test_anggota_boleh_melihat_tapi_tidak_mengelola(tmp_path):
    d = _ds(tmp_path)
    tugas.undang(d, "darma", "aditya")
    data = tugas.baca(d, "darma")
    assert tugas.boleh_lihat(data, "aditya") is True
    assert tugas.boleh_kelola(data, "aditya") is False
    assert tugas.boleh_kelola(data, "darma") is True
    assert tugas.boleh_lihat(data, "rizky") is False


def test_gambar_yang_sudah_ditugaskan_tidak_dipindah_diam_diam(tmp_path):
    """Memindahkan pekerjaan berjalan tanpa memberi tahu keduanya adalah cara
    tercepat membuat dua orang mengerjakan hal yang sama."""
    d = _ds(tmp_path)
    tugas.tugaskan(d, "darma", "aditya", ["a.jpg", "b.jpg"])
    r = tugas.tugaskan(d, "darma", "rizky", ["b.jpg", "c.jpg"])
    assert r["n"] == 1 and r["dilewati"] == 1
    data = tugas.baca(d, "darma")
    assert tugas.pelabel_gambar(data, "b.jpg") == "aditya"
    assert tugas.pelabel_gambar(data, "c.jpg") == "rizky"


def test_mengeluarkan_anggota_membubarkan_tugasnya(tmp_path):
    """Job tanpa pelabel yang berhak adalah pekerjaan yang tidak bisa
    dilanjutkan siapa pun."""
    d = _ds(tmp_path)
    tugas.tugaskan(d, "darma", "aditya", ["a.jpg"])
    tugas.keluarkan_anggota(d, "darma", "aditya")
    data = tugas.baca(d, "darma")
    assert data["tugas"] == {} and "aditya" not in data["anggota"]


# ============================================================
# DATASET
# ============================================================

def test_masuk_dan_keluar_dataset(tmp_path):
    d = _ds(tmp_path)
    tugas.tugaskan(d, "darma", "aditya", ["a.jpg", "b.jpg"])
    assert tugas.masukkan(d, ["a.jpg"], "darma")["ditambah"] == 1
    assert tugas.masukkan(d, ["a.jpg"], "darma")["ditambah"] == 0, "kembar"
    data = tugas.baca(d, "darma")
    assert tugas.di_dataset(data, "a.jpg") and not tugas.di_dataset(data, "b.jpg")

    tugas.keluarkan(d, ["a.jpg"], "darma")
    assert not tugas.di_dataset(tugas.baca(d, "darma"), "a.jpg")


def test_papan_membagi_job_ke_tiga_keadaan(tmp_path):
    d = _ds(tmp_path)
    tugas.tugaskan(d, "darma", "aditya", ["a.jpg", "b.jpg"])
    tugas.tugaskan(d, "darma", "rizky", ["c.jpg"])
    semua = {"a.jpg", "b.jpg", "c.jpg", "d.jpg"}

    p = tugas.papan(tugas.baca(d, "darma"), berlabel=set(), semua=semua)
    assert p["belum_ditugaskan"] == 1
    assert {k["keadaan"] for k in p["kartu"]} == {tugas.BARU}

    p = tugas.papan(tugas.baca(d, "darma"), berlabel={"a.jpg"}, semua=semua)
    aditya = next(k for k in p["kartu"] if k["pelabel"] == "aditya")
    assert aditya["keadaan"] == tugas.JALAN and aditya["persen"] == 50

    tugas.masukkan(d, ["c.jpg"], "darma")
    p = tugas.papan(tugas.baca(d, "darma"), berlabel={"a.jpg", "c.jpg"}, semua=semua)
    rizky = next(k for k in p["kartu"] if k["pelabel"] == "rizky")
    assert rizky["keadaan"] == tugas.SELESAI


# ============================================================
# LINTAS AKUN
# ============================================================

def test_projek_orang_lain_tidak_bisa_dibuka_sebelum_diundang(tmp_path):
    root = tmp_path / "unggahan"
    d = _ds(root / "darma", "rahasia")
    assert projek.temukan(root, "aditya", "darma/rahasia") is None
    assert projek.punya_tamu(root, "aditya") == []

    tugas.undang(d, "darma", "aditya")
    assert projek.temukan(root, "aditya", "darma/rahasia") == d
    tamu = projek.punya_tamu(root, "aditya")
    assert [t["ds"] for t in tamu] == ["darma/rahasia"]

    # Dan yang tidak diundang tetap tidak bisa.
    assert projek.temukan(root, "rizky", "darma/rahasia") is None


def test_temukan_tidak_bisa_dipakai_keluar_dari_ruang_unggahan(tmp_path):
    root = tmp_path / "unggahan"
    (root / "darma").mkdir(parents=True)
    (tmp_path / "luar").mkdir()
    for jahat in ("../luar", "../../luar", "darma/../../luar", "/etc"):
        d = projek.temukan(root, "darma", jahat)
        assert d is None or projek._didalam(d, root), (jahat, d)


# ============================================================
# RUTE
# ============================================================

def test_rute_menolak_menyunting_gambar_orang_lain(klien, lingkungan):
    """Diperiksa di rute, bukan disembunyikan dari kanvas.

    Kanvas orang lain tetap bisa mengirim permintaan ini, dan aturan yang cuma
    berlaku di tampilan bukan aturan.
    """
    import pathlib

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    from tests.test_projek import _projek
    d = _projek(ruang, "berbagi", n=3)
    klien.post(f"/setsrc?path={d}")

    gambar = sorted(str(p) for p in d.glob("*.jpg"))
    # paul menugaskan satu gambar ke anggi, lalu mencoba... tetap boleh,
    # karena paul pemiliknya. Yang tidak boleh justru sebaliknya.
    r = klien.post("/api/tugas/bagi", json={"pelabel": "anggi",
                                            "gambar": gambar[:1]}).json()
    assert r["ok"] and r["n"] == 1, r

    j = klien.post("/api/simpan", json={"path": gambar[0], "shapes": []}).json()
    assert j["ok"] is True, "pemilik projek harus tetap bisa menyunting"

    # anggi hanya boleh gambar jatahnya.
    klien.get("/logout")
    masuk(klien, "anggi", PW_ANGGI)
    klien.post(f"/setsrc?path={d}")
    j = klien.post("/api/simpan", json={"path": gambar[1], "shapes": []}).json()
    assert j["ok"] is False and "belum ditugaskan" in j["error"], j
    j = klien.post(f"/markbg?path={gambar[1]}").json()
    assert j["ok"] is False, "jalur tandai latar ikut dijaga"
    j = klien.post("/api/simpan", json={"path": gambar[0], "shapes": []}).json()
    assert j["ok"] is True, "gambar jatahnya sendiri harus bisa"


def test_rute_bagi_hanya_untuk_pemilik(klien, lingkungan):
    import pathlib

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    from tests.test_projek import _projek
    d = _projek(ruang, "punyapaul", n=2)
    klien.post(f"/setsrc?path={d}")
    gambar = sorted(str(p) for p in d.glob("*.jpg"))
    klien.post("/api/tugas/bagi", json={"pelabel": "anggi", "gambar": gambar})

    klien.get("/logout")
    masuk(klien, "anggi", PW_ANGGI)
    klien.post(f"/setsrc?path={d}")
    for rute, params in (("/api/tugas/undang", {"akun": "paul"}),
                         ("/api/tugas/bubarkan", {"id": "t1"})):
        j = klien.post(rute, params=params).json()
        assert j["ok"] is False and "pemilik" in j["error"], (rute, j)
    j = klien.post("/api/tugas/bagi", json={"pelabel": "anggi",
                                            "gambar": gambar}).json()
    assert j["ok"] is False and "pemilik" in j["error"], j
