"""
Uji ekspor YOLO.

Nilai acuan di sini diambil dari perbandingan langsung terhadap
FormatExporter.export_to_yolo milik AnyLabeling: keluarannya byte-identik untuk
mode segmentation maupun detection, termasuk rectangle yang dijadikan 4 sudut
dan poligon yang diringkas menjadi bounding box.
"""
from __future__ import annotations

import io
import json
import zipfile

import cv2
import numpy as np

from app.services import export as ex
from app.services import scanner


def _dataset(d, W=100, H=80):
    d.mkdir(parents=True, exist_ok=True)
    isi = [
        [{"label": "botol", "shape_type": "polygon",
          "points": [[10, 10], [90, 12], [95, 70], [15, 65]]},
         {"label": "kaleng", "shape_type": "rectangle",
          "points": [[20, 20], [60, 55]]}],
        [],                                    # tanpa objek: contoh negatif
    ]
    for i, shapes in enumerate(isi):
        p = d / f"g{i}.jpg"
        cv2.imwrite(str(p), np.full((H, W, 3), 50 + i * 40, np.uint8))
        p.with_suffix(".json").write_text(json.dumps({
            "version": "0.4.36", "flags": {}, "shapes": shapes,
            "imagePath": p.name, "imageData": None,
            "imageHeight": H, "imageWidth": W}))
    return d


def test_peta_kelas_terurut_mulai_nol(tmp_path):
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    assert ex.peta_kelas(items) == {"botol": 0, "kaleng": 1}


def test_baris_segmentasi_sama_dengan_anylabeling(tmp_path):
    """
    Cincin poligon DITUTUP: titik pertama diulang di akhir.

    AnyLabeling melakukannya untuk tiap poligon hasil SAM
    (`segment_anything.py:235`), dan ekspor Roboflow juga — diukur 40 dari 40
    poligon pada dataset nyata. Bentuknya tidak berubah sedikit pun karena
    perasterisasi menutup poligon sendiri; ini murni supaya berkas kita bisa
    dibandingkan berdampingan dengan keduanya.
    """
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    peta = ex.peta_kelas(items)
    baris = ex.baris_yolo(items[0], peta, True)
    assert baris == [
        "0 0.100000 0.125000 0.900000 0.150000 0.950000 0.875000 0.150000 0.812500"
        " 0.100000 0.125000",
        # rectangle -> kiri-atas, kanan-atas, kanan-bawah, kiri-bawah, lalu tutup
        "1 0.200000 0.250000 0.600000 0.250000 0.600000 0.687500 0.200000 0.687500"
        " 0.200000 0.250000",
    ]
    # titik pertama dan terakhir memang sama persis
    for b in baris:
        v = [float(x) for x in b.split()[1:]]
        assert (v[0], v[1]) == (v[-2], v[-1])


def test_baris_deteksi_sama_dengan_anylabeling(tmp_path):
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    peta = ex.peta_kelas(items)
    assert ex.baris_yolo(items[0], peta, False) == [
        "0 0.525000 0.500000 0.850000 0.750000",     # poligon -> bbox
        "1 0.400000 0.468750 0.400000 0.437500",
    ]


def test_gambar_tanpa_objek_dapat_label_kosong(tmp_path):
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    peta = ex.peta_kelas(items)
    assert ex.baris_yolo(items[1], peta, True) == []


def test_zip_bertata_letak_roboflow(tmp_path):
    """
    Tata letak disalin dari ekspor Roboflow nyata
    (sirsak-v13/botol-kaleng-tetra-mlp-cup-1): data.yaml di akar, lalu
    train/valid/test masing-masing berisi images/ dan labels/.
    """
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    with zipfile.ZipFile(io.BytesIO(ex.zip_yolo(items, "ds", True))) as z:
        isi = z.namelist()
        assert "data.yaml" in isi and "README.txt" in isi
        # setiap gambar mendarat di salah satu split, dengan label bernama sama
        for stem in ("g0", "g1"):
            gambar = [n for n in isi if n.endswith(f"/images/{stem}.jpg")]
            label = [n for n in isi if n.endswith(f"/labels/{stem}.txt")]
            assert len(gambar) == 1 and len(label) == 1, stem
            assert gambar[0].split("/")[0] == label[0].split("/")[0]
            assert gambar[0].split("/")[0] in ex.SPLIT
        # tidak ada tata letak lama yang tertinggal
        assert not [n for n in isi if n.startswith(("images/", "labels/"))]
        assert "classes.txt" not in isi


