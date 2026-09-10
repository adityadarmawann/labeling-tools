"""
Unduhan dataset ASLI: satu ZIP datar images/ + labels/.

Ini kebalikan dari ekspor Versi. Di sana yang keluar adalah HASIL sebuah resep
— sudah dibelah train/valid/test, sudah dipreprocessing, sudah diaugmentasi —
dan bentuk itu tidak bisa diunggah balik tanpa menjadi dataset yang berbeda
dari yang dimulai. Di sini tidak ada satu pun keputusan yang dibekukan ke dalam
ZIP-nya, dan yang keluar bisa langsung diunggah lagi ke HIGOLAB.
"""
import io
import json
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.services import export as ex


def _gambar(d: Path, nama: str, kelas: str | None = "botol"):
    d.mkdir(parents=True, exist_ok=True)
    p = d / nama
    cv2.imwrite(str(p), np.full((40, 60, 3), 80, np.uint8))
    p.with_suffix(".json").write_text(json.dumps({
        "version": "0.4.36", "flags": {}, "imagePath": p.name,
        "imageHeight": 40, "imageWidth": 60, "imageData": None,
        "shapes": ([] if kelas is None else
                   [{"label": kelas, "shape_type": "rectangle",
                     "points": [[2, 2], [30, 30]]}])}))
    return p


# ============================================================
# MEMISAHKAN SALINAN MESIN
# ============================================================

def test_turunan_dipisah_beserta_angkanya():
    """Angkanya bukan hiasan.

    Pada dataset yang diunggah dalam keadaan sudah ber-aug-bal, foto aslinya
    bisa tinggal seperlima isi folder. Unduhan yang diam-diam memuat seperlima
    itu adalah cara terburuk memberi tahu orang bahwa datasetnya pernah
    diaugmentasi.
    """
    butir = [{"img": Path(n), "shapes": []} for n in (
        "a1.jpg", "a2.jpg", "a1_aug1.jpg", "a1_swout0.jpg",
        "a2_p5crop_normal.jpg", "a1_bal3_12.jpg", "a2_neg1.jpg")]
    asli, sisa = ex.pisah_turunan(butir)
    assert [p["img"].name for p in asli] == ["a1.jpg", "a2.jpg"]
    assert sisa == {"aug": 3, "bal": 2}


# ============================================================
# BENTUK ZIP
# ============================================================

@pytest.fixture
def zip_asli(tmp_path):
    # Butirnya dibaca lewat scanner, bukan disusun tangan: bentuk dalam yang
    # dipakai `baris_yolo` (W/H, shapes[].type) bukan bentuk berkas labelme,
    # dan butir buatan tangan menguji sesuatu yang tidak pernah dilewati kode
    # sungguhan.
    from app.services import scanner

    d = tmp_path / "ds"
    for nama, kelas in (("a1.jpg", "botol"), ("a2.jpg", "kaleng"),
                        ("a3.jpg", None)):
        _gambar(d, nama, kelas)
    items, names = scanner.scan(d)
    data = ex.zip_asli(items, "ds", names=names, sisa={"aug": 7, "bal": 2})
    return zipfile.ZipFile(io.BytesIO(data)), items


def test_tata_letak_datar_tanpa_split(zip_asli):
    z, _ = zip_asli
    nama = [n for n in z.namelist() if not n.endswith("/")]
    assert sorted(n for n in nama if n.startswith("images/")) == [
        "images/a1.jpg", "images/a2.jpg", "images/a3.jpg"]
    assert sorted(n for n in nama if n.startswith("labels/")) == [
        "labels/a1.txt", "labels/a2.txt", "labels/a3.txt"]
    # Tidak satu pun entri berada di dalam train/, valid/ atau test/ — itu
    # justru yang membedakannya dari ekspor Versi.
    assert not [n for n in z.namelist()
                if n.split("/")[0] in ("train", "valid", "test")], z.namelist()


def test_gambar_tanpa_objek_tetap_dapat_label_kosong(zip_asli):
    """Penanda contoh negatif yang sah, bukan label yang lupa ditulis."""
    z, _ = zip_asli
    assert z.read("labels/a3.txt").decode().strip() == ""
    assert z.read("labels/a1.txt").decode().strip() != ""


def test_data_yaml_menunjuk_folder_yang_benar_benar_ada(zip_asli):
    """`train: ../train/images` akan menunjuk folder yang tidak ada di sini.

    Perkakas latih yang membaca data.yaml ini akan mengeluh tentang path yang
    hilang, dan pesannya tidak menyebut sama sekali bahwa penyebabnya bentuk
    ZIP-nya datar.
    """
    z, _ = zip_asli
    y = z.read("data.yaml").decode()
    assert "train: ../images" in y
    assert "val: ../images" in y
    assert "../train/images" not in y
    assert "nc: 2" in y
    assert "'botol'" in y and "'kaleng'" in y


def test_readme_menyebut_yang_tidak_ikut_diunduh(zip_asli):
    z, _ = zip_asli
    r = z.read("README.txt").decode()
    assert "Gambar : 3" in r
    assert "Hasil augmentasi   : 7" in r
    assert "Hasil penyeimbangan: 2" in r
    assert "diunggah kembali" in r


