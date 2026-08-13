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
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    peta = ex.peta_kelas(items)
    baris = ex.baris_yolo(items[0], peta, True)
    assert baris == [
        "0 0.100000 0.125000 0.900000 0.150000 0.950000 0.875000 0.150000 0.812500",
        # rectangle -> kiri-atas, kanan-atas, kanan-bawah, kiri-bawah
        "1 0.200000 0.250000 0.600000 0.250000 0.600000 0.687500 0.200000 0.687500",
    ]


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


def test_coco_area_poligon_mengikuti_anylabeling(tmp_path):
    """
    AnyLabeling menaruh abs() di dalam penjumlahan shoelace, sehingga `area`
    membengkak makin jauh poligon dari titik-asal. Ditiru dengan sengaja; uji
    ini memastikan perilakunya tidak berubah tanpa keputusan.
    """
    from app.services import export as ex
    kotak = [(100.0, 100.0), (110.0, 100.0), (110.0, 110.0), (100.0, 110.0)]
    assert ex._luas_poligon_coco(kotak) == 2100.0      # luas geometris 100
    di_asal = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    assert ex._luas_poligon_coco(di_asal) == 100.0     # di (0,0) kebetulan benar


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
