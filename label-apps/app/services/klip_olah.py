"""
Augmentasi + penyeimbang klip, lalu pembekuan versi — padanan buatversi.py
untuk projek VIDEO sub-jenis aksi (Langkah 7).

APA YANG DIKERJAKAN
-------------------
Membaca klip BERLABEL sebuah projek (klip_scan + .klip.json + daftar kelas
aksi di tugas.aksi{}), lalu menulis sebuah dataset BEKU ke `<projek>/.versi/vN/`
dengan tata letak folder-per-kelas yang dibaca ketiga backend aksi
(VideoMAE/SlowFast/PoseC3D):

    .versi/vN/train/<kelas>/<klip>.mp4     ORI + _aug_ + _bal_   (untuk melatih)
    .versi/vN/valid/<kelas>/<klip>.mp4     ORI saja              (untuk menilai)
    .versi/vN/aksi.yaml                    daftar kelas + jumlah
    .versi/vN/MANIFES.json                 asal-usul tiap klip keluaran

Diport dari modelling/aug-balance-v4.py, disusun ulang menjadi sebuah
Pekerjaan bergaya buatversi.Pekerjaan: fase berurut yang melapor kemajuan,
semaphore Giliran + batas utas dipinjam dari buatversi, dan SUMBER TIDAK PERNAH
disentuh (klip asli tetap di klip/; seluruh hasil hidup di .versi/vN/, jadi satu
versi bisa dibuang tanpa menyisakan kerusakan).

EMPAT INVARIAN SAKRAL (MEMORY + rencana C.7) — diuji di test_klip_versi.py:

  1. BELAH SEBELUM AUGMENTASI, DIKELOMPOKKAN PER VIDEO SUMBER. Klip dari satu
     video sumber tak boleh tersebar ke train DAN valid — lapangan/pemain/
     pencahayaan yang sama di kedua sisi membuat angka valid bohong (MEMORY
     "split sirsak-v13 bocor per sesi", "upload selalu ratakan split"). Karena
     augmentasi baru dibuat SESUDAH pembelahan, kebocoran turunan jadi mustahil
     secara struktur, bukan bergantung penguraian nama berkas.

  2. VALID = KLIP ASLI SAJA. Nol klip _aug_/_bal_ di valid/. Menilai model pada
     klip yang sudah di-flip/digelapkan itu mengukur hal yang salah. Diverifikasi
     di kode (fase tutup, _verifikasi_valid_bersih) DAN di uji.

  3. PULIHKAN PORSI KELAS NEGATIF. Kalau projek punya kelas negatif eksplisit
     (tugas.aksi{}.negatif), penyeimbangan kelas lain MENGENCERKAN porsinya;
     fase neg memulihkannya ke porsi asalnya (MEMORY "fase 7 porsi negatif").
     Label KOSONG = belum dilabeli, BUKAN negatif — negatif adalah kelas
     eksplisit (MEMORY "sampel negatif label kosong").

  4. TAK PERNAH CROP; LETTERBOX. Standardisasi & semua varian geometri
     mengecilkan + memberi bingkai hitam, tak pernah memotong piksel —
     konsisten dengan klip.potong dan buatversi.

CATATAN MODE WARNA (MEMORY "mode warna BENTUK vs WARNA"): varian hue/kecerahan
dijaga MODEST dan daftar-hitam-kecerahan mencegah menggelapkan klip yang sudah
gelap. Backbone aksi (VideoMAE/SlowFast) dilatih-awal pada Kinetics, jadi
augmentasi temporal/spasial baku aman; perubahan warna sengaja ditahan kecil.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import threading
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from ..log import catat
from ..security import safe_slug
from . import klip, versi
# Dipinjam apa adanya: tak ada gunanya menulis ulang semaphore pembatas
# pekerjaan serentak dan pembatas utas yang sudah dikalibrasi di buatversi.
from .buatversi import Dibatalkan, Giliran, batasi_utas

log = catat("labelapp.klip_olah")

SPLIT = ("train", "valid")

# Fase + nama tampilnya pada bilah kemajuan. Angkanya hanya menjaga bilah tak
# melompat, bukan janji waktu — persis buatversi.FASE.
FASE = [
    ("pra",   "Menyiapkan klip berlabel"),
    ("split", "Membelah per video sumber & menstandarkan"),
    ("aug",   "Augmentasi (train)"),
    ("kelas", "Menyeimbangkan jumlah kelas (train)"),
    ("neg",   "Memulihkan porsi kelas negatif (train)"),
    ("tutup", "Menulis manifes & verifikasi"),
]

# Cap default: jumlah akhir sebuah kelas di train tak boleh melebihi 3x median
# (dan 3x jumlah ASLI kelas itu sendiri) — mencegah kelas kecil digelembungkan
# jadi tumpukan near-duplicate. BALANCE_MAX_RATIO di aug-balance-v4.
CAP_BAWAAN = 3.0
VARIAN_BAWAAN = 2          # salinan augmentasi per klip di train (Fase 1 v4)
RASIO_VAL_BAWAAN = 0.20    # 80:20 dari jumlah KLIP, dibelah per video sumber


# ============================================================
# RESEP
# ============================================================
def resep_sah(resep: dict | None) -> dict:
    """Bersihkan + lengkapi parameter aug/balance ke bentuk kanonik yang aman.

    Nilai dari form tak dipercaya: dijepit ke rentang wajar supaya satu angka
    keliru tak membuat ffmpeg meledak atau versi membengkak tak terhingga.
    """
    r = resep if isinstance(resep, dict) else {}

    def _num(x, bawaan):
        try:
            return float(x)
        except (TypeError, ValueError):
            return float(bawaan)

    varian = int(_num(r.get("varian"), VARIAN_BAWAAN))
    varian = min(max(varian, 0), 8)
    rasio = _num(r.get("rasio_val"), RASIO_VAL_BAWAAN)
    rasio = min(max(rasio, 0.05), 0.5)
    cap = _num(r.get("cap"), CAP_BAWAAN)
    cap = min(max(cap, 1.0), 10.0)

    fps = int(_num(r.get("fps"), 25))
    fps = min(max(fps, 1), 120)
    uk = r.get("ukuran") if isinstance(r.get("ukuran"), (list, tuple)) else []
    try:
        W, H = int(uk[0]), int(uk[1])
    except (IndexError, TypeError, ValueError):
        W, H = 224, 224
    # libx264 (yuv420p) menuntut dimensi genap.
    W = max(16, W + (W % 2))
    H = max(16, H + (H % 2))

    sasaran = str(r.get("sasaran") or "median").strip().lower()
    if sasaran not in ("median", "max", "mean"):
        sasaran = "median"

    return {"varian": varian, "rasio_val": round(rasio, 4), "cap": round(cap, 2),
            "fps": fps, "ukuran": [W, H], "sasaran": sasaran}


# ============================================================
# KATALOG VARIAN AUGMENTASI  (diport dari aug-balance-v4.AUG_VARIANTS)
# ============================================================
# ATURAN MUTLAK: KLIP TIDAK PERNAH DIPOTONG. Semua geometri bekerja dengan
# mengecilkan + memberi bingkai (pad), bukan crop — isi frame 100% utuh selalu.
_SCALE_SOFT = 0.92
_SCALE_MILD = 0.85
_SCALE_SHIFT = 0.88

# Filter ffmpeg per varian "sederhana" (yang hanya fotometrik / flip / temporal
# ringan). Varian geometri (zoom_out_*, pad_shift_*, speed_*) dibangun khusus di
# _vf_varian karena punya grafik scale/pad/setpts sendiri.
_FILTER_SEDERHANA = {
    "hflip":          ["hflip"],
    "brighten":       ["eq=brightness=0.15:contrast=1.05"],
    "darken":         ["eq=brightness=-0.15:contrast=0.95"],
    "brighten_hflip": ["hflip", "eq=brightness=0.12:contrast=1.05"],
    "darken_hflip":   ["hflip", "eq=brightness=-0.12:contrast=0.95"],
    "contrast_high":  ["eq=contrast=1.3:brightness=0.05"],
    "contrast_low":   ["eq=contrast=0.75:brightness=-0.05"],
    "blur_slight":    ["boxblur=2:1"],
    "sharpen":        ["unsharp=5:5:1.0:5:5:0.0"],
    "hue_shift":      ["hue=h=15:s=1.1"],
    "hue_shift_neg":  ["hue=h=-15:s=0.9"],
    "noise":          ["noise=alls=8:allf=t"],
}

# Varian geometri/temporal — tak punya entri filter karena dibangun khusus.
_VARIAN_KHUSUS = (
    "zoom_out_soft", "zoom_out_mild", "zoom_out_hflip", "zoom_out_dark",
    "zoom_out_bright", "pad_shift_left", "pad_shift_right", "pad_shift_up",
    "pad_shift_down", "speed_up", "speed_down",
)

# Urutan prioritas — yang paling "natural" & paling mirip inferensi lebih dulu.
AUG_PRIORITY = [
    "hflip", "zoom_out_soft", "brighten", "darken", "pad_shift_left",
    "pad_shift_right", "zoom_out_mild", "brighten_hflip", "darken_hflip",
    "pad_shift_up", "pad_shift_down", "zoom_out_hflip", "zoom_out_dark",
    "zoom_out_bright", "speed_up", "speed_down", "contrast_high",
    "contrast_low", "hue_shift", "hue_shift_neg", "blur_slight", "sharpen",
    "noise",
]

# Varian yang HARAM untuk klip gelap/terang — menggelapkan klip yang sudah gelap
# (atau menerangkan yang sudah terang) menghapus sinyalnya, bukan menambah variasi.
_DAFTAR_HITAM = {
    "dark":   ["darken", "darken_hflip", "zoom_out_dark", "contrast_low"],
    "bright": ["brighten", "brighten_hflip", "zoom_out_bright", "contrast_high"],
    "normal": [],
}


def katalog_json() -> dict:
    """Katalog varian untuk UI (padanan olah.katalog_json untuk klip).

    Dibaca sekali saat form dibuka; cuma keterangan, bukan keputusan."""
    return {"aug": {n: {"urut": i} for i, n in enumerate(AUG_PRIORITY)},
            "prioritas": list(AUG_PRIORITY)}


# ============================================================
# FILTER FFMPEG
# ============================================================
def _vf_standar(W: int, H: int) -> str:
    """Letterbox ke WxH: kecilkan agar muat lalu bingkai hitam. TAK ADA crop."""
    return (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black")


def _genap(n: int) -> int:
    return n + (n % 2)


def _vf_zoom_keluar(skala: float, W: int, H: int, extra: str | None = None) -> str:
    """Kecilkan isi `skala`x lalu pad ke tengah — isi 100% utuh, bukan crop."""
    skala = max(0.75, min(1.0, skala))
    sw, sh = _genap(int(W * skala)), _genap(int(H * skala))
    px, py = (W - sw) // 2, (H - sh) // 2
    bagian = [f"scale={sw}:{sh}", f"pad={W}:{H}:{px}:{py}:color=black"]
    if extra:
        bagian.append(extra)
    return ",".join(bagian)


def _vf_pad_geser(fx: float, fy: float, W: int, H: int,
                  skala: float = _SCALE_SHIFT) -> str:
    """Geser posisi isi di dalam frame TANPA memotong — meniru kotak detektor
    yang meleset. fx/fy ∈ [0,1]: 0=mepet kiri/atas, .5=tengah, 1=kanan/bawah."""
    skala = max(0.75, min(1.0, skala))
    fx, fy = max(0.0, min(1.0, fx)), max(0.0, min(1.0, fy))
    sw, sh = _genap(int(W * skala)), _genap(int(H * skala))
    px, py = int((W - sw) * fx), int((H - sh) * fy)
    return f"scale={sw}:{sh},pad={W}:{H}:{px}:{py}:color=black"


def _vf_varian(nama: str, W: int, H: int) -> str | None:
    """Rangkai filter -vf untuk satu varian. None kalau namanya tak dikenal.

    Varian temporal (speed_*) memakai setpts — durasinya berubah sendiri; kita
    SENGAJA tak memaksa -t, jadi tak ada klip yang terpotong (aturan "tak pernah
    memotong", sejalan speed_down di aug-balance-v4 yang klip 1.18s-nya dibiarkan
    utuh karena loader training mengiris jendelanya sendiri)."""
    base = _vf_standar(W, H)
    if nama == "speed_up":
        return f"setpts=0.8*PTS,{base}"
    if nama == "speed_down":
        return f"setpts=1.18*PTS,{base}"
    if nama == "zoom_out_soft":
        return _vf_zoom_keluar(_SCALE_SOFT, W, H)
    if nama == "zoom_out_mild":
        return _vf_zoom_keluar(_SCALE_MILD, W, H)
    if nama == "zoom_out_hflip":
        return _vf_zoom_keluar(0.88, W, H) + ",hflip"
    if nama == "zoom_out_dark":
        return _vf_zoom_keluar(0.88, W, H, "eq=brightness=-0.15:contrast=0.95")
    if nama == "zoom_out_bright":
        return _vf_zoom_keluar(0.88, W, H, "eq=brightness=0.12:contrast=1.05")
    if nama == "pad_shift_left":
        return _vf_pad_geser(0.0, 0.5, W, H)
    if nama == "pad_shift_right":
        return _vf_pad_geser(1.0, 0.5, W, H)
    if nama == "pad_shift_up":
        return _vf_pad_geser(0.5, 0.0, W, H)
    if nama == "pad_shift_down":
        return _vf_pad_geser(0.5, 1.0, W, H)
    filt = _FILTER_SEDERHANA.get(nama)
    if not filt:
        return None
    return ",".join(filt) + "," + base


def _jalankan(cmd: list[str], timeout: int = 120) -> bool:
    """Jalankan ffmpeg; True kalau keluar 0. Senyap — pemanggil yang melapor."""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return r.returncode == 0
    except Exception:                             # noqa: BLE001
        return False


# ============================================================
# IDENTITAS VIDEO SUMBER  (diport dari aug-balance-v4.source_key/video_token)
# ============================================================
_SEG_SUFFIX = re.compile(r"_t?\d+(_s\d+)?$")       # penanda potongan: _t0485 / _0160_s01


def source_key(stem: str) -> str:
    """Identitas VIDEO SUMBER dari nama klip, tahan terhadap penanda potongan
    dan suffix _aug_/_bal_. Klip dari video yang sama -> kunci yang sama."""
    for s in ("_aug_", "_bal_"):
        if s in stem:
            stem = stem[:stem.index(s)]
    stem = _SEG_SUFFIX.sub("", stem)
    return stem or "src"


def video_token(key: str) -> str:
    """Token ringkas TANPA '_' untuk kunci kanonik. ID 11-karakter bersih (YouTube)
    dipertahankan; sisanya jadi hash md5 deterministik -> sumber sama = token sama."""
    if len(key) == 11 and "_" not in key:
        return key
    return "v" + hashlib.md5(key.encode()).hexdigest()[:10]


# ============================================================
# MATEMATIKA MURNI (tanpa ffmpeg — diuji langsung di CI tanpa ffmpeg)
# ============================================================
def bagi_split(records: list[dict], rasio_val: float, seed: int = 42) -> dict:
    """Peta {rel -> "train"|"valid"}, DIKELOMPOKKAN PER VIDEO SUMBER.

    `records` tiap elemen minimal punya `rel` (kunci klip) dan `srcid` (id video
    sumber). Satu video sumber = satu grup utuh, LINTAS kelas: semua klipnya
    pergi ke sisi yang sama. Inilah penjamin anti-bocor invarian #1 — dan karena
    augmentasi baru dibuat sesudah ini, turunan sebuah klip tak mungkin nyasar
    ke valid sementara induknya di train.

    Deterministik (seed tetap): dua build dari resep sama memberi pembagian sama,
    jadi angka valid bisa dibandingkan antar-run.
    """
    per_video: dict[str, list[str]] = defaultdict(list)
    for r in records:
        token = video_token(source_key(Path(r["rel"]).stem)) if not r.get("srcid") \
            else video_token(r["srcid"])
        per_video[token].append(r["rel"])

    total = sum(len(v) for v in per_video.values())
    if total == 0:
        return {}
    sasaran = total * rasio_val

    rng = random.Random(seed)
    vids = sorted(per_video)                       # urut dulu -> deterministik
    rng.shuffle(vids)

    val_vids: set[str] = set()
    n_val = 0
    for t in vids:
        if n_val >= sasaran:
            break
        sz = len(per_video[t])
        # Lewati video yang membuat val melompat jauh melewati sasaran (1.25x).
        if n_val > 0 and n_val + sz > sasaran * 1.25:
            continue
        val_vids.add(t)
        n_val += sz
    # Jaminan val tak pernah kosong (dataset kecil / satu video besar).
    if not val_vids and vids:
        val_vids.add(min(vids, key=lambda t: len(per_video[t])))

    peta: dict[str, str] = {}
    for t, rels in per_video.items():
        s = "valid" if t in val_vids else "train"
        for rel in rels:
            peta[rel] = s
    return peta


def rencana_kelas(hitung: dict[str, int], asli: dict[str, int], cap: float,
                  sasaran: str = "median") -> tuple[dict[str, int], int]:
    """({kelas -> jumlah yang perlu DITAMBAH}, sasaran). Murni, tanpa berkas.

    `hitung` = jumlah train SAAT INI per kelas (sesudah augmentasi), `asli` =
    jumlah klip ASLI per kelas (sumber balancing; augmentasi hasil augmentasi
    menumpuk distorsi). Sasaran = median (atau max/mean). Kekurangan ditambal,
    TAPI dijepit dua lapis supaya invarian cap (#“tak lebih 3x”) tak terlanggar:

        jumlah akhir kelas <= cap * median      (tes "tak melebihi 3x median")
        jumlah akhir kelas <= cap * asli[kelas] (cegah kelas kecil membengkak —
                                                 koreksi bug aug-balance-v4 yang
                                                 keliru mengizinkan 4x asli)
    """
    nilai = [v for v in hitung.values() if v > 0]
    if not nilai:
        return {}, 0
    if sasaran == "max":
        target = max(nilai)
    elif sasaran == "mean":
        target = int(round(sum(nilai) / len(nilai)))
    else:
        target = int(np.median(nilai))
    atap_median = int(cap * target)

    rencana: dict[str, int] = {}
    for kelas, n in hitung.items():
        butuh = target - n
        if butuh <= 0:
            continue
        o = asli.get(kelas, 0)
        if o <= 0:
            continue
        atap = min(atap_median, int(cap * o))
        boleh = max(0, atap - n)
        butuh = min(butuh, boleh)
        if butuh > 0:
            rencana[kelas] = butuh
    return rencana, target


def sasaran_negatif(hitung_train_total: int, neg_kini: int,
                    porsi_asal: float, cap_atap: int) -> int:
    """Berapa klip negatif yang perlu DITAMBAH agar porsinya pulih ke `porsi_asal`.

    Dijepit `cap_atap` (= cap * median) supaya pemulihan tak melanggar invarian
    cap. 0 kalau porsinya sudah >= asal. Murni, diuji lewat build bila ffmpeg ada.
    """
    if porsi_asal <= 0 or porsi_asal >= 0.999:
        return 0
    pos = hitung_train_total - neg_kini
    neg_sasaran = int(round(porsi_asal * pos / max(1 - porsi_asal, 1e-9)))
    neg_sasaran = min(neg_sasaran, cap_atap)
    return max(0, neg_sasaran - neg_kini)


# ============================================================
# KEMAJUAN  (pola sama dengan buatversi; namespace TERPISAH supaya build klip
# dan build gambar oleh akun yang sama tak saling menimpa kemajuan)
# ============================================================
_maju: dict[str, dict] = {}
_kunci_maju = threading.Lock()
_batal_minta: set[str] = set()
_kunci_batal = threading.Lock()


def catat_maju(kunci: str, **nilai) -> None:
    with _kunci_maju:
        d = _maju.setdefault(kunci, {})
        d.update(nilai)
        d["saat"] = time.time()


def kemajuan(kunci: str) -> dict:
    with _kunci_maju:
        return dict(_maju.get(kunci) or {})


def bersihkan_maju(kunci: str) -> None:
    with _kunci_maju:
        _maju.pop(kunci, None)


def minta_batal(kunci: str) -> None:
    with _kunci_batal:
        _batal_minta.add(kunci)


def _batal_diset(kunci: str) -> bool:
    with _kunci_batal:
        return kunci in _batal_minta


def _lupakan_batal(kunci: str) -> None:
    with _kunci_batal:
        _batal_minta.discard(kunci)


# ============================================================
# BERKAS
# ============================================================
def dir_versi(ds: Path, nomor: int) -> Path:
    return Path(ds) / ".versi" / f"v{nomor}"


def buang_hasil(ds: Path, nomor: int) -> None:
    """Buang berkas hasil sebuah versi klip. Sumber (klip/) tak pernah disentuh."""
    shutil.rmtree(dir_versi(ds, nomor), ignore_errors=True)


# ============================================================
# PEKERJAAN
# ============================================================
class Pekerjaan:
    """Satu pembuatan versi klip. Seluruh keadaan dipegang instance ini — tak
    ada global yang dimutasi, jadi dua pekerjaan boleh jalan bersamaan tanpa
    saling merusak keacakannya (persis buatversi.Pekerjaan)."""

    def __init__(self, ds: Path, nomor: int, items: list[dict], aksi: dict,
                 resep: dict, *, kunci: str, seed: int = 42, batal=None,
                 oleh: str = ""):
        self.ds = Path(ds)
        self.nomor = nomor
        self.aksi = aksi or {}
        self.resep = resep_sah(resep)
        self.kunci = kunci
        self.oleh = oleh
        self.rng = random.Random(seed)
        self._batal = batal or (lambda: False)
        self.dirv = dir_versi(ds, nomor)

        self.kelas = list(self.aksi.get("kelas") or [])
        self.merge = dict(self.aksi.get("merge") or {})
        self.negatif = str(self.aksi.get("negatif") or "")
        self.W, self.H = self.resep["ukuran"]
        self.fps = self.resep["fps"]

        # Biner ffmpeg + encoder dipilih SEKALI: memanggil `-encoders` tiap klip
        # itu mahal, dan binernya tak berubah selama build. Melempar KlipTolak
        # kalau tak ada encoder H.264/MPEG-4 — jauh lebih baik daripada tiap
        # potong gagal senyap (jebakan ffmpeg-conda-tanpa-libx264).
        self.ffmpeg, self.ffprobe, self.enc, self.opts = klip.detect_ffmpeg()

        # Diisi fase-fase. `train_asli` = {kelas -> [Path klip asli train]},
        # sumber augmentasi & balancing (keduanya mengaugmentasi ASLI, bukan
        # hasil augmentasi). `peta_split` = {rel -> split}.
        self.records: list[dict] = []
        self.peta_split: dict[str, str] = {}
        self.train_asli: dict[str, list[Path]] = defaultdict(list)
        self.asli_hitung: dict[str, int] = defaultdict(int)
        self.manifes: list[dict] = []
        self._t0 = time.time()
        # Hanya item BERLABEL yang ikut; label dipetakan lewat merge aksi{}.
        for it in items:
            lab = self._label_akhir(it.get("label"))
            if not lab:
                continue
            self.records.append({"klip": Path(it["klip"]), "rel": it["rel"],
                                  "label": lab, "srcid": it.get("srcid") or ""})

    # ---------------------------------------------------------- pembantu
    def _label_akhir(self, label) -> str:
        """Nama kelas sesudah merge aksi{}. "" kalau tak berlabel / bukan kelas sah."""
        lab = " ".join(str(label or "").split())
        if not lab:
            return ""
        lab = self.merge.get(lab, lab)           # terapkan peta merge
        return lab if lab in self.kelas else ""

    def cek(self):
        if self._batal():
            raise Dibatalkan()

    def maju(self, fase: str, n: int = 0, total: int = 0, **sisa):
        i = [k for k, _ in FASE].index(fase)
        dalam = (n / total) if total else 0.0
        persen = round((i + min(dalam, 1.0)) / len(FASE) * 100, 1)
        catat_maju(self.kunci, jalan=True, fase=fase, fase_nama=dict(FASE)[fase],
                   fase_ke=i + 1, fase_dari=len(FASE), n=n, total=total,
                   persen=persen, nomor=self.nomor,
                   detik=round(time.time() - self._t0, 1), **sisa)

    def _dir_kelas(self, split: str, kelas: str) -> Path:
        d = self.dirv / split / safe_slug(kelas)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _nama_bebas(self, folder: Path, dasar: str) -> Path:
        """`<dasar>.mp4`, dijamin tak bentrok di folder (akhiran bertambah)."""
        p = folder / f"{dasar}.mp4"
        k = 2
        while p.exists():
            p = folder / f"{dasar}-{k}.mp4"
            k += 1
        return p

    def _standar(self, src: Path, dst: Path) -> bool:
        """Standardisasi fps + ukuran (letterbox, tak crop). Durasi DIBIARKAN
        apa adanya — memotong durasi berisiko membuang aksi di ujung klip."""
        vf = _vf_standar(self.W, self.H)
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(src),
               "-r", str(self.fps), "-vf", vf, "-c:v", self.enc, *self.opts,
               "-pix_fmt", "yuv420p", "-an", "-y", str(dst)]
        return _jalankan(cmd) and dst.is_file()

    def _augment(self, src: Path, dst: Path, varian: str) -> bool:
        vf = _vf_varian(varian, self.W, self.H)
        if vf is None:
            return False
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(src),
               "-r", str(self.fps), "-vf", vf, "-c:v", self.enc, *self.opts,
               "-pix_fmt", "yuv420p", "-an", "-y", str(dst)]
        return _jalankan(cmd) and dst.is_file()

    def _kategori_terang(self, src: Path) -> str:
        """"dark"/"normal"/"bright" dari satu frame tengah (32x32 gray mean).
        Dipakai daftar hitam supaya klip gelap tak digelapkan lagi."""
        try:
            info = klip.probe(src, self.ffprobe)
            dur = info.get("duration") or 1.0
            proc = subprocess.run(
                [self.ffmpeg, "-ss", str(dur / 2.0), "-i", str(src),
                 "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray",
                 "-s", "32x32", "-loglevel", "quiet", "pipe:1"],
                capture_output=True, timeout=10)
            if proc.returncode != 0 or len(proc.stdout) < 32 * 32:
                return "normal"
            mean = float(np.frombuffer(proc.stdout[:32 * 32], np.uint8).mean())
        except Exception:                         # noqa: BLE001
            return "normal"
        if mean < 50:
            return "dark"
        if mean > 180:
            return "bright"
        return "normal"

    def _varian_aman(self, src: Path) -> list[str]:
        hitam = _DAFTAR_HITAM.get(self._kategori_terang(src), [])
        return [v for v in AUG_PRIORITY if v not in hitam]

    def _catat(self, dst: Path, split: str, kelas: str, asal: str, sumber: str):
        rel = dst.relative_to(self.dirv).as_posix()
        self.manifes.append({"berkas": rel, "split": split, "kelas": kelas,
                             "asal": asal, "sumber": sumber})

    # ------------------------------------------------------------- Fase pra
    def fase_pra(self):
        """Kumpulkan klip berlabel + hitung token video sumber. Cepat (tanpa
        ffmpeg) — tugas beratnya menstandarkan, dikerjakan fase split."""
        self.maju("pra", 0, len(self.records))
        # srcid kosong -> turunkan dari nama klip (klip.potong menamai
        # <slug>_<srcid>_tNNNN), supaya pengelompokan per sumber tetap jalan.
        for r in self.records:
            if not r["srcid"]:
                r["srcid"] = source_key(r["klip"].stem)
        self.maju("pra", len(self.records), len(self.records),
                  klip=len(self.records))
        return len(self.records)

    # ------------------------------------------------------------- Fase split
    def fase_split(self):
        """Belah train/valid PER VIDEO SUMBER (invarian #1), lalu standardisasi
        + tulis tiap klip ASLI ke {split}/<kelas>/. Originals ditulis ke KEDUA
        sisi di sini; augmentasi/balancing sesudahnya HANYA menyentuh train —
        itulah yang membuat valid 100% asli (invarian #2)."""
        self.peta_split = bagi_split(self.records, self.resep["rasio_val"],
                                     seed=42)
        total = len(self.records)
        # Nama kanonik: <kelas>_<token>_<urut> — token diwarisi varian aug/bal,
        # jadi asal-usul tiap klip tetap terbaca dari namanya.
        urut: dict[tuple[str, str], int] = defaultdict(int)
        jadi = gagal = 0
        for i, r in enumerate(self.records):
            self.cek()
            if i % 5 == 0:
                self.maju("split", i, total, jadi=jadi, gagal=gagal)
            split = self.peta_split.get(r["rel"], "train")
            kelas = r["label"]
            token = video_token(r["srcid"])
            urut[(kelas, token)] += 1
            dasar = f"{safe_slug(kelas)}_{token}_{urut[(kelas, token)]:04d}"
            dst = self._nama_bebas(self._dir_kelas(split, kelas), dasar)
            if not self._standar(r["klip"], dst):
                gagal += 1
                continue
            self._catat(dst, split, kelas, "asli", r["rel"])
            jadi += 1
            if split == "train":
                self.train_asli[kelas].append(dst)
                self.asli_hitung[kelas] += 1
        if jadi == 0:
            raise KlipOlahTolak(
                "tak satu klip pun berhasil distandarkan — periksa ffmpeg/encoder")
        self.maju("split", total, total, jadi=jadi, gagal=gagal)
        return jadi

    # ------------------------------------------------------------- Fase aug
    def fase_aug(self):
        """Augmentasi variasi: HANYA train, N varian per klip asli (invarian #2).
        Varian dipilih acak dari yang AMAN menurut kecerahan klipnya."""
        n = self.resep["varian"]
        if n <= 0:
            self.maju("aug", 0, 0)
            return 0
        sumber = [(k, p) for k, ps in self.train_asli.items() for p in ps]
        total = len(sumber) * n
        jadi = 0
        sudah = 0
        for kelas, src in sumber:
            self.cek()
            aman = self._varian_aman(src)
            if not aman:
                continue
            pilih = self.rng.sample(aman, min(n, len(aman)))
            for vi, varian in enumerate(pilih):
                sudah += 1
                if sudah % 10 == 0:
                    self.maju("aug", sudah, total, jadi=jadi)
                folder = self._dir_kelas("train", kelas)
                dst = self._nama_bebas(folder, f"{src.stem}_aug_{varian}_{vi:02d}")
                if self._augment(src, dst, varian):
                    self._catat(dst, "train", kelas, "aug", src.name)
                    jadi += 1
        self.maju("aug", total, total, jadi=jadi)
        return jadi

    # ----------------------------------------------------------- hitung train
    def _hitung_train(self) -> dict[str, int]:
        """Jumlah klip train SAAT INI per kelas (semua: asli + aug + bal)."""
        out: dict[str, int] = {}
        for kelas in self.kelas:
            d = self.dirv / "train" / safe_slug(kelas)
            out[kelas] = sum(1 for _ in d.glob("*.mp4")) if d.is_dir() else 0
        return out

    def _tambah_balans(self, kelas: str, butuh: int, tanda: str) -> int:
        """Tambah `butuh` varian balancing dari klip ASLI train kelas ini.
        Sumber hanya ASLI (mengaugmentasi hasil augmentasi menumpuk distorsi)."""
        asli = list(self.train_asli.get(kelas) or [])
        if not asli or butuh <= 0:
            return 0
        folder = self._dir_kelas("train", kelas)
        jadi = jaga = i = 0
        while jadi < butuh and jaga < butuh * 4:
            self.cek()
            src = asli[i % len(asli)]
            i += 1
            aman = self._varian_aman(src)
            if not aman:
                jaga += 1
                continue
            varian = aman[(jadi + jaga) % len(aman)]
            dst = self._nama_bebas(folder, f"{src.stem}_bal_{varian}_{jadi:04d}")
            if self._augment(src, dst, varian):
                self._catat(dst, "train", kelas, tanda, src.name)
                jadi += 1
            else:
                jaga += 1
        return jadi

    # ------------------------------------------------------------- Fase kelas
    def fase_kelas(self):
        """Seimbangkan jumlah train per kelas ke median, cap 3x (invarian cap).
        Hanya menambal kekurangan dengan MENGAUGMENTASI klip asli."""
        hitung = self._hitung_train()
        if len([v for v in hitung.values() if v > 0]) < 2:
            self.maju("kelas", 0, 0)
            return 0
        rencana, target = rencana_kelas(hitung, dict(self.asli_hitung),
                                         self.resep["cap"], self.resep["sasaran"])
        self._target_kelas = target
        total = sum(rencana.values())
        if total <= 0:
            self.maju("kelas", 0, 0, sasaran=target)
            return 0
        jadi = sudah = 0
        for kelas, butuh in rencana.items():
            n = self._tambah_balans(kelas, butuh, "balans_kelas")
            jadi += n
            sudah += butuh
            self.maju("kelas", sudah, total, jadi=jadi, sasaran=target)
        self.maju("kelas", total, total, jadi=jadi, sasaran=target)
        return jadi

    # ------------------------------------------------------------- Fase neg
    def fase_negatif(self):
        """Pulihkan porsi kelas negatif (invarian #3). Penyeimbangan kelas lain
        menambah train; porsi negatif yang dirancang menyusut. Fase ini
        mengembalikannya ke porsi ASLI, dijepit cap supaya tak melanggar atap."""
        if not self.negatif or self.negatif not in self.kelas:
            self.maju("neg", 0, 0)
            return 0
        neg_asli = self.asli_hitung.get(self.negatif, 0)
        total_asli = sum(self.asli_hitung.values())
        if neg_asli <= 0 or total_asli <= 0:
            self.maju("neg", 0, 0)
            return 0
        porsi0 = neg_asli / total_asli

        hitung = self._hitung_train()
        total_train = sum(hitung.values())
        neg_kini = hitung.get(self.negatif, 0)
        target = getattr(self, "_target_kelas", 0) or int(np.median(
            [v for v in hitung.values() if v > 0] or [neg_kini]))
        atap = int(self.resep["cap"] * target)
        butuh = sasaran_negatif(total_train, neg_kini, porsi0, atap)
        if butuh <= 0:
            self.maju("neg", 0, 0, porsi_asal=round(porsi0, 4))
            return 0
        jadi = self._tambah_balans(self.negatif, butuh, "negatif")
        self.maju("neg", butuh, butuh, jadi=jadi, porsi_asal=round(porsi0, 4))
        return jadi

    # ------------------------------------------------------------- tutup
    def _verifikasi_valid_bersih(self):
        """Pemeriksaan terakhir invarian #2: valid/ NOL klip _aug_/_bal_.
        Melempar kalau terlanggar — build gagal keras, bukan diam menulis alat
        ukur yang bocor."""
        vdir = self.dirv / "valid"
        if not vdir.is_dir():
            return
        kotor = [p.name for p in vdir.rglob("*.mp4")
                 if "_aug_" in p.stem or "_bal_" in p.stem]
        if kotor:
            raise KlipOlahTolak(
                f"verifikasi gagal: {len(kotor)} klip augmentasi di valid/ "
                f"(mis. {kotor[0]}) — valid harus 100% klip asli")

    def tutup(self, catatan: str = "") -> dict:
        """aksi.yaml + MANIFES.json + verifikasi + ringkasan angka."""
        self.maju("tutup", 0, 1)
        self._verifikasi_valid_bersih()

        jumlah = {s: 0 for s in SPLIT}
        per_kelas: dict[str, dict[str, int]] = {
            k: {"train": 0, "valid": 0} for k in self.kelas}
        for s in SPLIT:
            for kelas in self.kelas:
                d = self.dirv / s / safe_slug(kelas)
                n = sum(1 for _ in d.glob("*.mp4")) if d.is_dir() else 0
                jumlah[s] += n
                per_kelas[kelas][s] = n

        # aksi.yaml: tata letak folder-per-kelas yang dibaca ketiga backend.
        nama = list(self.kelas)
        yaml = [
            "# dataset klip aksi (HIGOLAB)", "train: train", "val: valid",
            f"nc: {len(nama)}",
            "names: [" + ", ".join(f"'{n}'" for n in nama) + "]",
            f"negatif: {self.negatif or '~'}", "",
        ]
        (self.dirv / "aksi.yaml").write_text("\n".join(yaml), encoding="utf-8")

        (self.dirv / "MANIFES.json").write_text(json.dumps({
            "versi": self.nomor, "jenis": "aksi", "resep": self.resep,
            "aksi": {"kelas": nama, "merge": self.merge, "negatif": self.negatif},
            "berkas": self.manifes,
        }, ensure_ascii=False), encoding="utf-8")

        asal_hitung: dict[str, int] = defaultdict(int)
        for m in self.manifes:
            asal_hitung[m["asal"]] += 1
        ringkas = {"n": sum(jumlah.values()), "jumlah": jumlah,
                   "kelas": len(self.kelas), "per_kelas": per_kelas,
                   "negatif": self.negatif, "asal": dict(asal_hitung),
                   "jenis": "aksi", "detik": round(time.time() - self._t0, 1)}
        self.maju("tutup", 1, 1, ringkas=ringkas)
        return ringkas

    # ------------------------------------------------------------- jalankan
    def jalankan(self, catatan: str = "") -> dict:
        buang_hasil(self.ds, self.nomor)
        self.dirv.mkdir(parents=True, exist_ok=True)
        try:
            self.fase_pra()
            if not self.records:
                raise KlipOlahTolak(
                    "belum ada klip berlabel — labeli klip dulu sebelum "
                    "membuat versi aksi")
            self.fase_split()
            self.fase_aug()
            self.fase_kelas()
            self.fase_negatif()
            return self.tutup(catatan)
        except Dibatalkan:
            buang_hasil(self.ds, self.nomor)
            catat_maju(self.kunci, jalan=False, batal=True,
                       fase_nama="Dibatalkan")
            raise


class KlipOlahTolak(Exception):
    """Build versi klip tak bisa diselesaikan (ffmpeg, atau invarian terlanggar)."""


# ============================================================
# PELUNCUR  (dipanggil dari thread latar rute — mirip versi_mulai gambar)
# ============================================================
def jalankan_versi(ds: Path, nomor: int, items: list[dict], aksi: dict,
                   resep: dict, *, kunci: str, oleh: str, catatan: str = "") -> None:
    """Jalankan satu build versi klip di bawah Giliran + batas utas, lalu
    daftarkan hasilnya lewat versi.buat supaya muncul di daftar/lencana Versi.

    Dipanggil DI DALAM thread latar: Giliran ditunggu di sini (bukan di rute),
    jadi rutenya tetap menjawab seketika dan yang antre melihat "Menunggu
    giliran" di panel kemajuan yang sama. Kemajuan & galat ditulis ke _maju
    lewat `kunci` yang dipoll peramban.
    """
    _lupakan_batal(kunci)
    bersihkan_maju(kunci)
    catat_maju(kunci, jalan=True, nomor=nomor, persen=0.0,
               fase_nama="Menunggu giliran")
    try:
        with Giliran(kunci):
            if _batal_diset(kunci):
                catat_maju(kunci, jalan=False, batal=True)
                return
            batasi_utas()
            job = Pekerjaan(ds, nomor, items, aksi, resep, kunci=kunci,
                            oleh=oleh, batal=lambda: _batal_diset(kunci))
            try:
                hasil = job.jalankan(catatan)
            except Dibatalkan:
                catat_maju(kunci, jalan=False, batal=True)
                return
            # Daftarkan versi: `gambar` = nama berkas keluaran (klip), `peta` =
            # nama->split. versi.daftar menghitung n_ada atas gambar (IMG_EXT),
            # jadi untuk klip ia 0 — tak masalah, yang penting versinya terdaftar
            # dan lencana "Versi" menghitungnya.
            gambar = [Path(m["berkas"]).name for m in job.manifes]
            peta = {Path(m["berkas"]).name: m["split"] for m in job.manifes}
            rasio = f"{round((1 - resep_sah(resep)['rasio_val']) * 100)}:" \
                    f"{round(resep_sah(resep)['rasio_val'] * 100)}"
            ringkas = {"split": hasil["jumlah"], "kelas": hasil["kelas"],
                       "objek": hasil["n"], "beralas": False}
            versi.buat(ds, oleh, rasio, gambar, peta, ringkas, catatan,
                       resep=resep_sah(resep), nomor=nomor, hasil=hasil)
            catat_maju(kunci, jalan=False, selesai=True, nomor=nomor)
    except KlipOlahTolak as e:
        buang_hasil(ds, nomor)
        catat_maju(kunci, jalan=False, galat=str(e)[:200])
    except Exception as e:                        # noqa: BLE001
        log.exception("pembuatan versi aksi gagal")
        buang_hasil(ds, nomor)
        catat_maju(kunci, jalan=False, galat=str(e)[:200])
