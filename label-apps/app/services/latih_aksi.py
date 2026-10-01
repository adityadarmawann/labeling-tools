"""
Registri pelatihan CLASSIFIER AKSI VIDEO — padanan latih.py untuk projek video.

Bentuknya DISENGAJA sama persis dengan latih.py (registri JSON + subproses
terlepas + kunci berkas + kemajuan dibaca dari disk), karena ketiga alasan yang
membuat bentuk itu benar di sana berlaku kata per kata di sini:

  1. TRAINING SEBAGAI SUBPROSES, BUKAN THREAD. Melatih VideoMAE/SlowFast/PoseC3D
     memakan menit sampai jam. Server dev menyala ulang tiap berkas berubah;
     thread di dalamnya ikut mati dan membuang pekerjaan setengah jalan.
     Subproses dengan sesi sendiri bertahan melewati itu.

  2. KEMAJUAN DIBACA DARI DISK. Tak ada objek di memori yang bisa ditanyai
     sesudah server menyala ulang, jadi tiap epoch trainer menulis satu baris ke
     results.csv di folder run-nya, dan status() membacanya — sama seperti
     Ultralytics menulis results.csv untuk jalur gambar.

  3. SATU LAJUR GPU SENDIRI. Training aksi memakai KUNCI berkas yang BERBEDA dari
     training gambar (lihat latih_aksi_jalan.KUNCI_GPU). Itu disengaja: training
     aksi mengantre di lajurnya sendiri dan TAK PERNAH mematikan training gambar
     yang sedang berjalan — keduanya berbagi satu kartu, dan penjaga VRAM di
     runner menolak jalan kalau sisa VRAM di bawah ambang (rencana G#9).

APA YANG BERBEDA DARI latih.py

  * Berkasnya berawalan A (`.latih/A<n>.json`, run dir `.latih/A<n>/`) supaya
    HIDUP BERDAMPINGAN dengan training gambar (`L<n>`) di folder .latih yang
    sama tanpa saling menimpa nomor.
  * Sumbernya versi KLIP AKSI (`.versi/vN/` folder-per-kelas + aksi.yaml), bukan
    data.yaml YOLO. Daftar kelas dibekukan dari aksi.yaml versinya saat siapkan.
  * Tiga backend (videomae/slowfast/posec3d) dengan prasyarat pustaka berbeda;
    siap_latih_aksi() melaporkannya PER BACKEND supaya form hanya menawarkan yang
    benar-benar bisa jalan di server ini.

KONTRAK results.csv (ditulis trainer, dibaca baca_hasil_csv):

    epoch,train_loss,train_acc,val_loss,val_acc,val_acc_ma,detik

  `val_acc_ma` adalah metrik UTAMA pemilih checkpoint terbaik — rata-rata
  bergerak akurasi val untuk VideoMAE/SlowFast, akurasi RATA-RATA KELAS untuk
  PoseC3D (keduanya ditulis ke kolom yang sama supaya pembacaannya seragam).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from ..log import catat
from . import latih  # hanya untuk statistik() GPU/RAM — TIDAK mengimpor torch

log = catat("labelapp.latih_aksi")

FOLDER = ".latih"            # folder yang SAMA dengan training gambar (A* vs L*)
MAKS_NAMA = 60
MAKS_CATATAN = 300

_kunci = threading.Lock()


# ============================================================
# BACKEND
# ============================================================
# Tiap backend: nama tampil, pustaka yang WAJIB bisa diimpor supaya ia jalan di
# server ini, dan parameter bawaannya. `butuh` dipakai siap_latih_aksi() untuk
# memutuskan apakah form boleh menawarkannya — diperiksa lewat find_spec, jadi
# TAK satu pun pustaka berat benar-benar diimpor di proses server.
#
# PoseC3D SENGAJA hanya menuntut ultralytics+torch: ia memakai posec3d_model.py
# standalone (bukan mmcv/mmaction2/mmpose), persis acuan action-v14 — pustaka mm*
# terkenal rapuh terhadap versi torch/CUDA, jadi tidak diadopsi (rencana G#1).
BACKEND: dict[str, dict] = {
    "videomae": {
        "nama": "VideoMAE",
        "butuh": ("torch", "transformers", "pytorchvideo", "av"),
        "par": {"epochs": 80, "batch": 6},
        "ket": "Transformer video (RGB). Terkuat; paling berat (~86 jt param).",
    },
    "slowfast": {
        "nama": "SlowFast-R50",
        "butuh": ("torch", "pytorchvideo"),
        "par": {"epochs": 80, "batch": 2},
        "ket": "Dua jalur lambat+cepat (RGB). Lebih berat VRAM dari VideoMAE.",
    },
    "posec3d": {
        "nama": "PoseC3D",
        "butuh": ("torch", "ultralytics"),
        "par": {"epochs": 60, "batch": 16},
        "ket": "Berbasis skeleton (pose + bola). Teringan (~8 jt param), tanpa mmcv.",
    },
}

# Yang boleh diubah orang lewat form: hanya epoch & batch. Resep lainnya
# (LR/LLRD/loss/augmentasi) dibekukan di tiap trainer — angka yang dibayar mahal
# pada acuan, dan membukanya sebagai kotak isian hanya mengundang salah ketik
# yang baru ketahuan berjam-jam kemudian (pelajaran yang sama dengan latih.BATAS).
BATAS: dict[str, tuple] = {
    "epochs": (1, 1000),
    "batch": (1, 64),
}


# ============================================================
# BERKAS
# ============================================================

def _dir(ds) -> Path:
    return Path(ds) / FOLDER


def dir_latih(ds, nomor: int) -> Path:
    return _dir(ds) / f"A{int(nomor)}"


def berkas_latih(ds, nomor: int) -> Path:
    return _dir(ds) / f"A{int(nomor)}.json"


def nomor_berikut(ds) -> int:
    d = _dir(ds)
    if not d.is_dir():
        return 1
    n = 0
    for p in d.glob("A*.json"):
        m = re.fullmatch(r"A(\d+)", p.stem)
        if m:
            n = max(n, int(m.group(1)))
    return n + 1


def baca(ds, nomor: int) -> dict | None:
    p = berkas_latih(ds, nomor)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def _tulis(ds, nomor: int, isi: dict) -> None:
    p = berkas_latih(ds, nomor)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(isi, indent=1))
    os.replace(tmp, p)


def perbarui(ds, nomor: int, **nilai) -> dict:
    with _kunci:
        isi = baca(ds, nomor) or {}
        isi.update(nilai)
        _tulis(ds, nomor, isi)
        return isi


def bobot_training(ds, nomor: int, jenis: str = "best") -> Path | None:
    """best.pt / last.pt hasil sebuah training aksi. None kalau belum ada."""
    j = "last" if str(jenis).lower() == "last" else "best"
    p = dir_latih(ds, nomor) / "weights" / f"{j}.pt"
    return p if p.exists() else None


# ============================================================
# KELAS VERSI AKSI  (dibekukan saat siapkan)
# ============================================================

def kelas_versi_aksi(ds, nomor: int) -> list[str]:
    """Daftar kelas sebuah versi KLIP AKSI, urut indeks — dari MANIFES.json
    (otoritatif, JSON bersih) dengan aksi.yaml sebagai cadangan.

    Dipakai siapkan() untuk MEMBEKUKAN kelas yang dikenal model, sama alasannya
    dengan latih: versinya bisa dihapus belakangan, dan keterangan "model ini
    kenal kelas apa saja" harus tetap terbaca sesudah itu.
    """
    from . import klip_olah

    vd = klip_olah.dir_versi(Path(ds), int(nomor))
    man = vd / "MANIFES.json"
    if man.is_file():
        try:
            m = json.loads(man.read_text(encoding="utf-8"))
            if m.get("jenis") == "aksi":
                k = ((m.get("aksi") or {}).get("kelas")) or []
                if k:
                    return [str(x) for x in k]
        except (OSError, ValueError):
            pass
    # Cadangan: baca names: [...] dari aksi.yaml.
    ay = vd / "aksi.yaml"
    if ay.is_file():
        try:
            import yaml as _yaml

            y = _yaml.safe_load(ay.read_text(encoding="utf-8")) or {}
            k = y.get("names") or []
            if k:
                return [str(x) for x in k]
        except Exception:                        # noqa: BLE001
            pass
    return []


def versi_aksi_siap(ds, nomor: int) -> bool:
    """Versi klip aksi itu BENAR-BENAR terbangun dan bisa dilatih: folder versi
    ada + aksi.yaml + minimal satu klip train. Dicek sebelum meluncurkan supaya
    penolakannya jelas, bukan subproses yang jatuh di baris scan dataset."""
    from . import klip_olah

    vd = klip_olah.dir_versi(Path(ds), int(nomor))
    if not (vd / "aksi.yaml").is_file():
        return False
    td = vd / "train"
    return td.is_dir() and any(td.rglob("*.mp4"))


# ============================================================
# MEMBACA KEMAJUAN DARI DISK  (kontrak results.csv)
# ============================================================

def _angka(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def baca_hasil_csv(d: Path) -> dict:
    """Ringkas results.csv run training aksi: epoch selesai + metrik + kurva.

    Kolom yang dipahami: epoch, train_loss, train_acc, val_loss, val_acc,
    val_acc_ma, detik. Hanya baris LENGKAP yang dibaca — baris terakhir bisa
    separuh ditulis selagi trainer menulisnya, dan memunculkan angka ngawur di
    layar untuk satu detik jauh lebih buruk daripada melewatinya (padanan
    latih.baca_hasil_csv)."""
    p = Path(d) / "results.csv"
    kosong = {"epoch": 0, "baris": [], "metrik": {}, "terbaik": {}, "kurva": [],
              "utama": "val_acc_ma", "detik": 0.0}
    if not p.exists():
        return kosong
    try:
        teks = p.read_text().strip().splitlines()
    except OSError:
        return kosong
    if len(teks) < 2:
        return kosong

    kepala = [k.strip() for k in teks[0].split(",")]
    baris = []
    for ln in teks[1:]:
        sel = [s.strip() for s in ln.split(",")]
        if len(sel) != len(kepala):
            continue                      # baris yang sedang separuh ditulis
        baris.append(dict(zip(kepala, sel)))
    if not baris:
        return kosong

    akhir = baris[-1]
    # Metrik yang ditampilkan: nilai TERAKHIR; `terbaik` = maksimum sepanjang
    # run (checkpoint best.pt mengikuti yang terbaik, bukan yang terakhir).
    LABEL = (("val_acc_ma", "akurasi val (MA/kelas)"),
             ("val_acc", "akurasi val"),
             ("train_acc", "akurasi latih"))
    metrik, terbaik = {}, {}
    for kol, label in LABEL:
        v = _angka(akhir.get(kol))
        if v is None:
            continue
        metrik[label] = v
        nilai = [x for x in (_angka(b.get(kol)) for b in baris) if x is not None]
        if nilai:
            terbaik[label] = max(nilai)

    kurva = []
    langkah = max(1, len(baris) // 120)
    for b in baris[::langkah]:
        kurva.append({
            "epoch": _angka(b.get("epoch")) or 0,
            "nilai": _angka(b.get("val_acc_ma")) if b.get("val_acc_ma") is not None
            else _angka(b.get("val_acc")),
            "loss": _angka(b.get("train_loss")),
        })
    return {"epoch": int(_angka(akhir.get("epoch")) or len(baris)),
            "metrik": metrik, "terbaik": terbaik, "kurva": kurva,
            "utama": "akurasi val (MA/kelas)",
            "detik": _angka(akhir.get("detik")) or 0.0}


def hidup(pid) -> bool:
    """Proses training aksi itu masih berjalan atau tidak.

    Memeriksa /proc/<pid>/cmdline berisi 'latih_aksi_jalan' — bukan sekadar pid
    hidup. pid dipakai ulang sistem, dan tanpa pemeriksaan ini training yang
    sudah mati bisa terlihat berjalan selamanya. Penanda 'latih_aksi_jalan'
    BERBEDA dari 'latih_jalan' milik jalur gambar, jadi keduanya tak tertukar."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf8", "replace")
        return "latih_aksi_jalan" in cmd
    except OSError:
        return True


