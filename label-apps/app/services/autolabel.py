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

from . import olah_gpu
from . import gpu_sinyal
from ..log import catat
from . import osam_api

log = catat("labelapp.autolabel")

# ---------------------------------------------------------------- setelan

# Default diubah ke sam2:small atas permintaan tim: batas objeknya lebih rapi
# daripada MobileSAM. Catatan: ia lewat osam dan menyita ~4 GB GPU pada pemakaian
# pertama, jadi osam harus hidup. MobileSAM tetap tersedia sebagai pilihan ringan.
MODEL_DEFAULT = "sam2:small"

MODEL_DIIZINKAN = {
    "mobilesam",            # ONNX lokal, bawaan
    "efficientsam:10m",     # lewat osam, ~40 MB
    "efficientsam:latest",
    "sam2:tiny",            # lewat osam, batas objek lebih rapi
    "sam2:small",
    # Dua yang lebih besar, DITAWARKAN tetapi bukan bawaan.
    #
    # Diukur pada 8 gambar berlabel manusia di projek coca-cola, satu klik di
    # tengah objek, dibanding kotak buatan orang:
    #
    #   sam2:tiny    IoU rata 0,97   8/8 di atas 0,7   0,21 dtk
    #   sam2:small   IoU rata 0,99   8/8 di atas 0,7   0,17 dtk
    #   sam2:large   IoU rata 0,98   8/8 di atas 0,7   0,61 dtk
    #
    # Jadi yang lebih besar TIDAK otomatis lebih baik, dan pada foto produk
    # satu objek begini small malah unggul tipis sambil tiga kali lebih
    # cepat. Keduanya tetap ditawarkan karena bentuk yang sulit (objek tipis,
    # tepi berbaur, objek saling menutup) bisa berbeda ceritanya, dan satu-
    # satunya cara tahu adalah mencobanya pada gambar yang bermasalah itu.
    "sam2:latest",          # base plus
    "sam2:large",
    # SAM 3 asli. Hanya muncul kalau osam terpasang 0.12 ke atas; lihat
    # MODEL_PERLU_OSAM_BARU di bawah dan `model_klik()`.
    #
    # Diukur pada 6 gambar berlabel manusia di projek coca-cola, batch yang
    # dilaporkan "jadi jelek banget", satu klik di tengah objek:
    #
    #   sam2:small   IoU rata 0,84   terburuk 0,32   0,30 dtk
    #   sam3:latest  IoU rata 0,94   terburuk 0,84   1,29 dtk klik pertama,
    #                                                0,01 dtk klik berikutnya
    #
    # Yang menentukan kasus terburuknya, bukan rata-ratanya: pada objek yang
    # sulit sam2:small sempat jatuh ke 0,32, SAM 3 tak pernah di bawah 0,84.
    # Harganya bobot 1,8 GB sekali unduh dan klik pertama yang lebih lama,
    # jadi ia DITAWARKAN, bukan dijadikan bawaan.
    "sam3:latest",
}

# Model yang jalur kliknya baru ada di osam 0.12. Di 0.5 SAM 3 menolak klik
# tunggal mentah-mentah ("only two point prompts (left-top, right-bottom) are
# supported"), jadi menawarkannya di sana berarti menjanjikan sesuatu yang
# pasti gagal pada klik pertama.
MODEL_PERLU_OSAM_BARU = {"sam3:latest"}

# Model yang menerima PROMPT TEKS: sebutkan nama kelasnya, lalu seluruh gambar
# dipindai sekaligus. Ini padanan dua hal di AnyLabeling yang ternyata satu
# jalur kode di sini — prompt teks SAM3 (segment_anything.py:318-352) dan tombol
# Run yang melabeli satu gambar penuh (auto_labeling.py:83-84).
#
# Keduanya perlu unduhan besar pada pemakaian pertama, dan angkanya disebutkan
# di antarmuka supaya tidak ada kejutan: osam mengunduhnya sendiri ke
# ~/.cache/osam saat pertama dipanggil.
MODEL_TEKS = {
    "yoloworld:latest": {"nama": "YOLO-World XL", "unduh_mb": 641,
                         "bentuk": "rectangle"},
    "sam3:latest": {"nama": "SAM 3", "unduh_mb": 3412, "bentuk": "polygon"},
}

# Unduhan SAM 3 bergantung versi osam, dan angkanya disebut di antarmuka, jadi
# tak boleh satu angka untuk dua kenyataan: 0.5 memakai salinan float32 (3,4
# GB), 0.12 memakai float16 di GPU, yaitu 1,8 GB untuk kelima berkas yang
# dipakai jalur gambar (encoder gambar 887 MB, encoder bahasa 772 MB, decoder
# 63 MB, decoder titik 9 MB, penyusut mask, ditambah berkas memori ~75 MB).
UNDUH_SAM3_OSAM_BARU = 1800

# Ukuran kanvas encoder. Nilai (tinggi, lebar) ini diambil apa adanya dari
# SegmentAnythingONNX AnyLabeling (`self.input_size`), bukan dipilih sendiri:
# hasil segmentasi bergantung padanya. Diuji berdampingan — dengan padding
# 1024x1024 buatan sendiri, galat pada sumbu Y mencapai 14 px; dengan nilai ini
# hasilnya sama dengan aplikasi desktop, galat 1 px.
SAM_INPUT = (684, 1024)
SAM_SIDE = 1024

