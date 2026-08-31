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
from app.services import tag as tag_mod
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


# ============================================================
# UNDANGAN LEWAT TAUTAN
# ============================================================

def test_undangan_sekali_pakai(tmp_path):
    """Tautan yang diteruskan ke orang lain tidak boleh menambah anggota lagi."""
    d = _ds(tmp_path)
    r = tugas.undang_email(d, "darma", "rizky@higo.id")
    tok = r["token"]

    ok = tugas.pakai_undangan(d, tok, "rizky")
    assert ok["ok"] and ok["pemilik"] == "darma"
    assert "rizky" in tugas.baca(d, "darma")["anggota"]

    lagi = tugas.pakai_undangan(d, tok, "orang-lain")
    assert lagi["ok"] is False and "sudah dipakai" in lagi["error"]
    assert "orang-lain" not in tugas.baca(d, "darma")["anggota"]


def test_undangan_tak_dikenal_dan_pemilik_sendiri_ditolak(tmp_path):
    d = _ds(tmp_path)
    tok = tugas.undang_email(d, "darma", "x@higo.id")["token"]
    assert tugas.pakai_undangan(d, "bukan-token", "rizky")["ok"] is False
    assert tugas.pakai_undangan(d, tok, "darma")["ok"] is False


def test_undangan_ditemukan_tanpa_menyebut_nama_projek(tmp_path):
    """Tokennya sengaja tidak memuat nama projek: tautan yang menyebutnya sudah
    membocorkan isinya sebelum ada yang menerima undangannya."""
    root = tmp_path / "unggahan"
    d = _ds(root / "darma", "rahasia")
    tok = tugas.undang_email(d, "darma", "rizky@higo.id")["token"]
    assert projek.cari_undangan(root, tok) == d
    assert projek.cari_undangan(root, "token-palsu-panjang-sekali") is None
    assert projek.cari_undangan(root, "pendek") is None


def test_undangan_terbuka_dan_pembatalannya(tmp_path):
    d = _ds(tmp_path)
    tok = tugas.undang_email(d, "darma", "rizky@higo.id")["token"]
    assert len(tugas.undangan_terbuka(tugas.baca(d, "darma"))) == 1
    tugas.batalkan_undangan(d, "darma", tok)
    assert tugas.undangan_terbuka(tugas.baca(d, "darma")) == []
    assert tugas.pakai_undangan(d, tok, "rizky")["ok"] is False


def test_rute_undangan_untuk_email_yang_sudah_punya_akun(klien, lingkungan):
    """Menyuruh orang yang sudah punya akun menerima tautan cuma menambah satu
    langkah yang tidak menghasilkan apa-apa."""
    import json as _json
    import pathlib

    masuk(klien, "paul", PW_PAUL)
    # beri anggi sebuah email di berkas akun
    f = lingkungan["users"]
    u = _json.loads(f.read_text())
    u["anggi"]["email"] = "anggi@higo.id"
    f.write_text(_json.dumps(u))

    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    from tests.test_projek import _projek
    d = _projek(ruang, "undang-uji", n=2)
    klien.post(f"/setsrc?path={d}")

    j = klien.post("/api/tugas/undang-email?email=anggi@higo.id").json()
    assert j["ok"] and j["sudah_terdaftar"] is True and j["akun"] == "anggi", j
    assert "anggi" in tugas.baca(d, "paul")["anggota"]

    j = klien.post("/api/tugas/undang-email?email=belum-punya@higo.id").json()
    assert j["ok"] and j["sudah_terdaftar"] is False
    assert "/undangan/" in j["tautan"], j


def test_rute_undangan_dibuka_menjadikan_anggota(klien, aplikasi, lingkungan):
    import pathlib

    from conftest import klien_baru

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    from tests.test_projek import _projek
    d = _projek(ruang, "undang-buka", n=2)
    klien.post(f"/setsrc?path={d}")
    tautan = klien.post("/api/tugas/undang-email?email=baru@higo.id").json()["tautan"]
    jalur = "/undangan/" + tautan.rsplit("/", 1)[1]

    lain = klien_baru(aplikasi, "anggi", PW_ANGGI)
    h = lain.get(jalur).text
    assert "Kamu bergabung" in h and "undang-buka" in h
    assert "anggi" in tugas.baca(d, "paul")["anggota"]

    # Dibuka kedua kalinya, tautannya sudah mati.
    assert "tidak berlaku" in lain.get(jalur).text


