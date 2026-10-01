"""
Bagian yang IDENTIK antara trainer VideoMAE dan SlowFast — dikumpulkan di sini
supaya "resep training yang disamakan untuk perbandingan adil" (lihat komentar
train_action-slowfast-v2.py) benar-benar satu sumber, bukan dua salinan yang
pelan-pelan menyimpang.

ATURAN MUTLAK: tak ada impor berat di tingkat modul. torch/pytorchvideo diimpor
DI DALAM fungsi, supaya server CPU bisa mengimpor seluruh aplikasi — termasuk
runner yang mengimpor trainer yang mengimpor berkas ini — tanpa torch terpasang.
Berkas ini TIDAK dijalankan di CI; yang dijaga di CI hanya bahwa ia bisa diimpor
bersih (lihat test_latih_aksi).

KONTRAK DATA: versi klip aksi di .versi/vN/ bertata letak folder-per-kelas
    train/<kelas>/*.mp4   (asli + _aug_ + _bal_)
    valid/<kelas>/*.mp4   (asli saja — diverifikasi bersih)
Pembelahan train/valid SUDAH dibekukan per video sumber saat build (klip_olah),
jadi di sini TAK ADA pembelahan lagi — tinggal dibaca apa adanya.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path


# ============================================================
# DATASET  (folder-per-kelas, sudah terbelah)
# ============================================================

def extract_video_id(nama: str) -> str:
    """Id video sumber dari nama klip — buang suffix _aug_/_bal_ lalu ambil
    token 11-karakter (id YouTube) kalau ada. Dipakai bila suatu saat perlu
    membelah ulang; untuk versi beku kita, pembelahan sudah terjadi."""
    stem = Path(nama).stem
    for s in ("_aug_", "_bal_"):
        if s in stem:
            stem = stem[:stem.index(s)]
    for p in stem.split("_"):
        if len(p) == 11:
            return p
    bagian = stem.split("_")
    return bagian[0] if bagian else stem


def kelas_dari_yaml(versi_dir: Path) -> list[str]:
    """Daftar kelas (urut) dari aksi.yaml versinya — names: [...]."""
    ay = Path(versi_dir) / "aksi.yaml"
    try:
        import yaml

        y = yaml.safe_load(ay.read_text(encoding="utf-8")) or {}
        return [str(x) for x in (y.get("names") or [])]
    except Exception:                            # noqa: BLE001
        return []


def _kumpul(root: Path, kelas: list[str]):
    out, found = [], {}
    for c in kelas:
        d = Path(root) / c
        klip = sorted(d.glob("*.mp4")) if d.is_dir() else []
        found[c] = len(klip)
        out += [(p, c) for p in klip]
    return out, found


def baca_dataset(versi_dir: Path, kelas: list[str]):
    """(train_s, val_s, c2i). Membaca train/ + valid/ folder-per-kelas.

    Melempar ValueError kalau valid/ memuat klip _aug_/_bal_ — angka validasi
    pada klip augmentasi menipu (invarian valid-bersih dari build)."""
    base = Path(versi_dir)
    c2i = {c: i for i, c in enumerate(kelas)}
    train_s, _ = _kumpul(base / "train", kelas)
    val_s, _ = _kumpul(base / "valid", kelas)
    kotor = [p.name for p, _ in val_s
             if "_aug_" in p.stem or "_bal_" in p.stem]
    if kotor:
        raise ValueError(
            f"valid/ memuat {len(kotor)} klip augmentasi (mis. {kotor[0]}) — "
            "angka validasi akan menipu")
    return train_s, val_s, c2i


# ============================================================
# SAMPLER + BOBOT KELAS  (torch diimpor di dalam)
# ============================================================

def make_sampler(samples, c2i):
    """WeightedRandomSampler 1/count, DI-CAP 1,5x rata-rata — identik acuan."""
    import torch
    from torch.utils.data import WeightedRandomSampler

    labels = [c2i[c] for _, c in samples]
    counts = Counter(labels)
    raw = {l: 1.0 / counts[l] for l in counts}
    avg = sum(raw.values()) / max(len(raw), 1)
    cap = avg * 1.5
    raw = {l: min(w, cap) for l, w in raw.items()}
    bobot = [raw[l] for l in labels]
    return WeightedRandomSampler(bobot, len(labels), replacement=True)


def class_weights(samples, c2i, device):
    import torch

    labels = [c2i[c] for _, c in samples]
    counts = Counter(labels)
    n = len(c2i)
    total = len(labels)
    w = [total / (n * counts.get(i, 1)) for i in range(n)]
    return torch.tensor(w, dtype=torch.float32, device=device)


# ============================================================
# LOSS + WARMUP  (fungsional supaya tak perlu nn.Module di tingkat modul)
# ============================================================

def focal_loss(logits, targets, alpha=None, gamma=2.0, label_smoothing=0.0):
    """FocalLoss identik acuan, tetapi sebagai FUNGSI (bukan nn.Module) supaya
    berkas ini tak memegang kelas turunan nn.Module di tingkat modul."""
    import torch
    import torch.nn.functional as F

    n_cls = logits.size(1)
    if label_smoothing > 0:
        smooth = label_smoothing / n_cls
        toh = torch.zeros_like(logits).scatter_(1, targets.unsqueeze(1), 1.0)
        toh = toh * (1 - label_smoothing) + smooth
        ce = -(toh * F.log_softmax(logits, dim=1)).sum(dim=1)
    else:
        ce = F.cross_entropy(logits, targets, reduction="none")
    p_t = F.softmax(logits, dim=1).gather(1, targets.unsqueeze(1)).squeeze(1)
    fw = (1 - p_t) ** gamma
    if alpha is not None:
        fw = alpha.gather(0, targets) * fw
    return (fw * ce).mean()


def warmup_lr(optimizer, epoch_in_phase, warmup_epochs, base_lrs):
    """Linear warmup — identik acuan."""
    if epoch_in_phase < warmup_epochs:
        faktor = (epoch_in_phase + 1) / warmup_epochs
        for pg, base in zip(optimizer.param_groups, base_lrs):
            pg["lr"] = base * faktor


# Tetapan resep yang DISAMAKAN antar backend (train_action-slowfast-v2 menyebut
# ini "diselaraskan dengan VideoMAE v12" supaya perbandingannya adil).
CLIP_DURATION = 1.0
RESIZE = 224
NUM_FRAMES_VIDEOMAE = 16
NUM_FRAMES_SLOW = 8
NUM_FRAMES_FAST = 32
PHASE1_EPOCHS = 15
LR_HEAD_P1 = 1e-3
LR_HEAD_P2 = 5e-5
LR_BB_P2 = 1e-5
LLRD_FACTOR = 0.75
WEIGHT_DECAY = 0.05
WARMUP_EPOCHS = 8
PATIENCE = 8
MA_WINDOW = 5
FOCAL_GAMMA = 2.0
LABEL_SMOOTH = 0.15
MIXUP_ALPHA = 0.2
MIXUP_PROB = 0.3
DROPOUT_P2 = 0.1
SEED = 42
