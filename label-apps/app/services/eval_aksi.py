"""
Evaluasi CLASSIFIER AKSI VIDEO — padanan evaluasi.py untuk klip (Langkah 10).

Diport dari posec3d/action-v14/03_evaluasi.py: akurasi, AKURASI RATA-RATA KELAS,
confusion matrix (PNG + angka), dan pasangan kelas yang paling sering tertukar.
Yang menentukan langkah perbaikan berikutnya adalah pasangan yang tertukar, bukan
satu angka akurasi — itu sebabnya confusion matrix-nya disimpan, bukan cuma skor.

SATU HAL YANG MENJADI SELURUH INTI LANGKAH INI — "alat ukur harus diuji pada
model buruk" (MEMORY). Alat ukur yang melaporkan model rusak sebagai "baik" lebih
berbahaya daripada tidak ada alat ukur sama sekali, karena ia memberi izin
mengirim model yang akan gagal di produksi. Maka hitung_metrik() punya PENJAGA
yang eksplisit, bukan sekadar rata-rata naif:

  1. KELAS TANPA DUKUNGAN (support 0). Kelas yang TAK PUNYA satu pun klip valid
     tidak bisa diukur recall-nya — 0/0. Naif-nya itu jadi NaN, atau lebih buruk
     dibulatkan jadi 1,0 (100%) dan MENGEREK akurasi-rata-rata-kelas ke atas
     tanpa dasar. Di sini kelas seperti itu DIKELUARKAN dari rata-rata dan
     dilaporkan terpisah (`kelas_tanpa_dukungan`) — tak pernah dihitung sempurna.

  2. MODEL DEGENERAT (menebak satu kelas untuk SEMUANYA). Pada set seimbang
     nc-kelas, model begini HARUS memberi akurasi-rata-rata-kelas ~1/nc (satu
     kelas recall 1,0, sisanya 0,0), BUKAN angka tinggi. Itu terjadi dengan
     sendirinya KARENA penjaga #1 mengecualikan berdasar DUKUNGAN (jumlah label
     sebenarnya), bukan berdasar apakah kelas itu pernah ditebak: kelas yang
     tak pernah ditebak tetap punya dukungan, recall-nya 0,0, dan tetap ikut
     rata-rata. Degenerasi juga ditandai (`degenerate`) dan divonis "buruk".

  3. TAK ADA DATA. Masukan kosong tak boleh melempar ZeroDivision maupun
     melaporkan 100%: ia mengembalikan hasil "kosong" dengan akurasi None.

BAGIAN BERAT (prediksi tiap klip lewat best.pt + gambar PNG) butuh torch/
matplotlib — hanya ada di .venv-gpu, diimpor DI DALAM fungsi. hitung_metrik()
sendiri murni numpy, jadi SELURUH matematika pengukuran bisa diuji di suite CPU
tanpa satu pun pustaka berat. Eval sungguhan GPU ditangguhkan (kartu sibuk);
suite CPU membuktikan matematikanya + pipanya.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from ..log import catat
from . import latih_aksi

log = catat("labelapp.eval_aksi")

_kunci = threading.Lock()


# ============================================================
# METRIK MURNI  (numpy saja — bisa diuji di CPU, TANPA matplotlib/sklearn/torch)
# ============================================================

def hitung_metrik(y_true, y_pred, kelas) -> dict:
    """Metrik klasifikasi dari daftar label sebenarnya & tebakan.

    `y_true`, `y_pred` daftar nama kelas (string) sepanjang jumlah klip valid;
    `kelas` daftar nama kelas urut indeks (dari aksi.yaml/MANIFES versinya).

    Mengembalikan dict (skema di komentar modul). Baris = sebenarnya, kolom =
    tebakan pada `matriks`. SEMUA penjaga kewarasan ada di sini — lihat komentar
    modul; masing-masing dijaga oleh test_eval_aksi.
    """
    import numpy as np

    kelas = [str(k) for k in (kelas or [])]
    nc = len(kelas)
    idx = {c: i for i, c in enumerate(kelas)}

    # Pasangkan hanya yang label sebenarnya-nya dikenal. Tebakan di luar daftar
    # kelas (semestinya tak terjadi — model dilatih atas kelas ini) dihitung
    # sebagai "salah ke kelas tak dikenal": tak pernah diam-diam dibuang.
    pasangan = []
    for a, p in zip(y_true or [], y_pred or []):
        if a is None or a not in idx:
            continue
        pasangan.append((idx[a], idx.get(p, -1)))
    n = len(pasangan)

    kosong_hasil = {
        "n": 0, "kelas": kelas,
        "akurasi": None, "akurasi_rerata_kelas": None,
        "per_kelas": {c: {"recall": None, "support": 0, "benar": 0}
                      for c in kelas},
        "matriks": [[0] * nc for _ in range(nc)],
        "pasangan_bingung": [], "kelas_tanpa_dukungan": list(kelas),
        "degenerate": False, "tingkat": "kosong",
        "pesan": ("Tidak ada klip valid yang bisa dinilai — versi tak punya "
                  "split valid, atau model gagal memprediksi semuanya. Akurasi "
                  "TIDAK bisa dilaporkan (bukan 100%): perbaiki datanya dulu."),
    }
    if n == 0 or nc == 0:
        return kosong_hasil

    # ---- confusion matrix (baris=sebenarnya, kolom=tebakan) ----
    matriks = np.zeros((nc, nc), dtype=int)
    tebak_tak_dikenal = 0
    for a, p in pasangan:
        if p < 0:
            tebak_tak_dikenal += 1
            continue
        matriks[a, p] += 1

    benar = int(sum(1 for a, p in pasangan if a == p))
    akurasi = benar / n

    # ---- recall per kelas + PENJAGA kelas tanpa dukungan ----
    per_kelas: dict[str, dict] = {}
    recall_dukung: list[float] = []     # hanya kelas dengan support > 0
    tanpa_dukungan: list[str] = []
    for i, c in enumerate(kelas):
        support = int(matriks[i].sum())
        if support == 0:
            # 0/0 — TIDAK terdefinisi. Dikeluarkan dari rata-rata, dilaporkan
            # terpisah. Tak pernah dihitung 1,0 (itu akan mengerek skor palsu).
            per_kelas[c] = {"recall": None, "support": 0, "benar": 0}
            tanpa_dukungan.append(c)
            continue
        bc = int(matriks[i, i])
        rec = bc / support
        per_kelas[c] = {"recall": rec, "support": support, "benar": bc}
        recall_dukung.append(rec)

    # Rata-rata HANYA atas kelas yang punya dukungan — inti penjaga #1 & #2.
    akurasi_rerata_kelas = (float(np.mean(recall_dukung))
                            if recall_dukung else None)

    # ---- pasangan paling sering tertukar (true -> pred, i != j) ----
    bingung = []
    for i in range(nc):
        total_i = int(matriks[i].sum())
        for j in range(nc):
            if i != j and matriks[i, j] > 0:
                jml = int(matriks[i, j])
                bingung.append({"dari": kelas[i], "ke": kelas[j], "jml": jml,
                                "porsi": round(jml / total_i, 4) if total_i
                                else 0.0})
    # Urut: terbanyak dulu; seri dipecah stabil (nama) supaya hasilnya
    # deterministik — test bergantung pada urutan ini.
    bingung.sort(key=lambda t: (-t["jml"], t["dari"], t["ke"]))
    pasangan_bingung = bingung[:8]

    # ---- PENJAGA #2: model degenerat (satu kelas untuk semua tebakan) ----
    kelas_ditebak = {p for _, p in pasangan if p >= 0}
    degenerate = len(kelas_ditebak) == 1 and n >= 2

    # ---- vonis: JANGAN pernah menyebut model buruk "baik" ----
    nc_dukung = len(recall_dukung)
    acak = 1.0 / nc_dukung if nc_dukung else 0.0
    if degenerate:
        satu = kelas[next(iter(kelas_ditebak))]
        tingkat = "buruk"
        pesan = (f"Model menebak `{satu}` untuk SEMUA {n} klip. Ia tak belajar "
                 f"membedakan kelas. Akurasi-rata-rata-kelas {akurasi_rerata_kelas:.2f} "
                 f"hanya sebesar tebakan acak (~{acak:.2f}). JANGAN dikirim.")
    elif akurasi_rerata_kelas is None:
        tingkat = "kosong"
        pesan = kosong_hasil["pesan"]
    elif akurasi_rerata_kelas <= acak * 1.1:
        # Nyaris tak lebih baik dari menebak acak — rusak, apa pun akurasi
        # keseluruhannya (yang bisa tinggi kalau satu kelas mendominasi).
        tingkat = "buruk"
        pesan = (f"Akurasi-rata-rata-kelas {akurasi_rerata_kelas:.2f} nyaris "
                 f"sama dengan tebakan acak (~{acak:.2f}) — model belum belajar "
                 "membedakan kelas. Lihat confusion matrix: kemungkinan beberapa "
                 "kelas tak pernah dikenali.")
    elif akurasi_rerata_kelas < 0.5:
        tingkat = "sedang"
        pesan = (f"Akurasi-rata-rata-kelas {akurasi_rerata_kelas:.2f}. Beberapa "
                 "kelas masih lemah — periksa pasangan yang paling sering "
                 "tertukar untuk tahu mana yang perlu lebih banyak klip.")
    else:
        tingkat = "baik"
        pesan = (f"Akurasi {akurasi:.2f}, akurasi-rata-rata-kelas "
                 f"{akurasi_rerata_kelas:.2f}. Seimbang antar kelas.")
    if tanpa_dukungan and tingkat not in ("buruk", "kosong"):
        pesan += (f" Catatan: {len(tanpa_dukungan)} kelas tanpa klip valid "
                  f"({', '.join(tanpa_dukungan)}) tak ikut dinilai.")

    hasil = {
        "n": n, "kelas": kelas,
        "akurasi": round(akurasi, 4),
        "akurasi_rerata_kelas": (round(akurasi_rerata_kelas, 4)
                                 if akurasi_rerata_kelas is not None else None),
        "per_kelas": per_kelas,
        "matriks": matriks.tolist(),
        "pasangan_bingung": pasangan_bingung,
        "kelas_tanpa_dukungan": tanpa_dukungan,
        "degenerate": degenerate,
        "tingkat": tingkat, "pesan": pesan,
    }
    if tebak_tak_dikenal:
        hasil["tebak_tak_dikenal"] = tebak_tak_dikenal
    return hasil


# ============================================================
# GAMBAR CONFUSION  (matplotlib lazy — HANYA .venv-gpu)
# ============================================================

def matriks_png(matriks, kelas, metrik: dict | None = None) -> bytes:
    """Confusion matrix sebagai PNG (byte). matplotlib diimpor DI DALAM fungsi.

    Melempar RuntimeError dengan pesan jelas kalau matplotlib tak terpasang —
    suite CPU TIDAK memanggilnya (hanya memastikan ia melempar bersih). Baris =
    sebenarnya, kolom = tebakan; angka = porsi per baris (recall), supaya warna
    satu baris bisa dibaca sebagai "dari kelas ini, ke mana saja".
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:                        # noqa: BLE001
        raise RuntimeError(
            "matplotlib tak terpasang — gambar confusion matrix hanya bisa "
            "dibuat di venv GPU (requirements-gpu.txt). Angka metriknya tetap "
            f"tersedia di metrik.json. ({e})") from e

    import io

    import numpy as np

    cm = np.asarray(matriks, dtype=float)
    n = cm.shape[0]
    kelas = [str(k) for k in kelas]
    baris = cm.sum(1, keepdims=True)
    cmn = cm / np.maximum(baris, 1)

    fig, ax = plt.subplots(figsize=(max(6, n * 0.9), max(5, n * 0.8)))
    im = ax.imshow(cmn, cmap="Blues", vmin=0, vmax=1)
    ax.set_xticks(range(n)); ax.set_xticklabels(kelas, rotation=45, ha="right")
    ax.set_yticks(range(n)); ax.set_yticklabels(kelas)
    ax.set_xlabel("tebakan"); ax.set_ylabel("sebenarnya")
    judul = "Confusion matrix (porsi per baris)"
    if metrik:
        ak = metrik.get("akurasi"); mk = metrik.get("akurasi_rerata_kelas")
        if ak is not None and mk is not None:
            judul = f"akurasi {ak:.3f}  rata-kelas {mk:.3f}"
    ax.set_title(judul)
    for i in range(n):
        for j in range(n):
            ax.text(j, i, f"{cmn[i, j]:.2f}", ha="center", va="center",
                    color="white" if cmn[i, j] > 0.5 else "black", fontsize=8)
    fig.colorbar(im); fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    plt.close(fig)
    return buf.getvalue()


