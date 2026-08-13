"""
Anotasi berbantuan SAM.

Dua mesin, satu antarmuka:

  mobilesam  — ONNX MobileSAM langsung lewat onnxruntime (bawaan, dipakai tim).
               Bobotnya Apache-2.0, diunduh dari repo ONNX vietanhdev yang juga
               dipakai AnyLabeling, jadi hasilnya sama dengan yang sudah
               divalidasi di desktop.
  osam:*     — lewat paket `osam` (MIT), mesin yang sama dipakai AnyLabeling
               untuk SAM2/EfficientSAM. Cadangan dan pembanding.

Sengaja tidak meng-import kode AnyLabeling: AnyLabeling GPLv3, sedangkan
aplikasi ini tidak ingin terikat kewajiban GPL. Kontrak ONNX-nya dibaca
langsung dari berkas modelnya, bukan dari kodenya.

Dua hal yang membuatnya terasa cepat:

1. Embedding encoder di-cache per gambar. Klik pertama pada sebuah gambar
   memakan waktu penuh; klik berikutnya hanya menjalankan decoder, puluhan
   kali lebih cepat. Tanpa cache ini setiap klik terasa berat.
2. Mask disederhanakan dengan approxPolyDP supaya poligonnya masih bisa
   disunting manusia — puluhan titik, bukan ribuan.
"""
from __future__ import annotations

import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

# ---------------------------------------------------------------- setelan

MODEL_DEFAULT = "mobilesam"

MODEL_DIIZINKAN = {
    "mobilesam",            # ONNX lokal, bawaan
    "efficientsam:10m",     # lewat osam, ~40 MB
    "efficientsam:latest",
    "sam2:tiny",            # lewat osam, batas objek lebih rapi
    "sam2:small",
}

# Ukuran sisi terpanjang yang diminta SAM. Gambar diskalakan ke ukuran ini
# sebelum masuk encoder; koordinat prompt harus diskalakan dengan faktor sama.
SAM_SIDE = 1024

# Embedding beberapa MB per gambar. Delapan gambar terakhir cukup untuk pola
# kerja "buka gambar, klik beberapa objek, lanjut gambar berikutnya".
CACHE_MAKS = 8

_KUNCI = threading.Lock()
_cache: OrderedDict[tuple, tuple] = OrderedDict()
_sesi_mobilesam = None


def dir_model() -> Path:
    """Folder berisi berkas .onnx MobileSAM."""
    v = os.environ.get("LABELAPP_SAM_DIR", "").strip()
    if v:
        return Path(v).expanduser().resolve()
    # label-apps/app/services/autolabel.py -> labeling-tools/models
    return Path(__file__).resolve().parents[3] / "models"


class TidakAdaObjek(Exception):
    """SAM tidak menemukan apa pun pada prompt itu, atau prompt tidak sah."""


@dataclass
class Usulan:
    """Satu usulan poligon untuk ditampilkan di kanvas."""

    points: list[list[float]]
    bbox: tuple[int, int, int, int]
    model: str
    dari_cache: bool


# ---------------------------------------------------------------- MobileSAM

