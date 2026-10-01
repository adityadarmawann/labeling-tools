"""
PoseC3D MANDIRI — tanpa MMAction2/mmcv/mmpose. Port setia dari
posec3d/action-v14/posec3d.py.

Dua bagian:
  1. buat_heatmap()  : koordinat sendi (M,T,V,2) -> volume peta panas (C,T,H,W)
  2. buat_model()    : CNN 3D bergaya SlowOnly di atas volume itu (~8 jt param)

Kenapa peta panas, bukan koordinat mentah (ST-GCN): pose estimator pada klip aksi
hanya mencapai keyakinan rata-rata ~0,62 dan sering melewatkan sendi. Peta panas
membawa ketidakpastian itu secara alami — sendi ragu jadi titik lemah, bukan
koordinat pasti yang mungkin salah.

Kenapa standalone: ini hal paling berharga dari acuan action-v14 — PoseC3D tanpa
neraka versi mmcv/mmaction2/mmpose. numpy di tingkat modul (bukan pustaka berat);
torch hanya DI DALAM buat_model, supaya berkas ini bisa diimpor di server CPU.
"""
from __future__ import annotations

import numpy as np

# Konfigurasi skeleton + heatmap + model, diport dari konfigurasi.py action-v14.
# Dibawa sebagai dict supaya trainer bisa menimpa (mis. jml_kelas dinamis per
# projek, pakai_bola mati bila detektor objek tak ada).
KONFIG: dict = {
    # skeleton
    "jml_sendi": 17,                # COCO
    "pakai_node_bola": True,
    "jml_orang": 2,                 # pelaku + lawan terdekat
    "panjang_klip": 24,             # frame diambil per klip
    "conf_pose": 0.25,
    "conf_keypoint": 0.30,
    "conf_bola": 0.20,
    "kelas_bola": 0,                # indeks kelas bola di detektor objek
    "maks_tambal": 5,
    # pemilih pelaku (jumlah 1,0)
    "bobot_tengah": 0.35, "bobot_luas": 0.25,
    "bobot_kehadiran": 0.20, "bobot_bola": 0.20,
    # pelacak
    "iou_lacak": 0.3, "maks_hilang": 5,
    # heatmap
    "tinggi_peta": 56, "lebar_peta": 56, "sigma": 0.6, "perbesar_kotak": 1.25,
    # model
    "lebar_dasar": 32, "blok": (2, 3, 3), "dropout": 0.5,
    # pelatihan (resep)
    "weight_decay": 3e-4, "warmup_epoch": 3, "label_smoothing": 0.1,
    "sabar": 12, "pekerja": 4, "seed": 7,
    "aug_flip": 0.5, "aug_geser": 0.05, "aug_skala": 0.15, "aug_putar": 8.0,
}


def jml_node(cfg: dict) -> int:
    return cfg["jml_sendi"] + (1 if cfg.get("pakai_node_bola") else 0)


def idx_node_bola(cfg: dict) -> int:
    return cfg["jml_sendi"]          # node ke-18 (indeks 17)


# ══════════════════════════ peta panas (numpy murni) ══════════════════════════
def kotak_gabungan(koor, conf, pad):
    m = conf > 0
    if not m.any():
        return None
    xs = koor[..., 0][m]; ys = koor[..., 1][m]
    x1, x2 = float(xs.min()), float(xs.max())
    y1, y2 = float(ys.min()), float(ys.max())
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    w = max(x2 - x1, 1.0) * pad
    h = max(y2 - y1, 1.0) * pad
    sisi = max(w, h)
    return cx - sisi / 2, cy - sisi / 2, sisi


def buat_heatmap(koor, conf, cfg=KONFIG, pisah_orang=True):
    """koor (M,T,V,2) piksel, conf (M,T,V) -> volume (C,T,H,W) float32.
    C = M*V bila pisah_orang. Port setia dari posec3d.buat_heatmap."""
    tinggi, lebar = cfg["tinggi_peta"], cfg["lebar_peta"]
    sigma = cfg["sigma"]
    M, T, V, _ = koor.shape
    kg = kotak_gabungan(koor, conf, cfg["perbesar_kotak"])
    C = M * V if pisah_orang else V
    vol = np.zeros((C, T, tinggi, lebar), dtype=np.float32)
    if kg is None:
        return vol
    x0, y0, sisi = kg
    skala_x = lebar / sisi; skala_y = tinggi / sisi
    gy = np.arange(tinggi, dtype=np.float32).reshape(-1, 1)
    gx = np.arange(lebar, dtype=np.float32).reshape(1, -1)
    dua_sigma2 = 2.0 * sigma * sigma
    for m in range(M):
        for t in range(T):
            c = conf[m, t]
            if not (c > 0).any():
                continue
            px = (koor[m, t, :, 0] - x0) * skala_x
            py = (koor[m, t, :, 1] - y0) * skala_y
            for v in range(V):
                if c[v] <= 0:
                    continue
                x, y = px[v], py[v]
                if x < -3 or y < -3 or x > lebar + 3 or y > tinggi + 3:
                    continue
                xa, xb = max(0, int(x - 3 * sigma - 1)), min(lebar, int(x + 3 * sigma + 2))
                ya, yb = max(0, int(y - 3 * sigma - 1)), min(tinggi, int(y + 3 * sigma + 2))
                if xa >= xb or ya >= yb:
                    continue
                d = ((gx[:, xa:xb] - x) ** 2 + (gy[ya:yb] - y) ** 2)
                g = np.exp(-d / dua_sigma2, dtype=np.float32) * float(c[v])
                ch = m * V + v if pisah_orang else v
                np.maximum(vol[ch, t, ya:yb, xa:xb], g,
                           out=vol[ch, t, ya:yb, xa:xb])
    return vol