# Rasio penyederhanaan poligon. 0.001 diambil dari segment_anything.py
# AnyLabeling (`epsilon = 0.001 * cv2.arcLength`). Nilai yang lebih besar
# menghasilkan poligon lebih kasar dengan titik lebih sedikit.
EPSILON_ANYLABELING = 0.001

# Embedding beberapa MB per gambar. Delapan gambar terakhir cukup untuk pola
# kerja "buka gambar, klik beberapa objek, lanjut gambar berikutnya".
CACHE_MAKS = 8

_KUNCI = threading.Lock()
_cache: OrderedDict[tuple, tuple] = OrderedDict()
# Jalur osam 0.12 tidak menyimpan embedding di sini (osam yang menyimpannya),
# hanya CATATAN gambar mana yang sudah pernah di-encode, supaya `panaskan`
# menjawab jujur. Dibatasi sama dengan cache di atas, lebih kecil daripada
# anggaran osam, jadi catatan ini tak pernah lebih optimistis dari kenyataan.
_panas: OrderedDict[tuple, bool] = OrderedDict()
_sesi_mobilesam = None

# GroundingDINO (transformers): detektor open-vocabulary teks->kotak. Recall
# tinggi (menangkap yang YOLO-World/SAM 3 lewatkan); hanya kotak, jadi SAM bisa
# merapikannya jadi mask terpisah ("Grounded-SAM"). Dimuat sekali, di-cache.
_GDINO_ID = "IDEA-Research/grounding-dino-tiny"
_KUNCI_GD = threading.Lock()
_gdino = None                 # (processor, model, device) atau None


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


@dataclass
class Temuan:
    """Satu objek yang ditemukan dari prompt teks."""

    label: str
    points: list[list[float]]
    bbox: tuple[int, int, int, int]
    skor: float
    shape_type: str


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
        # Penyedia ditentukan SAKLAR LABELAPP_OLAH, bukan sekadar "CUDA ada
        # atau tidak". Kedua venv memang sudah dipisah, jadi dalam pemakaian
        # biasa hasilnya sama — tetapi mengandalkan kebetulan itu berarti
        # memasang onnxruntime-gpu ke .venv diam-diam mengubah jalur yang
        # dipakai, padahal saklarnya jelas-jelas berkata cpu. Saklar yang
        # menentukan; ketersediaan paket cuma syarat tambahan.
        penyedia = ort.get_available_providers()
        # Selama training memegang GPU, MobileSAM pun diload di CPU supaya tak
        # ikut merebut VRAM (walau kecil, menjaga satu aturan untuk semua SAM).
        pakai_gpu = (olah_gpu.MODE == "gpu"
                     and "CUDAExecutionProvider" in penyedia
                     and not gpu_sinyal.latih_aktif())
        pilih = (["CUDAExecutionProvider", "CPUExecutionProvider"]
                 if pakai_gpu else ["CPUExecutionProvider"])
        self.encoder = ort.InferenceSession(str(encoder), opsi, providers=pilih)
        self.decoder = ort.InferenceSession(str(decoder), opsi, providers=pilih)
        self.provider = self.encoder.get_providers()[0]

    def embed(self, bgr: np.ndarray):
        """
        Gambar -> (embedding, matriks, (H_asli, W_asli)).

        Meniru SegmentAnythingONNX.encode di AnyLabeling langkah demi langkah:
        gambar di-warpAffine dengan skala min(1024/W, 684/H) ke kanvas
        684x1024, lalu dikirim mentah sebagai float32 — encoder ekspor ini
        mengurus normalisasinya sendiri.

        Bukan pilihan gaya: dengan padding 1024x1024 buatan sendiri, mask pada
        gambar uji bergeser 14 px di sumbu Y dibanding hasil desktop. Dengan
        cara ini keduanya sama.
        """
        H, W = bgr.shape[:2]
        skala = min(SAM_INPUT[1] / W, SAM_INPUT[0] / H)
        matriks = np.array([[skala, 0, 0], [0, skala, 0], [0, 0, 1]], np.float64)
        kanvas = cv2.warpAffine(bgr, matriks[:2], (SAM_INPUT[1], SAM_INPUT[0]),
                                flags=cv2.INTER_LINEAR)
        emb = self.encoder.run(None, {"input_image": kanvas.astype(np.float32)})[0]
        return emb, matriks, (H, W)

    def decode(self, emb, matriks, hw, points, labels) -> np.ndarray:
        """
        Prompt -> mask biner seukuran gambar asli.

        Koordinat prompt dipetakan dengan transform SAM resmi (skala
        1024/sisi-terpanjang), sementara `orig_im_size` diisi ukuran KANVAS,
        bukan ukuran gambar asli — persis seperti AnyLabeling. Mask yang keluar
        lalu di-warp balik dengan matriks kebalikannya.
        """
        H, W = hw
        # Koordinat prompt HARUS diskalakan dengan skala warp yang sama, yaitu
        # min(1024/W, 684/H) — diambil dari matriksnya. Memakai
        # 1024/sisi-terpanjang tampak benar dan kebetulan sama untuk gambar
        # 640x400, tetapi salah begitu tinggi gambar yang menentukan skala:
        # pada 640x488 skalanya 1.4016 bukan 1.6, dan mask hasil prompt titik
        # meluber ke hampir seluruh gambar (IoU 0.017 terhadap hasil desktop).
        pts = np.asarray(points, np.float32) * float(matriks[0][0])
        lbl = np.asarray(labels, np.float32)

        # Titik pengisi [0,0] berlabel -1 SELALU ditambahkan, termasuk untuk
        # prompt kotak. AnyLabeling melakukannya tanpa syarat di run_decoder,
        # dan kehadirannya mengubah hasil: dengan syarat "hanya kalau bukan
        # kotak", mask prompt kotak berbeda dari hasil desktop (IoU 0.949).
        pts = np.concatenate([pts, np.zeros((1, 2), np.float32)], 0)
        lbl = np.concatenate([lbl, np.array([-1], np.float32)], 0)

        keluar = self.decoder.run(None, {
            "image_embeddings": emb,
            "point_coords": pts[None, ...],
            "point_labels": lbl[None, ...],
            "mask_input": np.zeros((1, 1, 256, 256), np.float32),
            "has_mask_input": np.zeros(1, np.float32),
            "orig_im_size": np.array(SAM_INPUT, np.float32),
        })
        masks = keluar[0]
        m = masks
        while m.ndim > 2:        # ekspor ini mengeluarkan satu mask: (1,1,h,w)
            m = m[0]
        balik = np.linalg.inv(matriks)
        m = cv2.warpAffine(m.astype(np.float32), balik[:2], (W, H),
                           flags=cv2.INTER_LINEAR)
        return m > 0


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

