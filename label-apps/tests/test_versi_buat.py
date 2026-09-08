"""
Alur "Buat versi": resep preprocessing + augmentasi, pekerjaan latar, hasil
yang tersimpan, dan ekspornya.

Yang dijaga di sini bukan angka persisnya — augmentasi acak — melainkan
sifat-sifat yang kalau hilang membuat versinya salah tanpa kelihatan salah:
preprocessing harus deterministik, sampel negatif harus selamat, valid/test
tidak boleh pernah diaugmentasi, dan berkas hasil versi tidak boleh terbaca
sebagai gambar dataset.
"""
from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from tests.test_data import masuk, PW_PAUL


def _ruang(klien) -> Path:
    return Path(klien.get("/api/projek/daftar").json()["ruang"])


def _ds(klien, n_botol=20, n_kaleng=10, n_negatif=10) -> Path:
    """
    Dataset labelme kecil yang timpang dan punya sampel negatif, DI RUANG KERJA
    akun ini.

    Bukan di folder dataset bersama: folder bersama tidak punya pemilik, dan
    alur versi memang sengaja menolaknya (test_akses.py:457). Membuatnya di
    sana berarti menguji penolakan itu, bukan alur versinya.
    """
    d = _ruang(klien) / "vbuat"
    d.mkdir(parents=True, exist_ok=True)
    (d / "classes.txt").write_text("botol\nkaleng\n")
    rng = np.random.default_rng(3)
    rencana = ["botol"] * n_botol + ["kaleng"] * n_kaleng + [None] * n_negatif
    for i, kelas in enumerate(rencana):
        im = (rng.random((240, 320, 3)) * 90 + 40).astype(np.uint8)
        shapes = []
        if kelas:
            x, y, w, h = 60, 40, 90, 90
            im[y:y + h, x:x + w] = (rng.random((h, w, 3)) * 255).astype(np.uint8)
            shapes = [{"label": kelas, "shape_type": "polygon",
                       "points": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]}]
        cv2.imwrite(str(d / f"g{i:02d}.jpg"), im)
        (d / f"g{i:02d}.json").write_text(json.dumps({
            "version": "0.4.36", "flags": {}, "shapes": shapes,
            "imagePath": f"g{i:02d}.jpg", "imageData": None,
            "imageHeight": 240, "imageWidth": 320}))
    return d


def _tunggu(klien, batas=120):
    """Tunggu pekerjaan latar selesai; kembalikan kemajuan terakhir."""
    for _ in range(batas * 10):
        k = klien.get("/api/versi/kemajuan").json()
        if k.get("selesai") or k.get("batal") or k.get("galat"):
            return k
        time.sleep(0.1)
    pytest.fail(f"pembuatan versi tidak selesai dalam {batas} detik: {k}")


def _mulai(klien, resep=None, split="80,10,10", catatan=""):
    return klien.post(f"/api/versi/mulai?split={split}&catatan={catatan}",
                      json={"resep": resep or {}}).json()


