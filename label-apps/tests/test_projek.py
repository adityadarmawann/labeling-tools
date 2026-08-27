"""
Uji operasi projek.

Yang dijaga di sini bukan "tombolnya jalan", melainkan bahwa operasi yang
menyentuh ribuan berkas tidak bisa keluar dari ruang kerja akunnya, dan
tidak ada satu pun jalan yang menghapus pekerjaan orang tanpa bisa
dikembalikan.
"""
from __future__ import annotations

import json

import pytest

from app.services import projek


def _projek(root, nama, n=3, label=True):
    import cv2
    import numpy as np

    d = root / nama
    d.mkdir(parents=True, exist_ok=True)
    # Nama DAN isi dibedakan per projek. Kalau dua projek memakai nama berkas
    # yang sama dengan isi yang sama, mesin gabung benar melewatkannya sebagai
    # "sudah ada" — dan uji gabungnya jadi mengukur hal yang salah.
    cap = abs(hash(nama)) % 900 + 100
    for i in range(n):
        p = d / f"{nama[:6]}-IMG_2026063{i % 10}_08{i:02d}00.jpg"
        cv2.imwrite(str(p), np.full((40, 60, 3), (cap + i * 7) % 256, np.uint8))
        if label:
            p.with_suffix(".json").write_text(json.dumps({
                "version": "0.4.36", "flags": {}, "imagePath": p.name,
                "imageHeight": 40, "imageWidth": 60, "imageData": None,
                "shapes": [{"label": "botol", "shape_type": "polygon",
                            "points": [[2, 2], [30, 2], [30, 30]]}]}))
    return d


# ============================================================
# BATAS RUANG KERJA
# ============================================================

@pytest.mark.parametrize("jahat", [
    "../keluar", "../../etc", "/etc/passwd", "sub/dalam",
    "..", ".", "", "   ", "....//x",
])
def test_nama_tidak_bisa_menunjuk_keluar_ruang_kerja(tmp_path, jahat):
    """Nama projek datang dari kotak isian, jadi tidak boleh dipercaya.

    Tanpa dibersihkan, `../` saja cukup untuk memindahkan atau menimpa folder
    di luar ruang kerja akun itu.
    """
    root = tmp_path / "ruang"
    root.mkdir()
    (tmp_path / "keluar").mkdir()
    try:
        d = projek._folder(root, jahat)
    except projek.Tolak:
        return                      # ditolak mentah-mentah, itu sudah benar
    # Kalau diterima, hasilnya WAJIB tetap di dalam root.
    assert projek._didalam(d, root), (jahat, d)


def test_folder_sistem_tidak_muncul_sebagai_projek(tmp_path):
    root = tmp_path / "ruang"
    _projek(root, "punyaku")
    _projek(root, projek.SAMPAH)
    _projek(root, ".tersembunyi")
    nama = {p["nama"] for p in projek.daftar(root)}
    assert nama == {"punyaku"}


# ============================================================
# DAFTAR
# ============================================================

def test_kartu_membawa_angka_yang_dipakai_tampilan(tmp_path):
    root = tmp_path / "ruang"
    _projek(root, "berlabel", n=4, label=True)
    _projek(root, "polos", n=2, label=False)
    kartu = {p["nama"]: p for p in projek.daftar(root)}

    assert kartu["berlabel"]["jumlah"] == 4
    assert kartu["berlabel"]["anotasi"] == 4
    assert kartu["polos"]["jumlah"] == 2
    assert kartu["polos"]["anotasi"] == 0
    for p in kartu.values():
        assert p["sampul"].endswith(".jpg")
        assert p["usia"] and p["diubah"] > 0


# ============================================================
# GANTI NAMA
# ============================================================

def test_ganti_nama_menolak_menimpa_projek_lain(tmp_path):
    root = tmp_path / "ruang"
    _projek(root, "satu")
    _projek(root, "dua")
    with pytest.raises(projek.Tolak):
        projek.ganti_nama(root, "satu", "dua")
    # keduanya harus masih utuh
    assert (root / "satu").is_dir() and (root / "dua").is_dir()


def test_ganti_nama_memindahkan_isinya(tmp_path):
    root = tmp_path / "ruang"
    _projek(root, "lama", n=3)
    r = projek.ganti_nama(root, "lama", "baru sekali")
    assert r["nama"] == "baru sekali"
    assert not (root / "lama").exists()
    assert len(list((root / "baru sekali").glob("*.jpg"))) == 3


# ============================================================
# DUPLIKAT
# ============================================================

def test_duplikat_menyalin_penuh_dan_tidak_menyentuh_aslinya(tmp_path):
    root = tmp_path / "ruang"
    _projek(root, "asli", n=3)
    r = projek.duplikat(root, "asli")
    assert r["nama"] == "asli 2"
    assert len(list((root / "asli").glob("*.jpg"))) == 3
    assert len(list((root / "asli 2").glob("*.jpg"))) == 3
    assert len(list((root / "asli 2").glob("*.json"))) == 3
    # ditekan dua kali tidak boleh gagal
    assert projek.duplikat(root, "asli")["nama"] == "asli 3"


# ============================================================
# SAMPAH
# ============================================================

def test_membuang_memindahkan_bukan_menghapus(tmp_path):
    """Tidak ada rmtree di berkas pengguna.

    Satu klik keliru berarti ribuan gambar dan berjam-jam pelabelan. Yang
    dibuang harus punya jalan pulang.
    """
    root = tmp_path / "ruang"
    _projek(root, "penting", n=4)
    r = projek.ke_sampah(root, "penting")

    assert not (root / "penting").exists()
    assert "penting" not in {p["nama"] for p in projek.daftar(root)}
    # berkasnya masih ada, utuh
    dibuang = tmp_path / "ruang" / projek.SAMPAH
    sisa = list(dibuang.rglob("*.jpg"))
    assert len(sisa) == 4, sisa
    assert r["sampah"].startswith(str(dibuang))


