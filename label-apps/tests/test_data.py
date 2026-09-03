"""Unggahan, tandai latar, dan saringan grid."""
from __future__ import annotations

import json
import os
import re

import pytest

from conftest import PW_ANGGI, PW_PAUL, masuk


# ---------------------------------------------------------------- unggahan

def test_unggah_berkas_normal(klien, lingkungan):
    masuk(klien, "anggi", PW_ANGGI)
    r = klien.put("/upload?ds=batch-1&name=foto.png", content=b"x" * 500)
    assert r.json() == {"ok": True, "name": "foto.png", "bytes": 500,
                        "arsip": False}
    assert (lingkungan["roots"] / "_unggahan" / "anggi" / "batch-1" / "foto.png").exists()


def test_nama_berkas_disterilkan_dan_tetap_di_folder_akun(klien, lingkungan):
    masuk(klien, "anggi", PW_ANGGI)
    unggahan = lingkungan["roots"] / "_unggahan"

    # Nama bermuatan `..` DITOLAK, bukan ditafsirkan jadi path lain. Unggahan
    # folder yang sah tidak pernah memuatnya, jadi menolak lebih jelas.
    r = klien.put("/upload?ds=batch-1&name=../../../etc/jahat.png", content=b"x" * 10)
    assert r.json()["ok"] is False
    assert not (unggahan.parent / "etc").exists()
    assert not list(unggahan.rglob("jahat.png"))

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
    assert not list((lingkungan["roots"] / "_unggahan").rglob("*.part"))
    assert not list((lingkungan["roots"] / "_unggahan").rglob("besar.png"))


def test_hasil_unggahan_bisa_dibuka_sebagai_dataset(klien, lingkungan):
    masuk(klien, "anggi", PW_ANGGI)
    # gambar sungguhan supaya bisa dipindai OpenCV
    asli = (lingkungan["roots"] / "ds-beta" / "ds-beta-00.jpg").read_bytes()
    klien.put("/upload?ds=batch-2&name=satu.jpg", content=asli)
    klien.put("/upload?ds=batch-2&name=dua.jpg", content=asli)

    r = klien.post("/useupload?ds=batch-2")
    assert r.json()["ok"] is True
    assert r.json()["n"] == 2

    # Terbaca dua, tetapi halaman Dataset belum menampilkannya: gambar yang
    # baru diunggah belum dilabeli dan belum dimasukkan siapa pun. Halamannya
    # mengatakan itu, bukan diam lalu tampak kosong tanpa sebab.
    h = klien.get("/").text
    assert h.count('class="card"') == 0
    assert "Belum ada gambar yang masuk dataset" in h

    # Dan setelah dimasukkan, keduanya muncul.
    import pathlib
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    klien.post("/api/tugas/dataset", json={
        "gambar": [str(ruang / "batch-2" / n) for n in ("satu.jpg", "dua.jpg")]})
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
    """Halaman pilih menampilkan apa yang ADA; mengisi projek ada di /unggah.

    Kotak seret dan kotak path dulu ikut di halaman ini, di dalam dialog
    "Projek baru". Keduanya pindah ke halaman Unggah data karena di sanalah
    ada ruang untuk memperlihatkan apa yang akan terkirim sebelum terkirim.
    """
    masuk(klien, "paul", PW_PAUL)
    html = klien.get("/pilih").text
    assert "ds-alpha" in html and "ds-beta" in html
    assert 'id="dsname"' in html        # dialog projek baru: namanya saja
    assert 'id="drop"' not in html

    klien.post("/api/projek/baru?nama=uji-unggah")
    ug = klien.get("/unggah?ds=uji-unggah").text
    assert 'id="ug-drop"' in ug         # kotak seret
    assert 'id="ug-server-path"' in ug  # kotak path folder server


# ---------------------------------------------------------------- unggah folder

def test_safe_relpath_menjaga_subfolder_dan_membuang_yang_berbahaya():
    from app.security import safe_relpath
    assert safe_relpath("sirsak/images/a.jpg") == "sirsak/images/a.jpg"
    assert safe_relpath("sirsak/labels/a.txt") == "sirsak/labels/a.txt"
    assert safe_relpath("sirsak\\images\\w.jpg") == "sirsak/images/w.jpg"
    # `..` ditolak seluruhnya, tidak ditafsirkan
    assert safe_relpath("../../etc/x.png") == ""
    assert safe_relpath("a/./b/../c.jpg") == ""
    # path absolut kehilangan awalannya, sisanya tetap relatif
    assert safe_relpath("/abs/path/b.jpg") == "abs/path/b.jpg"
    assert safe_relpath("a/./b/c.jpg") == "a/b/c.jpg"
    # ekstensi tetap disaring
    assert safe_relpath("folder/x.sh") == ""
    assert safe_relpath("") == ""
    # kedalaman dibatasi
    from app.security import MAKS_DALAM
    dalam = safe_relpath("/".join(f"d{i}" for i in range(12)) + "/z.jpg")
    assert dalam.count("/") <= MAKS_DALAM


def test_nama_panjang_ala_roboflow_tidak_hilang():
    """
    Regresi. Dulu setiap komponen path dipotong ke 80 karakter lebih dulu,
    termasuk nama berkasnya. Nama ekspor Roboflow berbentuk
    `<asli>.rf.<32 digit hash>.jpg` dan mudah lewat dari 80, jadi potongannya
    ikut memakan `.jpg`; berkasnya lalu tertolak karena ekstensinya tidak
    dikenal, dan gambar hilang tanpa pesan apa pun. Satu dataset sirsak
    kehilangan 440 berkas persis karena ini.
    """
    from app.security import MAKS_NAMA, safe_filename, safe_relpath
    nama = "WhatsApp-Image-2024-05-22-at-12-17-46-1-_jpeg.rf." + "e" * 32 + ".jpg"
    assert len(nama) > 80
    assert safe_relpath(f"train/images/{nama}") == f"train/images/{nama}"

    # Yang benar-benar kepanjangan tetap dipotong, tetapi ekstensinya bertahan
    # sehingga berkasnya masih dikenali sebagai gambar.
    panjang = "a" * 400 + ".jpg"
    hasil = safe_filename(panjang)
    assert hasil.endswith(".jpg") and len(hasil) <= MAKS_NAMA
    assert safe_relpath("train/images/" + panjang).endswith(".jpg")

    # Nama subfolder tetap dibatasi — yang dilonggarkan hanya nama berkas.
    from app.security import MAKS_KOMPONEN
    p = safe_relpath("b" * 300 + "/c.jpg")
    assert len(p.split("/")[0]) == MAKS_KOMPONEN


def test_unggah_folder_mempertahankan_struktur_yolo(klien, lingkungan):
    """
    Struktur images/ + labels/ harus utuh, karena pemindai mengenali dataset
    YOLO justru dari kedua folder itu. Kalau diratakan, dataset yang diunggah
    tidak akan terbaca sebagai YOLO.
    """
    from conftest import PW_ANGGI, masuk
    from app.services import scanner

    masuk(klien, "anggi", PW_ANGGI)
    asli = (lingkungan["roots"] / "ds-beta" / "ds-beta-00.jpg").read_bytes()

    for nama in ("ds/images/a.jpg", "ds/images/b.jpg"):
        r = klien.put(f"/upload?ds=folder-uji&name={nama}", content=asli)
        assert r.json()["ok"] is True, nama
    for nama in ("ds/labels/a.txt", "ds/labels/b.txt"):
        r = klien.put(f"/upload?ds=folder-uji&name={nama}", content=b"0 0.5 0.5 0.2 0.2\n")
        assert r.json()["ok"] is True, nama

    d = lingkungan["roots"] / "_unggahan" / "anggi" / "folder-uji" / "ds"
    assert (d / "images" / "a.jpg").exists()
    assert (d / "labels" / "a.txt").exists()
    # dan pemindai mengenalinya sebagai dataset YOLO
    items, _ = scanner.scan(d)
    assert len(items) == 2


# ---------------------------------------------------------------- daftar kelas resmi

def test_kelas_resmi_dibedakan_dari_kelas_yang_kebetulan_dipakai(klien, lingkungan):
    """
    Kanvas perlu tahu kelas mana yang DIDEKLARASIKAN dataset, bukan sekadar
    yang sudah dipakai. Tanpa pembedaan itu, "Botol" hasil salah ketik langsung
    tampak sah — begitu satu objek terlanjur diberi nama itu, dia jadi "kelas
    yang sudah dipakai" dan tidak ada lagi yang bisa membedakannya dari kelas
    sungguhan.
    """
    d = lingkungan["roots"] / "ds-resmi"
    _buat_yolo(d, kelas="botol\nkaleng\nplastic-cup\n")
    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={d}")

    html = klien.get("/label").text
    m = re.search(r'id="data-awal"[^>]*>(.*?)</script>', html, re.S)
    data = json.loads(m.group(1))
    # Hanya "botol" yang benar-benar dipakai di anotasi...
    assert data["kelas"] == ["botol"]
    # ...tetapi ketiganya dideklarasikan dataset.
    assert data["kelas_resmi"] == ["botol", "kaleng", "plastic-cup"]


def test_dataset_labelme_dengan_classes_txt_juga_punya_kelas_resmi(lingkungan):
    """Bukan cuma YOLO: folder labelme yang menyertakan classes.txt ikut terbaca."""
    from conftest import buat_dataset
    from app.services import scanner

    d = lingkungan["roots"] / "labelme-resmi"
    buat_dataset(d, 2, 2)
    (d / "classes.txt").write_text("botol\nkaleng\ntetra\n")
    _, names = scanner.scan(d)
    assert [names[i] for i in sorted(names)] == ["botol", "kaleng", "tetra"]


def test_dataset_tanpa_daftar_kelas_memberi_kelas_resmi_kosong(lingkungan):
    """Tanpa daftar resmi, penjaga salah ketik memang tidak berlaku."""
    from conftest import buat_dataset
    from app.services import scanner

    d = lingkungan["roots"] / "labelme-polos"
    buat_dataset(d, 2, 2)
    _, names = scanner.scan(d)
    assert names == {}


# ---------------------------------------------------------------- unggah .yaml & .zip

def test_data_yaml_diterima_unggahan(klien, lingkungan):
    """
    Regresi. Dulu `.yaml` tidak ada di daftar ekstensi, sehingga data.yaml
    ekspor Roboflow ditolak diam-diam saat unggah folder — dan seluruh dataset
    lalu tampil dengan kelas "0", "1", "2" karena nama kelasnya memang hanya
    ada di berkas itu.
    """
    masuk(klien, "anggi", PW_ANGGI)
    for nama in ("data.yaml", "sub/data.yml", "dataset.yaml"):
        r = klien.put(f"/upload?ds=rf&name={nama}", content=b"names: ['a']\n")
        assert r.json()["ok"] is True, (nama, r.json())
    d = lingkungan["roots"] / "_unggahan" / "anggi" / "rf"
    assert (d / "data.yaml").exists() and (d / "sub" / "data.yml").exists()


def test_ekstensi_berbahaya_tetap_ditolak_setelah_pelonggaran(klien):
    """Melonggarkan .yaml/.zip tidak boleh ikut membuka yang lain."""
    masuk(klien, "anggi", PW_ANGGI)
    for nama in ("a.sh", "b.py", "c.exe", "d.yaml.sh", "e.so", "f.zip.py"):
        r = klien.put(f"/upload?ds=x&name={nama}", content=b"x" * 10)
        assert r.json()["ok"] is False, nama


def _zip_bytes(entri):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for nama, isi in entri:
            z.writestr(nama, isi)
    return buf.getvalue()


def test_unggah_zip_lalu_dibongkar_di_server(klien, lingkungan):
    import cv2
    import numpy as np

    masuk(klien, "anggi", PW_ANGGI)
    img = cv2.imencode(".jpg", np.full((60, 80, 3), 90, np.uint8))[1].tobytes()
    blob = _zip_bytes([
        ("data.yaml", b"nc: 2\nnames: ['botol','kaleng']\n"),
        ("train/images/a.jpg", img),
        ("train/labels/a.txt", b"0 0.5 0.5 0.4 0.4\n"),
        ("valid/images/b.jpg", img),
        ("valid/labels/b.txt", b"1 0.4 0.4 0.2 0.2\n"),
    ])
    assert klien.put("/upload?ds=rfzip&name=export.zip", content=blob).json()["ok"] is True
    r = klien.post("/unzip?ds=rfzip&name=export.zip").json()
    assert r["ok"] is True, r
    assert r["n"] == 5, r

    d = lingkungan["roots"] / "_unggahan" / "anggi" / "rfzip"
    assert (d / "data.yaml").exists()
    assert (d / "train" / "images" / "a.jpg").exists()
    assert not (d / "export.zip").exists(), "arsip seharusnya dibuang setelah dibongkar"

    # dan datasetnya terbaca dengan NAMA kelas, bukan angka
    r = klien.post("/useupload?ds=rfzip").json()
    assert r["ok"] is True and r["n"] == 2, r
    assert r["peringatan"] == [], r["peringatan"]
    from app.services import scanner
    items, _ = scanner.scan(d)
    assert sorted({s["label"] for i in items for s in i["shapes"]}) == ["botol", "kaleng"]