def statistik() -> dict:
    """GPU + RAM sekarang. Dipinjam apa adanya dari jalur gambar — bar yang sama
    menolong orang menakar apakah kartu sedang sibuk sebelum menekan Jalankan."""
    return latih.statistik()


# ============================================================
# STATUS SATU TRAINING
# ============================================================
# antre   -> subprosesnya hidup tetapi menunggu kunci GPU / VRAM
# jalan   -> sedang melatih
# selesai -> berhenti wajar, checkpoint best ada
# gagal   -> berhenti dengan galat
# batal   -> dihentikan orang
# hilang  -> prosesnya tak ada lagi padahal statusnya masih jalan (mesin mati di
#            tengah training) — dinamai sendiri lebih jujur daripada "berjalan"
#            selamanya.
BERJALAN = ("antre", "jalan")


def status(ds, nomor: int) -> dict:
    isi = baca(ds, nomor)
    if isi is None:
        return {}
    d = dir_latih(ds, nomor)
    best = d / "weights" / "best.pt"
    keadaan = isi.get("keadaan") or "antre"
    if keadaan in BERJALAN and not hidup(isi.get("pid")):
        keadaan = "selesai" if best.exists() else "hilang"
        isi = perbarui(ds, nomor, keadaan=keadaan,
                       selesai_pada=isi.get("selesai_pada")
                       or datetime.now().strftime("%Y-%m-%d %H:%M"))

    csv = baca_hasil_csv(d)
    epochs = int((isi.get("par") or {}).get("epochs") or 0)
    ep = csv.get("epoch") or 0
    persen = min(100.0, round(ep / epochs * 100, 1)) if epochs else 0.0
    detik = csv.get("detik") or 0.0
    sisa = (detik / ep * (epochs - ep)) if (ep and epochs > ep) else 0.0

    kelas = isi.get("kelas")
    if not kelas and isi.get("versi"):
        kelas = kelas_versi_aksi(ds, int(isi["versi"]))

    bk = BACKEND.get(isi.get("backend") or "", {})
    out = {
        **{k: isi.get(k) for k in
           ("nomor", "nama", "catatan", "versi", "backend", "oleh",
            "dibuat", "selesai_pada", "galat", "par")},
        "backend_nama": bk.get("nama") or isi.get("backend") or "",
        "kelas": kelas or [],
        "keadaan": keadaan,
        "epoch": ep, "epochs": epochs, "persen": persen,
        "detik": detik, "sisa": sisa,
        "metrik": csv.get("metrik") or {},
        "terbaik": csv.get("terbaik") or {},
        "utama": csv.get("utama"),
        "punya_bobot": best.exists(),
        "punya_last": (d / "weights" / "last.pt").exists(),
    }
    return out


