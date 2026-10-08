"""
Proses yang melatih RF-DETR (arsitektur training KEDUA, di samping YOLO/
ultralytics). Dijalankan latih.jalankan() sebagai SUBPROSES TERLEPAS, pola yang
sama persis dengan latih_jalan.py:

    python -m app.services.latih_rfdetr <folder-projek> <nomor>

Jalan di bawah interpreter server (.venv-gpu) — di situ rfdetr DAN ultralytics
hidup berdampingan (torch dipakai bersama; lihat requirements-gpu.txt). `rfdetr`
diimpor LAZY (di dalam _latih_rfdetr_asli), jadi modul ini AMAN diimpor di
.venv CPU (tanpa rfdetr) untuk suite.

Alur: ekspor versi -> COCO per-split (ekspor_coco) -> RF-DETR.train() ->
serap best checkpoint + metrik ke .latih/L<n>/ (konvensi yang sama dengan jalur
YOLO: weights/best.pt + results.csv), sehingga status()/unduh bobot/kartu
berlaku tanpa pembedaan.

SATU GPU, SATU TRAINING: memakai flock yang SAMA dengan latih_jalan
(/tmp/labelapp-latih-gpu.lock) supaya RF-DETR lokal dan YOLO lokal bergantian,
tidak berebut VRAM.
"""
from __future__ import annotations

import fcntl
import shutil
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

from . import latih

KUNCI_GPU = Path(tempfile.gettempdir()) / "labelapp-latih-gpu.lock"
# Peta ukuran -> kelas RF-DETR; diimpor lazy agar modul aman di venv tanpa rfdetr.
_KELAS_RFDETR = {"nano": "RFDETRNano", "small": "RFDETRSmall",
                 "medium": "RFDETRMedium", "large": "RFDETRLarge"}


def _sekarang() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _snap56(x: int) -> int:
    """Resolusi RF-DETR wajib kelipatan 56 (patch 14 x num_windows 4). Jepit ke
    kelipatan 56 terdekat, minimal 56."""
    x = max(56, int(round(float(x) / 56.0)) * 56)
    return x


def _latih_rfdetr_asli(coco_dir: Path, out_dir: Path, model: str, par: dict,
                       lapor) -> dict:
    """Jalankan training RF-DETR SUNGGUHAN. Satu-satunya tempat `rfdetr` diimpor
    -> seam yang dipalsukan di tes. Kembalikan {"epoch": n, "map": x|None}."""
    import rfdetr

    Model = getattr(rfdetr, _KELAS_RFDETR.get(model, "RFDETRNano"))
    res = _snap56(int(par.get("resolution") or 560))
    lapor(pesan=f"melatih RF-DETR {model} @ {res}px…")
    m = Model(resolution=res)
    m.train(
        dataset_dir=str(coco_dir), output_dir=str(out_dir),
        epochs=int(par.get("epochs") or 100),
        batch_size=int(par.get("batch_size") or 4),
        grad_accum_steps=int(par.get("grad_accum_steps") or 4),
        lr=float(par.get("lr") or 1e-4),
        lr_encoder=float(par.get("lr_encoder") or 1.5e-4),
        warmup_epochs=float(par.get("warmup_epochs") or 0.0),
        early_stopping=bool(par.get("early_stopping") or False),
    )
    return {"epoch": int(par.get("epochs") or 0), "map": None}


