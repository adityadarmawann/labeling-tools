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

Yang belum: Pascal VOC, COCO, dan CreateML. Ketiganya ada di
`export_formats.py` dan belum aku baca utuh — menuliskannya dari ingatan
berisiko menghasilkan berkas yang tampak benar tapi tidak dikenali perkakas
lain, jadi lebih baik belum ada daripada salah.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from . import scanner

FORMAT = {
    "yolo-seg": "YOLO segmentation (poligon)",
    "yolo": "YOLO detection (bounding box)",
}


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