def daftar(ds) -> list[dict]:
    """Semua training aksi di projek ini, terbaru dulu."""
    d = _dir(ds)
    if not d.is_dir():
        return []
    nomor = []
    for p in d.glob("A*.json"):
        m = re.fullmatch(r"A(\d+)", p.stem)
        if m:
            nomor.append(int(m.group(1)))
    return [s for s in (status(ds, n) for n in sorted(nomor, reverse=True)) if s]


def ada_yang_jalan(ds) -> dict | None:
    for s in daftar(ds):
        if s.get("keadaan") in BERJALAN:
            return s
    return None


# ============================================================
# KESIAPAN PER BACKEND
# ============================================================

def _pustaka_ada(nama: str) -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec(nama) is not None
    except Exception:                            # noqa: BLE001
        return False


def siap_latih_aksi() -> dict:
    """Backend mana yang BISA jalan di server ini, PER BACKEND beserta alasannya.

    Diperiksa SEBELUM form ditampilkan, bukan sesudah orang menekan Jalankan:
    pustaka berat (transformers/pytorchvideo/av/ultralytics/torch) hanya ada di
    .venv-gpu. Tanpa ini, backend yang pustakanya kurang tetap ditawarkan,
    subprosesnya jatuh di baris import, lalu kartunya muncul "gagal" beberapa
    detik kemudian — dan orang tak punya cara tahu yang kurang sebuah paket,
    bukan datanya.

    Diperiksa lewat find_spec, jadi TAK satu pun pustaka berat benar-benar
    diimpor di proses server (CPU) ini. Form hanya menawarkan backend yang siap.
    """
    out: dict[str, dict] = {}
    for key, info in BACKEND.items():
        kurang = [p for p in info["butuh"] if not _pustaka_ada(p)]
        siap = not kurang
        if siap:
            alasan = ""
        else:
            alasan = (
                f"{info['nama']} butuh {', '.join(info['butuh'])} yang belum "
                f"terpasang di server ini ({', '.join(kurang)} hilang). Nyalakan "
                "server dengan venv GPU (requirements-gpu.txt: transformers, "
                "pytorchvideo, av); melatih di CPU memakan berjam-jam.")
        out[key] = {"nama": info["nama"], "ket": info["ket"], "butuh":
                    list(info["butuh"]), "siap": siap, "alasan": alasan,
                    "kurang": kurang, "par": dict(info["par"])}
    return out


