"""
Backend training: SERVER REMOTE lewat SSH (offload ke GPU mesin lain sendiri).

HIGOLAB tetap di mesin INI. Yang pindah hanya EKSEKUSI training: dataset versi
dikirim ke server GPU lain lewat rsync, YOLO (Ultralytics) jalan di GPU-nya,
lalu results.csv + bobot ditarik balik ke .latih/L<n>/ secara berkala.

Kontrak subproses sama dengan latih_jalan / latih_kaggle:
    python -m app.services.latih_server <ds> <nomor>
subproses ini terlepas dari server, menulis results.csv + weights/best.pt ke
.latih/L<n>/ (yang DIBACA status()/baca_hasil_csv untuk kemajuan di UI) dan
memperbarui `keadaan`. Ia TIDAK memegang flock GPU lokal (GPU-nya di remote),
jadi boleh jalan berbarengan dengan training lokal/Kaggle -- seperti Kaggle.

Keamanan: host + kunci privat disetel lewat env (settings.remote_*), TAK di
repo. Kunci dipakai apa adanya; tak ada password yang disimpan.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from . import buatversi, latih
from ..config import get_settings

POLL = 20          # detik antar-pengambilan hasil dari remote
CONNECT_TIMEOUT = 25


def _sekarang() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------- SSH / rsync
def _ssh_opts(key: str) -> list[str]:
    return ["-i", key, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
            "-o", f"ConnectTimeout={CONNECT_TIMEOUT}"]


def _ssh(key: str, host: str, cmd: str, timeout: int = 90,
         masukan: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["ssh", *_ssh_opts(key), host, cmd],
                          input=masukan, capture_output=True, text=True,
                          timeout=timeout)


def _ssh_ok(key: str, host: str, cmd: str, timeout: int = 90) -> str:
    r = _ssh(key, host, cmd, timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ssh gagal ({r.returncode}): {(r.stderr or r.stdout)[:200]}")
    return r.stdout.strip()


def _rsync(key: str, src: str, dst: str, timeout: int = 7200) -> subprocess.CompletedProcess:
    e = "ssh " + " ".join(_ssh_opts(key))
    return subprocess.run(["rsync", "-az", "--partial", "-e", e, src, dst],
                          capture_output=True, text=True, timeout=timeout)


def _tulis_remote(key: str, host: str, path: str, isi: str) -> None:
    # Tulis berkas di remote lewat `cat > path` (isi lewat stdin, tak lewat argv).
    r = _ssh(key, host, f"cat > {path}", timeout=60, masukan=isi)
    if r.returncode != 0:
        raise RuntimeError(f"gagal menulis {path} di remote: {r.stderr[:200]}")


# --------------------------------------------------------------- dataset
def _data_yaml_remote(lokal_yaml: Path, ds_abs: str) -> str:
    """data.yaml BERSIH untuk remote: `path` absolut + subpath apa adanya.

    data.yaml versi memakai jalur relatif (../train/images) yang resolusinya
    bergantung konteks; di remote kita tulis ulang eksplisit supaya tak ambigu.
    nc/names/kpt_shape/flip_idx dipertahankan (pose butuh dua yang terakhir)."""
    import yaml as _yaml
    asal = _yaml.safe_load(lokal_yaml.read_text()) or {}
    out = {"path": ds_abs, "train": "train/images", "val": "valid/images",
           "test": "test/images", "nc": asal.get("nc"), "names": asal.get("names")}
    for k in ("kpt_shape", "flip_idx"):
        if asal.get(k) is not None:
            out[k] = asal[k]
    return _yaml.safe_dump(out, sort_keys=False, allow_unicode=True)


def _bobot_lokal(bobot: str) -> Path | None:
    """Kalau bobot awal itu BERKAS lokal (bukan nama pra-latih yang diunduh),
    kembalikan path-nya supaya ikut dikirim ke remote. None = nama, remote unduh."""
    p = Path(bobot)
    if p.is_file():
        return p
    for d in latih.dir_bobot():
        c = d / bobot
        if c.is_file():
            return c
    return None


def _sesuaikan_par_pose(par: dict, lokal_yaml: Path) -> dict:
    """Cermin latih_jalan: augmentasi aman-pose (flipud mati; fliplr mati kalau
    flip_idx identitas/kosong; mosaic dibatasi)."""
    par = dict(par)
    par["flipud"] = 0.0
    try:
        import yaml as _yaml
        fi = (_yaml.safe_load(lokal_yaml.read_text()) or {}).get("flip_idx") or []
        if not fi or fi == list(range(len(fi))):
            par["fliplr"] = 0.0
    except Exception:                       # noqa: BLE001
        par["fliplr"] = 0.0
    if par.get("mosaic", 0.0) > 0.5:
        par["mosaic"] = 0.5
    return par


# --------------------------------------------------------------- remote script
def _skrip_latih(data_abs: str, bobot: str, tugas: str, par: dict,
                 run_parent: str) -> str:
    """Skrip Ultralytics MANDIRI (tanpa HIGOLAB) yang jalan di remote: train ke
    {run_parent}/run/ (results.csv + weights/), lalu tulis EXIT dgn kode-nya."""
    return (
        "import sys, traceback\n"
        "from ultralytics import YOLO\n"
        # repr() -> literal PYTHON (True/False/None). json.dumps memancarkan
        # true/false/null gaya JSON yang di skrip Python jadi NameError di baris
        # ini -- crash sebelum try/except, tanpa EXIT, hingga run tampak "hilang".
        f"PAR = {par!r}\n"
        "try:\n"
        f"    YOLO({bobot!r}).train(data={data_abs!r}, task={tugas!r},\n"
        f"        project={run_parent!r}, name='run', exist_ok=True, device=0, **PAR)\n"
        "    code = 0\n"
        "except Exception:\n"
        "    traceback.print_exc(); code = 1\n"
        f"open({run_parent!r} + '/run/EXIT','w').write(str(code))\n"
        "sys.exit(code)\n"
    )


# --------------------------------------------------------- remote RF-DETR
# Badan skrip RF-DETR MANDIRI (tanpa HIGOLAB) -- statis; konfigurasi per-run
# disuntik lewat baris `CFG_JSON = '...'` di atasnya oleh _skrip_rfdetr. Raw
# string: `\n` tetap dua karakter, jadi string literal di skrip remote benar.
# Mencerminkan latih_rfdetr lokal: callback PTL menulis results.csv tiap epoch
# + menyalin checkpoint terbaik -> poll menarik kemajuan LIVE & bobot walau
# training berhenti di tengah. Keluaran disusun seperti jalur YOLO
# ({run}/results.csv + {run}/weights/best.pt + {run}/EXIT) supaya poller sama.
_RFDETR_BODY = r'''
import sys, time, glob, os, shutil, traceback, json
import rfdetr
CFG = json.loads(CFG_JSON)
RUN = CFG["run"]; OUT = RUN + "/out"
os.makedirs(RUN + "/weights", exist_ok=True); os.makedirs(OUT, exist_ok=True)

def _best():
    for pat in ("checkpoint_best_total.pth", "checkpoint_best_ema.pth",
                "checkpoint_best_regular.pth", "*best*.pth", "*.pth"):
        g = sorted(glob.glob(OUT + "/**/" + pat, recursive=True), key=len)
        if g:
            return g[0]
    return None

def _sync(rows):
    try:
        with open(RUN + "/results.csv", "w") as f:
            f.write("epoch,time,metrics/mAP50-95(B)\n")
            for e, d, mp in rows:
                f.write("%d,%s,%s\n" % (e, round(d, 1), "" if mp is None else mp))
    except OSError:
        pass
    b = _best()
    if b:
        try:
            shutil.copy2(b, RUN + "/weights/best.pt")
        except OSError:
            pass

import pytorch_lightning as pl
from pytorch_lightning.callbacks import Callback

class _P(Callback):
    def on_fit_start(self, t, m):
        self._t0 = time.time(); self._rows = []
    def on_train_epoch_end(self, t, m):
        ep = int(t.current_epoch) + 1
        det = time.time() - getattr(self, "_t0", time.time())
        mp = None
        for k, v in (t.callback_metrics or {}).items():
            if "map" in str(k).lower():
                try:
                    mp = float(v)
                except Exception:
                    pass
        self._rows = getattr(self, "_rows", [])
        self._rows.append((ep, det, mp))
        _sync(self._rows)

_oi = pl.Trainer.__init__
def _ni(self, *a, **k):
    cb = list(k.get("callbacks") or []); cb.append(_P()); k["callbacks"] = cb
    return _oi(self, *a, **k)
pl.Trainer.__init__ = _ni

code = 0
try:
    Model = getattr(rfdetr, CFG["cls"])
    m = Model(resolution=int(CFG["res"]))
    m.train(dataset_dir=CFG["coco"], output_dir=OUT, **CFG["par"])
    b = _best()
    if b:
        shutil.copy2(b, RUN + "/weights/best.pt")
    last = (sorted(glob.glob(OUT + "/**/checkpoint.pth", recursive=True))
            or sorted(glob.glob(OUT + "/**/*last*.pth", recursive=True)))
    if last:
        shutil.copy2(last[0], RUN + "/weights/last.pt")
    if not os.path.exists(RUN + "/results.csv"):
        _sync([(CFG["par"]["epochs"], 0.0, None)])
except Exception:
    traceback.print_exc(); code = 1
open(RUN + "/EXIT", "w").write(str(code))
sys.exit(code)
'''


def _skrip_rfdetr(coco_abs: str, model: str, par: dict, run_parent: str) -> str:
    """Skrip RF-DETR MANDIRI untuk remote. Konfigurasi (kelas model, resolusi
    dijepit kelipatan 56, path COCO + run, hiperparameter) disuntik sebagai JSON;
    badannya statis (_RFDETR_BODY)."""
    import json as _json

    kelas = {"nano": "RFDETRNano", "small": "RFDETRSmall",
             "medium": "RFDETRMedium", "large": "RFDETRLarge"}
    res = max(56, int(round(float(par.get("resolution") or 560) / 56.0)) * 56)
    cfg = {
        "cls": kelas.get(model, "RFDETRNano"),
        "res": res,
        "coco": coco_abs,
        "run": run_parent + "/run",
        "par": {
            "epochs": int(par.get("epochs") or 100),
            "batch_size": int(par.get("batch_size") or 4),
            "grad_accum_steps": int(par.get("grad_accum_steps") or 4),
            "lr": float(par.get("lr") or 1e-4),
            "lr_encoder": float(par.get("lr_encoder") or 1.5e-4),
            "warmup_epochs": float(par.get("warmup_epochs") or 0.0),
            "early_stopping": bool(par.get("early_stopping") or False),
        },
    }
    return "CFG_JSON = " + repr(_json.dumps(cfg)) + "\n" + _RFDETR_BODY


# --------------------------------------------------------------- main
def main() -> int:
    ds, nomor = Path(sys.argv[1]), int(sys.argv[2])
    isi = latih.baca(ds, nomor)
    if isi is None:
        return 1
    st = get_settings()
    host = st.remote_ssh.strip()
    key = str(Path(st.remote_key).expanduser())
    py = st.remote_py or "python3"
    rdir = (st.remote_dir or "~/higolab-train").rstrip("/")

    dl = latih.dir_latih(ds, nomor)
    dl.mkdir(parents=True, exist_ok=True)
    (dl / "weights").mkdir(exist_ok=True)
    ds_dir = buatversi.dir_versi(ds, int(isi["versi"]))
    lokal_yaml = ds_dir / "data.yaml"
    if not lokal_yaml.is_file():
        latih.perbarui(ds, nomor, keadaan="gagal",
                       galat=f"versi v{isi['versi']} tak punya data.yaml")
        return 1

    run_id = f"L{nomor}-{secrets.token_hex(3)}"
    rroot = f"{rdir}/{run_id}"
    t0 = time.time()
    latih.perbarui(ds, nomor, keadaan="jalan", mulai_pada=_sekarang(), pid=os.getpid())
    print(f"  backend: SERVER ({st.remote_nama}) via {host}", flush=True)
    try:
        # Praperiksa koneksi + ruang kerja remote.
        home = _ssh_ok(key, host, "echo $HOME")
        _ssh_ok(key, host, f"mkdir -p {rroot}/run {rdir}")
        ds_abs = f"{home}/{rroot[2:]}/ds" if rroot.startswith("~/") else f"{rroot}/ds"

        arsitektur = (isi.get("arsitektur") or "yolo").strip().lower()
        run_parent = ds_abs.rsplit("/", 1)[0]       # = <home>/<rroot>

        if arsitektur == "rfdetr":
            # RF-DETR: dataset dikirim dalam FORMAT COCO (bukan YOLO), dan skrip
            # memakai interpreter remote KHUSUS rfdetr (venv terpisah di remote,
            # supaya torch/deps-nya tak mengusik env YOLO/ultralytics remote).
            from . import ekspor_coco
            py = (st.remote_py_rfdetr or "").strip() or py
            print(f"  ekspor versi {ds_dir.name} ke COCO lalu kirim "
                  f"-> {host}:{rroot}/ds/ …", flush=True)
            with tempfile.TemporaryDirectory() as tmp:
                coco_lokal = Path(tmp) / "coco"
                ekspor_coco.versi_ke_coco(ds_dir, coco_lokal)
                r = _rsync(key, str(coco_lokal) + "/", f"{host}:{rroot}/ds/")
                if r.returncode != 0:
                    raise RuntimeError(f"rsync COCO gagal: {r.stderr[:200]}")
            _tulis_remote(key, host, f"{rroot}/train.py",
                          _skrip_rfdetr(ds_abs, isi.get("rfdetr_model") or "nano",
                                        dict(isi.get("par") or {}), run_parent))
        else:
            # YOLO: kirim pohon versi + data.yaml bersih + bobot, skrip ultralytics.
            print(f"  kirim dataset {ds_dir.name} -> {host}:{rroot}/ds/ …",
                  flush=True)
            r = _rsync(key, str(ds_dir) + "/", f"{host}:{rroot}/ds/")
            if r.returncode != 0:
                raise RuntimeError(f"rsync dataset gagal: {r.stderr[:200]}")
            # Bobot awal: berkas lokal dikirim; nama pra-latih diunduh remote sendiri.
            bl = _bobot_lokal(isi["bobot"])
            if bl is not None:
                _rsync(key, str(bl), f"{host}:{rroot}/bobot.pt")
                bobot_remote = f"{run_parent}/bobot.pt"
            else:
                bobot_remote = isi["bobot"]
            par = dict(isi.get("par") or {})
            if isi["tugas"] == "pose":
                par = _sesuaikan_par_pose(par, lokal_yaml)
            _tulis_remote(key, host, f"{rroot}/ds/data.yaml",
                          _data_yaml_remote(lokal_yaml, ds_abs))
            _tulis_remote(key, host, f"{rroot}/train.py",
                          _skrip_latih(f"{ds_abs}/data.yaml", bobot_remote,
                                       isi["tugas"], par, run_parent))

        # flock menahan selama training -> satu training per GPU remote (serial).
        luncur = (f"cd {rroot} && (flock -w 5 {rdir}/.gpu.lock "
                  f"{py} train.py > train.log 2>&1 &) ; echo LAUNCHED")
        _ssh_ok(key, host, luncur, timeout=60)
        print(f"  training berjalan di {st.remote_nama} (poll tiap {POLL}s) …", flush=True)

        # Poll: tarik hasil + deteksi selesai (berkas EXIT) / mati.
        ep_lama = -1
        while True:
            time.sleep(POLL)
            _rsync(key, f"{host}:{rroot}/run/results.csv", str(dl / "results.csv"))
            _rsync(key, f"{host}:{rroot}/run/weights/", str(dl / "weights") + "/")
            n = int((latih.baca_hasil_csv(dl) or {}).get("epoch") or 0)
            if n != ep_lama:
                ep_lama = n
                print(f"  epoch {n}  · {int(time.time()-t0)//60}m", flush=True)
            exit_s = _ssh(key, host, f"cat {rroot}/run/EXIT 2>/dev/null", timeout=40).stdout.strip()
            if exit_s != "":
                break
            hidup = _ssh(key, host, f"pgrep -f '{rroot}/train.py' >/dev/null && echo 1 || echo 0",
                         timeout=40).stdout.strip()
            if hidup != "1":
                # proses hilang tanpa menulis EXIT (mis. OOM/kill) -> gagal.
                exit_s = "1"
                break

        # Tarik hasil final.
        _rsync(key, f"{host}:{rroot}/run/results.csv", str(dl / "results.csv"))
        _rsync(key, f"{host}:{rroot}/run/weights/", str(dl / "weights") + "/")
        best = dl / "weights" / "best.pt"
        if exit_s == "0" and best.is_file():
            latih.perbarui(ds, nomor, keadaan="selesai", selesai_pada=_sekarang(),
                           detik_total=round(time.time() - t0, 1))
            print(f"  SELESAI · bobot: {best}", flush=True)
            rc = 0
        else:
            ekor = _ssh(key, host, f"tail -n 25 {rroot}/train.log 2>/dev/null",
                        timeout=40).stdout.strip()
            latih.perbarui(ds, nomor, keadaan="gagal",
                           galat=("training remote gagal. " + ekor[-240:]))
            print("  GAGAL di remote:\n" + ekor, flush=True)
            rc = 1
    except Exception as e:                           # noqa: BLE001
        import traceback
        traceback.print_exc()
        latih.perbarui(ds, nomor, keadaan="gagal", galat=str(e)[:260])
        rc = 1
    finally:
        try:
            _ssh(key, host, f"rm -rf {rroot}", timeout=60)   # bersihkan remote
        except Exception:                            # noqa: BLE001
            pass
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