def test_zip_slip_ditolak(klien, lingkungan):
    """Entri yang mencoba menulis di luar folder unggahan harus dilewati."""
    masuk(klien, "anggi", PW_ANGGI)
    import cv2
    import numpy as np
    img = cv2.imencode(".jpg", np.full((60, 80, 3), 90, np.uint8))[1].tobytes()
    blob = _zip_bytes([
        ("../../../jahat.jpg", img),
        ("/etc/jahat2.jpg", img),
        ("aman.jpg", img),
    ])
    klien.put("/upload?ds=slip&name=x.zip", content=blob)
    r = klien.post("/unzip?ds=slip&name=x.zip").json()
    assert r["ok"] is True, r

    unggahan = lingkungan["roots"] / "_unggahan"
    milik = unggahan / "anggi"
    for p in unggahan.rglob("*"):
        if p.is_file():
            assert milik in p.parents, f"bocor keluar: {p}"
    assert not list(unggahan.parent.glob("jahat*.jpg"))
    assert (milik / "slip" / "aman.jpg").exists()


def test_isi_zip_yang_tidak_didukung_dilewati_bukan_menggagalkan(klien, lingkungan):
    import cv2
    import numpy as np

    masuk(klien, "anggi", PW_ANGGI)
    img = cv2.imencode(".jpg", np.full((60, 80, 3), 90, np.uint8))[1].tobytes()
    blob = _zip_bytes([
        ("a.jpg", img),
        ("jahat.sh", b"rm -rf /"),
        ("dalam.zip", b"PK\x03\x04bukan-zip-sungguhan"),
    ])
    klien.put("/upload?ds=campur&name=c.zip", content=blob)
    r = klien.post("/unzip?ds=campur&name=c.zip").json()
    assert r["ok"] is True and r["n"] == 1, r
    assert r["dilewati"] == 2, r
    d = lingkungan["roots"] / "_unggahan" / "anggi" / "campur"
    assert (d / "a.jpg").exists()
    assert not (d / "jahat.sh").exists()
    # zip di dalam zip tidak ikut ditulis, jadi pembongkaran tidak pernah berlapis
    assert not (d / "dalam.zip").exists()


def test_zip_bomb_ditolak(tmp_path):
    """
    5 MB nol memampat jadi beberapa kilobyte. Tanpa pagar ukuran, arsip kecil
    bisa memenuhi disk server. Diuji langsung di lapisan layanan karena batas
    sungguhannya (puluhan GB) tidak masuk akal dijalankan lewat HTTP.
    """
    from app.services import arsip

    zp = tmp_path / "b.zip"
    zp.write_bytes(_zip_bytes([("besar.txt", b"0" * (5 * 1024 * 1024))]))
    with pytest.raises(arsip.ArsipTolak) as e:
        arsip.bongkar(zp, tmp_path / "keluar", maks_byte=1024)
    assert "melebihi batas" in str(e.value)
    # tidak meninggalkan berkas setengah jadi
    assert not list((tmp_path / "keluar").rglob("*.part"))


def test_entri_zip_terlalu_banyak_ditolak(tmp_path):
    from app.services import arsip

    zp = tmp_path / "banyak.zip"
    zp.write_bytes(_zip_bytes([(f"f{i}.txt", b"x") for i in range(40)]))
    with pytest.raises(arsip.ArsipTolak) as e:
        arsip.bongkar(zp, tmp_path / "keluar", maks_byte=10**9, maks_entri=10)
    assert "terlalu banyak" in str(e.value)


def test_arsip_rusak_memberi_pesan_jelas(klien):
    masuk(klien, "anggi", PW_ANGGI)
    klien.put("/upload?ds=rusak&name=r.zip", content=b"ini bukan zip sama sekali")
    r = klien.post("/unzip?ds=rusak&name=r.zip").json()
    assert r["ok"] is False and "tidak terbaca" in r["error"], r


def test_peringatan_muncul_kalau_yolo_tanpa_nama_kelas(klien, lingkungan):
    import cv2
    import numpy as np

    masuk(klien, "anggi", PW_ANGGI)
    img = cv2.imencode(".jpg", np.full((60, 80, 3), 90, np.uint8))[1].tobytes()
    klien.put("/upload?ds=tanpa-yaml&name=train/images/a.jpg", content=img)
    klien.put("/upload?ds=tanpa-yaml&name=train/labels/a.txt",
              content=b"0 0.5 0.5 0.4 0.4\n")
    r = klien.post("/useupload?ds=tanpa-yaml").json()
    assert r["ok"] is True
    assert any("data.yaml" in p for p in r["peringatan"]), r["peringatan"]


# ---------------------------------------------------------------- ekspor bersplit

def _buat_ekspor_roboflow(root, splits=("train", "valid", "test"), n=2,
                          data_yaml=True):
    """Tiruan struktur ekspor Roboflow: train/valid/test, masing-masing YOLO."""
    import cv2
    import numpy as np

    root.mkdir(parents=True, exist_ok=True)
    if data_yaml:
        (root / "data.yaml").write_text(
            "train: ../train/images\nval: ../valid/images\ntest: ../test/images\n"
            "\nnc: 2\nnames: ['botol', 'kaleng']\n")
    for s in splits:
        (root / s / "images").mkdir(parents=True, exist_ok=True)
        (root / s / "labels").mkdir(parents=True, exist_ok=True)
        for i in range(n):
            ip = root / s / "images" / f"{s}-{i}.jpg"
            cv2.imwrite(str(ip), np.full((60, 80, 3), 90, np.uint8))
            (root / s / "labels" / f"{s}-{i}.txt").write_text(
                f"{i % 2} 0.5 0.5 0.4 0.4\n")
    return root


def test_ekspor_roboflow_dipindai_dari_akarnya(lingkungan):
    """
    Regresi. Dulu menunjuk akar ekspor Roboflow membuat SELURUH gambar tampak
    "belum dilabeli" — bukan karena anotasinya hilang, tapi karena `labels/`
    ada satu tingkat lebih dalam dan tidak pernah dicari. Pada dataset 55 ribu
    gambar, itu terbaca seolah seluruh pekerjaan pelabelan lenyap.
    """
    from app.services import scanner

    root = _buat_ekspor_roboflow(lingkungan["roots"] / "rf-export")
    items, names = scanner.scan(root)

    assert len(items) == 6, [i["img"].name for i in items]
    assert all(i["shapes"] for i in items), "ada gambar yang tidak terbaca anotasinya"
    assert sorted({i["split"] for i in items}) == ["test", "train", "valid"]
    # Nama kelas diambil dari data.yaml di akar, bukan angka indeks.
    assert sorted({s["label"] for i in items for s in i["shapes"]}) == ["botol", "kaleng"]


def test_nama_kelas_ditemukan_dari_data_yaml_induk(lingkungan):
    """Membuka SATU split pun harus tetap memberi nama kelas yang benar."""
    from app.services import scanner

    root = _buat_ekspor_roboflow(lingkungan["roots"] / "rf-split")
    items, names = scanner.scan(root / "train")
    assert len(items) == 2
    assert sorted({s["label"] for i in items for s in i["shapes"]}) == ["botol", "kaleng"]


def test_classes_txt_masih_dipakai_kalau_tidak_ada_data_yaml(lingkungan):
    from app.services import scanner

    root = _buat_ekspor_roboflow(lingkungan["roots"] / "rf-txt", data_yaml=False)
    (root / "classes.txt").write_text("botol\nkaleng\n")
    items, _ = scanner.scan(root)
    assert sorted({s["label"] for i in items for s in i["shapes"]}) == ["botol", "kaleng"]


def test_folder_biasa_bersubfolder_test_tidak_dianggap_ekspor_bersplit(lingkungan):
    """
    Penjagaan arah sebaliknya: dataset labelme biasa yang kebetulan punya
    subfolder bernama `test` tidak boleh tiba-tiba dibaca sebagai ekspor
    bersplit — kalau itu terjadi, gambar di akarnya hilang dari pandangan.
    """
    from conftest import buat_dataset
    from app.services import scanner

    d = lingkungan["roots"] / "biasa"
    buat_dataset(d, 3, 2)                       # 3 gambar di akar
    buat_dataset(d / "test", 2, 1)              # plus subfolder bernama test

    assert scanner.split_bersarang(d) == []
    items, _ = scanner.scan(d)
    assert len(items) == 5                      # akar DAN subfolder ikut terbaca


def test_split_bersarang_menuntut_semua_split_berbentuk_yolo(lingkungan):
    from app.services import scanner

    root = lingkungan["roots"] / "setengah"
    _buat_ekspor_roboflow(root, splits=("train",))
    (root / "test").mkdir(parents=True)         # ada, tapi bukan YOLO
    assert scanner.split_bersarang(root) == []


def test_dimensi_dibaca_tanpa_mendekode_seluruh_gambar(lingkungan):
    from app.services import scanner

    ip = lingkungan["roots"] / "ds-alpha" / "ds-alpha-00.jpg"
    assert scanner.dimensi(ip) == (60, 80)
    # Berkas rusak dilaporkan None, bukan melempar galat.
    rusak = lingkungan["tmp"] / "rusak.jpg"
    rusak.write_bytes(b"bukan gambar")
    assert scanner.dimensi(rusak) is None


# ---------------------------------------------------------------- simpan YOLO

def _buat_yolo(d, baris="0 0.5 0.5 0.4 0.4\n", kelas="botol\nkaleng\n"):
    import cv2
    import numpy as np

    (d / "images").mkdir(parents=True, exist_ok=True)
    (d / "labels").mkdir(parents=True, exist_ok=True)
    ip = d / "images" / "a.jpg"
    cv2.imwrite(str(ip), np.full((60, 80, 3), 60, np.uint8))
    (d / "labels" / "a.txt").write_text(baris)
    (d / "classes.txt").write_text(kelas)
    return ip


def _bentuk_di_kanvas(klien, ip):
    html = klien.get(f"/label?path={ip}").text
    m = re.search(r'id="data-awal"[^>]*>(.*?)</script>', html, re.S)
    return json.loads(m.group(1))["shapes"]


def test_suntingan_yolo_tidak_hilang_setelah_pindai_ulang(klien, lingkungan):
    """
    Regresi kehilangan data. Dulu menyimpan dataset YOLO hanya menulis .json di
    sebelah gambar, sementara pemindai membaca labels/*.txt — sehingga aplikasi
    melaporkan "Tersimpan" lalu pekerjaannya lenyap begitu dataset dipindai
    ulang, tanpa pesan apa pun.
    """
    d = lingkungan["roots"] / "yolo-suntingan"
    ip = _buat_yolo(d)
    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={d}")

    b = _bentuk_di_kanvas(klien, ip)
    assert b[0]["label"] == "botol"
    b[0]["label"] = "kaleng"
    b[0]["points"] = [[10.0, 10.0], [70.0, 50.0]]
    r = klien.post("/api/simpan", json={"path": str(ip), "shapes": b, "flags": {}})
    assert r.json()["ok"] is True

    # Berkas YOLO-nya sendiri ikut berubah, bukan cuma .json di sebelahnya.
    isi = (d / "labels" / "a.txt").read_text().strip()
    assert isi.startswith("1 "), isi           # kelas jadi "kaleng" (indeks 1)
    assert (d / "images" / "a.json").exists()  # cadangan tetap ditulis

    klien.post("/rescan")
    b2 = _bentuk_di_kanvas(klien, ip)
    assert b2[0]["label"] == "kaleng"
    assert b2[0]["points"][0][0] == pytest.approx(10.0, abs=0.01)