def mask_ke_poligon(mask, offset=(0, 0), epsilon_rasio: float = EPSILON_ANYLABELING):
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
    # CHAIN_APPROX_NONE seperti AnyLabeling: semua titik kontur diambil dulu,
    # penyederhanaan diserahkan sepenuhnya ke approxPolyDP. Dengan SIMPLE,
    # segmen lurus sudah dipadatkan lebih awal sehingga hasilnya berbeda.
    kontur, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not kontur:
        return None
    # Kontur yang menutupi >90% gambar dibuang — biasanya latar yang ikut
    # tersegmentasi, bukan objek. Aturan yang sama ada di segment_anything.py.
    luas_gambar = m.shape[0] * m.shape[1]
    layak = [c for c in kontur if cv2.contourArea(c) < 0.9 * luas_gambar] or list(kontur)
    c = max(layak, key=cv2.contourArea)
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
        emb, matriks, hw = _cache[kunci]
        dari_cache = True
    else:
        bgr = cv2.imread(str(img))
        if bgr is None:
            raise TidakAdaObjek("gambar tidak bisa dibaca")
        emb, matriks, hw = sesi.embed(bgr)
        _cache[kunci] = (emb, matriks, hw)
        while len(_cache) > CACHE_MAKS:
            _cache.popitem(last=False)
        dari_cache = False

    mask = sesi.decode(emb, matriks, hw, points, labels)
    poly = mask_ke_poligon(mask, (0, 0), eps)
    if poly is None:
        raise TidakAdaObjek("mask terlalu kecil untuk dijadikan poligon")
    return Usulan(poly.tolist(), _bbox(poly), "mobilesam", dari_cache)


# ------------------------------------------------- model osam dan VRAM
#
# Model lewat osam (SAM 3, YOLO-World, EfficientSAM, SAM 2) DIPAKSA berjalan
# di CPU, bahkan ketika saklar LABELAPP_OLAH bernilai gpu. Itu bukan
# kehati-hatian berlebih — tanpa ini, prompt teks GAGAL TOTAL di mode GPU:
#
#   Failed to allocate memory for requested buffer of size 1719926784
#   (Softmax node_Softmax_968)
#
# Sebabnya muat atau tidaknya di kartu ini, bukan setelan: SAM 3 membawa
# 3,4 GB bobot (image encoder 1,8 GB + language encoder 1,6 GB) dan menuntut
# satu buffer 1,64 GB di tengah jalan. Di RTX 3060 Ti yang 8 GB, dan yang
# dipakai bersama pembuatan versi, itu tidak cukup. Diuji: gagal pada gambar
# 480x640 MAUPUN 720x1280 dengan permintaan alokasi yang sama persis — model
# ini menormalkan masukannya, jadi mengecilkan foto tidak menolong sama
# sekali. Di CPU ia berjalan sampai selesai.
#
# osam punya fallback ke CPU sendiri, tetapi hanya kalau PEMBUATAN SESI yang
# gagal. Di sini sesinya terbentuk dengan mulus dan yang gagal inferensinya,
# jadi fallback itu tidak pernah terpicu.
#
# Yang dipaksa penyedianya, bukan CUDA_VISIBLE_DEVICES: mengosongkan variabel
# itu berlaku untuk seluruh proses dan ikut mematikan torch, sehingga
# pembuatan versi kehilangan GPU-nya juga.
#
# LABELAPP_OSAM_GPU=1 memaksa SEMUA model osam ke GPU (lewati penjaga ukuran).
OSAM_GPU = (os.environ.get("LABELAPP_OSAM_GPU") or "").strip().lower() in (
    "1", "true", "ya")