# ============================================================
# PREDIKSI SATU VERSI  (GPU-only; impor berat DI DALAM per backend)
# ============================================================
EXTS_KLIP = (".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v")


def _klip_valid(versi_dir: Path, kelas: list[str]):
    """[(path, nama_kelas)] semua klip di valid/<kelas>/. Hanya kelas yang
    dikenal — valid/ sudah diverifikasi bersih (asli saja) saat build."""
    base = Path(versi_dir) / "valid"
    out = []
    for c in kelas:
        d = base / c
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.suffix.lower() in EXTS_KLIP and not p.name.startswith("."):
                out.append((p, c))
    return out


def _prediksi_videomae(best_pt: Path, klips, kelas, dev, progres):
    import numpy as np
    import torch
    import torch.nn.functional as F
    from pytorchvideo.data.encoded_video import EncodedVideo
    from transformers import (VideoMAEForVideoClassification,
                              VideoMAEImageProcessor)

    from . import latih_aksi_umum as U, latih_aksi_videomae as V

    ck = torch.load(best_pt, map_location=dev, weights_only=False)
    kelas = [str(c) for c in (ck.get("classes") or kelas)]
    c2i = ck.get("class_to_idx") or {c: i for i, c in enumerate(kelas)}
    i2c = {i: c for c, i in c2i.items()}

    model = VideoMAEForVideoClassification.from_pretrained(
        V.MODEL_CKPT, num_labels=len(kelas), ignore_mismatched_sizes=True)
    model.load_state_dict(ck["model"]); model.to(dev); model.eval()
    proc = VideoMAEImageProcessor.from_pretrained(V.MODEL_CKPT)

    def frames_val(path):
        video = EncodedVideo.from_path(str(path))
        dur = float(video.duration)
        start = max(0.0, dur / 2 - U.CLIP_DURATION / 2)
        fr = video.get_clip(start_sec=start,
                            end_sec=min(start + U.CLIP_DURATION, dur))["video"]
        if fr is None or fr.shape[1] < 2:
            return None
        t = fr.shape[1]
        fr = fr[:, torch.linspace(0, t - 1, V.NUM_FRAMES).long()]
        if fr.shape[2] != U.RESIZE or fr.shape[3] != U.RESIZE:
            fr = F.interpolate(fr.permute(1, 0, 2, 3).float(),
                               size=(U.RESIZE, U.RESIZE), mode="bilinear",
                               align_corners=False).permute(1, 0, 2, 3)
        fr = fr.permute(1, 2, 3, 0)
        return [fr[i].numpy().astype(np.uint8) if fr[i].max() > 1
                else (fr[i].numpy() * 255).astype(np.uint8)
                for i in range(V.NUM_FRAMES)]

    y_true, y_pred = [], []
    with torch.no_grad():
        for k, (path, c) in enumerate(klips):
            f = frames_val(path)
            if f is None:
                continue
            pv = proc(f, return_tensors="pt")["pixel_values"].to(dev)
            out = model(pixel_values=pv).logits
            y_true.append(c)
            y_pred.append(i2c.get(int(out.argmax(1)[0]), "?"))
            if progres:
                progres(k + 1, len(klips))
    return y_true, y_pred, kelas