def test_bbox_tetap_bbox_dan_poligon_tetap_poligon(klien, lingkungan):
    """
    Menyimpan tidak boleh mengubah JENIS berkas label. Dataset bbox yang
    tiba-tiba tertulis dalam format segmentasi akan menggagalkan pipeline
    latihan yang mengharapkan 5 kolom.
    """
    masuk(klien, "paul", PW_PAUL)

    # Poligon sumber ditulis dengan cincin tertutup, sama seperti ekspor
    # Roboflow — jadi jumlah kolomnya tetap setelah bulat-balik.
    for nama, baris, kolom in (
            ("yolo-bbox", "0 0.5 0.5 0.4 0.4\n", 5),
            ("yolo-seg", "0 0.1 0.1 0.9 0.1 0.9 0.9 0.1 0.9 0.1 0.1\n", 11)):
        d = lingkungan["roots"] / nama
        ip = _buat_yolo(d, baris)
        klien.post(f"/setsrc?path={d}")
        b = _bentuk_di_kanvas(klien, ip)
        klien.post("/api/simpan", json={"path": str(ip), "shapes": b, "flags": {}})
        hasil = (d / "labels" / "a.txt").read_text().strip().split("\n")[0]
        assert len(hasil.split()) == kolom, (nama, hasil)


def test_group_id_dan_teks_bertahan_lewat_cadangan_json(klien, lingkungan):
    """Format YOLO tidak punya tempat untuk keduanya; cadangan .json yang menyimpannya."""
    d = lingkungan["roots"] / "yolo-catatan"
    ip = _buat_yolo(d)
    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={d}")

    b = _bentuk_di_kanvas(klien, ip)
    b[0]["group_id"] = 7
    b[0]["text"] = "perlu dicek lagi"
    klien.post("/api/simpan", json={"path": str(ip), "shapes": b, "flags": {}})
    klien.post("/rescan")

    b2 = _bentuk_di_kanvas(klien, ip)
    assert b2[0]["group_id"] == 7
    assert b2[0]["text"] == "perlu dicek lagi"


def test_cadangan_json_diabaikan_kalau_jumlah_bentuk_tidak_cocok(lingkungan):
    """
    Kalau .txt disunting dari luar sehingga jumlah bentuknya berbeda dari
    cadangan, catatan lama TIDAK boleh dipasangkan asal-asalan ke bentuk yang
    sebenarnya bukan pasangannya.
    """
    from app.services import scanner

    d = lingkungan["roots"] / "yolo-tidak-cocok"
    ip = _buat_yolo(d)
    (d / "images" / "a.json").write_text(json.dumps({
        "version": "0.4.36", "flags": {},
        "shapes": [{"label": "botol", "shape_type": "rectangle",
                    "points": [[1, 1], [2, 2]], "group_id": 9, "flags": {}},
                   {"label": "botol", "shape_type": "rectangle",
                    "points": [[3, 3], [4, 4]], "group_id": 9, "flags": {}}],
        "imagePath": "a.jpg", "imageData": None,
        "imageHeight": 60, "imageWidth": 80}))
    items, _ = scanner.scan(d)                 # .txt hanya berisi 1 bentuk
    assert len(items[0]["shapes"]) == 1
    assert items[0]["shapes"][0].get("group_id") is None


def test_bentuk_yang_tidak_muat_di_yolo_diberi_peringatan(klien, lingkungan):
    d = lingkungan["roots"] / "yolo-titik"
    ip = _buat_yolo(d)
    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={d}")

    b = _bentuk_di_kanvas(klien, ip)
    b.append({"label": "botol", "shape_type": "point", "points": [[30.0, 30.0]],
              "text": "", "group_id": None, "flags": {}, "titipan": {}})
    r = klien.post("/api/simpan", json={"path": str(ip), "shapes": b, "flags": {}})
    j = r.json()
    assert j["ok"] is True
    assert any("tidak muat" in p for p in j["peringatan"]), j["peringatan"]
    # Titiknya tetap ada di cadangan, hanya tidak ikut ke .txt.
    assert len((d / "labels" / "a.txt").read_text().strip().splitlines()) == 1
    cad = json.loads((d / "images" / "a.json").read_text())["shapes"]
    assert [s["shape_type"] for s in cad] == ["rectangle", "point"]


def test_baris_yang_tidak_disunting_ditulis_persis_seperti_aslinya(klien, lingkungan):
    """
    Menyimpan satu objek tidak boleh menyentuh objek lain di berkas yang sama.

    Berkas YOLO di lapangan sering punya lebih dari 6 desimal (0.144853125).
    Kalau setiap penyimpanan menulis ulang seluruh berkas dengan 6 desimal,
    ketelitian objek yang tidak disunting siapa pun ikut terpangkas diam-diam.
    Bedanya memang di bawah seperseratus piksel, tetapi berkas orang berubah
    tanpa ada yang memintanya — dan itu terlihat sebagai baris berubah di git.
    """
    d = lingkungan["roots"] / "yolo-presisi"
    ip = _buat_yolo(d, baris="0 0.144853125 0.3447296875 0.2 0.3\n"
                             "1 0.5 0.5 0.4 0.4\n")
    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={d}")
    sebelum = (d / "labels" / "a.txt").read_text()

    b = _bentuk_di_kanvas(klien, ip)
    assert len(b) == 2
    # Sunting HANYA objek kedua.
    b[1]["points"] = [[10.0, 10.0], [70.0, 50.0]]
    klien.post("/api/simpan", json={"path": str(ip), "shapes": b, "flags": {}})

    baris = (d / "labels" / "a.txt").read_text().splitlines()
    assert baris[0] == sebelum.splitlines()[0], "baris yang tidak disunting ikut berubah"
    assert baris[1] != sebelum.splitlines()[1], "baris yang disunting seharusnya berubah"


def test_buka_lalu_simpan_tanpa_perubahan_tidak_mengubah_berkas(klien, lingkungan):
    d = lingkungan["roots"] / "yolo-utuh"
    asli = "0 0.144853125 0.3447296875 0.2 0.3\n1 0.29840686274509803 0.5 0.4 0.4\n"
    ip = _buat_yolo(d, baris=asli)
    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={d}")

    b = _bentuk_di_kanvas(klien, ip)
    klien.post("/api/simpan", json={"path": str(ip), "shapes": b, "flags": {}})
    assert (d / "labels" / "a.txt").read_text() == asli


def test_kelas_di_luar_daftar_diberi_peringatan(klien, lingkungan):
    d = lingkungan["roots"] / "yolo-kelas-baru"
    ip = _buat_yolo(d)
    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={d}")

    b = _bentuk_di_kanvas(klien, ip)
    b[0]["label"] = "kelas-yang-belum-ada"
    j = klien.post("/api/simpan",
                   json={"path": str(ip), "shapes": b, "flags": {}}).json()
    assert any("belum ada di daftar kelas" in p for p in j["peringatan"]), j
    # Barisnya tidak ditulis sembarangan dengan indeks tebakan.
    assert (d / "labels" / "a.txt").read_text().strip() == ""


# ---------------------------------------------------------------- tipe bentuk

def _tulis_enam_bentuk(lingkungan):
    """Satu gambar berisi kelima tipe bentuk labelme yang punya titik tetap."""
    import cv2
    import numpy as np

    d = lingkungan["roots"] / "ds-bentuk"
    d.mkdir(parents=True, exist_ok=True)
    ip = d / "b-00.jpg"
    cv2.imwrite(str(ip), np.full((60, 80, 3), 60, np.uint8))
    ip.with_suffix(".json").write_text(json.dumps({
        "version": "0.4.36", "flags": {},
        "shapes": [
            {"label": "a", "shape_type": "rectangle", "points": [[10, 10], [50, 40]],
             "group_id": None, "flags": {}},
            {"label": "b", "shape_type": "point", "points": [[30, 30]],
             "group_id": None, "flags": {}},
            {"label": "c", "shape_type": "line", "points": [[5, 5], [70, 55]],
             "group_id": None, "flags": {}},
            {"label": "d", "shape_type": "circle", "points": [[40, 30], [55, 30]],
             "group_id": None, "flags": {}},
            {"label": "e", "shape_type": "linestrip",
             "points": [[5, 50], [20, 20], [60, 50]], "group_id": None, "flags": {}},
            {"label": "f", "shape_type": "polygon",
             "points": [[20, 15], [60, 15], [60, 45]], "group_id": None, "flags": {}},
        ],
        "imagePath": ip.name, "imageData": None,
        "imageHeight": 60, "imageWidth": 80,
    }))
    return d, ip


def test_enam_tipe_bentuk_dibaca_pemindai(lingkungan):
    from app.services import scanner

    d, ip = _tulis_enam_bentuk(lingkungan)
    sh, W, H = scanner.read_json(ip.with_suffix(".json"))
    assert [s["type"] for s in sh] == [
        "rectangle", "point", "line", "circle", "linestrip", "polygon"]
    # `pts` siap gambar: rectangle jadi 4 sudut, circle jadi poligon.
    assert len(sh[0]["pts"]) == 4
    assert len(sh[3]["pts"]) == scanner.SISI_LINGKARAN
    # `pts_asli` mempertahankan titik seperti di berkas.
    assert sh[0]["pts_asli"] == [[10.0, 10.0], [50.0, 40.0]]
    assert sh[3]["pts_asli"] == [[40.0, 30.0], [55.0, 30.0]]


def test_rectangle_tetap_dua_titik_setelah_disimpan_ulang(klien, lingkungan):
    """
    Regresi kerusakan data. Dulu `read_json` memekarkan rectangle 2 titik jadi
    4, kanvas menerima yang sudah dimekarkan, lalu menyimpannya kembali apa
    adanya — sehingga rectangle di berkas jadi 4 titik. Berkas seperti itu
    TIDAK BISA dibuka lagi di AnyLabeling desktop: shape.py:160 di sana
    menuntut rectangle punya tepat 1 atau 2 titik.
    """
    d, ip = _tulis_enam_bentuk(lingkungan)
    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={d}")

    html = klien.get(f"/label?path={ip}").text
    m = re.search(r'id="data-awal"[^>]*>(.*?)</script>', html, re.S)
    bentuk = json.loads(m.group(1))["shapes"]

    # Yang dikirim ke kanvas sudah memakai titik asli, bukan yang dimekarkan.
    dikirim = {b["shape_type"]: len(b["points"]) for b in bentuk}
    assert dikirim == {"rectangle": 2, "point": 1, "line": 2, "circle": 2,
                       "linestrip": 3, "polygon": 3}, dikirim

    # Dan bulat-balik lewat penyimpanan tidak mengubah satu titik pun.
    r = klien.post("/api/simpan",
                   json={"path": str(ip), "shapes": bentuk, "flags": {}})
    assert r.json()["ok"] is True, r.json()
    sesudah = json.loads(ip.with_suffix(".json").read_text())["shapes"]
    assert [s["shape_type"] for s in sesudah] == [
        "rectangle", "point", "line", "circle", "linestrip", "polygon"]
    assert sesudah[0]["points"] == [[10.0, 10.0], [50.0, 40.0]]
    assert sesudah[3]["points"] == [[40.0, 30.0], [55.0, 30.0]]


def test_rectangle_dimekarkan_dari_kanvas_dikembalikan_jadi_dua_titik(klien, lingkungan):
    """Kanvas boleh keliru mengirim 4 titik; berkas tetap harus 2."""
    d, ip = _tulis_enam_bentuk(lingkungan)
    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={d}")

    r = klien.post("/api/simpan", json={"path": str(ip), "flags": {}, "shapes": [
        {"label": "a", "shape_type": "rectangle",
         "points": [[10, 10], [50, 10], [50, 40], [10, 40]],
         "text": "", "group_id": None, "flags": {}, "titipan": {}}]})
    assert r.json()["ok"] is True
    s = json.loads(ip.with_suffix(".json").read_text())["shapes"][0]
    assert s["points"] == [[10.0, 10.0], [50.0, 40.0]]


def test_bentuk_tanpa_luas_tidak_dinilai_sebagai_mask_kecil(lingkungan):
    from app.services import scanner

    d, ip = _tulis_enam_bentuk(lingkungan)
    sh, W, H = scanner.read_json(ip.with_suffix(".json"))
    temuan = scanner.inspect(sh, W, H, has_ann=True)
    # `point` luasnya nol; kalau ikut dinilai, tiap titik akan selalu dilaporkan
    # sebagai "mask sangat kecil" dan temuan itu jadi kebisingan belaka.
    assert "mask sangat kecil" not in temuan, temuan


