# Konsep Paper: Konsistensi Augmentasi–Training Dua-Mode untuk Deteksi Objek yang Robust terhadap Warna

> Dokumen konsep (bukan draft final). Disusun sebagai kerangka untuk artikel jurnal
> Q1/Q2 informatika / kecerdasan buatan. Berisi: masalah, novelti, formulasi, alur,
> desain eksperimen, posisi vs prior art, dan rencana kerja.
>
> Konteks sistem: HIGOLAB — alat pelabelan + pembuatan versi dataset + training untuk
> mesin sortir sampah / *reverse vending machine* (RVM). Studi kasus: produk botol,
> kaleng, tetra, mlp, plastic-cup (bentuk sebagai pembeda) vs produk Kahf/paragon
> (warna sebagai pembeda).

---

## 1. Opsi Judul

1. *Consistent Two-Stage Photometric Augmentation: Coupling Dataset Synthesis and Training for Color-Robust Object Detection*
2. *When Color Is (Not) the Class: A Dual-Mode Augmentation–Training Formula for Industrial Object Detection*
3. *Avoiding the Color Shortcut: Augmentation–Training Consistency for Reverse Vending Machines*
4. *Lock the Signal, Free the Rest: Discriminability-Guided Two-Stage Color Augmentation for Fine-Grained Product Detection*

Judul kerja yang dipakai di dokumen ini: **"Consistent Two-Stage Photometric Augmentation"**.

---

## 2. Ringkasan (draft abstract)

Sebagian besar riset augmentasi memperlakukan perturbasi warna hanya sebagai proses
*on-the-fly* saat training. Pada pipeline industri nyata, augmentasi sering
**dimaterialisasi lebih dulu** ke sebuah versi dataset, baru kemudian model dilatih
dengan augmentasi training tambahan di atasnya. Kami menunjukkan bahwa pada pipeline
dua-tahap ini, **konsistensi perturbasi fotometrik antara tahap sintesis-dataset dan
tahap training adalah syarat kelas-satu**: ketidaksesuaian menimbulkan *color shortcut*
— model mencapai mAP validasi tinggi namun runtuh di bawah pergeseran iluminasi produksi
(pada sistem kami terukur mAP50-95 0,95 lalu benar 0 dari 7 di ruang detektor nyata).

Kami mengangkat **dua formula terkoordinasi** yang mengunci kedua tahap sekaligus:
mode **BENTUK** (warna sebagai fitur insidental — model dipaksa belajar bentuk) dan
mode **WARNA** (warna sebagai bagian identitas kelas — rona dijaga). Kami memformalkan
kendala konsistensinya, mendokumentasikan mode kegagalannya, dan mengusulkan metode
**pemilihan mode berbasis data** menggunakan rasio diskriminan per-kanal HSV di region
objek. Metode divalidasi pada dua dataset kontras alami (bentuk-defining vs warna-defining)
di sebuah sistem terpasang.

---

## 3. Masalah & Motivasi

### 3.1 Mode kegagalan: *color shortcut*
Bila dua kelas kebetulan berbeda warna pada data latih, model cenderung memakai warna
sebagai **pintasan** ketimbang belajar bentuk/struktur. Pintasan ini **rapuh**: lampu
ruang detektor RVM berwarna (mis. ungu/magenta, puncak hue ~310°) berada **di luar
distribusi** data latih. Akibatnya: metrik validasi bagus, performa produksi runtuh.

Bukti nyata dari sistem kami (motivating result):
- Versi v13: mAP50-95 **0,9499** di validasi → **0 dari 7** benar di ruang detektor.
- Diagnosis: model mengunci warna sebagai identitas kelas; iluminan produksi *out-of-distribution*.

### 3.2 Kenapa dua-tahap penting
Pipeline kami: **(1)** operator membangun *versi dataset* dengan augmentasi yang
dimaterialisasi (ditulis ke berkas), **(2)** model dilatih dengan augmentasi training
(hyperparameter HSV Ultralytics) di atas versi itu. Jika tahap (1) merusak warna tetapi
tahap (2) mengasumsikan warna informatif (atau sebaliknya), sinyal yang dipelajari model
menjadi tak koheren — inilah sumber *color shortcut* yang terukur.

---

## 4. Konsep Inti & Novelti

**Tesis:** Perturbasi fotometrik pada tahap sintesis-dataset dan tahap training harus
**sepadan (consistent)**. Ada dua titik operasi yang koheren, dipilih sesuai peran warna:

| Mode | Peran warna | Sisi DATASET (materialisasi) | Sisi TRAINING (hyperparameter) |
|---|---|---|---|
| **BENTUK** | insidental (botol boleh warna apa pun) | 6 operasi rona diacak lebar (hue-sat, blackbody, iluminan, color-jitter, grayscale) | `hsv_h=0.03, hsv_s=0.70, hsv_v=0.50, bgr=0.10` |
| **WARNA** | identitas kelas (Kahf navy vs hitam) | operasi rona dimatikan, rona dikunci | `hsv_h=0.0, hsv_s=0.20, hsv_v=0.50, bgr=0.0` |

### Tiga pilar kontribusi
1. **Formalisasi konsistensi augmentasi↔training** pada pipeline dua-tahap, beserta dua
   titik operasi koheren (color-incidental / color-defining). *(Novelti utama.)*
2. **Karakterisasi & reproduksi mode kegagalan *color shortcut*** — ablasi 4-sel
   (dataset-mode × training-mode) yang menunjukkan sel tak-konsisten runtuh di bawah
   *illumination shift* meski mAP validasinya menyesatkan.
3. **Pemilihan mode & penyetelan gain berbasis data**: rasio diskriminan per-kanal HSV
   pada region objek menentukan dimensi warna mana yang membawa identitas kelas, lalu
   gain augmentasi disetel terbalik terhadapnya. Divalidasi di sistem terpasang (RVM).

### Pembeda dari literatur
- Bukan sekadar "menyetel hsv_s" — inti kebaruannya adalah **kopling lintas dua tahap**
  dan **mode kegagalan terukur**, bukan hyperparameter tunggal.
- Sebagian besar augmentasi (AutoAugment, RandAugment, Planckian Jitter) beroperasi
  hanya di tahap training on-the-fly; kopling dengan augmentasi yang **dimaterialisasi**
  lebih dulu belum banyak dibahas.

---

## 5. Formulasi

### 5.1 Augmentasi HSV training (Ultralytics `RandomHSV`)
Di ruang HSV OpenCV (H∈[0,180], S,V∈[0,255]), dengan gain acak `r ~ U(-1,1)`:

```
Hue :  H' = (H + r_h · 180) mod 180          # aditif — rotasi roda warna
Sat :  S' = clip(S · (r_s + 1), 0, 255)      # multiplikatif — gain [1-s, 1+s]
Val :  V' = clip(V · (r_v + 1), 0, 255)      # multiplikatif — gain [1-v, 1+v]
```
`bgr` = probabilitas tukar kanal R↔B (harus 0 untuk tugas warna).

### 5.2 Kendala konsistensi (formal)
Misal `A_ds` = himpunan perturbasi fotometrik pada tahap dataset, `A_tr` = pada tahap
training. Mode koheren mensyaratkan **arah invariansi keduanya sama**:
- BENTUK: `A_ds` dan `A_tr` sama-sama **menghancurkan** informasi rona (invariansi warna).
- WARNA: `A_ds` dan `A_tr` sama-sama **mempertahankan** rona (mengunci hue, menekan gain saturasi).

Ketidaksesuaian (`A_ds` menghancurkan warna, `A_tr` mempertahankan — atau sebaliknya)
menghasilkan target belajar tak koheren → *color shortcut*.

### 5.3 Pemilihan mode berbasis data (usulan metode)
Untuk tiap kanal `c ∈ {H, S, V}`, hitung rasio diskriminan Fisher pada rata-rata warna
per-objek di dalam mask:

```
D_c = Var_antar-kelas(mean_c) / Var_dalam-kelas(mean_c)
```

- `D_c` tinggi → kanal itu **membawa identitas kelas** → kunci/kecilkan gain-nya.
- `D_c` rendah → aman diaugmentasi lebar untuk robustness cahaya.

Aturan: `gain_c ∝ 1 / D_c` (dinormalkan ke rentang aman). Ini mengubah pilihan
biner BENTUK/WARNA menjadi kebijakan **per-kanal, kontinu, training-free, interpretable**.

---

## 6. Alur Sistem (pipeline)