# ============================================================
# HALAMAN BAGI
# ============================================================

def test_halaman_bagi_menampilkan_yang_belum_ditugaskan(klien, lingkungan):
    import pathlib

    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "bagi-uji", n=4)
    klien.post(f"/setsrc?path={d}")

    h = klien.get("/bagi?ds=bagi-uji").text
    assert h.count('class="bg-ubin"') == 4
    assert 'id="bg-mulai"' in h

    gambar = sorted(str(x) for x in d.glob("*.jpg"))
    klien.post("/api/tugas/bagi", json={"pelabel": "anggi", "gambar": gambar[:3]})

    h = klien.get("/bagi?ds=bagi-uji").text
    assert h.count('class="bg-ubin"') == 1, "yang sudah ditugaskan masih tampil"

    klien.post("/api/tugas/bagi", json={"pelabel": "anggi", "gambar": gambar[3:]})
    h = klien.get("/bagi?ds=bagi-uji").text
    assert "Semua gambar sudah ditugaskan" in h


def test_halaman_bagi_menolak_yang_bukan_pemilik(klien, aplikasi, lingkungan):
    """Anggota tetap boleh membuka halamannya, tetapi tidak diberi alatnya.

    Menyembunyikan halamannya sama sekali menyisakan teka-teki; halaman yang
    menjelaskan siapa pemiliknya tidak.
    """
    import pathlib

    from conftest import klien_baru
    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "bagi-hak", n=2)
    klien.post(f"/setsrc?path={d}")
    klien.post("/api/tugas/undang?akun=anggi")

    lain = klien_baru(aplikasi, "anggi", PW_ANGGI)
    h = lain.get("/bagi?ds=paul/bagi-hak").text
    assert "Hanya pemilik projek yang membagi tugas" in h
    assert 'id="bg-mulai"' not in h

    j = lain.get("/api/tugas/calon").json()
    assert j["ok"] is False and "pemilik" in j["error"]


def test_calon_pelabel_hanya_untuk_pemilik(klien, lingkungan):
    """Daftar akun adalah keterangan tentang orang.

    Membukanya ke siapa pun yang punya sesi berarti siapa pun bisa menyusun
    daftar seluruh anggota tim.
    """
    import pathlib

    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "calon-uji", n=2)
    klien.post(f"/setsrc?path={d}")

    j = klien.get("/api/tugas/calon").json()
    assert j["ok"] and {a["akun"] for a in j["akun"]} >= {"paul", "anggi"}
    assert next(a for a in j["akun"] if a["akun"] == "paul")["anggota"] is True


# ============================================================
# PAPAN ANOTASI
# ============================================================

def test_papan_menampilkan_tiga_kolom_dan_ringkasan_per_orang(klien, lingkungan):
    """Yang paling sering ditanyakan: si aditya sudah berapa persen."""
    import pathlib

    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    # 4 gambar, semuanya berlabel di fixture ini
    d = _projek(ruang, "papan-uji", n=4)
    klien.post(f"/setsrc?path={d}")
    gambar = sorted(str(x) for x in d.glob("*.jpg"))

    h = klien.get("/anotasi?ds=papan-uji").text
    # Ketiga kolomnya, dengan nama yang dipakai di seluruh aplikasi.
    assert "Belum ditugaskan" in h and "Dikerjakan" in h and "Dataset" in h
    # Kolom pertama menyebut UNGGAHANNYA, bukan satu angka gabungan: satu
    # unggahan besar dan lima unggahan kecil tidak boleh terlihat sama.
    assert "Tanpa nama unggahan" in h and ">4<" in h

    klien.post("/api/tugas/bagi", json={"pelabel": "anggi",
                                        "gambar": gambar[:2],
                                        "catatan": "catatan uji"})
    h = klien.get("/anotasi?ds=papan-uji").text
    assert "anggi" in h and "catatan uji" in h
    assert "100%" in h, "dua gambar berlabel dari dua ditugaskan"

    # Belum masuk dataset, jadi belum pindah ke kolom Selesai.
    assert "an-selesai" not in h
    klien.post("/api/tugas/dataset", json={"gambar": gambar[:2]})
    h = klien.get("/anotasi?ds=papan-uji").text
    assert "an-selesai" in h, "job yang seluruhnya di dataset pindah ke Selesai"