def test_nama_berkas_kembar_tidak_saling_menimpa(tmp_path):
    """Dataset bersplit yang diratakan punya dua `dup.jpg` dari folder berbeda.

    Tanpa penomoran, yang kedua menimpa yang pertama di dalam ZIP dan yang
    hilang tidak disebut di mana pun.
    """
    from app.services import scanner

    for sub, kelas in (("sub1", "botol"), ("sub2", "kaleng")):
        _gambar(tmp_path / "ds" / sub, "dup.jpg", kelas)
    items, names = scanner.scan(tmp_path / "ds")
    assert len(items) == 2, [i["img"] for i in items]
    z = zipfile.ZipFile(io.BytesIO(ex.zip_asli(items, "ds", names=names)))
    nama = [n for n in z.namelist() if not n.endswith("/")]
    assert len(nama) == len(set(nama)), f"entri ZIP ganda: {nama}"
    assert len([n for n in nama if n.startswith("images/")]) == 2, nama
    baris = [b for n in nama if n.startswith("labels/")
             for b in z.read(n).decode().splitlines() if b.strip()]
    assert len({b.split()[0] for b in baris}) == 2, f"satu kelas hilang: {baris}"


# ============================================================
# RUTE
# ============================================================

def _projek_beraugbal(klien):
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    ruang = Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = ruang / "beraugbal"
    for nama in ("a1.jpg", "a2.jpg", "a1_aug1.jpg", "a1_bal3_12.jpg"):
        _gambar(d, nama)
    r = klien.post(f"/setsrc?path={d}")
    assert r.json()["n"] == 4, r.text
    return d


def test_ringkasan_menyebut_berapa_yang_ikut_dan_berapa_yang_tidak(klien,
                                                                   lingkungan):
    klien_ = klien
    _projek_beraugbal(klien_)
    j = klien_.get("/api/ekspor/asli").json()
    assert j["ok"] and j["n"] == 2, j
    assert j["aug"] == 1 and j["bal"] == 1, j
    assert j["n_obj"] == 2, j


def test_rute_mengunduh_zip_datar_berisi_foto_asli_saja(klien, lingkungan):
    _projek_beraugbal(klien)
    r = klien.get("/ekspor/asli")
    assert r.status_code == 200, r.text[:300]
    assert "attachment" in r.headers["content-disposition"]
    assert "beraugbal-asli.zip" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(n for n in z.namelist() if n.startswith("images/")
                  and not n.endswith("/")) == ["images/a1.jpg", "images/a2.jpg"]


def test_tanpa_dataset_terbuka_ditolak_dengan_sebabnya(klien, lingkungan):
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    r = klien.get("/ekspor/asli")
    assert r.status_code == 400
    assert "belum ada dataset" in r.text
    assert klien.get("/api/ekspor/asli").json()["ok"] is False


def test_dataset_yang_seluruhnya_turunan_ditolak_bukan_zip_kosong(klien,
                                                                  lingkungan):
    """ZIP kosong baru ketahuan salah sesudah diunduh dan dibuka."""
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    ruang = Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = ruang / "semua-turunan"
    for nama in ("a1_aug1.jpg", "a1_bal3_12.jpg"):
        _gambar(d, nama)
    klien.post(f"/setsrc?path={d}")
    r = klien.get("/ekspor/asli")
    assert r.status_code == 409
    assert "hasil augmentasi atau penyeimbangan" in r.text


def test_tombolnya_ada_di_samping_pindai_ulang(klien, lingkungan):
    _projek_beraugbal(klien)
    h = klien.get("/").text
    assert 'id="unduh-asli"' in h
    # Berdampingan, bukan satu baris lagi di dalam menu Ekspor: menu itu
    # menjawab "dalam format apa dataset ini mau dilatih", tombol ini menjawab
    # "kembalikan dataset saya seperti sebelum semuanya".
    assert h.index("rescan()") < h.index('id="unduh-asli"')
    assert "menu-ekspor" not in h.split('id="unduh-asli"')[0][-400:]


def test_hasilnya_bisa_dipindai_ulang_sebagai_dataset(klien, lingkungan,
                                                      tmp_path):
    """Syarat yang membuat tombol ini ada sama sekali.

    ZIP yang cantik tetapi tidak bisa diunggah balik tidak menjawab apa pun:
    scanner mengenali sebuah folder sebagai dataset YOLO hanya kalau ia punya
    images/ dan labels/ bersebelahan.
    """
    from app.services import scanner

    _projek_beraugbal(klien)
    z = zipfile.ZipFile(io.BytesIO(klien.get("/ekspor/asli").content))
    keluar = tmp_path / "diunggah-lagi"
    z.extractall(keluar)
    items, names = scanner.scan(keluar)
    assert len(items) == 2, [i["img"].name for i in items]
    assert sorted(i["img"].name for i in items) == ["a1.jpg", "a2.jpg"]
    # Kelasnya terbaca sebagai NAMA, bukan angka 0/1/2 — itu gunanya data.yaml
    # ikut ke dalam ZIP.
    assert "botol" in set(names.values()), names
    assert all(s["label"] == "botol" for i in items for s in i["shapes"]), items