```
        ┌─────────────┐     pilih mode      ┌──────────────────────┐
Foto ──▶│ Anotasi &   │──────(operator)────▶│ Pembuatan VERSI      │
mentah  │ dataset     │  BENTUK / WARNA     │ (augmentasi           │
        └─────────────┘         │           │  DIMATERIALISASI)     │
                                │           └──────────┬───────────┘
                                │  KENDALA KONSISTENSI │  versi dataset
                                │  (dipaksa oleh sistem)│  (berkas .pt/.jpg)
                                ▼           ┌──────────▼───────────┐
                        hyperparameter ────▶│ TRAINING (HSV aug    │
                        HSV = f(mode)       │  training = f(mode))  │
                                            └──────────┬───────────┘
                                                       ▼
                                            ┌──────────────────────┐
                                            │ Evaluasi produksi     │
                                            │ (illumination shift,  │
                                            │  ruang detektor RVM)  │
                                            └──────────────────────┘
```

Poin kunci: **satu pilihan mode** menyetel **kedua** kotak augmentasi secara sepadan.
Sistem menolak kombinasi tak-konsisten.

---

## 7. Desain Eksperimen

### 7.1 Ablasi inti (bukti tesis) — matriks 4-sel
| # | Mode DATASET | Mode TRAINING | Konsisten? | mAP val | Akurasi produksi (lampu shift) |
|---|---|---|---|---|---|
| 1 | BENTUK | BENTUK | ✅ | isi | isi |
| 2 | WARNA | WARNA | ✅ | isi | isi |
| 3 | BENTUK | WARNA | ❌ | isi (diduga menipu tinggi) | isi (diduga runtuh) |
| 4 | WARNA | BENTUK | ❌ | isi | isi |

Hipotesis: sel 1 & 2 (konsisten) unggul di **produksi**; sel 3 & 4 (tak konsisten)
menunjukkan celah mAP-val vs produksi terbesar (*color shortcut*).

### 7.2 Dataset
- **Warna-defining:** paragon / Kahf (facewash navy vs deodorant hitam) — pembeda utama SATURASI.
- **Bentuk-defining:** botol-kaleng-tetra-mlp-cup (5 kelas) — warna insidental.
- **Publik (untuk generalisasi):** ≥1 dataset fine-grained warna (mis. Flowers-102) + 1 dataset produk/ritel.

### 7.3 Baseline pembanding
- Augmentasi default Ultralytics (hsv_h=0.015, hsv_s=0.7, hsv_v=0.4).
- Color-invariant tetap (setara BENTUK).
- Color-preserving tetap (hue-lock; setara WARNA).
- Planckian Jitter (robustness iluminan fisika).
- RandAugment / AutoAugment / LA3 (kebijakan augmentasi terpelajar).

### 7.4 Metrik
- mAP50 & mAP50-95 (box & mask), **per-kelas AP**.
- **Celah val↔produksi** (indikator *shortcut*).
- Robustness di *illumination shift* terkontrol (grayscale, hue±, saturasi×, iluminan berwarna).
- **Signifikansi statistik**: ≥3 seed, uji-t / bootstrap.

### 7.5 Ablasi tambahan
- Kontribusi tiap kanal (H/S/V) pada mode WARNA.
- Sensitivitas pemetaan `gain ∝ 1/D_c`.
- Pengaruh sampel negatif & filter distribusi (katalog vs asli) — lihat §9.

---

## 8. Hasil Awal (preliminary — CPU, tanpa training GPU)

Studi pendahuluan pada paragon (2 produk Kahf) sudah mendukung hipotesis:

**8.1 Dimensi diskriminatif = SATURASI, bukan HUE**
| Produk | Hue | Sat | Val |
|---|---|---|---|
| Facewash (navy) | ~187° | 89 | 77 |
| Deodorant (hitam) | ~176° | 27 | 38 |
| **Selisih** | ~11° (kecil) | **62 (besar)** | 39 |

**8.2 Erosi keterpisahan kelas oleh `hsv_s`** (akurasi pemisahan 1-D berbasis saturasi):
| hsv_s | Akurasi pisah warna |
|---|---|
| 0 (tanpa aug) | 98,9% |
| **0,20 (mode WARNA)** | **98,1%** |
| 0,5 | 94,8% |
| **0,7 (default Ultralytics)** | **91,1%** |

→ Mode WARNA (hsv_s=0,20) mempertahankan sinyal warna; default 0,7 mengikisnya −7,8 poin.
→ **Bentuk tak terpengaruh** karena augmentasi geometrik (rotasi/skala/flip/mosaic)
   adalah jalur terpisah dari fotometrik.

*(Catatan: ini validasi keterpisahan tingkat-fitur; validasi penuh butuh training GPU
— matriks 4-sel §7.1.)*

---

