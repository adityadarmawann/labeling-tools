"""
Pre-label batch: jalankan model YOLO terlatih ke banyak gambar BELUM teranotasi
sekaligus, isi prediksinya sebagai anotasi yang tinggal diverifikasi manusia.

Tiga sifat yang dijaga, sama semangatnya dengan autosave dan /api/simpan:

  1. TIDAK PERNAH menimpa kerja manusia. Hanya gambar yang benar-benar kosong
     (severity 'stop') yang diisi; yang sudah berlabel atau bertanda latar
     dilewati. gambar_konteks_job(saring='belum') sudah menyaringnya.
  2. Gambar tanpa deteksi dibiarkan "belum", BUKAN ditulis kosong (yang berarti
     'latar'). Model yang tak menemukan apa-apa belum tentu berarti gambar itu
     negatif; menandainya latar diam-diam akan mengubah dataset tanpa diminta.
  3. Prediksi ditulis dalam format yang SAMA PERSIS dengan simpan manual
     (labelme .json + label YOLO .txt lewat scanner.tulis_yolo), jadi hasilnya
     tampil dan bisa disunting seperti anotasi biasa.

Pola job latarnya meniru buatversi.py: dict kemajuan berkunci akun, throttle
Semaphore supaya tak membebani pelabel yang sedang bekerja di depan layar, dan
pembatalan lewat callback.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from . import ai_antre, annotations, latih, olah_gpu, scanner, tugas

# ------------------------------------------------------------------ kemajuan
_maju: dict[str, dict] = {}
_kunci = threading.Lock()


def catat_maju(kunci: str, **nilai) -> None:
    with _kunci:
        d = _maju.setdefault(kunci, {})
        d.update(nilai)
        d["saat"] = time.time()


def kemajuan(kunci: str) -> dict:
    with _kunci:
        return dict(_maju.get(kunci) or {})


def bersihkan_maju(kunci: str) -> None:
    with _kunci:
        _maju.pop(kunci, None)


# --------------------------------------------------------------- throttle
# Prediksi berebut GPU dengan training/pembuatan versi. Antreannya DIPAKAI
# BERSAMA dengan auto-label (ai_antre): dua semaphore terpisah membiarkan satu
# orang memuat dua model sekaligus di GPU yang sama.
SERENTAK = ai_antre.SERENTAK
_giliran = ai_antre.giliran


class Dibatalkan(Exception):
    """Job dihentikan atas permintaan pengguna."""


# ------------------------------------------------------------------ model
_model_cache: dict[str, object] = {}
_cache_kunci = threading.Lock()


def _muat_model(pt: Path):
    """Muat model ultralytics, di-cache per berkas. Impor di dalam fungsi: torch
    itu berat dan tak boleh ikut tertarik saat server CPU mengimpor modul ini."""
    key = str(Path(pt).resolve())
    with _cache_kunci:
        m = _model_cache.get(key)
        if m is None:
            from ultralytics import YOLO
            m = YOLO(key)
            _model_cache[key] = m
        return m


def _nama_kelas(ci: int, names_model: dict, kelas: list) -> str:
    ci = int(ci)
    if names_model and ci in names_model:
        return str(names_model[ci])
    if 0 <= ci < len(kelas):
        return str(kelas[ci])
    return str(ci)


def _shapes_dari_hasil(r, kelas: list, W: int, H: int,
                       skor: list | None = None) -> list[dict]:
    """Satu hasil ultralytics -> daftar bentuk labelme (kotak atau poligon).

    `skor` opsional: kalau diberi list, keyakinan tiap deteksi DITAMBAHKAN ke
    situ, sejajar indeks dengan bentuk yang dikembalikan. Sengaja parameter
    keluaran terpisah, bukan field di dalam dict bentuknya: dict itu ditulis
    APA ADANYA ke .json anotasi, jadi menitipkan angka di sana akan bocor ke
    berkas anotasi orang. 0.0 kalau modelnya tak memberi keyakinan.
    """
    def kurung(x, batas):
        return min(max(float(x), 0.0), float(batas))

    names_model = getattr(r, "names", {}) or {}
    boxes = getattr(r, "boxes", None)
    masks = getattr(r, "masks", None)
    out: list[dict] = []

    def confs() -> list:
        c = getattr(boxes, "conf", None) if boxes is not None else None
        try:
            return [float(v) for v in c.tolist()]
        except (AttributeError, TypeError, ValueError):
            return []

    cf = confs()

    def catat(i: int) -> None:
        if skor is not None:
            skor.append(cf[i] if i < len(cf) else 0.0)

    # Model segmentasi: ambil poligon per objek (koordinat gambar penuh).
    if (masks is not None and getattr(masks, "xy", None) is not None
            and boxes is not None):
        clss = boxes.cls.tolist()
        for i, (poly_xy, ci) in enumerate(zip(masks.xy, clss)):
            if poly_xy is None or len(poly_xy) < 3:
                continue
            pts = [[kurung(x, W), kurung(y, H)] for x, y in poly_xy]
            pts = scanner.tutup_cincin(pts)
            out.append({"label": _nama_kelas(ci, names_model, kelas), "text": "",
                        "points": pts, "group_id": None,
                        "shape_type": "polygon", "flags": {}})
            catat(i)
        return out

    # Model deteksi: kotak 2 titik [[x1,y1],[x2,y2]], konvensi labelme.
    if boxes is not None:
        for i, (xyxy, ci) in enumerate(zip(boxes.xyxy.tolist(),
                                            boxes.cls.tolist())):
            x1, y1, x2, y2 = xyxy
            out.append({
                "label": _nama_kelas(ci, names_model, kelas), "text": "",
                "points": [[kurung(x1, W), kurung(y1, H)],
                           [kurung(x2, W), kurung(y2, H)]],
                "group_id": None, "shape_type": "rectangle", "flags": {}})
            catat(i)
    return out


def _prediksi_satu(model, it: dict, conf: float, kelas: list,
                   skor: list | None = None) -> list[dict]:
    dev = 0 if olah_gpu.MODE == "gpu" else "cpu"
    try:
        hasil = model(str(it["img"]), conf=float(conf), verbose=False, device=dev)
    except Exception:
        # Satu gambar gagal (mis. OOM GPU sesaat) tak boleh menjatuhkan seluruh
        # job; coba sekali lagi di CPU, lalu menyerah pada gambar ini saja.
        hasil = model(str(it["img"]), conf=float(conf), verbose=False, device="cpu")
    out: list[dict] = []
    for r in hasil:
        out += _shapes_dari_hasil(r, kelas, it["W"], it["H"], skor)
    return out


# ------------------------------------------------------------------ tulis
def _beritahu(ds, gambar) -> None:
    """Naikkan pencacah perubahan projek supaya sesi lain memindai ulang.

    Jalur AI menulis dari thread latar, jauh dari rute yang biasanya
    memanggilnya. Dilewatkan, hasilnya tak pernah terlihat sesi yang sedang
    terbuka, dan lebih buruk: perbandingan sidik di periksa_disk bisa
    MENELANNYA, karena segarkan() menyetel ulang sidik tanpa memuat apa pun.
    """
    from ..session import tandai_berubah
    try:
        tandai_berubah(ds, gambar)
    except Exception:                       # noqa: BLE001 -- jangan jatuhkan job
        pass


def _kotak_bentuk(b: dict) -> tuple:
    """Kotak pembungkus sebuah bentuk labelme, untuk membandingkan dua bentuk."""
    ttk = b.get("points") or []
    if not ttk:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [float(p[0]) for p in ttk]
    ys = [float(p[1]) for p in ttk]
    return (min(xs), min(ys), max(xs), max(ys))


def _tumpang(a: tuple, b: tuple) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    ua = ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)
    return inter / ua if ua > 0 else 0.0


def gabung_bentuk(lama: list, baru: list, iou: float = 0.6) -> list:
    """Bentuk lama + bentuk baru, tanpa menggandakan objek yang sama.

    Dipakai jalan auto-label KEDUA dan seterusnya. Tanpa pembanding ini,
    menjalankan resep yang sama dua kali menumpuk dua salinan tiap objek di
    tempat yang sama persis, dan memisahkannya kembali jauh lebih lama
    daripada melabeli dari nol.

    Yang dianggap sama: KELAS sama, JENIS bentuk sama, dan kotaknya bertindih
    lebih dari `iou`. Dua hal di tempat yang sama justru yang dicari:
    kelas berbeda (mask produk dan kotak logo memang bertumpuk) maupun jenis
    berbeda (poligon presisi di jalan pertama, kotak di jalan kedua). Jenis
    ikut jadi kunci karena jalan kedua memilih bentuknya secara sadar, dan
    membuang pilihan itu diam-diam lebih buruk daripada satu duplikat yang
    kelihatan dan bisa dihapus.
    """
    keluar = list(lama)
    kunci_lama = [(str(b.get("label")), str(b.get("shape_type")),
                   _kotak_bentuk(b)) for b in lama]
    for b in baru:
        k = _kotak_bentuk(b)
        nama = str(b.get("label"))
        jenis = str(b.get("shape_type"))
        if any(n == nama and j == jenis and _tumpang(k, kl) > iou
               for n, j, kl in kunci_lama):
            continue
        keluar.append(b)
        kunci_lama.append((nama, jenis, k))
    return keluar


def tulis_prediksi(it: dict, src: Path, names: dict, bentuk: list[dict],
                   tambah: bool = False, ditambah: list | None = None) -> dict:
    """Tulis bentuk prediksi untuk SATU gambar, format sama dengan /api/simpan.

    `tambah=True` MENAMBAHKAN ke bentuk yang sudah ada alih-alih menggantinya.
    Itu yang membuat jalan auto-label kedua mungkin: mask produk dulu, lalu
    kotak logo menyusul, tanpa yang pertama terhapus.

    Mengembalikan peta kelas yang mungkin bertambah (nama baru didaftarkan),
    `ditambah` opsional: kalau diberi list, JUMLAH bentuk yang benar-benar
    masuk ditambahkan ke situ. Dengan `tambah=True` sebagian bentuk baru bisa
    dibuang karena menduplikasi yang sudah ada, dan pemanggil yang menghitung
    dari panjang `bentuk` akan melaporkan objek yang tak pernah ditulis --
    persis jenis kebohongan yang sudah diperbaiki pada pencacah "dilewati".
    """
    import json

    jp: Path = it["img"].with_suffix(".json")
    lama = {}
    if jp.exists():
        try:
            lama = json.loads(jp.read_text(encoding="utf-8")) or {}
        except Exception:
            lama = {}
    if tambah:
        sebelum = list(lama.get("shapes") or [])
        bentuk = gabung_bentuk(sebelum, bentuk)
        if ditambah is not None:
            ditambah.append(len(bentuk) - len(sebelum))
    elif ditambah is not None:
        ditambah.append(len(bentuk))
    isi = {
        "version": lama.get("version") or annotations.LABELME_VERSION,
        "flags": lama.get("flags") or {},
        "shapes": bentuk,
        "imagePath": it["img"].name,
        "imageData": lama.get("imageData"),
        "imageHeight": it["H"],
        "imageWidth": it["W"],
        # Hasil prediksi AI -> tandai "perlu dicek" supaya pelabel tahu ini
        # belum diverifikasi manusia. Dibersihkan saat /api/simpan manual.
        "perlu_dicek": True,
    }
    annotations.tulis_aman(jp, json.dumps(isi, ensure_ascii=False, indent=2))

    if it.get("yolo"):
        ada = set(names.values())
        baru = []
        for s in bentuk:
            nm = str(s.get("label") or "").strip()
            if nm and nm not in ada and nm not in baru:
                baru.append(nm)
        if baru:
            names = scanner.daftarkan_kelas(src, names, baru)
        indeks = {n: i for i, n in names.items()}
        scanner.tulis_yolo(it["labels"], bentuk, it["W"], it["H"], indeks)
    return names


# ------------------------------------------------------------------ pose
# Projek kerangka (pose) itu DATAR labelme, bukan YOLO: cukup menulis .json
# dengan bentuk point + rectangle bergabung lewat group_id. Ekspor YOLO-pose
# (cls cx cy w h + K*(px py v)) terjadi belakangan saat versi dibuat
# (export.baris_yolo_pose), bukan per gambar. Karena itu tulis_prediksi_pose
# TIDAK pernah menyentuh scanner.tulis_yolo (yang membuang bentuk point) dan
# TIDAK mendaftarkan nama keypoint sebagai kelas projek.

def _shapes_pose_dari_hasil(r, titik: list, kelas_rect: str,
                            W: int, H: int) -> list[dict]:
    """Satu hasil ultralytics pose -> bentuk labelme: per instance, satu
    rectangle + K point (bergabung lewat group_id). Keypoint diberi nama dari
    template `titik` (urutannya = urutan latih model). Visibilitas dari
    keyakinan keypoint: >=0,5 terlihat (2), >=0,1 terhalang (1), selain itu
    absen (0). Instance tanpa satu pun keypoint terlihat dilewati."""
    def kurung(x, batas):
        return min(max(float(x), 0.0), float(batas))

    kps = getattr(r, "keypoints", None)
    if kps is None or getattr(kps, "xy", None) is None:
        return []
    xy = kps.xy.tolist()                              # N x K x 2
    conf = kps.conf.tolist() if getattr(kps, "conf", None) is not None else None
    boxes = getattr(r, "boxes", None)
    xyxy = (boxes.xyxy.tolist()
            if (boxes is not None and getattr(boxes, "xyxy", None) is not None)
            else None)
    K = len(titik)
    out: list[dict] = []
    for n, inst in enumerate(xy):
        kp_shapes = []
        for i in range(min(K, len(inst))):
            x, y = inst[i][0], inst[i][1]
            c = conf[n][i] if (conf is not None and n < len(conf)) else 1.0
            v = 2 if c >= 0.5 else (1 if c >= 0.1 else 0)
            kp_shapes.append({"label": str(titik[i]), "text": "",
                              "points": [[kurung(x, W), kurung(y, H)]],
                              "group_id": n, "shape_type": "point",
                              "flags": {"v": v}})
        if not any(s["flags"]["v"] >= 1 for s in kp_shapes):
            continue                                  # tak ada titik terlihat
        if xyxy is not None and n < len(xyxy):
            x1, y1, x2, y2 = xyxy[n]
        else:
            vis = [s["points"][0] for s in kp_shapes if s["flags"]["v"] >= 1]
            xs = [p[0] for p in vis]
            ys = [p[1] for p in vis]
            x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        out.append({"label": str(kelas_rect or "objek"), "text": "",
                    "points": [[kurung(x1, W), kurung(y1, H)],
                               [kurung(x2, W), kurung(y2, H)]],
                    "group_id": n, "shape_type": "rectangle", "flags": {}})
        out += kp_shapes
    return out


def _prediksi_pose(model, it: dict, conf: float, titik: list,
                   kelas_rect: str) -> list[dict]:
    dev = 0 if olah_gpu.MODE == "gpu" else "cpu"
    try:
        hasil = model(str(it["img"]), conf=float(conf), verbose=False, device=dev)
    except Exception:
        hasil = model(str(it["img"]), conf=float(conf), verbose=False, device="cpu")
    out: list[dict] = []
    for r in hasil:
        out += _shapes_pose_dari_hasil(r, titik, kelas_rect, it["W"], it["H"])
    return out


def tulis_prediksi_pose(it: dict, bentuk: list[dict]) -> None:
    """Tulis bentuk pose untuk SATU gambar: HANYA labelme .json (projek pose
    datar, tak ada YOLO .txt per gambar). Format sama dengan /api/simpan."""
    import json

    jp: Path = it["img"].with_suffix(".json")
    lama = {}
    if jp.exists():
        try:
            lama = json.loads(jp.read_text(encoding="utf-8")) or {}
        except Exception:
            lama = {}
    isi = {
        "version": lama.get("version") or annotations.LABELME_VERSION,
        "flags": lama.get("flags") or {},
        "shapes": bentuk,
        "imagePath": it["img"].name,
        "imageData": lama.get("imageData"),
        "imageHeight": it["H"],
        "imageWidth": it["W"],
        "perlu_dicek": True,          # hasil pose AI -> perlu diverifikasi manusia
    }
    annotations.tulis_aman(jp, json.dumps(isi, ensure_ascii=False, indent=2))


# ------------------------------------------------------------------ daftar model
def model_terlatih(ds: Path) -> list[dict]:
    """Training run YOLO yang punya best.pt -> bisa dipakai memprediksi. RF-DETR
    dan yang belum punya bobot disaring keluar (bukan model ultralytics)."""
    out = []
    for s in latih.daftar(ds):
        if (s.get("arsitektur") or "yolo") != "yolo":
            continue
        if not s.get("punya_bobot"):
            continue
        out.append({"nomor": s.get("nomor"), "versi": s.get("versi"),
                    "kelas": list(s.get("kelas") or []),
                    "keadaan": s.get("keadaan")})
    return out


def model_eksternal(ds: Path) -> list[dict]:
    """Model .pt yang DIUNGGAH dari luar (disimpan di .latih/_eksternal/), untuk
    dipilih sebagai sumber auto-label gabungan. Tiap .pt dimuat sekali (di-cache)
    untuk membaca kelas + task; yang gagal dimuat ditandai, tidak menjatuhkan
    daftar. CATATAN KEAMANAN: memuat .pt menjalankan unpickle -- hanya unggah
    model tepercaya milik tim sendiri (gerbang unggah = pemilik/Editor)."""
    d = latih._dir(ds) / "_eksternal"
    out = []
    for pt in sorted(d.glob("*.pt")) if d.is_dir() else []:
        info = {"nama": pt.stem, "kelas": [], "task": ""}
        try:
            m = _muat_model(pt)
            names = getattr(m, "names", None) or {}
            info["kelas"] = [str(v) for v in (names.values() if isinstance(names, dict)
                                              else names)]
            info["task"] = str(getattr(m, "task", "") or "")
        except Exception:                    # noqa: BLE001 -- berkas rusak/bukan ultralytics
            info["task"] = "gagal-dimuat"
        out.append(info)
    return out


# ------------------------------------------------------------------ job
def jalankan(ds: Path, uploads_root: Path, tid: str, nomor: int, akun: str,
             batas: int, conf: float, batal=None,
             batch: str | None = None, sasaran: str = "belum") -> dict:
    """Pre-label gambar BELUM teranotasi dengan model terlatih (maks `batas`).

    Lingkup: satu JOB (`tid`) ATAU satu BATCH/unggahan (`batch`), persis seperti
    autolabel_zero dan autolabel_gabung. Tepat satu dipakai, `batch is not None`
    menang. Lingkup batch penting khusus di sini: jalur inilah SATU-SATUNYA yang
    bisa projek kerangka (pose), jadi tanpa dia projek pose tak punya jalan
    auto-label sebatch sama sekali.

    `batal` callable -> True untuk berhenti. Melempar Dibatalkan kalau diminta,
    FileNotFoundError kalau modelnya tak ada atau tasknya tak dikenal."""
    kunci = akun
    # Saluran kemajuan DIKOSONGKAN lebih dulu, bukan sekadar ditimpa nol.
    #
    # catat_maju menggabung (d.update), jadi bendera akhir jalan SEBELUMNYA
    # (selesai, galat, dibatalkan, training) tetap tinggal walau pencacahnya
    # sudah disetel nol di sini. Antarmuka memeriksa `selesai` lebih dulu,
    # sehingga jalan kedua dan seterusnya di proses server yang sama LANGSUNG
    # memperlihatkan "Selesai. 0 gambar diisi, 0 objek" lalu berhenti memantau,
    # padahal pekerjaannya baru saja mulai dan terus menulis di latar.
    # Dilaporkan dari prod: 580 berkas ditulis sementara layar berkata nol.
    bersihkan_maju(kunci)
    catat_maju(kunci, jalan=True, persen=0.0, fase_nama="Menunggu giliran",
               diproses=0, objek=0, dilewati=0, total=0)

    with _giliran:
        catat_maju(kunci, fase_nama="Menyiapkan model")
        pt = latih.bobot_training(ds, nomor, "best")
        if not pt or not Path(pt).exists():
            raise FileNotFoundError("model terlatih (best.pt) tak ditemukan")
        st = latih.status(ds, nomor) or {}
        kelas = list(st.get("kelas") or [])
        model = _muat_model(pt)

        # Dipindai ulang di sini, bukan memakai sess.items: job jalan di thread
        # lain setelah permintaan balik, dan keadaan gambar bisa sudah berubah.
        items, names = scanner.scan(ds)
        data = tugas.baca_projek(ds, uploads_root)
        # Projek kerangka (pose): model memberi keypoint, bukan kotak/mask.
        # Keluaran ditulis sebagai bentuk point + rectangle (datar labelme).
        pose = tugas.skeleton_aktif(data)
        _sk = data.get("skeleton") or {}
        pose_titik = list(_sk.get("titik") or [])
        pose_kelas = _sk.get("kelas") or "objek"
        sasaran_x = sasaran if sasaran in tugas.SASARAN else "belum"
        tambah = sasaran_x != "belum"
        if batch is not None:
            target = tugas.gambar_belum_batch(items, ds, batch, sasaran_x)
        else:
            target = tugas.gambar_konteks_job(
                items, data, ds, tid,
                saring="belum" if sasaran_x == "belum"
                else ("ai" if sasaran_x == "dicek" else "semua"))
            if target is None:
                raise FileNotFoundError("tugas tak dikenal")
        if batas and batas > 0:
            target = target[:int(batas)]
        total = len(target)
        catat_maju(kunci, total=total, fase_nama="Memprediksi")

        diproses = objek = ditolak = kosong = 0
        for i, it in enumerate(target):
            if batal and batal():
                raise Dibatalkan()
            # Hak tulis dihormati: labeler yang hanya berhak atas batch tertentu
            # tak boleh menulis ke gambar di luar jatahnya.
            if tugas.tolak_tulis(ds, akun, it["img"]):
                ditolak += 1
            else:
                try:
                    if pose:
                        bentuk = _prediksi_pose(model, it, conf, pose_titik,
                                                pose_kelas)
                    else:
                        bentuk = _prediksi_satu(model, it, conf, kelas)
                except Exception:
                    bentuk = []
                if bentuk:
                    # Sesi lain harus tahu berkas ini berubah. Tanpa ini,
                    # pelabel yang sedang bekerja di kanvas TIDAK AKAN PERNAH
                    # melihat hasil auto-label selama ia memakai panah kiri/
                    # kanan: segarkan() keluar lebih awal kalau pencacahnya tak
                    # naik. Ia lalu melabeli ulang dari nol lalu menyimpan, dan
                    # simpanannya menimpa hasil AI.
                    _beritahu(ds, it["img"])
                    if pose:
                        tulis_prediksi_pose(it, bentuk)
                        # Hitung instance (rectangle), bukan tiap titik.
                        objek += sum(1 for s in bentuk
                                     if s["shape_type"] == "rectangle")
                    else:
                        names = tulis_prediksi(it, ds, names, bentuk,
                                               tambah=tambah)
                        objek += len(bentuk)
                    diproses += 1
                else:
                    # Tak ada deteksi -> biarkan "belum", JANGAN tandai latar.
                    kosong += 1
            catat_maju(kunci,
                       persen=round((i + 1) / max(total, 1) * 100, 1),
                       diproses=diproses, objek=objek,
                       dilewati=ditolak + kosong, kosong=kosong,
                       ditolak=ditolak)

    # Dua sebab yang SANGAT berbeda, dulu dihitung di ember yang
    # sama: "dilewati 8" tak memberi petunjuk apakah modelnya memang
    # tak menemukan apa-apa atau orangnya tidak berhak menulis ke
    # gambar-gambar itu. Yang pertama berarti ganti prompt/model, yang
    # kedua berarti minta akses. `dilewati` dipertahankan sebagai
    # jumlah keduanya supaya pembaca lama tetap benar.
    return {"total": total, "diproses": diproses, "objek": objek,
            "dilewati": ditolak + kosong, "kosong": kosong, "ditolak": ditolak}
