"""
Trainer backend SlowFast-R50 — port setia dari
action-labeler/modelling/train_action-slowfast-v2.py, disusun jadi fungsi
`latih(...)` yang dipanggil latih_aksi_jalan.

Resep training SENGAJA sama dengan VideoMAE (lewat latih_aksi_umum) supaya
angka kedua backend bisa dibandingkan adil — itu seluruh maksud v2 acuan. Yang
khas arsitektur: dua jalur slow(8)/fast(32) dari jendela 1 detik yang sama,
normalisasi 0.45/0.225 (statistik Kinetics SlowFast), LLRD atas blok ResNet,
dan head-prefix DINAMIS (menghindari bug v1 yang melatih nol parameter di
Phase 1).

BUG YANG DIPERBAIKI SAAT PORT: get_param_groups_llrd acuan memakai `n_blocks`
yang TAK PERNAH didefinisikan di cabang fallback-nya (NameError begitu ada
parameter sisa di luar blok). Di sini grup sisa memakai len(depth) sebagai
pangkat peluruhan — jumlah blok ber-parameter yang benar-benar ikut LLRD —
sehingga LR-nya satu tingkat di bawah blok backbone terbawah, konsisten dengan
semangat LLRD. Lihat komentar di param_groups_llrd.

SEMUA impor berat ada DI DALAM latih(): modul wajib bisa diimpor di server CPU
tanpa torch/pytorchvideo. TIDAK dijalankan di CI.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path

from . import latih_aksi_umum as U

NUM_FRAMES_SLOW = U.NUM_FRAMES_SLOW
NUM_FRAMES_FAST = U.NUM_FRAMES_FAST
RESIZE = U.RESIZE
CLIP_DURATION = U.CLIP_DURATION
MEAN = [0.45, 0.45, 0.45]
STD = [0.225, 0.225, 0.225]
GRAD_ACCUM = 9                    # batch efektif 2x9 = 18 (sama VideoMAE 6x3)
DROPOUT_HEAD = 0.5


def latih(*, versi_dir: Path, run_dir: Path, par: dict, progres, device: str,
          ds=None) -> None:
    import random

    import numpy as np
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from torch.utils.data import DataLoader, Dataset

    from pytorchvideo.data.encoded_video import EncodedVideo
    from pytorchvideo.models.hub import slowfast_r50

    kelas = U.kelas_dari_yaml(versi_dir)
    if len(kelas) < 2:
        raise ValueError("versi ini punya < 2 kelas aksi. Tak bisa dilatih")
    epochs = int(par.get("epochs") or 80)
    batch_size = int(par.get("batch") or 2)
    dev = torch.device(device if device in ("cuda", "cpu") else "cpu")
    if dev.type == "cuda" and not torch.cuda.is_available():
        dev = torch.device("cpu")

    random.seed(U.SEED); np.random.seed(U.SEED); torch.manual_seed(U.SEED)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(U.SEED)

    weights = run_dir / "weights"
    weights.mkdir(parents=True, exist_ok=True)
    best_pt, last_pt = weights / "best.pt", weights / "last.pt"

    train_s, val_s, c2i = U.baca_dataset(versi_dir, kelas)
    if not train_s or not val_s:
        raise ValueError("train/ atau valid/ kosong. Versi belum siap dilatih")

    def load_pathways(path, is_train):
        try:
            video = EncodedVideo.from_path(str(path))
            dur = float(video.duration)
            if is_train:
                start = random.uniform(0, max(0.0, dur - CLIP_DURATION))
            else:
                start = max(0.0, dur / 2 - CLIP_DURATION / 2)
            clip_end = min(start + CLIP_DURATION, dur)
            frames = video.get_clip(start_sec=start, end_sec=clip_end)["video"]
            if frames is None or frames.shape[1] < 2:
                return None
            if frames.shape[2] != RESIZE or frames.shape[3] != RESIZE:
                frames = F.interpolate(
                    frames.permute(1, 0, 2, 3).float(), size=(RESIZE, RESIZE),
                    mode="bilinear", align_corners=False).permute(1, 0, 2, 3)
            frames = frames.float()
            if frames.max() > 1.5:
                frames = frames / 255.0
            mean = torch.tensor(MEAN).view(3, 1, 1, 1)
            std = torch.tensor(STD).view(3, 1, 1, 1)
            frames = (frames - mean) / std
            T = frames.shape[1]
            slow_idx = torch.linspace(0, T - 1, NUM_FRAMES_SLOW).round().long()
            fast_idx = torch.linspace(0, T - 1, NUM_FRAMES_FAST).round().long()
            return frames[:, slow_idx], frames[:, fast_idx]
        except Exception:                        # noqa: BLE001
            return None

    class DS(Dataset):
        def __init__(self, samples, is_train):
            self.samples = samples; self.is_train = is_train

        def __len__(self):
            return len(self.samples)

        def __getitem__(self, i):
            path, cls = self.samples[i]
            out = load_pathways(path, self.is_train)
            if out is None:
                out = (torch.zeros(3, NUM_FRAMES_SLOW, RESIZE, RESIZE),
                       torch.zeros(3, NUM_FRAMES_FAST, RESIZE, RESIZE))
            slow, fast = out
            if self.is_train:
                if random.random() < 0.3:
                    slow = torch.flip(slow, dims=[3])
                    fast = torch.flip(fast, dims=[3])
                if random.random() < 0.3:
                    g = random.uniform(0.75, 1.25)
                    slow = slow * g; fast = fast * g
            return slow, fast, c2i[cls]

    def collate(batch):
        return (torch.stack([b[0] for b in batch]),
                torch.stack([b[1] for b in batch]),
                torch.tensor([b[2] for b in batch], dtype=torch.long))

    train_loader = DataLoader(
        DS(train_s, True), batch_size=batch_size,
        sampler=U.make_sampler(train_s, c2i), num_workers=4,
        pin_memory=(dev.type == "cuda"), collate_fn=collate, drop_last=True)
    val_loader = DataLoader(
        DS(val_s, False), batch_size=batch_size, shuffle=False, num_workers=4,
        pin_memory=(dev.type == "cuda"), collate_fn=collate)

    # ---------- model ----------
    model = slowfast_r50(pretrained=True)
    in_feat = model.blocks[-1].proj.in_features
    model.blocks[-1].proj = nn.Sequential(
        nn.Dropout(p=DROPOUT_HEAD), nn.Linear(in_feat, len(kelas)))
    model = model.to(dev)

    def head_prefix():
        # blok terakhir = head; ditentukan dinamis supaya tak salah seperti v1
        # yang menuliskan "blocks.5" keras dan melatih nol parameter di Phase 1.
        return f"blocks.{len(model.blocks) - 1}.proj"

    def param_groups_llrd(head_lr, bb_lr, factor=U.LLRD_FACTOR):
        groups, used = [], set()
        hp = head_prefix()
        head = [p for n, p in model.named_parameters()
                if n.startswith(hp) and p.requires_grad]
        if head:
            groups.append({"params": head, "lr": head_lr})
            used.update(id(p) for p in head)
        # Hanya blok yang PUNYA parameter yang ikut peluruhan (blocks.5 kosong
        # parameter di slowfast_r50). rank 0 = blok teratas -> LR penuh.
        depth = []
        for b in range(len(model.blocks)):
            ps = [p for n, p in model.named_parameters()
                  if n.startswith(f"blocks.{b}.") and p.requires_grad
                  and id(p) not in used]
            if ps:
                depth.append((b, ps))
        for rank, (_b, ps) in enumerate(reversed(depth)):
            groups.append({"params": ps, "lr": bb_lr * (factor ** rank)})
            used.update(id(p) for p in ps)
        rest = [p for p in model.parameters()
                if p.requires_grad and id(p) not in used]
        if rest:
            # PERBAIKAN BUG acuan: acuan memakai `n_blocks` (tak terdefinisi,
            # NameError). Yang benar len(depth) — satu tingkat di bawah blok
            # backbone terbawah, menjaga urutan peluruhan LLRD tetap monoton.
            groups.append({"params": rest, "lr": bb_lr * (factor ** len(depth))})
        return groups

    alpha = U.class_weights(train_s, c2i, dev)
    scaler = torch.cuda.amp.GradScaler(enabled=(dev.type == "cuda"))

    def mixup(slow, fast, labels):
        if random.random() > U.MIXUP_PROB or U.MIXUP_ALPHA <= 0:
            return slow, fast, labels, False
        lam = random.betavariate(U.MIXUP_ALPHA, U.MIXUP_ALPHA)
        idx = torch.randperm(slow.size(0), device=slow.device)
        sm = lam * slow + (1 - lam) * slow[idx]
        fm = lam * fast + (1 - lam) * fast[idx]
        lm = (lam * F.one_hot(labels, len(kelas)).float()
              + (1 - lam) * F.one_hot(labels[idx], len(kelas)).float())
        return sm, fm, lm, True

    def train_epoch(optimizer):
        model.train()
        tot_loss = correct = total = 0
        optimizer.zero_grad()
        for step, (slow, fast, labels) in enumerate(train_loader):
            slow = slow.to(dev, non_blocking=True)
            fast = fast.to(dev, non_blocking=True)
            labels = labels.to(dev, non_blocking=True)
            slow, fast, lout, mixed = mixup(slow, fast, labels)
            with torch.cuda.amp.autocast(enabled=(dev.type == "cuda")):
                logits = model([slow, fast])
                if mixed:
                    loss = -(lout * F.log_softmax(logits, 1)).sum(1).mean()
                else:
                    loss = U.focal_loss(logits, lout, alpha, U.FOCAL_GAMMA,
                                        U.LABEL_SMOOTH)
                loss = loss / GRAD_ACCUM
            if not torch.isfinite(loss):
                optimizer.zero_grad(); continue
            scaler.scale(loss).backward()
            if (step + 1) % GRAD_ACCUM == 0:
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(optimizer); scaler.update()
                optimizer.zero_grad()
            tot_loss += loss.item() * GRAD_ACCUM
            hard = lout.argmax(1) if mixed else lout
            correct += (logits.argmax(1) == hard).sum().item()
            total += labels.size(0)
        return tot_loss / max(len(train_loader), 1), correct / max(total, 1)

    @torch.no_grad()
    def validate():
        model.eval()
        tot_loss = correct = total = 0
        for slow, fast, labels in val_loader:
            slow = slow.to(dev, non_blocking=True)
            fast = fast.to(dev, non_blocking=True)
            labels = labels.to(dev, non_blocking=True)
            with torch.cuda.amp.autocast(enabled=(dev.type == "cuda")):
                logits = model([slow, fast])
                loss = U.focal_loss(logits, labels, alpha, U.FOCAL_GAMMA,
                                    U.LABEL_SMOOTH)
            if torch.isfinite(loss):
                tot_loss += loss.item()
            correct += (logits.argmax(1) == labels).sum().item()
            total += labels.size(0)
        return tot_loss / max(len(val_loader), 1), correct / max(total, 1)

    # ---------- Phase 1: head only ----------
    hp = head_prefix()
    for n, p in model.named_parameters():
        p.requires_grad = n.startswith(hp)
    if sum(p.numel() for p in model.parameters() if p.requires_grad) == 0:
        raise ValueError(f"tak ada parameter terlatih di '{hp}'. Struktur "
                         "SlowFast tak dikenali")
    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad],
        lr=U.LR_HEAD_P1, weight_decay=U.WEIGHT_DECAY)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=U.PHASE1_EPOCHS, eta_min=1e-6)

    best = 0.0
    hist = deque(maxlen=U.MA_WINDOW)
    patience = 0
    phase = 1
    base_lrs_p2 = None

    def simpan(path, epoch, ma):
        torch.save({"epoch": epoch, "model": model.state_dict(), "classes": kelas,
                    "class_to_idx": c2i, "val_acc_ma": ma, "arch": "slowfast_r50"},
                   path)

    for epoch in range(1, epochs + 1):
        if epoch == U.PHASE1_EPOCHS + 1 and phase == 1:
            phase = 2
            for p in model.parameters():
                p.requires_grad = True
            for m in model.modules():
                if isinstance(m, torch.nn.Dropout):
                    m.p = U.DROPOUT_P2
            pg = param_groups_llrd(U.LR_HEAD_P2, U.LR_BB_P2)
            base_lrs_p2 = [g["lr"] for g in pg]
            optimizer = torch.optim.AdamW(pg, weight_decay=U.WEIGHT_DECAY)
            scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
                optimizer, T_max=epochs - U.PHASE1_EPOCHS, eta_min=1e-9)
            patience = 0

        p2 = epoch - U.PHASE1_EPOCHS
        if phase == 2 and p2 <= U.WARMUP_EPOCHS and base_lrs_p2:
            U.warmup_lr(optimizer, p2 - 1, U.WARMUP_EPOCHS, base_lrs_p2)

        tr_loss, tr_acc = train_epoch(optimizer)
        va_loss, va_acc = validate()
        if not (phase == 2 and p2 <= U.WARMUP_EPOCHS):
            scheduler.step()

        hist.append(va_acc)
        ma = sum(hist) / len(hist)
        progres(epoch, train_loss=tr_loss, train_acc=tr_acc, val_loss=va_loss,
                val_acc=va_acc, val_acc_ma=ma)
        simpan(last_pt, epoch, ma)
        if ma > best:
            best = ma; patience = 0
            simpan(best_pt, epoch, ma)
        else:
            patience += 1
            if patience >= U.PATIENCE and phase == 2:
                break
