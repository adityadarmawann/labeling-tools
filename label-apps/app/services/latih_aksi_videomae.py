"""
Trainer backend VideoMAE untuk classifier aksi — port setia dari
action-labeler/modelling/train_action_VideoMae-V12.py, disusun ulang menjadi
sebuah fungsi `latih(...)` yang dipanggil latih_aksi_jalan sebagai subproses.

Yang DIPERTAHANKAN dari acuan (resep yang dibayar dengan percobaan v1..v12):
  * ckpt MCG-NJU/videomae-base-finetuned-kinetics, 16 frame, jendela 1,0 dtk.
  * Dua fase: head-only (15 epoch) -> full + LLRD 0,75 + warmup 8 epoch.
  * FocalLoss(alpha=bobot kelas, gamma 2.0, label_smoothing 0.15) + mixup +
    WeightedRandomSampler (cap 1,5x) + AMP + grad-accum.
  * Dropout Phase 2 = 0.1 (0.7 pernah meruntuhkan training — lihat acuan).

Yang DISESUAIKAN untuk HIGOLAB (bukan perubahan resep, hanya I/O):
  * Jumlah & nama kelas DINAMIS dari aksi.yaml versinya (acuan mengunci 6 kelas
    basket) — classifier aksi tiap projek berbeda kelas.
  * Membaca .versi/vN/{train,valid}/<kelas>/ (acuan: train/ + val/).
  * Checkpoint -> run_dir/weights/{best,last}.pt; kemajuan -> results.csv lewat
    callback `progres` (acuan menulis _log.csv sendiri).

SEMUA impor berat ada DI DALAM latih(): modul ini wajib bisa diimpor di server
CPU tanpa torch/transformers/pytorchvideo/av. TIDAK dijalankan di CI.
"""
from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

from . import latih_aksi_umum as U

# Konstanta resep (disamakan antar backend lewat U).
MODEL_CKPT = "MCG-NJU/videomae-base-finetuned-kinetics"
NUM_FRAMES = U.NUM_FRAMES_VIDEOMAE
RESIZE = U.RESIZE
CLIP_DURATION = U.CLIP_DURATION
GRAD_ACCUM = 3                     # batch efektif 6x3 = 18 (acuan)


