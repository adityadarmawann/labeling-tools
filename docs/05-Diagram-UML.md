<div align="center">

# 🧩 Diagram UML & Alur HIGOLAB

*Use-case, hak akses, flowchart tiap fitur, sequence, dan state — lengkap & rinci.*

> Semua diagram memakai **Mermaid** — dirender otomatis di GitHub.

</div>

---

## 📇 Daftar Isi
1. [Diagram Use-Case & Hak Akses](#1--diagram-use-case--hak-akses)
2. [Matriks Hak Akses](#2--matriks-hak-akses)
3. [Flowchart Induk (seluruh sistem)](#3--flowchart-induk-seluruh-sistem)
4. [Alur: Autentikasi & Pendaftaran](#4--alur-autentikasi--pendaftaran)
5. [Alur: Unggah & Impor](#5--alur-unggah--impor)
6. [Alur: Anotasi (Kanvas + SAM)](#6--alur-anotasi-kanvas--sam)
7. [Alur: Dataset & Penugasan](#7--alur-dataset--penugasan)
8. [Alur: Buat Versi (Wizard 6 Langkah)](#8--alur-buat-versi-wizard-6-langkah)
9. [Alur: Training & Training Lanjutan](#9--alur-training--training-lanjutan)
10. [Alur: Evaluasi Produksi & Ekspor](#10--alur-evaluasi-produksi--ekspor)
11. [Sequence: Buat Versi Asinkron](#11--sequence-buat-versi-asinkron)
12. [State: Status Training](#12--state-status-training)
13. [State: Tingkat Anotasi Gambar](#13--state-tingkat-anotasi-gambar)
14. [Class Diagram: Model Data (Sidecar)](#14--class-diagram-model-data-sidecar)

---

## 1. 👥 Diagram Use-Case & Hak Akses

```mermaid
flowchart LR
    Admin(["🔑 Admin"])
    Pemilik(["👤 Pemilik Projek"])
    Pelabel(["🖊️ Pelabel Bertugas"])
    Anggota(["👁️ Anggota"])
    Tamu(["🚫 Pengunjung"])

    subgraph UC["Use Cases HIGOLAB"]
        u_login["Masuk / Daftar"]
        u_kelolauser["Kelola Pengguna & Persetujuan"]
        u_unggah["Unggah / Impor Data"]
        u_lihat["Lihat Projek & Gambar"]
        u_labelall["Melabeli SEMUA Gambar"]
        u_labeljatah["Melabeli Jatah Sendiri"]
        u_dataset["Kelola Dataset (masukkan/keluarkan)"]
        u_tugas["Tugaskan Pelabel & Undang Anggota"]
        u_versi["Buat / Hapus Versi"]
        u_latih["Latih / Lanjutkan Model"]
        u_eval["Evaluasi Produksi"]
        u_ekspor["Ekspor / Unduh Bobot"]
    end

    Tamu --> u_login
    Anggota --> u_lihat
    Pelabel --> u_lihat
    Pelabel --> u_labeljatah
    Pemilik --> u_lihat
    Pemilik --> u_unggah
    Pemilik --> u_labelall
    Pemilik --> u_dataset
    Pemilik --> u_tugas
    Pemilik --> u_versi
    Pemilik --> u_latih
    Pemilik --> u_eval
    Pemilik --> u_ekspor
    Admin --> u_kelolauser
    Admin -. "juga bisa jadi pemilik" .-> Pemilik

    style Admin fill:#ffcdd2,stroke:#c62828
    style Pemilik fill:#c8e6c9,stroke:#2e7d32
    style Pelabel fill:#fff9c4,stroke:#f9a825
    style Anggota fill:#bbdefb,stroke:#1565c0
    style Tamu fill:#eeeeee,stroke:#616161
```

---

## 2. 🔐 Matriks Hak Akses

| Aksi | 🔑 Admin | 👤 Pemilik | 🖊️ Pelabel | 👁️ Anggota | 🚫 Tamu |
|---|:---:|:---:|:---:|:---:|:---:|
| Masuk / Daftar | ✅ | ✅ | ✅ | ✅ | ✅ |
| Kelola pengguna & persetujuan | ✅ | ❌ | ❌ | ❌ | ❌ |
| Lihat projek | ✅¹ | ✅ | ✅ | ✅ | ❌ |
| Melabeli **semua** gambar | ✅¹ | ✅ | ❌ | ❌ | ❌ |
| Melabeli **jatah sendiri** | — | ✅ | ✅ | ❌ | ❌ |
| Kelola dataset | ✅¹ | ✅ | ❌ | ❌ | ❌ |
| Tugaskan pelabel / undang anggota | ✅¹ | ✅ | ❌ | ❌ | ❌ |
| Buat / hapus versi | ✅¹ | ✅ | ❌ | ❌ | ❌ |
| Latih / lanjutkan model | ✅¹ | ✅ | ❌ | ❌ | ❌ |
| Evaluasi & ekspor | ✅¹ | ✅ | ❌ | ❌ | ❌ |

> ¹ Admin punya hak kelola-pengguna sistem; untuk aksi **di dalam sebuah projek** ia mengikuti aturan pemilik (hak penuh hanya pada projek yang ia miliki / folder bersama). Projek yang bukan miliknya & tak diundang **tidak muncul**.

---

## 3. 🗺️ Flowchart Induk (Seluruh Sistem)

```mermaid
flowchart TD
    Start([🚪 Buka HIGOLAB]) --> Login{Sudah masuk?}
    Login -->|Belum| Auth[Masuk / Daftar]
    Auth --> Home
    Login -->|Sudah| Home[🏠 Daftar Projek]

    Home --> Pilih[Pilih / Buat Projek]
    Pilih --> Unggah[📤 Unggah Data]
    Unggah --> Anotasi[✍️ Anotasi]
    Anotasi --> Dataset[🗂️ Masukkan ke Dataset]
    Dataset --> Versi[📦 Buat Versi]
    Versi --> Latih[🎯 Training]
    Latih --> Eval[🔬 Evaluasi Produksi]
    Latih --> Ekspor[⬇️ Ekspor / Unduh]
    Eval --> Selesai([✅ Model siap pakai])
    Ekspor --> Selesai

    Anotasi -.->|tugaskan| Tugas[👥 Penugasan Pelabel]
    Tugas -.-> Anotasi

    style Start fill:#e3f2fd,stroke:#1976d2
    style Selesai fill:#c8e6c9,stroke:#2e7d32
    style Versi fill:#f3e5f5,stroke:#7b1fa2
    style Latih fill:#fce4ec,stroke:#c2185b
```

---

## 4. 🔑 Alur: Autentikasi & Pendaftaran

```mermaid
flowchart TD
    A[Buka aplikasi] --> B{Punya akun?}
    B -->|Tidak| C[Daftar: nama + sandi + email]
    C --> D{Pengguna pertama?}
    D -->|Ya| E[🔑 Otomatis jadi ADMIN]
    D -->|Tidak| F[Status: menunggu persetujuan]
    F --> G[Admin menyetujui via /akun]
    G --> H[Akun aktif]
    E --> H
    B -->|Ya| I[Masuk]
    H --> I
    I --> J{Kredensial benar?}
    J -->|Ya| K[🏠 Masuk ke Daftar Projek]
    J -->|Tidak| I

    style E fill:#ffcdd2,stroke:#c62828
    style K fill:#c8e6c9,stroke:#2e7d32
```

---

## 5. 📤 Alur: Unggah & Impor

```mermaid
flowchart TD
    A[Halaman Unggah Data] --> B{Jenis sumber?}
    B -->|Foto satuan/batch| C[Pilih berkas + beri nama batch]
    B -->|Dataset Roboflow/YOLO/labelme| D[Pilih arsip/folder]
    C --> E[Unggah per berkas<br/>1 gagal ≠ semua gagal]
    D --> F[Ratakan split train/valid/test]
    E --> G[Tercatat: batch + tanggal di .tag.json]
    F --> G
    G --> H[🖼️ Muncul di grid projek]
    H --> I{Lanjut?}
    I -->|Anotasi| J[Ke Kanvas]
    I -->|Bagi tugas| K[Ke Penugasan]

    style H fill:#fff3e0,stroke:#f57c00
```

---

## 6. ✍️ Alur: Anotasi (Kanvas + SAM)

```mermaid
flowchart TD
    A[Buka gambar di kanvas] --> B{Cara melabeli?}
    B -->|Manual| C[Gambar kotak / poligon]
    B -->|SAM otomatis| D[Klik objek]
    D --> E[SAM hasilkan poligon]
    E --> F{Sudah pas?}
    F -->|Belum| G["+Point / −Point<br/>(tuntun SAM)"]
    G --> E
    F -->|Ya| H[Beri nama kelas]
    C --> H
    B -->|Tak ada objek| I[Tandai LATAR<br/>contoh negatif]
    H --> J{Kelas baru?}
    J -->|Ya| K[Didaftarkan otomatis<br/>ke data.yaml]
    J -->|Tidak| L[Simpan]
    K --> L
    I --> L
    L --> M[Navigasi ⬅️➡️<br/>ikut filter tab penugasan]
    M --> A

    style D fill:#e8f5e9,stroke:#388e3c
    style I fill:#eeeeee,stroke:#616161
    style K fill:#fff9c4,stroke:#f9a825
```

---

## 7. 🗂️ Alur: Dataset & Penugasan

```mermaid
flowchart TD
    subgraph Penugasan
        T1[Pemilik pilih gambar] --> T2[Tugaskan ke pelabel]
        T2 --> T3[Pelabel melabeli jatahnya]
        T3 --> T4{Selesai?}
        T4 -->|Belum| T3
        T4 -->|Ya| T5[Gambar beranotasi]
    end
    subgraph Dataset
        D1[Pilih gambar untuk Dataset] --> D2{Sudah beranotasi<br/>atau latar?}
        D2 -->|Tidak| D3[❌ Ditolak]
        D2 -->|Ya| D4[✅ Masuk Dataset]
    end
    T5 --> D1
    D4 --> V[Siap Buat Versi]

    style D3 fill:#ffcdd2,stroke:#c62828
    style D4 fill:#c8e6c9,stroke:#2e7d32
```

---

## 8. 📦 Alur: Buat Versi (Wizard 6 Langkah)

```mermaid
flowchart TD
    S1["1️⃣ Gambar Sumber<br/>+ Filter (batch/tag/katalog)<br/>⚠️ peringatan bila buang >40%"] --> S2
    S2["2️⃣ Kelas (Modify Classes)<br/>pakai / latar / gabung / rename"] --> S3
    S3["3️⃣ Split train/valid/test<br/>(anti-bocor opsional)"] --> S4
    S4["4️⃣ Preprocessing<br/>(resize, auto-orient, dll.)"] --> S5
    S5["5️⃣ Augmentasi<br/>+ Mode Warna (BENTUK/WARNA)"] --> S6
    S6["6️⃣ Buat<br/>estimasi: gambar, objek, ukuran, sisa disk"] --> B{Cukup ruang?}
    B -->|Tidak| W[⚠️ Tolak / peringatan disk]
    B -->|Ya| BUILD[🔨 Build asinkron ke .versi/vN/<br/>sumber TAK disentuh]
    BUILD --> P[Pantau kemajuan]
    P --> DONE[✅ Versi siap dilatih]

    style S2 fill:#fff9c4,stroke:#f9a825
    style S5 fill:#e8f5e9,stroke:#388e3c
    style BUILD fill:#f3e5f5,stroke:#7b1fa2
    style DONE fill:#c8e6c9,stroke:#2e7d32
```

---

## 9. 🎯 Alur: Training & Training Lanjutan

```mermaid
flowchart TD
    A[Pilih versi + tugas detect/segment] --> B{Server mode GPU?}
    B -->|Tidak| X[❌ Ditolak: perlu LABELAPP_OLAH=gpu]
    B -->|Ya| C[Setel parameter<br/>preset v14 / lanjutan]
    C --> D[Mode Warna disinkronkan<br/>hsv latih = mode versi]
    D --> E[🚀 Jalankan training subproses]
    E --> F[Pantau: kurva/epoch, ETA, GPU/RAM]
    F --> G{Selesai / berhenti dini}
    G --> H[Rincian: mAP per kelas,<br/>galeri, daftar kelas]
    H --> I{Mau lanjut latih?}
    I -->|Ya| J["Training Lanjutan<br/>dari best.pt/last.pt<br/>(warisi setelan)"]
    J --> E
    I -->|Tidak| K[⬇️ Unduh best.pt/last.pt]

    style X fill:#ffcdd2,stroke:#c62828
    style D fill:#e8f5e9,stroke:#388e3c
    style J fill:#fff9c4,stroke:#f9a825
```

---

## 10. 🔬 Alur: Evaluasi Produksi & Ekspor

```mermaid
flowchart LR
    subgraph Evaluasi
        E1[Pilih model] --> E2[Uji: geser warna<br/>grayscale, hue±, saturasi×, iluminan]
        E2 --> E3{Keputusan berubah<br/>karena warna?}
        E3 -->|Ya, banyak| E4[⚠️ Model bergantung warna]
        E3 -->|Tidak| E5[✅ Stabil]
        E2 --> E6[Uji latar kosong<br/>+ akurasi + confusion]
    end
    subgraph Ekspor
        X1[Pilih format] --> X2[YOLO / COCO / VOC / CreateML]
        X2 --> X3[⬇️ ZIP / bobot .pt]
    end

    style E4 fill:#ffe0b2,stroke:#e65100
    style E5 fill:#c8e6c9,stroke:#2e7d32
```

---

## 11. 🔁 Sequence: Buat Versi Asinkron

```mermaid
sequenceDiagram
    actor U as 👤 Pemilik
    participant JS as 🌐 versi.js
    participant API as ⚙️ FastAPI
    participant W as 🧵 Pekerjaan (thread)
    participant FS as 💾 .versi/vN/

    U->>JS: Klik "Buat"
    JS->>API: POST /api/versi/estimasi (resep)
    API-->>JS: perkiraan (gambar, ukuran, disk)
    U->>JS: Konfirmasi
    JS->>API: POST /api/versi/mulai (resep)
    API->>W: mulai build (create_task)
    API-->>JS: {ok, nomor} (langsung balas)
    loop tiap ~0.7s
        JS->>API: GET /api/versi/kemajuan
        API-->>JS: persen, fase, ETA
    end
    W->>FS: tulis gambar teraugmentasi
    W->>API: catat selesai
    JS->>API: GET /api/versi/kemajuan
    API-->>JS: selesai ✅
    JS-->>U: Versi siap
```

---

## 12. 🔵 State: Status Training

```mermaid
stateDiagram-v2
    [*] --> antre: dibuat
    antre --> jalan: subproses mulai
    jalan --> selesai: best.pt tersimpan
    jalan --> gagal: error
    jalan --> hilang: proses mati tanpa hasil
    jalan --> batal: dihentikan pengguna
    selesai --> [*]
    gagal --> [*]
    hilang --> [*]
    batal --> [*]

    note right of jalan
        Pantauan: kurva/epoch,
        ETA, GPU/RAM
    end note
```

---

## 13. 🎨 State: Tingkat Anotasi Gambar

```mermaid
stateDiagram-v2
    [*] --> belum: baru diunggah
    belum --> latar: ditandai latar (negatif)
    belum --> ok: dianotasi, valid
    belum --> warn: dianotasi, ada peringatan
    warn --> ok: peringatan diperbaiki
    ok --> dataset: dimasukkan ke Dataset
    latar --> dataset: dimasukkan ke Dataset
    belum --> [*]: TIDAK bisa masuk Dataset

    note right of latar
        Sampel negatif DISENGAJA —
        tak boleh dibuang filter
    end note
```

---

## 14. 🗃️ Class Diagram: Model Data (Sidecar)

```mermaid
classDiagram
    class Projek {
        +file: .tugas.json
        +pemilik: str
        +anggota: map
        +dataset: list_gambar
        +tugas: pelabel_ke_gambar
    }
    class Gambar {
        +berkas: jpg
        +anotasi: json_atau_txt
        +tag: list
        +batch: str
        +tingkat: belum_latar_ok_warn
    }
    class Versi {
        +folder: .versi_vN
        +nomor: int
        +resep: augmentasi_kelas_filter
        +mode_warna: BENTUK_atau_WARNA
        +hasil: gambar_teraugmentasi
    }
    class Training {
        +folder: .latih_Ln
        +nomor: int
        +versi: int
        +par: hsv_lr_epochs_dll
        +kelas: list
        +lanjut_dari: int_opsional
        +bobot: best_atau_last_pt
    }
    class Pengguna {
        +file: users.json
        +nama: str
        +peran: admin_atau_biasa
        +disetujui: bool
    }

    Projek "1" *-- "banyak" Gambar
    Projek "1" *-- "banyak" Versi
    Versi "1" --> "banyak" Training : dilatih jadi
    Training ..> Training : lanjut_dari
    Pengguna "1" --> "banyak" Projek : memiliki
```

---

<div align="center">

⬅️ [Kamus Parameter](04-Kamus-Parameter.md) · [Tentang HIGOLAB](01-Tentang-HIGOLAB.md) 🏠

</div>
