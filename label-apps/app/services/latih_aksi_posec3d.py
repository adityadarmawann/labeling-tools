"""
Trainer backend PoseC3D — port setia dari posec3d/action-v14/
{01_ekstrak_skeleton.py, 02_latih.py}, disusun jadi fungsi `latih(...)` dua
langkah yang dipanggil latih_aksi_jalan.

    1. EKSTRAK SKELETON: tiap klip .mp4 -> (M,T,V,2) koordinat + (M,T,V) conf,
       lewat ultralytics YOLO-pose (COCO 17) + detektor objek proyek untuk node
       bola ke-18. Pelacak IoU sederhana + pemilih pelaku + penambal frame
       bolong. Disimpan sebagai .pkl di run_dir (sekali; latih tak buka video lagi).
    2. LATIH: PoseC3D standalone (posec3d_model) di atas peta panas skeleton.
       CrossEntropy + bobot kelas + label smoothing + OneCycleLR + AMP + early stop.

TANPA mmcv/mmaction2/mmpose — hanya ultralytics + torch + numpy + cv2 (rencana
G#1). Semua impor berat DI DALAM fungsi; modul wajib bisa diimpor di server CPU.
TIDAK dijalankan di CI.

Model pose/objek dicari di latih.dir_bobot() (tempat best-object-basketball.pt &
yolov8s-pose.pt diletakkan, sama dengan bobot YOLO lain). Bila detektor objek tak
ada, node bola DIMATIKAN dengan anggun (seperti --tanpa-bola di acuan), bukan gagal.
"""
from __future__ import annotations

import pickle
from collections import deque
from pathlib import Path

import numpy as np

from . import latih as svc_latih, latih_aksi_umum as U
from . import posec3d_model as P

CFG = dict(P.KONFIG)


# ══════════════════════════ skeleton: pembantu numpy ══════════════════════════
def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    la = (a[2] - a[0]) * (a[3] - a[1])
    lb = (b[2] - b[0]) * (b[3] - b[1])
    return inter / max(la + lb - inter, 1e-6)


def _ambil_indeks(n, target):
    if n == 0:
        return []
    return np.linspace(0, n - 1, target).round().astype(int).tolist()


class _Pelacak:
    """Pelacak orang antar frame via IoU. Sengaja sederhana — klip ~1 detik."""

    def __init__(self):
        self.lintasan = {}
        self.id_berikut = 0

    def perbarui(self, kotak_list, kp_list, conf_list, frame_idx):
        dipakai = set()
        for tid, t in list(self.lintasan.items()):
            terbaik, skor = -1, CFG["iou_lacak"]
            for i, kotak in enumerate(kotak_list):
                if i in dipakai:
                    continue
                s = _iou(t["kotak"], kotak)
                if s > skor:
                    terbaik, skor = i, s
            if terbaik >= 0:
                dipakai.add(terbaik)
                t["kotak"] = kotak_list[terbaik]; t["hilang"] = 0
                t["riwayat"][frame_idx] = (kp_list[terbaik], conf_list[terbaik],
                                           kotak_list[terbaik])
            else:
                t["hilang"] += 1
                if t["hilang"] > CFG["maks_hilang"]:
                    del self.lintasan[tid]
        for i, kotak in enumerate(kotak_list):
            if i in dipakai:
                continue
            self.lintasan[self.id_berikut] = dict(
                kotak=kotak, hilang=0,
                riwayat={frame_idx: (kp_list[i], conf_list[i], kotak)})
            self.id_berikut += 1