def test_data_yaml_sama_bentuknya_dengan_roboflow(tmp_path):
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    y = ex.data_yaml(ex.peta_kelas(items), "ds")
    assert y.startswith("train: ../train/images\n"
                        "val: ../valid/images\n"
                        "test: ../test/images\n")
    assert "nc: 2" in y
    assert "names: ['botol', 'kaleng']" in y      # daftar rata, bukan dict
    assert "path:" not in y                        # Roboflow tidak memakainya


def test_pembagian_split_deterministik(tmp_path):
    """
    Ekspor berulang harus memberi pembagian yang sama. Kalau tidak, gambar
    latih bisa berpindah ke validasi dan angka evaluasi jadi terlalu bagus.
    """
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    a = {k: [x["img"].name for x in v] for k, v in ex.bagi_split(items).items()}
    b = {k: [x["img"].name for x in v] for k, v in ex.bagi_split(items).items()}
    assert a == b
    # setiap gambar tepat di satu split
    semua = [n for v in a.values() for n in v]
    assert sorted(semua) == ["g0.jpg", "g1.jpg"]


def test_zip_tanpa_gambar(tmp_path):
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    with zipfile.ZipFile(io.BytesIO(ex.zip_yolo(items, "ds", True, False))) as z:
        isi = z.namelist()
        # entri folder (berakhiran "/") selalu ada; yang harus kosong adalah
        # berkas gambarnya
        assert not [n for n in isi if "/images/" in n and not n.endswith("/")]
        assert [n for n in isi if "/labels/" in n and not n.endswith("/")]


def test_ringkasan(tmp_path):
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    r = ex.ringkasan(items, True)
    assert r["gambar"] == 2 and r["objek"] == 2 and r["kelas"] == 2
    assert r["tanpa_objek"] == 1
    assert r["nama_kelas"] == ["botol", "kaleng"]


# ---------------------------------------------------------------- VOC & COCO

def test_voc_xml_sama_dengan_anylabeling(tmp_path):
    """Struktur & nilai acuan dari export_to_pascal_voc; diuji identik."""
    from app.services import export as ex
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    x = ex.voc_xml(items[0])
    for potongan in ('<folder>', '<filename>g0.jpg</filename>', '<database>Unknown</database>',
                     '<width>100</width>', '<height>80</height>', '<depth>3</depth>',
                     '<segmented>0</segmented>', '<pose>Unspecified</pose>',
                     '<truncated>0</truncated>', '<difficult>0</difficult>'):
        assert potongan in x, potongan
    # bndbox: koordinat dipotong int, bukan dibulatkan
    assert '<xmin>10</xmin>' in x and '<ymax>70</ymax>' in x     # poligon
    assert '<xmin>20</xmin>' in x and '<xmax>60</xmax>' in x     # rectangle
    assert x.count('<object>') == 2
    # gambar tanpa objek: XML tetap ada, tanpa <object>
    assert '<object>' not in ex.voc_xml(items[1])


def test_coco_struktur_dan_id(tmp_path):
    from app.services import export as ex
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    d = ex.coco_dict(items, "ds")
    assert d["categories"] == [
        {"id": 1, "name": "botol", "supercategory": "none"},
        {"id": 2, "name": "kaleng", "supercategory": "none"},
    ]                                        # id mulai 1, label terurut
    assert [i["id"] for i in d["images"]] == [1, 2]
    assert d["images"][0]["file_name"] == "g0.jpg"
    assert d["licenses"] == [{"id": 1, "name": "Unknown", "url": ""}]
    a = d["annotations"]
    assert [x["id"] for x in a] == [1, 2]     # id anotasi mulai 1, menerus
    assert a[0]["iscrowd"] == 0
    assert a[1]["bbox"] == [20.0, 20.0, 40.0, 35.0]
    # rectangle -> segmentasi 4 sudut, area width*height
    assert a[1]["segmentation"] == [[20.0, 20.0, 60.0, 20.0, 60.0, 55.0, 20.0, 55.0]]
    assert a[1]["area"] == 40.0 * 35.0


