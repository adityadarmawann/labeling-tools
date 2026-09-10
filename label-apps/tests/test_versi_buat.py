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
    # 19 = 16 transform Fase 1 v14, ditambah tiga saklar yang JUGA terdaftar di
    # panel v14 tetapi di sana cuma varian Fase 5: vignette, turun-naik
    # resolusi, dan artefak JPEG. Ketiganya tidak pernah menghasilkan satu
    # berkas pun di v14 (PHASE5_VARIANTS_PER_RESULT=1 membuat jumlah varian
    # tambahan nol), jadi di sini mereka jadi transform yang berdiri sendiri.
    # Tetap kelompok "default" karena asalnya memang saklar v14, bukan tambahan
    # gaya Roboflow.
    assert len(aug_default) == 19, sorted(aug_default)
    for i in ("vignette", "downscale", "artefak_jpeg"):
        assert i in aug_default, i
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


# ------------------------------------------- setiap pilihan benar-benar jalan
@pytest.mark.parametrize("oid", list(__import__(
    "app.services.olah", fromlist=["x"]).KATALOG_PRA))
def test_setiap_operasi_preprocessing_jalan_sendiri_sendiri(oid):
    """
    Dinyalakan satu per satu pada gambar sungguhan.

    Popup menawarkan delapan operasi; kalau salah satunya melempar, yang
    menyalakannya baru tahu sesudah pembuatan versi berjalan setengah jalan dan
    gagal. Di sini ia gagal dalam sepersekian detik.
    """
    from app.services import olah
    rng = np.random.default_rng(11)
    img = (rng.random((240, 320, 3)) * 255).astype(np.uint8)
    # Koordinat YOLO: datar dan ternormalisasi, bukan pasangan (x, y).
    label = [(0, [0.13, 0.13, 0.44, 0.13, 0.44, 0.54, 0.13, 0.54])]
    par = {"aktif": True}
    for k, s in olah.KATALOG_PRA[oid]["param"].items():
        if s["jenis"] in ("int", "float"):
            par[k] = s["bawaan"]
    if oid == "potong_tetap":                 # bawaannya kotak penuh: no-op
        par.update({"x1": 5, "y1": 5, "x2": 95, "y2": 95})
    hasil = olah.terapkan_pra(img, label, {"pra": {oid: par}}, n_kelas=2)
    assert hasil is not None, f"{oid} membuang gambar yang berisi objek"
    keluar, _ = hasil
    assert keluar.ndim == 3 and keluar.shape[2] == 3 and keluar.size > 0


@pytest.mark.parametrize("oid", list(__import__(
    "app.services.olah", fromlist=["x"]).KATALOG_AUG))
def test_setiap_operasi_augmentasi_jalan_sendiri_sendiri(oid):
    """Sama, untuk 21 augmentasi: dipaksa kena (p=1) supaya benar-benar jalan."""
    from app.services import olah
    rng = np.random.default_rng(12)
    img = (rng.random((240, 320, 3)) * 255).astype(np.uint8)
    # Koordinat YOLO: datar dan ternormalisasi, bukan pasangan (x, y).
    label = [(0, [0.13, 0.13, 0.44, 0.13, 0.44, 0.54, 0.13, 0.54])]
    pipa = olah.bangun_pipeline({"aug": {i: {"aktif": i == oid, "p": 1.0}
                                         for i in olah.KATALOG_AUG}})
    assert pipa is not None
    hasil = olah.augmentasi_sekali(img, label, pipa, rng)
    assert hasil is not None, f"{oid} tidak menghasilkan apa pun"
    keluar, _ = hasil
    assert keluar.ndim == 3 and keluar.size > 0


