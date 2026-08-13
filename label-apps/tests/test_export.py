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


def test_zip_bertata_letak_ultralytics(tmp_path):
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    data = ex.zip_yolo(items, "ds", True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        isi = set(z.namelist())
        assert {"images/g0.jpg", "images/g1.jpg",
                "labels/g0.txt", "labels/g1.txt",
                "classes.txt", "data.yaml", "RINGKASAN.txt"} <= isi
        assert z.read("classes.txt").decode() == "botol\nkaleng\n"
        y = z.read("data.yaml").decode()
        assert "nc: 2" in y and "0: botol" in y and "1: kaleng" in y
        # gambar tanpa objek tetap punya berkas label, isinya kosong
        assert z.read("labels/g1.txt").decode() == ""


def test_zip_tanpa_gambar(tmp_path):
    items, _ = scanner.scan(_dataset(tmp_path / "ds"))
    with zipfile.ZipFile(io.BytesIO(ex.zip_yolo(items, "ds", True, False))) as z:
        assert not [n for n in z.namelist() if n.startswith("images/")]
        assert "labels/g0.txt" in z.namelist()


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
        assert "annotations/instances.json" in z.namelist()
        assert "images/g0.jpg" in z.namelist()
        assert len(json.loads(z.read("annotations/instances.json"))["annotations"]) == 2
    with zipfile.ZipFile(io.BytesIO(ex.zip_dataset(items, "ds", "voc"))) as z:
        assert {"Annotations/g0.xml", "Annotations/g1.xml"} <= set(z.namelist())