def test_unggah_tidak_bisa_keluar_folder_akun(klien, lingkungan):
    from conftest import PW_ANGGI, masuk
    masuk(klien, "anggi", PW_ANGGI)
    unggahan = lingkungan["roots"] / "_unggahan"

    for jahat in ("../../../etc/lolos.png", "/etc/lolos2.png",
                  "a/../../../../lolos3.png"):
        klien.put(f"/upload?ds=uji&name={jahat}", content=b"x" * 10)

    milik = unggahan / "anggi"
    for p in unggahan.rglob("*"):
        if p.is_file():
            assert milik in p.parents, f"bocor keluar: {p}"
    assert not (unggahan.parent / "etc").exists()


# ---------------------------------------------------- impor dari path di server

def _sidik(d):
    """Nama + ukuran + waktu ubah tiap berkas — untuk membuktikan folder utuh."""
    import hashlib
    h = hashlib.sha256()
    for p in sorted(d.rglob("*")):
        if p.is_file():
            st = p.stat()
            h.update(f"{p.relative_to(d)}|{st.st_size}|{st.st_mtime_ns}".encode())
    return h.hexdigest()


def test_impor_menyalin_dan_tidak_pernah_menyentuh_sumber(klien, lingkungan):
    """
    Janji utama fitur ini, dan satu-satunya alasan ia dipilih ketimbang
    membuka folder di tempat: apa pun yang terjadi sesudahnya — menyunting,
    menandai latar, menambah gambar — folder sumber tetap persis seperti semula.
    """
    import json as _json

    from conftest import PW_PAUL, buat_dataset, masuk
    masuk(klien, "paul", PW_PAUL)

    sumber = buat_dataset(lingkungan["roots"] / "sumber" / "asal-ku", 3, 2)
    (sumber / "data.yaml").write_text("names: [botol, kaleng]\n")
    sebelum = _sidik(sumber)

    r = klien.post(f"/impor?path={sumber}&ds=salinanku").json()
    assert r["ok"] is True, r
    salinan = lingkungan["roots"] / "_unggahan" / "paul" / "salinanku"
    assert r["disalin"] == 6                      # 3 gambar + 2 anotasi + yaml
    assert (salinan / "data.yaml").exists()

    # Sunting salinannya, lalu buktikan sumbernya tetap utuh.
    ip = salinan / "asal-ku-00.jpg"
    sh = _json.loads((salinan / "asal-ku-00.json").read_text())["shapes"]
    sh[0]["label"] = "kaleng"
    assert klien.post("/api/simpan", json={"path": str(ip), "shapes": sh,
                                           "flags": {}}).json()["ok"] is True
    assert _json.loads((salinan / "asal-ku-00.json").read_text(
        ))["shapes"][0]["label"] == "kaleng"
    assert _json.loads((sumber / "asal-ku-00.json").read_text(
        ))["shapes"][0]["label"] == "botol"
    assert _sidik(sumber) == sebelum, "folder sumber berubah — ini tidak boleh"


def test_impor_menolak_tujuan_di_dalam_sumber(lingkungan):
    """Tanpa penjagaan ini, penyalinan memakan hasil salinannya sendiri."""
    from app.services import impor
    from conftest import buat_dataset

    sumber = buat_dataset(lingkungan["roots"] / "s2", 2, 1)
    with pytest.raises(impor.ImporTolak):
        impor.impor_folder(sumber, sumber / "di-dalam")


def test_impor_melewati_berkas_asing_dan_melaporkannya(lingkungan):
    from app.services import impor
    from conftest import buat_dataset

    sumber = buat_dataset(lingkungan["roots"] / "s3", 2, 1)
    (sumber / "catatan.sh").write_text("rm -rf /")
    (sumber / "besar.mp4").write_bytes(b"x" * 100)
    tujuan = lingkungan["tmp"] / "hasil3"

    h = impor.impor_folder(sumber, tujuan)
    assert h["berkas"] == 3 and h["dilewati"] == 2
    assert not (tujuan / "catatan.sh").exists()
    assert sorted(h["contoh_dilewati"]) == ["besar.mp4", "catatan.sh"]
    # Survei harus memakai aturan yang sama, kalau tidak taksirannya meleset.
    assert impor.survei(sumber)["berkas"] == h["berkas"]


def test_impor_tidak_menimpa_saat_nama_bentrok(lingkungan):
    """
    Dua nama berbeda bisa menyatu setelah disterilkan (spasi jadi '-').

    Tidak ada yang ditimpa dan tidak ada yang dibuang: yang kedua masuk dengan
    akhiran. Sebelumnya yang kedua dilewati begitu saja, dan itu berarti satu
    gambar hilang hanya karena namanya mengandung spasi.
    """
    from app.services import impor

    sumber = lingkungan["tmp"] / "s4"
    sumber.mkdir()
    (sumber / "foto a.jpg").write_bytes(b"pertama")
    (sumber / "foto+a.jpg").write_bytes(b"kedua")
    tujuan = lingkungan["tmp"] / "hasil4"

    h = impor.impor_folder(sumber, tujuan)
    assert h["berkas"] == 2 and h["dilewati"] == 0
    assert len(h["bentrok"]) == 1
    assert (tujuan / "foto-a.jpg").read_bytes() == b"pertama"
    assert (tujuan / "foto-a-2.jpg").read_bytes() == b"kedua"


def test_impor_ulang_tidak_menggandakan_berkas_yang_isinya_sama(lingkungan):
    """Menyalin folder yang sama dua kali harus menghasilkan dataset yang sama,
    bukan dataset dengan setiap gambar kembar dua."""
    from app.services import impor
    from conftest import buat_dataset

    sumber = buat_dataset(lingkungan["roots"] / "s4b", 3, 2)
    tujuan = lingkungan["tmp"] / "hasil4b"

    a = impor.impor_folder(sumber, tujuan)
    b = impor.impor_folder(sumber, tujuan)
    assert a["berkas"] == 5 and a["sudah_ada"] == 0
    assert b["berkas"] == 0 and b["sudah_ada"] == 5
    assert len(list(tujuan.glob("*.jpg"))) == 3


def test_impor_menolak_kalau_disk_hampir_penuh(lingkungan, monkeypatch):
    import shutil as _shutil

    from app.services import impor
    from conftest import buat_dataset

    sumber = buat_dataset(lingkungan["roots"] / "s5", 2, 1)
    monkeypatch.setattr(_shutil, "disk_usage",
                        lambda p: _shutil._ntuple_diskusage(100, 100, 1024))
    with pytest.raises(impor.ImporTolak, match="disk"):
        impor.impor_folder(sumber, lingkungan["tmp"] / "hasil5")


def test_riwayat_path_diingat_lintas_sesi(klien, aplikasi, lingkungan):
    """
    Riwayat harus bertahan setelah proses restart — itu seluruh alasannya
    ditulis ke berkas, bukan disimpan di sesi.
    """
    from app.services import riwayat
    from conftest import buat_dataset, klien_baru
    from app.session import store

    masuk(klien, "paul", PW_PAUL)
    src = buat_dataset(lingkungan["roots"] / "riw" / "ds-riwayat", 2, 1)
    assert klien.post(f"/setsrc?path={src}").json()["ok"] is True
    # Riwayat path ikut pindah ke halaman Unggah data bersama kotak pathnya.
    klien.post("/api/projek/baru?nama=uji-riwayat")
    assert str(src.resolve()) in klien.get("/unggah?ds=uji-riwayat").text

    # semua sesi dibuang, seperti restart
    store._data.clear()
    lain = klien_baru(aplikasi, "paul", PW_PAUL)
    lain.post("/api/projek/baru?nama=uji-riwayat2")
    assert str(src.resolve()) in lain.get("/unggah?ds=uji-riwayat2").text

    # milik akun lain tidak ikut terlihat
    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    assert str(src.resolve()) not in anggi.get("/pilih").text

    # melupakan hanya membuang catatannya, foldernya tetap ada
    assert lain.post(f"/lupakan-path?path={src.resolve()}").json()["ok"] is True
    assert str(src.resolve()) not in lain.get("/pilih").text
    assert src.is_dir() and len(list(src.glob("*.jpg"))) == 2


def test_riwayat_menandai_folder_yang_sudah_hilang(lingkungan):
    from app.config import get_settings
    from app.services import riwayat

    s = get_settings()
    riwayat.catat(s, "paul", lingkungan["roots"] / "ds-alpha", "buka")
    riwayat.catat(s, "paul", lingkungan["tmp"] / "sudah-dihapus", "salin")

    d = riwayat.baca(s, "paul")
    assert [r["cara"] for r in d] == ["salin", "buka"]      # terbaru di atas
    assert d[0]["ada"] is False and d[1]["ada"] is True


def test_riwayat_tidak_menumpuk_dan_tidak_ganda(lingkungan):
    from app.config import get_settings
    from app.services import riwayat

    s = get_settings()
    for i in range(riwayat.MAKS + 5):
        riwayat.catat(s, "paul", f"/tmp/ds-{i}", "buka")
    riwayat.catat(s, "paul", "/tmp/ds-3", "salin")          # yang lama dinaikkan

    d = riwayat.baca(s, "paul")
    assert len(d) == riwayat.MAKS
    assert d[0]["path"] == "/tmp/ds-3"
    assert len({r["path"] for r in d}) == len(d)


def test_riwayat_rusak_tidak_menggagalkan_halaman(klien, lingkungan):
    from app.config import get_settings
    from app.services import riwayat

    masuk(klien, "paul", PW_PAUL)
    p = riwayat._berkas(get_settings(), "paul")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ ini bukan json")
    assert klien.get("/pilih").status_code == 200


def test_impor_melaporkan_kemajuannya_selagi_menyalin(klien, lingkungan):
    """
    Tanpa laporan ini penyalinan 22 ribu berkas tampak seperti halaman macet.
    Yang diuji: angkanya benar-benar bergerak selama penyalinan, bukan cuma
    terisi di akhir.
    """
    from app.services import impor
    from conftest import buat_dataset

    sumber = buat_dataset(lingkungan["roots"] / "s6", impor.LAPOR_TIAP * 3, 0)
    jejak = []

    asli = impor.catat_maju

    def rekam(kunci, **n):
        asli(kunci, **n)
        jejak.append(impor.kemajuan(kunci))

    impor.catat_maju = rekam
    try:
        h = impor.impor_folder(sumber, lingkungan["tmp"] / "hasil6", kunci="paul")
    finally:
        impor.catat_maju = asli

    tahap = [j["tahap"] for j in jejak]
    assert tahap[0] == "survei" and tahap[-1] == "pindai"
    salin = [j for j in jejak if j["tahap"] == "salin"]
    assert len(salin) >= 3, tahap                     # benar-benar bertahap
    assert [j["berkas"] for j in salin] == sorted(j["berkas"] for j in salin)
    assert salin[-1]["total"] == h["berkas"]
    assert impor.kemajuan("paul")["berkas"] == h["berkas"]
    assert impor.kemajuan("akun-lain") == {}          # tidak bocor antarakun


def test_rute_kemajuan_bisa_ditanyakan_terpisah(klien, lingkungan):
    from app.services import impor

    masuk(klien, "paul", PW_PAUL)
    impor.catat_maju("paul", tahap="salin", berkas=7, total=10,
                     bytes=99, total_bytes=200)
    r = klien.get("/api/impor/kemajuan").json()
    assert r == {"ok": True, "tahap": "salin", "berkas": 7, "total": 10,
                 "bytes": 99, "total_bytes": 200}


# ------------------------------------------- tambah gambar ke dataset terbuka

def _proyek_bersplit(root, n=(80, 10, 10)):
    """Dataset YOLO bersplit 80:10:10 di ruang kerja akun."""
    import cv2
    import numpy as np
    for s, k in zip(("train", "valid", "test"), n):
        (root / s / "images").mkdir(parents=True, exist_ok=True)
        (root / s / "labels").mkdir(parents=True, exist_ok=True)
        for i in range(k):
            cv2.imwrite(str(root / s / "images" / f"{s}{i}.jpg"),
                        np.full((60, 80, 3), (50 + i) % 250, np.uint8))
            (root / s / "labels" / f"{s}{i}.txt").write_text("0 0.5 0.5 0.2 0.2\n")
    (root / "data.yaml").write_text("names: [botol, kaleng]\n")
    return root


