"""Pratinjau anotasi di halaman "Buka hasil augmentasi".

Dua hal yang diperbaiki di sini, dan yang kedua jauh lebih serius daripada
yang pertama:

1. Grid hasil versi cuma memperlihatkan gambar polos. Tidak ada kotak,
   poligon, atau rangka keypoint, padahal justru itu yang perlu diperiksa
   orang setelah membangun versi.

2. Baris YOLO-pose tak pernah terbaca. Panjangnya 1 + 4 + 3K, dan parser
   lama hanya mengenal 4 nilai (kotak) atau nilai genap >= 6 (poligon).
   Akibatnya bergantung pada K:
     - K ganjil (court-basketball, K=33 -> 103 nilai): barisnya DIBUANG,
       seluruh 1624 gambar terhitung "negatif" dan hitungan kelasnya nol.
     - K genap (court-badminton, K=30 -> 94 nilai): barisnya terbaca sebagai
       poligon 47 titik dari campuran x, y, dan nilai visibilitas -- jumlahnya
       kebetulan benar, geometrinya kacau.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import buatversi, olah
from tests.test_data import masuk, PW_PAUL
from tests.test_versi_buat import _ds, _mulai, _tunggu


def _baris_pose(K: int, kelas: int = 0) -> str:
    """Satu baris YOLO-pose sah: kelas + bbox + K x (x, y, v)."""
    kp = []
    for i in range(K):
        kp += [f"{0.10 + i * 0.001:.6f}", f"{0.20 + i * 0.001:.6f}", "2"]
    return " ".join([str(kelas), "0.5", "0.5", "0.4", "0.4"] + kp)


# ------------------------------------------------------------------ parser
@pytest.mark.parametrize("K", [33, 30])
def test_baris_pose_terbaca_utuh(tmp_path, K):
    p = tmp_path / "a.txt"
    p.write_text(_baris_pose(K) + "\n")
    b = olah.baca_bentuk(p, K)
    assert len(b) == 1
    assert b[0]["jenis"] == "pose"
    assert len(b[0]["kp"]) == K
    assert b[0]["kp"][0][2] == 2                      # visibilitas ikut terbawa
    assert len(b[0]["poli"]) == 8                     # bbox jadi 4 sudut


@pytest.mark.parametrize("K,lama", [(33, 0), (30, 1)])
def test_parser_lama_memang_keliru(tmp_path, K, lama):
    """Mengunci sebab kegagalannya, supaya tak ada yang 'merapikan'
    baca_bentuk kembali ke perilaku baca_label."""
    p = tmp_path / "a.txt"
    p.write_text(_baris_pose(K) + "\n")
    assert len(olah.baca_label(p)) == lama             # K ganjil hilang sama sekali
    if lama:                                           # K genap: terbaca, tapi salah
        # 4 nilai bbox + 3K nilai keypoint, dibaca sebagai (4+3K)/2 titik
        # poligon dari campuran x, y, dan v.
        assert len(olah.baca_label(p)[0][1]) == 4 + 3 * K


def test_kotak_dan_poligon_tak_berubah(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("0 0.5 0.5 0.2 0.4\n1 0.1 0.1 0.9 0.1 0.9 0.9\n")
    b = olah.baca_bentuk(p, 0)
    assert [x["jenis"] for x in b] == ["kotak", "poligon"]
    assert b[0]["poli"] == [0.4, 0.3, 0.6, 0.3, 0.6, 0.7, 0.4, 0.7]
    assert b[1]["poli"] == [0.1, 0.1, 0.9, 0.1, 0.9, 0.9]
    assert all(x["kp"] == [] for x in b)


def test_K_salah_tidak_memaksa_baris_jadi_pose(tmp_path):
    """Poligon 6 titik (12 nilai) di projek pose K=4 (juga 12+4=16) tidak boleh
    saling tertukar: pose dikenali dari panjang PERSIS 4+3K."""
    p = tmp_path / "a.txt"
    p.write_text("0 " + " ".join(["0.5"] * 12) + "\n")
    assert olah.baca_bentuk(p, 4)[0]["jenis"] == "poligon"


# ------------------------------------------------------------------ indeks
def test_indeks_versi_pose_tidak_semua_negatif(tmp_path):
    """Regresi langsung dari court-basketball v1: 1624 gambar, semuanya
    dilaporkan negatif dengan hitungan kelas kosong."""
    ds = tmp_path / "proj"
    v = buatversi.dir_versi(ds, 1)
    (v / "train" / "images").mkdir(parents=True)
    (v / "train" / "labels").mkdir(parents=True)
    (v / "data.yaml").write_text(
        "kpt_shape: [33, 3]\nnc: 1\nnames: ['court']\n")
    import cv2
    import numpy as np
    for i in range(3):
        cv2.imwrite(str(v / "train" / "images" / f"g{i}.jpg"),
                    np.zeros((40, 60, 3), dtype=np.uint8))
        (v / "train" / "labels" / f"g{i}.txt").write_text(_baris_pose(33) + "\n")
    buatversi._INDEKS_CACHE.clear()
    assert buatversi.kpt_versi(ds, 1) == 33
    idx = buatversi.indeks_hasil(ds, 1)
    assert idx["total"] == 3
    assert idx["negatif"] == 0
    assert idx["hitung_kelas"] == {"court": 3}
    assert idx["K"] == 33


def test_panel_detail_versi_pose_juga_benar(tmp_path):
    """Panel rincian di halaman Versi memakai jalur hitung yang berbeda
    (isi_versi), dan buta pose dengan cara yang sama: court-basketball v1
    melaporkan 1624 negatif dan nol objek."""
    ds = tmp_path / "proj"
    v = buatversi.dir_versi(ds, 1)
    (v / "train" / "images").mkdir(parents=True)
    (v / "train" / "labels").mkdir(parents=True)
    (v / "data.yaml").write_text("kpt_shape: [33, 3]\nnc: 1\nnames: ['court']\n")
    import cv2
    import numpy as np
    for i in range(4):
        cv2.imwrite(str(v / "train" / "images" / f"g{i}.jpg"),
                    np.zeros((40, 60, 3), dtype=np.uint8))
        (v / "train" / "labels" / f"g{i}.txt").write_text(_baris_pose(33) + "\n")
    isi = buatversi.isi_versi(ds, 1)
    assert isi["negatif"] == 0
    assert isi["objek"] == 4
    assert isi["per_kelas"] == {"court": 4}


# ------------------------------------------------------- tag nama kelas
def _gbr(tmp_path, w=400, h=300):
    import cv2
    import numpy as np
    p = tmp_path / "g.jpg"
    cv2.imwrite(str(p), np.full((h, w, 3), 90, dtype=np.uint8))
    return p


def test_nama_kelas_tergambar_di_bentuknya(tmp_path):
    """Bentuk berwarna saja tak menjawab "ini kelas apa": warnanya harus
    dihafal dulu. Nama kelas digambar sebagai tag, seperti grid anotasi."""
    from app.services import render
    img = _gbr(tmp_path)
    b = [{"kelas": 0, "jenis": "kotak", "kp": [],
          "poli": [0.2, 0.2, 0.6, 0.2, 0.6, 0.7, 0.2, 0.7]}]
    polos = render.gambar_versi(img, 400, b, ["kaleng"])
    kosong = render.gambar_versi(img, 400, [], [])
    assert polos is not None and kosong is not None
    # Tanpa teks, tak ada piksel putih/terang di luar bentuknya; dengan tag,
    # ada blok warna kelas + huruf -> bedanya jauh lebih dari sekadar garis.
    import numpy as np
    assert int(np.abs(polos.astype(int) - kosong.astype(int)).sum()) > 0

    tanpa_nama = render.gambar_versi(img, 400, b, [])      # nama_kelas kosong
    assert not np.array_equal(polos, tanpa_nama)           # teksnya memang beda


def test_tag_tak_saling_menimpa(tmp_path):
    """Objek berdekatan menaruh tagnya di tempat hampir sama. Dua nama yang
    bertindih membuat keduanya tak terbaca, jadi yang bentrok digeser turun."""
    from app.services import render
    dipakai = []
    import cv2
    import numpy as np
    im = np.zeros((300, 400, 3), dtype=np.uint8)
    for _ in range(4):
        render._tag_kelas(im, 50, 100, "player", (0, 180, 0), 0.5, dipakai)
    assert len(dipakai) == 4
    for i in range(len(dipakai)):
        for j in range(i + 1, len(dipakai)):
            assert not render._tabrak(dipakai[i], dipakai[j]), (dipakai[i], dipakai[j])


def test_tag_tak_keluar_dari_gambar(tmp_path):
    from app.services import render
    import numpy as np
    im = np.zeros((120, 160, 3), dtype=np.uint8)
    dipakai = []
    render._tag_kelas(im, 155, 2, "nama-kelas-yang-panjang", (200, 40, 40), 0.5,
                      dipakai)
    for x0, y0, x1, y1 in dipakai:
        assert x0 >= 0 and y0 >= 0
        assert y1 <= im.shape[0]


# ------------------------------------------------------------------ halaman
def test_halaman_hasil_punya_sakelar_label(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    d = _ds(klien, n_botol=6, n_kaleng=4, n_negatif=2)
    klien.post(f"/setsrc?path={d}")
    resep = {"volume": {"per_gambar": 0},
             "fase": {"crop_zoom": {"aktif": False}, "balans_skala": {"aktif": False},
                      "balans_kelas": {"aktif": False}, "porsi_negatif": {"aktif": False}}}
    assert _mulai(klien, resep).get("ok")
    assert _tunggu(klien).get("selesai")

    h = klien.get("/versi/hasil?nomor=1").text
    assert 'id="vh-label"' in h                       # sakelarnya ada
    assert "data-src=" in h                           # JS bisa menukar sumber
    # Berlabel memakai thumbnail lebih besar: nama kelas digambar DI dalam
    # gambar, dan pada 320 px hurufnya pecah saat kartunya ditampilkan penuh.
    assert "&s=480&label=1" in h
    # Menyalakan label beralih ke contain: dengan cover, anotasi yang jatuh di
    # bagian terpotong hilang tanpa tanda.
    assert "object-fit:contain" in h.replace(" ", "")


def test_gambar_versi_berlabel_beda_dari_polosnya(klien, lingkungan):
    """Rute benar-benar menggambar sesuatu: byte-nya harus berbeda, dan
    keduanya tetap JPEG yang sah."""
    masuk(klien, "paul", PW_PAUL)
    d = _ds(klien, n_botol=6, n_kaleng=4, n_negatif=0)
    klien.post(f"/setsrc?path={d}")
    resep = {"volume": {"per_gambar": 0},
             "fase": {"crop_zoom": {"aktif": False}, "balans_skala": {"aktif": False},
                      "balans_kelas": {"aktif": False}, "porsi_negatif": {"aktif": False}}}
    assert _mulai(klien, resep).get("ok")
    assert _tunggu(klien).get("selesai")

    idx = buatversi.indeks_hasil(d, 1)
    berisi = next(it for it in idx["items"] if not it["neg"])
    q = f"/versi/gambar?nomor=1&nama={berisi['nama']}&s=320"
    polos = klien.get(q)
    label = klien.get(q + "&label=1")
    assert polos.status_code == label.status_code == 200
    assert polos.headers["content-type"] == label.headers["content-type"] == "image/jpeg"
    assert len(label.content) > 100 and label.content[:2] == b"\xff\xd8"
    assert label.content != polos.content             # anotasinya benar-benar digambar


def test_gambar_negatif_berlabel_tetap_dilayani(klien, lingkungan):
    """Sampel negatif tidak punya bentuk apa pun. label=1 harus tetap memberi
    gambar polos, bukan 404."""
    masuk(klien, "paul", PW_PAUL)
    d = _ds(klien, n_botol=4, n_kaleng=2, n_negatif=4)
    klien.post(f"/setsrc?path={d}")
    resep = {"volume": {"per_gambar": 0},
             "fase": {"crop_zoom": {"aktif": False}, "balans_skala": {"aktif": False},
                      "balans_kelas": {"aktif": False}, "porsi_negatif": {"aktif": False}}}
    assert _mulai(klien, resep).get("ok")
    assert _tunggu(klien).get("selesai")

    idx = buatversi.indeks_hasil(d, 1)
    neg = next((it for it in idx["items"] if it["neg"]), None)
    if neg is None:
        pytest.skip("versi ini tak menyisakan sampel negatif")
    r = klien.get(f"/versi/gambar?nomor=1&nama={neg['nama']}&s=320&label=1")
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
