"""
Proses yang benar-benar melatih CLASSIFIER AKSI. Dijalankan latih_aksi.jalankan()
sebagai SUBPROSES TERLEPAS:

    python -m app.services.latih_aksi_jalan <folder-projek> <nomor>

Alasan berkas tersendiri sama persis dengan latih_jalan.py: ia harus hidup
melewati matinya server, namanya muncul di /proc/<pid>/cmdline untuk latih_aksi.
hidup(), dan torch/transformers hanya diimpor di sini (proses terpisah) jadi
server CPU tak pernah membayar ongkosnya.

DUA HAL YANG MEMBEDAKANNYA DARI latih_jalan.py — keduanya demi tidak merusak
training gambar yang berbagi satu kartu (rencana G#9):

  1. KUNCI GPU TERPISAH. flock-nya `/tmp/labelapp-latih-aksi.lock`, BUKAN
     `labelapp-latih-gpu.lock` milik jalur gambar. Dengan begitu training aksi
     mengantre di LAJURNYA SENDIRI: dua training aksi tetap bergantian (satu
     kartu, satu pada satu waktu), tetapi training aksi tak pernah menunggui —
     atau lebih buruk, ikut mengisi VRAM bersama — training gambar.

  2. PENJAGA VRAM. Diport dari posec3d/konfigurasi.periksa_vram: sebelum memuat
     model, periksa sisa VRAM. Kalau ada proses lain (mis. training gambar)
     sedang memakai kartu sehingga sisa VRAM di bawah ambang, TUNGGU sampai lega
     (berbatas waktu), lalu kalau tetap sempit JATUH KE CPU — tak pernah memaksa
     masuk dan membuat dua-duanya kehabisan memori.

KONTRAK KEMAJUAN
Tiap epoch, trainer memanggil progress callback yang MENULIS SATU BARIS ke
results.csv di folder run (lihat latih_aksi.baca_hasil_csv untuk kolomnya), jadi
kemajuan bertahan melewati server yang menyala ulang, persis jalur gambar.
"""
from __future__ import annotations

import fcntl
import os
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

from . import klip_olah, latih_aksi

# Kunci BERBEDA dari jalur gambar — inilah lajur GPU tersendiri untuk aksi.
KUNCI_GPU = Path(tempfile.gettempdir()) / "labelapp-latih-aksi.lock"

# Penjaga VRAM (diport dari posec3d/konfigurasi.py). Di bawah ambang ini, kartu
# dianggap sedang dipakai proses lain dan training aksi menunggu / jatuh ke CPU.
MIN_VRAM_BEBAS_MB = 2800
TUNGGU_VRAM_MAKS_DETIK = 1800      # tunggu lega sampai 30 menit, lalu pakai CPU


def _sekarang() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ============================================================
# PENJAGA VRAM  (diport dari konfigurasi.periksa_vram)
# ============================================================

def periksa_vram(butuh_mb: int = MIN_VRAM_BEBAS_MB, diam: bool = False) -> str:
    """'cuda' bila aman, 'cpu' bila tidak. Tidak pernah melempar.

    Diport apa adanya dari posec3d/konfigurasi.periksa_vram: melindungi training
    lain yang sedang memakai kartu. torch diimpor DI DALAM fungsi supaya modul
    ini tetap bisa diimpor di server CPU tanpa torch.
    """
    try:
        import torch

        if not torch.cuda.is_available():
            if not diam:
                print("  [GPU] CUDA tidak tersedia, memakai CPU", flush=True)
            return "cpu"
        bebas, total = torch.cuda.mem_get_info()
        bebas_mb = bebas / 1024 / 1024
        if bebas_mb < butuh_mb:
            if not diam:
                print(f"  [GPU] sisa VRAM {bebas_mb:.0f} MB < ambang {butuh_mb} MB "
                      "— ada proses lain memakai GPU.", flush=True)
            return "cpu"
        if not diam:
            print(f"  [GPU] sisa VRAM {bebas_mb:.0f} MB dari "
                  f"{total/1024/1024:.0f} MB, aman", flush=True)
        return "cuda"
    except Exception as e:                       # noqa: BLE001
        if not diam:
            print(f"  [GPU] gagal memeriksa ({e}), memakai CPU", flush=True)
        return "cpu"