def test_indeks_kelas_mengikuti_urutan_data_yaml(tmp_path):
    """
    Regresi diam-diam. Indeks kelas dulu selalu diturunkan ulang dari label
    yang KEBETULAN ada di seleksi, diurutkan abjad. Begitu satu kelas tidak
    punya objek — setelah menyaring grid, atau saat mengekspor sebagian —
    kelas itu hilang dan seluruh indeks sesudahnya bergeser. Berkasnya tetap
    konsisten dengan data.yaml barunya sehingga tidak ada yang tampak salah,
    padahal labelnya tidak lagi cocok dengan dataset asal.
    """
    from pathlib import Path
    import numpy as np

    names = {0: "botol", 1: "kaleng", 2: "mlp", 3: "plastic-cup", 4: "tetra"}

    def it(label):
        return {"img": Path(f"/x/{label}.jpg"), "W": 100, "H": 100,
                "shapes": [{"label": label, "type": "polygon",
                            "pts": np.array([[10, 10], [50, 10], [50, 50]], np.float32)}]}

    sebagian = [it(l) for l in ("botol", "mlp", "tetra")]

    # tanpa daftar kelas resmi: indeks bergeser
    assert ex.peta_kelas(sebagian) == {"botol": 0, "mlp": 1, "tetra": 2}
    # dengan daftar kelas resmi: indeks aslinya dipertahankan, kelas kosong ikut
    assert ex.peta_kelas(sebagian, names) == {
        "botol": 0, "kaleng": 1, "mlp": 2, "plastic-cup": 3, "tetra": 4}


def test_label_di_luar_daftar_resmi_ditambahkan_di_belakang(tmp_path):
    """Kelas baru tidak boleh terbuang, dan tidak boleh menggeser yang lama."""
    from pathlib import Path
    import numpy as np

    names = {0: "botol", 1: "kaleng"}
    items = [{"img": Path("/x/a.jpg"), "W": 100, "H": 100,
              "shapes": [{"label": "kelas-baru", "type": "polygon",
                          "pts": np.array([[1, 1], [9, 1], [9, 9]], np.float32)}]}]
    assert ex.peta_kelas(items, names) == {"botol": 0, "kaleng": 1, "kelas-baru": 2}


def test_data_yaml_mengikuti_peta_kelas(tmp_path):
    names = {0: "botol", 1: "kaleng", 2: "mlp"}
    y = ex.data_yaml(ex.peta_kelas([], names), "uji")
    assert "nc: 3" in y
    assert "names: ['botol', 'kaleng', 'mlp']" in y


def test_cincin_poligon_ditutup_dan_dibuka_secara_simetris():
    """
    Berkas menyimpan cincin tertutup; kanvas memakainya terbuka.

    Dua titik yang bertumpuk persis membuat penyuntingan menyesatkan —
    menyeret salah satunya meninggalkan duri yang tidak terlihat asalnya.
    Jadi penutupannya dipasang saat menulis dan dilepas saat membaca.
    """
    from app.services import scanner as sc

    terbuka = [[0, 0], [10, 0], [10, 10]]
    tertutup = sc.tutup_cincin(terbuka)
    assert tertutup == [[0, 0], [10, 0], [10, 10], [0, 0]]
    assert sc.buka_cincin(tertutup) == terbuka
    # idempoten: menutup dua kali tidak menambah dua titik
    assert sc.tutup_cincin(tertutup) == tertutup
    assert sc.buka_cincin(terbuka) == terbuka
    # bentuk terlalu kecil dibiarkan apa adanya
    assert sc.tutup_cincin([[0, 0], [1, 1]]) == [[0, 0], [1, 1]]


def test_titik_dikurung_dan_kembaran_beruntun_dibuang(tmp_path):
    """
    Dua hal yang diukur berbeda dari Roboflow, dan keduanya berpihak pada
    Roboflow: dari 3.905 poligon nyata mereka, NOL koordinat di luar [0,1] dan
    NOL titik kembar beruntun. Keluaran kita disamakan.
    """
    from app.services import scanner as sc

    # keluar batas gambar -> dikurung
    p = sc.rapikan_titik([[-5, -5], [105, 50], [50, 105]], 100, 100)
    assert all(0 <= x <= 100 and 0 <= y <= 100 for x, y in p), p
    # kembaran beruntun -> dibuang
    p = sc.rapikan_titik([[10, 10], [10, 10], [50, 10], [50, 50]], 100, 100)
    assert p == [[10.0, 10.0], [50.0, 10.0], [50.0, 50.0]]
    # bentuk yang sudah rapi tidak diubah
    asal = [[10.0, 10.0], [50.0, 10.0], [50.0, 50.0]]
    assert sc.rapikan_titik(asal, 100, 100) == asal


