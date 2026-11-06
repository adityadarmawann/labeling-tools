"""
Auto-label KOLABORATIF (ensemble): jalankan beberapa sumber (model terlatih
projek, model .pt diunggah, zero-shot SAM3/YOLO-World, nanti Claude) lalu
GABUNGKAN hasilnya supaya di mana satu sumber lemah yang lain menutup.

Terbukti dari uji nyata: pada projek foto produk brand-spesifik, zero-shot dan
model terlatih sama-sama presisi tinggi tapi SALING MELENGKAPI (satu jago
karton, satu jago kaleng); gabungannya menutupi jauh lebih banyak.

File ini berisi LANGKAH 1: engine merge murni (tanpa model) -- bentuk data satu
deteksi, IoU, penggabungan/dedup, dan koersi ke jenis anotasi projek. Orkestrator
job + adapter sumber ada di langkah berikutnya.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import scanner

# Urutan prioritas sumber saat dua sumber menemukan objek yang SAMA: label
# diambil dari sumber paling tepercaya untuk KELAS. Model terlatih projek tahu
# taksonomi projek, jadi labelnya menang atas zero-shot (yang cuma kata prompt).
PRIORITAS_BAWAAN = ("terlatih", "eksternal", "claude", "zeroshot")

# Ambang IoU: dua deteksi dengan tumpang tindih di atas ini dianggap objek sama.
IOU_BAWAAN = 0.6


@dataclass
class Deteksi:
    """Satu objek yang ditemukan sebuah sumber, dinormalkan lintas sumber."""
    bbox: tuple[float, float, float, float]      # (x1, y1, x2, y2) piksel
    label: str
    skor: float = 0.0
    sumber: str = ""
    pts: list | None = None                      # poligon [[x,y],...] atau None
    extra: dict = field(default_factory=dict)    # mis. shape pose mentah


def _iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def gabung(deteksi: list[Deteksi], iou_ambang: float = IOU_BAWAAN,
           prioritas: tuple = PRIORITAS_BAWAAN) -> list[Deteksi]:
    """Gabungkan deteksi dari banyak sumber:

    - Deteksi yang tumpang tindih (IoU > ambang) dianggap OBJEK SAMA -> satu.
    - Label kelompok = dari sumber prioritas tertinggi (model terlatih menang).
    - Geometri = anggota poligon prioritas tertinggi (mask SAM presisi) kalau
      ada, selain itu kotak prioritas tertinggi. Jadi kelas dari satu sumber +
      bentuk dari sumber lain BISA berpadu -- itu justru intinya.
    - Deteksi yang tak berpasangan tetap disertakan (union) -> recall naik.
    """
    pidx = {s: i for i, s in enumerate(prioritas)}
    pr = lambda d: pidx.get(d.sumber, len(prioritas))

    # Seed kelompok dari skor tertinggi supaya deteksi terbaik jadi inti.
    kelompok: list[list[Deteksi]] = []
    for d in sorted(deteksi, key=lambda x: -x.skor):
        taruh = False
        for g in kelompok:
            if any(_iou(d.bbox, m.bbox) > iou_ambang for m in g):
                g.append(d)
                taruh = True
                break
        if not taruh:
            kelompok.append([d])

    out: list[Deteksi] = []
    for g in kelompok:
        oleh_pr = sorted(g, key=pr)
        label = oleh_pr[0].label
        geom = next((m for m in oleh_pr if m.pts), oleh_pr[0])
        out.append(Deteksi(
            bbox=geom.bbox, pts=geom.pts, label=label,
            skor=max(m.skor for m in g),
            sumber="+".join(sorted({m.sumber for m in g}))))
    return out


def _kurung(x, batas):
    return min(max(float(x), 0.0), float(batas))


def ke_bentuk(deteksi: list[Deteksi], jenis: str, W: int, H: int) -> list[dict]:
    """Deteksi gabungan -> bentuk labelme sesuai JENIS projek:

    - 'poligon' -> polygon (pakai pts kalau ada; kalau cuma kotak, kotak diubah
      jadi poligon 4 titik sebagai perkiraan kasar untuk disunting manusia).
    - selain itu (kotak/kosong) -> rectangle dua titik.
    Pose ('kerangka') ditangani di jalur terpisah (bukan merge lintas sumber).
    """
    out: list[dict] = []
    poligon = str(jenis or "").strip().lower() == "poligon"
    for d in deteksi:
        x1, y1, x2, y2 = d.bbox
        if poligon:
            if d.pts and len(d.pts) >= 3:
                pts = [[_kurung(x, W), _kurung(y, H)] for x, y in d.pts]
                pts = scanner.tutup_cincin(pts)
            else:
                pts = [[_kurung(x1, W), _kurung(y1, H)],
                       [_kurung(x2, W), _kurung(y1, H)],
                       [_kurung(x2, W), _kurung(y2, H)],
                       [_kurung(x1, W), _kurung(y2, H)]]
            out.append({"label": str(d.label), "text": "", "points": pts,
                        "group_id": None, "shape_type": "polygon", "flags": {}})
        else:
            out.append({"label": str(d.label), "text": "",
                        "points": [[_kurung(x1, W), _kurung(y1, H)],
                                   [_kurung(x2, W), _kurung(y2, H)]],
                        "group_id": None, "shape_type": "rectangle", "flags": {}})
    return out