def _skor_lintasan(riwayat, n_frame, lebar, tinggi, bola_per_frame):
    if not riwayat:
        return 0.0
    cx_f, cy_f = lebar / 2.0, tinggi / 2.0
    diag = (lebar ** 2 + tinggi ** 2) ** 0.5
    n_tengah, n_luas, n_bola = [], [], []
    for fi, (_kp, _cf, kotak) in riwayat.items():
        cx = (kotak[0] + kotak[2]) / 2.0; cy = (kotak[1] + kotak[3]) / 2.0
        jarak = ((cx - cx_f) ** 2 + (cy - cy_f) ** 2) ** 0.5
        n_tengah.append(1.0 - min(jarak / (diag / 2), 1.0))
        luas = max((kotak[2] - kotak[0]) * (kotak[3] - kotak[1]), 0.0)
        n_luas.append(min(luas / (lebar * tinggi), 1.0))
        b = bola_per_frame.get(fi)
        if b is not None:
            jb = ((cx - b[0]) ** 2 + (cy - b[1]) ** 2) ** 0.5
            n_bola.append(1.0 - min(jb / (diag / 2), 1.0))
    s_tengah = float(np.mean(n_tengah))
    s_luas = float(np.mean(n_luas)) ** 0.5
    s_hadir = len(riwayat) / max(n_frame, 1)
    s_bola = float(np.mean(n_bola)) if n_bola else 0.0
    return (CFG["bobot_tengah"] * s_tengah + CFG["bobot_luas"] * s_luas +
            CFG["bobot_kehadiran"] * s_hadir + CFG["bobot_bola"] * s_bola)


def _pilih_orang(lintasan, n_frame, lebar, tinggi, bola_per_frame):
    if not lintasan:
        return []
    skor = {tid: _skor_lintasan(t["riwayat"], n_frame, lebar, tinggi, bola_per_frame)
            for tid, t in lintasan.items()}
    urut = sorted(skor, key=skor.get, reverse=True)
    pelaku = urut[0]
    terpilih = [pelaku]
    if CFG["jml_orang"] > 1 and len(urut) > 1:
        pk = lintasan[pelaku]["riwayat"]
        pusat = np.mean([[(k[2][0] + k[2][2]) / 2, (k[2][1] + k[2][3]) / 2]
                         for k in pk.values()], axis=0)
        jarak = {}
        for tid in urut[1:]:
            rw = lintasan[tid]["riwayat"]
            p = np.mean([[(k[2][0] + k[2][2]) / 2, (k[2][1] + k[2][3]) / 2]
                         for k in rw.values()], axis=0)
            jarak[tid] = float(np.linalg.norm(p - pusat))
        terpilih.append(min(jarak, key=jarak.get))
    return terpilih[:CFG["jml_orang"]]


def _tambal(koor, conf):
    T, V, _ = koor.shape
    for v in range(V):
        ada = np.where(conf[:, v] > 0)[0]
        if len(ada) < 2:
            continue
        for a, b in zip(ada[:-1], ada[1:]):
            lubang = b - a - 1
            if 0 < lubang <= CFG["maks_tambal"]:
                for t in range(a + 1, b):
                    w = (t - a) / (b - a)
                    koor[t, v] = koor[a, v] * (1 - w) + koor[b, v] * w
                    conf[t, v] = min(conf[a, v], conf[b, v]) * 0.5
    return koor, conf


def _baca_frame(path):
    import cv2

    cap = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        frames.append(fr)
    cap.release()
    return frames


