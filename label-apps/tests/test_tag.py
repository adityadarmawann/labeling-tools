"""
Uji tag dan nama unggahan.

Tag disimpan di berkas pendamping, terpisah dari anotasi. Yang dijaga di sini
justru akibat dari pilihan itu: berkas pendampingnya tidak boleh menggagalkan
apa pun kalau rusak, tidak boleh tumbuh menyimpan catatan gambar yang sudah
hilang, dan rutenya tidak boleh mau menulis untuk gambar di luar dataset yang
sedang dibuka.
"""
from __future__ import annotations

import json

from app.services import tag


def _ds(tmp_path):
    d = tmp_path / "projek"
    d.mkdir()
    return d


# ============================================================
# PENYIMPANAN
# ============================================================

def test_tag_bertahan_dan_dihitung_per_nama(tmp_path):
    d = _ds(tmp_path)
    tag.pasang(d, ["a.jpg", "b.jpg"], tambah=["sesi pagi"], batch="Unggahan 1")
    tag.pasang(d, ["b.jpg"], tambah=["lampu redup"])

    data = tag.baca(d)
    assert tag.untuk(data, "a.jpg") == {"batch": "Unggahan 1", "tag": ["sesi pagi"]}
    assert tag.untuk(data, "b.jpg")["tag"] == ["sesi pagi", "lampu redup"]
    assert tag.hitung(data)["tag"] == {"lampu redup": 1, "sesi pagi": 2}
    assert tag.hitung(data)["batch"] == {"Unggahan 1": 2}


def test_tag_yang_dibuang_hilang_dan_entri_kosong_tidak_ditinggal(tmp_path):
    """Entri kosong yang menumpuk membuat berkasnya tumbuh tanpa isi."""
    d = _ds(tmp_path)
    tag.pasang(d, ["a.jpg"], tambah=["x", "y"])
    tag.pasang(d, ["a.jpg"], buang=["x"])
    assert tag.untuk(tag.baca(d), "a.jpg")["tag"] == ["y"]

    tag.pasang(d, ["a.jpg"], buang=["y"])
    assert tag.baca(d)["gambar"] == {}


def test_tag_yang_sama_tidak_masuk_dua_kali(tmp_path):
    d = _ds(tmp_path)
    tag.pasang(d, ["a.jpg"], tambah=["botol"])
    tag.pasang(d, ["a.jpg"], tambah=["botol", "botol"])
    assert tag.untuk(tag.baca(d), "a.jpg")["tag"] == ["botol"]


def test_koma_dan_titik_koma_dibuang_dari_nama_tag(tmp_path):
    """Keduanya dipakai sebagai pemisah saat tag ikut ke berkas ekspor.

    Dibiarkan, satu tag bernama "pagi,sore" membuat berkas itu terbaca sebagai
    dua kolom yang bergeser untuk seluruh baris sesudahnya.
    """
    assert tag.bersihkan_tag("pagi,sore") == "pagi sore"
    assert tag.bersihkan_tag("a;b") == "a b"
    assert tag.bersihkan_tag("   ") == ""
    assert len(tag.bersihkan_tag("x" * 200)) == tag.MAKS_TAG


def test_berkas_tag_rusak_tidak_menggagalkan_apa_pun(tmp_path):
    """Tag itu keterangan tambahan.

    Kehilangannya jauh lebih ringan daripada membuat seluruh projek tidak bisa
    dibuka gara-gara satu berkas pendamping yang terpotong.
    """
    d = _ds(tmp_path)
    (d / tag.BERKAS).write_text("{ ini bukan json")
    assert tag.baca(d) == {"versi": tag.VERSI, "gambar": {}}
    tag.pasang(d, ["a.jpg"], tambah=["x"])          # tetap bisa ditulis ulang
    assert tag.untuk(tag.baca(d), "a.jpg")["tag"] == ["x"]


def test_catatan_gambar_yang_sudah_hilang_dirapikan(tmp_path):
    d = _ds(tmp_path)
    tag.pasang(d, ["ada.jpg", "hilang.jpg"], tambah=["x"])
    assert tag.rapikan(d, {"ada.jpg"}) == 1
    assert list(tag.baca(d)["gambar"]) == ["ada.jpg"]
    assert tag.rapikan(d, {"ada.jpg"}) == 0, "tidak ada yang perlu ditulis lagi"


def test_kunci_memakai_jalur_relatif_supaya_split_tidak_bertabrakan(tmp_path):
    """Dataset YOLO menyimpan gambarnya di images/train dan images/val.

    Dengan nama telanjang, `images/train/a.jpg` dan `images/val/a.jpg` menjadi
    satu kunci, dan menandai yang satu ikut menandai yang lain.
    """
    d = _ds(tmp_path)
    for sub in ("images/train", "images/val"):
        (d / sub).mkdir(parents=True)
        (d / sub / "a.jpg").write_bytes(b"x")
    k1 = tag.kunci_gambar(d, d / "images/train/a.jpg")
    k2 = tag.kunci_gambar(d, d / "images/val/a.jpg")
    assert k1 == "images/train/a.jpg" and k2 == "images/val/a.jpg"


# ============================================================
# RUTE
# ============================================================

def test_rute_tag_menolak_gambar_di_luar_dataset_terbuka(klien, lingkungan):
    """Tanpa penyaringan ini, rute ini bisa dipakai menulis nama berkas apa
    pun ke dalam berkas tag milik projek orang lain."""
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    src = lingkungan["roots"] / "ds-alpha"
    klien.post(f"/setsrc?path={src}")

    j = klien.post("/api/tag/pasang", json={
        "paths": ["/etc/passwd", "/tmp/bukan-punyaku.jpg"],
        "tambah": ["x"]}).json()
    assert j["ok"] is False and "tidak satu pun" in j["error"], j
    assert not (src / tag.BERKAS).exists(), "berkas tag terlanjur ditulis"


def test_rute_tag_menandai_lalu_menghitungnya(klien, lingkungan):
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    src = lingkungan["roots"] / "ds-alpha"
    klien.post(f"/setsrc?path={src}")
    gambar = sorted(str(p) for p in src.glob("*.jpg"))
    assert gambar, "dataset uji tidak punya gambar"

    j = klien.post("/api/tag/pasang", json={
        "paths": gambar[:2], "tambah": ["sesi pagi"],
        "batch": "Unggahan uji"}).json()
    assert j["ok"] and j["n"] == 2, j
    assert j["tag"] == {"sesi pagi": 2}
    assert j["batch"] == {"Unggahan uji": 2}

    j = klien.get("/api/tag/daftar").json()
    assert j["ok"] and j["tag"] == {"sesi pagi": 2}

    isi = json.loads((src / tag.BERKAS).read_text())
    assert len(isi["gambar"]) == 2


def test_tag_tanpa_dataset_terbuka_ditolak_dengan_jelas(klien, lingkungan):
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    j = klien.get("/api/tag/daftar").json()
    assert j["ok"] is False and "dataset" in j["error"]