def test_semua_pilihan_menyala_sekaligus_lewat_kelima_langkah(klien, lingkungan):
    """
    Kelima langkah wizard dijalankan sekaligus: seluruh preprocessing menyala,
    seluruh 21 augmentasi menyala, tiap angka digeser jauh dari bawaannya, dan
    keempat fase lanjutan hidup.

    Ini yang tidak bisa dibuktikan uji satuan: gabungan. Grayscale sesudah
    CLAHE, crop sesudah resize, lalu 21 transform acak di atasnya, lalu empat
    fase yang masing-masing menambah berkas ke folder yang sama.

    Filter Null sengaja DIMATIKAN, satu-satunya yang dimatikan: ia membuang
    sampel negatif, dan Fase 7 justru bertugas memulihkan porsinya. Menyalakan
    keduanya berarti meminta dua hal yang berlawanan sekaligus.
    """
    from app.services import olah
    masuk(klien, "paul", PW_PAUL)
    d = _ds(klien, n_botol=12, n_kaleng=5, n_negatif=6)
    klien.post("/setsrc", params={"path": str(d)})

    def geser(spec):
        """Jauhkan tiap angka dari bawaannya, tetapi tetap di dalam rentang."""
        par = {}
        for k, s in spec.items():
            if s["jenis"] not in ("int", "float", "peluang"):
                continue
            tengah = (s["min"] + s["maks"]) / 2
            par[k] = int(round(tengah)) if s["jenis"] == "int" else round(tengah, 3)
        return par

    resep = {"pra": {}, "aug": {}, "volume": {"per_gambar": 1},
             "fase": {"crop_zoom": {"aktif": True},
                      "balans_skala": {"aktif": True},
                      "balans_kelas": {"aktif": True},
                      "porsi_negatif": {"aktif": True}}}
    for oid, meta in olah.KATALOG_PRA.items():
        if oid == "buang_kosong":
            resep["pra"][oid] = {"aktif": False}
            continue
        resep["pra"][oid] = {"aktif": True, **geser(meta["param"])}
    resep["pra"]["resize"].update({"mode": "fit", "lebar": 320, "tinggi": 320})
    for oid, meta in olah.KATALOG_AUG.items():
        resep["aug"][oid] = {"aktif": True, **geser(meta["param"])}

    r = _mulai(klien, resep)
    assert r.get("ok"), r
    k = _tunggu(klien, batas=300)
    assert k.get("selesai") and not k.get("galat"), k

    isi = klien.get("/api/versi/isi", params={"nomor": 1}).json()
    assert isi["ok"], isi
    n = isi["versi"]["jumlah"]
    assert n["train"] > 0 and n["valid"] > 0 and n["test"] > 0, n

    # Angka yang digeser benar-benar ikut tersimpan bersama versinya: tanpa ini
    # versi lama tidak bisa diulang, dan itu SATU-SATUNYA gunanya versi.
    simpan = isi["versi"]["resep"]
    assert simpan["aug"]["rotasi"]["derajat"] == 90
    assert simpan["pra"]["resize"]["lebar"] == 320

    # Ukurannya benar-benar 320 dan gambarnya benar-benar ada di disk.
    from app.services import buatversi
    items = buatversi.items_hasil(Path(isi["versi"]["dir"])) \
        if isi["versi"].get("dir") else None
    z = klien.get("/ekspor", params={"nomor": 1, "format": "yolo"})
    assert z.status_code == 200
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        gambar = [x for x in zf.namelist() if x.endswith((".jpg", ".png"))]
        assert gambar, zf.namelist()[:20]
        with zf.open(gambar[0]) as fh:
            im = cv2.imdecode(np.frombuffer(fh.read(), np.uint8), cv2.IMREAD_COLOR)
    assert im.shape[:2] == (320, 320), im.shape


# ------------------------------------------------------------- Modify Classes
def test_daftar_kelas_ikut_di_perkiraan(klien, lingkungan):
    """Popup butuh nama DAN indeksnya; tanpa keduanya ia cuma bisa menampilkan
    angka telanjang."""
    masuk(klien, "paul", PW_PAUL)
    d = _ds(klien, n_botol=6, n_kaleng=4, n_negatif=2)
    klien.post("/setsrc", params={"path": str(d)})
    r = klien.post("/api/versi/estimasi?split=80,10,10", json={"resep": {}}).json()
    assert r["ok"], r
    nama = {k["nama"]: k for k in r["daftar_kelas"]}
    assert set(nama) == {"botol", "kaleng"}, r["daftar_kelas"]
    assert nama["botol"]["objek"] == 6 and nama["kaleng"]["objek"] == 4
    assert {k["i"] for k in r["daftar_kelas"]} == {0, 1}


