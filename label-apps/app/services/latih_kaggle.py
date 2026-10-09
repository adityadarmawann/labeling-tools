"""
Backend training KEDUA: menjalankan training YOLO di Kaggle lewat API resmi,
sebagai CADANGAN GPU di luar satu-satunya mesin ini.

    python -m app.services.latih_kaggle <folder-projek> <nomor>

Dipanggil latih.jalankan() sebagai subproses terlepas — SAMA seperti
latih_jalan.py (jalur lokal) — jadi seluruh mesin status()/hidup(pid) yang
sudah ada berlaku tanpa diubah: selama poller ini hidup, keadaan "jalan"; saat
ia menulis best.pt + results.csv ke .latih/L<n>/ lalu keluar, status() membaca
hasilnya persis seperti training lokal. Bedanya dengan jalur lokal:

  * TIDAK memakai GPU lokal dan TIDAK mengambil flock GPU -> training Kaggle
    bisa berjalan BERSAMAAN dengan training lokal. Itulah gunanya: satu mesin
    fisik tak lagi jadi satu-satunya lajur.

  * Kaggle membatasi satu run ~12 jam dan ~30 jam GPU/minggu per akun. Maka satu
    training panjang dijalankan BERBILAH (leg): tiap leg berhenti rapi sebelum
    batas waktu (callback wall-clock), menyimpan last.pt, lalu leg berikutnya
    MELANJUTKAN dari bobot itu. Kalau kuota satu akun menipis, poller berpindah
    ke akun berikutnya di pool (rotasi). Satu subproses ini mengorkestrasi
    seluruh rantai dan tahan restart server.

Semua sentuhan jaringan lewat satu fungsi _kg(); tesnya memalsukan itu sehingga
suite tak pernah menyentuh Kaggle.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from . import latih

# Batas waktu satu leg (jam). Di bawah batas keras Kaggle (~12 jam) supaya
# training berhenti rapi dan last.pt sempat tersimpan sebelum kernel dibunuh.
BATAS_JAM = float(os.environ.get("LABELAPP_KAGGLE_BATAS_JAM", "11") or 11)
# Anggaran kuota GPU per akun per 7 hari (jam). Akun dilewati kalau pemakaian
# 7-harinya + satu leg akan menembus ini. Konservatif terhadap batas resmi ~30.
KUOTA_MINGGU_JAM = float(os.environ.get("LABELAPP_KAGGLE_KUOTA_JAM", "28") or 28)
# GPU yang diminta. T4 x2 sama seperti kernel paragon yang sudah terbukti.
AKSELERATOR = os.environ.get("LABELAPP_KAGGLE_GPU", "nvidiaTeslaT4") or "nvidiaTeslaT4"
# Kernel Kaggle sesekali ERROR karena sebab sesaat (blip infra, init DDP di
# T4x2) lalu sukses saat diulang dengan konfigurasi yang SAMA — terbukti saat
# uji. Maka satu leg yang gagal TANPA kemajuan dan BUKAN karena kuota dicoba
# ulang beberapa kali dulu sebelum dinyatakan gagal. (Kegagalan kuota tidak
# ikut dihitung di sini — itu memicu rotasi akun, bukan coba-ulang.)
MAKS_COBA_LEG = max(1, int(os.environ.get("LABELAPP_KAGGLE_COBA", "3") or 3))
# Batas KERAS durasi run kernel (detik), dipasang di `kernels push --timeout`.
# Jaring pengaman: kalau kernel MACET sebelum/di luar training (mis. pip install
# menggantung, dataset tak terbaca), Kaggle memaksa berhenti — jadi tak ada
# skenario kuota GPU terbakar tanpa progres. Di atas BATAS_JAM plus kelonggaran
# untuk pip install + scan dataset + validasi/simpan setelah stop wall-clock;
# Kaggle sendiri tak akan melewati batas globalnya (~12 jam).
TIMEOUT_KERNEL = int(BATAS_JAM * 3600 + 1800)


def _sekarang() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ============================================================
# AKUN & KREDENSIAL
# ============================================================
#
# Token TIDAK PERNAH ada di repo. Diambil dari berkas di luar repo (token_file)
# atau — hanya untuk pool yang ditulis admin sendiri — inline "token".

def _baca_token_file(p: Path) -> str:
    """Baris pertama yang tampak seperti token Kaggle dari sebuah berkas.
    Berkas catatan boleh berisi teks lain; yang diambil token 'KGAT_...' atau,
    kalau tak ada pola itu, baris tak-kosong pertama."""
    try:
        teks = Path(p).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for ln in teks.splitlines():
        ln = ln.strip()
        if ln.startswith("KGAT_") or re.fullmatch(r"[A-Za-z0-9_\-]{20,}", ln):
            return ln
    for ln in teks.splitlines():
        if ln.strip():
            return ln.strip()
    return ""


def _token_dari_spec(spec: dict) -> str:
    if not isinstance(spec, dict):
        return ""
    if spec.get("token"):
        return str(spec["token"]).strip()
    tf = spec.get("token_file")
    return _baca_token_file(Path(tf).expanduser()) if tf else ""


def akun_pool(settings) -> list[dict]:
    """Daftar akun Kaggle [{user, token}] yang bisa dipakai, token sudah
    diselesaikan. Dari AKUN_FILE (pool untuk rotasi) atau USER+TOKEN_FILE (satu
    akun). Entri tanpa user/token dibuang. [] = backend Kaggle mati."""
    out: list[dict] = []
    f = getattr(settings, "kaggle_akun_file", None)
    if f and Path(f).exists():
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = []
        for spec in data if isinstance(data, list) else []:
            user = str((spec or {}).get("user") or "").strip()
            tok = _token_dari_spec(spec or {})
            if user and tok:
                out.append({"user": user, "token": tok})
    # Akun tunggal (dipakai kalau pool tidak ada / kosong).
    if not out:
        user = str(getattr(settings, "kaggle_user", "") or "").strip()
        tf = getattr(settings, "kaggle_token_file", None)
        tok = _baca_token_file(Path(tf).expanduser()) if tf else ""
        if user and tok:
            out.append({"user": user, "token": tok})
    # Buang duplikat user (akun yang sama disebut dua kali), pertahankan urutan.
    unik, lihat = [], set()
    for a in out:
        if a["user"] not in lihat:
            lihat.add(a["user"])
            unik.append(a)
    return unik


def siap(settings) -> tuple[bool, str]:
    """Backend Kaggle bisa dipakai dari server ini atau tidak, beserta alasannya.
    Dipakai router untuk memutuskan menawarkan pilihan "Kaggle" di form atau
    tidak — dan untuk menolak lebih awal dengan pesan yang jelas."""
    import importlib.util
    if importlib.util.find_spec("kaggle") is None:
        return False, ("Paket 'kaggle' belum terpasang di venv server ini. "
                       "Pasang dengan: pip install kaggle")
    if not akun_pool(settings):
        return False, ("Belum ada akun Kaggle terkonfigurasi. Set "
                       "LABELAPP_KAGGLE_AKUN_FILE (pool) atau LABELAPP_KAGGLE_USER "
                       "+ LABELAPP_KAGGLE_TOKEN_FILE.")
    return True, ""


# ============================================================
# SEAM JARINGAN — satu-satunya tempat kaggle CLI dipanggil
# ============================================================

def _kaggle_bin() -> str:
    """Executable kaggle di venv yang menjalankan server ini (tempat ia
    dipasang), dengan cadangan PATH."""
    kandidat = Path(sys.executable).with_name("kaggle")
    if kandidat.exists():
        return str(kandidat)
    return shutil.which("kaggle") or "kaggle"


def _kg(args: list[str], token: str, timeout: int = 1800,
        masuk: str | None = None) -> tuple[int, str]:
    """Jalankan satu perintah kaggle CLI dengan token akun ybs di env. Satu-
    satunya sentuhan jaringan — tes memalsukan fungsi INI, bukan di bawahnya."""
    env = dict(os.environ)
    if token:
        env["KAGGLE_API_TOKEN"] = token
    try:
        p = subprocess.run([_kaggle_bin(), *args], input=masuk,
                           capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired as e:
        return 124, f"timeout: {e}"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _tidur(detik: float) -> None:
    """Dibungkus supaya tes bisa mem-patch jadi no-op."""
    time.sleep(detik)


# ============================================================
# LEDGER KUOTA — GLOBAL lintas-projek (<uploads_root>/_kaggle)
# ============================================================
#
# Kuota Kaggle (~30 jam GPU/minggu) milik AKUN, bukan projek. Maka ledgernya
# satu untuk seluruh instalasi — bukan per-projek — supaya "akun mana yang masih
# punya jatah minggu ini" menghitung SEMUA training akun itu, tidak buta pada
# projek lain. Fungsi-fungsinya menerima `basis` (folder ledger) agar mudah
# diuji; produksi memakai basis_ledger(settings).

def basis_ledger(settings) -> Path:
    """Folder ledger kuota global: <uploads_root>/_kaggle."""
    return Path(settings.uploads_root) / "_kaggle"


def _ledger_path(basis) -> Path:
    return Path(basis) / "kaggle-pakai.json"


def _baca_ledger(basis) -> dict:
    p = _ledger_path(basis)
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def _tulis_ledger(basis, data: dict) -> None:
    p = _ledger_path(basis)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, p)


def _pakai_7hari(ledger: dict, user: str) -> float:
    """Jumlah jam GPU yang terpakai akun ini dalam 7 hari terakhir."""
    now = time.time()
    tot = 0.0
    for e in (ledger.get("pakai") or []):
        if e.get("user") != user:
            continue
        if now - float(e.get("ts") or 0) <= 7 * 86400:
            tot += float(e.get("jam") or 0)
    return tot


def _catat_pakai(basis, user: str, jam: float) -> None:
    led = _baca_ledger(basis)
    led.setdefault("pakai", []).append(
        {"user": user, "ts": time.time(), "jam": round(float(jam), 3)})
    # Pangkas entri lebih tua dari 14 hari supaya berkas tidak tumbuh selamanya.
    batas = time.time() - 14 * 86400
    led["pakai"] = [e for e in led["pakai"] if float(e.get("ts") or 0) >= batas]
    _tulis_ledger(basis, led)


def _tandai_habis(basis, user: str) -> None:
    """Tandai akun kehabisan kuota sampai jendela 7-hari bergeser (reaktif:
    dipakai saat kernel gagal dengan galat kuota)."""
    led = _baca_ledger(basis)
    led.setdefault("habis", {})[user] = time.time()
    _tulis_ledger(basis, led)


def _akun_habis(basis, user: str) -> bool:
    led = _baca_ledger(basis)
    ts = (led.get("habis") or {}).get(user)
    return bool(ts and time.time() - float(ts) < 7 * 86400)


def reset_habis(basis) -> None:
    """Hapus penanda 'kuota habis' semua akun. Dipakai saat orang menekan
    'Lanjutkan di Kaggle' pada job yang tertunda: itu permintaan eksplisit untuk
    MENCOBA LAGI SEKARANG. Kalau kuotanya memang masih habis, kernel akan gagal
    lagi dengan galat kuota dan penandanya dipasang ulang — jadi aman."""
    led = _baca_ledger(basis)
    if led.get("habis"):
        led["habis"] = {}
        _tulis_ledger(basis, led)


def _pilih_akun(basis, pool: list[dict]) -> dict | None:
    """Akun pertama yang (a) tidak ditandai habis dan (b) pemakaian 7-harinya
    masih menyisakan ruang untuk satu leg. None kalau semua mentok."""
    led = _baca_ledger(basis)
    for a in pool:
        if _akun_habis(basis, a["user"]):
            continue
        if _pakai_7hari(led, a["user"]) + BATAS_JAM <= KUOTA_MINGGU_JAM:
            return a
    return None


def ringkas_akun(settings) -> list[dict]:
    """Status tiap akun di pool: jam GPU terpakai 7 hari, sisa perkiraan, dan
    apakah sedang ditandai habis. Dipakai UI supaya terlihat akun mana yang
    masih punya jatah minggu ini — inti dari rotasi multi-akun."""
    basis = basis_ledger(settings)
    led = _baca_ledger(basis)
    out = []
    for a in akun_pool(settings):
        pakai = round(_pakai_7hari(led, a["user"]), 1)
        out.append({
            "user": a["user"],
            "pakai_jam": pakai,
            "kuota_jam": KUOTA_MINGGU_JAM,
            "sisa_jam": round(max(0.0, KUOTA_MINGGU_JAM - pakai), 1),
            "habis": _akun_habis(basis, a["user"]),
        })
    return out


# ============================================================
# NAMA & METADATA (murni — diuji tanpa jaringan)
# ============================================================

def _slug(s: str, maks: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")
    return (s or "x")[:maks].strip("-") or "x"


def nama_dataset(ds, versi: int, arsitektur: str = "yolo") -> str:
    # RF-DETR memakai dataset COCO (beda isi dari YOLO) -> slug beda supaya reuse
    # ledger tak keliru memakai dataset YOLO untuk RF-DETR pada versi yang sama.
    sfx = "-coco" if arsitektur == "rfdetr" else ""
    return _slug(f"higolab-{Path(ds).name}-v{int(versi)}{sfx}")


def nama_kernel(ds, nomor: int, leg: int) -> str:
    return _slug(f"higolab-{Path(ds).name}-l{int(nomor)}-leg{int(leg)}")


def _meta_dataset(user: str, slug: str, judul: str) -> dict:
    return {"title": judul[:50], "id": f"{user}/{slug}",
            "licenses": [{"name": "CC0-1.0"}]}


def _meta_kernel(user: str, slug: str, code_file: str,
                 dataset_sources: list[str]) -> dict:
    return {
        "id": f"{user}/{slug}", "title": slug[:50], "code_file": code_file,
        "language": "python", "kernel_type": "script", "is_private": True,
        "enable_gpu": True, "enable_tpu": False, "enable_internet": True,
        "keywords": [], "dataset_sources": list(dataset_sources),
        "kernel_sources": [], "competition_sources": [], "model_sources": [],
    }


# Skrip yang BENAR-BENAR melatih di sisi Kaggle. Dibuat dari templat dengan
# penggantian token (bukan .format) supaya kurung di dalam kode Python tidak
# bentrok. Guard augmentasi pose MENYALIN latih_jalan.py: flip vertikal mati,
# flip horizontal hanya kalau flip_idx bukan identitas, mosaic dijepit <=0.5.
# Stop wall-clock lewat callback: training berhenti rapi sebelum batas Kaggle.
_SKRIP_TEMPLAT = '''
import os, sys, glob, subprocess, zipfile, json, time, shutil
print("== pasang ultralytics ==", flush=True)
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "ultralytics"], check=True)
import torch
print("CUDA_TERSEDIA", torch.cuda.is_available(), "N_GPU", torch.cuda.device_count(), flush=True)

PAR   = json.loads(r"""__PAR_JSON__""")
TUGAS = "__TUGAS__"
BOBOT = "__BOBOT__"
RUN   = "__RUN__"
BATAS_DETIK = float(__BATAS_JAM__) * 3600.0
RESUME = __RESUME__

# --- temukan dataset (data.yaml) di /kaggle/input, apa pun kedalamannya ---
ys = glob.glob("/kaggle/input/**/data.yaml", recursive=True)
if not ys:
    for z in glob.glob("/kaggle/input/**/*.zip", recursive=True):
        try: zipfile.ZipFile(z).extractall("/kaggle/working/ds")
        except Exception as e: print("gagal unzip", z, e, flush=True)
    ys = glob.glob("/kaggle/working/**/data.yaml", recursive=True)
assert ys, "data.yaml tak ketemu di /kaggle/input"
SRC = os.path.dirname(sorted(ys, key=len)[0])
print("DATASET_DI", SRC, flush=True)

import yaml
y = yaml.safe_load(open(os.path.join(SRC, "data.yaml")))
y["path"] = SRC
for k in ("train", "val", "test"):
    sub = {"val": "valid/images"}.get(k, k + "/images")
    if os.path.isdir(os.path.join(SRC, os.path.dirname(sub))):
        y[k] = sub
DATA_YAML = "/kaggle/working/data.yaml"
yaml.safe_dump(y, open(DATA_YAML, "w"), sort_keys=False, allow_unicode=True)

# --- guard augmentasi khusus pose (menyalin latih_jalan.py) ---
if TUGAS == "pose":
    PAR["flipud"] = 0.0
    fi = (y.get("flip_idx") or [])
    if not fi or list(fi) == list(range(len(fi))):
        PAR["fliplr"] = 0.0
    if PAR.get("mosaic", 0.0) > 0.5:
        PAR["mosaic"] = 0.5

# --- bobot awal: leg lanjutan mulai dari last.pt yang dibawa dari leg sebelum ---
mula = BOBOT
if RESUME:
    ck = glob.glob("/kaggle/input/**/last.pt", recursive=True) or \\
         glob.glob("/kaggle/input/**/best.pt", recursive=True)
    assert ck, "RESUME tapi checkpoint (last.pt) tak ketemu di input"
    mula = sorted(ck, key=len)[0]
    print("LANJUT_DARI", mula, flush=True)

from ultralytics import YOLO
DEV = "0,1" if torch.cuda.device_count() >= 2 else 0
print("DEVICE", DEV, "BATAS_JAM", __BATAS_JAM__, "RESUME", RESUME, flush=True)

t0 = time.time()
def _stop_waktu(trainer):
    if time.time() - t0 >= BATAS_DETIK:
        print("BATAS_WAKTU tercapai -> berhenti rapi", flush=True)
        trainer.stop = True

model = YOLO(mula)
model.add_callback("on_fit_epoch_end", _stop_waktu)
model.train(data=DATA_YAML, task=TUGAS, project="/kaggle/working", name=RUN,
            exist_ok=True, device=DEV, **PAR)

import pandas as pd
rp = "/kaggle/working/" + RUN + "/results.csv"
EP = 0
if os.path.exists(rp):
    try: EP = len(pd.read_csv(rp))
    except Exception: EP = 0
bp = "/kaggle/working/" + RUN + "/weights/best.pt"
print("EPOCH_LEG", EP, flush=True)
print("BEST_EXISTS", os.path.exists(bp), (os.path.getsize(bp) if os.path.exists(bp) else 0), flush=True)
'''


def skrip_latih(tugas: str, bobot: str, par: dict, *, run: str,
                batas_jam: float, resume: bool) -> str:
    """Rakit skrip kernel. Murni -> diuji tanpa jaringan."""
    return (_SKRIP_TEMPLAT
            .replace("__PAR_JSON__", json.dumps(par))
            .replace("__TUGAS__", str(tugas))
            .replace("__BOBOT__", str(bobot))
            .replace("__RUN__", str(run))
            .replace("__BATAS_JAM__", repr(float(batas_jam)))
            .replace("__RESUME__", "True" if resume else "False"))


# Skrip kernel RF-DETR: pip install rfdetr sendiri (nol dampak lokal), cari
# dataset COCO di /kaggle/input, RFDETR<ukuran>(resolution kelipatan-56).train(),
# simpan checkpoint terbaik ke /kaggle/working/<run>. Augmentasi internal rfdetr.
#
# RESUME BERBILAH (seperti YOLO): kalau RESUME, utamakan last.ckpt (state PENUH
# PyTorch-Lightning: optimizer+epoch) dari /kaggle/input; epochs = TOTAL target,
# Lightning melanjutkan current_epoch -> max_epochs, BUKAN mengulang dari 0.
#
# STOP WALL-CLOCK: rfdetr membangun Trainer sendiri tanpa jalur max_time, jadi
# callback stop DISUNTIK lewat monkeypatch pl.Trainer.__init__ (terbukti di
# spike lokal). Stateless -> tiap leg dapat jatah segar, tak bentrok resume.
# Berhenti RAPI di batas epoch sebelum batas keras Kaggle -> last.ckpt tersimpan
# -> Kaggle commit output -> leg berikutnya lanjut dari situ (bukan di-kill, yang
# mungkin tak menyimpan output).
_SKRIP_RFDETR = '''
import os, sys, glob, json, csv, time, subprocess
print("== pasang rfdetr ==", flush=True)
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "rfdetr[train]"], check=True)
import torch
print("CUDA_TERSEDIA", torch.cuda.is_available(), "N_GPU", torch.cuda.device_count(), flush=True)
PAR = json.loads(r"""__PAR_JSON__""")
MODEL = "__MODEL__"
RUN = "__RUN__"
RESUME = __RESUME__
BATAS_DETIK = float(__BATAS_JAM__) * 3600.0

# Suntik stop wall-clock ke SETIAP Trainer yang dibangun rfdetr. Callback tanpa
# state -> tak di-restore saat resume, jadi tiap leg mulai hitung dari 0 lagi
# (tepat: tiap kernel punya ~11 jam sendiri).
import pytorch_lightning as pl
from pytorch_lightning.callbacks import Callback
class _StopJam(Callback):
    def on_fit_start(self, trainer, pl_module):
        self._t0 = time.time()
    def on_train_epoch_end(self, trainer, pl_module):
        if time.time() - getattr(self, "_t0", time.time()) > BATAS_DETIK:
            print("STOP_WAKTU epoch", trainer.current_epoch, flush=True)
            trainer.should_stop = True
_orig_init = pl.Trainer.__init__
def _init(self, *a, **k):
    cbs = list(k.get("callbacks") or [])
    cbs.append(_StopJam())
    k["callbacks"] = cbs
    return _orig_init(self, *a, **k)
pl.Trainer.__init__ = _init

cand = glob.glob("/kaggle/input/**/train/_annotations.coco.json", recursive=True)
assert cand, "dataset COCO (train/_annotations.coco.json) tak ketemu di input"
DS = os.path.dirname(os.path.dirname(sorted(cand, key=len)[0]))
print("DATASET_DI", DS, flush=True)
import rfdetr
KELAS = {"nano": "RFDETRNano", "small": "RFDETRSmall", "medium": "RFDETRMedium", "large": "RFDETRLarge"}
Model = getattr(rfdetr, KELAS.get(MODEL, "RFDETRNano"))
res = max(56, round(float(PAR.get("resolution", 560)) / 56) * 56)
OUT = "/kaggle/working/" + RUN
kw = dict(dataset_dir=DS, output_dir=OUT, epochs=int(PAR.get("epochs", 100)),
          batch_size=int(PAR.get("batch_size", 4)),
          grad_accum_steps=int(PAR.get("grad_accum_steps", 4)),
          lr=float(PAR.get("lr", 1e-4)), lr_encoder=float(PAR.get("lr_encoder", 1.5e-4)),
          warmup_epochs=float(PAR.get("warmup_epochs", 0.0)),
          early_stopping=bool(PAR.get("early_stopping", False)))
if RESUME:
    # Utamakan last.ckpt (state PTL penuh: optimizer+scheduler+epoch) supaya
    # resume MELANJUTKAN; baru .pth (bobot+epoch saja) sebagai cadangan.
    ck = (sorted(glob.glob("/kaggle/input/**/last.ckpt", recursive=True), key=len)
          or sorted(glob.glob("/kaggle/input/**/checkpoint_best_total.pth", recursive=True), key=len)
          or sorted(glob.glob("/kaggle/input/**/*.pth", recursive=True), key=len))
    if ck:
        kw["resume"] = ck[0]; print("RESUME_DARI", ck[0], flush=True)
# 2-GPU (T4 x2) OPSIONAL — DEFAULT MATI. Kalau dinyalakan DAN ada >=2 GPU:
# DDP spawn (aman di kernel Kaggle yang dijalankan sebagai notebook), dan
# grad_accum DIBAGI jumlah GPU supaya batch efektif (batch x n_gpu x accum)
# TETAP 16 sesuai resep Roboflow. Mati -> jalur 1-GPU yang sudah terbukti.
MULTIGPU = __MULTIGPU__
if MULTIGPU and torch.cuda.device_count() >= 2:
    NG = torch.cuda.device_count()
    kw["devices"] = NG
    kw["strategy"] = "ddp_spawn"
    ga = int(kw.get("grad_accum_steps", 1))
    kw["grad_accum_steps"] = max(1, ga // NG)
    print("MULTI_GPU AKTIF", NG, "GPU · grad_accum", ga, "->", kw["grad_accum_steps"],
          "(batch efektif tetap)", flush=True)
else:
    print("MULTI_GPU nonaktif — 1 GPU", flush=True)
print("MODEL", MODEL, "res", res, "epochs", kw["epochs"], "resume", RESUME, flush=True)
Model(resolution=res).train(**kw)
# Epoch KUMULATIF global dari metrics.csv (CSVLogger PTL melanjutkan penomoran
# epoch lintas-resume), supaya orkestrator tahu sudah berapa epoch keseluruhan.
mc = sorted(glob.glob(OUT + "/**/metrics.csv", recursive=True), key=len)
gmax = -1
if mc:
    for row in csv.DictReader(open(mc[0])):
        e = row.get("epoch")
        if e not in (None, ""):
            try: gmax = max(gmax, int(float(e)))
            except ValueError: pass
best = (glob.glob(OUT + "/**/checkpoint_best_total.pth", recursive=True)
        or glob.glob(OUT + "/**/*best*.pth", recursive=True) or glob.glob(OUT + "/**/*.pth", recursive=True))
last = glob.glob(OUT + "/**/last.ckpt", recursive=True)
print("EPOCH_GLOBAL", gmax + 1, flush=True)
print("LAST_CKPT", bool(last), flush=True)
print("BEST_EXISTS", bool(best), (os.path.getsize(best[0]) if best else 0), flush=True)
'''


def skrip_rfdetr(model: str, par: dict, *, run: str, resume: bool = False,
                 batas_jam: float = BATAS_JAM, multigpu: bool = False) -> str:
    """Rakit skrip kernel RF-DETR. Murni -> diuji tanpa jaringan.

    multigpu=True -> pakai SEMUA GPU (T4 x2) lewat DDP spawn, grad_accum dibagi
    jumlah GPU agar batch efektif tetap 16. Default False (1-GPU terbukti)."""
    return (_SKRIP_RFDETR
            .replace("__PAR_JSON__", json.dumps(par))
            .replace("__MODEL__", str(model))
            .replace("__RUN__", str(run))
            .replace("__BATAS_JAM__", repr(float(batas_jam)))
            .replace("__MULTIGPU__", "True" if multigpu else "False")
            .replace("__RESUME__", "True" if resume else "False"))


# ============================================================
# SERAP KELUARAN (murni) — salin hasil kernel ke .latih/L<n>/
# ============================================================

def _epoch_csv(d: Path) -> int:
    """Berapa baris data di results.csv (0 kalau tak ada)."""
    return latih.baca_hasil_csv(Path(d)).get("epoch") or 0


def serap_keluaran(out_dir: Path, run: str, dir_latih: Path) -> int:
    """Pindahkan isi folder run hasil kernel (<out>/<run>/*) ke dir_latih,
    datar seperti training lokal: results.csv, weights/, grafik PNG. Kembalikan
    jumlah epoch yang terbaca dari results.csv setelah penyerapan."""
    out_dir, dir_latih = Path(out_dir), Path(dir_latih)
    sumber = out_dir / run
    if not sumber.is_dir():
        # Kadang Kaggle meratakan output; cari results.csv di mana pun.
        kandidat = list(out_dir.rglob("results.csv"))
        sumber = kandidat[0].parent if kandidat else out_dir
    dir_latih.mkdir(parents=True, exist_ok=True)
    for p in sumber.rglob("*"):
        if p.is_dir():
            continue
        rel = p.relative_to(sumber)
        tuju = dir_latih / rel
        tuju.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(p, tuju)
        except OSError:
            pass
    return _epoch_csv(dir_latih)


def _epoch_map_metrics(out_dir: Path) -> tuple[int, float | None]:
    """Baca metrics.csv RF-DETR (CSVLogger PTL): kembalikan (jumlah_epoch_global,
    mAP_terakhir|None). Epoch rfdetr 0-indexed dan BERLANJUT lintas-resume, jadi
    max(epoch)+1 = jumlah epoch yang sudah diselesaikan keseluruhan."""
    mc = sorted(Path(out_dir).rglob("metrics.csv"), key=lambda p: len(str(p)))
    if not mc:
        return 0, None
    import csv as _csv
    emax = -1
    mp = None
    try:
        for row in _csv.DictReader(open(mc[0], encoding="utf-8")):
            e = row.get("epoch")
            if e not in (None, ""):
                try:
                    emax = max(emax, int(float(e)))
                except ValueError:
                    pass
            # kolom mAP rfdetr bervariasi (val/ema_map, metrics/mAP50-95, …) —
            # ambil nilai map apa pun yang terisi, yang terakhir menang.
            for kcol, v in row.items():
                if kcol and "map" in kcol.lower() and v not in (None, ""):
                    try:
                        mp = float(v)
                    except ValueError:
                        pass
    except OSError:
        return 0, None
    return (emax + 1 if emax >= 0 else 0), mp


def _serap_rfdetr(out_dir: Path, dir_latih: Path) -> int:
    """Serap hasil kernel RF-DETR ke .latih/L<n>/ untuk jalur Kaggle berbilah:
    - checkpoint_best_total.pth -> weights/best.pt (deliverable; punya_bobot/unduh)
    - last.ckpt -> rfdetr/last.ckpt (STATE PENUH untuk resume leg berikutnya)
    - PNG grafik -> kartu; results.csv minimal dari epoch+mAP metrics.csv
    Kembalikan jumlah epoch GLOBAL (kumulatif lintas-leg)."""
    out_dir, dir_latih = Path(out_dir), Path(dir_latih)
    (dir_latih / "weights").mkdir(parents=True, exist_ok=True)
    (dir_latih / "rfdetr").mkdir(parents=True, exist_ok=True)
    best = (sorted(out_dir.rglob("checkpoint_best_total.pth"))
            or sorted(out_dir.rglob("*best*.pth")) or sorted(out_dir.rglob("*.pth")))
    if best:
        shutil.copy2(best[0], dir_latih / "weights" / "best.pt")
    last = sorted(out_dir.rglob("last.ckpt"))
    if last:
        shutil.copy2(last[0], dir_latih / "rfdetr" / "last.ckpt")
    for png in out_dir.rglob("*.png"):
        try:
            shutil.copy2(png, dir_latih / png.name)
        except OSError:
            pass
    ep, mp = _epoch_map_metrics(out_dir)
    rp = dir_latih / "results.csv"
    if mp is not None:
        rp.write_text(f"epoch,metrics/mAP50-95(B)\n{ep},{mp}\n")
    elif ep:
        rp.write_text(f"epoch\n{ep}\n")
    return latih.baca_hasil_csv(dir_latih).get("epoch") or ep


# ============================================================
# OPERASI JARINGAN (tipis di atas _kg)
# ============================================================

def _unggah_dataset(basis, versi_dir: Path, akun: dict, slug: str,
                    judul: str) -> str:
    """Pastikan versi ada sebagai dataset milik akun ini; kembalikan 'user/slug'.
    Reuse: versi itu beku, jadi kalau dataset sudah ada di akun ini ia dipakai
    ulang tanpa unggah lagi (ditandai di ledger global per akun)."""
    user, token = akun["user"], akun["token"]
    dsid = f"{user}/{slug}"
    led = _baca_ledger(basis)
    sudah = (led.get("dataset") or {}).get(f"{user}:{slug}")
    if sudah:
        rc, _ = _kg(["datasets", "status", dsid], token, timeout=120)
        if rc == 0:
            return dsid                      # sudah ada & milik kita -> reuse
    with tempfile.TemporaryDirectory() as tmp:
        paket = Path(tmp) / "ds"
        shutil.copytree(versi_dir, paket)
        (paket / "dataset-metadata.json").write_text(
            json.dumps(_meta_dataset(user, slug, judul), indent=2))
        rc, out = _kg(["datasets", "status", dsid], token, timeout=120)
        if rc == 0:                          # sudah ada -> versi baru
            rc, out = _kg(["datasets", "version", "-p", str(paket), "-r", "zip",
                           "-m", "higolab", "-q"], token, timeout=3600)
        else:                                # belum ada -> buat
            rc, out = _kg(["datasets", "create", "-p", str(paket), "-r", "zip", "-q"],
                          token, timeout=3600)
        if rc != 0:
            raise RuntimeError(f"unggah dataset gagal: {out[-300:]}")
    # tunggu diproses sampai 'ready'
    for _ in range(40):
        _tidur(6)
        rc, out = _kg(["datasets", "status", dsid], token, timeout=120)
        if "ready" in out.lower():
            break
    led = _baca_ledger(basis)
    led.setdefault("dataset", {})[f"{user}:{slug}"] = dsid
    _tulis_ledger(basis, led)
    return dsid


def _unggah_checkpoint(ds, dir_latih: Path, akun: dict, slug: str) -> str:
    """Kemas folder run (last.pt + results.csv) jadi dataset kecil untuk leg
    lanjutan. Selalu versi baru karena bobotnya berubah tiap leg."""
    user, token = akun["user"], akun["token"]
    dsid = f"{user}/{slug}"
    with tempfile.TemporaryDirectory() as tmp:
        paket = Path(tmp) / "ck"
        (paket / "weights").mkdir(parents=True)
        for f in ("weights/last.pt", "weights/best.pt", "results.csv", "args.yaml"):
            src = Path(dir_latih) / f
            if src.exists():
                shutil.copy2(src, paket / f)
        (paket / "dataset-metadata.json").write_text(
            json.dumps(_meta_dataset(user, slug, slug), indent=2))
        rc, out = _kg(["datasets", "status", dsid], token, timeout=120)
        if rc == 0:
            rc, out = _kg(["datasets", "version", "-p", str(paket), "-r", "zip",
                           "-m", "leg", "-q"], token, timeout=600)
        else:
            rc, out = _kg(["datasets", "create", "-p", str(paket), "-r", "zip", "-q"],
                          token, timeout=600)
        if rc != 0:
            raise RuntimeError(f"unggah checkpoint gagal: {out[-300:]}")
    for _ in range(30):
        _tidur(5)
        rc, out = _kg(["datasets", "status", dsid], token, timeout=120)
        if "ready" in out.lower():
            break
    return dsid


def _unggah_checkpoint_rfdetr(ds, dir_latih: Path, akun: dict, slug: str) -> str:
    """Kemas last.ckpt RF-DETR (state PENUH PTL: optimizer+scheduler+epoch) jadi
    dataset kecil untuk leg lanjutan. Selalu versi baru (checkpoint berubah tiap
    leg). Kernel me-resume dari last.ckpt ini -> MELANJUTKAN, bukan dari nol."""
    user, token = akun["user"], akun["token"]
    dsid = f"{user}/{slug}"
    with tempfile.TemporaryDirectory() as tmp:
        paket = Path(tmp) / "ck"
        paket.mkdir(parents=True)
        src = Path(dir_latih) / "rfdetr" / "last.ckpt"
        if not src.exists():
            raise FileNotFoundError("last.ckpt RF-DETR tak ada untuk disambung")
        shutil.copy2(src, paket / "last.ckpt")
        (paket / "dataset-metadata.json").write_text(
            json.dumps(_meta_dataset(user, slug, slug), indent=2))
        rc, out = _kg(["datasets", "status", dsid], token, timeout=120)
        if rc == 0:
            rc, out = _kg(["datasets", "version", "-p", str(paket), "-r", "zip",
                           "-m", "leg", "-q"], token, timeout=600)
        else:
            rc, out = _kg(["datasets", "create", "-p", str(paket), "-r", "zip", "-q"],
                          token, timeout=600)
        if rc != 0:
            raise RuntimeError(f"unggah checkpoint RF-DETR gagal: {out[-300:]}")
    for _ in range(30):
        _tidur(5)
        rc, out = _kg(["datasets", "status", dsid], token, timeout=120)
        if "ready" in out.lower():
            break
    return dsid


def _push_kernel(akun: dict, slug: str, skrip: str,
                 dataset_sources: list[str]) -> tuple[str, str]:
    """Push satu kernel GPU; kembalikan (kernel_id, url)."""
    user, token = akun["user"], akun["token"]
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "main.py").write_text(skrip)
        (d / "kernel-metadata.json").write_text(
            json.dumps(_meta_kernel(user, slug, "main.py", dataset_sources), indent=2))
        # --timeout: batas keras durasi run di sisi Kaggle (jaring pengaman
        # anti-macet). `timeout=600` di _kg hanya untuk perintah push-nya, bukan
        # run kernelnya — maka keduanya perlu.
        rc, out = _kg(["kernels", "push", "-p", str(d),
                       "--timeout", str(TIMEOUT_KERNEL)], token, timeout=600)
    if rc != 0:
        raise RuntimeError(f"push kernel gagal: {out[-300:]}")
    kid = f"{user}/{slug}"
    return kid, f"https://www.kaggle.com/code/{kid}"


_KATA_KUOTA = ("quota", "exceeded", "limit", "no gpu", "gpu is not available")


def _poll_kernel(akun: dict, kid: str, lapor) -> tuple[str, str]:
    """Poll status sampai selesai. `lapor(status)` dipanggil tiap perubahan.
    Kembalikan (hasil, teks_status_terakhir) di mana hasil ∈ {complete, error,
    timeout}."""
    token = akun["token"]
    t0 = time.time()
    batas = BATAS_JAM * 3600 + 3600         # beri kelonggaran di atas batas leg
    terakhir = ""
    while time.time() - t0 < batas:
        _tidur(20)
        rc, out = _kg(["kernels", "status", kid], token, timeout=120)
        low = out.lower()
        if low != terakhir:
            terakhir = low
            lapor(out.strip()[:200])
        if "complete" in low:
            return "complete", out
        if "error" in low or "cancel" in low:
            return "error", out
    return "timeout", terakhir


def _tarik_keluaran(akun: dict, kid: str, tujuan: Path) -> Path:
    token = akun["token"]
    Path(tujuan).mkdir(parents=True, exist_ok=True)
    rc, out = _kg(["kernels", "output", kid, "-p", str(tujuan)], token, timeout=1800)
    if rc != 0:
        raise RuntimeError(f"tarik output gagal: {out[-300:]}")
    return Path(tujuan)


def _galat_kuota(teks: str) -> bool:
    low = (teks or "").lower()
    return any(k in low for k in _KATA_KUOTA)


def batalkan_remote(settings, kernel_id: str, akun_user: str) -> tuple[bool, str]:
    """Best-effort: HAPUS kernel yang sedang jalan di Kaggle supaya kuota GPU tak
    terus terpakai setelah orang menekan Hentikan. `kernels delete` terbukti
    melenyapkan kernel yang RUNNING (run ikut dibatalkan). Butuh token akun
    pemilik kernel. TIDAK PERNAH melempar — pembatalan lokal tetap berlaku
    walau langkah ini gagal."""
    if not kernel_id or not akun_user:
        return False, "tak ada kernel/akun untuk dibatalkan"
    tok = next((a["token"] for a in akun_pool(settings)
                if a["user"] == akun_user), None)
    if not tok:
        return False, f"token akun {akun_user} tak tersedia di pool"
    try:
        rc, out = _kg(["kernels", "delete", kernel_id], tok, timeout=120,
                      masuk="yes\nyes\n")
    except Exception as e:                       # noqa: BLE001
        return False, str(e)[:160]
    return (rc == 0), out.strip()[-160:]


# ============================================================
# JALUR RF-DETR DI KAGGLE (single-run; reuse helper + ledger)
# ============================================================
#
# Terpisah dari loop YOLO berbilah supaya jalur YOLO-Kaggle yang sudah terbukti
# TAK tersentuh. RF-DETR: unggah dataset COCO (bukan YOLO), kernel pip-install
# rfdetr sendiri, satu run (bukan berbilah — rfdetr nano/small lazim muat dalam
# 12 jam; resume berbilah menyusul). Retry transient + rotasi akun saat kuota
# habis tetap berlaku. Dipanggil dari main() -> ikut except->gagal di sana.

def _rfdetr_kaggle(ds: Path, nomor: int, isi: dict, settings, basis, pool) -> int:
    from . import buatversi, ekspor_coco

    versi = int(isi["versi"])
    versi_dir = buatversi.dir_versi(ds, versi)
    if not (versi_dir / "data.yaml").exists():
        raise FileNotFoundError(
            f"versi v{versi} belum punya data.yaml — tak bisa diekspor ke COCO")
    par = dict(isi.get("par") or {})
    model = isi.get("rfdetr_model") or "nano"
    multigpu = bool(isi.get("rfdetr_multigpu"))   # saklar 2-GPU (T4x2); default mati
    target = int(par.get("epochs") or 0)
    dl = latih.dir_latih(ds, nomor)
    (dl / "weights").mkdir(parents=True, exist_ok=True)

    kag = dict(isi.get("kaggle") or {})
    leg = int(kag.get("leg") or 0)
    # Epoch KUMULATIF lintas-leg dari rekaman (fallback results.csv). Beda dari
    # YOLO: rfdetr me-resume dengan last.ckpt (state penuh), jadi penomoran epoch
    # BERLANJUT — _serap_rfdetr mengembalikan epoch GLOBAL, bukan per-leg.
    epochs_done = int(kag.get("epochs_done") or 0) or _epoch_csv(dl)

    def lapor(**kv):
        kag.update(kv)
        latih.perbarui(ds, nomor, kaggle=dict(kag))

    latih.perbarui(ds, nomor, keadaan="jalan", mulai_pada=_sekarang(),
                   pid=os.getpid(), galat="")
    lapor(leg=leg, epochs_done=epochs_done, pesan="menyiapkan RF-DETR di Kaggle…")

    coco_root = Path(tempfile.mkdtemp(prefix="higolab-coco-"))
    try:
        sumber_coco = coco_root / "coco"
        ekspor_coco.versi_ke_coco(versi_dir, sumber_coco)   # YOLO versi -> COCO
        ds_slug = nama_dataset(ds, versi, "rfdetr")
        judul = f"HIGOLAB {Path(ds).name} v{versi} (COCO)"
        run = f"run-l{nomor}"
        percubaan = 0
        while epochs_done < target:
            akun = _pilih_akun(basis, pool)
            if akun is None:
                latih.perbarui(ds, nomor, keadaan="tertunda", selesai_pada=_sekarang(),
                               galat="kuota semua akun Kaggle menipis — "
                                     "training bisa dilanjutkan nanti atau tambah akun")
                lapor(pesan="tertunda: kuota semua akun menipis")
                return 0

            # Resume kalau leg sebelumnya meninggalkan last.ckpt (state penuh).
            resume = epochs_done > 0 and (dl / "rfdetr" / "last.ckpt").exists()
            par_leg = dict(par)
            par_leg["epochs"] = target        # TOTAL — Lightning lanjut ke max_epochs
            slug_k = nama_kernel(ds, nomor, leg)
            lapor(akun=akun["user"], leg=leg,
                  pesan=f"unggah dataset COCO ke {akun['user']}…")

            dsid = _unggah_dataset(basis, sumber_coco, akun, ds_slug, judul)
            sumber = [dsid]
            if resume:
                ck_slug = _slug(f"higolab-ckdetr-l{nomor}")
                ckid = _unggah_checkpoint_rfdetr(ds, dl, akun, ck_slug)
                sumber.append(ckid)

            skrip = skrip_rfdetr(model, par_leg, run=run, resume=resume,
                                 batas_jam=BATAS_JAM, multigpu=multigpu)
            kid, url = _push_kernel(akun, slug_k, skrip, sumber)
            lapor(kernel=kid, kernel_url=url, remote="queued",
                  pesan=f"RF-DETR {model} leg {leg+1} di Kaggle ({akun['user']})")

            t0 = time.time()
            hasil, teks = _poll_kernel(akun, kid, lambda s: lapor(remote=s))
            _catat_pakai(basis, akun["user"], (time.time() - t0) / 3600.0)

            # Tarik hasil leg; _serap_rfdetr -> epoch GLOBAL + simpan last.ckpt
            # ke dl/rfdetr/ untuk leg berikutnya, best.pt ke weights/.
            ep_global = epochs_done
            with tempfile.TemporaryDirectory() as tmp:
                try:
                    _tarik_keluaran(akun, kid, Path(tmp))
                    ep_global = _serap_rfdetr(Path(tmp), dl)
                except Exception:                           # noqa: BLE001
                    if hasil == "complete":
                        raise
                    ep_global = epochs_done

            if ep_global > epochs_done:                     # leg maju -> lanjut
                epochs_done = ep_global
                leg += 1
                percubaan = 0
                lapor(leg=leg, epochs_done=epochs_done,
                      pesan=f"leg {leg} selesai. {epochs_done}/{target} epoch")
                continue

            if _galat_kuota(teks):
                _tandai_habis(basis, akun["user"])
                lapor(pesan=f"akun {akun['user']} kehabisan kuota. Rotasi ke akun berikut")
                continue
            percubaan += 1
            if percubaan >= MAKS_COBA_LEG:
                raise RuntimeError(f"RF-DETR di Kaggle gagal {percubaan}x: {teks[-200:]}")
            lapor(pesan=f"gagal (mungkin sesaat). Coba ulang {percubaan}/{MAKS_COBA_LEG-1}")

        latih.perbarui(ds, nomor, keadaan="selesai", selesai_pada=_sekarang())
        lapor(epochs_done=epochs_done,
              pesan=f"selesai. RF-DETR {model}, {epochs_done} epoch dalam {leg} leg")
        return 0
    finally:
        shutil.rmtree(coco_root, ignore_errors=True)


# ============================================================
# ORKESTRATOR BERBILAH
# ============================================================

def main() -> int:
    if len(sys.argv) < 3:
        print("pemakaian: latih_kaggle <folder-projek> <nomor>", file=sys.stderr)
        return 2
    ds, nomor = Path(sys.argv[1]), int(sys.argv[2])

    isi = latih.baca(ds, nomor)
    if isi is None:
        print(f"training L{nomor} tidak ada di {ds}", file=sys.stderr)
        return 2

    from ..config import get_settings
    settings = get_settings()
    pool = akun_pool(settings)
    if not pool:
        latih.perbarui(ds, nomor, keadaan="gagal",
                       galat="tidak ada akun Kaggle terkonfigurasi",
                       selesai_pada=_sekarang())
        return 1
    basis = basis_ledger(settings)          # ledger kuota GLOBAL, bukan per-projek

    try:
        # RF-DETR punya jalur sendiri (dataset COCO, kernel rfdetr); jalur YOLO
        # berbilah di bawah tak tersentuh.
        if isi.get("arsitektur") == "rfdetr":
            return _rfdetr_kaggle(ds, nomor, isi, settings, basis, pool)
        from . import buatversi
        versi = int(isi["versi"])
        versi_dir = buatversi.dir_versi(ds, versi)
        if not (versi_dir / "data.yaml").exists():
            raise FileNotFoundError(
                f"versi v{versi} belum punya data.yaml — tak bisa diunggah ke Kaggle")

        par = dict(isi.get("par") or {})
        target = int(par.get("epochs") or 0)
        tugas = isi.get("tugas") or "segment"
        bobot = isi.get("bobot") or "yolov8n.pt"
        dl = latih.dir_latih(ds, nomor)
        (dl / "weights").mkdir(parents=True, exist_ok=True)

        kag = dict(isi.get("kaggle") or {})
        leg = int(kag.get("leg") or 0)
        # Epoch KUMULATIF lintas-leg dipegang di rekaman, BUKAN dibaca dari
        # results.csv: tiap leg lanjutan "mulai dari bobot" memulai penomoran
        # epoch dari 1 lagi (optimizer di-strip, resume murni mustahil — temuan
        # dari kernel resume paragon), jadi results.csv hanya mencatat leg
        # TERAKHIR. Fallback ke results.csv untuk run yang belum mencatat.
        epochs_done = int(kag.get("epochs_done") or 0) or _epoch_csv(dl)

        def lapor(**kv):
            kag.update(kv)
            latih.perbarui(ds, nomor, kaggle=dict(kag))

        # galat="" membersihkan pesan error dari percobaan sebelumnya saat job
        # ini disambung lagi — supaya kartu tak memamerkan galat basi selagi
        # jalan atau setelah akhirnya selesai.
        latih.perbarui(ds, nomor, keadaan="jalan", mulai_pada=_sekarang(),
                       pid=os.getpid(), galat="")
        lapor(leg=leg, epochs_done=epochs_done,
              pesan="menyiapkan training di Kaggle…")

        ds_slug = nama_dataset(ds, versi)
        judul_ds = f"HIGOLAB {Path(ds).name} v{versi}"

        percubaan = 0            # berapa kali leg saat ini gagal transient
        while epochs_done < target:
            akun = _pilih_akun(basis, pool)
            if akun is None:
                latih.perbarui(
                    ds, nomor, keadaan="tertunda", selesai_pada=_sekarang(),
                    galat="kuota semua akun Kaggle menipis minggu ini — "
                          "training bisa dilanjutkan nanti atau tambah akun")
                lapor(pesan="tertunda: kuota semua akun menipis")
                return 0

            resume = epochs_done > 0
            sisa = max(1, target - epochs_done)
            par_leg = dict(par)
            par_leg["epochs"] = sisa
            run = f"run-l{nomor}"
            slug_k = nama_kernel(ds, nomor, leg)
            lapor(akun=akun["user"], leg=leg,
                  pesan=f"unggah dataset ke akun {akun['user']}…")

            dsid = _unggah_dataset(basis, versi_dir, akun, ds_slug, judul_ds)
            sumber = [dsid]
            if resume:
                ck_slug = _slug(f"higolab-ck-l{nomor}")
                ckid = _unggah_checkpoint(ds, dl, akun, ck_slug)
                sumber.append(ckid)

            skrip = skrip_latih(tugas, bobot, par_leg, run=run,
                                batas_jam=BATAS_JAM, resume=resume)
            kid, url = _push_kernel(akun, slug_k, skrip, sumber)
            lapor(kernel=kid, kernel_url=url, remote="queued",
                  pesan=f"leg {leg+1} berjalan di Kaggle (akun {akun['user']})")

            t0 = time.time()
            hasil, teks = _poll_kernel(akun, kid,
                                       lambda s: lapor(remote=s))
            _catat_pakai(basis, akun["user"], (time.time() - t0) / 3600.0)

            # Tarik hasil leg ini. ep_leg = epoch yang DISELESAIKAN leg ini
            # (penomoran leg sendiri), ditambahkan ke kumulatif. best.pt/last.pt/
            # results.csv terbaru mendarat di .latih/L<n>/ — jadi leg berikutnya
            # melanjutkan dari sana dan status()/unduh bobot tetap berlaku.
            ep_leg = 0
            with tempfile.TemporaryDirectory() as tmp:
                try:
                    _tarik_keluaran(akun, kid, Path(tmp))
                    ep_leg = serap_keluaran(Path(tmp), run, dl)
                except Exception as e:                   # noqa: BLE001
                    if hasil == "complete":
                        raise                            # sukses tapi output tak terbaca -> nyata gagal
                    ep_leg = 0                           # leg tak sukses: tak apa kosong

            if ep_leg > 0:                               # leg maju -> lanjut/selesai
                epochs_done += ep_leg
                leg += 1
                percubaan = 0
                lapor(leg=leg, epochs_done=epochs_done,
                      pesan=f"leg {leg} selesai. {epochs_done}/{target} epoch")
                continue

            # Tak ada kemajuan. Kuota habis -> rotasi akun (bukan coba-ulang).
            if _galat_kuota(teks):
                _tandai_habis(basis, akun["user"])
                lapor(pesan=f"akun {akun['user']} kehabisan kuota. "
                            "Rotasi ke akun berikutnya")
                continue
            # Selain kuota: anggap sesaat, coba ulang leg yang SAMA beberapa kali.
            percubaan += 1
            if percubaan >= MAKS_COBA_LEG:
                raise RuntimeError(
                    f"leg {leg+1} gagal {percubaan}x berturut di Kaggle: {teks[-200:]}")
            lapor(pesan=f"leg {leg+1} gagal (mungkin sesaat). Coba ulang "
                        f"{percubaan}/{MAKS_COBA_LEG-1}")
            continue

        latih.perbarui(ds, nomor, keadaan="selesai", selesai_pada=_sekarang())
        lapor(epochs_done=epochs_done,
              pesan=f"selesai. {epochs_done} epoch dalam {leg} leg")
        return 0
    except Exception as e:                       # noqa: BLE001
        latih.perbarui(ds, nomor, keadaan="gagal", galat=str(e)[:300],
                       selesai_pada=_sekarang())
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