def _prediksi_slowfast(best_pt: Path, klips, kelas, dev, progres):
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from pytorchvideo.data.encoded_video import EncodedVideo
    from pytorchvideo.models.hub import slowfast_r50

    from . import latih_aksi_slowfast as S, latih_aksi_umum as U

    ck = torch.load(best_pt, map_location=dev, weights_only=False)
    kelas = [str(c) for c in (ck.get("classes") or kelas)]
    c2i = ck.get("class_to_idx") or {c: i for i, c in enumerate(kelas)}
    i2c = {i: c for c, i in c2i.items()}

    model = slowfast_r50(pretrained=False)
    in_feat = model.blocks[-1].proj.in_features
    model.blocks[-1].proj = nn.Sequential(nn.Dropout(p=S.DROPOUT_HEAD),
                                          nn.Linear(in_feat, len(kelas)))
    model.load_state_dict(ck["model"]); model.to(dev); model.eval()
    mean = torch.tensor(S.MEAN).view(3, 1, 1, 1)
    std = torch.tensor(S.STD).view(3, 1, 1, 1)

    def pathways_val(path):
        video = EncodedVideo.from_path(str(path))
        dur = float(video.duration)
        start = max(0.0, dur / 2 - U.CLIP_DURATION / 2)
        fr = video.get_clip(start_sec=start,
                            end_sec=min(start + U.CLIP_DURATION, dur))["video"]
        if fr is None or fr.shape[1] < 2:
            return None
        if fr.shape[2] != U.RESIZE or fr.shape[3] != U.RESIZE:
            fr = F.interpolate(fr.permute(1, 0, 2, 3).float(),
                               size=(U.RESIZE, U.RESIZE), mode="bilinear",
                               align_corners=False).permute(1, 0, 2, 3)
        fr = fr.float()
        if fr.max() > 1.5:
            fr = fr / 255.0
        fr = (fr - mean) / std
        t = fr.shape[1]
        slow = fr[:, torch.linspace(0, t - 1, S.NUM_FRAMES_SLOW).round().long()]
        fast = fr[:, torch.linspace(0, t - 1, S.NUM_FRAMES_FAST).round().long()]
        return slow, fast

    y_true, y_pred = [], []
    with torch.no_grad():
        for k, (path, c) in enumerate(klips):
            out = pathways_val(path)
            if out is None:
                continue
            slow, fast = out
            logits = model([slow.unsqueeze(0).to(dev), fast.unsqueeze(0).to(dev)])
            y_true.append(c)
            y_pred.append(i2c.get(int(logits.argmax(1)[0]), "?"))
            if progres:
                progres(k + 1, len(klips))
    return y_true, y_pred, kelas