def test_isi_sampah_dan_pulihkan(tmp_path):
    root = tmp_path / "ruang"
    _projek(root, "hilang", n=2)
    projek.ke_sampah(root, "hilang")

    isi = projek.isi_sampah(root)
    assert len(isi) == 1 and isi[0]["nama"] == "hilang"

    projek.pulihkan(root, isi[0]["folder"])
    assert (root / "hilang").is_dir()
    assert len(list((root / "hilang").glob("*.jpg"))) == 2
    assert projek.isi_sampah(root) == []


def test_pulih_tidak_menimpa_projek_bernama_sama(tmp_path):
    """Membuang "botol", membuat "botol" baru, lalu memulihkan yang lama."""
    root = tmp_path / "ruang"
    _projek(root, "botol", n=2)
    projek.ke_sampah(root, "botol")
    _projek(root, "botol", n=5)          # yang baru, isinya berbeda

    folder = projek.isi_sampah(root)[0]["folder"]
    r = projek.pulihkan(root, folder)
    assert r["nama"] != "botol"
    assert len(list((root / "botol").glob("*.jpg"))) == 5, "yang baru tertimpa"
    assert len(list((root / r["nama"]).glob("*.jpg"))) == 2


def test_pulihkan_menolak_folder_di_luar_sampah(tmp_path):
    root = tmp_path / "ruang"
    _projek(root, "punyaku")
    for jahat in ("../punyaku", "..", "/etc"):
        with pytest.raises(projek.Tolak):
            projek.pulihkan(root, jahat)
    assert (root / "punyaku").is_dir()


# ============================================================
# GABUNG
# ============================================================

def test_gabung_menyalin_dan_tidak_menghapus_sumbernya(tmp_path):
    """Menggabungkan lalu membuang yang lama adalah dua keputusan berbeda."""
    root = tmp_path / "ruang"
    _projek(root, "sumber", n=3)
    _projek(root, "tujuan", n=2)

    r = projek.gabung(root, "sumber", "tujuan")
    assert r["ditambah"] > 0
    assert (root / "sumber").is_dir(), "sumbernya ikut hilang"
    assert len(list((root / "sumber").glob("*.jpg"))) == 3
    assert len(list((root / "tujuan").rglob("*.jpg"))) == 5


def test_gabung_menolak_ke_dirinya_sendiri(tmp_path):
    root = tmp_path / "ruang"
    _projek(root, "satu")
    with pytest.raises(projek.Tolak):
        projek.gabung(root, "satu", "satu")


# ============================================================
# RUTE
# ============================================================

def test_rute_hanya_menyentuh_ruang_kerja_akun_itu(klien, lingkungan):
    """Folder dataset bersama boleh dibaca, tidak boleh diubah dari sini.

    Isinya dipakai orang lain dan sebagian milik proyek lain di mesin yang
    sama. Rute projek tidak menerima path bebas sama sekali; yang dikirim
    hanya NAMA, dan rootnya ditentukan server.
    """
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    bersama = lingkungan["roots"] / "ds-alpha"
    assert bersama.is_dir(), "dataset bersama untuk uji ini tidak ada"

    j = klien.post(f"/api/projek/sampah?nama={bersama.name}").json()
    assert j["ok"] is False
    assert bersama.is_dir(), "dataset bersama ikut terbuang"

    j = klien.post("/api/projek/ganti-nama"
                   f"?nama=../{bersama.parent.name}/{bersama.name}&baru=x").json()
    assert j["ok"] is False
    assert bersama.is_dir()


def test_rute_daftar_menandai_projek_yang_sedang_dibuka(klien, lingkungan,
                                                        tmp_path):
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    ruang = klien.get("/api/projek/daftar").json()["ruang"]
    _projek(__import__("pathlib").Path(ruang), "punyaku", n=3)

    j = klien.get("/api/projek/daftar").json()
    kartu = {p["nama"]: p for p in j["projek"]}
    assert "punyaku" in kartu and kartu["punyaku"]["dibuka"] is False

    klien.post(f"/setsrc?path={kartu['punyaku']['path']}")
    kartu = {p["nama"]: p for p in klien.get("/api/projek/daftar").json()["projek"]}
    assert kartu["punyaku"]["dibuka"] is True


def test_membuang_projek_yang_sedang_dibuka_menutup_sesinya(klien, lingkungan):
    """Sesinya menunjuk folder yang tidak ada lagi.

    Dibiarkan, halaman grid tetap menampilkan daftar gambar dari ingatan dan
    setiap gambar baru gagal saat dibuka, satu per satu, tanpa menjelaskan
    sebabnya.
    """
    import pathlib

    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "sedang-dipakai", n=3)
    klien.post(f"/setsrc?path={d}")

    j = klien.post("/api/projek/sampah?nama=sedang-dipakai").json()
    assert j["ok"] and j["sesi_ditutup"] is True
    assert klien.get("/api/projek/daftar").json()["ruang"] == str(ruang)


def test_sampul_menolak_berkas_di_luar_ruang_kerja(klien, lingkungan):
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    luar = lingkungan["tmp"] / "rahasia.jpg"
    import cv2
    import numpy as np
    cv2.imwrite(str(luar), np.zeros((10, 10, 3), np.uint8))
    r = klien.get(f"/api/projek/sampul?path={luar}")
    assert r.status_code == 404