def test_ekspor_tidak_pernah_menulis_koordinat_di_luar_rentang(tmp_path):
    from pathlib import Path
    import numpy as np

    items = [{"img": Path("/x/a.jpg"), "W": 100, "H": 100, "shapes": [
        {"label": "a", "type": "polygon",
         "pts": np.array([[-20, -20], [130, 10], [60, 140]], np.float32)}]}]
    baris = ex.baris_yolo(items[0], {"a": 0}, True)
    v = [float(x) for x in baris[0].split()[1:]]
    assert all(-1e-9 <= x <= 1 + 1e-9 for x in v), v


def test_penutupan_cincin_tidak_menulis_ulang_baris_yang_tidak_disunting(tmp_path):
    """
    Menambahkan penutupan cincin tidak boleh membuat berkas orang lain
    ditulis ulang. Diuji pada dataset nyata: 900 berkas label Roboflow,
    buka-simpan tanpa mengubah apa pun, byte-nya identik semua.
    """
    from app.services import scanner as sc

    tp = tmp_path / "a.txt"
    for asli in (
            # cincin tertutup (seperti Roboflow)
            "0 0.100000 0.100000 0.900000 0.100000 0.900000 0.900000 0.100000 0.100000\n",
            # cincin terbuka, dan desimalnya lebih panjang
            "0 0.1015625 0.1 0.9 0.1 0.9 0.9\n"):
        tp.write_text(asli)
        sh = sc.read_yolo(tp, 100, 100, {0: "botol"})
        bentuk = [{"label": s["label"], "shape_type": s["type"],
                   "points": s["pts_asli"]} for s in sh]
        sc.tulis_yolo(tp, bentuk, 100, 100, {"botol": 0})
        assert tp.read_text() == asli, asli


def test_split_asli_dataset_dipertahankan(tmp_path):
    """
    Dataset yang sudah terbagi (mis. ekspor Roboflow) tidak boleh dibagi ulang.

    Mengacaknya ulang memindahkan gambar yang tadinya di valid ke train,
    sehingga perbandingan dengan hasil latihan sebelumnya jadi tidak berarti —
    dan gambar yang pernah dilatih bisa muncul saat evaluasi.
    """
    from pathlib import Path
    from app.services import export as ex

    items = []
    for split, n in (("train", 6), ("valid", 2), ("val", 1), ("test", 3)):
        for i in range(n):
            items.append({"img": Path(f"/x/{split}-{i}.jpg"), "shapes": [],
                          "W": 80, "H": 60, "split": split})
    bag = ex.bagi_split(items, (0.8, 0.1, 0.1))
    assert len(bag["train"]) == 6
    assert len(bag["valid"]) == 3          # `val` digabung ke `valid`
    assert len(bag["test"]) == 3


def test_augmentasi_dari_satu_foto_tidak_terpecah_antar_split(tmp_path):
    """
    Regresi kebocoran. Ekspor Roboflow menempelkan akhiran augmentasi sesudah
    `.rf.<hash>`, sehingga satu foto bisa punya puluhan berkas. Membagi per
    nama berkas menyebarkan variasi foto yang sama ke train DAN valid — model
    lalu dinilai memakai versi lain dari gambar yang sudah dia pelajari.

    Pada dataset pengguna, cara lama memecah 54% foto asal seperti itu.
    """
    from pathlib import Path
    from collections import defaultdict
    from app.services import export as ex

    items = []
    for f in range(60):
        induk = f"foto{f}_jpg.rf.{f:032x}"
        for aug in ("", "_aug1", "_bal4_216", "_p5zoom_normal", "_swout0"):
            items.append({"img": Path(f"/x/{induk}{aug}.jpg"), "shapes": [],
                          "W": 80, "H": 60})

    bag = ex.bagi_split(items, (0.8, 0.1, 0.1))
    tersebar = defaultdict(set)
    for split, daftar in bag.items():
        for it in daftar:
            tersebar[ex.kunci_asal(it["img"].name)].add(split)

    pecah = {k: v for k, v in tersebar.items() if len(v) > 1}
    assert not pecah, f"{len(pecah)} foto asal terpecah antar split"
    assert sum(len(v) for v in bag.values()) == len(items)   # tidak ada yang hilang