def _prediksi_posec3d(best_pt: Path, klips, kelas, dev, progres):
    import torch

    from ultralytics import YOLO

    from . import latih_aksi_posec3d as PC, posec3d_model as P

    ck = torch.load(best_pt, map_location=dev, weights_only=False)
    kelas = [str(c) for c in (ck.get("classes") or kelas)]
    i2c = {i: c for i, c in enumerate(kelas)}
    cfg = dict(ck.get("cfg") or P.KONFIG)
    pakai_bola = bool(ck.get("pakai_bola", False))
    masuk_ch = ck.get("masuk_ch") or (cfg["jml_orang"] * P.jml_node(cfg))

    model = P.buat_model(len(kelas), cfg, masuk_ch=masuk_ch).to(dev)
    model.load_state_dict(ck["model"]); model.eval()

    pose_path = PC._cari_model(["yolov8s-pose.pt", "yolo26n-pose.pt",
                               "yolov8n-pose.pt"]) or "yolov8s-pose.pt"
    m_pose = YOLO(pose_path)
    obj_path = PC._cari_model(["best-object-basketball.pt"]) if pakai_bola else None
    m_objek = YOLO(obj_path) if obj_path else None

    y_true, y_pred = [], []
    with torch.no_grad():
        for k, (path, c) in enumerate(klips):
            r = None
            try:
                r = PC._proses_klip(path, 0, m_pose, m_objek, dev, pakai_bola)
            except Exception:                    # noqa: BLE001
                r = None
            if r is None:
                # Pose gagal total pada klip ini — tak bisa diprediksi. Jangan
                # mengarang tebakan; lewati (tercermin sebagai klip hilang).
                if progres:
                    progres(k + 1, len(klips))
                continue
            vol = P.buat_heatmap(r["koor"], r["conf"], cfg)
            x = torch.from_numpy(vol).unsqueeze(0).to(dev)
            out = model(x)
            y_true.append(c)
            y_pred.append(i2c.get(int(out.argmax(1)[0]), "?"))
            if progres:
                progres(k + 1, len(klips))
    return y_true, y_pred, kelas