def _proses_klip(path, label_idx, m_pose, m_objek, device, pakai_bola):
    frames = _baca_frame(path)
    if not frames:
        return None
    tinggi, lebar = frames[0].shape[:2]
    idx = _ambil_indeks(len(frames), CFG["panjang_klip"])
    pilihan = [frames[i] for i in idx]

    hasil_pose = m_pose.predict(pilihan, verbose=False, conf=CFG["conf_pose"],
                                device=device)
    bola_per_frame = {}
    if m_objek is not None and pakai_bola:
        hasil_obj = m_objek.predict(pilihan, verbose=False, conf=CFG["conf_bola"],
                                    device=device)
        for fi, r in enumerate(hasil_obj):
            if r.boxes is None or len(r.boxes) == 0:
                continue
            kls = r.boxes.cls.cpu().numpy().astype(int)
            cf = r.boxes.conf.cpu().numpy()
            xy = r.boxes.xyxy.cpu().numpy()
            m = kls == CFG["kelas_bola"]
            if not m.any():
                continue
            j = int(np.argmax(cf[m])); b = xy[m][j]
            bola_per_frame[fi] = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2,
                                  float(cf[m][j]))

    pelacak = _Pelacak()
    frame_ada_orang = 0
    for fi, r in enumerate(hasil_pose):
        kp = r.keypoints
        if kp is None or kp.conf is None or len(kp.conf) == 0:
            continue
        frame_ada_orang += 1
        xy = kp.xy.cpu().numpy(); cf = kp.conf.cpu().numpy()
        kotak = r.boxes.xyxy.cpu().numpy()
        pelacak.perbarui([k for k in kotak], [x for x in xy], [c for c in cf], fi)
    if not pelacak.lintasan:
        return None

    terpilih = _pilih_orang(pelacak.lintasan, len(pilihan), lebar, tinggi,
                            bola_per_frame)
    if not terpilih:
        return None

    V = P.jml_node(CFG)
    T, M = len(pilihan), CFG["jml_orang"]
    koor = np.zeros((M, T, V, 2), dtype=np.float32)
    conf = np.zeros((M, T, V), dtype=np.float32)
    for mi, tid in enumerate(terpilih):
        rw = pelacak.lintasan[tid]["riwayat"]
        for fi, (kp, cf, _kotak) in rw.items():
            n = min(CFG["jml_sendi"], len(kp))
            koor[mi, fi, :n] = kp[:n]
            c = cf[:n].copy(); c[c < CFG["conf_keypoint"]] = 0.0
            conf[mi, fi, :n] = c
        koor[mi], conf[mi] = _tambal(koor[mi], conf[mi])
    if pakai_bola and CFG["pakai_node_bola"]:
        ib = P.idx_node_bola(CFG)
        for fi, b in bola_per_frame.items():
            koor[0, fi, ib] = (b[0], b[1]); conf[0, fi, ib] = b[2]

    return dict(nama=path.name, label=label_idx, koor=koor, conf=conf,
                mutu=float(frame_ada_orang / max(len(pilihan), 1)))


def _cari_model(nama_kandidat: list[str]) -> str | None:
    """Cari berkas .pt pertama yang cocok di svc_latih.dir_bobot(). None kalau tak
    ada (pemanggil memutuskan: unduh via ultralytics, atau matikan fiturnya).

    Dialias svc_latih (bukan `latih`) karena fungsi entri trainer di berkas ini
    juga bernama `latih` dan akan membayangi impor modulnya di global."""
    for d in svc_latih.dir_bobot():
        for nama in nama_kandidat:
            p = Path(d) / nama
            if p.is_file():
                return str(p)
    return None


def _ekstrak_split(versi_dir: Path, split: str, kelas, c2i, m_pose, m_objek,
                   device, pakai_bola):
    data = []
    base = Path(versi_dir) / split
    for c in kelas:
        d = base / c
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.mp4")):
            try:
                r = _proses_klip(f, c2i[c], m_pose, m_objek, device, pakai_bola)
            except Exception:                    # noqa: BLE001
                r = None
            if r is not None:
                data.append(r)
    return data


# ══════════════════════════ dataset skeleton (torch lazy) ══════════════════════════
_PASANGAN_LR = [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10), (11, 12), (13, 14),
                (15, 16)]