class MobileSam:
    """
    Pembungkus ONNX MobileSAM.

    Kontrak modelnya (dibaca langsung dari berkasnya):
      encoder  input_image[H,W,3] float        -> image_embeddings[1,256,64,64]
      decoder  image_embeddings, point_coords[1,N,2], point_labels[1,N],
               mask_input[1,1,256,256], has_mask_input[1], orig_im_size[2]
               -> masks (sudah diperbesar ke ukuran gambar asli)

    Encoder menerima ukuran dinamis dan mengurus normalisasi sendiri, jadi yang
    dikirim cukup gambar RGB float mentah 0-255 yang sudah diskalakan.
    """

    def __init__(self, encoder: Path, decoder: Path):
        import onnxruntime as ort

        opsi = ort.SessionOptions()
        opsi.log_severity_level = 3          # sembunyikan peringatan bentuk
        penyedia = ort.get_available_providers()
        pilih = (["CUDAExecutionProvider", "CPUExecutionProvider"]
                 if "CUDAExecutionProvider" in penyedia else ["CPUExecutionProvider"])
        self.encoder = ort.InferenceSession(str(encoder), opsi, providers=pilih)
        self.decoder = ort.InferenceSession(str(decoder), opsi, providers=pilih)
        self.provider = self.encoder.get_providers()[0]

    def embed(self, bgr: np.ndarray):
        """
        Gambar -> (embedding, skala, (H_asli, W_asli)).

        Sisi terpanjang diskalakan ke 1024, lalu diberi padding nol sampai
        1024x1024 — preprocessing resmi SAM.

        Catatan hasil uji: encoder ekspor ini ternyata sudah mengurus padding
        sendiri, keluarannya sama dengan atau tanpa padding di sini. Padding
        tetap dilakukan supaya tidak bergantung pada perilaku internal ekspor
        ONNX yang bisa berubah antar versi.

        Koordinat prompt WAJIB diskalakan dengan `skala` yang sama (lihat
        decode). Ini sudah diuji dengan gambar sintetis: lingkaran di posisi
        yang diketahui menghasilkan galat 15 px dengan koordinat berskala,
        versus 184 px kalau koordinat asli dikirim mentah.
        """
        H, W = bgr.shape[:2]
        skala = SAM_SIDE / max(H, W)
        h, w = max(1, round(H * skala)), max(1, round(W * skala))
        kecil = cv2.resize(bgr, (w, h), interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(kecil, cv2.COLOR_BGR2RGB).astype(np.float32)
        kanvas = np.zeros((SAM_SIDE, SAM_SIDE, 3), np.float32)
        kanvas[:h, :w] = rgb
        emb = self.encoder.run(None, {"input_image": kanvas})[0]
        return emb, skala, (H, W)

    def decode(self, emb, skala, hw, points, labels) -> np.ndarray:
        """Prompt -> mask biner seukuran gambar asli."""
        H, W = hw
        pts = np.asarray(points, np.float32) * skala          # ke ruang 1024
        lbl = np.asarray(labels, np.float32)

        # Ekspor ONNX resmi SAM menuntut satu titik pengisi berlabel -1 kalau
        # prompt-nya bukan kotak. Tanpa ini mask-nya melebar tak beraturan.
        if not (lbl == 2).any():
            pts = np.concatenate([pts, np.zeros((1, 2), np.float32)], 0)
            lbl = np.concatenate([lbl, np.array([-1], np.float32)], 0)

        keluar = self.decoder.run(None, {
            "image_embeddings": emb,
            "point_coords": pts[None, ...],
            "point_labels": lbl[None, ...],
            "mask_input": np.zeros((1, 1, 256, 256), np.float32),
            "has_mask_input": np.zeros(1, np.float32),
            "orig_im_size": np.array([H, W], np.float32),
        })
        masks = keluar[0]                                     # logit, bukan 0/1
        iou = keluar[1].reshape(-1)
        m = masks.reshape(-1, *masks.shape[-2:])
        terbaik = int(np.argmax(iou)) if iou.size == m.shape[0] else 0
        return m[terbaik] > 0


def _mobilesam() -> MobileSam:
    global _sesi_mobilesam
    if _sesi_mobilesam is None:
        d = dir_model()
        enc = next(iter(sorted(d.glob("*encoder*.onnx"))), None)
        dec = next(iter(sorted(d.glob("*decoder*.onnx"))), None)
        if not enc or not dec:
            raise TidakAdaObjek(
                f"berkas MobileSAM tidak ada di {d} — unduh encoder & decoder "
                "ONNX-nya, atau pakai model 'efficientsam:10m'")
        _sesi_mobilesam = MobileSam(enc, dec)
    return _sesi_mobilesam


# ---------------------------------------------------------------- poligon

def mask_ke_poligon(mask, offset=(0, 0), epsilon_rasio: float = 0.004):
    """
    Mask biner -> poligon dalam koordinat gambar.

    offset dipakai karena kedua mesin berbeda: MobileSAM mengeluarkan mask
    seukuran gambar asli (offset 0,0), sedangkan osam mengeluarkan mask yang
    terpotong sebatas bounding box hasil prediksi sehingga harus digeser
    sebesar (xmin, ymin). Salah di sini membuat poligon mendarat di sudut
    kiri atas gambar.
    """
    m = (np.asarray(mask) > 0).astype(np.uint8)
    if m.ndim != 2 or not m.any():
        return None
    kontur, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not kontur:
        return None
    c = max(kontur, key=cv2.contourArea)
    if cv2.contourArea(c) < 4:
        return None
    approx = cv2.approxPolyDP(c, epsilon_rasio * cv2.arcLength(c, True), True)
    poly = approx.reshape(-1, 2).astype(float)
    if len(poly) < 3:
        return None
    return poly + np.asarray(offset, float)


def _bbox(poly: np.ndarray) -> tuple[int, int, int, int]:
    return (int(poly[:, 0].min()), int(poly[:, 1].min()),
            int(poly[:, 0].max()), int(poly[:, 1].max()))


# ---------------------------------------------------------------- inti

def _kunci_cache(img: Path, model: str) -> tuple:
    st = img.stat()
    return (str(img.resolve()), st.st_mtime_ns, st.st_size, model)


def _segment_mobilesam(img: Path, points, labels, eps) -> Usulan:
    kunci = _kunci_cache(img, "mobilesam")
    sesi = _mobilesam()

    if kunci in _cache:
        _cache.move_to_end(kunci)
        emb, skala, hw = _cache[kunci]
        dari_cache = True
    else:
        bgr = cv2.imread(str(img))
        if bgr is None:
            raise TidakAdaObjek("gambar tidak bisa dibaca")
        emb, skala, hw = sesi.embed(bgr)
        _cache[kunci] = (emb, skala, hw)
        while len(_cache) > CACHE_MAKS:
            _cache.popitem(last=False)
        dari_cache = False

    mask = sesi.decode(emb, skala, hw, points, labels)
    poly = mask_ke_poligon(mask, (0, 0), eps)
    if poly is None:
        raise TidakAdaObjek("mask terlalu kecil untuk dijadikan poligon")
    return Usulan(poly.tolist(), _bbox(poly), "mobilesam", dari_cache)


def _segment_osam(img: Path, points, labels, model: str, eps) -> Usulan:
    import osam.apis
    import osam.types

    kunci = _kunci_cache(img, model)
    if kunci in _cache:
        _cache.move_to_end(kunci)
        emb = _cache[kunci]
        dari_cache = True
    else:
        bgr = cv2.imread(str(img))
        if bgr is None:
            raise TidakAdaObjek("gambar tidak bisa dibaca")
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        r = osam.apis.generate(osam.types.GenerateRequest(
            model=model, image=rgb,
            prompt=osam.types.Prompt(points=np.array([[w / 2, h / 2]], float),
                                     point_labels=np.array([1]))))
        emb = r.image_embedding
        _cache[kunci] = emb
        while len(_cache) > CACHE_MAKS:
            _cache.popitem(last=False)
        dari_cache = False

    r = osam.apis.generate(osam.types.GenerateRequest(
        model=model, image_embedding=emb,
        prompt=osam.types.Prompt(points=np.array(points, float),
                                 point_labels=np.array(labels, int))))
    if not r.annotations:
        raise TidakAdaObjek("SAM tidak menemukan objek di sana")
    a = r.annotations[0]
    bb = a.bounding_box
    poly = mask_ke_poligon(a.mask, (bb.xmin, bb.ymin), eps)
    if poly is None:
        raise TidakAdaObjek("mask terlalu kecil untuk dijadikan poligon")
    return Usulan(poly.tolist(), (bb.xmin, bb.ymin, bb.xmax, bb.ymax),
                  model, dari_cache)


def _segment(img: Path, points, labels, model: str, eps: float) -> Usulan:
    if model not in MODEL_DIIZINKAN:
        raise TidakAdaObjek(f"model '{model}' tidak diizinkan")
    with _KUNCI:            # kedua mesin memakai sesi global, harus diserialkan
        if model == "mobilesam":
            return _segment_mobilesam(img, points, labels, eps)
        return _segment_osam(img, points, labels, model, eps)


# ---------------------------------------------------------------- API

def dari_kotak(img: Path, x1, y1, x2, y2, model: str = MODEL_DEFAULT,
               eps: float = 0.004) -> Usulan:
    """Prompt kotak: pengguna menarik kotak di sekitar objek."""
    x1, x2 = sorted((float(x1), float(x2)))
    y1, y2 = sorted((float(y1), float(y2)))
    if x2 - x1 < 2 or y2 - y1 < 2:
        raise TidakAdaObjek("kotaknya terlalu kecil")
    # label 2 = sudut kiri-atas kotak, 3 = sudut kanan-bawah
    return _segment(img, [[x1, y1], [x2, y2]], [2, 3], model, eps)


def dari_titik(img: Path, titik, label=None, model: str = MODEL_DEFAULT,
               eps: float = 0.004) -> Usulan:
    """
    Prompt titik: klik di atas objek.

    Titik berlabel 0 memberi tahu SAM bagian mana yang BUKAN objek — cara
    membetulkan mask yang meluber tanpa menggambar ulang, sama seperti klik
    kanan di AnyLabeling.
    """
    if not titik:
        raise TidakAdaObjek("tidak ada titik")
    label = list(label) if label else [1] * len(titik)
    if len(label) != len(titik):
        raise TidakAdaObjek("jumlah titik dan labelnya tidak sama")
    return _segment(img, titik, label, model, eps)


def info() -> dict:
    """Keadaan mesin, untuk ditampilkan di antarmuka."""
    d = dir_model()
    ada = bool(list(d.glob("*encoder*.onnx")) and list(d.glob("*decoder*.onnx")))
    return {"default": MODEL_DEFAULT, "tersedia": sorted(MODEL_DIIZINKAN),
            "mobilesam_siap": ada, "dir_model": str(d),
            "provider": _sesi_mobilesam.provider if _sesi_mobilesam else None}


def kosongkan_cache() -> None:
    with _KUNCI:
        _cache.clear()