_PREDIKSI = {"videomae": _prediksi_videomae, "slowfast": _prediksi_slowfast,
             "posec3d": _prediksi_posec3d}


def prediksi_versi(best_pt: Path, versi_dir: Path, backend: str, kelas,
                   device: str, progres=None):
    """(y_true, y_pred, kelas) dengan memprediksi SETIAP klip valid/<kelas>/.

    GPU-only — tiap backend mengimpor pustakanya DI DALAM fungsinya. Melempar
    kalau backend tak dikenal atau tak ada klip valid."""
    fn = _PREDIKSI.get(backend)
    if fn is None:
        raise ValueError(f"backend tak dikenal untuk evaluasi: {backend!r}")
    klips = _klip_valid(versi_dir, [str(c) for c in (kelas or [])])
    if not klips:
        raise FileNotFoundError(
            "tak ada klip di valid/<kelas>/ — versi tak punya split valid, "
            "tak ada yang bisa diukur")
    return fn(Path(best_pt), klips, list(kelas), device, progres)


# ============================================================
# ORKESTRATOR  (dipanggil runner terlepas)
# ============================================================

def evaluasi(ds, nomor: int, *, device: str = "cuda", progres=None) -> dict:
    """Prediksi valid -> metrik -> tulis metrik.json + confusion.png ke run dir.

    Mengembalikan dict metrik (hitung_metrik). Dipanggil eval_aksi_jalan di dalam
    kunci GPU aksi; impor berat terjadi di prediksi_versi (lazy per backend)."""
    from . import klip_olah

    isi = latih_aksi.baca(ds, nomor)
    if isi is None:
        raise ValueError(f"training aksi A{nomor} tidak ada")
    best_pt = latih_aksi.bobot_training(ds, nomor, "best")
    if best_pt is None:
        raise FileNotFoundError("best.pt belum ada. Trainingnya belum selesai")
    backend = isi.get("backend") or ""
    versi_nomor = int(isi.get("versi") or 0)
    versi_dir = klip_olah.dir_versi(Path(ds), versi_nomor)
    kelas = isi.get("kelas") or latih_aksi.kelas_versi_aksi(ds, versi_nomor)

    y_true, y_pred, kelas_pakai = prediksi_versi(
        best_pt, versi_dir, backend, kelas, device, progres)
    metrik = hitung_metrik(y_true, y_pred, kelas_pakai)

    run_dir = latih_aksi.dir_latih(ds, nomor)
    run_dir.mkdir(parents=True, exist_ok=True)
    # Gambar hanya kalau matplotlib ada — angka metriknya tetap ditulis tanpanya.
    try:
        png = matriks_png(metrik["matriks"], kelas_pakai, metrik)
        (run_dir / "confusion.png").write_bytes(png)
        metrik["punya_gambar"] = True
    except RuntimeError as e:
        log.warning("confusion.png dilewati: %s", e)
        metrik["punya_gambar"] = False
    (run_dir / "metrik.json").write_text(json.dumps(metrik, indent=1))
    return metrik