def test_papan_membubarkan_tugas_tidak_menghapus_labelnya(klien, lingkungan):
    """Yang hilang cuma penugasannya."""
    import pathlib

    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "bubar-uji", n=3)
    klien.post(f"/setsrc?path={d}")
    gambar = sorted(str(x) for x in d.glob("*.jpg"))
    j = klien.post("/api/tugas/bagi", json={"pelabel": "anggi",
                                            "gambar": gambar[:2]}).json()

    n_json = len(list(d.glob("*.json")))
    assert klien.post(f"/api/tugas/bubarkan?id={j['id']}").json()["ok"]
    assert len(list(d.glob("*.json"))) == n_json, "label ikut terhapus"

    h = klien.get("/anotasi?ds=bubar-uji").text
    assert h.count('class="an-batch"') == 1 and ">3<" in h, \
        "gambarnya kembali menganggur"


# ============================================================
# RINCIAN JOB
# ============================================================

def test_rincian_job_menyaring_dan_memindahkan_ke_dataset(klien, lingkungan):
    """Melabeli dan menyatakan selesai adalah dua tindakan terpisah."""
    import pathlib

    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "job-uji", n=4)
    klien.post(f"/setsrc?path={d}")
    gambar = sorted(str(x) for x in d.glob("*.jpg"))
    tid = klien.post("/api/tugas/bagi",
                     json={"pelabel": "paul", "gambar": gambar}).json()["id"]

    h = klien.get(f"/tugas/{tid}?ds=job-uji").text
    assert h.count('class="jb-ubin"') == 4
    assert 'id="jb-masukkan"' in h
    assert "0 sudah di dataset" in h

    klien.post("/api/tugas/dataset", json={"gambar": gambar[:2]})
    h = klien.get(f"/tugas/{tid}?ds=job-uji").text
    assert h.count("jb-cap-ds") == 2, "cap dataset tidak muncul"
    assert "2 sudah di dataset" in h

    # Dikeluarkan lagi.
    j = klien.post("/api/tugas/dataset",
                   json={"gambar": gambar[:2], "keluarkan": True}).json()
    assert j["ok"] and j["dikeluarkan"] == 2
    assert "0 sudah di dataset" in klien.get(f"/tugas/{tid}?ds=job-uji").text


def test_rincian_job_hanya_bisa_diubah_pelabelnya_atau_pemilik(klien, aplikasi,
                                                               lingkungan):
    import pathlib

    from conftest import klien_baru
    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "job-hak", n=3)
    klien.post(f"/setsrc?path={d}")
    gambar = sorted(str(x) for x in d.glob("*.jpg"))
    tid = klien.post("/api/tugas/bagi",
                     json={"pelabel": "anggi", "gambar": gambar[:2]}).json()["id"]

    # Pemilik: boleh.
    assert 'id="jb-masukkan"' in klien.get(f"/tugas/{tid}?ds=job-hak").text

    # Pelabelnya sendiri: boleh.
    lain = klien_baru(aplikasi, "anggi", PW_ANGGI)
    h = lain.get(f"/tugas/{tid}?ds=paul/job-hak").text
    assert 'id="jb-masukkan"' in h

    # Gambar yang bukan tugasnya tetap ditolak di rutenya.
    j = lain.post("/api/tugas/dataset", json={"gambar": gambar[2:]}).json()
    assert j["ok"] is False and "bukan tugasmu" in j["error"], j


def test_job_yang_tidak_ada_kembali_ke_papan(klien, lingkungan):
    import pathlib

    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    _projek(ruang, "job-hilang", n=2)
    r = klien.get("/tugas/tidak-ada?ds=job-hilang", follow_redirects=False)
    assert r.status_code == 303 and "/anotasi" in r.headers["location"]


