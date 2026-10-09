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


def _tulis_progres(dl: Path, baris: list[tuple]) -> None:
    """Tulis results.csv (konvensi HIGOLAB) dari baris (epoch, detik, mAP|None),
    supaya status() menampilkan epoch & waktu berlalu LIVE selagi latih jalan —
    tanpa ini UI beku di 'epoch 1' sepanjang training yang berjam-jam."""
    try:
        teks = ["epoch,time,metrics/mAP50-95(B)"]
        for ep, det, mp in baris:
            teks.append(f"{ep},{round(det, 1)},{'' if mp is None else mp}")
        (Path(dl) / "results.csv").write_text("\n".join(teks) + "\n")
    except OSError:
        pass


# Checkpoint rfdetr -> lokasi HIGOLAB. Tiap tujuan punya DAFTAR kandidat sumber
# berurutan (fallback), karena rfdetr menamai berbeda-beda: best bisa
# checkpoint_best_total.pth (ditulis di akhir) ATAU checkpoint_best_ema.pth
# (ditulis selama training saat val membaik); "last" ema (last_ema.pth) baru
# muncul di akhir. Tanpa fallback, best.pt tak tersalin selama training.
_SYNC_CKPT = (
    (("weights", "best.pt"),
     ["checkpoint_best_total.pth", "checkpoint_best_ema.pth",
      "checkpoint_best_regular.pth", "*best*.pth"]),
    (("weights", "last.pt"), ["last_ema.pth", "last*.pth"]),
    (("rfdetr", "last.ckpt"), ["last.ckpt"]),
)


def _sync_ckpt(out_dir: Path, dl: Path, mt: dict) -> None:
    """Salin checkpoint terbaru dari out_dir ke folder HIGOLAB — TIAP EPOCH yang
    berubah. Tujuannya: begitu training dihentikan di tengah, best.pt/last.pt
    (dan last.ckpt untuk Lanjutkan) SUDAH ADA dan mutakhir, tak hilang bersama
    proses. Disalin hanya saat mtime berubah supaya I/O tak boros."""
    out_dir, dl = Path(out_dir), Path(dl)
    for (sub, tuju_nama), kandidat in _SYNC_CKPT:
        src = None
        for pola in kandidat:
            got = sorted(out_dir.rglob(pola), key=lambda p: len(str(p)))
            if got:
                src = got[0]
                break
        if src is None:
            continue
        kunci = (sub, tuju_nama)
        try:
            m = src.stat().st_mtime
        except OSError:
            continue
        if mt.get(kunci) == m:
            continue
        tuju = dl / sub / tuju_nama
        tuju.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, tuju)
            mt[kunci] = m
        except OSError:
            pass