def test_kunci_asal_membedakan_foto_tanpa_pola_roboflow():
    from app.services import export as ex

    # Berkas biasa: tiap berkas foto asalnya sendiri.
    assert ex.kunci_asal("a.jpg") != ex.kunci_asal("b.jpg")
    # Berkas Roboflow: augmentasinya satu kelompok.
    induk = "IMG_1_jpg.rf.0123456789abcdef0123456789abcdef"
    assert ex.kunci_asal(f"{induk}.jpg") == ex.kunci_asal(f"{induk}_aug1.jpg")
    assert ex.kunci_asal(f"{induk}_bal4_216.jpg") == ex.kunci_asal(f"{induk}.jpg")


def test_ringkasan_memberitahu_kalau_memakai_split_bawaan(tmp_path):
    from pathlib import Path
    from app.services import export as ex

    berpisah = [{"img": Path("/x/a.jpg"), "shapes": [], "W": 80, "H": 60,
                 "split": "train"}]
    polos = [{"img": Path("/x/a.jpg"), "shapes": [], "W": 80, "H": 60}]
    assert ex.ringkasan(berpisah, True)["split_bawaan"] is True
    assert ex.ringkasan(polos, True)["split_bawaan"] is False


def test_coco_area_poligon_tidak_bergantung_posisi():
    """
    Regresi atas bug AnyLabeling: abs() di dalam penjumlahan shoelace membuat
    `area` membengkak makin jauh poligon dari titik-asal, sehingga bentuk yang
    identik punya "luas" berbeda hanya karena letaknya. Kita memakai shoelace
    yang benar; uji ini menjaga sifat terpentingnya — luas tidak berubah saat
    poligon digeser.
    """
    from app.services import export as ex
    kotak = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    for dx, dy in ((0, 0), (100, 100), (500, 300), (4000, 3000)):
        geser = [(x + dx, y + dy) for x, y in kotak]
        assert ex._luas_poligon_coco(geser) == 100.0, (dx, dy)

    # Arah putaran tidak mengubah luas (abs di luar penjumlahan).
    assert ex._luas_poligon_coco(list(reversed(kotak))) == 100.0

    # Segitiga alas 10 tinggi 10 -> 50.
    assert ex._luas_poligon_coco([(0.0, 0.0), (10.0, 0.0), (0.0, 10.0)]) == 50.0

    # Konsistensi antar tipe: poligon 4 sudut dari sebuah rectangle harus
    # memberi luas yang sama dengan jalur rectangle (width*height), yang di
    # AnyLabeling justru tidak sama.
    rect = [(20.0, 20.0), (60.0, 20.0), (60.0, 55.0), (20.0, 55.0)]
    assert ex._luas_poligon_coco(rect) == 40.0 * 35.0


def test_zip_coco_dan_voc(tmp_path):
    import io, json, zipfile
    from app.services import export as ex
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    with zipfile.ZipFile(io.BytesIO(ex.zip_dataset(items, "ds", "coco"))) as z:
        isi = z.namelist()
        # nama berkas anotasi mengikuti ekspor Roboflow
        coco = [n for n in isi if n.endswith("/_annotations.coco.json")]
        assert coco and all(n.split("/")[0] in ex.SPLIT for n in coco)
        total = sum(len(json.loads(z.read(n))["annotations"]) for n in coco)
        assert total == 2
    with zipfile.ZipFile(io.BytesIO(ex.zip_dataset(items, "ds", "voc"))) as z:
        xml = [n for n in z.namelist() if n.endswith(".xml")]
        assert len(xml) == 2
        assert all(n.split("/")[0] in ex.SPLIT for n in xml)


def test_folder_split_selalu_ada_walau_kosong(tmp_path):
    """
    data.yaml menunjuk ../test/images. Pada dataset kecil split test bisa
    kebagian nol gambar, dan folder yang hilang membuat perkakas latih
    mengeluh soal path yang tidak ada.
    """
    from app.services import export as ex
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    with zipfile.ZipFile(io.BytesIO(ex.zip_yolo(items, "ds", True))) as z:
        isi = set(z.namelist())
        for split in ex.SPLIT:
            assert f"{split}/images/" in isi, split
            assert f"{split}/labels/" in isi, split