def test_path_di_atribut_tidak_di_quote(klien, lingkungan):
    """Regresi atas bug yang hanya muncul pada projek bernama dengan spasi.

    imgpath meng-quote path untuk query string. Dipakai di data-path, nilainya
    dibaca JavaScript lewat dataset dan dikirim MENTAH di dalam bodi JSON,
    tanpa ada yang meng-unquote-nya lagi. Projek "Ada Spasi" karena itu
    mengirim path berisi %20, dan server menjawab "tidak satu pun gambar itu
    ada di projek ini" untuk gambar yang jelas ada.

    Seluruh projek tanpa spasi melewati jalur yang sama tanpa gejala apa pun.
    """
    import pathlib

    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "Ada Spasi", n=2)
    klien.post(f"/setsrc?path={d}")

    h = klien.get("/bagi?ds=Ada Spasi").text
    assert "%20" not in h.split('data-path="')[1].split('"')[0]

    # Dan path dari atribut itu benar-benar diterima rutenya.
    import re
    paths = re.findall(r'class="bg-ubin" data-path="([^"]+)"', h)
    assert paths, "tidak ada ubin"
    j = klien.post("/api/tugas/bagi",
                   json={"pelabel": "anggi", "gambar": paths}).json()
    assert j["ok"] and j["n"] == 2, j


# ============================================================
# GRID DAN KANVAS
# ============================================================

def test_grid_menyaring_tugasku_dan_menandai_pemiliknya(klien, aplikasi,
                                                        lingkungan):
    """Di projek 10.000 gambar, jatah seseorang bisa 200.

    Tanpa saringan ini ia menggulir sembilan ribu delapan ratus gambar milik
    orang lain untuk menemukan pekerjaannya sendiri.
    """
    import pathlib

    from conftest import klien_baru
    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "grid-tugas", n=4)
    klien.post(f"/setsrc?path={d}")
    gambar = sorted(str(x) for x in d.glob("*.jpg"))

    # Belum dibagi: chipnya tidak muncul sama sekali.
    assert "Tugasku" not in klien.get("/").text

    klien.post("/api/tugas/bagi", json={"pelabel": "anggi", "gambar": gambar[:2]})
    klien.post("/api/tugas/bagi", json={"pelabel": "paul", "gambar": gambar[2:]})

    h = klien.get("/").text
    assert "Tugasku" in h and h.count("tugas-cap") == 4
    assert klien.get("/?f=tugasku").text.count('class="card"') == 2

    lain = klien_baru(aplikasi, "anggi", PW_ANGGI)
    assert lain.get("/?ds=paul/grid-tugas&f=tugasku").text.count('class="card"') == 2


def test_kanvas_jujur_sejak_dibuka_bukan_menolak_saat_disimpan(klien, aplikasi,
                                                               lingkungan):
    """Kanvas yang membiarkan orang menggambar lalu menolak saat Simpan ditekan
    membuang pekerjaannya; yang jujur sejak dibuka tidak."""
    import pathlib

    from conftest import klien_baru
    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "kanvas-hak", n=3)
    klien.post(f"/setsrc?path={d}")
    gambar = sorted(str(x) for x in d.glob("*.jpg"))
    klien.post("/api/tugas/bagi", json={"pelabel": "anggi", "gambar": gambar[:1]})

    # Pemilik: boleh menyunting apa pun, termasuk yang ditugaskan ke orang lain.
    assert "data-baca-saja" not in klien.get(f"/label?path={gambar[0]}").text

    lain = klien_baru(aplikasi, "anggi", PW_ANGGI)
    lain.get("/?ds=paul/kanvas-hak")

    # Jatahnya sendiri: kanvas penuh.
    assert "data-baca-saja" not in lain.get(f"/label?path={gambar[0]}").text

    # Bukan jatahnya: baca saja, beserta alasannya di layar.
    h = lain.get(f"/label?path={gambar[1]}").text
    assert "data-baca-saja" in h
    assert "Hanya bisa dilihat" in h and "belum ditugaskan" in h

    # Dan rutenya tetap menolak walau kanvasnya dipaksa mengirim.
    j = lain.post("/api/simpan", json={"path": gambar[1], "shapes": []}).json()
    assert j["ok"] is False


# ============================================================
# DATASET MENENTUKAN EKSPOR
# ============================================================

def test_projek_warisan_mengekspor_semuanya_seperti_sebelumnya(klien, lingkungan):
    """Tidak ada satu pun projek lama yang ekspornya berubah."""
    import pathlib

    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "warisan-ekspor", n=4)
    klien.post(f"/setsrc?path={d}")

    j = klien.get("/api/ekspor/ringkasan?format=yolo-seg&split=8:1:1").json()
    assert j["warisan"] is True
    assert j["n_semua"] == j["n_dataset"] == 4
    assert klien.get("/ekspor?format=yolo-seg&gambar=0").status_code == 200