# Model osam yang boleh naik GPU (gate OSAM_GPU_BEBAS per-model). Diukur ulang
# di stack sekarang (torch 2.14 cu130, onnxruntime-gpu 1.30, GPU 8 GB), SEMUA
# dengan sesi hemat memori (lihat _sesi_osam_gpu): mem_pattern mati + arena
# kSameAsRequested + workspace conv kecil, yang menurunkan puncak tanpa memperlambat.
#   sam2:tiny/small, efficientsam  -> ~2,5 GB, ~1,5 dtk/klik.
#   yoloworld:latest (teks->kotak) -> ~1,85 GB, ~2 dtk/gambar (dulu dikira tak
#     muat; ternyata muat dan jauh lebih cepat dari CPU ~4 dtk).
#   sam3:latest (teks->poligon)    -> HYBRID: image_encoder (ViT-H, berat) di
#     GPU, language_encoder + decoder di CPU. Memuat ketiganya ke GPU sekaligus
#     OOM (bobot menyita VRAM sebelum encoder memuncak), tetapi encoder sendirian
#     memuncak ~6,8 GB dan MUAT di 8 GB. Hasil: ~13 dtk/gambar di GPU vs ~35 dtk
#     di CPU. Pembagian encoder-GPU/sisa-CPU diatur _blob_boleh_gpu.
OSAM_GPU_KECIL = {"sam2:tiny", "sam2:small",
                  "efficientsam:10m", "efficientsam:latest",
                  "yoloworld:latest", "sam3:latest"}
# Sisa VRAM minimal supaya osam boleh naik GPU. Di bawah ini — mis. training
# atau pembuatan versi sedang memakai GPU — osam JATUH KE CPU: pelan, tapi tidak
# merebut memori fungsi utama itu dan tidak OOM. Default ~4,7 GB (sam2:small ~4
# GB + sedikit kepala). SAM 3 memuncak ~6,8 GB (hampir seluruh GPU), jadi hanya
# naik saat GPU nyaris kosong: butuh ~7,2 GB bebas.
OSAM_GPU_MIN_BEBAS = 4_700_000_000
# Ambang khusus per model, UNTUK osam 0.5, yang bobotnya float32.
OSAM_GPU_BEBAS = {"sam3:latest": 7_200_000_000}

# Di osam 0.12 bobotnya float16 dan tak ada lagi model yang butuh ambang
# khusus. Diukur di mesin ini, proses segar, satu model saja, di luar yang
# sudah dipakai proses lain:
#
#   sam2:small   ~2,2 GB
#   sam3:latest  ~2,8 GB   klik pertama 7,1 dtk (memuat bobot 1,8 GB), gambar
#                          baru 1,3 dtk, klik lagi di gambar yang sama 0,01 dtk
#
# Keduanya di bawah ambang umum 4,7 GB, jadi tabel ini kosong dan SAM 3 ikut
# aturan yang sama dengan model lain. Membiarkan ambang 7,2 GB di sini akan
# membuatnya praktis selalu jatuh ke CPU, dan di CPU ia terukur 67 detik per
# klik lewat jalur aplikasi: bukan sesuatu yang bisa dipakai orang.
OSAM_GPU_BEBAS_OSAM_BARU: dict[str, int] = {}

_osam_dipaksa = False
_osam_model_kini = None                # model yang sedang dimuat -> pemilih provider


def _boleh_gpu_osam(model) -> bool:
    """GPU untuk osam HANYA kalau: saklar olah=gpu, model termasuk yang kecil
    (atau OSAM_GPU dipaksa), CUDA ada, DAN VRAM cukup bebas. Selain itu CPU."""
    if olah_gpu.MODE != "gpu":
        return False
    if not OSAM_GPU and model not in OSAM_GPU_KECIL:
        return False
    # Training sedang memegang GPU -> SAM ke CPU, apa pun sisa VRAM-nya. Ini lebih
    # tegas daripada ambang VRAM semata: di awal training (muat data, sebelum
    # model naik GPU) VRAM sempat terlihat longgar, dan tanpa penjaga ini SAM bisa
    # menyelinap naik GPU lalu training OOM saat modelnya menyusul.
    if gpu_sinyal.latih_aktif():
        return False
    try:
        import torch
        if not torch.cuda.is_available():
            return False
        bebas, _ = torch.cuda.mem_get_info()
        tabel = OSAM_GPU_BEBAS_OSAM_BARU if osam_api.baru() else OSAM_GPU_BEBAS
        return bebas >= tabel.get(model, OSAM_GPU_MIN_BEBAS)
    except Exception:
        return False


def _blob_boleh_gpu(model, blob) -> bool:
    """Per-SESI: tidak semua sesi sebuah model muat di GPU bareng. SAM 3 punya
    tiga sesi; kalau ketiganya di GPU, bobotnya menyita VRAM dan encoder OOM saat
    memuncak. Maka hanya image_encoder (yang berat) di GPU; language_encoder +
    decoder (input teks/kecil) tetap CPU. Model lain: semua sesinya boleh GPU.

    Hanya berlaku untuk osam 0.5, sebab hanya di sana pembuat sesi ditambal.
    Di 0.12 bobotnya float16, ketiga sesinya muat bersama, dan perangkatnya
    dipilih per klien, bukan per sesi."""
    if model == "sam3:latest":
        return "image_encoder" in (getattr(blob, "url", "") or "")
    return True


def _sesi_osam_gpu(blob):
    """Sesi onnxruntime CUDA hemat memori untuk osam: mem_pattern mati + arena
    kSameAsRequested + workspace conv kecil menurunkan puncak VRAM (SAM 3
    encoder: ~7,7 GB -> ~6,8 GB) tanpa memperlambat. Jatuh ke CPU kalau CUDA
    gagal dibuat (mis. driver)."""
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.enable_mem_pattern = False
    cuda_opt = {"arena_extend_strategy": "kSameAsRequested",
                "cudnn_conv_use_max_workspace": "0",
                "cudnn_conv_algo_search": "HEURISTIC"}
    try:
        return ort.InferenceSession(
            blob.path, sess_options=so,
            providers=[("CUDAExecutionProvider", cuda_opt), "CPUExecutionProvider"])
    except Exception:            # noqa: BLE001 -- CUDA gagal -> CPU
        return ort.InferenceSession(blob.path, providers=["CPUExecutionProvider"])


