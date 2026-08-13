"""
Ekspor anotasi ke format latih.

Rumusnya diambil baris demi baris dari `utils/export_formats.py` AnyLabeling
(`FormatExporter.export_to_yolo`), termasuk hal-hal kecil yang menentukan
kecocokan berkas:

- Hanya `rectangle` dan `polygon` yang diekspor; tipe lain dilewati.
- Peta kelas dibuat dari label unik yang **diurutkan**, indeks mulai 0.
- Angka ditulis dengan `%.6f`.
- Mode segmentasi: rectangle dijadikan 4 sudut dengan urutan
  kiri-atas, kanan-atas, kanan-bawah, kiri-bawah.
- Mode deteksi: poligon diringkas menjadi bounding box.

Pascal VOC dan COCO juga mengikuti `export_formats.py` baris demi baris,
termasuk hal yang tampak ganjil — lihat catatan pada `_luas_poligon_coco`.

Yang belum: CreateML.
"""
from __future__ import annotations

import io
import json
import os.path as osp
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from xml.dom import minidom

from . import scanner

FORMAT = {
    "yolo-seg": "YOLO segmentation (poligon)",
    "yolo": "YOLO detection (bounding box)",
    "coco": "COCO (satu instances.json)",
    "voc": "Pascal VOC (satu .xml per gambar)",
}

# Versi yang ditulis di berkas keluaran, sama dengan AnyLabeling 0.4.36.
VERSI = "0.4.36"


def peta_kelas(items: list[dict]) -> dict[str, int]:
    """Label unik terurut -> indeks kelas, seperti label_map AnyLabeling."""
    label = {str(s["label"]).strip() for it in items for s in it["shapes"]
             if s["label"] is not None and str(s["label"]).strip()}
    return {l: i for i, l in enumerate(sorted(label))}