def tunggu_vram(butuh_mb: int = MIN_VRAM_BEBAS_MB) -> str:
    """Pilih perangkat sambil MELINDUNGI training lain: kalau CUDA ada tetapi
    VRAM sempit (proses lain sedang memakainya), tunggu sampai lega — berbatas
    TUNGGU_VRAM_MAKS_DETIK — lalu kalau tetap sempit, jatuh ke CPU. Tak pernah
    memaksa masuk GPU yang sedang penuh dan membuat dua-duanya OOM."""
    try:
        import torch

        if not torch.cuda.is_available():
            return "cpu"
    except Exception:                            # noqa: BLE001
        return "cpu"

    t0 = time.time()
    lapor = 0.0
    while True:
        dev = periksa_vram(butuh_mb, diam=True)
        if dev == "cuda":
            periksa_vram(butuh_mb)               # cetak sekali keterangan amannya
            return "cuda"
        lewat = time.time() - t0
        if lewat >= TUNGGU_VRAM_MAKS_DETIK:
            print("  [GPU] VRAM tetap sempit setelah menunggu — memakai CPU "
                  "(pelan, tetapi tak mengganggu training lain).", flush=True)
            return "cpu"
        if lewat - lapor >= 30:
            lapor = lewat
            print(f"  [GPU] menunggu VRAM lega… ({int(lewat)} dtk)", flush=True)
        time.sleep(10)


# ============================================================
# KEMAJUAN -> results.csv  (kontrak di latih_aksi.baca_hasil_csv)
# ============================================================
_KOLOM = ("epoch", "train_loss", "train_acc", "val_loss", "val_acc",
          "val_acc_ma", "detik")


def _buat_pencatat(run_dir: Path, t0: float):
    """Kembalikan fungsi callback(epoch, **nilai) yang MENAMBAH satu baris ke
    results.csv. Header ditulis sekali. Ditulis via berkas sementara + replace
    supaya pembaca tak pernah melihat baris setengah jadi."""
    csv = run_dir / "results.csv"
    baris: list[str] = [",".join(_KOLOM)]
    if csv.exists():
        try:
            baris = csv.read_text().strip().splitlines() or baris
        except OSError:
            pass

    def catat(epoch: int, **nilai) -> None:
        nilai.setdefault("detik", round(time.time() - t0, 1))
        sel = []
        for k in _KOLOM:
            v = epoch if k == "epoch" else nilai.get(k)
            sel.append("" if v is None else (f"{v:.4f}" if isinstance(v, float)
                                             else str(v)))
        baris.append(",".join(sel))
        tmp = csv.with_suffix(".csv.tmp")
        tmp.write_text("\n".join(baris) + "\n")
        os.replace(tmp, csv)

    return catat


# ============================================================
# UTAMA
# ============================================================

def _verifikasi_versi(ds: Path, nomor_versi: int) -> Path:
    """Folder versi klip aksi yang terbangun, atau melempar dengan pesan jelas.
    Juga memeriksa valid/ bersih dari _aug_/_bal_ — kalau build bocor, angka
    validasinya menipu (invarian #2 klip_olah), lebih baik gagal keras."""
    vd = klip_olah.dir_versi(ds, nomor_versi)
    if not (vd / "aksi.yaml").is_file():
        raise FileNotFoundError(
            f"versi v{nomor_versi} belum terbangun (aksi.yaml tak ada di {vd}) — "
            "bangun dulu versinya di halaman Label Aksi")
    td = vd / "train"
    if not (td.is_dir() and any(td.rglob("*.mp4"))):
        raise FileNotFoundError(
            f"versi v{nomor_versi} tak punya klip train — versinya kosong")
    vval = vd / "valid"
    if vval.is_dir():
        kotor = [p.name for p in vval.rglob("*.mp4")
                 if "_aug_" in p.stem or "_bal_" in p.stem]
        if kotor:
            raise ValueError(
                f"valid/ versi v{nomor_versi} memuat {len(kotor)} klip augmentasi "
                f"(mis. {kotor[0]}) — angka validasi akan menipu")
    return vd