def _siapkan_osam():
    """Pasang pemilih provider osam. Aman dipanggil berkali-kali.

    Model kecil naik GPU kalau ada ruang; yang besar (SAM 3, YOLO-World) dan
    saat GPU sesak jatuh ke CPU. Diputuskan SAAT sesi dibuat, memakai
    `_osam_model_kini` yang diset pemanggil (di dalam _KUNCI, jadi tak balapan).
    """
    global _osam_dipaksa
    if _osam_dipaksa:
        return
    if osam_api.baru():
        # osam 0.12 memilih perangkatnya lewat argumen klien, jadi tak ada yang
        # perlu ditambal. Modulnya pun tak ada lagi di sana.
        _osam_dipaksa = True
        return
    import osam.types._model as _m

    asli = _m._load_inference_session

    def _rute(blob, providers=None):
        if (_boleh_gpu_osam(_osam_model_kini)
                and _blob_boleh_gpu(_osam_model_kini, blob)):
            return _sesi_osam_gpu(blob)
        return asli(blob, providers=["CPUExecutionProvider"])

    _m._load_inference_session = _rute
    _osam_dipaksa = True


def _prompt_osam_baru(points, labels) -> dict:
    """Terjemahkan prompt gaya osam 0.5 ke bentuk 0.12.

    Di 0.5 sebuah kotak dikirim sebagai dua titik berlabel 2 dan 3 (sudut
    kiri-atas dan kanan-bawah). Di 0.12 kotak punya medannya sendiri, dan
    label titik hanya boleh 0 atau 1, jadi mengirim 2 dan 3 ke sana ditolak.
    Bentuk pemanggil di aplikasi ini tidak diubah: penerjemahannya di sini.
    """
    titik: list[tuple[float, float]] = []
    lab: list[int] = []
    sudut: dict[int, tuple[float, float]] = {}
    for (x, y), l in zip(points, labels):
        if int(l) in (2, 3):
            sudut[int(l)] = (float(x), float(y))
        else:
            titik.append((float(x), float(y)))
            lab.append(int(l))
    if (2 in sudut) != (3 in sudut):
        raise TidakAdaObjek("kotak hanya punya satu sudut")
    out: dict = {}
    if 2 in sudut:
        out["box"] = (*sudut[2], *sudut[3])
    if titik:
        out["points"] = titik
        out["point_labels"] = lab
    if not out:
        raise TidakAdaObjek("prompt kosong")
    return out


def _segment_osam_baru(img: Path, points, labels, model: str, eps) -> Usulan:
    """Jalur osam 0.12: satu panggilan, embedding diurus osam sendiri.

    Cache embedding tak lagi dipegang di sini. osam menyimpannya sendiri,
    berkunci nama model dan sha256 gambar, dengan anggaran 1 GB. Yang tetap
    dicatat hanyalah gambar mana yang SUDAH pernah di-encode, supaya
    `panaskan` bisa menjawab jujur tanpa menghitung ulang.
    """
    prompt = _prompt_osam_baru(points, labels)
    bgr = cv2.imread(str(img))
    if bgr is None:
        raise TidakAdaObjek("gambar tidak bisa dibaca")
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    kunci = _kunci_cache(img, model)
    dari_cache = kunci in _panas
    anotasi = osam_api.segmen(model, rgb, gpu=_boleh_gpu_osam(model), **prompt)
    _panas[kunci] = True
    _panas.move_to_end(kunci)
    while len(_panas) > CACHE_MAKS:
        _panas.popitem(last=False)
    if not anotasi:
        raise TidakAdaObjek("SAM tidak menemukan objek di sana")
    a = anotasi[0]
    bb = a.bounding_box
    poly = mask_ke_poligon(a.mask, (bb.xmin, bb.ymin), eps)
    if poly is None:
        raise TidakAdaObjek("mask terlalu kecil untuk dijadikan poligon")
    return Usulan(poly.tolist(), (bb.xmin, bb.ymin, bb.xmax, bb.ymax),
                  model, dari_cache)


def _segment_osam(img: Path, points, labels, model: str, eps) -> Usulan:
    if osam_api.baru():
        return _segment_osam_baru(img, points, labels, model, eps)
    global _osam_model_kini
    _osam_model_kini = model            # pemilih provider saat sesi dimuat
    _siapkan_osam()
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