# ------------------------------------------------------------------ katalog
def test_katalog_memisahkan_bawaan_v14_dari_tambahan(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    k = klien.get("/api/versi/katalog").json()
    assert k["ok"]
    # Kedua tahap harus punya dua kelompok, dan kelompok default harus benar-
    # benar berasal dari v14 — bukan sekadar label.
    pra_default = [i for i, v in k["pra"].items() if v["kelompok"] == "default"]
    aug_default = [i for i, v in k["aug"].items() if v["kelompok"] == "default"]
    assert "resize" in pra_default and "periksa_label" in pra_default
    assert len(aug_default) == 16, "Fase 1 v14 punya 16 transform"
    assert all(v.get("saklar_v14") for i, v in k["aug"].items()
               if v["kelompok"] == "default")
    # Tambahan bawaannya MATI: menyalakannya keputusan orang, bukan bawaan.
    assert all(not v["bawaan_aktif"] for v in k["aug"].values()
               if v["kelompok"] == "tambahan")
    # Filter Null mati karena sampel negatif di projek ini disengaja.
    assert k["pra"]["buang_kosong"]["bawaan_aktif"] is False


# ---------------------------------------------------------------- perkiraan
def test_perkiraan_dihitung_sebelum_apa_pun_ditulis(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    d = _ds(klien)
    klien.post(f"/setsrc?path={d}")
    e = klien.post("/api/versi/estimasi?split=80,10,10",
                   json={"resep": {"volume": {"per_gambar": 2}}}).json()
    assert e["ok"] and e["n_sumber"] == 40
    assert e["n"] > e["n_sumber"], "augmentasi menambah gambar"
    assert e["negatif_sumber"] == 10 and e["negatif_train"] > 0
    assert e["byte"] > 0 and "cukup" in e
    assert not (d / ".versi").exists(), "perkiraan tidak boleh menulis apa pun"


# ------------------------------------------------------------------- alur
def test_versi_menghasilkan_berkas_dan_bisa_diunduh(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    d = _ds(klien)
    klien.post(f"/setsrc?path={d}")
    r = _mulai(klien, {"volume": {"per_gambar": 1},
                       "fase": {"balans_skala": {"aktif": False}}})
    assert r["ok"] and r["nomor"] == 1
    k = _tunggu(klien)
    assert k.get("selesai"), k

    v = d / ".versi" / "v1"
    assert (v / "data.yaml").exists() and (v / "MANIFES.json").exists()
    n_train = len(list((v / "train" / "images").glob("*.jpg")))
    n_valid = len(list((v / "valid" / "images").glob("*.jpg")))
    assert n_train > 0 and n_valid > 0

    # tiap gambar punya labelnya, termasuk yang kosong
    for split in ("train", "valid", "test"):
        gdir, ldir = v / split / "images", v / split / "labels"
        if not gdir.exists():
            continue
        for ip in gdir.glob("*.jpg"):
            assert (ldir / f"{ip.stem}.txt").exists(), f"{ip.name} tanpa label"

    # kartu versi menyebut hasilnya
    daftar = klien.get(f"/versi?ds=").text  # noqa: F841
    from app.services import versi as svc
    kartu = svc.daftar(d)[0]
    assert kartu["hasil"]["n"] == n_train + n_valid + \
        len(list((v / "test" / "images").glob("*.jpg")))
    assert kartu["resep"]["volume"]["per_gambar"] == 1

    # unduhan versi berisi gambar HASIL, bukan gambar sumber
    z = zipfile.ZipFile(io.BytesIO(klien.get("/ekspor?nomor=1&format=yolo-seg").content))
    nama = [n for n in z.namelist() if n.endswith(".jpg")]
    assert len(nama) == kartu["hasil"]["n"]
    assert any("_aug" in n for n in nama), "hasil augmentasi harus ikut terunduh"


def test_valid_dan_test_tidak_pernah_diaugmentasi(klien, lingkungan):
    """Alat ukur yang ikut diaugmentasi mengukur dirinya sendiri."""
    masuk(klien, "paul", PW_PAUL)
    d = _ds(klien)
    klien.post(f"/setsrc?path={d}")
    _mulai(klien, {"volume": {"per_gambar": 2}})
    _tunggu(klien)
    v = d / ".versi" / "v1"
    for split in ("valid", "test"):
        gdir = v / split / "images"
        if not gdir.exists():
            continue
        for ip in gdir.glob("*.jpg"):
            assert "_aug" not in ip.name and "_bal" not in ip.name, ip.name


def test_preprocessing_deterministik(klien, lingkungan):
    """
    Dua versi dengan resep preprocessing yang sama harus menghasilkan gambar
    valid/test yang identik byte demi byte.

    v14 memakai pelat latar ACAK untuk letterbox (f:1111), jadi di sana sifat
    ini tidak berlaku. Di sini ia syarat: valid dan test adalah alat ukur.
    """
    masuk(klien, "paul", PW_PAUL)
    d = _ds(klien)
    klien.post(f"/setsrc?path={d}")
    resep = {"volume": {"per_gambar": 0},
             "fase": {"crop_zoom": {"aktif": False}, "balans_skala": {"aktif": False},
                      "balans_kelas": {"aktif": False}, "porsi_negatif": {"aktif": False}}}
    _mulai(klien, resep); _tunggu(klien)
    _mulai(klien, resep); _tunggu(klien)
    a = d / ".versi" / "v1" / "valid" / "images"
    b = d / ".versi" / "v2" / "valid" / "images"
    berkas = sorted(p.name for p in a.glob("*.jpg"))
    assert berkas, "tidak ada gambar valid untuk dibandingkan"
    for n in berkas:
        assert (a / n).read_bytes() == (b / n).read_bytes(), n


def test_sampel_negatif_selamat_dan_porsinya_dipulihkan(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    d = _ds(klien, n_botol=20, n_kaleng=10, n_negatif=10)
    klien.post(f"/setsrc?path={d}")
    _mulai(klien, {"volume": {"per_gambar": 2}})
    _tunggu(klien)
    ldir = d / ".versi" / "v1" / "train" / "labels"
    kosong = [p for p in ldir.glob("*.txt") if p.stat().st_size == 0]
    assert kosong, "seluruh sampel negatif hilang"
    porsi = len(kosong) / len(list(ldir.glob("*.txt")))
    # Porsi asal 10/40 = 25%. Fase 7 mengembalikannya; toleransi lebar karena
    # augmentasi acak, yang dijaga adalah ia TIDAK menyusut jadi sekadar sisa.
    assert 0.12 < porsi < 0.45, f"porsi negatif {porsi:.0%}"


def test_filter_null_membuang_sampel_negatif_kalau_diminta(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    d = _ds(klien)
    klien.post(f"/setsrc?path={d}")
    _mulai(klien, {"volume": {"per_gambar": 0},
                   "pra": {"buang_kosong": {"aktif": True}},
                   "fase": {"crop_zoom": {"aktif": False},
                            "balans_skala": {"aktif": False},
                            "balans_kelas": {"aktif": False},
                            "porsi_negatif": {"aktif": False}}})
    _tunggu(klien)
    ldir = d / ".versi" / "v1"
    kosong = [p for p in ldir.rglob("labels/*.txt") if p.stat().st_size == 0]
    assert not kosong


def test_berkas_hasil_versi_tidak_terbaca_sebagai_gambar_dataset(klien, lingkungan):
    """
    Regresi. `.versi/vN/` berisi puluhan ribu .jpg dan .txt. Sebelum
    diperbaiki, scanner menelusurinya: satu versi yang dibuat membuat projek
    tampak bertambah puluhan ribu gambar, projek labelme salah dikenali
    sebagai dataset YOLO, dan versi berikutnya dibuat dari hasil versi
    sebelumnya.
    """
    from app.services import scanner

    masuk(klien, "paul", PW_PAUL)
    d = _ds(klien)
    klien.post(f"/setsrc?path={d}")
    sebelum, _ = scanner.scan(d)
    _mulai(klien, {"volume": {"per_gambar": 1}})
    _tunggu(klien)
    sesudah, _ = scanner.scan(d)
    assert len(sesudah) == len(sebelum) == 40
    assert not any(".versi" in str(it["img"]) for it in sesudah)
    assert "YOLO" not in " ".join(scanner.periksa_kelengkapan(d))


def test_hapus_versi_ikut_membuang_berkas_hasilnya(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    d = _ds(klien)
    klien.post(f"/setsrc?path={d}")
    _mulai(klien, {"volume": {"per_gambar": 0}})
    _tunggu(klien)
    assert (d / ".versi" / "v1").is_dir()
    assert klien.post("/api/versi/hapus?nomor=1").json()["ok"]
    assert not (d / ".versi" / "v1").exists(), "berkas hasil tertinggal"