def test_gabung_buang_dan_ganti_nama_kelas_benar_benar_terjadi(klien, lingkungan):
    """
    Ketiganya sekaligus pada dataset tiga kelas: satu digabung ke lain, satu
    dibuang, satu diganti namanya.

    Yang diperiksa bukan resepnya tersimpan, melainkan BERKASNYA: data.yaml
    memakai nama baru, dan tidak ada satu pun label yang masih menyebut kelas
    yang dibuang.
    """
    masuk(klien, "paul", PW_PAUL)
    d = _ruang(klien) / "vkelas"
    d.mkdir(parents=True, exist_ok=True)
    (d / "classes.txt").write_text("botol\nkaleng\ngelas\n")
    rng = np.random.default_rng(5)
    for i, kelas in enumerate(["botol"] * 8 + ["kaleng"] * 6 + ["gelas"] * 6):
        im = (rng.random((240, 320, 3)) * 90 + 40).astype(np.uint8)
        cv2.imwrite(str(d / f"k{i:02d}.jpg"), im)
        (d / f"k{i:02d}.json").write_text(json.dumps({
            "version": "0.4.36", "flags": {}, "imagePath": f"k{i:02d}.jpg",
            "imageData": None, "imageHeight": 240, "imageWidth": 320,
            "shapes": [{"label": kelas, "shape_type": "polygon",
                        "points": [[60, 40], [150, 40], [150, 130], [60, 130]]}]}))
    klien.post("/setsrc", params={"path": str(d)})

    kel = {k["nama"]: k["i"] for k in klien.post(
        "/api/versi/estimasi?split=80,10,10", json={"resep": {}}).json()["daftar_kelas"]}
    resep = {"pra": {"ubah_kelas": {
        "aktif": True,
        # kaleng digabung ke botol, gelas dibuang, botol diganti namanya
        "peta": {str(kel["kaleng"]): kel["botol"], str(kel["gelas"]): None},
        "nama": {str(kel["botol"]): "wadah"},
    }}, "aug": False, "volume": {"per_gambar": 0},
        "fase": {"crop_zoom": {"aktif": False}, "balans_skala": {"aktif": False},
                 "balans_kelas": {"aktif": False}, "porsi_negatif": {"aktif": False}}}
    assert _mulai(klien, resep).get("ok")
    k = _tunggu(klien, batas=180)
    assert k.get("selesai") and not k.get("galat"), k

    z = klien.get("/ekspor", params={"nomor": 1, "format": "yolo"})
    assert z.status_code == 200
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        yml = next(n for n in zf.namelist() if n.endswith("data.yaml"))
        teks = zf.read(yml).decode()
        indeks = set()
        for n in zf.namelist():
            if n.endswith(".txt") and "/labels/" in n:
                for baris in zf.read(n).decode().split("\n"):
                    if baris.strip():
                        indeks.add(int(float(baris.split()[0])))
    assert "'wadah'" in teks, teks
    assert "'botol'" not in teks, teks
    # gelas dibuang: indeksnya tidak boleh muncul di satu label pun.
    assert kel["gelas"] not in indeks, (indeks, kel)
    # kaleng digabung ke botol: indeks kaleng juga lenyap.
    assert kel["kaleng"] not in indeks, (indeks, kel)
    assert indeks == {kel["botol"]}, (indeks, kel)


def test_ganti_nama_diabaikan_kalau_operasinya_mati(klien, lingkungan):
    """Saklar mati berarti mati. Nama baru yang tetap terpakai membuat saklarnya
    berbohong."""
    masuk(klien, "paul", PW_PAUL)
    d = _ds(klien, n_botol=6, n_kaleng=4, n_negatif=2)
    klien.post("/setsrc", params={"path": str(d)})
    resep = {"pra": {"ubah_kelas": {"aktif": False, "nama": {"0": "wadah"}}},
             "aug": False, "volume": {"per_gambar": 0},
             "fase": {"crop_zoom": {"aktif": False}, "balans_skala": {"aktif": False},
                      "balans_kelas": {"aktif": False}, "porsi_negatif": {"aktif": False}}}
    assert _mulai(klien, resep).get("ok")
    assert _tunggu(klien, batas=180).get("selesai")
    z = klien.get("/ekspor", params={"nomor": 1, "format": "yolo"})
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        teks = zf.read(next(n for n in zf.namelist()
                            if n.endswith("data.yaml"))).decode()
    assert "'wadah'" not in teks and "'botol'" in teks, teks


def test_dataset_tanpa_daftar_kelas_tidak_kehilangan_objeknya(klien, lingkungan):
    """
    Projek labelme yang tidak punya data.yaml maupun classes.txt.

    Dulu `names` kosong membuat kelas_idx() menjawab None untuk setiap bentuk,
    seluruh objek terbuang, dan versinya jadi dataset tanpa satu pun anotasi —
    tanpa galat, tanpa peringatan, dengan jumlah gambar yang terlihat benar.
    Indeksnya sekarang lewat export.peta_kelas, jalur yang sama dengan ekspor
    biasa, jadi keduanya tidak bisa lagi berbeda.
    """
    masuk(klien, "paul", PW_PAUL)
    d = _ds(klien, n_botol=8, n_kaleng=5, n_negatif=3)
    (d / "classes.txt").unlink()        # inilah bedanya
    klien.post("/setsrc", params={"path": str(d)})

    r = klien.post("/api/versi/estimasi?split=80,10,10", json={"resep": {}}).json()
    assert [k["nama"] for k in r["daftar_kelas"]] == ["botol", "kaleng"], r["daftar_kelas"]

    resep = {"aug": False, "volume": {"per_gambar": 0},
             "fase": {"crop_zoom": {"aktif": False}, "balans_skala": {"aktif": False},
                      "balans_kelas": {"aktif": False}, "porsi_negatif": {"aktif": False}}}
    assert _mulai(klien, resep).get("ok")
    assert _tunggu(klien, batas=180).get("selesai")

    z = klien.get("/ekspor", params={"nomor": 1, "format": "yolo"})
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        teks = zf.read(next(n for n in zf.namelist()
                            if n.endswith("data.yaml"))).decode()
        objek = sum(
            len([b for b in zf.read(n).decode().split("\n") if b.strip()])
            for n in zf.namelist() if n.endswith(".txt") and "/labels/" in n)
    assert "'botol'" in teks and "'kaleng'" in teks, teks
    assert "nc: 2" in teks, teks
    assert objek == 13, f"13 objek dilabeli, {objek} sampai ke versi"