def panaskan(img: Path, model: str = MODEL_DEFAULT) -> bool:
    """Hitung embedding gambar lalu simpan di cache, TANPA menghasilkan mask.

    Klik pertama pada sebuah gambar membayar satu kali jalan encoder; klik
    berikutnya memakai embedding yang sama dan selesai dalam hitungan puluhan
    milidetik. Terukur di prod pada sam2:small, gambar 640x640: klik pertama
    1,5 sampai 2,0 detik, klik berikutnya 0,03 detik.

    Selisih itu tak hilang dengan model yang lebih cepat, karena ia memang
    harga sekali encode. Yang bisa dilakukan adalah MEMBAYARNYA LEBIH AWAL,
    saat gambarnya baru tampil dan orangnya masih memandangi objek sebelum
    mengklik. Itulah gunanya fungsi ini.

    Mengembalikan True kalau embeddingnya sudah siap sesudah pemanggilan ini
    (entah baru dihitung atau memang sudah ada).
    """
    if model not in model_klik():
        raise TidakAdaObjek(f"model '{model}' tidak dikenal")
    kunci = _kunci_cache(img, model)
    sudah = _panas if osam_api.baru() else _cache
    if kunci in sudah:
        sudah.move_to_end(kunci)
        return True
    # Jalur pemanasan memakai mesin yang sama dengan segmentasi, bukan
    # menyalinnya: dua jalur yang menghitung embedding dengan cara sedikit
    # berbeda akan menghasilkan dua entri cache untuk satu gambar, dan
    # pemanasannya jadi sia-sia tanpa ada yang tahu.
    try:
        _segment(img, [[1.0, 1.0]], [1], model, EPSILON_ANYLABELING)
    except TidakAdaObjek:
        pass                     # tak menemukan objek di titik umpan: wajar
    return _kunci_cache(img, model) in (_panas if osam_api.baru() else _cache)


def _segment(img: Path, points, labels, model: str, eps: float) -> Usulan:
    if model not in model_klik():
        raise TidakAdaObjek(f"model '{model}' tidak diizinkan")
    with _KUNCI:            # kedua mesin memakai sesi global, harus diserialkan
        if model == "mobilesam":
            return _segment_mobilesam(img, points, labels, eps)
        return _segment_osam(img, points, labels, model, eps)


def bebaskan_gpu() -> bool:
    """Lepaskan SEMUA sesi SAM yang menahan VRAM (MobileSAM + model osam), lalu
    kosongkan cache CUDA. Dipakai TEPAT sebelum training lokal dimulai supaya GPU
    8 GB tidak kehabisan memori gara-gara sesi SAM yang masih residen (~4 GB untuk
    sam2:small). Aman dipanggil kapan saja: kalau tak ada yang residen, tak ada
    efek. Sesinya dimuat ULANG otomatis pada klik SAM berikutnya -- dan selama
    training berjalan, muatan ulang itu jatuh ke CPU (lihat _boleh_gpu_osam dan
    MobileSam.__init__), jadi VRAM tetap milik training."""
    global _sesi_mobilesam, _gdino
    lepas = False
    with _KUNCI_GD:                  # lepas GroundingDINO juga (menahan ~2 GB)
        if _gdino is not None:
            _gdino = None
            lepas = True
    with _KUNCI:                     # jangan lepas sesi di tengah inferensi
        if _sesi_mobilesam is not None:
            _sesi_mobilesam = None
            lepas = True
        if osam_api.baru():
            # 0.12: menutup kliennya melepas model DAN cache embeddingnya.
            # Catatan gambar yang sudah di-encode ikut dikosongkan, kalau tidak
            # `panaskan` akan mengaku siap untuk embedding yang sudah lenyap.
            if osam_api.tutup():
                lepas = True
            _panas.clear()
        else:
            try:
                import osam.apis as _a
                if getattr(_a, "running_model", None) is not None:
                    _a.running_model = None
                    lepas = True
                # Sesi NMS osam (dipakai sebagian model) juga menahan sedikit VRAM.
                if getattr(_a, "_non_maximum_suppression_inference_session", None) is not None:
                    _a._non_maximum_suppression_inference_session = None
            except Exception:        # noqa: BLE001 -- osam belum pernah dipakai
                pass
    # Melepas objek sesi -> kolektor sampah menutup arena CUDA onnxruntime; lalu
    # minta torch melepas blok cache-nya juga.
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:                # noqa: BLE001
        pass
    if lepas:
        log.info("VRAM SAM dibebaskan untuk training")
    return lepas


# ---------------------------------------------------------------- API

def dari_kotak(img: Path, x1, y1, x2, y2, model: str = MODEL_DEFAULT,
               eps: float = EPSILON_ANYLABELING) -> Usulan:
    """Prompt kotak: pengguna menarik kotak di sekitar objek."""
    x1, x2 = sorted((float(x1), float(x2)))
    y1, y2 = sorted((float(y1), float(y2)))
    if x2 - x1 < 2 or y2 - y1 < 2:
        raise TidakAdaObjek("kotaknya terlalu kecil")
    # label 2 = sudut kiri-atas kotak, 3 = sudut kanan-bawah
    return _segment(img, [[x1, y1], [x2, y2]], [2, 3], model, eps)


def dari_titik(img: Path, titik, label=None, model: str = MODEL_DEFAULT,
               eps: float = EPSILON_ANYLABELING) -> Usulan:
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