def latih(*, versi_dir: Path, run_dir: Path, par: dict, progres, device: str,
          ds=None) -> None:
    import random

    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, Dataset

    from ultralytics import YOLO

    kelas = U.kelas_dari_yaml(versi_dir)
    if len(kelas) < 2:
        raise ValueError("versi ini punya < 2 kelas aksi. Tak bisa dilatih")
    c2i = {c: i for i, c in enumerate(kelas)}
    epochs = int(par.get("epochs") or 60)
    batch = int(par.get("batch") or 16)
    lr = float(par.get("lr") or 1e-3)
    dev = device if device in ("cuda", "cpu") else "cpu"
    if dev == "cuda" and not torch.cuda.is_available():
        dev = "cpu"

    torch.manual_seed(CFG["seed"]); np.random.seed(CFG["seed"])
    random.seed(CFG["seed"])

    weights = run_dir / "weights"
    weights.mkdir(parents=True, exist_ok=True)
    best_pt, last_pt = weights / "best.pt", weights / "last.pt"

    # ---------- Langkah 1: ekstrak skeleton -> .pkl ----------
    pose_path = _cari_model(["yolov8s-pose.pt", "yolo26n-pose.pt",
                             "yolov8n-pose.pt"]) or "yolov8s-pose.pt"
    obj_path = _cari_model(["best-object-basketball.pt"])
    pakai_bola = bool(CFG["pakai_node_bola"] and obj_path)
    m_pose = YOLO(pose_path)
    m_objek = YOLO(obj_path) if pakai_bola else None

    pkl_tr = run_dir / "skeleton_train.pkl"
    pkl_va = run_dir / "skeleton_val.pkl"
    if not pkl_tr.exists() or not pkl_va.exists():
        data_tr = _ekstrak_split(versi_dir, "train", kelas, c2i, m_pose,
                                 m_objek, dev, pakai_bola)
        data_va = _ekstrak_split(versi_dir, "valid", kelas, c2i, m_pose,
                                 m_objek, dev, pakai_bola)
        meta = dict(kelas=kelas, pakai_bola=pakai_bola,
                    jml_node=P.jml_node(CFG), jml_orang=CFG["jml_orang"])
        with open(pkl_tr, "wb") as fh:
            pickle.dump({**meta, "data": data_tr}, fh, protocol=4)
        with open(pkl_va, "wb") as fh:
            pickle.dump({**meta, "data": data_va}, fh, protocol=4)
    with open(pkl_tr, "rb") as fh:
        isi_tr = pickle.load(fh)
    with open(pkl_va, "rb") as fh:
        isi_va = pickle.load(fh)
    if not isi_tr["data"] or not isi_va["data"]:
        raise ValueError("ekstraksi skeleton tak menghasilkan klip di train/valid "
                         "— pose gagal pada semua klip? periksa model pose")
    pakai_bola = bool(isi_tr.get("pakai_bola"))

    # ---------- dataset skeleton ----------
    class DataSkeleton(Dataset):
        def __init__(self, isi, latih_mode):
            self.data = isi["data"]; self.latih = latih_mode

        def __len__(self):
            return len(self.data)

        def _aug(self, koor, conf):
            V = koor.shape[2]
            m = conf > 0
            if not m.any():
                return koor, conf
            cx = koor[..., 0][m].mean(); cy = koor[..., 1][m].mean()
            rentang = max(np.ptp(koor[..., 0][m]), np.ptp(koor[..., 1][m]), 1.0)
            if random.random() < CFG["aug_flip"]:
                koor[..., 0] = 2 * cx - koor[..., 0]
                for a, b in _PASANGAN_LR:
                    if a < V and b < V:
                        koor[:, :, [a, b]] = koor[:, :, [b, a]]
                        conf[:, :, [a, b]] = conf[:, :, [b, a]]
            if CFG["aug_putar"] > 0:
                th = np.deg2rad(random.uniform(-CFG["aug_putar"], CFG["aug_putar"]))
                c, s = np.cos(th), np.sin(th)
                dx = koor[..., 0] - cx; dy = koor[..., 1] - cy
                koor[..., 0] = cx + c * dx - s * dy
                koor[..., 1] = cy + s * dx + c * dy
            if CFG["aug_skala"] > 0:
                sk = 1.0 + random.uniform(-CFG["aug_skala"], CFG["aug_skala"])
                koor[..., 0] = cx + (koor[..., 0] - cx) * sk
                koor[..., 1] = cy + (koor[..., 1] - cy) * sk
            if CFG["aug_geser"] > 0:
                koor[..., 0] += random.uniform(-CFG["aug_geser"], CFG["aug_geser"]) * rentang
                koor[..., 1] += random.uniform(-CFG["aug_geser"], CFG["aug_geser"]) * rentang
            return koor, conf

        def __getitem__(self, i):
            d = self.data[i]
            koor = d["koor"].copy(); conf = d["conf"].copy()
            if self.latih:
                koor, conf = self._aug(koor, conf)
            vol = P.buat_heatmap(koor, conf, CFG)
            return torch.from_numpy(vol), d["label"]

    ds_tr = DataSkeleton(isi_tr, True)
    ds_va = DataSkeleton(isi_va, False)
    dl_tr = DataLoader(ds_tr, batch_size=batch, shuffle=True,
                       num_workers=CFG["pekerja"], pin_memory=(dev == "cuda"),
                       drop_last=len(ds_tr) > batch)
    dl_va = DataLoader(ds_va, batch_size=batch, shuffle=False,
                       num_workers=CFG["pekerja"], pin_memory=(dev == "cuda"))

    # bobot kelas (steal dsb jauh lebih sedikit)
    hitung = np.bincount([d["label"] for d in isi_tr["data"]], minlength=len(kelas))
    bobot = hitung.sum() / (len(kelas) * np.maximum(hitung, 1))
    bobot = torch.tensor(bobot, dtype=torch.float32, device=dev)

    masuk_ch = CFG["jml_orang"] * P.jml_node(CFG)
    model = P.buat_model(len(kelas), CFG, masuk_ch=masuk_ch).to(dev)
    kriteria = nn.CrossEntropyLoss(weight=bobot,
                                   label_smoothing=CFG["label_smoothing"])
    optim = torch.optim.AdamW(model.parameters(), lr=lr,
                              weight_decay=CFG["weight_decay"])
    # epochs boleh 1..1000 (latih_aksi.PAR). Kalau epochs <= warmup_epoch,
    # warmup/epochs bisa >= 1 dan OneCycleLR menolak pct_start di luar (0,1) —
    # batasi <= 0,5 supaya run pendek (uji cepat) tak gagal keras.
    pct_start = min(0.5, CFG["warmup_epoch"] / max(epochs, 1))
    sched = torch.optim.lr_scheduler.OneCycleLR(
        optim, max_lr=lr, epochs=epochs, steps_per_epoch=max(len(dl_tr), 1),
        pct_start=pct_start)
    scaler = torch.amp.GradScaler("cuda") if dev == "cuda" else None

    def akurasi_rata_kelas(benar, pred):
        nilai = []
        for k in range(len(kelas)):
            m = benar == k
            if m.sum():
                nilai.append(float((pred[m] == k).mean()))
        return float(np.mean(nilai)) if nilai else 0.0

    def jalankan(loader, optim=None):
        latih_mode = optim is not None
        model.train(latih_mode)
        total_rugi, bb, pp = 0.0, [], []
        for x, y in loader:
            x = x.to(dev, non_blocking=True); y = y.to(dev, non_blocking=True)
            with torch.autocast("cuda", enabled=(dev == "cuda")):
                out = model(x); rugi = kriteria(out, y)
            if latih_mode:
                optim.zero_grad(set_to_none=True)
                if scaler is not None:
                    scaler.scale(rugi).backward()
                    scaler.unscale_(optim)
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
                    scaler.step(optim); scaler.update()
                else:
                    rugi.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
                    optim.step()
                sched.step()
            total_rugi += float(rugi) * len(y)
            bb.append(y.detach().cpu().numpy())
            pp.append(out.detach().argmax(1).cpu().numpy())
        b = np.concatenate(bb); p = np.concatenate(pp)
        return (total_rugi / len(b), float((b == p).mean()),
                akurasi_rata_kelas(b, p))

    def simpan(path, epoch, kelas_va):
        torch.save(dict(model=model.state_dict(), classes=kelas, arch="posec3d",
                        pakai_bola=pakai_bola, jml_node=P.jml_node(CFG),
                        jml_orang=CFG["jml_orang"], val_acc_ma=kelas_va,
                        epoch=epoch, masuk_ch=masuk_ch, cfg=CFG), path)

    best = 0.0
    sabar = 0
    for ep in range(1, epochs + 1):
        rt, at, _ = jalankan(dl_tr, optim)
        with torch.no_grad():
            rv, av, kv = jalankan(dl_va)
        # val_acc_ma = akurasi RATA-RATA KELAS (metrik utama PoseC3D).
        progres(ep, train_loss=rt, train_acc=at, val_loss=rv, val_acc=av,
                val_acc_ma=kv)
        simpan(last_pt, ep, kv)
        if kv > best:
            best = kv; sabar = 0
            simpan(best_pt, ep, kv)
        else:
            sabar += 1
            if sabar >= CFG["sabar"]:
                break