# ------------------------------------ jenis anotasi: kotak vs poligon
def test_satu_poligon_nyasar_tidak_lagi_mengubah_semua_kotak():
    """
    Regresi atas kekeliruan yang paling mahal di alur ini.

    Format keluaran dulu ditentukan `any(bentuk bukan rectangle)` atas SELURUH
    projek. Satu poligon nyasar di antara sepuluh ribu kotak membuat kesepuluh
    ribu kotak itu ditulis sebagai poligon empat titik: mask persegi yang tidak
    pernah digambar siapa pun, dan model belajar bahwa objeknya memang
    berbentuk kotak. Lima bentuk `point`, yang bahkan tidak punya luas, sudah
    cukup memicunya.
    """
    from app.services import tugas

    kotak = [{"shapes": [{"type": "rectangle"}]}] * 10_000
    poligon = [{"shapes": [{"type": "polygon"}]}] * 10_000
    belum = {"jenis_anotasi": ""}

    assert tugas.jenis_berlaku(belum, kotak) == "kotak"
    assert tugas.jenis_berlaku(belum, kotak + poligon[:1]) == "kotak", \
        "satu poligon nyasar TIDAK boleh membalik sepuluh ribu kotak"
    assert tugas.jenis_berlaku(belum, poligon + kotak[:1]) == "poligon"
    assert tugas.jenis_berlaku(belum, [{"shapes": [{"type": "point"}]}] * 5) \
        == "poligon"
    # Setelan projek selalu menang atas tebakan.
    assert tugas.jenis_berlaku({"jenis_anotasi": "kotak"}, poligon) == "kotak"
    assert tugas.jenis_berlaku({"jenis_anotasi": "poligon"}, kotak) == "poligon"


def test_ketidaksesuaian_bentuk_bersifat_asimetris():
    """
    Poligon -> kotak itu penurunan yang wajar; kotak -> poligon mengarang mask.

    Karena itu hanya arah kedua yang dianggap tidak sesuai. Menyamakan keduanya
    akan menandai seluruh dataset deteksi yang sehat sebagai bermasalah.
    """
    from app.services.scanner import bentuk_tak_sesuai

    bentuk = [{"type": "polygon"}, {"type": "rectangle"}, {"type": "circle"},
              {"type": "point"}, {"type": "line"}, {"type": "linestrip"}]
    assert bentuk_tak_sesuai(bentuk, "kotak") == []
    tak = {s["type"] for s in bentuk_tak_sesuai(bentuk, "poligon")}
    assert tak == {"rectangle", "point", "line", "linestrip"}, tak
    assert bentuk_tak_sesuai(bentuk, "") == [], "tanpa jenis: perilaku lama"


def test_bentuk_tak_sesuai_muncul_sebagai_temuan_di_grid(tmp_path):
    """Pelabel harus melihat alasannya, bukan menemukan gambarnya lenyap."""
    import cv2
    import numpy as np

    from app.services import scanner

    d = tmp_path / "campur"
    d.mkdir()
    (d / "classes.txt").write_text("botol\n")
    for i in range(6):
        cv2.imwrite(str(d / f"g{i}.jpg"), np.zeros((100, 100, 3), np.uint8))
        sh = ([{"label": "botol", "shape_type": "polygon",
                "points": [[10, 10], [80, 10], [80, 80], [40, 90], [20, 70],
                           [12, 40]]}] if i < 5 else
              [{"label": "botol", "shape_type": "rectangle",
                "points": [[10, 10], [80, 80]]}])
        (d / f"g{i}.json").write_text(json.dumps({
            "version": "0.4.36", "flags": {}, "imagePath": f"g{i}.jpg",
            "imageData": None, "imageHeight": 100, "imageWidth": 100,
            "shapes": sh}))
    items, _ = scanner.scan(d)
    nakal = [it for it in items if it["img"].name == "g5.jpg"][0]
    assert any("dataset poligon" in x for x in nakal["issues"]), nakal["issues"]
    lain = [it for it in items if it["img"].name == "g0.jpg"][0]
    assert not any("dataset poligon" in x for x in lain["issues"])