def dari_teks(img: Path, teks: list[str], model: str = "yoloworld:latest",
              ambang: float = 0.1, maks: int = 100,
              eps: float = EPSILON_ANYLABELING) -> list[Temuan]:
    """
    Sebutkan nama kelasnya, seluruh gambar dipindai sekaligus.

    Mengembalikan SEMUA objek yang cocok, masing-masing dengan nama kelas yang
    memicunya. Berbeda dari jalur prompt titik/kotak yang selalu mengembalikan
    satu objek.

    Jalur ini TIDAK memakai cache embedding: embedding yang disimpan di sana
    milik encoder SAM berprompt-geometri, sedangkan model teks memakai encoder
    lain dan bahkan bentuk keluaran yang lain. Memakai ulang cache-nya akan
    memberi hasil yang salah tanpa satu pun galat.
    """
    if model not in MODEL_TEKS:
        raise TidakAdaObjek(f"model '{model}' tidak menerima prompt teks")
    teks = [str(t).strip() for t in teks if str(t).strip()]
    if not teks:
        raise TidakAdaObjek("belum ada nama kelas yang dicari")

    global _osam_model_kini
    _siapkan_osam()

    bgr = cv2.imread(str(img))
    if bgr is None:
        raise TidakAdaObjek("gambar tidak bisa dibaca")
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    if osam_api.baru():
        with _KUNCI:        # sesi model global, harus diserialkan
            anotasi = osam_api.segmen(model, rgb, gpu=_boleh_gpu_osam(model),
                                      texts=teks, score_threshold=float(ambang),
                                      max_annotations=int(maks))
    else:
        import osam.apis
        import osam.types
        with _KUNCI:        # sesi model global, harus diserialkan
            _osam_model_kini = model    # pemilih provider saat sesi dibuat (_rute)
            anotasi = osam.apis.generate(osam.types.GenerateRequest(
                model=model, image=rgb,
                prompt=osam.types.Prompt(texts=teks, score_threshold=float(ambang),
                                         max_annotations=int(maks)))).annotations

    out: list[Temuan] = []
    for a in anotasi or []:
        bb = a.bounding_box
        if bb is None:
            continue
        kotak = (int(bb.xmin), int(bb.ymin), int(bb.xmax), int(bb.ymax))
        # Mask dipakai kalau modelnya memberi mask (SAM 3); kalau tidak, yang
        # ada hanya kotak (YOLO-World), dan itu memang bentuk keluarannya.
        titik = None
        if a.mask is not None:
            poly = mask_ke_poligon(a.mask, (kotak[0], kotak[1]), eps)
            if poly is not None:
                titik = poly.tolist()
        jenis = "polygon" if titik else "rectangle"
        if titik is None:
            titik = [[kotak[0], kotak[1]], [kotak[2], kotak[3]]]
        out.append(Temuan(label=a.text or teks[0], points=titik, bbox=kotak,
                          skor=float(a.score or 0.0), shape_type=jenis))
    if not out:
        raise TidakAdaObjek("tidak ada objek yang cocok dengan nama kelas itu")
    return out


# ---------------------------------------------------------------- GroundingDINO

def _boleh_gpu_gdino() -> bool:
    """GPU untuk GroundingDINO (~2 GB) kalau olah=gpu, CUDA ada, tak ada training,
    dan VRAM bebas cukup. Selain itu CPU (lambat tapi jalan)."""
    if olah_gpu.MODE != "gpu" or gpu_sinyal.latih_aktif():
        return False
    try:
        import torch
        if not torch.cuda.is_available():
            return False
        bebas, _ = torch.cuda.mem_get_info()
        return bebas >= 3_000_000_000
    except Exception:                # noqa: BLE001
        return False


def _muat_gdino():
    """Muat GroundingDINO sekali (cache). Dipanggil DI DALAM _KUNCI_GD."""
    global _gdino
    dev = "cuda" if _boleh_gpu_gdino() else "cpu"
    if _gdino is not None and _gdino[2] == dev:
        return _gdino
    from transformers import AutoProcessor, GroundingDinoForObjectDetection
    proc = AutoProcessor.from_pretrained(_GDINO_ID)
    model = GroundingDinoForObjectDetection.from_pretrained(_GDINO_ID).to(dev).eval()
    _gdino = (proc, model, dev)
    return _gdino


def gdino_tersedia() -> bool:
    """transformers punya GroundingDINO (paket terpasang) -> bisa ditawarkan."""
    try:
        import transformers          # noqa: F401
        from transformers import GroundingDinoForObjectDetection  # noqa: F401
        return True
    except Exception:                # noqa: BLE001
        return False


def _samakan(t: str) -> str:
    """Bentuk banding: huruf dan angka saja, huruf kecil."""
    return "".join(c for c in str(t).lower() if c.isalnum())


def cocokkan_istilah(frasa: str, istilah: list[str]) -> str:
    """Frasa balasan GroundingDINO -> SATU istilah dari prompt.

    GroundingDINO melakukan phrase grounding: untuk tiap kotak ia
    mengembalikan potongan token prompt yang cocok, bukan istilah yang kita
    minta. Pada nama kelas ber-garis-bawah, tokenizer memecahnya jadi subkata
    lalu merangkainya kembali apa adanya, sehingga satu kelas
    `kahf_skinergizing_facewash_50ml` melahirkan belasan "kelas" baru:
    `kahf _ skiner facewash _ml`, `kaf skin face`, bahkan `ka`.

    Ditulis mentah sebagai nama kelas, pecahan itu mengotori daftar kelas
    projek dan tiap satunya harus dihapus dengan tangan. Padahal tiap deteksi
    PASTI berasal dari salah satu istilah yang kita kirim, jadi yang benar
    adalah mengembalikannya ke istilah itu.

    Dikembalikan dalam ejaan ASLI yang diketik orang, bukan versi huruf kecil
    yang dikirim ke model.
    """
    if not istilah:
        return str(frasa).strip()
    f = _samakan(frasa)
    if not f:
        return istilah[0]
    from difflib import SequenceMatcher
    terbaik, nilai = istilah[0], -1.0
    for it in istilah:
        n = _samakan(it)
        if not n:
            continue
        # Awalan diutamakan: pecahan GroundingDINO hampir selalu potongan
        # DEPAN istilahnya, dan rasio kemiripan saja bisa kalah pada istilah
        # panjang yang kebetulan banyak huruf samanya.
        r = SequenceMatcher(None, f, n).ratio() + (0.25 if n.startswith(f) else 0)
        if r > nilai:
            terbaik, nilai = it, r
    return terbaik


