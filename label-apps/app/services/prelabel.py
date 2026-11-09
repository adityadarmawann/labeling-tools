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

from . import annotations, latih, olah_gpu, scanner, tugas

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
# Prediksi berebut GPU dengan training/pembuatan versi. Dibatasi satu job
# serentak secara bawaan: yang kedua MENUNGGU giliran, bukan ditolak, supaya
# orang tak perlu menekan tombolnya lagi. Angkanya bisa dinaikkan lewat env.
SERENTAK = max(1, int(os.environ.get("LABELAPP_PRELABEL_SERENTAK") or 1))
_giliran = threading.Semaphore(SERENTAK)


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


def _shapes_dari_hasil(r, kelas: list, W: int, H: int) -> list[dict]:
    """Satu hasil ultralytics -> daftar bentuk labelme (kotak atau poligon)."""
    def kurung(x, batas):
        return min(max(float(x), 0.0), float(batas))

    names_model = getattr(r, "names", {}) or {}
    boxes = getattr(r, "boxes", None)
    masks = getattr(r, "masks", None)
    out: list[dict] = []

    # Model segmentasi: ambil poligon per objek (koordinat gambar penuh).
    if (masks is not None and getattr(masks, "xy", None) is not None
            and boxes is not None):
        clss = boxes.cls.tolist()
        for poly_xy, ci in zip(masks.xy, clss):
            if poly_xy is None or len(poly_xy) < 3:
                continue
            pts = [[kurung(x, W), kurung(y, H)] for x, y in poly_xy]
            pts = scanner.tutup_cincin(pts)
            out.append({"label": _nama_kelas(ci, names_model, kelas), "text": "",
                        "points": pts, "group_id": None,
                        "shape_type": "polygon", "flags": {}})
        return out

    # Model deteksi: kotak 2 titik [[x1,y1],[x2,y2]], konvensi labelme.
    if boxes is not None:
        for xyxy, ci in zip(boxes.xyxy.tolist(), boxes.cls.tolist()):
            x1, y1, x2, y2 = xyxy
            out.append({
                "label": _nama_kelas(ci, names_model, kelas), "text": "",
                "points": [[kurung(x1, W), kurung(y1, H)],
                           [kurung(x2, W), kurung(y2, H)]],
                "group_id": None, "shape_type": "rectangle", "flags": {}})
    return out


def _prediksi_satu(model, it: dict, conf: float, kelas: list) -> list[dict]:
    dev = 0 if olah_gpu.MODE == "gpu" else "cpu"
    try:
        hasil = model(str(it["img"]), conf=float(conf), verbose=False, device=dev)
    except Exception:
        # Satu gambar gagal (mis. OOM GPU sesaat) tak boleh menjatuhkan seluruh
        # job; coba sekali lagi di CPU, lalu menyerah pada gambar ini saja.
        hasil = model(str(it["img"]), conf=float(conf), verbose=False, device="cpu")
    out: list[dict] = []
    for r in hasil:
        out += _shapes_dari_hasil(r, kelas, it["W"], it["H"])
    return out


# ------------------------------------------------------------------ tulis
def tulis_prediksi(it: dict, src: Path, names: dict, bentuk: list[dict]) -> dict:
    """Tulis bentuk prediksi untuk SATU gambar, format sama dengan /api/simpan.

    Mengembalikan peta kelas yang mungkin bertambah (nama baru didaftarkan),
    supaya pemanggil memakainya untuk gambar berikutnya."""
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
             batch: str | None = None) -> dict:
    """Pre-label gambar BELUM teranotasi dengan model terlatih (maks `batas`).

    Lingkup: satu JOB (`tid`) ATAU satu BATCH/unggahan (`batch`), persis seperti
    autolabel_zero dan autolabel_gabung. Tepat satu dipakai, `batch is not None`
    menang. Lingkup batch penting khusus di sini: jalur inilah SATU-SATUNYA yang
    bisa projek kerangka (pose), jadi tanpa dia projek pose tak punya jalan
    auto-label sebatch sama sekali.

    `batal` callable -> True untuk berhenti. Melempar Dibatalkan kalau diminta,
    FileNotFoundError kalau modelnya tak ada atau tasknya tak dikenal."""
    kunci = akun
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
        if batch is not None:
            target = tugas.gambar_belum_batch(items, ds, batch)
        else:
            target = tugas.gambar_konteks_job(items, data, ds, tid,
                                              saring="belum")
            if target is None:
                raise FileNotFoundError("tugas tak dikenal")
        if batas and batas > 0:
            target = target[:int(batas)]
        total = len(target)
        catat_maju(kunci, total=total, fase_nama="Memprediksi")

        diproses = objek = dilewati = 0
        for i, it in enumerate(target):
            if batal and batal():
                raise Dibatalkan()
            # Hak tulis dihormati: labeler yang hanya berhak atas batch tertentu
            # tak boleh menulis ke gambar di luar jatahnya.
            if tugas.tolak_tulis(ds, akun, it["img"]):
                dilewati += 1
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
                    if pose:
                        tulis_prediksi_pose(it, bentuk)
                        # Hitung instance (rectangle), bukan tiap titik.
                        objek += sum(1 for s in bentuk
                                     if s["shape_type"] == "rectangle")
                    else:
                        names = tulis_prediksi(it, ds, names, bentuk)
                        objek += len(bentuk)
                    diproses += 1
                else:
                    # Tak ada deteksi -> biarkan "belum", JANGAN tandai latar.
                    dilewati += 1
            catat_maju(kunci,
                       persen=round((i + 1) / max(total, 1) * 100, 1),
                       diproses=diproses, objek=objek, dilewati=dilewati)

    return {"total": total, "diproses": diproses, "objek": objek,
            "dilewati": dilewati}