# ============================================================
# BERKAS HASIL + STATUS  (padanan evaluasi_jalan.baca)
# ============================================================

def berkas_hasil(ds, nomor: int) -> Path:
    return latih_aksi._dir(ds) / f"A{int(nomor)}.eval.json"


def baca_hasil(ds, nomor: int) -> dict | None:
    p = berkas_hasil(ds, nomor)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def tulis_hasil(ds, nomor: int, isi: dict) -> None:
    p = berkas_hasil(ds, nomor)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(isi, indent=1))
    os.replace(tmp, p)


def hidup(pid) -> bool:
    """Proses evaluasi aksi masih berjalan — ditandai 'eval_aksi_jalan' di
    cmdline-nya (BERBEDA dari 'latih_aksi_jalan', jadi tak tertukar dengan
    training yang berjalan di lajur GPU yang sama)."""
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
        return "eval_aksi_jalan" in cmd
    except OSError:
        return True


def status(ds, nomor: int) -> dict | None:
    """Hasil eval + keadaan terkini. 'jalan' yang prosesnya lenyap -> 'hilang'
    (padanan latih_aksi.status: proses mati di tengah tak boleh 'jalan' abadi)."""
    isi = baca_hasil(ds, nomor)
    if isi is None:
        return None
    if isi.get("keadaan") in ("antre", "jalan") and not hidup(isi.get("pid")):
        isi["keadaan"] = "hilang"
    isi["punya_gambar"] = (latih_aksi.dir_latih(ds, nomor)
                           / "confusion.png").exists()
    return isi