def dari_groundingdino(img: Path, teks: list[str], ambang: float = 0.25,
                       teks_ambang: float = 0.2, maks: int = 100) -> list[Temuan]:
    """Open-vocabulary teks -> KOTAK via GroundingDINO. teks huruf kecil dipisah
    '. ' (format GroundingDINO). Kotak saja; untuk projek segmentasi, pemanggil
    merapikan tiap kotak jadi mask lewat dari_kotak (SAM)."""
    # Ejaan ASLI dipertahankan untuk nama kelas; yang dikirim ke model tetap
    # huruf kecil (format GroundingDINO). Tanpa ini, kelas "Botol PET" pulang
    # jadi "botol pet" tanpa ada yang memintanya.
    asli = [str(t).strip() for t in teks if str(t).strip()]
    teks = [t.lower() for t in asli]
    if not teks:
        raise TidakAdaObjek("belum ada nama objek yang dicari")
    bgr = cv2.imread(str(img))
    if bgr is None:
        raise TidakAdaObjek("gambar tidak bisa dibaca")
    H, W = bgr.shape[:2]
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    prompt = ". ".join(teks) + "."
    import torch
    with _KUNCI_GD:
        proc, model, dev = _muat_gdino()
        inp = proc(images=rgb, text=prompt, return_tensors="pt").to(dev)
        with torch.no_grad():
            keluar = model(**inp)
        try:
            res = proc.post_process_grounded_object_detection(
                keluar, inp["input_ids"], box_threshold=float(ambang),
                text_threshold=float(teks_ambang), target_sizes=[(H, W)])[0]
        except TypeError:            # tanda tangan transformers lebih baru
            res = proc.post_process_grounded_object_detection(
                keluar, threshold=float(ambang), text_threshold=float(teks_ambang),
                target_sizes=[(H, W)])[0]
    boxes = res["boxes"].cpu().numpy()
    skor = res["scores"].cpu().numpy()
    labs = res.get("text_labels") or res.get("labels") or [""] * len(boxes)
    out: list[Temuan] = []
    for (x1, y1, x2, y2), s, l in list(zip(boxes, skor, labs))[:int(maks)]:
        x1, y1, x2, y2 = float(x1), float(y1), float(x2), float(y2)
        out.append(Temuan(label=cocokkan_istilah(l, asli),
                          points=[[x1, y1], [x2, y2]],
                          bbox=(int(x1), int(y1), int(x2), int(y2)),
                          skor=float(s), shape_type="rectangle"))
    if not out:
        raise TidakAdaObjek("GroundingDINO tak menemukan objek")
    return out


def model_klik() -> list[str]:
    """Model yang boleh dipakai untuk prompt titik atau kotak DI MESIN INI.

    Satu sumber untuk dua hal yang harus sepakat: daftar di pemilih model dan
    penjaga di jalur segmentasi. Kalau keduanya ditulis terpisah, yang paling
    mungkin terjadi adalah antarmuka menawarkan model yang backendnya tolak.
    """
    tersedia = osam_api.baru()
    return sorted(m for m in MODEL_DIIZINKAN
                  if tersedia or m not in MODEL_PERLU_OSAM_BARU)


def info() -> dict:
    """Keadaan mesin, untuk ditampilkan di antarmuka."""
    d = dir_model()
    ada = bool(list(d.glob("*encoder*.onnx")) and list(d.glob("*decoder*.onnx")))
    return {"default": MODEL_DEFAULT, "tersedia": model_klik(),
            "mobilesam_siap": ada, "dir_model": str(d),
            "groundingdino_siap": gdino_tersedia(),
            "provider": _sesi_mobilesam.provider if _sesi_mobilesam else None,
            # Model teks dilaporkan terpisah beserta ukuran unduhannya dan
            # apakah bobotnya sudah ada, supaya antarmuka bisa mengatakan
            # biayanya SEBELUM orang menekan tombolnya.
            "teks": [{"model": m, **d2, "unduh_mb": _unduh_mb(m, d2),
                       "terunduh": _sudah_terunduh(m)}
                     for m, d2 in sorted(MODEL_TEKS.items())]}


def _unduh_mb(model: str, d: dict) -> int:
    if model == "sam3:latest" and osam_api.baru():
        return UNDUH_SAM3_OSAM_BARU
    return int(d.get("unduh_mb") or 0)


def _sudah_terunduh(model: str) -> bool:
    """
    Bobot model itu sudah ada di cache atau belum.

    Ditanyakan ke osam sendiri lewat get_modified_at(), yang mengembalikan None
    selama bobotnya belum lengkap. Sempat ditebak dengan melihat apakah folder
    cache berisi sesuatu — tebakan itu menjawab "sudah" untuk SEMUA model begitu
    satu model mana pun pernah diunduh, yaitu persis jenis klaim keliru yang
    membuat antarmuka berbohong.
    """
    if osam_api.baru():
        # 0.12 menyebutkannya langsung di daftar modelnya: `size` kosong
        # selama bobotnya belum ada di disk.
        return osam_api.terunduh(model)
    try:
        import osam.apis
        k = osam.apis.get_model_type_by_name(model)
        return k.get_modified_at() is not None
    except Exception:
        return False


def kosongkan_cache() -> None:
    with _KUNCI:
        _cache.clear()
        _panas.clear()