def ada_backend_siap() -> bool:
    return any(v["siap"] for v in siap_latih_aksi().values())


# ============================================================
# MENYIAPKAN + MENJALANKAN
# ============================================================

def _saring_par(backend: str, minta: dict) -> tuple[dict, list[str]]:
    """Ambil par bawaan backend lalu timpa epochs/batch yang diminta, dijepit
    ke BATAS. Kunci selain epochs/batch diabaikan — resep lain dibekukan trainer."""
    bk = BACKEND.get(backend) or {}
    par = dict(bk.get("par") or {})
    galat: list[str] = []
    for k in ("epochs", "batch"):
        if k not in (minta or {}):
            continue
        lo, hi = BATAS[k]
        try:
            v = int(minta[k])
        except (TypeError, ValueError):
            galat.append(f"{k} bukan angka")
            continue
        if not (lo <= v <= hi):
            galat.append(f"{k} harus antara {lo} dan {hi}")
            continue
        par[k] = v
    return par, galat


def siapkan(ds, *, versi_nomor: int, backend: str, par: dict, oleh: str,
            nama: str = "", catatan: str = "") -> dict:
    """Catat satu training aksi baru. Belum dijalankan.

    Membekukan: daftar kelas (dari aksi.yaml versinya), backend, dan par — sama
    alasannya dengan latih.siapkan: versinya bisa dihapus belakangan, dan rincian
    training harus tetap terbaca sesudah itu.
    """
    backend = str(backend or "").strip().lower()
    if backend not in BACKEND:
        raise ValueError(f"backend harus salah satu dari {tuple(BACKEND)}")
    par_bersih, galat = _saring_par(backend, par or {})
    if galat:
        raise ValueError("; ".join(galat))

    kelas = kelas_versi_aksi(ds, int(versi_nomor))
    n = nomor_berikut(ds)
    isi = {
        "nomor": n,
        "nama": " ".join((nama or "").split())[:MAKS_NAMA]
        or f"{BACKEND[backend]['nama']} v{int(versi_nomor)}",
        "catatan": " ".join((catatan or "").split())[:MAKS_CATATAN],
        "versi": int(versi_nomor),
        "backend": backend,
        "kelas": kelas,
        "par": par_bersih,
        "oleh": oleh,
        "dibuat": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "keadaan": "antre",
        "pid": 0,
    }
    _tulis(ds, n, isi)
    return isi