def ada_yang_jalan(ds, nomor: int) -> bool:
    isi = baca_hasil(ds, nomor)
    return bool(isi and isi.get("keadaan") in ("antre", "jalan")
                and hidup(isi.get("pid")))


# ============================================================
# MELUNCURKAN RUNNER TERLEPAS  (padanan latih.jalankan_evaluasi)
# ============================================================

def jalankan(ds, nomor: int) -> dict:
    """Luncurkan evaluasi sebagai SUBPROSES terlepas (lajur GPU aksi yang sama).

    Menolak kalau best.pt belum ada atau sebuah evaluasi untuk nomor ini masih
    berjalan. Tak memblokir event loop server (impor torch/matplotlib terjadi di
    subproses, bukan di sini)."""
    isi = latih_aksi.baca(ds, nomor)
    if isi is None:
        raise ValueError(f"training aksi A{nomor} tidak ada")
    if latih_aksi.bobot_training(ds, nomor, "best") is None:
        raise ValueError("belum ada best.pt. Trainingnya belum selesai")
    if ada_yang_jalan(ds, nomor):
        raise ValueError("evaluasi untuk training ini masih berjalan")

    with _kunci:
        akar = Path(__file__).resolve().parents[2]
        log_p = latih_aksi._dir(ds) / f"A{nomor}.eval.log"
        log_p.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.setdefault("PYTHONPATH", str(akar))
        env["YOLO_VERBOSE"] = "False"
        tulis_hasil(ds, nomor, {"keadaan": "antre",
                                "mulai": datetime.now().strftime("%Y-%m-%d %H:%M")})
        with open(log_p, "ab", buffering=0) as f:
            f.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} evaluasi A{nomor} "
                    "===\n".encode())
            p = subprocess.Popen(
                [sys.executable, "-m", "app.services.eval_aksi_jalan",
                 str(Path(ds).resolve()), str(nomor)],
                cwd=str(akar), env=env, stdout=f, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True)
        tulis_hasil(ds, nomor, {"keadaan": "antre", "pid": p.pid,
                                "mulai": datetime.now().strftime("%Y-%m-%d %H:%M")})
    log.info("evaluasi aksi A%s dimulai di %s (pid %s)", nomor, Path(ds).name,
             p.pid)
    return {"pid": p.pid}