def test_ekspor_dan_splitting_hanya_memakai_isi_dataset(klien, lingkungan):
    """Yang sudah di-add itulah yang di-splitting, diberi versi, dan diekspor."""
    import io
    import pathlib
    import zipfile

    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "ekspor-tugas", n=5)
    klien.post(f"/setsrc?path={d}")
    gambar = sorted(str(x) for x in d.glob("*.jpg"))
    klien.post("/api/tugas/bagi", json={"pelabel": "paul", "gambar": gambar})

    # Berkas tugas sudah ada, dataset masih kosong: ekspor DITOLAK dengan
    # sebabnya, bukan mengirim ZIP kosong.
    r = klien.get("/ekspor?format=yolo-seg&gambar=0")
    assert r.status_code == 409 and "Tambahkan ke dataset" in r.text
    j = klien.post("/api/split/jalankan?split=8:1:1").json()
    assert j["ok"] is False and "dataset" in j["error"]

    klien.post("/api/tugas/dataset", json={"gambar": gambar[:2]})
    j = klien.get("/api/ekspor/ringkasan?format=yolo-seg&split=8:1:1").json()
    assert j["n_semua"] == 5 and j["n_dataset"] == 2 and j["gambar"] == 2

    # Isi ZIP harus sama persis dengan yang dihitung ringkasan.
    r = klien.get("/ekspor?format=yolo-seg&gambar=0")
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    label = [x for x in z.namelist() if "/labels/" in x and x.endswith(".txt")]
    assert len(label) == 2, label


def test_berkas_pendamping_tidak_dikira_anotasi(tmp_path):
    """Regresi: berkas pendamping kita sendiri merusak pemindaian.

    .tag.json dan .tugas.json ikut terhitung sebagai anotasi labelme, sehingga
    dataset YOLO yang sudah pernah ditugaskan diperingatkan "memuat anotasi
    labelme DAN YOLO sekaligus" — peringatan yang muncul justru KARENA
    memakai fitur penugasannya.
    """
    import cv2
    import numpy as np

    from app.services import scanner

    d = tmp_path / "ds"
    (d / "images").mkdir(parents=True)
    (d / "labels").mkdir()
    cv2.imwrite(str(d / "images" / "a.jpg"), np.zeros((40, 40, 3), np.uint8))
    (d / "labels" / "a.txt").write_text("0 0.5 0.5 0.2 0.2\n")
    (d / "data.yaml").write_text("names:\n  0: botol\n")
    assert scanner.periksa_kelengkapan(d) == []

    (d / tag_mod.BERKAS).write_text('{"versi":1,"gambar":{}}')
    (d / tugas.BERKAS).write_text('{"versi":1,"pemilik":"x"}')
    assert scanner.periksa_kelengkapan(d) == [], "berkas pendamping dikira anotasi"

    # Dan isinya tidak ikut terbaca sebagai gambar/anotasi.
    items, _ = scanner.scan(d)
    assert len(items) == 1 and items[0]["img"].name == "a.jpg"


# ============================================================
# VERSI
# ============================================================

def test_versi_tetap_sama_walau_dataset_bertambah(klien, lingkungan):
    """Itu seluruh alasan versi ada.

    Tanpa ini, satu-satunya cara mengetahui data apa yang dipakai melatih
    sebuah model adalah mengingatnya.
    """
    import io
    import pathlib
    import zipfile

    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "versi-uji", n=6)
    klien.post(f"/setsrc?path={d}")
    gambar = sorted(str(x) for x in d.glob("*.jpg"))
    klien.post("/api/tugas/bagi", json={"pelabel": "paul", "gambar": gambar})
    klien.post("/api/tugas/dataset", json={"gambar": gambar[:3]})

    j = klien.post("/api/versi/buat?split=8:1:1&catatan=tiga pertama").json()
    assert j["ok"] and j["nomor"] == 1 and j["n"] == 3, j

    def isi_zip(url):
        r = klien.get(url)
        assert r.status_code == 200, r.status_code
        z = zipfile.ZipFile(io.BytesIO(r.content))
        return sorted(x for x in z.namelist()
                      if "/labels/" in x and x.endswith(".txt"))

    v1 = isi_zip("/ekspor?nomor=1&format=yolo-seg&gambar=0")
    assert len(v1) == 3

    # Dataset bertambah; ekspor biasa ikut, versi TIDAK.
    klien.post("/api/tugas/dataset", json={"gambar": gambar[3:]})
    assert klien.get(
        "/api/ekspor/ringkasan?format=yolo-seg&split=8:1:1").json()["n_dataset"] == 6
    assert isi_zip("/ekspor?nomor=1&format=yolo-seg&gambar=0") == v1

    # Versi kedua memotret keadaan yang baru.
    assert klien.post("/api/versi/buat?split=8:1:1").json()["n"] == 6
    assert len(isi_zip("/ekspor?nomor=2&format=yolo-seg&gambar=0")) == 6