def jalankan(ds, nomor: int) -> dict:
    """Luncurkan subproses training aksi yang terlepas dari server."""
    isi = baca(ds, nomor)
    if isi is None:
        raise ValueError(f"training A{nomor} tidak ada")
    d = dir_latih(ds, nomor)
    d.mkdir(parents=True, exist_ok=True)
    log_p = _dir(ds) / f"A{nomor}.log"

    akar = Path(__file__).resolve().parents[2]
    perintah = [sys.executable, "-m", "app.services.latih_aksi_jalan",
                str(Path(ds).resolve()), str(nomor)]
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(akar))
    env["YOLO_VERBOSE"] = "False"

    with open(log_p, "ab", buffering=0) as f:
        f.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} mulai A{nomor} ===\n"
                .encode())
        p = subprocess.Popen(perintah, cwd=str(akar), env=env,
                             stdout=f, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL,
                             # Sesi sendiri: subproses TIDAK ikut mati ketika
                             # server dimatikan / --reload menyalakannya ulang.
                             start_new_session=True)
    log.info("training aksi A%s dimulai di %s (pid %s)", nomor, Path(ds).name,
             p.pid)
    return perbarui(ds, nomor, pid=p.pid, keadaan="antre")


def batalkan(ds, nomor: int) -> dict:
    """Hentikan training aksi yang sedang berjalan."""
    isi = baca(ds, nomor) or {}
    pid = isi.get("pid") or 0
    if hidup(pid):
        try:
            # Seluruh grup: trainer memakai worker DataLoader, mematikan induknya
            # saja meninggalkan mereka menggantung.
            os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError) as e:
            log.warning("gagal menghentikan A%s: %s", nomor, e)
    return perbarui(ds, nomor, keadaan="batal",
                    selesai_pada=datetime.now().strftime("%Y-%m-%d %H:%M"))


def buang(ds, nomor: int) -> bool:
    """Hapus satu training aksi beserta checkpoint & skeleton .pkl-nya."""
    isi = baca(ds, nomor)
    if isi is None:
        return False
    if isi.get("keadaan") in BERJALAN and hidup(isi.get("pid")):
        raise ValueError("training itu masih berjalan — hentikan dulu")
    shutil.rmtree(dir_latih(ds, nomor), ignore_errors=True)
    # Sapu SEMUA berkas milik nomor ini dengan pola (A<n>.json, A<n>.log,
    # A<n>.json.tmp). Nomor tak pernah dipakai ulang, jadi pola A<n>.* tak
    # mungkin mengenai training lain — dan pola A* tak akan menyentuh L* (gambar).
    for x in _dir(ds).glob(f"A{int(nomor)}.*"):
        if x.is_file():
            x.unlink(missing_ok=True)
    return True


def ekor_log(ds, nomor: int, baris: int = 40) -> str:
    p = _dir(ds) / f"A{nomor}.log"
    if not p.exists():
        return ""
    try:
        return "\n".join(p.read_text(errors="replace").splitlines()[-baris:])
    except OSError:
        return ""