def _serap(out_dir: Path, dir_latih: Path, hasil: dict) -> int:
    """Pindahkan hasil RF-DETR ke .latih/L<n>/ dengan konvensi jalur YOLO:
    checkpoint terbaik -> weights/best.pt (biar punya_bobot/unduh berlaku);
    grafik PNG ikut; tulis results.csv minimal (epoch + mAP bila ada) supaya
    status menampilkan kemajuan. Kembalikan epoch terbaca."""
    out_dir, dir_latih = Path(out_dir), Path(dir_latih)
    (dir_latih / "weights").mkdir(parents=True, exist_ok=True)
    # best checkpoint: rfdetr menulis checkpoint_best_total.pth (lihat notebook);
    # fallback ke .pth apa pun yang paling "best", lalu .pth mana pun.
    best = (sorted(out_dir.rglob("checkpoint_best_total.pth"))
            or sorted(out_dir.rglob("*best*.pth"))
            or sorted(out_dir.rglob("*.pth")))
    if best:
        shutil.copy2(best[0], dir_latih / "weights" / "best.pt")
    last = sorted(out_dir.rglob("checkpoint.pth")) or sorted(out_dir.rglob("*last*.pth"))
    if last:
        shutil.copy2(last[0], dir_latih / "weights" / "last.pt")
    for png in out_dir.rglob("*.png"):          # metrics_plot.png dll -> kartu
        try:
            shutil.copy2(png, dir_latih / png.name)
        except OSError:
            pass
    epoch = int(hasil.get("epoch") or 0)
    mp = hasil.get("map")
    rp = dir_latih / "results.csv"
    if mp is not None:
        rp.write_text(f"epoch,metrics/mAP50-95(B)\n{epoch},{mp}\n")
    elif epoch:
        rp.write_text(f"epoch\n{epoch}\n")
    return latih.baca_hasil_csv(dir_latih).get("epoch") or epoch


def main() -> int:
    if len(sys.argv) < 3:
        print("pemakaian: latih_rfdetr <folder-projek> <nomor>", file=sys.stderr)
        return 2
    ds, nomor = Path(sys.argv[1]), int(sys.argv[2])
    isi = latih.baca(ds, nomor)
    if isi is None:
        print(f"training L{nomor} tidak ada di {ds}", file=sys.stderr)
        return 2

    kag = {}

    def lapor(**kv):                            # dicatat di field "rfdetr" job
        kag.update(kv)
        latih.perbarui(ds, nomor, rfdetr=dict(kag))

    # Antre GPU bersama latih_jalan: satu training pada satu waktu di kartu.
    KUNCI_GPU.touch(exist_ok=True)
    f = open(KUNCI_GPU, "r+")
    try:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            latih.perbarui(ds, nomor, keadaan="antre")
            lapor(pesan="menunggu giliran GPU…")
            fcntl.flock(f, fcntl.LOCK_EX)

        t0 = time.time()
        latih.perbarui(ds, nomor, keadaan="jalan", mulai_pada=_sekarang(),
                       pid=__import__("os").getpid(), galat="")
        from . import buatversi, ekspor_coco
        versi = int(isi["versi"])
        versi_dir = buatversi.dir_versi(ds, versi)
        if not (versi_dir / "data.yaml").exists():
            raise FileNotFoundError(
                f"versi v{versi} belum punya data.yaml — tak bisa diekspor ke COCO")
        par = dict(isi.get("par") or {})
        model = isi.get("rfdetr_model") or "nano"
        dl = latih.dir_latih(ds, nomor)
        dl.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp:
            coco_dir = Path(tmp) / "coco"
            lapor(pesan="ekspor versi ke COCO…")
            ring = ekspor_coco.versi_ke_coco(versi_dir, coco_dir)
            print(f"  COCO: {ring}", flush=True)
            out_dir = Path(tmp) / "out"
            out_dir.mkdir()
            hasil = _latih_rfdetr_asli(coco_dir, out_dir, model, par, lapor)
            epoch = _serap(out_dir, dl, hasil)

        latih.perbarui(ds, nomor, keadaan="selesai", selesai_pada=_sekarang(),
                       detik_total=round(time.time() - t0, 1))
        lapor(pesan=f"selesai — RF-DETR {model}, {epoch} epoch")
        print(f"  bobot terbaik: {dl / 'weights' / 'best.pt'}")
        return 0
    except Exception as e:                       # noqa: BLE001
        latih.perbarui(ds, nomor, keadaan="gagal", galat=str(e)[:300],
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