def test_daftar_versi_tidak_membawa_petanya(klien, lingkungan):
    """Peta bisa berisi puluhan ribu baris dan tidak dipakai menampilkan daftar."""
    import pathlib

    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "versi-daftar", n=4)
    klien.post(f"/setsrc?path={d}")
    gambar = sorted(str(x) for x in d.glob("*.jpg"))
    klien.post("/api/tugas/bagi", json={"pelabel": "paul", "gambar": gambar})
    klien.post("/api/tugas/dataset", json={"gambar": gambar})
    klien.post("/api/versi/buat?split=8:1:1")

    from app.services import versi as svc_versi

    ruang_p = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    v = svc_versi.daftar(ruang_p / "versi-daftar")
    assert len(v) == 1 and "peta" not in v[0] and "gambar" not in v[0]
    assert v[0]["jumlah"] and sum(v[0]["jumlah"].values()) == 4

    h = klien.get("/versi?ds=versi-daftar").text
    assert "v1" in h and "Buat versi" in h

    assert klien.post("/api/versi/hapus?nomor=1").json()["ok"]
    assert "Belum ada versi" in klien.get("/versi?ds=versi-daftar").text


def test_versi_tanpa_gambar_di_dataset_ditolak(klien, lingkungan):
    import pathlib

    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "versi-kosong", n=2)
    klien.post(f"/setsrc?path={d}")
    gambar = sorted(str(x) for x in d.glob("*.jpg"))
    klien.post("/api/tugas/bagi", json={"pelabel": "paul", "gambar": gambar})

    j = klien.post("/api/versi/buat?split=8:1:1").json()
    assert j["ok"] is False and "dataset" in j["error"]


def test_kolom_belum_ditugaskan_dikelompokkan_per_unggahan(klien, lingkungan):
    """Satu angka gabungan tidak memberi tahu apa pun tentang asalnya.

    Satu unggahan besar dan lima unggahan kecil terlihat sama, padahal
    keputusan membaginya hampir selalu per unggahan.
    """
    import pathlib

    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "batch-papan", n=6)
    klien.post(f"/setsrc?path={d}")
    g = sorted(str(x) for x in d.glob("*.jpg"))
    klien.post("/api/tag/pasang", json={"paths": g[:4], "batch": "Folder pagi"})
    klien.post("/api/tag/pasang", json={"paths": g[4:], "batch": "Folder sore"})

    h = klien.get("/anotasi?ds=batch-papan").text
    assert h.count('class="an-batch"') == 2
    assert "Folder pagi" in h and "Folder sore" in h
    # Yang besar didahulukan; tanpa nama selalu terakhir.
    assert h.index("Folder pagi") < h.index("Folder sore")

    # Tombolnya membawa nama unggahannya, dan halaman bagi membatasi ke situ.
    assert "batch=Folder%20pagi" in h or "batch=Folder+pagi" in h
    hb = klien.get("/bagi?ds=batch-papan&batch=Folder pagi").text
    assert hb.count('class="bg-ubin"') == 4, "halaman bagi tidak dibatasi"
    assert klien.get("/bagi?ds=batch-papan").text.count('class="bg-ubin"') == 6


def test_kolom_dataset_menautkan_ke_gambarnya(klien, lingkungan):
    import pathlib

    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "dataset-tautan", n=3)
    klien.post(f"/setsrc?path={d}")
    g = sorted(str(x) for x in d.glob("*.jpg"))
    klien.post("/api/tugas/bagi", json={"pelabel": "paul", "gambar": g})
    klien.post("/api/tugas/dataset", json={"gambar": g[:2]})

    h = klien.get("/anotasi?ds=dataset-tautan").text
    assert "Lihat semua 2 gambar di dataset" in h
    assert 'href="/?ds=dataset-tautan"' in h
