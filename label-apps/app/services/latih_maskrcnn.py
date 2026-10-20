"""
Proses yang melatih Mask R-CNN (arsitektur training KETIGA, di samping YOLO dan
RF-DETR). Dijalankan latih.jalankan() sebagai SUBPROSES TERLEPAS, pola yang sama
persis dengan latih_jalan.py / latih_rfdetr.py:

    python -m app.services.latih_maskrcnn <folder-projek> <nomor>

KENAPA Mask R-CNN, dan KENAPA torchvision (bukan Detectron2):
projek seperti frisian-flag melabeli SATU foto dengan dua jenis bentuk — produk
sebagai POLIGON (mask/segmentasi) dan logo sebagai KOTAK (bbox). Mask R-CNN satu
model menghasilkan KEDUANYA: kotak untuk semua kelas + mask untuk kelas yang
punya poligon. Detectron2 (Facebook) tak mendukung torch modern (2.14/cu13/
py3.13) -> dipakai Mask R-CNN bawaan torchvision (maskrcnn_resnet50_fpn[_v2]),
arsitektur yang sama, tanpa dependensi install-sensitif.

PARTIAL MASK SUPERVISION: anotasi produk membawa segmentation poligon ->
mask asli; anotasi logo membawa `segmentation: []` (lihat ekspor_coco) -> tak
ada mask, jadi dipakai KOTAK-nya sebagai mask (logo memang kira-kira kotak);
saat inferensi, logo dibaca dari kotaknya. Jadi kotak logo presisi, mask produk
presisi, dan keduanya dari satu model.

Alur: ekspor versi -> COCO per-split (ekspor_coco, SAMA dengan RF-DETR) ->
loop training torchvision -> best.pt/last.pt/last.ckpt + results.csv ke
.latih/L<n>/ (konvensi YOLO), sehingga status()/unduh bobot/kartu berlaku tanpa
pembedaan.

torch/torchvision diimpor LAZY (di dalam _latih_maskrcnn_asli) -> modul ini AMAN
diimpor di .venv CPU (tanpa torch) untuk suite. SATU GPU SATU TRAINING: flock
yang SAMA dengan latih_jalan (/tmp/labelapp-latih-gpu.lock).
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

from . import latih

KUNCI_GPU = Path(tempfile.gettempdir()) / "labelapp-latih-gpu.lock"
# Ukuran -> fungsi pembangun torchvision; diimpor lazy agar modul aman tanpa torch.
MODEL_TV = {"r50v2": "maskrcnn_resnet50_fpn_v2", "r50": "maskrcnn_resnet50_fpn"}


def _sekarang() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _tulis_progres(dl: Path, baris: list[tuple]) -> None:
    """results.csv (konvensi HIGOLAB) dari baris (epoch, detik, mAP|None) supaya
    status() menampilkan epoch & waktu LIVE selagi training berjam-jam berjalan."""
    try:
        teks = ["epoch,time,metrics/mAP50-95(B)"]
        for ep, det, mp in baris:
            teks.append(f"{ep},{round(det, 1)},{'' if mp is None else round(mp, 4)}")
        (Path(dl) / "results.csv").write_text("\n".join(teks) + "\n")
    except OSError:
        pass


def baca_coco(coco_dir: Path, split: str) -> tuple[list[dict], list[str]]:
    """Baca satu split COCO hasil ekspor_coco jadi daftar contoh MURNI (tanpa
    torch) -> bisa diuji di .venv. Tiap contoh:
        {"gambar": path, "boxes": [[x1,y1,x2,y2]...], "labels": [int...],
         "segs": [seg_atau_None...]}  (seg = [[x,y,...]] poligon absolut; None =
         kotak-saja/logo). Kembalikan (contoh, nama_kelas_urut_indeks).

    Kotak diambil dari field bbox COCO (xywh absolut). Anotasi dengan w/h <= 0
    dibuang. Gambar tanpa anotasi TETAP dimasukkan (contoh negatif)."""
    d = Path(coco_dir) / split
    jf = d / "_annotations.coco.json"
    if not jf.exists():
        return [], []
    data = json.loads(jf.read_text(encoding="utf-8"))
    kat = {c["id"]: c["name"] for c in data.get("categories", [])}
    nama = [kat[k] for k in sorted(kat)]
    per_gambar: dict[int, dict] = {}
    for im in data.get("images", []):
        per_gambar[im["id"]] = {"gambar": str(d / im["file_name"]),
                                "boxes": [], "labels": [], "segs": []}
    for a in data.get("annotations", []):
        g = per_gambar.get(a["image_id"])
        if g is None:
            continue
        x, y, w, h = a.get("bbox") or [0, 0, 0, 0]
        if w <= 0 or h <= 0:
            continue
        g["boxes"].append([float(x), float(y), float(x + w), float(y + h)])
        g["labels"].append(int(a["category_id"]))
        seg = a.get("segmentation")
        g["segs"].append(seg if seg else None)      # [] / None -> kotak-saja
    return list(per_gambar.values()), nama


def _serap(dl: Path, hasil: dict) -> int:
    """Loop training menulis langsung ke .latih/L<n>/ (best.pt/last.pt/
    maskrcnn/last.ckpt/results.csv), jadi di sini cuma memastikan results.csv ada
    dan mengembalikan epoch terbaca."""
    dl = Path(dl)
    rp = dl / "results.csv"
    if not (rp.exists() and rp.stat().st_size > 0):
        ep = int(hasil.get("epoch") or 0)
        mp = hasil.get("map")
        if mp is not None:
            rp.write_text(f"epoch,metrics/mAP50-95(B)\n{ep},{mp}\n")
        elif ep:
            rp.write_text(f"epoch\n{ep}\n")
    return latih.baca_hasil_csv(dl).get("epoch") or int(hasil.get("epoch") or 0)


def _latih_maskrcnn_asli(coco_dir: Path, dl: Path, model: str, par: dict,
                         lapor, resume: str | None = None) -> dict:
    """Training Mask R-CNN SUNGGUHAN. Satu-satunya tempat torch diimpor -> seam
    yang dipalsukan di tes. Menulis LANGSUNG ke .latih/L<n>/ tiap epoch:
    weights/best.pt (val mAP terbaik), weights/last.pt, maskrcnn/last.ckpt (state
    penuh untuk Lanjutkan), results.csv. Kembalikan {"epoch": n, "map": best}.

    resume = path last.ckpt: muat model+optimizer+epoch, lanjut sampai `epochs`."""
    import numpy as np
    import cv2
    import torch
    import torchvision
    from torch.utils.data import DataLoader, Dataset
    from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
    from torchvision.models.detection.mask_rcnn import MaskRCNNPredictor

    dl = Path(dl)
    (dl / "weights").mkdir(parents=True, exist_ok=True)
    (dl / "maskrcnn").mkdir(parents=True, exist_ok=True)

    train_contoh, nama = baca_coco(coco_dir, "train")
    val_contoh, _ = baca_coco(coco_dir, "valid")
    if not nama:
        raise RuntimeError("COCO kosong: tak ada kelas untuk dilatih")
    n_kelas = len(nama) + 1                          # +1 background (indeks 0)

    def raster(seg, box, H, W):
        m = np.zeros((H, W), np.uint8)
        if seg:                                      # poligon -> mask asli
            for poli in seg:
                pts = np.array(poli, np.float32).reshape(-1, 2).astype(np.int32)
                if len(pts) >= 3:
                    cv2.fillPoly(m, [pts], 1)
        else:                                        # kotak-saja (logo)
            x1, y1, x2, y2 = (int(round(v)) for v in box)
            m[max(0, y1):max(0, y2), max(0, x1):max(0, x2)] = 1
        return m

    class DS(Dataset):
        def __init__(self, contoh, latih_mode):
            # Mode latih: buang gambar tanpa objek (box/mask butuh positif).
            self.c = [x for x in contoh if x["boxes"]] if latih_mode else contoh

        def __len__(self):
            return len(self.c)

        def __getitem__(self, i):
            e = self.c[i]
            bgr = cv2.imread(e["gambar"], cv2.IMREAD_COLOR)
            if bgr is None:
                bgr = np.zeros((32, 32, 3), np.uint8)
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            H, W = rgb.shape[:2]
            img = torch.from_numpy(rgb).permute(2, 0, 1).float() / 255.0
            boxes = e["boxes"]
            if boxes:
                mk = np.stack([raster(s, b, H, W)
                               for s, b in zip(e["segs"], boxes)])
                tgt = {"boxes": torch.as_tensor(boxes, dtype=torch.float32),
                       "labels": torch.as_tensor(e["labels"], dtype=torch.int64),
                       "masks": torch.as_tensor(mk, dtype=torch.uint8)}
            else:
                tgt = {"boxes": torch.zeros((0, 4), dtype=torch.float32),
                       "labels": torch.zeros((0,), dtype=torch.int64),
                       "masks": torch.zeros((0, H, W), dtype=torch.uint8)}
            return img, tgt

    res = int(par.get("resolution") or 800)
    buat = getattr(torchvision.models.detection, MODEL_TV.get(model, MODEL_TV["r50v2"]))
    # Transfer learning: bobot COCO penuh kalau ada internet; kalau gagal unduh,
    # jatuh ke backbone ImageNet; terakhir acak. min_size/max_size dari resolusi.
    maks = int(round(res * 5 / 3))
    try:
        net = buat(weights="DEFAULT", min_size=res, max_size=maks)
    except Exception:                                # noqa: BLE001
        try:
            net = buat(weights=None, weights_backbone="DEFAULT",
                       min_size=res, max_size=maks)
        except Exception:                            # noqa: BLE001
            net = buat(weights=None, weights_backbone=None,
                       min_size=res, max_size=maks)
    inf = net.roi_heads.box_predictor.cls_score.in_features
    net.roi_heads.box_predictor = FastRCNNPredictor(inf, n_kelas)
    infm = net.roi_heads.mask_predictor.conv5_mask.in_channels
    net.roi_heads.mask_predictor = MaskRCNNPredictor(infm, 256, n_kelas)

    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    net.to(dev)
    p = [x for x in net.parameters() if x.requires_grad]
    opt = torch.optim.SGD(p, lr=float(par.get("lr") or 0.005),
                          momentum=0.9, weight_decay=5e-4)
    total_ep = int(par.get("epochs") or 50)
    mulai_ep = 0
    best_map = -1.0

    if resume and Path(resume).exists():
        try:
            ck = torch.load(resume, map_location=dev)
            net.load_state_dict(ck["model"])
            if ck.get("optimizer"):
                opt.load_state_dict(ck["optimizer"])
            mulai_ep = int(ck.get("epoch") or 0)
            best_map = float(ck.get("best_map", -1.0))
            lapor(pesan=f"melanjutkan dari epoch {mulai_ep}…")
        except Exception as e:                       # noqa: BLE001
            print("resume gagal, mulai segar:", str(e)[:120], flush=True)

    bs = max(1, int(par.get("batch_size") or 2))
    kolasi = lambda b: tuple(zip(*b))
    dltr = DataLoader(DS(train_contoh, True), batch_size=bs, shuffle=True,
                      num_workers=2, collate_fn=kolasi)
    dlva = DataLoader(DS(val_contoh, False), batch_size=1, shuffle=False,
                      num_workers=2, collate_fn=kolasi)
    if len(dltr) == 0:
        raise RuntimeError("tak ada gambar train berobjek untuk Mask R-CNN")

    from torchmetrics.detection import MeanAveragePrecision
    warm = max(1, int(float(par.get("warmup_epochs") or 1.0) * len(dltr)))
    base_lr = float(par.get("lr") or 0.005)
    t0 = time.time()
    rows: list[tuple] = []
    it_glob = mulai_ep * len(dltr)

    lapor(pesan=f"melatih Mask R-CNN {model} @ {res}px, {len(dltr.dataset)} gbr…")
    for ep in range(mulai_ep, total_ep):
        net.train()
        for img, tgt in dltr:
            it_glob += 1
            if it_glob <= warm:                      # warmup linear LR
                for g in opt.param_groups:
                    g["lr"] = base_lr * it_glob / warm
            img = [x.to(dev) for x in img]
            tgt = [{k: v.to(dev) for k, v in t.items()} for t in tgt]
            loss = net(img, tgt)
            rugi = sum(loss.values())
            opt.zero_grad()
            rugi.backward()
            opt.step()
        # ---- validasi mAP (kotak) ----
        mp = None
        try:
            if len(dlva) > 0:
                net.eval()
                metrik = MeanAveragePrecision(box_format="xyxy", iou_type="bbox")
                with torch.no_grad():
                    for img, tgt in dlva:
                        pred = net([x.to(dev) for x in img])
                        metrik.update(
                            [{k: v.cpu() for k, v in p.items()} for p in pred],
                            [{k: v.cpu() for k, v in t.items()
                              if k in ("boxes", "labels")} for t in tgt])
                mp = float(metrik.compute()["map"])
                if mp < 0:
                    mp = 0.0
        except Exception as e:                       # noqa: BLE001
            print("validasi mAP gagal:", str(e)[:120], flush=True)
        # ---- simpan checkpoint (mid-stop aman) ----
        rows.append((ep + 1, time.time() - t0, mp))
        _tulis_progres(dl, rows)
        ckpt = {"model": net.state_dict(), "optimizer": opt.state_dict(),
                "epoch": ep + 1, "n_kelas": n_kelas, "kelas": nama,
                "best_map": best_map, "arsitektur": "maskrcnn", "ukuran": model}
        torch.save(net.state_dict(), dl / "weights" / "last.pt")
        torch.save(ckpt, dl / "maskrcnn" / "last.ckpt")
        if mp is not None and mp >= best_map:
            best_map = mp
            torch.save(net.state_dict(), dl / "weights" / "best.pt")
        elif not (dl / "weights" / "best.pt").exists():
            torch.save(net.state_dict(), dl / "weights" / "best.pt")
        lapor(pesan=f"epoch {ep + 1}/{total_ep}"
                    + (f", mAP {mp:.3f}" if mp is not None else ""))
    return {"epoch": total_ep, "map": (None if best_map < 0 else best_map)}


def main() -> int:
    if len(sys.argv) < 3:
        print("pemakaian: latih_maskrcnn <folder-projek> <nomor>", file=sys.stderr)
        return 2
    ds, nomor = Path(sys.argv[1]), int(sys.argv[2])
    isi = latih.baca(ds, nomor)
    if isi is None:
        print(f"training L{nomor} tidak ada di {ds}", file=sys.stderr)
        return 2

    kag: dict = {}

    def lapor(**kv):                                 # dicatat di field "maskrcnn"
        kag.update(kv)
        latih.perbarui(ds, nomor, maskrcnn=dict(kag))

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
                       pid=os.getpid(), galat="")
        from . import buatversi, ekspor_coco
        versi = int(isi["versi"])
        versi_dir = buatversi.dir_versi(ds, versi)
        if not (versi_dir / "data.yaml").exists():
            raise FileNotFoundError(
                f"versi v{versi} belum punya data.yaml — tak bisa diekspor ke COCO")
        par = dict(isi.get("par") or {})
        model = isi.get("maskrcnn_model") or "r50v2"
        dl = latih.dir_latih(ds, nomor)
        dl.mkdir(parents=True, exist_ok=True)
        # Lanjutkan: last.ckpt yang disemai dari training sumber -> resume sejati.
        seed = dl / "maskrcnn" / "last.ckpt"
        resume_ckpt = str(seed) if seed.exists() else None

        with tempfile.TemporaryDirectory() as tmp:
            coco_dir = Path(tmp) / "coco"
            lapor(pesan="ekspor versi ke COCO…")
            ring = ekspor_coco.versi_ke_coco(versi_dir, coco_dir)
            print(f"  COCO: {ring}", flush=True)
            hasil = _latih_maskrcnn_asli(coco_dir, dl, model, par, lapor,
                                         resume=resume_ckpt)
            epoch = _serap(dl, hasil)

        latih.perbarui(ds, nomor, keadaan="selesai", selesai_pada=_sekarang(),
                       detik_total=round(time.time() - t0, 1))
        lapor(pesan=f"selesai. Mask R-CNN {model}, {epoch} epoch")
        print(f"  bobot terbaik: {dl / 'weights' / 'best.pt'}")
        return 0
    except Exception as e:                           # noqa: BLE001
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