# ══════════════════════════ model (torch lazy) ══════════════════════════
def buat_model(jml_kelas: int, cfg: dict = KONFIG, masuk_ch: int | None = None):
    """Bangun PoseC3D. torch diimpor DI SINI — modul tetap bisa diimpor di CPU.
    Kelas Blok3D/PoseC3D didefinisikan di dalam supaya nn.Module tak pernah
    tersentuh di tingkat modul."""
    import torch.nn as nn
    import torch.nn.functional as F

    V = jml_node(cfg)
    if masuk_ch is None:
        masuk_ch = cfg["jml_orang"] * V

    class Blok3D(nn.Module):
        """Blok residual 3D. Konvolusi temporal hanya di tahap akhir (SlowOnly):
        tahap awal fokus bentuk, tahap akhir fokus gerak."""

        def __init__(self, masuk, keluar, stride_spasial=1, temporal=False):
            super().__init__()
            kt = 3 if temporal else 1
            pt = 1 if temporal else 0
            self.c1 = nn.Conv3d(masuk, keluar, (kt, 3, 3),
                                stride=(1, stride_spasial, stride_spasial),
                                padding=(pt, 1, 1), bias=False)
            self.b1 = nn.BatchNorm3d(keluar)
            self.c2 = nn.Conv3d(keluar, keluar, (1, 3, 3), padding=(0, 1, 1),
                                bias=False)
            self.b2 = nn.BatchNorm3d(keluar)
            self.pintas = None
            if masuk != keluar or stride_spasial != 1:
                self.pintas = nn.Sequential(
                    nn.Conv3d(masuk, keluar, 1,
                              stride=(1, stride_spasial, stride_spasial),
                              bias=False),
                    nn.BatchNorm3d(keluar))

        def forward(self, x):
            sisa = x if self.pintas is None else self.pintas(x)
            x = F.relu(self.b1(self.c1(x)), inplace=True)
            x = self.b2(self.c2(x))
            return F.relu(x + sisa, inplace=True)

    class PoseC3D(nn.Module):
        def __init__(self):
            super().__init__()
            lebar = cfg["lebar_dasar"]; blok = cfg["blok"]
            self.masuk_ch = masuk_ch
            self.stem = nn.Sequential(
                nn.Conv3d(masuk_ch, lebar, (1, 7, 7), stride=(1, 2, 2),
                          padding=(0, 3, 3), bias=False),
                nn.BatchNorm3d(lebar), nn.ReLU(inplace=True))
            tahap, ch = [], lebar
            for i, n in enumerate(blok):
                keluar = lebar * (2 ** (i + 1))
                for j in range(n):
                    tahap.append(Blok3D(ch, keluar,
                                        stride_spasial=2 if (j == 0 and i > 0) else 1,
                                        temporal=(i >= 1)))
                    ch = keluar
            self.tahap = nn.Sequential(*tahap)
            self.pool = nn.AdaptiveAvgPool3d(1)
            self.drop = nn.Dropout(cfg["dropout"])
            self.fc = nn.Linear(ch, jml_kelas)
            for m in self.modules():
                if isinstance(m, nn.Conv3d):
                    nn.init.kaiming_normal_(m.weight, mode="fan_out",
                                            nonlinearity="relu")
                elif isinstance(m, nn.BatchNorm3d):
                    nn.init.constant_(m.weight, 1); nn.init.constant_(m.bias, 0)

        def forward(self, x):            # x: (B,C,T,H,W)
            x = self.stem(x)
            x = self.tahap(x)
            x = self.pool(x).flatten(1)
            return self.fc(self.drop(x))

        def jml_parameter(self):
            return sum(p.numel() for p in self.parameters())

    return PoseC3D()
