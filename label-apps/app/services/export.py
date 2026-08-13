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


def data_yaml(peta: dict[str, int], nama: str) -> str:
    """data.yaml siap dipakai ultralytics."""
    kelas = "\n".join(f"  {i}: {l}" for l, i in sorted(peta.items(), key=lambda kv: kv[1]))
    return (f"# Dataset {nama}, diekspor dari labeling-tools\n"
            f"path: .\ntrain: images\nval: images\n\nnc: {len(peta)}\nnames:\n{kelas}\n")


def zip_yolo(items: list[dict], nama_dataset: str, segmentasi: bool,
             sertakan_gambar: bool = True) -> bytes:
    """
    Seluruh dataset -> arsip ZIP bertata letak ultralytics:

        images/<nama>.jpg
        labels/<nama>.txt
        classes.txt
        data.yaml

    Gambar tanpa objek tetap mendapat berkas label kosong — itu penanda contoh
    negatif yang sah di YOLO, bukan kelalaian.
    """
    peta = peta_kelas(items)
    buf = io.BytesIO()
    n_objek = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for it in items:
            p: Path = it["img"]
            baris = baris_yolo(it, peta, segmentasi)
            n_objek += len(baris)
            z.writestr(f"labels/{p.stem}.txt", "\n".join(baris) + ("\n" if baris else ""))
            if sertakan_gambar:
                z.write(p, f"images/{p.name}")
        urut = [l for l, _ in sorted(peta.items(), key=lambda kv: kv[1])]
        z.writestr("classes.txt", "\n".join(urut) + ("\n" if urut else ""))
        z.writestr("data.yaml", data_yaml(peta, nama_dataset))
        z.writestr("RINGKASAN.txt",
                   f"Dataset  : {nama_dataset}\n"
                   f"Format   : {'YOLO segmentation' if segmentasi else 'YOLO detection'}\n"
                   f"Gambar   : {len(items)}\n"
                   f"Objek    : {n_objek}\n"
                   f"Kelas    : {len(peta)}  {', '.join(urut)}\n")
    return buf.getvalue()


def ringkasan(items: list[dict], segmentasi: bool) -> dict:
    """Angka untuk ditampilkan sebelum orang menekan unduh."""
    peta = peta_kelas(items)
    n_objek = sum(len(baris_yolo(it, peta, segmentasi)) for it in items)
    n_kosong = sum(1 for it in items if not baris_yolo(it, peta, segmentasi))
    dilewati = sum(1 for it in items for s in it["shapes"]
                   if s["type"] not in ("rectangle", "polygon"))
    return {"gambar": len(items), "objek": n_objek, "kelas": len(peta),
            "nama_kelas": [l for l, _ in sorted(peta.items(), key=lambda kv: kv[1])],
            "tanpa_objek": n_kosong, "bentuk_dilewati": dilewati}


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
                sertakan_gambar: bool = True) -> bytes:
    """Satu pintu untuk semua format."""
    if format in ("yolo", "yolo-seg"):
        return zip_yolo(items, nama, format == "yolo-seg", sertakan_gambar)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if format == "coco":
            z.writestr("annotations/instances.json",
                       json.dumps(coco_dict(items, nama), indent=2))
        elif format == "voc":
            for it in items:
                z.writestr(f"Annotations/{it['img'].stem}.xml", voc_xml(it))
        else:
            raise ValueError(f"format '{format}' tidak dikenal")
        if sertakan_gambar:
            for it in items:
                z.write(it["img"], f"images/{it['img'].name}")
    return buf.getvalue()