def _titik_rectangle(pts):
    """2 titik labelme -> 4 sudut: kiri-atas, kanan-atas, kanan-bawah, kiri-bawah."""
    (x1, y1), (x2, y2) = pts[0], pts[1]
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def baris_yolo(it: dict, peta: dict[str, int], segmentasi: bool) -> list[str]:
    """Satu gambar -> baris-baris berkas label YOLO."""
    W, H = it["W"], it["H"]
    if not W or not H:
        return []
    keluar = []
    for s in it["shapes"]:
        if s["type"] not in ("rectangle", "polygon"):
            continue
        label = None if s["label"] is None else str(s["label"]).strip()
        if label not in peta:
            continue
        k = peta[label]
        pts = [(float(x), float(y)) for x, y in s["pts"].tolist()]

        if segmentasi:
            if s["type"] == "rectangle" and len(pts) == 2:
                pts = _titik_rectangle(pts)
            if len(pts) < 3:
                continue
            angka = []
            for x, y in pts:
                angka += [x / W, y / H]
            keluar.append(f"{k} " + " ".join(f"{v:.6f}" for v in angka))
        else:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
            cx = (xmin + xmax) / (2 * W)
            cy = (ymin + ymax) / (2 * H)
            bw = (xmax - xmin) / W
            bh = (ymax - ymin) / H
            keluar.append(f"{k} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
    return keluar


# Pembagian bawaan 80:10:10. Roboflow membiarkan pemakainya menentukan sendiri
# lewat Train/Test Split, dan angka ini yang biasa dipakai di proyek ini.
RASIO_BAWAAN = (0.8, 0.1, 0.1)
SPLIT = ("train", "valid", "test")


def data_yaml(peta: dict[str, int], nama: str) -> str:
    """
    data.yaml dengan bentuk yang sama seperti ekspor Roboflow.

    Disalin dari contoh nyata (sirsak-v13/botol-kaleng-tetra-mlp-cup-1):
    path relatif berawalan `../`, `nc` lalu `names` sebagai daftar rata, dan
    tidak ada kunci `path:`. Ultralytics menerima bentuk ini apa adanya.
    """
    urut = [l for l, _ in sorted(peta.items(), key=lambda kv: kv[1])]
    nama_kelas = ", ".join(f"'{l}'" for l in urut)
    return ("train: ../train/images\n"
            "val: ../valid/images\n"
            "test: ../test/images\n"
            "\n"
            f"nc: {len(peta)}\n"
            f"names: [{nama_kelas}]\n"
            "\n"
            "labeling-tools:\n"
            f"  dataset: {nama}\n"
            "  format: YOLO segmentation\n")


def baca_rasio(teks: str | None) -> tuple[float, float, float]:
    """
    "80,10,10" -> (0.8, 0.1, 0.1). Menerima persen maupun pecahan, dan
    dinormalkan supaya jumlahnya selalu 1 — pemakai mengetik 80/10/10 atau
    70/20/10 tanpa harus pas.
    """
    if not teks:
        return RASIO_BAWAAN
    try:
        angka = [float(x) for x in str(teks).replace("/", ",").split(",")]
    except ValueError:
        return RASIO_BAWAAN
    if len(angka) != 3 or any(a < 0 for a in angka):
        return RASIO_BAWAAN
    total = sum(angka)
    if total <= 0:
        return RASIO_BAWAAN
    return (angka[0] / total, angka[1] / total, angka[2] / total)


def bagi_split(items: list[dict], rasio=RASIO_BAWAAN) -> dict[str, list[dict]]:
    """
    Bagi dataset menjadi train / valid / test.

    Pembagiannya **deterministik**, diturunkan dari nama berkas, bukan dari
    pengacak. Alasannya penting: kalau ekspor kedua memakai acak baru, gambar
    yang tadinya di train bisa pindah ke valid, dan model yang dievaluasi
    dengan gambar yang pernah dilatih akan terlihat lebih baik daripada
    kenyataannya. Dengan cara ini ekspor berapa kali pun memberi pembagian
    yang sama.
    """
    import hashlib

    batas_train = rasio[0]
    batas_valid = rasio[0] + rasio[1]
    hasil: dict[str, list[dict]] = {k: [] for k in SPLIT}
    for it in sorted(items, key=lambda x: x["img"].name):
        h = hashlib.sha1(it["img"].name.encode()).digest()
        v = int.from_bytes(h[:4], "big") / 0xFFFFFFFF
        if v < batas_train:
            hasil["train"].append(it)
        elif v < batas_valid:
            hasil["valid"].append(it)
        else:
            hasil["test"].append(it)
    return hasil


def zip_yolo(items: list[dict], nama_dataset: str, segmentasi: bool,
             sertakan_gambar: bool = True, rasio=RASIO_BAWAAN) -> bytes:
    """
    Seluruh dataset -> ZIP dengan tata letak yang **sama seperti ekspor
    Roboflow**, supaya bisa langsung dilatih tanpa dirapikan lagi:

        data.yaml
        README.txt
        train/images/  train/labels/
        valid/images/  valid/labels/
        test/images/   test/labels/

    Gambar tanpa objek tetap mendapat berkas label kosong — itu penanda contoh
    negatif yang sah di YOLO, bukan kelalaian.
    """
    peta = peta_kelas(items)
    bagian = bagi_split(items, rasio)
    buf = io.BytesIO()
    n_objek = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for split, daftar in bagian.items():
            for it in daftar:
                p: Path = it["img"]
                baris = baris_yolo(it, peta, segmentasi)
                n_objek += len(baris)
                z.writestr(f"{split}/labels/{p.stem}.txt",
                           "\n".join(baris) + ("\n" if baris else ""))
                if sertakan_gambar:
                    z.write(p, f"{split}/images/{p.name}")
        # Ketiga folder selalu dibuat, walau salah satu split kosong: data.yaml
        # menunjuk ../test/images, dan folder yang tidak ada membuat perkakas
        # latih mengeluh tentang path yang hilang. Pada dataset kecil, split
        # test memang bisa kebagian nol gambar.
        for split in SPLIT:
            for sub in ("images", "labels"):
                z.writestr(f"{split}/{sub}/", "")
        z.writestr("data.yaml", data_yaml(peta, nama_dataset))
        urut = [l for l, _ in sorted(peta.items(), key=lambda kv: kv[1])]
        z.writestr("README.txt",
                   f"{nama_dataset}\n{'=' * len(nama_dataset)}\n\n"
                   "Diekspor dari labeling-tools, pengembangan dari AnyLabeling.\n"
                   f"Format   : YOLO {'segmentation' if segmentasi else 'detection'}\n"
                   f"Gambar   : {len(items)}"
                   f"  (train {len(bagian['train'])}, valid {len(bagian['valid'])},"
                   f" test {len(bagian['test'])})\n"
                   f"Objek    : {n_objek}\n"
                   f"Kelas    : {len(peta)}  {', '.join(urut)}\n\n"
                   "Pembagian train/valid/test deterministik, diturunkan dari nama\n"
                   "berkas. Ekspor berapa kali pun memberi pembagian yang sama, jadi\n"
                   "tidak ada gambar latih yang berpindah ke validasi.\n")
    return buf.getvalue()


def ringkasan(items: list[dict], segmentasi: bool, rasio=RASIO_BAWAAN) -> dict:
    """Angka untuk ditampilkan sebelum orang menekan unduh, termasuk jumlah
    gambar per split — seperti yang Roboflow tampilkan di Train/Test Split."""
    peta = peta_kelas(items)
    bagian = bagi_split(items, rasio)
    n_objek = sum(len(baris_yolo(it, peta, segmentasi)) for it in items)
    n_kosong = sum(1 for it in items if not baris_yolo(it, peta, segmentasi))
    dilewati = sum(1 for it in items for s in it["shapes"]
                   if s["type"] not in ("rectangle", "polygon"))
    return {"gambar": len(items), "objek": n_objek, "kelas": len(peta),
            "nama_kelas": [l for l, _ in sorted(peta.items(), key=lambda kv: kv[1])],
            "tanpa_objek": n_kosong, "bentuk_dilewati": dilewati,
            "split": {k: len(v) for k, v in bagian.items()},
            "rasio": [round(r * 100) for r in rasio]}


# ---------------------------------------------------------------- Pascal VOC

def voc_xml(it: dict) -> str:
    """
    Satu gambar -> XML Pascal VOC.

    Struktur, urutan elemen, dan `toprettyxml(indent="  ")` mengikuti
    `export_to_pascal_voc`. Koordinat ditulis `str(int(...))` — dipotong, bukan
    dibulatkan, sama seperti di sana.
    """
    p: Path = it["img"]
    ann = ET.Element("annotation")
    ET.SubElement(ann, "folder").text = osp.dirname(str(p))
    ET.SubElement(ann, "filename").text = p.name
    ET.SubElement(ann, "path").text = str(p)
    ET.SubElement(ET.SubElement(ann, "source"), "database").text = "Unknown"
    size = ET.SubElement(ann, "size")
    ET.SubElement(size, "width").text = str(it["W"])
    ET.SubElement(size, "height").text = str(it["H"])
    ET.SubElement(size, "depth").text = "3"
    ET.SubElement(ann, "segmented").text = "0"

    for s in it["shapes"]:
        if s["type"] not in ("rectangle", "polygon"):
            continue
        obj = ET.SubElement(ann, "object")
        ET.SubElement(obj, "name").text = "" if s["label"] is None else str(s["label"])
        ET.SubElement(obj, "pose").text = "Unspecified"
        ET.SubElement(obj, "truncated").text = "0"
        ET.SubElement(obj, "difficult").text = "0"
        pts = s["pts"].tolist()
        xs = [q[0] for q in pts]
        ys = [q[1] for q in pts]
        bnd = ET.SubElement(obj, "bndbox")
        ET.SubElement(bnd, "xmin").text = str(int(min(xs)))
        ET.SubElement(bnd, "ymin").text = str(int(min(ys)))
        ET.SubElement(bnd, "xmax").text = str(int(max(xs)))
        ET.SubElement(bnd, "ymax").text = str(int(max(ys)))

    return minidom.parseString(ET.tostring(ann, encoding="utf-8")).toprettyxml(indent="  ")


# ---------------------------------------------------------------- COCO

def _luas_poligon_coco(pts) -> float:
    """
    Luas poligon persis seperti `export_to_coco` AnyLabeling.

    CATATAN PENTING — nilai ini bukan luas geometris. AnyLabeling menaruh abs()
    di dalam penjumlahan:

        area += 0.5 * abs(x1*y2 - x2*y1)

    sedangkan shoelace menaruhnya di luar. Akibatnya nilai membengkak makin
    jauh poligon dari titik-asal: kotak 10x10 di (100,100) menghasilkan 2100
    bukan 100, di (500,300) menghasilkan 8100. Jalur rectangle di fungsi yang
    sama memakai width*height sehingga hasilnya berbeda untuk bentuk identik.

    Tetap ditiru karena aturannya jelas: keluaran harus sama dengan desktop.
    Dampaknya terbatas pada evaluasi — pycocotools memakai `area` untuk memilah
    objek small/medium/large, jadi mAP per ukuran ikut bergeser. Pelatihan tidak
    membaca field ini.
    """
    luas = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        luas += 0.5 * abs(x1 * y2 - x2 * y1)
    return luas


def coco_dict(items: list[dict], nama_dataset: str) -> dict:
    """Seluruh dataset -> satu dict COCO. Kategori dan id mengikuti aslinya:
    label unik terurut, `id` mulai 1, `supercategory` "none"."""
    label = sorted({str(s["label"]) for it in items for s in it["shapes"]
                    if s["label"] is not None})
    kategori = [{"id": i + 1, "name": l, "supercategory": "none"}
                for i, l in enumerate(label)]
    peta = {c["name"]: c["id"] for c in kategori}

    d = {
        "info": {
            "description": f"Dataset {nama_dataset} diekspor dari labeling-tools"
                           " (pengembangan AnyLabeling)",
            "url": "", "version": VERSI, "year": 2023,
            "contributor": "labeling-tools", "date_created": "",
        },
        "licenses": [{"id": 1, "name": "Unknown", "url": ""}],
        "images": [], "annotations": [], "categories": kategori,
    }

    id_anotasi = 1
    for i, it in enumerate(items):
        id_gambar = i + 1
        d["images"].append({
            "id": id_gambar, "file_name": it["img"].name,
            "width": it["W"], "height": it["H"],
            "license": 1, "date_captured": "",
        })
        for s in it["shapes"]:
            if s["type"] not in ("rectangle", "polygon"):
                continue
            pts = [(float(x), float(y)) for x, y in s["pts"].tolist()]
            xs = [q[0] for q in pts]
            ys = [q[1] for q in pts]
            xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)

            if s["type"] == "rectangle":
                w, h = xmax - xmin, ymax - ymin
                segmentasi = [[xmin, ymin, xmax, ymin, xmax, ymax, xmin, ymax]]
                luas = w * h
                bbox = [xmin, ymin, w, h]
            else:
                segmentasi = [[k for q in pts for k in q]]
                luas = _luas_poligon_coco(pts)
                bbox = [xmin, ymin, xmax - xmin, ymax - ymin]

            d["annotations"].append({
                "id": id_anotasi, "image_id": id_gambar,
                "category_id": peta[str(s["label"])],
                "segmentation": segmentasi, "area": luas,
                "bbox": bbox, "iscrowd": 0,
            })
            id_anotasi += 1
    return d


# ---------------------------------------------------------------- arsip

def zip_dataset(items: list[dict], nama: str, format: str,
                sertakan_gambar: bool = True, rasio=RASIO_BAWAAN) -> bytes:
    """Satu pintu untuk semua format."""
    if format in ("yolo", "yolo-seg"):
        return zip_yolo(items, nama, format == "yolo-seg", sertakan_gambar, rasio)

    bagian = bagi_split(items, rasio)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for split in SPLIT:
            z.writestr(f"{split}/images/", "")
        for split, daftar in bagian.items():
            if format == "coco":
                # Nama berkas mengikuti ekspor Roboflow: _annotations.coco.json
                # di dalam folder split-nya.
                z.writestr(f"{split}/_annotations.coco.json",
                           json.dumps(coco_dict(daftar, nama), indent=2))
            elif format == "voc":
                for it in daftar:
                    z.writestr(f"{split}/{it['img'].stem}.xml", voc_xml(it))
            else:
                raise ValueError(f"format '{format}' tidak dikenal")
            if sertakan_gambar:
                for it in daftar:
                    z.write(it["img"], f"{split}/images/{it['img'].name}")
    return buf.getvalue()