def _gambar_baru(d, n, mulai=0):
    import cv2
    import numpy as np
    d.mkdir(parents=True, exist_ok=True)
    for i in range(mulai, mulai + n):
        cv2.imwrite(str(d / f"n{i}.jpg"), np.full((60, 80, 3), (100 + i) % 250, np.uint8))
        (d / f"n{i}.txt").write_text("1 0.5 0.5 0.3 0.3\n")
    return d


def _isi(root):
    return {s: len(list((root / s / "images").glob("*.jpg")))
            for s in ("train", "valid", "test")}


def test_tambah_menjaga_rasio_split_yang_sudah_ada(klien, lingkungan):
    """
    Rasionya harus utuh sesudah penambahan, bukan cuma mendekati.

    Pernah gagal justru di sini: hitungan per split hanya naik setelah berkasnya
    mendarat, sehingga setiap gambar dinilai seolah ia satu-satunya yang
    ditambahkan dan seluruh batch menumpuk di train — 80:10:10 rusak jadi
    100:10:10.
    """
    masuk(klien, "paul", PW_PAUL)
    proyek = _proyek_bersplit(lingkungan["ruang"] / "proyek")
    assert klien.post("/useupload?ds=proyek").json()["n"] == 100
    baru = _gambar_baru(lingkungan["roots"] / "baru", 20)

    r = klien.post(f"/tambah/impor?path={baru}").json()
    assert r["ok"] is True and r["ditambah"] == 40 and r["n"] == 120
    assert _isi(proyek) == {"train": 96, "valid": 12, "test": 12}


def test_tambah_menaruh_label_di_split_yang_sama_dengan_gambarnya(klien,
                                                                  lingkungan):
    """Label yang terpisah dari gambarnya membuat gambar itu tampak belum
    dilabeli, dan labelnya menjadi yatim di split lain."""
    masuk(klien, "paul", PW_PAUL)
    proyek = _proyek_bersplit(lingkungan["ruang"] / "p2")
    klien.post("/useupload?ds=p2")
    baru = _gambar_baru(lingkungan["roots"] / "baru2", 20)
    klien.post(f"/tambah/impor?path={baru}")

    for s in ("train", "valid", "test"):
        gbr = {p.stem for p in (proyek / s / "images").glob("n*.jpg")}
        lbl = {p.stem for p in (proyek / s / "labels").glob("n*.txt")}
        assert gbr == lbl, f"split {s}: gambar {gbr} vs label {lbl}"