def test_baca_rasio(tmp_path):
    from app.services import export as ex
    assert ex.baca_rasio("80,10,10") == (0.8, 0.1, 0.1)
    assert ex.baca_rasio("70/20/10") == (0.7, 0.2, 0.1)
    # dinormalkan: pemakai tidak harus mengetik angka yang pas 100
    a = ex.baca_rasio("8,1,1")
    assert abs(a[0] - 0.8) < 1e-9 and abs(a[1] - 0.1) < 1e-9
    for buruk in ("", None, "abc", "50,50", "-1,1,1", "0,0,0"):
        assert ex.baca_rasio(buruk) == ex.RASIO_BAWAAN, buruk


def test_rasio_bawaan_80_10_10():
    from app.services import export as ex
    assert ex.RASIO_BAWAAN == (0.8, 0.1, 0.1)


def test_rasio_mengubah_pembagian(tmp_path):
    """Dengan cukup gambar, rasio berbeda harus memberi jumlah berbeda."""
    from app.services import export as ex
    d = tmp_path / "banyak"
    d.mkdir()
    for i in range(200):
        p = d / f"i{i:03d}.jpg"
        cv2.imwrite(str(p), np.zeros((20, 20, 3), np.uint8))
    items, _ = scanner.scan(d)
    a = ex.bagi_split(items, (0.8, 0.1, 0.1))
    b = ex.bagi_split(items, (0.5, 0.25, 0.25))
    assert len(a["train"]) > len(b["train"])
    assert sum(len(v) for v in a.values()) == 200
    assert sum(len(v) for v in b.values()) == 200
    # 80% dari 200 ~ 160, beri toleransi karena pembagian berbasis hash
    assert 140 <= len(a["train"]) <= 180


def test_ringkasan_memuat_persentase_nyata(tmp_path):
    """
    Persentase yang tercapai ditampilkan apa adanya, karena pembagian berbasis
    hash tidak selalu pas dengan rasio yang diminta.
    """
    from app.services import export as ex
    d = tmp_path / "banyak"
    d.mkdir()
    for i in range(100):
        cv2.imwrite(str(d / f"i{i:03d}.jpg"), np.zeros((20, 20, 3), np.uint8))
    items, _ = scanner.scan(d)
    r = ex.ringkasan(items, True, (0.8, 0.1, 0.1))
    assert r["rasio"] == [80, 10, 10]                 # yang diminta
    p = r["persen"]
    assert set(p) == {"train", "valid", "test"}
    assert abs(sum(p.values()) - 100.0) < 0.2         # menjumlah ke 100
    # angkanya turunan dari jumlah nyata, bukan salinan rasio
    for k in p:
        assert p[k] == round(100 * r["split"][k] / 100, 1)


def test_ringkasan_dataset_kosong_tidak_bagi_nol(tmp_path):
    from app.services import export as ex
    d = tmp_path / "kosong"
    d.mkdir()
    r = ex.ringkasan([], True)
    assert r["gambar"] == 0
    assert all(v == 0.0 for v in r["persen"].values())


def test_ringkasan_tidak_menahan_kunci_sesi_selama_menghitung(klien, lingkungan,
                                                              monkeypatch):
    """
    Regresi, dan yang paling mahal sejauh ini: seluruh server pernah membeku
    karenanya.

    sess.lock adalah threading.Lock. Menahannya melewati `await` mematikan
    server: permintaan kedua memanggil acquire() di thread event loop, thread
    itu berhenti, dan pemegang kuncinya tidak akan pernah bisa dilanjutkan
    untuk melepasnya — karena yang melanjutkannya justru event loop yang sudah
    berhenti itu. Servernya membeku di 0% CPU sampai direstart.

    Cukup dua permintaan ringkasan bertumpang untuk memicunya, misalnya karena
    kotak rasio diubah selagi hitungan pertama masih jalan. Yang diuji di sini:
    saat perhitungan berlangsung, kuncinya sudah dilepas.
    """
    from conftest import PW_PAUL, masuk
    from app.services import export
    from app.session import store

    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={lingkungan['roots'] / 'ds-alpha'}")
    sess = next(iter(store._data.values()))

    terkunci = []
    asli = export.ringkasan

    def rekam(*a, **k):
        terkunci.append(sess.lock.locked())
        return asli(*a, **k)

    monkeypatch.setattr(export, "ringkasan", rekam)
    assert klien.get("/api/ekspor/ringkasan?format=yolo-seg").json()["ok"] is True
    assert terkunci == [False], "kunci sesi masih dipegang selagi menghitung"