def _latih_rfdetr_asli(coco_dir: Path, out_dir: Path, model: str, par: dict,
                       lapor, resume: str | None = None,
                       dl_progres: Path | None = None) -> dict:
    """Jalankan training RF-DETR SUNGGUHAN. Satu-satunya tempat `rfdetr` diimpor
    -> seam yang dipalsukan di tes. Kembalikan {"epoch": n, "map": x|None}.

    resume = path last.ckpt (state penuh PyTorch-Lightning): dipakai jalur
    "Lanjutkan" supaya training MELANJUTKAN dari checkpoint (epochs = TOTAL baru,
    Lightning lanjut current_epoch -> max_epochs), bukan mulai dari nol.

    dl_progres = folder .latih/L<n>/: kalau diisi, sebuah callback menulis
    results.csv tiap epoch -> status()/kartu menampilkan kemajuan LIVE (epoch,
    waktu, perkiraan sisa), bukan beku di epoch 1 sampai training selesai."""
    import rfdetr

    Model = getattr(rfdetr, _KELAS_RFDETR.get(model, "RFDETRNano"))
    res = _snap56(int(par.get("resolution") or 560))
    lapor(pesan=f"melatih RF-DETR {model} @ {res}px…")

    # Suntik callback progres lewat monkeypatch pl.Trainer.__init__ (rfdetr
    # membangun Trainer sendiri; pola sama dengan stop wall-clock kernel Kaggle).
    if dl_progres is not None:
        import pytorch_lightning as pl
        from pytorch_lightning.callbacks import Callback

        class _Progres(Callback):
            def on_fit_start(self, trainer, pl_module):
                self._t0 = time.time()
                self._rows: list[tuple] = []
                self._mt: dict = {}

            def on_train_epoch_end(self, trainer, pl_module):
                ep = int(trainer.current_epoch) + 1
                det = time.time() - getattr(self, "_t0", time.time())
                mp = None
                for k, v in (trainer.callback_metrics or {}).items():
                    if "map" in str(k).lower():
                        try:
                            mp = float(v)
                        except (TypeError, ValueError):
                            pass
                self._rows = getattr(self, "_rows", [])
                self._rows.append((ep, det, mp))
                _tulis_progres(dl_progres, self._rows)
                # Salin checkpoint terbaru -> stop di tengah tetap meninggalkan
                # best.pt/last.pt/last.ckpt yang mutakhir.
                _sync_ckpt(out_dir, dl_progres, getattr(self, "_mt", {}))

        _orig_init = pl.Trainer.__init__

        def _init(self, *a, **k):
            cbs = list(k.get("callbacks") or [])
            cbs.append(_Progres())
            k["callbacks"] = cbs
            return _orig_init(self, *a, **k)
        pl.Trainer.__init__ = _init

    m = Model(resolution=res)
    kw = dict(
        dataset_dir=str(coco_dir), output_dir=str(out_dir),
        epochs=int(par.get("epochs") or 100),
        batch_size=int(par.get("batch_size") or 4),
        grad_accum_steps=int(par.get("grad_accum_steps") or 4),
        lr=float(par.get("lr") or 1e-4),
        lr_encoder=float(par.get("lr_encoder") or 1.5e-4),
        warmup_epochs=float(par.get("warmup_epochs") or 0.0),
        early_stopping=bool(par.get("early_stopping") or False),
    )
    if resume:
        kw["resume"] = str(resume)
    m.train(**kw)
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
    # last.ckpt = state PENUH PTL (optimizer+scheduler+epoch). Disimpan ke
    # rfdetr/ supaya training ini bisa DILANJUTKAN nanti (tombol "Lanjutkan")
    # dengan resume sejati, bukan warm-start bobot saja.
    ckpt = sorted(out_dir.rglob("last.ckpt"))
    if ckpt:
        (dir_latih / "rfdetr").mkdir(parents=True, exist_ok=True)
        shutil.copy2(ckpt[0], dir_latih / "rfdetr" / "last.ckpt")
    for png in out_dir.rglob("*.png"):          # metrics_plot.png dll -> kartu
        try:
            shutil.copy2(png, dir_latih / png.name)
        except OSError:
            pass
    epoch = int(hasil.get("epoch") or 0)
    mp = hasil.get("map")
    rp = dir_latih / "results.csv"
    # Kalau callback progres sudah menulis results.csv (jalur lokal biasa), JANGAN
    # ditimpa: punyanya lebih kaya (epoch + WAKTU per epoch), jadi kartu hasil
    # tetap menampilkan "Lama" yang benar. Hanya tulis kalau belum ada (mis.
    # training tanpa callback).
    if not (rp.exists() and rp.stat().st_size > 0):
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
        # Jalur "Lanjutkan": kalau ada last.ckpt yang disemai (dari training
        # sumber), resume SEJATI dari situ. Training SEGAR tak punya berkas ini,
        # jadi tetap mulai dari bobot pralatih seperti biasa.
        seed = dl / "rfdetr" / "last.ckpt"
        resume_ckpt = str(seed) if seed.exists() else None
        if resume_ckpt:
            lapor(pesan="melanjutkan dari checkpoint (resume state penuh)…")

        # out_dir PERSISTEN di .latih/L<n>/rfdetr/out (BUKAN folder sementara):
        # kalau training dihentikan di tengah, checkpoint tiap epoch TETAP ADA di
        # sini dan sudah disalin ke weights/ oleh callback -> stop awal tetap
        # meninggalkan best.pt/last.pt yang bisa dipakai. (COCO tetap sementara —
        # cuma input, diekspor ulang tiap run.)
        out_dir = dl / "rfdetr" / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            coco_dir = Path(tmp) / "coco"
            lapor(pesan="ekspor versi ke COCO…")
            ring = ekspor_coco.versi_ke_coco(versi_dir, coco_dir)
            print(f"  COCO: {ring}", flush=True)
            hasil = _latih_rfdetr_asli(coco_dir, out_dir, model, par, lapor,
                                       resume=resume_ckpt, dl_progres=dl)
            epoch = _serap(out_dir, dl, hasil)

        latih.perbarui(ds, nomor, keadaan="selesai", selesai_pada=_sekarang(),
                       detik_total=round(time.time() - t0, 1))
        lapor(pesan=f"selesai. RF-DETR {model}, {epoch} epoch")
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