def test_tambah_dua_kali_tidak_menggandakan(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    proyek = _proyek_bersplit(lingkungan["ruang"] / "p3")
    klien.post("/useupload?ds=p3")
    baru = _gambar_baru(lingkungan["roots"] / "baru3", 20)

    klien.post(f"/tambah/impor?path={baru}")
    sesudah = _isi(proyek)
    r = klien.post(f"/tambah/impor?path={baru}").json()
    assert r["ditambah"] == 0 and r["sudah_ada"] == 40
    assert _isi(proyek) == sesudah


def test_tambah_berkas_senama_tapi_beda_isi_tetap_masuk_berpasangan(klien,
                                                                    lingkungan):
    """'Tambah' berarti tidak ada yang diganti. Gambar senama yang isinya beda
    tetap masuk, dan labelnya harus ikut memakai nama pengganti yang sama —
    kalau tidak, gambarnya tampak belum dilabeli."""
    import cv2
    import numpy as np

    masuk(klien, "paul", PW_PAUL)
    proyek = _proyek_bersplit(lingkungan["ruang"] / "p4")
    klien.post("/useupload?ds=p4")
    klien.post(f"/tambah/impor?path={_gambar_baru(lingkungan['roots'] / 'baru4', 5)}")

    lain = lingkungan["roots"] / "lain4"
    lain.mkdir()
    cv2.imwrite(str(lain / "n0.jpg"), np.full((60, 80, 3), 7, np.uint8))
    (lain / "n0.txt").write_text("0 0.1 0.1 0.1 0.1\n")

    r = klien.post(f"/tambah/impor?path={lain}").json()
    assert r["ditambah"] == 2 and r["bentrok"]
    pasang = [(s, (proyek / s / "labels" / "n0-2.txt").exists())
              for s in ("train", "valid", "test")
              if (proyek / s / "images" / "n0-2.jpg").exists()]
    assert pasang and all(ada for _, ada in pasang), pasang
    # yang lama tidak tersentuh
    assert (proyek / pasang[0][0] / "labels" / "n0.txt").read_text() \
        == "1 0.5 0.5 0.3 0.3\n"


def test_tambah_ditolak_kalau_dataset_dibuka_dari_path_server(klien, lingkungan):
    """Menambah ke dataset yang dibuka di tempat berarti menulis ke folder
    sumber milik orang lain — aturan aplikasi ini, folder sumber hanya dibaca."""
    from conftest import buat_dataset

    masuk(klien, "paul", PW_PAUL)
    luar = buat_dataset(lingkungan["roots"] / "luar" / "ds", 3, 1)
    klien.post(f"/setsrc?path={luar}")
    sebelum = sorted(p.name for p in luar.iterdir())

    baru = _gambar_baru(lingkungan["tmp"] / "baru5", 2)
    r = klien.post(f"/tambah/impor?path={baru}").json()
    assert r["ok"] is False and "salin dulu" in r["error"]

    r = klien.put("/tambah?name=x.jpg", content=b"x" * 50).json()
    assert r["ok"] is False
    assert sorted(p.name for p in luar.iterdir()) == sebelum

    # dan halaman grid mengatakan alasannya, bukan menyembunyikan tombolnya
    assert "salin dulu" in klien.get("/").text


def test_tambah_satu_berkas_lewat_unggahan(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    proyek = _proyek_bersplit(lingkungan["ruang"] / "p6")
    klien.post("/useupload?ds=p6")
    gbr = (proyek / "train" / "images" / "train0.jpg").read_bytes()

    r = klien.put("/tambah?name=sub/folder/foto-baru.jpg", content=gbr).json()
    assert r["ok"] is True and r["hasil"] == "baru"
    # struktur folder pengirim dibuang; yang menentukan adalah tata letak tujuan
    assert (proyek / r["split"] / "images" / "foto-baru.jpg").exists()
    assert not (proyek / "sub").exists()

    # berkas yang sama persis, dikirim lagi
    assert klien.put("/tambah?name=foto-baru.jpg",
                     content=gbr).json()["hasil"] == "sudah-ada"


def test_tambah_menolak_jenis_berkas_yang_bukan_gambar_atau_anotasi(klien,
                                                                    lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _proyek_bersplit(lingkungan["ruang"] / "p7")
    klien.post("/useupload?ds=p7")
    for nama in ("catatan.sh", "data.yaml", "arsip.zip"):
        assert klien.put(f"/tambah?name={nama}",
                         content=b"x" * 20).json()["ok"] is False, nama


# ------------------------------------- paritas AnyLabeling: keutuhan per bentuk

def _json_bentuk(ip, shapes, W=120, H=100):
    import json as _json
    ip.with_suffix(".json").write_text(_json.dumps({
        "version": "0.4.36", "flags": {}, "imagePath": ip.name,
        "imageHeight": H, "imageWidth": W, "imageData": None, "shapes": shapes}))


def _dataset_satu(tmp, shapes, W=120, H=100):
    import cv2
    import numpy as np
    d = tmp / "satu"
    d.mkdir(parents=True, exist_ok=True)
    ip = d / "uji.jpg"
    cv2.imwrite(str(ip), np.full((H, W, 3), 60, np.uint8))
    _json_bentuk(ip, shapes, W, H)
    return d, ip


BENTUK_UJI = [
    # Poligon 2 titik: tipenya sah tapi titiknya kurang, jadi pemindai
    # melewatinya — dan dulu itulah yang menggeser semua bentuk sesudahnya.
    {"label": "rusak", "shape_type": "polygon", "points": [[1, 1], [2, 2]],
     "group_id": 7, "flags": {"sulit": True}, "text": "catatan RUSAK"},
    {"label": "botol", "shape_type": "polygon", "points": [[5, 5], [50, 5], [50, 60]],
     "group_id": 3, "flags": {"pecah": True}, "text": "catatan BOTOL",
     "description": "deskripsi BOTOL"},
    {"label": "kaleng", "shape_type": "polygon", "points": [[60, 5], [110, 5], [110, 60]],
     "group_id": 9, "flags": {"penyok": True}, "text": "catatan KALENG",
     "description": "deskripsi KALENG"},
]


def test_field_per_bentuk_tidak_bergeser_saat_ada_bentuk_dilewati(lingkungan):
    """
    Regresi paling mahal di jalur data.

    Bentuk dibaca lewat dua jalur lalu dipasangkan. Dulu pasangannya memakai
    nomor urut HASIL PINDAI, padahal pemindai boleh melewati bentuk yang tidak
    bisa digambar — sehingga satu bentuk terlewat membuat group_id, catatan, dan
    flag seluruh bentuk sesudahnya menempel ke objek yang salah, dan milik
    bentuk terakhir hilang.
    """
    from app.routers import annotate
    from app.services import scanner

    d, ip = _dataset_satu(lingkungan["roots"], BENTUK_UJI)
    items, _ = scanner.scan(d)
    it = items[0]
    assert len(it["shapes"]) == 2, "poligon 2 titik memang harus dilewati"

    kanvas = annotate.bentuk_untuk_kanvas(it, annotate.baca_mentah(ip.with_suffix(".json")))
    oleh = {b["label"]: b for b in kanvas}
    assert oleh["botol"]["group_id"] == 3
    assert oleh["botol"]["text"] == "catatan BOTOL"
    assert oleh["botol"]["flags"] == {"pecah": True}
    assert oleh["kaleng"]["group_id"] == 9
    assert oleh["kaleng"]["text"] == "catatan KALENG"
    assert oleh["kaleng"]["flags"] == {"penyok": True}


def test_description_bertahan_saat_disimpan_ulang_dari_web(klien, lingkungan):
    """`description` milik labelme 5.x / X-AnyLabeling. Kanvas kita tidak
    menyuntingnya, jadi ia harus lewat jalur titipan — dulu ia diklaim sebagai
    milik kita, dikeluarkan dari titipan, lalu tidak pernah ditulis kembali."""
    import json as _json

    from app.routers import annotate
    from app.services import scanner

    masuk(klien, "paul", PW_PAUL)
    d, ip = _dataset_satu(lingkungan["roots"], BENTUK_UJI)
    klien.post(f"/setsrc?path={d}")

    items, _ = scanner.scan(d)
    kanvas = annotate.bentuk_untuk_kanvas(
        items[0], annotate.baca_mentah(ip.with_suffix(".json")))
    r = klien.post("/api/simpan", json={"path": str(ip), "shapes": kanvas,
                                        "flags": {}})
    assert r.json()["ok"] is True

    sesudah = _json.loads(ip.with_suffix(".json").read_text())["shapes"]
    oleh = {s["label"]: s for s in sesudah}
    assert oleh["botol"]["description"] == "deskripsi BOTOL"
    assert oleh["kaleng"]["description"] == "deskripsi KALENG"
    # dan bentuk yang dilewati pemindai tidak ikut terhapus
    assert "rusak" in oleh, [s["label"] for s in sesudah]
    assert oleh["rusak"]["text"] == "catatan RUSAK"


def test_titik_di_luar_gambar_dikurung_saat_menyimpan(klien, lingkungan):
    """`.json` dan `.txt` harus menyimpan bentuk yang sama. Penulisan YOLO
    selalu mengurung, jadi kalau `.json` tidak, satu gambar punya dua bentuk."""
    import json as _json

    masuk(klien, "paul", PW_PAUL)
    d, ip = _dataset_satu(lingkungan["roots"], BENTUK_UJI)
    klien.post(f"/setsrc?path={d}")

    liar = [{"label": "botol", "shape_type": "polygon", "flags": {},
             "points": [[-30, -12], [500, 5], [50, 900]]}]
    assert klien.post("/api/simpan", json={"path": str(ip), "shapes": liar,
                                           "flags": {}}).json()["ok"] is True
    p = _json.loads(ip.with_suffix(".json").read_text())["shapes"][0]["points"]
    assert all(0 <= x <= 120 and 0 <= y <= 100 for x, y in p), p


# ------------------------------------ paritas panel: flag bawaan & catatan gambar

def test_flag_bawaan_dataset_selalu_ditawarkan(klien, lingkungan, monkeypatch):
    """
    label_widget.py:2187-2192 — daftar flag dari setelan SELALU tampil dengan
    nilai false, lalu ditimpa isi berkas. Tanpa daftar tetap, nama flag harus
    diketik ulang persis di tiap gambar dan satu salah ketik diam-diam membuat
    dua flag yang berbeda.
    """
    import json as _json

    from app.config import get_settings

    berkas = lingkungan["tmp"] / "flags.txt"
    berkas.write_text("buram\nterhalang\n")
    monkeypatch.setenv("LABELAPP_FLAGS_FILE", str(berkas))
    get_settings.cache_clear()

    masuk(klien, "paul", PW_PAUL)
    d, ip = _dataset_satu(lingkungan["roots"], BENTUK_UJI)
    klien.post(f"/setsrc?path={d}")
    html = klien.get(f"/label?path={ip}").text
    data = _json.loads(re.search(r'id="data-awal"[^>]*>(.*?)</script>',
                                 html, re.S).group(1))
    assert data["flags_gambar"] == {"buram": False, "terhalang": False}

    # nilai di berkas menang atas bawaan
    _json_bentuk(ip, BENTUK_UJI)
    isi = _json.loads(ip.with_suffix(".json").read_text())
    isi["flags"] = {"buram": True}
    ip.with_suffix(".json").write_text(_json.dumps(isi))
    klien.post("/rescan")
    html = klien.get(f"/label?path={ip}").text
    data = _json.loads(re.search(r'id="data-awal"[^>]*>(.*?)</script>',
                                 html, re.S).group(1))
    assert data["flags_gambar"] == {"buram": True, "terhalang": False}


def test_catatan_tingkat_gambar_bisa_dibaca_dan_ditulis(klien, lingkungan):
    """other_data["image_text"] (label_widget.py:1699) — catatan untuk GAMBAR,
    berbeda dari catatan per objek. Dulu terbawa tetapi tidak bisa dilihat
    maupun diubah dari web."""
    import json as _json

    masuk(klien, "paul", PW_PAUL)
    d, ip = _dataset_satu(lingkungan["roots"], BENTUK_UJI)
    jp = ip.with_suffix(".json")
    isi = _json.loads(jp.read_text())
    isi["image_text"] = "foto dari batch pagi"
    jp.write_text(_json.dumps(isi))
    klien.post(f"/setsrc?path={d}")

    html = klien.get(f"/label?path={ip}").text
    data = _json.loads(re.search(r'id="data-awal"[^>]*>(.*?)</script>',
                                 html, re.S).group(1))
    assert data["teks_gambar"] == "foto dari batch pagi"

    r = klien.post("/api/simpan", json={
        "path": str(ip), "shapes": [], "flags": {},
        "teks_gambar": "diganti dari web"})
    assert r.json()["ok"] is True
    assert _json.loads(jp.read_text())["image_text"] == "diganti dari web"


def test_daftar_berkas_membawa_penanda_split(klien, lingkungan):
    """Pada ekspor Roboflow nama berkas yang sama muncul di train/valid/test;
    tanpa penandanya barisnya tampak kembar dan orang tidak tahu mana yang
    sedang dibuka."""
    import json as _json

    masuk(klien, "paul", PW_PAUL)
    proyek = _proyek_bersplit(lingkungan["ruang"] / "psplit",
                              n=(2, 1, 1))
    klien.post("/useupload?ds=psplit")
    ip = proyek / "train" / "images" / "train0.jpg"
    html = klien.get(f"/label?path={ip}").text
    data = _json.loads(re.search(r'id="data-awal"[^>]*>(.*?)</script>',
                                 html, re.S).group(1))
    split = sorted({b["split"] for b in data["berkas"]})
    assert split == ["test", "train", "valid"], split


# ------------------------------------------------ deteksi lewat prompt teks

def test_deteksi_teks_mengembalikan_banyak_objek(klien, lingkungan, monkeypatch):
    """
    Jalur prompt teks: sebut nama kelasnya, seluruh gambar dipindai sekaligus.

    Modelnya diganti boneka supaya yang diuji adalah plumbingnya — penguraian
    permintaan, batas nilai, dan bentuk balasannya — tanpa menarik unduhan
    ratusan MB ke mesin yang menjalankan tes.
    """
    from app.services import autolabel

    masuk(klien, "paul", PW_PAUL)
    d, ip = _dataset_satu(lingkungan["roots"], BENTUK_UJI)
    klien.post(f"/setsrc?path={d}")

    dipanggil = {}

    def boneka(img, teks, model, ambang, maks, eps):
        dipanggil.update(teks=teks, model=model, ambang=ambang, maks=maks)
        return [
            autolabel.Temuan("botol", [[1, 1], [9, 1], [9, 9]], (1, 1, 9, 9),
                             0.91, "polygon"),
            autolabel.Temuan("kaleng", [[20, 20], [40, 40]], (20, 20, 40, 40),
                             0.77, "rectangle"),
        ]

    monkeypatch.setattr(autolabel, "dari_teks", boneka)
    r = klien.post("/api/deteksi", json={
        "path": str(ip), "teks": "botol, kaleng\ntetra", "model": "yoloworld:latest",
        "ambang": 0.3, "maks": 50}).json()

    assert r["ok"] is True and r["n"] == 2
    assert [b["label"] for b in r["bentuk"]] == ["botol", "kaleng"]
    assert [b["shape_type"] for b in r["bentuk"]] == ["polygon", "rectangle"]
    assert r["bentuk"][0]["skor"] == 0.91
    # teks dipisah dengan koma DAN baris baru
    assert dipanggil["teks"] == ["botol", " kaleng", "tetra"]
    assert dipanggil["ambang"] == 0.3 and dipanggil["maks"] == 50


def test_deteksi_teks_membatasi_nilai_yang_di_luar_akal(klien, lingkungan,
                                                        monkeypatch):
    from app.services import autolabel

    masuk(klien, "paul", PW_PAUL)
    d, ip = _dataset_satu(lingkungan["roots"], BENTUK_UJI)
    klien.post(f"/setsrc?path={d}")

    dilihat = {}

    def boneka(img, teks, model, ambang, maks, eps):
        dilihat.update(ambang=ambang, maks=maks)
        return [autolabel.Temuan("botol", [[1, 1], [9, 9]], (1, 1, 9, 9), 1.0,
                                 "rectangle")]

    monkeypatch.setattr(autolabel, "dari_teks", boneka)
    klien.post("/api/deteksi", json={"path": str(ip), "teks": "botol",
                                     "ambang": 99, "maks": 99999})
    assert dilihat["ambang"] == 0.95 and dilihat["maks"] == 500
    klien.post("/api/deteksi", json={"path": str(ip), "teks": "botol",
                                     "ambang": -5, "maks": 0})
    assert dilihat["ambang"] == 0.01 and dilihat["maks"] == 1


def test_deteksi_teks_menolak_model_yang_bukan_model_teks(lingkungan):
    from app.services import autolabel

    d, ip = _dataset_satu(lingkungan["roots"], BENTUK_UJI)
    with pytest.raises(autolabel.TidakAdaObjek, match="tidak menerima prompt teks"):
        autolabel.dari_teks(ip, ["botol"], "mobilesam")
    with pytest.raises(autolabel.TidakAdaObjek, match="belum ada nama kelas"):
        autolabel.dari_teks(ip, ["  ", ""], "yoloworld:latest")


def test_info_melaporkan_model_teks_beserta_ukuran_unduhannya():
    """Antarmuka harus bisa mengatakan biayanya SEBELUM tombolnya ditekan —
    3,4 GB bukan sesuatu yang boleh dimulai diam-diam."""
    from app.services import autolabel

    teks = {t["model"]: t for t in autolabel.info()["teks"]}
    assert set(teks) == {"yoloworld:latest", "sam3:latest"}
    assert teks["yoloworld:latest"]["unduh_mb"] == 641
    assert teks["sam3:latest"]["unduh_mb"] == 3412
    assert all(isinstance(t["terunduh"], bool) for t in teks.values())


# ------------------------------------------------- urutkan & cari di grid

def _grid_nama(klien, **q):
    """Nama berkas yang tampil di grid, sesuai urutan tampilnya."""
    import urllib.parse
    # doseq=True: nilai berupa daftar jadi parameter berulang (?c=a&c=b),
    # bukan satu string "['a', 'b']" yang tidak berarti apa-apa di server.
    url = "/?" + urllib.parse.urlencode(q, doseq=True) if q else "/"
    html = klien.get(url).text
    return re.findall(r'<div class="fn mono">(?:<span class="split">[^<]*</span>)?([^<]+)</div>',
                      html)


def _dataset_berwaktu(tmp, jeda):
    """Dataset labelme yang waktu-ubah anotasinya sengaja dibuat berbeda."""
    import cv2
    import numpy as np
    d = tmp / "urut"
    d.mkdir(parents=True, exist_ok=True)
    for i, (nama, geser) in enumerate(jeda.items()):
        p = d / f"{nama}.jpg"
        cv2.imwrite(str(p), np.full((60, 80, 3), 40 + i * 30, np.uint8))
        p.with_suffix(".json").write_text(json.dumps({
            "version": "0.4.36", "flags": {}, "imagePath": p.name,
            "imageHeight": 60, "imageWidth": 80, "imageData": None,
            "shapes": [{"label": "botol", "shape_type": "polygon",
                        "points": [[5, 5], [70, 5], [70, 50]]}] * (i + 1)}))
        t = 1_700_000_000 + geser
        os.utime(p.with_suffix(".json"), (t, t))
    return d


def test_grid_bisa_diurutkan_menurut_waktu_dilabeli(klien, lingkungan):
    """
    Yang paling sering dibutuhkan: melabeli beberapa gambar, kembali ke grid,
    dan langsung melihat hasilnya di depan.
    """
    masuk(klien, "paul", PW_PAUL)
    d = _dataset_berwaktu(lingkungan["roots"], {"aaa": 300, "bbb": 100, "ccc": 200})
    klien.post(f"/setsrc?path={d}")

    assert _grid_nama(klien) == ["aaa.jpg", "bbb.jpg", "ccc.jpg"]          # bawaan: abjad
    assert _grid_nama(klien, s="label-baru") == ["aaa.jpg", "ccc.jpg", "bbb.jpg"]
    assert _grid_nama(klien, s="label-lama") == ["bbb.jpg", "ccc.jpg", "aaa.jpg"]
    assert _grid_nama(klien, s="nama-turun") == ["ccc.jpg", "bbb.jpg", "aaa.jpg"]


def test_grid_urutan_waktu_ikut_berubah_setelah_menyimpan(klien, lingkungan):
    """Waktu label dibaca dari disk tiap render, bukan dibekukan saat memindai —
    kalau dibekukan, urutan 'terbaru dilabeli' tidak berubah sampai dipindai
    ulang, dan justru itu yang membuatnya tidak berguna."""
    masuk(klien, "paul", PW_PAUL)
    d = _dataset_berwaktu(lingkungan["roots"], {"aaa": 300, "bbb": 100, "ccc": 200})
    klien.post(f"/setsrc?path={d}")
    assert _grid_nama(klien, s="label-baru")[0] == "aaa.jpg"

    # bbb baru saja disimpan lewat kanvas -> harus naik ke depan, tanpa rescan
    ip = d / "bbb.jpg"
    r = klien.post("/api/simpan", json={
        "path": str(ip), "flags": {},
        "shapes": [{"label": "kaleng", "shape_type": "polygon",
                    "points": [[5, 5], [70, 5], [70, 50]], "flags": {}}]})
    assert r.json()["ok"] is True
    assert _grid_nama(klien, s="label-baru")[0] == "bbb.jpg"


def test_grid_urut_objek_dan_cari_nama(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    d = _dataset_berwaktu(lingkungan["roots"], {"aaa": 300, "bbb": 100, "ccc": 200})
    klien.post(f"/setsrc?path={d}")
    # jumlah objeknya 1, 2, 3 sesuai urutan pembuatan
    assert _grid_nama(klien, s="objek-banyak") == ["ccc.jpg", "bbb.jpg", "aaa.jpg"]
    assert _grid_nama(klien, s="objek-sedikit") == ["aaa.jpg", "bbb.jpg", "ccc.jpg"]
    assert _grid_nama(klien, q="bb") == ["bbb.jpg"]
    assert _grid_nama(klien, q="ZZZ") == []


def test_grid_urutan_bertahan_saat_saringan_diklik(klien, lingkungan):
    """Mengklik chip saringan tidak boleh diam-diam mengembalikan urutan ke
    bawaan — orang akan menyangka daftarnya yang berubah, bukan urutannya."""
    masuk(klien, "paul", PW_PAUL)
    d = _dataset_berwaktu(lingkungan["roots"], {"aaa": 300, "bbb": 100, "ccc": 200})
    klien.post(f"/setsrc?path={d}")
    html = klien.get("/?s=label-baru&q=b").text
    for potongan in ("s=label-baru", "q=b"):
        assert html.count(potongan) >= 3, f"{potongan} tidak dibawa tautan saringan"


def test_grid_urutan_tak_dikenal_kembali_ke_bawaan(klien, lingkungan):
    """Nilai asing di URL tidak boleh menggagalkan halaman."""
    masuk(klien, "paul", PW_PAUL)
    d = _dataset_berwaktu(lingkungan["roots"], {"aaa": 300, "bbb": 100, "ccc": 200})
    klien.post(f"/setsrc?path={d}")
    assert _grid_nama(klien, s="tidak-ada-urutan-ini") == ["aaa.jpg", "bbb.jpg", "ccc.jpg"]


def test_saringan_kelas_bisa_beberapa_sekaligus(klien, lingkungan):
    """
    Sebelumnya hanya satu kelas. Sekarang beberapa, dengan arti "punya SALAH
    SATU dari ini" — sama seperti filter Classes di Roboflow.
    """
    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={lingkungan['roots'] / 'ds-alpha'}")

    satu = klien.get("/?f=all&c=botol").text.count('class="card"')
    dua = klien.get("/?f=all&c=botol&c=kaleng").text.count('class="card"')
    assert satu == 1
    assert dua == 2, "dua kelas harus menampilkan gabungan keduanya"
    # tautan lama dengan satu kelas tetap bekerja seperti dulu
    assert klien.get("/?f=all&c=kaleng").text.count('class="card"') == 1


def test_saringan_kelas_asing_tetap_dipakai_bukan_diabaikan(klien, lingkungan):
    """Membuang kelas yang tidak dikenal membuat saringan diam-diam tidak
    berlaku, dan grid menampilkan SEMUANYA — terlihat seperti saringan yang
    bekerja padahal tidak."""
    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={lingkungan['roots'] / 'ds-alpha'}")
    html = klien.get("/?f=all&c=tidak-ada").text
    assert html.count('class="card"') == 0
    assert "0 dari 4 gambar tampil" in html


def test_dropdown_kelas_membawa_urutan_dan_pencarian(klien, lingkungan):
    """Menerapkan saringan kelas tidak boleh diam-diam mengembalikan urutan."""
    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={lingkungan['roots'] / 'ds-alpha'}")
    html = klien.get("/?f=all&s=label-baru&q=alpha").text
    # form dropdown membawa keduanya sebagai field tersembunyi
    assert 'name="s" value="label-baru"' in html
    assert 'name="q" value="alpha"' in html


def test_warna_kelas_sama_antara_server_dan_kanvas_dan_tidak_acak(lingkungan):
    """
    `hash()` bawaan Python diacak ulang tiap proses, jadi warna kelas berubah
    setiap server dinyalakan ulang dan tidak pernah cocok dengan kanvas — walau
    komentar di label.js selama ini menyatakan sebaliknya.
    """
    import subprocess
    import sys

    from app.services.render import cls_color, hash_kelas, warna_kelas

    # nilainya tetap, tidak bergantung pada proses
    keluar = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, '.');"
         "from app.services.render import warna_kelas; print(warna_kelas('botol'))"],
        capture_output=True, text=True, cwd=str(__import__("pathlib").Path(__file__).parent.parent))
    assert keluar.stdout.strip() == warna_kelas("botol"), keluar.stderr[-300:]

    # cocok dengan hashKode di label.js: h = (h*31 + kode) | 0, lalu abs
    def js_hash(t):
        h = 0
        for ch in t:
            h = (h * 31 + ord(ch)) & 0xFFFFFFFF
            if h >= 0x80000000:
                h -= 0x100000000
        return abs(h)

    for nama in ("botol", "kaleng", "plastic-cup", "kahf_extradry_deodorant_45ml"):
        assert hash_kelas(nama) == js_hash(nama), nama
    assert warna_kelas("botol") == "hsl(185, 62%, 55%)"
    assert cls_color("botol") == (69, 200, 211)


def test_saringan_latar_terpisah_dari_belum_dilabeli(klien, lingkungan):
    """
    Gambar yang SENGAJA ditandai tanpa objek adalah sampel negatif — sudah
    selesai diperiksa, bukan pekerjaan yang tertinggal. Keduanya sama-sama
    tanpa objek, jadi tanpa saringan sendiri ia tidak bisa dihitung maupun
    ditinjau ulang.
    """
    masuk(klien, "paul", PW_PAUL)
    src = lingkungan["roots"] / "ds-alpha"          # 4 gambar, 2 berlabel
    klien.post(f"/setsrc?path={src}")

    assert klien.get("/?f=bg").text.count('class="card"') == 0
    assert klien.get("/?f=unlab").text.count('class="card"') == 2
    assert klien.get("/?f=sudah").text.count('class="card"') == 2

    img = _gambar(lingkungan, "ds-alpha", 3)        # belum berlabel
    assert klien.post(f"/markbg?path={img}").json()["ok"] is True

    assert klien.get("/?f=bg").text.count('class="card"') == 1
    assert klien.get("/?f=unlab").text.count('class="card"') == 1
    # Latar IKUT "sudah dilabeli", dan itu yang dikatakan kalimat pertama
    # keterangan di atas: ia sudah selesai diperiksa. Empat tempat lain —
    # kartu projek, sidebar, papan anotasi, halaman tugas — sudah
    # menghitungnya begitu sejak awal, dan grid yang sendirian
    # mengecualikannya membuat satu projek menyebut dua angka berbeda untuk
    # hal yang sama di dua halaman.
    assert klien.get("/?f=sudah").text.count('class="card"') == 3, \
        "latar tidak ikut dihitung sudah dilabeli"
    # Yang tetap terpisah: latar BUKAN belum-dilabeli. Itu bedanya, dan itu
    # yang membuat saringannya sendiri tetap perlu ada.
    assert klien.get("/?f=unlab").text.count('class="card"') == 1
    assert klien.get("/?f=all").text.count('class="card"') == 4

    # Angka di chip harus sama dengan isi grid saat chip itu diklik. "Sudah
    # dilabeli" memuat yang latar, jadi ia tumpang tindih dengan chip Latar —
    # sama seperti ia sudah tumpang tindih dengan "Perlu dicek". Yang saling
    # lepas adalah potongan bilah kemajuan, bukan chipnya.
    html = klien.get("/?f=all").text
    assert _chip(html, "Latar") == 1
    assert _chip(html, "Sudah dilabeli") == 3
    assert _chip(html, "Belum dilabeli") == 1


def _potongan(html: str) -> dict:
    """Potongan bilah kemajuan -> {keadaan: bobotnya}."""
    import re
    return {k: int(v) for k, v in
            re.findall(r'class="p-(\w+)" style="flex-grow:(\d+)"', html)}


def test_bilah_kemajuan_sepadan_dengan_isi_dataset(klien, lingkungan):
    """Bilah kemajuan harus bisa dipercaya sebagai gambaran seluruh dataset.

    Dua hal yang dijaga. Potongannya saling lepas dan jumlahnya pas jumlah
    gambar — kalau tidak, satu gambar terhitung dua kali dan bilahnya
    memperbesar kemajuan yang sebenarnya. Dan keadaan yang nol tidak
    digambar sama sekali; potongan dengan lebar minimum akan terlihat sebagai
    pekerjaan yang tidak pernah ada.
    """
    masuk(klien, "paul", PW_PAUL)
    src = lingkungan["roots"] / "ds-alpha"          # 4 gambar, 2 berlabel
    klien.post(f"/setsrc?path={src}")

    html = klien.get("/").text
    # Kedua gambar berlabel di dataset uji ini berstatus "perlu dicek", dan itu
    # justru inti perkaranya: chip "Sudah dilabeli" ikut menghitung keduanya,
    # jadi chip itu TIDAK bisa dipakai sebagai potongan bilah tanpa membuat dua
    # gambar yang sama terhitung dua kali.
    assert _potongan(html) == {"warn": 2, "stop": 2}, _potongan(html)
    assert _chip(html, "Sudah dilabeli") == 2 and _chip(html, "Perlu dicek") == 2
    assert "50% selesai" in html

    klien.post(f"/markbg?path={_gambar(lingkungan, 'ds-alpha', 3)}")
    html = klien.get("/").text
    pot = _potongan(html)
    assert pot == {"warn": 2, "bg": 1, "stop": 1}, pot
    assert sum(pot.values()) == 4, "potongan tidak menjumlah seluruh gambar"
    assert "75% selesai" in html, "latar itu sudah selesai diperiksa"

    # Chip membawa titik warna yang sama dengan potongannya; itu yang
    # menjadikan barisan chip sekaligus keterangan bilah.
    for keadaan in ("ok", "warn", "stop", "bg"):
        assert f'class="titik t-{keadaan}"' in html, keadaan


def test_saringan_latar_bisa_digabung_dengan_urutan(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    src = lingkungan["roots"] / "ds-alpha"
    klien.post(f"/setsrc?path={src}")
    for i in (2, 3):
        klien.post(f"/markbg?path={_gambar(lingkungan, 'ds-alpha', i)}")
    nama = _grid_nama(klien, f="bg", s="nama-turun")
    assert nama == ["ds-alpha-03.jpg", "ds-alpha-02.jpg"], nama


def test_dropdown_kelas_bisa_memilih_latar_dan_belum_dilabeli(klien, lingkungan):
    """
    Padanan "null" di filter Classes Roboflow. Keduanya sama-sama gambar tanpa
    objek tetapi artinya berlawanan: latar sudah selesai diperiksa dan sengaja
    dikosongkan, belum-dilabeli justru pekerjaan yang tertinggal.
    """
    masuk(klien, "paul", PW_PAUL)
    src = lingkungan["roots"] / "ds-alpha"          # 4 gambar, 2 berlabel
    klien.post(f"/setsrc?path={src}")
    klien.post(f"/markbg?path={_gambar(lingkungan, 'ds-alpha', 3)}")

    n = lambda u: klien.get(u).text.count('class="card"')   # noqa: E731
    assert n("/?f=all&x=latar") == 1
    assert n("/?f=all&x=unlab") == 1
    assert n("/?f=all&x=latar&x=unlab") == 2

    # digabung dengan kelas biasa, artinya ATAU
    assert n("/?f=all&c=botol") == 1
    assert n("/?f=all&c=botol&x=latar") == 2
    assert n("/?f=all&c=botol&c=kaleng&x=latar&x=unlab") == 4

    # nilai asing diabaikan, bukan menggagalkan halaman
    assert n("/?f=all&x=tidak-ada") == 4


def test_pilihan_latar_bertahan_saat_saringan_lain_diklik(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    src = lingkungan["roots"] / "ds-alpha"
    klien.post(f"/setsrc?path={src}")
    klien.post(f"/markbg?path={_gambar(lingkungan, 'ds-alpha', 3)}")
    html = klien.get("/?f=all&x=latar&s=label-baru").text
    assert html.count("x=latar") >= 3, "pilihan latar tidak dibawa tautan lain"
    assert 'name="s" value="label-baru"' in html


def _ds_dua_kelas(tmp):
    """Empat gambar: botol saja, kaleng saja, keduanya, dan tanpa objek."""
    import cv2
    import numpy as np
    d = tmp / "duakelas"
    d.mkdir(parents=True, exist_ok=True)
    isi = {"a-botol": ["botol"], "b-kaleng": ["kaleng"],
           "c-dua": ["botol", "kaleng"], "d-kosong": []}
    for i, (nama, labels) in enumerate(isi.items()):
        p = d / f"{nama}.jpg"
        cv2.imwrite(str(p), np.full((60, 80, 3), 40 + i * 30, np.uint8))
        p.with_suffix(".json").write_text(json.dumps({
            "version": "0.4.36", "flags": {}, "imagePath": p.name,
            "imageHeight": 60, "imageWidth": 80, "imageData": None,
            "shapes": [{"label": l, "shape_type": "polygon",
                        "points": [[5, 5], [70, 5], [70, 50]]} for l in labels]}))
    return d


def test_saringan_kelas_mode_dan_menuntut_semuanya_dalam_satu_gambar(klien,
                                                                     lingkungan):
    """
    Mencentang dua kelas bisa berarti dua hal yang sangat berbeda:
    "ada botol ATAU ada kaleng", atau "ada botol DAN kaleng di gambar yang
    sama". Keduanya dibutuhkan, jadi aturannya bisa dipilih.
    """
    masuk(klien, "paul", PW_PAUL)
    d = _ds_dua_kelas(lingkungan["roots"])
    klien.post(f"/setsrc?path={d}")

    assert _grid_nama(klien, f="all", c=["botol", "kaleng"]) == \
        ["a-botol.jpg", "b-kaleng.jpg", "c-dua.jpg"]
    assert _grid_nama(klien, f="all", c=["botol", "kaleng"], m="dan") == ["c-dua.jpg"]

    # satu kelas: kedua aturan memberi hasil yang sama
    assert _grid_nama(klien, f="all", c=["botol"], m="dan") == \
        _grid_nama(klien, f="all", c=["botol"])

    # nilai mode asing dianggap "atau", bukan menggagalkan halaman
    assert len(_grid_nama(klien, f="all", c=["botol", "kaleng"], m="zzz")) == 3


def test_mengurutkan_tidak_merusak_saringan_kelas_yang_aktif(klien, lingkungan,
                                                             tmp_path):
    """Form urutkan/cari membawa c, x, dan m apa adanya.

    Dulu seluruh daftar kelas ditulis sebagai satu nilai, jadi isinya menjadi
    repr Python ("['botol', 'kaleng']"). Mengurutkan saat saringan aktif
    mengirim satu nama kelas palsu yang tidak cocok apa pun, dan grid
    mendadak kosong. `x` dan `m` bahkan tidak ikut sama sekali.
    """
    masuk(klien, "paul", PW_PAUL)
    d = _ds_dua_kelas(lingkungan["roots"])
    klien.post(f"/setsrc?path={d}")
    html = klien.get("/?c=botol&c=kaleng&m=dan").text
    form = html[html.index('class="bar bar-urut"'):]
    form = form[:form.index("</form>")]
    nilai = re.findall(r'name="c" value="([^"]*)"', form)
    assert nilai == ["botol", "kaleng"], nilai
    assert 'name="m" value="dan"' in form

    # dan hasilnya benar-benar bertahan saat form itu dikirim
    lanjut = klien.get("/?c=botol&c=kaleng&m=dan&s=label-baru").text
    assert "1 dari 4 gambar tampil" in lanjut, "hanya c-dua yang punya keduanya"


def test_tombol_kartu_membedakan_melabeli_dari_menyunting(klien, lingkungan,
                                                          tmp_path):
    """Kartu yang sudah ada bentuknya menawarkan "Edit label", bukan "Labeli".

    Yang ditandai latar tetap "Labeli": tidak ada label untuk disunting di
    sana, dan ke situlah kamu masuk justru kalau tanda latarnya salah.
    """
    masuk(klien, "paul", PW_PAUL)
    d = _ds_dua_kelas(lingkungan["roots"])
    klien.post(f"/setsrc?path={d}")
    kosong = d / "d-kosong.jpg"
    klien.post(f"/markbg?path={kosong}")

    html = klien.get("/").text
    kartu = {}
    for bagian in html.split('<div class="card"')[1:]:
        nama = re.search(r'class="fn mono">(?:.*?</span>)?([^<]+)<', bagian).group(1)
        aksi = bagian[bagian.index('class="acts"'):]
        kartu[nama.strip()] = "Edit label" if ">Edit label</a>" in aksi else "Labeli"

    assert kartu["a-botol.jpg"] == "Edit label"
    assert kartu["c-dua.jpg"] == "Edit label"
    assert kartu["d-kosong.jpg"] == "Labeli", "kartu latar tidak punya label"
    # tautannya tetap ke kanvas yang sama, hanya katanya yang berubah
    assert html.count("/label?path=") == len(kartu)


def test_saringan_kelas_tidak_menumpuk_chip_di_samping_tombolnya(klien, lingkungan,
                                                                 tmp_path):
    """Keadaan saringan disebut sekali saja, di tombolnya sendiri.

    Dulu tiap kelas tercentang juga muncul sebagai chip terpisah di sebelah
    tombol. Pada nama kelas panjang — mis. kahf_skinergizing_facewash_50ml —
    dua centang saja sudah memenuhi satu baris penuh, mengulang isi dropdown
    di luar dropdownnya. Mencabut satu pilihan tetap bisa: buka dropdownnya
    dan lepas centangnya.
    """
    masuk(klien, "paul", PW_PAUL)
    d = _ds_dua_kelas(lingkungan["roots"])
    klien.post(f"/setsrc?path={d}")
    html = klien.get("/?c=botol&c=kaleng&m=dan&x=latar").text
    bar = html[html.index('id="menu-kelas"'):]
    bar = bar[:bar.index("</div>")]
    assert "\u00d7</a>" not in bar, "chip saringan muncul lagi di samping tombol"
    # tapi keadaannya tetap terbaca, bukan hilang diam-diam
    tombol = bar[bar.index('id="kelas-tombol"'):bar.index("</button>")]
    # x=latar sengaja gugur di mode "semuanya", jadi dua, bukan tiga
    assert "2 pilihan" in tombol and "semuanya" in tombol
    assert "botol, kaleng" in tombol, "nama yang dicentang hilang dari tooltip"


def test_mode_dan_tidak_menawarkan_latar_dan_belum_dilabeli(klien, lingkungan):
    """
    Keputusan yang diubah setelah dipakai.

    Awalnya aturan "semuanya" berlaku seragam, termasuk untuk Latar dan Belum
    dilabeli — sehingga "punya botol DAN latar" memberi nol. Angka nol itu
    memang benar: gambar berobjek menurut definisinya bukan latar. Tetapi
    MENAWARKAN pilihan yang pasti nol itu sendiri sudah cacat, betapa pun
    benarnya hasilnya.

    Sekarang keduanya tidak ditawarkan di mode itu, dan dibuang juga di server
    supaya URL lama tidak menghasilkan saringan yang tidak bisa dibuat lewat
    antarmukanya sendiri.
    """
    masuk(klien, "paul", PW_PAUL)
    d = _ds_dua_kelas(lingkungan["roots"])
    klien.post(f"/setsrc?path={d}")

    # mode "semuanya": x diabaikan, yang menentukan hanya kelasnya
    assert _grid_nama(klien, f="all", c=["botol"], x="latar", m="dan") == \
        _grid_nama(klien, f="all", c=["botol"], m="dan")
    assert _grid_nama(klien, f="all", c=["botol", "kaleng"], x="unlab", m="dan") == \
        ["c-dua.jpg"]

    # blok pilihannya juga tidak dirender, bukan sekadar diabaikan diam-diam
    html = klien.get("/?f=all&m=dan").text
    assert 'class="kelas-daftar kelas-tanpa"' in html
    assert re.search(r'kelas-daftar kelas-tanpa"\s*\n?\s*hidden', html), \
        "blok latar/belum-dilabeli harus tersembunyi di mode semuanya"

    # di mode "salah satu" keduanya tetap berlaku seperti biasa
    assert len(_grid_nama(klien, f="all", c=["botol"], x="unlab")) == 2
    assert "hidden" not in re.search(
        r'class="kelas-daftar kelas-tanpa"[^>]*>', klien.get("/?f=all").text).group(0)


def test_latar_tidak_pernah_masuk_perlu_dicek(klien, lingkungan):
    """Menandai latar itu keputusan, bukan kecurigaan.

    "Perlu dicek" menandai bentuk objek yang mencurigakan: mask sangat kecil
    atau memenuhi frame, poligon di bawah 4 titik, titik di luar tepi, kelas
    kosong. Semuanya tentang objek yang SUDAH digambar.

    Gambar latar tidak punya objek sama sekali, jadi tidak ada yang bisa
    dicurigai padanya. Kalau ia sampai masuk sini, setiap contoh negatif yang
    dibuat dengan sengaja akan tampak seperti pekerjaan yang salah.
    """
    masuk(klien, "paul", PW_PAUL)
    src = lingkungan["roots"] / "ds-alpha"          # 4 gambar, 2 berlabel
    klien.post(f"/setsrc?path={src}")
    sebelum = _chip(klien.get("/?f=all").text, "Perlu dicek")

    img = _gambar(lingkungan, "ds-alpha", 3)        # belum berlabel
    assert klien.post(f"/markbg?path={img}").json()["ok"] is True

    h = klien.get("/?f=all").text
    assert _chip(h, "Perlu dicek") == sebelum, "latar ikut dihitung perlu dicek"
    assert klien.get("/?f=issue").text.count('class="card"') == sebelum
    # Dan chipnya menjelaskan dirinya sebelum diklik: nama "Perlu dicek"
    # sendiri tidak memberi tahu apa yang dicek.
    assert "Gambar latar tidak termasuk" in h

    # Latar tetap punya saringannya sendiri, di chip dan di daftar kelas.
    assert klien.get("/?f=bg").text.count('class="card"') == 1
    assert klien.get("/?x=latar").text.count('class="card"') == 1


def test_bentuk_bertitik_kurang_tidak_diam_diam_jadi_latar(klien, lingkungan):
    """Berkas anotasi kosong adalah PENANDA LATAR, bukan "tidak ada objek".

    Bentuk yang titiknya di bawah minimum dibuang. Kalau seluruhnya terbuang,
    berkasnya ditulis kosong — dan gambarnya berubah jadi contoh negatif yang
    ikut dilatih, dengan balasan ok:true dan tanpa satu pun peringatan.
    """
    import pathlib

    masuk(klien, "paul", PW_PAUL)
    src = lingkungan["roots"] / "ds-alpha"
    klien.post(f"/setsrc?path={src}")
    img = _gambar(lingkungan, "ds-alpha", 3)          # belum berlabel
    jp = pathlib.Path(img).with_suffix(".json")
    jp.unlink(missing_ok=True)
    klien.post("/rescan")

    r = klien.post("/api/simpan", json={"path": str(img), "shapes": [
        {"label": "botol", "shape_type": "polygon", "points": [[5, 5], [40, 40]]}]}).json()
    assert r["ok"] is False and "titiknya kurang" in r["error"], r
    assert not jp.exists(), "berkas latar ditulis padahal tidak ada yang diminta"

    # Sebagian terbuang: yang sah tetap tersimpan, dan yang dibuang disebutkan.
    r = klien.post("/api/simpan", json={"path": str(img), "shapes": [
        {"label": "botol", "shape_type": "polygon",
         "points": [[5, 5], [40, 5], [40, 40]]},
        {"label": "botol", "shape_type": "polygon", "points": [[1, 1], [2, 2]]}]}).json()
    assert r["ok"] is True and r["n"] == 1 and r["kurang_titik"] == 1, r
    jp.unlink(missing_ok=True)


def test_berkas_anotasi_rusak_disisihkan_bukan_ditimpa(klien, lingkungan):
    """Berkasnya sudah rusak; yang bisa dilakukan cuma tidak ikut menghapusnya.

    Kanvas dulu terbuka dengan nol objek dan status "siap" — tidak ada satu pun
    tanda bahwa berkasnya tidak bisa dibaca — lalu menyimpan menimpanya, dan
    satu-satunya kesempatan memperbaikinya dengan tangan hilang.
    """
    import pathlib

    masuk(klien, "paul", PW_PAUL)
    src = lingkungan["roots"] / "ds-alpha"
    klien.post(f"/setsrc?path={src}")
    img = pathlib.Path(_gambar(lingkungan, "ds-alpha", 3))
    jp = img.with_suffix(".json")
    jp.write_text('{"shapes": [ini bukan json')
    klien.post("/rescan")

    h = klien.get(f"/label?path={img}").text
    assert "tidak bisa dibaca" in h, "kanvas diam soal berkas yang rusak"

    r = klien.post("/api/simpan", json={"path": str(img), "shapes": [
        {"label": "botol", "shape_type": "rectangle",
         "points": [[5, 5], [40, 40]]}]}).json()
    assert r["ok"] is True and r["cadangan_rusak"], r
    cadangan = list(img.parent.glob(img.stem + ".json.rusak-*"))
    assert len(cadangan) == 1, cadangan
    assert "ini bukan json" in cadangan[0].read_text()
    for q in cadangan:
        q.unlink()
    jp.unlink(missing_ok=True)