## 9. Kontribusi Pendukung (opsional, memperkuat paper aplikasi)

Sistem yang sama memuat dua temuan yang bisa jadi kontribusi sekunder / bagian "sistem":
- **Filter distribusi katalog-vs-asli**: memisahkan foto web/katalog (latar putih, kurang 3D)
  dari capture RVM asli via kecerahan bingkai — tanpa mengubah dataset, dengan penjaga
  agar sampel negatif tak ikut terbuang.
- **Preservasi sampel negatif**: latar/negatif disengaja tak boleh tersapu filter.

---

## 10. Posisi vs Prior Art

| Karya | Fokus | Celah yang kita isi |
|---|---|---|
| LA3 / AutoAugment (arXiv:2304.10310) | kebijakan augmentasi per-label via pencarian mahal | kopling dua-tahap; kebijakan closed-form murah |
| Planckian Jitter (arXiv:2202.07993) | augmentasi iluminan fisika tetap | adaptif per-dataset; bisa jadi komponen robustness |
| Learnable Semantic Aug (arXiv:2309.00399) | augmentasi level-fitur fine-grained | level-citra, interpretable, untuk deteksi/segmentasi |
| Fisher Discriminant Ratio (klasik) | threshold/klasifikasi | dipakai untuk MENYETEL gain augmentasi per-kanal |
| Generalization Gap: Illumination (arXiv:2404.07514) | celah generalisasi augmentasi cahaya | mode kegagalan *shortcut* pada pipeline dua-tahap terukur |

Novelti = **kopling augmentasi-dimaterialisasi ↔ augmentasi-training + mode kegagalan
terukur di deployment nyata**, bukan operasi augmentasi baru.

---

## 11. Jurnal Target (Q1/Q2 informatika / AI)
- **Q1:** Pattern Recognition; IEEE T-IP; Expert Systems with Applications; Knowledge-Based
  Systems; Engineering Applications of Artificial Intelligence; Neurocomputing; Computers in Industry.
- **Q1/Q2 ramah aplikasi:** IEEE Access; Sensors; Applied Sciences; Journal of Real-Time Image Processing.
- **Angle daur-ulang:** Resources, Conservation & Recycling; Waste Management.

Rekomendasi: **paper aplikasi** (angle sistem RVM + mode kegagalan nyata) ke Engineering
Applications of AI / Expert Systems with Applications / Computers in Industry.

---

## 12. Rencana Kerja / Yang Dibutuhkan

- [ ] Reproduksi terkontrol mode kegagalan *color shortcut* (sel 3/4 matriks).
- [ ] Eksperimen 4-sel × ≥2 dataset internal × ≥3 seed.
- [ ] Tambah ≥2 dataset publik untuk generalisasi.
- [ ] Baseline: default, Planckian, RandAugment, LA3.
- [ ] Skrip evaluasi *illumination shift* terkontrol + metrik celah val↔produksi.
- [ ] Turunan teori singkat: kenapa `gain ∝ 1/Fisher` mempertahankan keterpisahan.
- [ ] **(Blokir sekarang: GPU dipakai training lain — jangan OOM.)** Semua eksperimen
      GPU menunggu GPU bebas.

Catatan data: paragon sangat timpang (facewash 87 vs deodorant 8 objek) — perlu banyak
tambahan data deodorant sebelum angka bisa dipercaya.

---

## 13. Referensi (awal)
- Ultralytics — Data Augmentation: https://docs.ultralytics.com/guides/yolo-data-augmentation
- Ultralytics — Config (default HSV): https://docs.ultralytics.com/usage/cfg
- Ultralytics `RandomHSV` (kode): https://github.com/ultralytics/ultralytics/blob/main/ultralytics/data/augment.py
- Zini dkk., *Planckian Jitter* (ICLR 2023): https://arxiv.org/abs/2202.07993
- *LA3: Label-Aware AutoAugment*: https://arxiv.org/pdf/2304.10310
- *Learnable Semantic Data Augmentation* (fine-grained): https://arxiv.org/pdf/2309.00399
- *Generalization Gap in Data Augmentation: Illumination*: https://arxiv.org/pdf/2404.07514
- Fisher Discriminant Ratio (overview): https://www.sciencedirect.com/topics/computer-science/fisher-discriminant-ratio

---

*Disusun sebagai kerangka konsep. Angka pada §8 adalah hasil analisis fitur (CPU); klaim
performa akhir menunggu eksperimen training penuh (§7, §12).*