def main() -> int:
    if len(sys.argv) < 3:
        print("pemakaian: latih_aksi_jalan <folder-projek> <nomor>",
              file=sys.stderr)
        return 2
    ds, nomor = Path(sys.argv[1]), int(sys.argv[2])

    isi = latih_aksi.baca(ds, nomor)
    if isi is None:
        print(f"training aksi A{nomor} tidak ada di {ds}", file=sys.stderr)
        return 2

    backend = isi.get("backend") or ""
    try:
        vd = _verifikasi_versi(ds, int(isi["versi"]))
    except Exception as e:                       # noqa: BLE001
        latih_aksi.perbarui(ds, nomor, keadaan="gagal", galat=str(e)[:300],
                            selesai_pada=_sekarang())
        print(f"GAGAL sebelum mulai: {e}", file=sys.stderr)
        return 1

    # ---- antre: satu training aksi pada satu waktu, di lajur GPU SENDIRI ----
    KUNCI_GPU.touch(exist_ok=True)
    f = open(KUNCI_GPU, "r+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print("  giliran (lajur aksi) langsung didapat", flush=True)
    except OSError:
        print("  menunggu giliran — ada training AKSI lain memakai GPU",
              flush=True)
        latih_aksi.perbarui(ds, nomor, keadaan="antre")
        fcntl.flock(f, fcntl.LOCK_EX)
        print("  giliran didapat", flush=True)

    t0 = time.time()
    run_dir = latih_aksi.dir_latih(ds, nomor)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "weights").mkdir(exist_ok=True)
    catat = _buat_pencatat(run_dir, t0)

    try:
        # Penjaga VRAM: lindungi training lain yang mungkin sedang memakai kartu.
        latih_aksi.perbarui(ds, nomor, keadaan="antre", pid=os.getpid())
        device = tunggu_vram()

        latih_aksi.perbarui(ds, nomor, keadaan="jalan", mulai_pada=_sekarang(),
                            pid=os.getpid())
        par = dict(isi.get("par") or {})
        print(f"  backend : {backend}", flush=True)
        print(f"  versi   : {vd}", flush=True)
        print(f"  device  : {device}  | epochs {par.get('epochs')} "
              f"batch {par.get('batch')}", flush=True)

        # Dispatch ke trainer per backend. Diimpor DI SINI (lazy) supaya modul
        # runner ini tetap bisa diimpor di CPU: tiap trainer mengimpor torch dkk
        # hanya di dalam fungsinya.
        if backend == "videomae":
            from . import latih_aksi_videomae as trainer
        elif backend == "slowfast":
            from . import latih_aksi_slowfast as trainer
        elif backend == "posec3d":
            from . import latih_aksi_posec3d as trainer
        else:
            raise ValueError(f"backend tak dikenal: {backend!r}")

        trainer.latih(versi_dir=vd, run_dir=run_dir, par=par,
                      progres=catat, device=device, ds=ds)

        best = run_dir / "weights" / "best.pt"
        if not best.exists():
            # Trainer selesai tanpa melempar tetapi tak menyimpan checkpoint —
            # dataset mungkin terlalu kecil / satu kelas. Jangan tandai "selesai"
            # yang menyesatkan.
            latih_aksi.perbarui(ds, nomor, keadaan="gagal", selesai_pada=_sekarang(),
                                galat="training selesai tanpa checkpoint best.pt "
                                      "— periksa jumlah klip per kelas")
            print("  GAGAL: tak ada best.pt tersimpan", file=sys.stderr)
            return 1
        latih_aksi.perbarui(ds, nomor, keadaan="selesai", selesai_pada=_sekarang(),
                            detik_total=round(time.time() - t0, 1))
        print(f"\n  SELESAI dalam {int(time.time() - t0)} detik", flush=True)
        print(f"  bobot terbaik: {best}", flush=True)
        return 0
    except KeyboardInterrupt:
        latih_aksi.perbarui(ds, nomor, keadaan="batal", selesai_pada=_sekarang())
        return 130
    except Exception as e:                       # noqa: BLE001
        latih_aksi.perbarui(ds, nomor, keadaan="gagal", galat=str(e)[:300],
                            selesai_pada=_sekarang())
        traceback.print_exc()
        return 1
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
            f.close()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