def latih(*, versi_dir: Path, run_dir: Path, par: dict, progres, device: str,
          ds=None) -> None:
    """Latih VideoMAE dari versi klip aksi. Menulis best/last.pt + results.csv.

    progres(epoch, train_loss=, train_acc=, val_loss=, val_acc=, val_acc_ma=)
    dipanggil tiap epoch — itulah yang dibaca latih_aksi.status dari disk.
    """
    import random

    import numpy as np
    import torch
    import torch.nn.functional as F
    from torch.utils.data import DataLoader, Dataset

    from pytorchvideo.data.encoded_video import EncodedVideo
    from transformers import (VideoMAEForVideoClassification,
                              VideoMAEImageProcessor)

    kelas = U.kelas_dari_yaml(versi_dir)
    if len(kelas) < 2:
        raise ValueError("versi ini punya < 2 kelas aksi — tak bisa dilatih")
    epochs = int(par.get("epochs") or 80)
    batch_size = int(par.get("batch") or 6)
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
        raise ValueError("train/ atau valid/ kosong — versi belum siap dilatih")

    # ---------- pemuat frame (port load_frames) ----------
    def load_frames(path, is_train):
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
            T = frames.shape[1]
            idx = torch.linspace(0, T - 1, NUM_FRAMES).long()
            frames = frames[:, idx]
            if frames.shape[2] != RESIZE or frames.shape[3] != RESIZE:
                frames = F.interpolate(
                    frames.permute(1, 0, 2, 3).float(), size=(RESIZE, RESIZE),
                    mode="bilinear", align_corners=False).permute(1, 0, 2, 3)
            frames = frames.permute(1, 2, 3, 0)
            return [frames[i].numpy().astype(np.uint8) if frames[i].max() > 1
                    else (frames[i].numpy() * 255).astype(np.uint8)
                    for i in range(NUM_FRAMES)]
        except Exception:                        # noqa: BLE001
            return None

    processor = VideoMAEImageProcessor.from_pretrained(MODEL_CKPT)

    class DS(Dataset):
        def __init__(self, samples, is_train):
            self.samples = samples; self.is_train = is_train

        def __len__(self):
            return len(self.samples)

        def __getitem__(self, i):
            path, cls = self.samples[i]
            frames = load_frames(path, self.is_train)
            if frames is None:
                frames = [np.zeros((RESIZE, RESIZE, 3), np.uint8)] * NUM_FRAMES
            if self.is_train:
                if random.random() < 0.3:
                    frames = [np.fliplr(f).copy() for f in frames]
                if random.random() < 0.3:
                    g = random.uniform(0.75, 1.25)
                    frames = [np.clip(fr * g, 0, 255).astype(np.uint8)
                              for fr in frames]
            pv = processor(frames, return_tensors="pt")["pixel_values"].squeeze(0)
            return pv, c2i[cls]

    train_loader = DataLoader(
        DS(train_s, True), batch_size=batch_size,
        sampler=U.make_sampler(train_s, c2i), num_workers=4,
        pin_memory=(dev.type == "cuda"), drop_last=True)
    val_loader = DataLoader(
        DS(val_s, False), batch_size=batch_size, shuffle=False,
        num_workers=4, pin_memory=(dev.type == "cuda"))

    # ---------- model ----------
    model = VideoMAEForVideoClassification.from_pretrained(
        MODEL_CKPT, num_labels=len(kelas), ignore_mismatched_sizes=True,
        label2id=c2i, id2label={i: c for c, i in c2i.items()})
    model.videomae.encoder.gradient_checkpointing = True
    model = model.to(dev)

    alpha = U.class_weights(train_s, c2i, dev)
    scaler = torch.cuda.amp.GradScaler(enabled=(dev.type == "cuda"))

    def mixup(pv, labels):
        if random.random() > U.MIXUP_PROB or U.MIXUP_ALPHA <= 0:
            return pv, labels, False
        lam = random.betavariate(U.MIXUP_ALPHA, U.MIXUP_ALPHA)
        idx = torch.randperm(pv.size(0), device=pv.device)
        mixed = lam * pv + (1 - lam) * pv[idx]
        lm = (lam * F.one_hot(labels, len(kelas)).float()
              + (1 - lam) * F.one_hot(labels[idx], len(kelas)).float())
        return mixed, lm, True

    # ---------- LLRD (port get_param_groups_llrd, ViT 12 blok) ----------
    def param_groups_llrd(head_lr, bb_lr, factor=U.LLRD_FACTOR):
        groups, nblok = [], 12
        head = [p for n, p in model.named_parameters()
                if "classifier" in n and p.requires_grad]
        if head:
            groups.append({"params": head, "lr": head_lr})
        for bi in range(nblok - 1, -1, -1):
            scale = factor ** (nblok - 1 - bi)
            ps = [p for n, p in model.named_parameters()
                  if f"encoder.layer.{bi}." in n and p.requires_grad]
            if ps:
                groups.append({"params": ps, "lr": bb_lr * scale})
        other = [p for n, p in model.named_parameters()
                 if "classifier" not in n
                 and not any(f"encoder.layer.{i}." in n for i in range(nblok))
                 and p.requires_grad]
        if other:
            groups.append({"params": other, "lr": bb_lr * (factor ** nblok)})
        return groups

    def train_epoch(optimizer):
        model.train()
        tot_loss = correct = total = 0
        optimizer.zero_grad()
        for step, (pv, labels) in enumerate(train_loader):
            pv = pv.to(dev, non_blocking=True)
            labels = labels.to(dev, non_blocking=True)
            pv, lout, mixed = mixup(pv, labels)
            with torch.cuda.amp.autocast(enabled=(dev.type == "cuda")):
                logits = model(pixel_values=pv).logits
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
            total += pv.size(0)
        return tot_loss / max(len(train_loader), 1), correct / max(total, 1)

    @torch.no_grad()
    def validate():
        model.eval()
        tot_loss = correct = total = 0
        for pv, labels in val_loader:
            pv = pv.to(dev, non_blocking=True)
            labels = labels.to(dev, non_blocking=True)
            with torch.cuda.amp.autocast(enabled=(dev.type == "cuda")):
                logits = model(pixel_values=pv).logits
                loss = U.focal_loss(logits, labels, alpha, U.FOCAL_GAMMA,
                                    U.LABEL_SMOOTH)
            if torch.isfinite(loss):
                tot_loss += loss.item()
            correct += (logits.argmax(1) == labels).sum().item()
            total += labels.size(0)
        return tot_loss / max(len(val_loader), 1), correct / max(total, 1)

    # ---------- Phase 1: head only ----------
    for n, p in model.named_parameters():
        p.requires_grad = "classifier" in n
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
                    "class_to_idx": c2i, "val_acc_ma": ma, "arch": "videomae",
                    "model_ckpt": MODEL_CKPT}, path)

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
