"""
Melatih model YOLO dari sebuah VERSI dataset.

Acuannya train-rvm-v14.py. Angkanya tidak disalin begitu saja: tiap parameter
yang berbeda dari bawaan Ultralytics membawa alasannya sendiri di PRESET_V14,
karena angka tanpa alasan adalah angka yang akan diubah orang berikutnya tanpa
tahu apa yang ia batalkan.

TIGA KEPUTUSAN RANCANGAN YANG PERLU DIKETAHUI LEBIH DULU

1. TRAINING BERJALAN SEBAGAI SUBPROSES, BUKAN THREAD.
   Satu training memakan berjam-jam. Server dev berjalan dengan --reload dan
   menyalakan ulang dirinya tiap kali ada berkas berubah; server produksi pun
   sekali waktu di-restart. Thread di dalam proses server akan mati bersamanya
   dan membuang pekerjaan setengah jalan tanpa sisa. Subproses yang terlepas
   bertahan melewati itu.

2. KEMAJUAN DIBACA DARI DISK, BUKAN DARI MEMORI.
   Konsekuensi dari nomor 1: tidak ada objek di memori yang bisa ditanyai.
   Untungnya Ultralytics sendiri menulis results.csv tiap epoch, jadi kemajuan
   dan metriknya bisa dibaca dari situ oleh siapa pun, kapan pun — termasuk
   sesudah server dinyalakan ulang di tengah training.

3. SATU TRAINING PADA SATU WAKTU.
   Bukan kerapian: satu training YOLO mengisi hampir seluruh VRAM. Dua
   sekaligus di kartu 8 GB bukan dua kali lebih cepat melainkan dua-duanya
   gagal kehabisan memori. Yang lain menunggu di antrean, dan antreannya
   terlihat di layar.

SINKRONISASI WARNA DENGAN VERSINYA — INI YANG PALING MUDAH SALAH

v14 menaikkan hsv_h dari 0,0 ke 0,030 karena v13 melaporkan mAP50-95 0,9499
lalu gagal 0 dari 7 pada foto RVM sungguhan. Sebabnya model memutuskan lewat
WARNA, bukan bentuk: gelas plastik dijawab `kaleng` 0,97, tetapi gelas yang
sama dalam grayscale dijawab `plastic-cup` 0,93.

Docstring train-rvm-v14.py menyatakannya tegas:

    "aug-bal membuka warna di sisi dataset, script ini membukanya di sisi
     waktu-latih. Kalau salah satu dikembalikan ke setelan v13, perbaikannya
     batal."

Karena itu form training di sini TIDAK menawarkan setelan warna sebagai
pilihan bebas. Ia membaca resep versinya, menyimpulkan apakah warna dibuka di
sisi dataset, lalu menyarankan setelan waktu-latih yang sejalan — dan
memperingatkan kalau keduanya tidak cocok. Lihat periksa_warna().
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from ..log import catat

log = catat("labelapp.latih")

FOLDER = ".latih"
MAKS_NAMA = 60
MAKS_CATATAN = 300

_kunci = threading.Lock()


# ============================================================
# PARAMETER v14
# ============================================================
#
# Disalin dari train-rvm-v14.py beserta alasannya. Yang berubah dari bawaan
# Ultralytics diberi tanda; yang sama dengan bawaan tetap ditulis supaya
# seluruh setelan yang berlaku bisa dibaca di satu tempat, bukan separuh di
# sini dan separuh di dalam pustaka.
PRESET_V14: dict = {
    # ---- dasar ----
    "imgsz": 640,
    "epochs": 250,
    "batch": 16,
    "workers": 8,

    # ---- optimizer & laju belajar ----
    # AdamW dengan lr0 kecil: v14 melatih dari bobot pra-latih pada dataset
    # yang sudah diaugmentasi berat, jadi langkah besar merusak lebih banyak
    # daripada menolong.
    "optimizer": "AdamW",
    "lr0": 0.0003,
    "lrf": 0.01,
    "cos_lr": True,
    "warmup_epochs": 3,
    "patience": 50,

    # ---- bobot loss ----
    # cls 2.0 dipertahankan dari v13, DENGAN catatan v14: bobot loss bukan
    # obat untuk pintasan warna. Yang menyembuhkan augmentasi warna dan data
    # latar RVM, bukan angka ini.
    "box": 7.5,
    "cls": 2.0,
    "dfl": 1.5,

    # ---- WARNA: inti perubahan v14 ----
    # v13 memakai hsv_h=0.0 dan hsv_s=0.20 dengan alasan "saturasi pembeda
    # utama antar-material, jangan dilonggarkan". Alasan itu terbalik:
    # augmentasi bekerja dengan membuat sebuah petunjuk TIDAK BISA
    # DIANDALKAN. Kunci warnanya, dan warna jadi petunjuk termurah yang
    # paling akurat di data latih — model memakainya lalu berhenti belajar
    # bentuk.
    #
    # hsv_h 0.030 itu dua kali bawaan Ultralytics (0.015), karena pergeseran
    # iluminan RVM ekstrem: puncak hue produksi 310 derajat (ungu) lawan 26
    # derajat di data latih.
    "hsv_h": 0.030,
    "hsv_s": 0.70,
    "hsv_v": 0.50,
    # Tukar kanal R<->B pada 10% gambar. Cara termurah memaksa model tidak
    # menyandarkan keputusan pada satu kanal warna tertentu. Diverifikasi
    # berlaku untuk segmentasi (ultralytics/data/augment.py, kelas Format).
    "bgr": 0.10,

    # ---- skala & geometri ----
    # scale 0.3 dipertahankan dari v13 dan alasannya tetap benar: variasi
    # ukuran sudah ditangani fase crop/zoom di pembuatan versi secara
    # terarah, jadi tidak perlu diacak lebar lagi di sini.
    "scale": 0.3,
    "degrees": 25,
    "translate": 0.15,
    "shear": 0.0,
    "perspective": 0.0,
    "fliplr": 0.5,
    "flipud": 0.3,

    # ---- komposisi ----
    # mosaic 0.3, bukan 0.6. Diukur pada keluaran aug-bal v14:
    #     mosaic  median  <0,5% frame  seukuran produksi
    #     0.6      1,91%     19,1%          26,3%
    #     0.3      3,05%     12,8%          28,0%
    # Objek di RVM sungguhan sekitar 5-7% frame. Pada 0.6 hampir seperlima
    # contoh latih mengecil di bawah 0,5% frame — pada 640x640 itu cuma
    # ~2.000 piksel, terlalu sedikit untuk dipelajari.
    #
    # Catatan jujur yang ikut disalin dari v14: ini penyesuaian berdasarkan
    # sebaran ukuran, BUKAN perbaikan yang sudah terbukti menaikkan akurasi.
    "mosaic": 0.3,
    "close_mosaic": 30,
    # Menempel objek antar-gambar merusak konteks RVM yang justru sedang
    # diajarkan.
    "copy_paste": 0.0,
    "mixup": 0.0,
    "cutmix": 0.0,

    # ---- khusus segmentasi ----
    "overlap_mask": True,
    "mask_ratio": 2,

    # ---- keluaran ----
    "rect": False,
    "cache": False,
    "seed": 0,
    "plots": True,
}

# erasing SENGAJA TIDAK ADA di sini. Sempat disetel 0.20 di rancangan awal
# v14 lalu dibatalkan setelah diperiksa: parameter itu hanya dibaca
# classify_augmentations() (jalur ClassificationDataset), jadi untuk
# task=segment ia tidak berpengaruh sama sekali. Menuliskannya hanya menipu
# pembaca berikutnya seolah ada oklusi padahal tidak ada.

# Batas yang boleh diubah orang lewat form. Di luar ini permintaan ditolak —
# bukan untuk menggurui, tetapi karena angka di luar rentang ini hampir selalu
# salah ketik, dan salah ketik yang lolos baru ketahuan enam jam kemudian.
BATAS: dict[str, tuple] = {
    "epochs": (1, 1000),
    "batch": (1, 64),
    "imgsz": (320, 1280),
    "workers": (0, 16),
    "lr0": (1e-6, 0.1),
    "lrf": (1e-4, 1.0),
    "patience": (0, 500),
    "warmup_epochs": (0, 20),
    "box": (0.0, 20.0),
    "cls": (0.0, 20.0),
    "dfl": (0.0, 20.0),
    "hsv_h": (0.0, 0.5),
    "hsv_s": (0.0, 1.0),
    "hsv_v": (0.0, 1.0),
    "bgr": (0.0, 1.0),
    "scale": (0.0, 1.0),
    "degrees": (0.0, 180.0),
    "translate": (0.0, 1.0),
    "fliplr": (0.0, 1.0),
    "flipud": (0.0, 1.0),
    "mosaic": (0.0, 1.0),
    "close_mosaic": (0, 200),
    "copy_paste": (0.0, 1.0),
    "mask_ratio": (1, 8),
}

TUGAS = ("segment", "detect")

# Bobot awal yang boleh dipakai. Dicari di beberapa tempat supaya tidak
# menuntut orang menyalin berkas dulu sebelum bisa melatih sekali pun.
# Nama yang diunduh sendiri oleh Ultralytics kalau tidak ada berkas lokal.
# Disediakan supaya orang bisa melatih sekali pun tanpa menyalin berkas dulu;
# yang lokal tetap didahulukan karena tidak menuntut jaringan.
BOBOT_UNDUH = (
    ("yolo26n-seg.pt", "segment", "YOLO26 nano — segmentasi (dipakai v14)"),
    ("yolo26s-seg.pt", "segment", "YOLO26 small — segmentasi, lebih besar"),
    ("yolo26n.pt", "detect", "YOLO26 nano — deteksi kotak"),
    ("yolo26s.pt", "detect", "YOLO26 small — deteksi kotak"),
)


def dir_bobot() -> list[Path]:
    """Tempat berkas .pt dicari, berurutan."""
    v = (os.environ.get("LABELAPP_BOBOT_DIR") or "").strip()
    urut = [Path(v).expanduser()] if v else []
    # app/services/latih.py -> app -> label-apps -> labeling-tools(-dev)
    akar = Path(__file__).resolve().parents[3]
    urut.append(akar / "models")
    # Folder pipeline v14, kalau kebetulan ada di sebelah. Bukan keharusan:
    # sistem ini tidak boleh menuntut projek lain hadir untuk bisa jalan.
    urut.append(akar.parent.parent / "sirsak" / "sirsak-v14")
    return [d for d in urut if d.is_dir()]


def bobot_tersedia() -> list[dict]:
    """Bobot awal yang bisa dipakai: berkas lokal dulu, baru yang diunduh."""
    keluar, terlihat = [], set()
    for d in dir_bobot():
        for p in sorted(d.glob("*.pt")):
            if p.name in terlihat:
                continue
            terlihat.add(p.name)
            keluar.append({
                "nama": p.name, "path": str(p), "lokal": True,
                "mb": round(p.stat().st_size / 1e6, 1),
                # "-seg" di nama berkas Ultralytics menandai model segmentasi.
                "tugas": "segment" if "-seg" in p.stem else "detect",
                "ket": f"berkas lokal di {d.name}/",
            })
    for nama, tugas, ket in BOBOT_UNDUH:
        if nama in terlihat:
            continue
        keluar.append({"nama": nama, "path": nama, "lokal": False,
                       "mb": 0, "tugas": tugas,
                       "ket": ket + " (diunduh saat pertama dipakai)"})
    return keluar


# ============================================================
# SINKRONISASI WARNA
# ============================================================
#
# Operasi augmentasi di pembuatan versi yang benar-benar MENGGESER WARNA.
# grayscale ikut: ia menghapus warna sepenuhnya, yang juga membuat warna tidak
# bisa diandalkan. terang_kontras dan gamma TIDAK ikut — keduanya mengubah
# kecerahan tanpa menyentuh rona, jadi model yang memakai rona sebagai pintasan
# tetap aman memakainya.
OP_WARNA = ("hue_sat", "blackbody", "iluminan", "color_jitter", "grayscale",
            "saturasi")


def _aug_aktif(resep: dict, oid: str, bawaan: bool = True) -> bool:
    par = ((resep or {}).get("aug") or {}).get(oid)
    if par is None:
        return bawaan
    return bool(par.get("aktif", True))


def periksa_warna(versi: dict, katalog_aug: dict | None = None) -> dict:
    """Lihat _periksa_warna_bentuk / _periksa_warna_warna."""
    from . import mode_warna as mw

    resep = (versi or {}).get("resep") or {}
    m = mw.dari_resep(resep)
    if m == mw.WARNA:
        return _periksa_warna_warna(resep, m)
    return _periksa_warna_bentuk(versi, katalog_aug, m)


def _periksa_warna_warna(resep: dict, m: str) -> dict:
    """Mode `warna`: yang diperiksa justru KEBALIKANNYA.

    Di sini rona adalah identitas kelasnya, jadi yang harus dipastikan bukan
    "apakah warna sudah dibuka" melainkan "apakah warna sudah benar-benar
    DIKUNCI". Operasi penggeser rona yang tertinggal menyala menghasilkan
    dataset yang labelnya diam-diam salah.
    """
    from . import mode_warna as mw

    aug = resep.get("aug") or {}
    bocor = [o for o in mw.OP_GESER_RONA
             if (aug.get(o) or {}).get("aktif") is not False]
    saran = mw.par_latih(m)
    if bocor:
        # bangun_pipeline memaksanya mati, jadi ini tidak pernah sampai ke
        # datasetnya — tetapi tetap dilaporkan supaya resepnya bisa dirapikan.
        return {"dibuka": False, "mode": m, "tingkat": "ok", "saran": saran,
                "op_nyala": bocor, "pelat": "",
                "pesan": ("Mode warna: rona dipertahankan, jadi setelan "
                          "waktu-latih mengunci hsv_h dan bgr. Operasi "
                          "penggeser rona di resep (%s) diabaikan saat "
                          "augmentasi." % ", ".join(bocor))}
    return {"dibuka": False, "mode": m, "tingkat": "ok", "saran": saran,
            "op_nyala": [], "pelat": "",
            "pesan": ("Mode warna: warna kemasan adalah bagian dari kelasnya, "
                      "jadi rona tidak digeser di augmentasi dan dikunci di "
                      "waktu-latih (hsv_h 0, bgr 0). Yang tetap divariasikan "
                      "hanya terang.")}


def _periksa_warna_bentuk(versi: dict, katalog_aug: dict | None, m: str) -> dict:
    """Apakah setelan warna waktu-latih akan sejalan dengan versinya.

    Mengembalikan keterangan siap-tampil, bukan sekadar bool: yang perlu
    diketahui orang bukan "cocok/tidak" melainkan APA yang tidak cocok dan apa
    akibatnya, karena akibatnya baru terlihat enam jam kemudian dalam bentuk
    mAP tinggi yang tidak berarti apa-apa.
    """
    resep = (versi or {}).get("resep") or {}
    kat = katalog_aug or {}
    nyala = [o for o in OP_WARNA
             if _aug_aktif(resep, o, bool(kat.get(o, {}).get("bawaan_aktif", True)))]

    # Mode pelat latar RVM: "netral" menetralkan rona pelatnya, "asli"
    # membiarkan warna ruangan apa adanya. Yang netral membuat latar berhenti
    # jadi petunjuk kelas; itu sejalan dengan membuka warna.
    pelat = ((resep or {}).get("latar") or {}).get("mode") or ""

    dibuka = len(nyala) >= 2
    from . import mode_warna as mw

    if dibuka:
        saran = mw.par_latih(m)
        pesan = ("Versi ini dibuat dengan warna dibuka (%s). Setelan "
                 "waktu-latih di bawah mengikutinya." % ", ".join(nyala))
        tingkat = "ok"
    else:
        # Warna terkunci di sisi dataset. Membukanya di sini saja TIDAK
        # memperbaiki apa pun — v14 menyatakan keduanya harus sejalan — tetapi
        # menguncinya di kedua sisi persis keadaan v13 yang gagal 0 dari 7.
        saran = mw.par_latih(m)
        pesan = ("Versi ini dibuat dengan augmentasi warna nyaris mati (%s). "
                 "Itu keadaan v13, yang melaporkan mAP50-95 0,9499 lalu gagal "
                 "0 dari 7 pada foto RVM sungguhan karena model memutuskan "
                 "lewat warna, bukan bentuk. Membuka warna di sini saja tidak "
                 "menambalnya — buat ulang versinya dengan augmentasi warna "
                 "menyala."
                 % (", ".join(nyala) if nyala else "tidak ada satu pun"))
        tingkat = "awas"

    return {"dibuka": dibuka, "tingkat": tingkat, "pesan": pesan, "mode": m,
            "op_nyala": nyala, "pelat": pelat, "saran": saran}


# ============================================================
# BERKAS
# ============================================================

def _dir(ds) -> Path:
    return Path(ds) / FOLDER


def dir_latih(ds, nomor: int) -> Path:
    return _dir(ds) / f"L{int(nomor)}"


def berkas_latih(ds, nomor: int) -> Path:
    return _dir(ds) / f"L{int(nomor)}.json"


def nomor_berikut(ds) -> int:
    d = _dir(ds)
    if not d.is_dir():
        return 1
    n = 0
    for p in d.glob("L*.json"):
        m = re.fullmatch(r"L(\d+)", p.stem)
        if m:
            n = max(n, int(m.group(1)))
    return n + 1


def baca(ds, nomor: int) -> dict | None:
    p = berkas_latih(ds, nomor)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def _tulis(ds, nomor: int, isi: dict) -> None:
    p = berkas_latih(ds, nomor)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(isi, indent=1))
    os.replace(tmp, p)


def perbarui(ds, nomor: int, **nilai) -> dict:
    with _kunci:
        isi = baca(ds, nomor) or {}
        isi.update(nilai)
        _tulis(ds, nomor, isi)
        return isi


# ============================================================
# MEMBACA KEMAJUAN DARI DISK
# ============================================================
#
# Ultralytics menulis results.csv setiap epoch selesai. Itu yang dipakai di
# sini, bukan callback ke dalam memori: proses servernya boleh mati dan
# menyala lagi di tengah training tanpa kehilangan satu angka pun.

# Kolom metrik yang ditampilkan, diurutkan sesuai kepentingannya. Nama
# kolomnya berbeda antara detect dan segment — (B) untuk kotak, (M) untuk
# mask — jadi keduanya dicari dan yang ada dipakai.
METRIK = (
    ("mAP50-95(M)", "mAP50-95 mask"),
    ("mAP50-95(B)", "mAP50-95 kotak"),
    ("mAP50(M)", "mAP50 mask"),
    ("mAP50(B)", "mAP50 kotak"),
    ("precision(B)", "presisi"),
    ("recall(B)", "recall"),
)


def _angka(s: str):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def baca_hasil_csv(d: Path) -> dict:
    """Ringkas results.csv: berapa epoch selesai dan metrik terbaiknya."""
    p = Path(d) / "results.csv"
    if not p.exists():
        return {"epoch": 0, "baris": [], "metrik": {}, "terbaik": {}}
    try:
        teks = p.read_text().strip().splitlines()
    except OSError:
        return {"epoch": 0, "baris": [], "metrik": {}, "terbaik": {}}
    if len(teks) < 2:
        return {"epoch": 0, "baris": [], "metrik": {}, "terbaik": {}}

    kepala = [k.strip() for k in teks[0].split(",")]
    baris = []
    for ln in teks[1:]:
        sel = [s.strip() for s in ln.split(",")]
        if len(sel) != len(kepala):
            continue                      # baris yang sedang separuh ditulis
        baris.append(dict(zip(kepala, sel)))
    if not baris:
        return {"epoch": 0, "baris": [], "metrik": {}, "terbaik": {}}

    akhir = baris[-1]

    def cari(nama_akhir: str):
        for k, v in akhir.items():
            if k.endswith(nama_akhir):
                return _angka(v)
        return None

    metrik, terbaik = {}, {}
    for kunci, label in METRIK:
        v = cari(kunci)
        if v is None:
            continue
        metrik[label] = v
        kolom = next((k for k in kepala if k.endswith(kunci)), None)
        nilai = [_angka(b.get(kolom)) for b in baris] if kolom else []
        nilai = [x for x in nilai if x is not None]
        if nilai:
            terbaik[label] = max(nilai)

    # Kurva untuk grafik: epoch + metrik utama + loss, dijarangkan supaya
    # jawaban rutenya tidak membengkak pada training 250 epoch.
    utama = next((l for _, l in METRIK if l in metrik), None)
    kolom_utama = next((k for k in kepala
                        if utama and k.endswith(
                            next(kk for kk, ll in METRIK if ll == utama))), None)
    kurva = []
    langkah = max(1, len(baris) // 120)
    for b in baris[::langkah]:
        kurva.append({
            "epoch": _angka(b.get("epoch")) or 0,
            "nilai": _angka(b.get(kolom_utama)) if kolom_utama else None,
            "box": next((_angka(v) for k, v in b.items()
                         if k.endswith("train/box_loss")), None),
        })
    return {"epoch": int(_angka(akhir.get("epoch")) or len(baris)),
            "metrik": metrik, "terbaik": terbaik, "kurva": kurva,
            "utama": utama,
            "detik": _angka(akhir.get("time")) or 0.0}


def hidup(pid) -> bool:
    """Proses training itu masih berjalan atau tidak."""
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
    # PID bisa dipakai ulang sistem. Diperiksa bahwa yang memakainya memang
    # proses training kita, bukan proses lain yang kebetulan mendapat nomor
    # yang sama — tanpa ini, training yang sudah mati bisa terlihat "berjalan"
    # selamanya.
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf8", "replace")
        return "latih_jalan" in cmd
    except OSError:
        return True


def statistik() -> dict:
    """GPU dan RAM sekarang. Kosong kalau pustakanya tidak ada."""
    out: dict = {}
    try:
        import psutil

        v = psutil.virtual_memory()
        out["ram"] = {"pakai_gb": round(v.used / 2 ** 30, 1),
                      "total_gb": round(v.total / 2 ** 30, 1),
                      "persen": v.percent}
        out["cpu"] = psutil.cpu_percent(interval=None)
    except Exception:                            # noqa: BLE001
        pass
    try:
        import pynvml

        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        m = pynvml.nvmlDeviceGetMemoryInfo(h)
        u = pynvml.nvmlDeviceGetUtilizationRates(h)
        nama = pynvml.nvmlDeviceGetName(h)
        out["gpu"] = {
            "nama": nama.decode() if isinstance(nama, bytes) else nama,
            "vram_pakai_mb": m.used // 2 ** 20,
            "vram_total_mb": m.total // 2 ** 20,
            "util": u.gpu,
            "suhu": pynvml.nvmlDeviceGetTemperature(h, 0),
        }
    except Exception:                            # noqa: BLE001
        pass
    return out


# ============================================================
# STATUS SATU TRAINING
# ============================================================

# Status yang mungkin, dan artinya:
#   antre    -> subprosesnya hidup tetapi sedang menunggu kunci GPU
#   jalan    -> sedang melatih
#   selesai  -> berhenti wajar, bobotnya ada
#   gagal    -> berhenti dengan galat
#   batal    -> dihentikan orang
#   hilang   -> prosesnya tidak ada lagi padahal statusnya masih jalan.
#               Ini yang terjadi kalau mesin mati di tengah training, dan
#               menamainya sendiri lebih jujur daripada membiarkannya
#               tampak berjalan selamanya.
BERJALAN = ("antre", "jalan")


def status(ds, nomor: int) -> dict:
    """Keadaan satu training SEKARANG, disusun dari disk."""
    isi = baca(ds, nomor)
    if isi is None:
        return {}
    d = dir_latih(ds, nomor)
    keadaan = isi.get("keadaan") or "antre"
    if keadaan in BERJALAN and not hidup(isi.get("pid")):
        # Prosesnya lenyap. Kalau bobotnya terlanjur ada, ia sempat selesai
        # dan yang gagal cuma pencatatan status terakhirnya.
        keadaan = "selesai" if (d / "weights" / "best.pt").exists() else "hilang"
        isi = perbarui(ds, nomor, keadaan=keadaan,
                       selesai_pada=isi.get("selesai_pada")
                       or datetime.now().strftime("%Y-%m-%d %H:%M"))

    csv = baca_hasil_csv(d)
    epochs = int((isi.get("par") or {}).get("epochs") or 0)
    ep = csv.get("epoch") or 0
    persen = min(100.0, round(ep / epochs * 100, 1)) if epochs else 0.0

    # Estimasi sisa dari rata-rata waktu per epoch yang SUDAH terjadi, bukan
    # dari taksiran awal: epoch pertama selalu lebih lama (kompilasi kernel,
    # cache dataset), jadi menaksir dari situ saja membuat angkanya meleset
    # jauh justru di menit-menit pertama ketika orang paling sering melihat.
    detik = csv.get("detik") or 0.0
    sisa = (detik / ep * (epochs - ep)) if (ep and epochs > ep) else 0.0

    out = {
        **{k: isi.get(k) for k in
           ("nomor", "nama", "catatan", "versi", "tugas", "bobot", "oleh",
            "dibuat", "selesai_pada", "galat", "par", "warna")},
        "keadaan": keadaan,
        "epoch": ep, "epochs": epochs, "persen": persen,
        "detik": detik, "sisa": sisa,
        "metrik": csv.get("metrik") or {},
        "terbaik": csv.get("terbaik") or {},
        "utama": csv.get("utama"),
        "punya_bobot": (d / "weights" / "best.pt").exists(),
    }
    return out


def daftar(ds) -> list[dict]:
    """Semua training di projek ini, terbaru dulu."""
    d = _dir(ds)
    if not d.is_dir():
        return []
    nomor = []
    for p in d.glob("L*.json"):
        m = re.fullmatch(r"L(\d+)", p.stem)
        if m:
            nomor.append(int(m.group(1)))
    return [s for s in (status(ds, n) for n in sorted(nomor, reverse=True)) if s]


def ada_yang_jalan(ds) -> dict | None:
    for s in daftar(ds):
        if s.get("keadaan") in BERJALAN:
            return s
    return None


# ============================================================
# MENJALANKAN
# ============================================================

def _saring_par(minta: dict) -> tuple[dict, list[str]]:
    """Ambil PRESET_V14 lalu timpa yang diminta, dijepit ke BATAS."""
    par = dict(PRESET_V14)
    galat: list[str] = []
    for k, v in (minta or {}).items():
        if k not in PRESET_V14:
            continue                       # kunci asing diabaikan, bukan ditolak
        if k in BATAS:
            lo, hi = BATAS[k]
            try:
                v = type(PRESET_V14[k])(v) if not isinstance(
                    PRESET_V14[k], bool) else bool(v)
            except (TypeError, ValueError):
                galat.append(f"{k} bukan angka")
                continue
            if not (lo <= v <= hi):
                galat.append(f"{k} harus antara {lo} dan {hi}")
                continue
        par[k] = v
    return par, galat


def siapkan(ds, *, nama: str, versi_nomor: int, tugas: str, bobot: str,
            par: dict, oleh: str, catatan: str = "",
            warna: dict | None = None) -> dict:
    """Catat satu training baru. Belum dijalankan."""
    n = nomor_berikut(ds)
    par_bersih, galat = _saring_par(par)
    if galat:
        raise ValueError("; ".join(galat))

    # SETELAN WARNA DIPAKSA DARI MODENYA, apa pun yang dikirim pemanggil.
    #
    # Keempat angka ini (hsv_h, hsv_s, hsv_v, bgr) tidak lagi ditawarkan di
    # form, dan tidak boleh bisa disetel dari luar sama sekali. Alasannya bukan
    # kerapian: keempatnya harus SEJALAN dengan cara versinya diaugmentasi, dan
    # kombinasi yang tidak sejalan menghasilkan model yang angkanya bagus lalu
    # gagal di ruang detektor — itu persis kegagalan v13 (mAP50-95 0,9499, lalu
    # benar 0 dari 7).
    #
    # Selama keempatnya jadi kotak isian, selalu ada jalan untuk memasang
    # kombinasi yang salah: lewat permintaan yang dibuat sendiri, lewat batch
    # yang disalin dari percobaan lain, atau lewat orang yang mengubah satu
    # angka tanpa tahu pasangannya. Dipaksa di sini, jalan itu tertutup.
    from . import mode_warna as mw

    mode = mw.sah((warna or {}).get("mode"))
    par_bersih.update(mw.par_latih(mode))
    if tugas not in TUGAS:
        raise ValueError(f"tugas harus salah satu dari {TUGAS}")
    isi = {
        "nomor": n,
        "nama": " ".join((nama or "").split())[:MAKS_NAMA] or f"Latihan {n}",
        "catatan": " ".join((catatan or "").split())[:MAKS_CATATAN],
        "versi": int(versi_nomor),
        "tugas": tugas,
        "bobot": bobot,
        "par": par_bersih,
        # Hasil periksa_warna DIBEKUKAN di sini, bukan dihitung ulang saat
        # ditampilkan: versinya bisa saja dihapus nanti, dan alasan sebuah
        # training disetel begitu harus tetap terbaca sesudah itu.
        "warna": warna or {},
        "oleh": oleh,
        "dibuat": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "keadaan": "antre",
        "pid": 0,
    }
    _tulis(ds, n, isi)
    return isi


def jalankan(ds, nomor: int) -> dict:
    """Luncurkan subproses training yang terlepas dari server."""
    isi = baca(ds, nomor)
    if isi is None:
        raise ValueError(f"training L{nomor} tidak ada")
    d = dir_latih(ds, nomor)
    d.mkdir(parents=True, exist_ok=True)
    log_p = _dir(ds) / f"L{nomor}.log"

    akar = Path(__file__).resolve().parents[2]
    # Subproses dijalankan dengan interpreter YANG SAMA dengan server, jadi
    # saklar LABELAPP_OLAH ikut menentukan venv mana yang melatih — sama
    # seperti bagian lain sistem ini.
    perintah = [sys.executable, "-m", "app.services.latih_jalan",
                str(Path(ds).resolve()), str(nomor)]
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(akar))
    # Ultralytics menulis setelan dan cache ke home; dibiarkan, tetapi
    # verbose-nya dimatikan supaya log berisi baris kemajuan kita sendiri.
    env["YOLO_VERBOSE"] = "False"

    with open(log_p, "ab", buffering=0) as f:
        f.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} mulai L{nomor} ===\n"
                .encode())
        p = subprocess.Popen(perintah, cwd=str(akar), env=env,
                             stdout=f, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL,
                             # Sesi sendiri: subproses TIDAK ikut mati ketika
                             # server dimatikan atau --reload menyalakannya
                             # ulang. Itu seluruh alasan memakai subproses.
                             start_new_session=True)
    log.info("training L%s dimulai di %s (pid %s)", nomor, Path(ds).name, p.pid)
    return perbarui(ds, nomor, pid=p.pid, keadaan="antre")


def batalkan(ds, nomor: int) -> dict:
    """Hentikan training yang sedang berjalan."""
    isi = baca(ds, nomor) or {}
    pid = isi.get("pid") or 0
    if hidup(pid):
        try:
            # Seluruh grup: Ultralytics memakai worker DataLoader, dan
            # mematikan induknya saja meninggalkan mereka menggantung.
            os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError) as e:
            log.warning("gagal menghentikan L%s: %s", nomor, e)
    return perbarui(ds, nomor, keadaan="batal",
                    selesai_pada=datetime.now().strftime("%Y-%m-%d %H:%M"))


def buang(ds, nomor: int) -> bool:
    """Hapus satu training beserta bobotnya."""
    isi = baca(ds, nomor)
    if isi is None:
        return False
    if isi.get("keadaan") in BERJALAN and hidup(isi.get("pid")):
        raise ValueError("training itu masih berjalan — hentikan dulu")
    shutil.rmtree(dir_latih(ds, nomor), ignore_errors=True)
    # SELURUH berkas milik nomor ini disapu dengan pola, bukan disebut satu per
    # satu. Versi sebelumnya menyebut L<n>.json dan L<n>.log saja, dan ketika
    # evaluasi produksi ditambahkan belakangan, L<n>.eval.json dan
    # L<n>.eval.log tertinggal sebagai berkas yatim — tidak terlihat di layar,
    # tidak bisa dihapus dari mana pun, dan menumpuk diam-diam.
    #
    # Pola ini ikut menyapu berkas sementara (.tmp) yang tertinggal kalau
    # penulisan manifes terputus di tengah.
    #
    # Nomor tidak pernah dipakai ulang (nomor_berikut selalu dari yang
    # TERBESAR), jadi pola ini tidak mungkin mengenai training lain.
    for x in _dir(ds).glob(f"L{int(nomor)}.*"):
        if x.is_file():
            x.unlink(missing_ok=True)
    return True


def ekor_log(ds, nomor: int, baris: int = 40) -> str:
    p = _dir(ds) / f"L{nomor}.log"
    if not p.exists():
        return ""
    try:
        return "\n".join(p.read_text(errors="replace").splitlines()[-baris:])
    except OSError:
        return ""


# ============================================================
# EVALUASI PRODUKSI
# ============================================================
#
# Dipisah dari training karena ia menjawab pertanyaan yang berbeda. Training
# menghasilkan mAP; evaluasi ini menjawab "apakah model ini bisa dipakai di
# ruang detektor" — dan v13 membuktikan keduanya bisa sangat berbeda: mAP50-95
# 0,9499, lalu benar 0 dari 7 pada foto RVM sungguhan.
#
# Tidak memakai kunci GPU. Inferensi model nano memakai beberapa ratus MB dan
# selesai dalam belasan detik; menunggui training yang berjam-jam hanya untuk
# itu berarti tombol Uji tidak bisa dipakai sepanjang hari.

def jalankan_evaluasi(ds, nomor: int) -> dict:
    """Luncurkan evaluasi produksi untuk satu training yang sudah selesai."""
    isi = baca(ds, nomor)
    if isi is None:
        raise ValueError(f"training L{nomor} tidak ada")
    if not (dir_latih(ds, nomor) / "weights" / "best.pt").exists():
        raise ValueError("belum ada best.pt — trainingnya belum selesai")

    akar = Path(__file__).resolve().parents[2]
    log_p = _dir(ds) / f"L{nomor}.eval.log"
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(akar))
    env["YOLO_VERBOSE"] = "False"
    with open(log_p, "ab", buffering=0) as f:
        f.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} evaluasi L{nomor} ===\n"
                .encode())
        p = subprocess.Popen(
            [sys.executable, "-m", "app.services.evaluasi_jalan",
             str(Path(ds).resolve()), str(nomor)],
            cwd=str(akar), env=env, stdout=f, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True)
    log.info("evaluasi L%s dimulai (pid %s)", nomor, p.pid)
    return {"pid": p.pid}


def hasil_evaluasi(ds, nomor: int) -> dict | None:
    from . import evaluasi_jalan

    return evaluasi_jalan.baca(ds, nomor)
